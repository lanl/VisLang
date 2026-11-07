#!/usr/bin/env python3
"""The fire front per timestep from a saved .vts series (Sieve's overview, or any
other route's), with the definition the evaluation pass uses (server/eval_fullres.py):
at grid level 0, in each grid row with |y| <= 50 m, the largest x where theta crosses
400 K going downwind, interpolated; the largest over those rows.

    python front_overview.py SERIES_DIR OUT.csv

Also, for the overview: u_ahead_lowest and u_ahead_next, the mean u over the same rows
from the front to 60 m ahead of it, at the lowest level the series holds and the next one
(at stride 8: grid levels 0 and 8, 11.8 m over the box). This is what the overview alone
says about the near-ground wind ahead of the front.
"""
import csv
import glob
import os
import re
import sys

import numpy as np
import pyvista as pv

T, STRIP, AHEAD = 400.0, 50.0, 60.0


def leading_edge(x, th):
    hot = np.flatnonzero(th > T)
    if hot.size == 0:
        return None
    i = hot[-1]
    if i + 1 >= len(x):
        return float(x[i])
    return float(x[i] + (th[i] - T) / (th[i] - th[i + 1]) * (x[i + 1] - x[i]))


def front(path):
    m = pv.read(path)
    nx, ny, nz = m.dimensions
    P = np.asarray(m.points).reshape(nz, ny, nx, 3).transpose(2, 1, 0, 3)
    th = np.asarray(m.point_data["theta"]).reshape((nx, ny, nz), order="F")
    u = np.asarray(m.point_data["u"]).reshape((nx, ny, nz), order="F")
    x = P[:, 0, 0, 0]
    rows = np.flatnonzero(np.abs(P[0, :, 0, 1]) <= STRIP)
    fr = [f for f in (leading_edge(x, th[:, j, 0]) for j in rows) if f is not None]
    fx = max(fr) if fr else None
    ahead = [None, None]
    if fx is not None:
        sel = (x > fx) & (x <= fx + AHEAD)
        if sel.any():
            ahead = [float(u[np.ix_(sel, rows, [k])].mean()) for k in (0, 1)]
    return fx, len(fr), ahead


def main(src, out):
    files = sorted(glob.glob(os.path.join(src, "timestep#*.vts")),
                   key=lambda p: int(re.search(r"#(\d+)", p).group(1)))
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["label", "front_x", "n_rows", "u_ahead_lowest", "u_ahead_next"])
        for p in files:
            fx, n, ahead = front(p)
            w.writerow([int(re.search(r"#(\d+)", p).group(1)),
                        "" if fx is None else round(fx, 4), n]
                       + ["" if v is None else round(v, 4) for v in ahead])
    print(f"{len(files)} timesteps -> {out}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
