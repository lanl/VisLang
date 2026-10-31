#!/usr/bin/env python3
"""The wind on the lee slope at full resolution, from the Sieve Detail
(x -100..200, y -120..120 m, 39 terrain-following levels, one .vts per timestep).

    python cs2_leeslope.py DETAIL_DIR -o leeslope.csv [--strip 50] [--ahead 60]

Per timestep, over the central strip (|y| <= --strip):
  front_x        leading edge of theta > 400 K at the lowest level, interpolated
                 (as cs2_front.py; empty while the front is upwind of the box)
  ahead_u_kK     mean u (m/s) from the front to --ahead m downwind, at level K
                 (K = 0..20; heights above ground in levels.csv)
  ahead_zero_m   the reversed layer's depth: from level 1 (1.2 m) up, the
                 lowest height where that mean profile crosses from u < 0 below
                 to u >= 0 above (0 when u >= 0 at level 1; empty when it never
                 turns positive within level 20). Level 0 is left out: it is the
                 surface layer, where u is near zero at every timestep.
  fixed_u_kK     mean u at level K over a fixed stretch of the lee slope
                 (--fixed, default x 100..160 m) -- the same ground every
                 timestep, fire or not
  flank_u_kK     the same stretch at |y| >= 90 m (outside the burning strip)
"""
import argparse
import csv
import glob
import os
import re

import numpy as np
import pyvista as pv

T, NK = 400.0, 21

ap = argparse.ArgumentParser()
ap.add_argument("detail")
ap.add_argument("-o", "--out", required=True)
ap.add_argument("--strip", type=float, default=50.0)
ap.add_argument("--ahead", type=float, default=60.0)
ap.add_argument("--fixed", type=float, nargs=2, default=(100.0, 160.0))
a = ap.parse_args()


def load(path):
    m = pv.read(path)
    nx, ny, nz = m.dimensions
    P = np.asarray(m.points).reshape(nz, ny, nx, 3).transpose(2, 1, 0, 3)
    A = {n: np.asarray(m.point_data[n]).reshape((nx, ny, nz), order="F")
         for n in m.point_data.keys()}
    return P, A


def edge(x, th):
    hot = np.flatnonzero(th > T)
    if hot.size == 0:
        return None
    i = hot[-1]
    if i + 1 >= len(x):
        return float(x[i])
    return float(x[i] + (th[i] - T) / (th[i] - th[i + 1]) * (x[i + 1] - x[i]))


def zero_depth(h, prof):
    if prof[0] >= 0:
        return 0.0
    for k in range(1, len(prof)):
        if prof[k] >= 0:
            return float(h[k - 1] + (0 - prof[k - 1]) / (prof[k] - prof[k - 1]) * (h[k] - h[k - 1]))
    return None


files = sorted(glob.glob(os.path.join(a.detail, "timestep#*.vts")),
               key=lambda p: int(re.search(r"#(\d+)", p).group(1)))
ks = range(NK)
cols = (["label", "front_x", "ahead_zero_m"] + [f"ahead_u_k{k}" for k in ks]
        + [f"fixed_u_k{k}" for k in ks] + [f"flank_u_k{k}" for k in ks])
rows_out, heights = [], None
for p in files:
    P, A = load(p)
    x, y = P[:, 0, 0, 0], P[0, :, 0, 1]
    h = P[..., 2] - P[..., :1, 2]
    strip = np.flatnonzero(np.abs(y) <= a.strip)
    flank = np.flatnonzero(np.abs(y) >= 90)
    if heights is None:                      # level heights above ground, mid-box
        heights = h[len(x) // 2, len(y) // 2, :NK]
    fronts = [e for e in (edge(x, A["theta"][:, j, 0]) for j in strip) if e is not None]
    f = max(fronts) if fronts else None
    row = {"label": int(re.search(r"#(\d+)", p).group(1)), "front_x": f}
    u = A["u"]
    if f is not None and f > x[0]:
        sel = np.flatnonzero((x > f) & (x <= f + a.ahead))
        if sel.size:
            prof = u[np.ix_(sel, strip, ks)].mean(axis=(0, 1))
            hh = h[np.ix_(sel, strip, ks)].mean(axis=(0, 1))
            row.update({f"ahead_u_k{k}": prof[k] for k in ks})
            row["ahead_zero_m"] = zero_depth(hh[1:], prof[1:])
    fx = np.flatnonzero((x >= a.fixed[0]) & (x <= a.fixed[1]))
    row.update({f"fixed_u_k{k}": v for k, v in
                zip(ks, u[np.ix_(fx, strip, ks)].mean(axis=(0, 1)))})
    row.update({f"flank_u_k{k}": v for k, v in
                zip(ks, u[np.ix_(fx, flank, ks)].mean(axis=(0, 1)))})
    rows_out.append(row)

with open(a.out, "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=cols)
    w.writeheader()
    for r in rows_out:
        w.writerow({c: ("" if r.get(c) is None else round(float(r[c]), 3)) for c in cols})
with open(os.path.join(os.path.dirname(a.out), "levels.csv"), "w") as fh:
    fh.write("level,height_above_ground_m\n")
    for k, v in enumerate(heights):
        fh.write(f"{k},{v:.2f}\n")
print(f"{len(rows_out)} timesteps -> {a.out}")
