#!/usr/bin/env python3
"""Evaluation pass (not a baseline): read every timestep of the series at full
resolution on the server and write the reference quantities Case Study 2 is
checked against.

    python eval_fullres.py SERIES_DIR OUT_DIR

Writes, in OUT_DIR:
  fronts.csv     label, stride, front_x, n_rows: the fire front at strides 1 (full
                 resolution), 2, 4, 8, 16, each from the same read, sampled as
                 Sieve's subsample keeps points (indices 0, s, 2s, ... on every axis)
  profiles.csv   label, rows (strip|flanks), bin (metres ahead of the front),
                 level, u_mean, n: full-resolution u ahead of the front
  lee.csv        label, rows, level, u_mean, n: u on fixed lee ground (x 100-160 m)
  ambient.csv    label, level, height, u_mean: u at the upwind edge (first 10 x
                 columns, all y), at the level nearest 10 m above level 0
  levels.csv     level, height_mean, height_sd: height above level 0 (m)

Definitions (identical at every stride):
  front_x   in each grid row with |y| <= STRIP at grid level 0, the largest x where
            theta crosses T going downwind, interpolated linearly between the two
            grid points around the crossing; the largest over those rows.
  strip     rows with |y| <= 50 m; flanks: rows with |y| >= 90 m.
  bins      x - front_x in (0,10], (10,30], (30,60], (60,120] m.
Heights are measured from grid level 0: the files do not say whether level 0 is
the ground surface or the first cell centre.
"""
import csv
import glob
import os
import re
import sys

import numpy as np
import vtk
from vtk.util.numpy_support import vtk_to_numpy

T, STRIP, FLANK = 400.0, 50.0, 90.0
STRIDES = (1, 2, 4, 8, 16)
BINS = ((0, 10), (10, 30), (30, 60), (60, 120))
LEE = (100.0, 160.0)
NLEV = 30                                    # levels kept in the profiles (~60 m)


def read(path, names):
    r = vtk.vtkXMLStructuredGridReader()
    r.SetFileName(path)
    r.UpdateInformation()
    for i in range(r.GetNumberOfPointArrays()):
        n = r.GetPointArrayName(i)
        r.SetPointArrayStatus(n, 1 if n in names else 0)
    r.Update()
    g = r.GetOutput()
    d = [0, 0, 0]
    g.GetDimensions(d)
    nx, ny, nz = d
    P = vtk_to_numpy(g.GetPoints().GetData()).reshape(nz, ny, nx, 3).transpose(2, 1, 0, 3)
    A = {n: vtk_to_numpy(g.GetPointData().GetArray(n)).reshape((nx, ny, nz), order="F")
         for n in names}
    return P, A


def leading_edge(x, th):
    hot = np.flatnonzero(th > T)
    if hot.size == 0:
        return None
    i = hot[-1]
    if i + 1 >= len(x):
        return float(x[i])
    return float(x[i] + (th[i] - T) / (th[i] - th[i + 1]) * (x[i + 1] - x[i]))


def front(P, th):
    x = P[:, 0, 0, 0]
    rows = np.flatnonzero(np.abs(P[0, :, 0, 1]) <= STRIP)
    fr = [leading_edge(x, th[:, j, 0]) for j in rows]
    fr = [f for f in fr if f is not None]
    return (max(fr) if fr else None), len(fr)


def main(src, out):
    os.makedirs(out, exist_ok=True)
    files = sorted(glob.glob(os.path.join(src, "fire#*.vts")),
                   key=lambda p: int(re.search(r"#(\d+)", p).group(1)))
    f_fr = csv.writer(open(os.path.join(out, "fronts.csv"), "w", newline=""))
    f_pr = csv.writer(open(os.path.join(out, "profiles.csv"), "w", newline=""))
    f_le = csv.writer(open(os.path.join(out, "lee.csv"), "w", newline=""))
    f_am = csv.writer(open(os.path.join(out, "ambient.csv"), "w", newline=""))
    f_fr.writerow(["label", "stride", "front_x", "n_rows"])
    f_pr.writerow(["label", "rows", "bin", "level", "u_mean", "n"])
    f_le.writerow(["label", "rows", "level", "u_mean", "n"])
    f_am.writerow(["label", "level", "height", "u_mean"])
    levels_done = False
    for path in files:
        label = int(re.search(r"#(\d+)", path).group(1))
        P, A = read(path, ("theta", "u"))
        th, u = A["theta"], A["u"]
        for s in STRIDES:
            fx, n = front(P[::s, ::s, ::s], th[::s, ::s, ::s])
            f_fr.writerow([label, s, "" if fx is None else round(fx, 4), n])
        h = P[..., 2] - P[..., :1, 2]                     # height above level 0
        if not levels_done:
            with open(os.path.join(out, "levels.csv"), "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["level", "height_mean", "height_sd"])
                for k in range(h.shape[2]):
                    w.writerow([k, round(float(h[..., k].mean()), 4),
                                round(float(h[..., k].std()), 4)])
            levels_done = True
        x, y = P[:, 0, 0, 0], P[0, :, 0, 1]
        sets = {"strip": np.abs(y) <= STRIP, "flanks": np.abs(y) >= FLANK}
        fx, _ = front(P, th)
        for name, rows in sets.items():
            if fx is not None:
                for lo, hi in BINS:
                    sel = (x > fx + lo) & (x <= fx + hi)
                    for k in range(NLEV):
                        v = u[np.ix_(sel, rows, [k])]
                        f_pr.writerow([label, name, f"{lo}-{hi}", k,
                                       round(float(v.mean()), 5) if v.size else "", v.size])
            sel = (x >= LEE[0]) & (x <= LEE[1])
            for k in range(NLEV):
                v = u[np.ix_(sel, rows, [k])]
                f_le.writerow([label, name, k, round(float(v.mean()), 5), v.size])
        k10 = int(np.argmin(np.abs(h[:10].mean(axis=(0, 1)) - 10.0)))
        f_am.writerow([label, k10, round(float(h[:10, :, k10].mean()), 3),
                       round(float(u[:10, :, k10].mean()), 5)])
        print(label, file=sys.stderr, flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
