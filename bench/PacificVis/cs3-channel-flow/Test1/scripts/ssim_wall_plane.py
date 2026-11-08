#!/usr/bin/env python3
"""The SSIM numbers in the paper: each overview against the plane nearest the wall
(z = 1, y+ = 0.07), with a featureless image (every cell = the plane's mean u) as a
baseline, data_range = that plane's own range.

    python ssim_wall_plane.py results/eval/ssim/ssim_images.npz results/eval/ssim/ssim_wall_plane.json
    (cvision env: numpy, scikit-image)

Inputs are the arrays ssim_images.py saved (truth_row0, up_<overview>). The earlier
ssim_values.json used the range of all 16 levels of block row 0, far wider than the wall
plane's, which pushes every score toward 1; this file uses the compared plane's own range.
"""
import json
import sys

import numpy as np
from skimage.metrics import structural_similarity as ssim

src, out = sys.argv[1:3]
z = np.load(src)
T = z["truth_row0"][:, :, 0]
imgs = {k[3:]: z[k] for k in z.files if k.startswith("up_")}
imgs["featureless"] = np.full_like(T, T.mean())
dr = float(T.max() - T.min())
res = {"plane": "z = 1 (y+ = 0.07), the 1024 x 1024 reference area",
       "data_range": dr, "fraction_u_negative": float((T < 0).mean()),
       "ssim": {k: float(ssim(T, v, data_range=dr)) for k, v in imgs.items()}}
json.dump(res, open(out, "w"), indent=1)
print(json.dumps(res, indent=1))
