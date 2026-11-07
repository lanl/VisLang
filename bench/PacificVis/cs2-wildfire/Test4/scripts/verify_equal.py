#!/usr/bin/env python3
"""Check that two routes delivered the same data: for every timestep both series
hold, the grid positions and every field compared bit for bit (sha256 of the
arrays), and the grid dimensions.

    python verify_equal.py SERIES_A SERIES_B OUT.txt
"""
import glob
import hashlib
import os
import re
import sys

import numpy as np
import pyvista as pv


def digest(a):
    return hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()[:16]


def series(d):
    return {int(re.search(r"#(\d+)", p).group(1)): p
            for p in glob.glob(os.path.join(d, "timestep#*.vts"))}


a, b, out = series(sys.argv[1]), series(sys.argv[2]), sys.argv[3]
lines, bad = [], 0
for n in sorted(set(a) | set(b)):
    if n not in a or n not in b:
        lines.append(f"#{n}: only in {'A' if n in a else 'B'}")
        bad += 1
        continue
    ma, mb = pv.read(a[n]), pv.read(b[n])
    keys = sorted(set(ma.point_data.keys()) | set(mb.point_data.keys()))
    same = {"points": digest(ma.points) == digest(mb.points),
            "dims": tuple(ma.dimensions) == tuple(mb.dimensions)}
    for k in keys:
        same[k] = (k in ma.point_data and k in mb.point_data
                   and ma.point_data[k].dtype == mb.point_data[k].dtype
                   and digest(ma.point_data[k]) == digest(mb.point_data[k]))
    ok = all(same.values())
    bad += not ok
    lines.append(f"#{n}: {'identical' if ok else 'DIFFERENT ' + str(same)} "
                 f"dims {tuple(ma.dimensions)} fields {keys}")
lines.append(f"{len(set(a) | set(b)) - bad} of {len(set(a) | set(b))} timesteps identical")
open(out, "w").write("\n".join(lines) + "\n")
print(lines[-1])
