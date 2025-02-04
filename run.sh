export CUDA_VISIBLE_DEVICES=1

python3 scripts/run.py \
        --training_data "data/nerf/artery/transform_matrices.json" \
        --save_dir "results/rgb_out.pt" \
        --n_steps "2000" \
        --test_transforms "data/nerf/artery/test_transform.json" \