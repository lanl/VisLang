#!/usr/bin/env python3
"""Two-pass baseline, pass 1: read each downloaded whole file and measure the
front and the wind ahead of it (the same measurement as the Sieve route, from
cs2_front.measure), at full resolution.

    python pass1_overview.py DIR -o front_every5.csv
"""
import argparse, csv, glob, os, re, sys
import numpy as np
import vtk
from vtk.util.numpy_support import vtk_to_numpy
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from cs2_front import measure

ap = argparse.ArgumentParser()
ap.add_argument("dir"); ap.add_argument("-o", "--out", required=True)
a = ap.parse_args()
files = sorted(glob.glob(os.path.join(a.dir, "fire#*.vts")),
               key=lambda p: int(re.search(r"#(\d+)", p).group(1)))
rows = []
for path in files:
    r = vtk.vtkXMLStructuredGridReader()
    r.SetFileName(path)
    r.Update()
    g = r.GetOutput()
    d = [0, 0, 0]
    g.GetDimensions(d)
    nx, ny, nz = d
    P = vtk_to_numpy(g.GetPoints().GetData()).reshape(nz, ny, nx, 3).transpose(2, 1, 0, 3)
    A = {n: vtk_to_numpy(g.GetPointData().GetArray(n)).reshape((nx, ny, nz), order="F")
         for n in ("u", "w", "theta")}
    rows.append({"label": int(re.search(r"#(\d+)", path).group(1)), **measure(P, A, 50.0, 60.0)})
with open(a.out, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
print(f"{len(rows)} files -> {a.out}")
