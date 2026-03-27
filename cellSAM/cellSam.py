import os
import numpy as np
import tifffile as tiff
from cellSAM import segment_cellular_image

# ===== PATH =====
image_dir = "/home/ramanlab/Fileserver/cole/Imaging-Past/Recordings-20260324/full-odor-pannel_GH146x474_female_d_post0h_20260324_124307/fly_1_geno_GH146x474/trial_001_OFM_H/images/images/"

# ===== OUTPUT =====
output_dir = os.path.join(image_dir, "cellsam_outputs")
os.makedirs(output_dir, exist_ok=True)

# ===== PROCESS =====
for fname in sorted(os.listdir(image_dir)):
    if not fname.endswith(".tif"):
        continue

    path = os.path.join(image_dir, fname)

    # Load TIFF → numpy
    img = tiff.imread(path)

    # Debug info (important)
    print(f"Processing {fname} | shape={img.shape} dtype={img.dtype}")

    # If grayscale, ensure shape is valid
    if img.ndim == 2:
        pass
    elif img.ndim == 3:
        # If channels first (rare), fix if needed
        if img.shape[0] < 10:  # heuristic
            img = np.transpose(img, (1, 2, 0))
    else:
        raise ValueError(f"Unexpected shape: {img.shape}")

    # Run CellSAM
    mask, flows, styles = segment_cellular_image(
        img,
        device='cuda'  # change to 'cpu' if needed
    )

    # Save mask
    out_path = os.path.join(output_dir, fname.replace(".tif", "_mask.npy"))
    np.save(out_path, mask)

    print(f"Saved → {out_path}")