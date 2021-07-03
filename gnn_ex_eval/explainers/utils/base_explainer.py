from typing import List, Tuple, Dict

from math import sqrt

import torch
from torch import Tensor
import torch.nn as nn
import matplotlib.pyplot as plt
import networkx as nx
from torch_geometric.nn import MessagePassing
from torch_geometric.utils.loop import add_self_loops, remove_self_loops
from torch_geometric.data import Data, Batch
from torch_geometric.utils import to_networkx
from torch_geometric.utils import k_hop_subgraph as subgraph
#from ..models.utils import subgraph
#from rdkit import Chem
from matplotlib.axes import Axes
from matplotlib.patches import Path, PathPatch

import numpy as np
#from ..models.models import GNNPool

class GNNPool(nn.Module):
    def __init__(self):
        super().__init__()

EPS = 1e-15


class ExplainerBase(nn.Module):

    def __init__(self, model: nn.Module, epochs: int = 0, lr: float = 0, explain_graph: bool = False,
                 molecule: bool = False):
        super().__init__()
        self.model = model
        self.lr = lr
        self.epochs = epochs
        self.explain_graph = explain_graph
        self.molecule = molecule
        self.mp_layers = [module for module in self.model.modules() if isinstance(module, MessagePassing)]
        self.num_layers = len(self.mp_layers)

        self.ori_pred = None
        self.ex_labels = None
        self.edge_mask = None
        self.hard_edge_mask = None

        self.num_edges = None
        self.num_nodes = None
        self.device = None
        #self.table = Chem.GetPeriodicTable().GetElementSymbol

    def __set_masks__(self, x: Tensor, edge_index: Tensor, init="normal"):
        (N, F), E = x.size(), edge_index.size(1)

        std = 0.1
        self.node_feat_mask = torch.nn.Parameter(torch.randn(F, requires_grad=True, device=self.device) * 0.1)

        std = torch.nn.init.calculate_gain('relu') * sqrt(2.0 / (2 * N))
        self.edge_mask = torch.nn.Parameter(torch.randn(E, requires_grad=True, device=self.device) * std)
        # self.edge_mask = torch.nn.Parameter(100 * torch.ones(E, requires_grad=True))

        for module in self.model.modules():
            if isinstance(module, MessagePassing):
                module.__explain__ = True
                module.__edge_mask__ = self.edge_mask

    def __clear_masks__(self):
        for module in self.model.modules():
            if isinstance(module, MessagePassing):
                module.__explain__ = False
                module.__edge_mask__ = None
        self.node_feat_masks = None
        self.edge_mask = None

    @property
    def __num_hops__(self):
        if self.explain_graph:
            return -1
        else:
            return self.num_layers

    def __flow__(self):
        for module in self.model.modules():
            if isinstance(module, MessagePassing):
                return module.flow
        return 'source_to_target'

    def __subgraph__(self, node_idx: int, x: Tensor, edge_index: Tensor, **kwargs):
        '''Gets subgraph for a given node'''
        num_nodes, num_edges = x.size(0), edge_index.size(1)

        subset, edge_index, mapping, edge_mask = subgraph(
            node_idx, self.__num_hops__, edge_index, relabel_nodes=True,
            num_nodes=num_nodes, flow=self.__flow__())

        x = x[subset]
        for key, item in kwargs.items():
            if torch.is_tensor(item) and item.size(0) == num_nodes:
                item = item[subset]
            elif torch.is_tensor(item) and item.size(0) == num_edges:
                item = item[edge_mask]
            kwargs[key] = item

        return x, edge_index, mapping, edge_mask, kwargs


    def forward(self,
                x: Tensor,
                edge_index: Tensor,
                **kwargs
                ):
        self.num_edges = edge_index.shape[1]
        self.num_nodes = x.shape[0]
        self.device = x.device


    def control_sparsity(self, mask: Tensor, sparsity=None, **kwargs):
        r"""
        :param mask: mask that need to transform
        :param sparsity: sparsity we need to control i.e. 0.7, 0.5
        :return: transformed mask where top 1 - sparsity values are set to inf.
        """
        if sparsity is None:
            sparsity = 0.7

        if not self.explain_graph:
            assert self.hard_edge_mask is not None
            mask_indices = torch.where(self.hard_edge_mask)[0]
            sub_mask = mask[self.hard_edge_mask]
            mask_len = sub_mask.shape[0]
            _, sub_indices = torch.sort(sub_mask, descending=True)
            split_point = int((1 - sparsity) * mask_len)
            important_sub_indices = sub_indices[: split_point]
            important_indices = mask_indices[important_sub_indices]
            unimportant_sub_indices = sub_indices[split_point:]
            unimportant_indices = mask_indices[unimportant_sub_indices]
            trans_mask = mask.clone()
            trans_mask[:] = - float('inf')
            trans_mask[important_indices] = float('inf')
        else:
            _, indices = torch.sort(mask, descending=True)
            mask_len = mask.shape[0]
            split_point = int((1 - sparsity) * mask_len)
            important_indices = indices[: split_point]
            unimportant_indices = indices[split_point:]
            trans_mask = mask.clone()
            trans_mask[important_indices] = float('inf')
            trans_mask[unimportant_indices] = - float('inf')

        return trans_mask


    def visualize_graph(self, node_idx: int, edge_index: Tensor, edge_mask: Tensor, y: Tensor = None,
                           threshold: float = None, nolabel: bool = True, **kwargs) -> Tuple[Axes, nx.DiGraph]:
        r"""Visualizes the subgraph around :attr:`node_idx` given an edge mask
        :attr:`edge_mask`.
        Args:
            node_idx (int): The node id to explain.
            edge_index (LongTensor): The edge indices.
            edge_mask (Tensor): The edge mask.
            y (Tensor, optional): The ground-truth node-prediction labels used
                as node colorings. (default: :obj:`None`)
            threshold (float, optional): Sets a threshold for visualizing
                important edges. If set to :obj:`None`, will visualize all
                edges with transparancy indicating the importance of edges.
                (default: :obj:`None`)
            **kwargs (optional): Additional arguments passed to
                :func:`nx.draw`.
        :rtype: :class:`matplotlib.axes.Axes`, :class:`networkx.DiGraph`
        """
        edge_index, _ = add_self_loops(edge_index, num_nodes=kwargs.get('num_nodes'))
        assert edge_mask.size(0) == edge_index.size(1)

        if self.molecule:
            atomic_num = torch.clone(y)

        # Only operate on a k-hop subgraph around `node_idx`.
        subset, edge_index, _, hard_edge_mask = subgraph(
            node_idx, self.__num_hops__, edge_index, relabel_nodes=True,
            num_nodes=None, flow=self.__flow__())

        edge_mask = edge_mask[hard_edge_mask]

        # --- temp ---
        edge_mask[edge_mask == float('inf')] = 1
        edge_mask[edge_mask == - float('inf')] = 0
        # ---

        if threshold is not None:
            edge_mask = (edge_mask >= threshold).to(torch.float)

        if kwargs.get('dataset_name') == 'ba_lrp':
            y = torch.zeros(edge_index.max().item() + 1,
                            device=edge_index.device)
        if y is None:
            y = torch.zeros(edge_index.max().item() + 1,
                            device=edge_index.device)
        else:
            y = y[subset]

        if self.molecule:
            atom_colors = {6: '#8c69c5', 7: '#71bcf0', 8: '#aef5f1', 9: '#bdc499', 15: '#c22f72', 16: '#f3ea19',
                           17: '#bdc499', 35: '#cc7161'}
            node_colors = [None for _ in range(y.shape[0])]
            for y_idx in range(y.shape[0]):
                node_colors[y_idx] = atom_colors[y[y_idx].int().tolist()]
        else:
            atom_colors = {0: '#8c69c5', 1: '#c56973', 2: '#a1c569', 3: '#69c5ba'}
            node_colors = [None for _ in range(y.shape[0])]
            for y_idx in range(y.shape[0]):
                node_colors[y_idx] = atom_colors[y[y_idx].int().tolist()]


        data = Data(edge_index=edge_index, att=edge_mask, y=y,
                    num_nodes=y.size(0)).to('cpu')
        G = to_networkx(data, node_attrs=['y'], edge_attrs=['att'])
        mapping = {k: i for k, i in enumerate(subset.tolist())}
        G = nx.relabel_nodes(G, mapping)

        kwargs['with_labels'] = kwargs.get('with_labels') or True
        kwargs['font_size'] = kwargs.get('font_size') or 10
        kwargs['node_size'] = kwargs.get('node_size') or 250
        kwargs['cmap'] = kwargs.get('cmap') or 'cool'

        # calculate Graph positions
        pos = nx.kamada_kawai_layout(G)
        ax = plt.gca()

        for source, target, data in G.edges(data=True):
            ax.annotate(
                '', xy=pos[target], xycoords='data', xytext=pos[source],
                textcoords='data', arrowprops=dict(
                    arrowstyle="->",
                    lw=max(data['att'], 0.5) * 2,
                    alpha=max(data['att'], 0.4),  # alpha control transparency
                    color='#e1442a',  # color control color
                    shrinkA=sqrt(kwargs['node_size']) / 2.0,
                    shrinkB=sqrt(kwargs['node_size']) / 2.0,
                    connectionstyle="arc3,rad=0.08",  # rad control angle
                ))
        nx.draw_networkx_nodes(G, pos, node_color=node_colors, **kwargs)
        # define node labels
        if self.molecule:
            if nolabel:
                node_labels = {n: f'{self.table(atomic_num[n].int().item())}'
                               for n in G.nodes()}
                nx.draw_networkx_labels(G, pos, labels=node_labels, **kwargs)
            else:
                node_labels = {n: f'{n}:{self.table(atomic_num[n].int().item())}'
                               for n in G.nodes()}
                nx.draw_networkx_labels(G, pos, labels=node_labels, **kwargs)
        else:
            if not nolabel:
                nx.draw_networkx_labels(G, pos, **kwargs)

        return ax, G

    def eval_related_pred(self, x: Tensor, edge_index: Tensor, edge_masks: List[Tensor], **kwargs):

        node_idx = kwargs.get('node_idx')
        node_idx = 0 if node_idx is None else node_idx  # graph level: 0, node level: node_idx
        related_preds = []

        for ex_label, edge_mask in enumerate(edge_masks):

            self.edge_mask.data = float('inf') * torch.ones(edge_mask.size(), device=self.device)
            ori_pred = self.model(x=x, edge_index=edge_index, **kwargs)

            self.edge_mask.data = edge_mask
            masked_pred = self.model(x=x, edge_index=edge_index, **kwargs)

            # mask out important elements for fidelity calculation
            self.edge_mask.data = - edge_mask  # keep Parameter's id
            maskout_pred = self.model(x=x, edge_index=edge_index, **kwargs)

            # zero_mask
            self.edge_mask.data = - float('inf') * torch.ones(edge_mask.size(), device=self.device)
            zero_mask_pred = self.model(x=x, edge_index=edge_index, **kwargs)

            related_preds.append({'zero': zero_mask_pred[node_idx],
                                  'masked': masked_pred[node_idx],
                                  'maskout': maskout_pred[node_idx],
                                  'origin': ori_pred[node_idx]})

            # Adding proper activation function to the models' outputs.
            related_preds[ex_label] = {key: pred.softmax(0)[ex_label].item()
                                    for key, pred in related_preds[ex_label].items()}

        return related_preds


class WalkBase(ExplainerBase):

    def __init__(self, model: nn.Module, epochs: int = 0, lr: float = 0, explain_graph: bool = False, molecule: bool = False):
        super().__init__(model, epochs, lr, explain_graph, molecule)

    def extract_step(self, x: Tensor, edge_index: Tensor, detach: bool = True, split_fc: bool = False, forward_args: tuple = None):
        '''Gets information about every layer in the graph
        Args:
            forward_args (tuple, optional): Additional arguments to model forward call (other than x and edge_index)
                (default: :obj:`None`)
        '''

        layer_extractor = []
        hooks = []

        def register_hook(module: nn.Module):
            if not list(module.children()) or isinstance(module, MessagePassing):
                hooks.append(module.register_forward_hook(forward_hook))

        def forward_hook(module: nn.Module, input: Tuple[Tensor], output: Tensor):
            # input contains x and edge_index
            if detach:
                layer_extractor.append((module, input[0].clone().detach(), output.clone().detach()))
            else:
                layer_extractor.append((module, input[0], output))

        # --- register hooks ---
        self.model.apply(register_hook)

        # ADDED: OWEN QUEEN --------------
        if forward_args is None:
            _ = self.model(x, edge_index)
        else:
            _ = self.model(x, edge_index, *forward_args)
        # --------------------------------

        for hook in hooks:
            hook.remove()

        # --- divide layer sets ---

        walk_steps = []
        fc_steps = []
        pool_flag = False
        step = {'input': None, 'module': [], 'output': None}
        for layer in layer_extractor:
            if isinstance(layer[0], MessagePassing) or isinstance(layer[0], GNNPool):
                #print('Cond 1')
                if isinstance(layer[0], GNNPool):
                    pool_flag = True
                if step['module'] and step['input'] is not None:
                    walk_steps.append(step)
                step = {'input': layer[1], 'module': [], 'output': None}
            if pool_flag and split_fc and isinstance(layer[0], nn.Linear):
                #print('Cond 2')
                if step['module']:
                    fc_steps.append(step)
                step = {'input': layer[1], 'module': [], 'output': None}
            # ADDED: OWEN QUEEN -----------------
            # This is GCN-specific code; won't work for SAGEConv, etc. 
            elif isinstance(layer[0], nn.Linear):
                if step['module'] and step['input'] is not None:
                    walk_steps.append(step)
                step = {'input': layer[1], 'module': [], 'output': None}
            # -----------------------------------
            step['module'].append(layer[0])
            step['output'] = layer[2]


        for walk_step in walk_steps:
            if hasattr(walk_step['module'][0], 'nn') and walk_step['module'][0].nn is not None:
                # We don't allow any outside nn during message flow process in GINs
                walk_step['module'] = [walk_step['module'][0]]


        if split_fc:
            if step['module']:
                fc_steps.append(step)
            return walk_steps, fc_steps
        else:
            fc_step = step


        return walk_steps, fc_step