import random
import torch
import matplotlib.pyplot as plt

from graphxai.explainers import GuidedBP
from graphxai.explainers.utils.visualizations import visualize_subgraph_explanation
from graphxai.gnn_models.node_classification import BA_Houses, GCN, train, test

n = 300
m = 2
num_houses = 20

bah = BA_Houses(n, m)
data, inhouse = bah.get_data(num_houses, multiple_features=True)

model = GCN(64, input_feat = 3, classes = 2)
print(model)

optimizer = torch.optim.Adam(model.parameters(), lr=0.01, weight_decay=5e-4)
criterion = torch.nn.CrossEntropyLoss()

for epoch in range(1, 201):
    loss = train(model, optimizer, criterion, data)
    acc = test(model, data)
    print(f'Epoch: {epoch:03d}, Loss: {loss:.4f}, Test Acc: {acc:.4f}')

node_idx = random.choice(inhouse)

gbp = GuidedBP(model, criterion)
exp, khop_info = gbp.get_explanation_node(data.x, data.y, edge_index = data.edge_index, node_idx = int(node_idx))
exp_list = [exp['feature'][i,0].item() for i in khop_info[0]] # Index degree with [i,0]
print(exp_list)
subgraph_eidx = khop_info[1]

fig, (ax1, ax2) = plt.subplots(1, 2)

y_subgraph = data.y[khop_info[0]].tolist()
print(y_subgraph)

# visualize_subgraph_explanation(subgraph_eidx, node_weights = data.y.tolist(), node_idx = int(node_idx), 
#     ax = ax1, show = False)
visualize_subgraph_explanation(subgraph_eidx, node_weights = y_subgraph, node_idx = int(node_idx), 
    ax = ax1, show = False)
ax1.set_title('Ground Truth')

visualize_subgraph_explanation(subgraph_eidx, exp_list, node_idx = int(node_idx), 
    ax = ax2, show = False)
ax2.set_title('Guided Backprop (Explanation wrt Degree)')

model.eval()
pred = model(data.x, data.edge_index)[node_idx, :].reshape(-1, 1)

ymin, ymax = ax1.get_ylim()
xmin, xmax = ax1.get_xlim()
ax1.text(xmin, ymax - 0.1*(ymax-ymin), 'Label = {:d}'.format(data.y[node_idx].item()))
ax1.text(xmin, ymax - 0.15*(ymax-ymin), 'Pred  = {:d}'.format(pred.argmax(dim=0).item()))

plt.show()