"""Recompute the SSIM of the three overviews on the ground-truth area, exactly as
evaluate_area.py (section E) does, and keep every image that was compared.

    python ssim_images.py <outputs dir> <results dir>
    (cvision env: numpy, scikit-image, matplotlib; inputs are the .npy written by vti2npy.py)

Method (unchanged from evaluate_area.py): block row 0 of the area is its first 16 levels
(z = 1..16). Each overview's first level is blown up to full resolution by repeating each value
over 16 x 16 cells; SSIM (skimage.metrics.structural_similarity, default 7 x 7 window,
data_range = max - min of the 16 levels) is computed against each of the 16 levels and averaged.
The stride image is the ground truth sampled at every 16th cell of level z = 1 (equal to Sieve's
stride overview there; evaluate_area.py's cross-check A).

Writes to <results dir>:
  ssim_values.json        mean and per-level SSIM per overview, with the earlier values for comparison
  ssim_images.npz         every compared array (float64): truth_row0 (1024 x 1024 x 16),
                          up_<overview> (1024 x 1024), ssim_map_<overview> (1024 x 1024 x 16)
  png/truth_z<k>.png      the 16 full-resolution levels, one shared colour scale
  png/up_<overview>.png   each overview blown up, same colour scale
  png/ssim_<overview>_z<k>.png   the local SSIM map of each comparison (scale -1..1)
  png/overview_sheet.png  one sheet: level z = 1 and the three overviews, with their SSIM
Images: x (streamwise) left to right, y (spanwise) bottom to top; colour scale fixed by the
16 levels' range, so the images can be compared by eye.
"""
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from skimage.metrics import structural_similarity

OUT, RES = sys.argv[1:3]
PNG = os.path.join(RES, "png")
os.makedirs(PNG, exist_ok=True)
B = 16
NAMES = {"stride": "Stride (every 16th cell)", "mean": "Block mean", "min": "Block minimum"}

truth = np.load(os.path.join(OUT, "s2_truth.u.npy"))
amean = np.load(os.path.join(OUT, "s3_area_mean16.u.npy"))
amin = np.load(os.path.join(OUT, "s3_area_min16.u.npy"))

row0 = truth[:, :, :B]
lo, hi = float(row0.min()), float(row0.max())
rng = hi - lo
up = lambda a: np.repeat(np.repeat(a, B, axis=0), B, axis=1)
view = {"stride": up(truth[::B, ::B, 0]), "mean": up(amean[:, :, 0]), "min": up(amin[:, :, 0])}

values, maps = {}, {}
for name, img in view.items():
    s, m = [], []
    for k in range(B):
        v, smap = structural_similarity(row0[:, :, k], img, data_range=rng, full=True)
        s.append(float(v))
        m.append(smap)
    values[name] = {"mean": float(np.mean(s)), "per_level": s}
    maps[name] = np.stack(m, axis=-1)

prev_path = os.path.join(os.path.dirname(os.path.abspath(RES)), "area_eval.json")
prev = json.load(open(prev_path))["operators"] if os.path.exists(prev_path) else {}
for name in values:
    if name in prev:
        values[name]["earlier_mean"] = prev[name]["ssim_row0_mean"]
        values[name]["equal_to_earlier"] = (prev[name]["ssim_row0_per_plane"]
                                            == values[name]["per_level"])
json.dump({"data_range": [lo, hi], "levels": "z = 1..16 (block row 0)", "overviews": values},
          open(os.path.join(RES, "ssim_values.json"), "w"), indent=1)

np.savez_compressed(os.path.join(RES, "ssim_images.npz"), truth_row0=row0,
                    **{f"up_{n}": a for n, a in view.items()},
                    **{f"ssim_map_{n}": a for n, a in maps.items()})


def png(path, a, vmin, vmax, cmap):
    plt.imsave(path, a.T, origin="lower", vmin=vmin, vmax=vmax, cmap=cmap)


for k in range(B):
    png(os.path.join(PNG, f"truth_z{k + 1:02d}.png"), row0[:, :, k], lo, hi, "viridis")
for name, img in view.items():
    png(os.path.join(PNG, f"up_{name}.png"), img, lo, hi, "viridis")
    for k in range(B):
        png(os.path.join(PNG, f"ssim_{name}_z{k + 1:02d}.png"), maps[name][:, :, k], -1, 1, "RdBu")

fig, axes = plt.subplots(1, 4, figsize=(14, 3.9), constrained_layout=True)
panels = [("Full resolution, z = 1", row0[:, :, 0], None)] + \
         [(NAMES[n], view[n], values[n]["mean"]) for n in view]
for ax, (title, a, v) in zip(axes, panels):
    im = ax.imshow(a.T, origin="lower", vmin=lo, vmax=hi, cmap="viridis")
    ax.set_title(title, fontsize=10)
    ax.set_xticks([]), ax.set_yticks([])
    if v is not None:
        ax.set_xlabel(f"SSIM {v:.3f} (mean over z = 1..16)", fontsize=9)
fig.colorbar(im, ax=axes, shrink=0.8, label="u")
fig.savefig(os.path.join(PNG, "overview_sheet.png"), dpi=150)

for name, v in values.items():
    tail = (f"  earlier {v['earlier_mean']:.4f}, per level identical: {v['equal_to_earlier']}"
            if "earlier_mean" in v else "")
    print(f"{name:6s} SSIM {v['mean']:.4f}{tail}")
