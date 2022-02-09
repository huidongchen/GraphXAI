#!/bin/bash
#SBATCH -c 1
#SBATCH -t 0-08:00
#SBATCH -p gpu_quad
#SBATCH --gres=gpu:1
#SBATCH --mem=10G
#SBATCH -o mult_outs/SUBX_fNUM.out
#SBATCH -e mult_outs/SUBX_fNUM.err
#SBATCH -J SUBX_fNUM

source activate GXAI
python3 stability_saver.py --exp_method SUBX --model GIN --save_dir SUBX_results/stab --num_splits 50 --my_split NUM
