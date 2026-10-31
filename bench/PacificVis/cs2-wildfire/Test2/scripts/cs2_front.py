#!/usr/bin/env python3
"""The fire front and the near-ground wind ahead of it, per timestep, from a
Sieve overview (one .vts per timestep, any stride).

    python cs2_front.py OVERVIEW_DIR -o front.csv [--strip 50] [--ahead 60]

Per timestep:
  front_x      the leading edge of the fire: at the lowest grid level (≈0.75 m
               above ground), in each grid row with |y| <= --strip, the largest
               x where theta crosses 400 K going downwind, interpolated linearly
               between the two grid points around the crossing; the largest
               over the rows.
  u_ahead_k0   mean u (m/s) at the lowest level over the strip's rows, from the
               front to --ahead m downwind of it.
  u_ahead_k1   the same at the next level the overview kept (≈11-14 m up at
               stride 8).
  fire_*       bounding box of theta > 400 K over the whole grid (grid points,
               no interpolation), and fire_xmax_interp: its downwind edge
               interpolated as front_x is, over every row and level -- the
               quantity fire_roi_bounds.csv (full resolution) gives as xmax - 30.
  top400_agl   highest point with theta > 400 K, metres above the ground below it.
  w_max        largest vertical wind (m/s) anywhere.
"""
import argparse
import csv
import glob
import os
import re

import numpy as np
import pyvista as pv

T = 400.0


def load(path):
    m = pv.read(path)
    nx, ny, nz = m.dimensions
    P = np.asarray(m.points).reshape(nz, ny, nx, 3).transpose(2, 1, 0, 3)
    A = {n: np.asarray(m.point_data[n]).reshape((nx, ny, nz), order="F")
         for n in m.point_data.keys()}
    return P, A


def leading_edge(x, th):
    """Largest x along one grid line (x increasing) where th crosses T going
    downwind, interpolated; None if th never exceeds T."""
    hot = np.flatnonzero(th > T)
    if hot.size == 0:
        return None
    i = hot[-1]
    if i + 1 >= len(x):
        return float(x[i])
    return float(x[i] + (th[i] - T) / (th[i] - th[i + 1]) * (x[i + 1] - x[i]))


def measure(P, A, strip, ahead):
    th, u, w = A["theta"], A["u"], A["w"]
    x = P[:, 0, 0, 0]
    rows = np.flatnonzero(np.abs(P[0, :, 0, 1]) <= strip)
    fronts = [leading_edge(x, th[:, j, 0]) for j in rows]
    fronts = [f for f in fronts if f is not None]
    out = {"front_x": max(fronts) if fronts else None, "n_rows_burning": len(fronts)}
    for k, name in ((0, "u_ahead_k0"), (1, "u_ahead_k1")):
        if out["front_x"] is None:
            out[name] = None
            continue
        sel = (x > out["front_x"]) & (x <= out["front_x"] + ahead)
        out[name] = float(u[np.ix_(sel, rows, [k])].mean()) if sel.any() else None
    hot = th > T
    out["n_fire_pts"] = int(hot.sum())
    if hot.any():
        X, Y = P[..., 0][hot], P[..., 1][hot]
        out.update(fire_xmin=float(X.min()), fire_xmax=float(X.max()),
                   fire_ymin=float(Y.min()), fire_ymax=float(Y.max()))
        edges = [leading_edge(x, th[:, j, k]) for j in range(th.shape[1])
                 for k in range(th.shape[2])]
        out["fire_xmax_interp"] = max(e for e in edges if e is not None)
        agl = P[..., 2] - P[..., :1, 2]
        out["top400_agl"] = float(agl[hot].max())
    out["w_max"] = float(w.max())
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("overview")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--strip", type=float, default=50.0)
    ap.add_argument("--ahead", type=float, default=60.0)
    a = ap.parse_args()
    files = sorted(glob.glob(os.path.join(a.overview, "timestep#*.vts")),
                   key=lambda p: int(re.search(r"#(\d+)", p).group(1)))
    cols = ["label", "front_x", "n_rows_burning", "u_ahead_k0", "u_ahead_k1",
            "n_fire_pts", "fire_xmin", "fire_xmax", "fire_xmax_interp", "fire_ymin",
            "fire_ymax", "top400_agl", "w_max"]
    with open(a.out, "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=cols)
        wr.writeheader()
        for p in files:
            P, A = load(p)
            row = {"label": int(re.search(r"#(\d+)", p).group(1)),
                   **measure(P, A, a.strip, a.ahead)}
            wr.writerow({c: ("" if row.get(c) is None else
                             round(row[c], 3) if isinstance(row[c], float) else row[c])
                         for c in cols})
    print(f"{len(files)} timesteps -> {a.out}")


if __name__ == "__main__":
    main()
