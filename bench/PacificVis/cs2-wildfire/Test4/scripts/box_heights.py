#!/usr/bin/env python3
"""Height of each grid level above the lowest level, averaged over the box (the crest
and lee slope), from one timestep of the box series (the grid does not move).

    python box_heights.py BOX_SERIES_DIR OUT.csv
"""
import glob
import os
import sys

import numpy as np
import pyvista as pv

m = pv.read(sorted(glob.glob(os.path.join(sys.argv[1], "timestep#*.vts")))[0])
nx, ny, nz = m.dimensions
z = np.asarray(m.points)[:, 2].reshape(nz, ny, nx)
h = z - z[:1]
with open(sys.argv[2], "w") as f:
    f.write("level,height_m,height_sd\n")
    for k in range(nz):
        f.write(f"{k},{h[k].mean():.4f},{h[k].std():.4f}\n")
print(f"{nz} levels -> {sys.argv[2]}")
