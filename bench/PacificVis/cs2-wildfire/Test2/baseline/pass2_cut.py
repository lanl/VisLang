#!/usr/bin/env python3
"""Two-pass baseline, pass 2: cut the lee-slope box out of each downloaded whole
file at full resolution and keep u, v, w, theta: the smallest block of grid cells
holding every point inside x -100..200, y -120..120, z 0..350 m (the rule Sieve's
region applies), written as .vts.

    python pass2_cut.py DIR OUTDIR [labels…]
"""
import os, re, sys, glob
import numpy as np
import vtk
from vtk.util.numpy_support import vtk_to_numpy

BOX = ((-100.0, 200.0), (-120.0, 120.0), (0.0, 350.0))
KEEP = ("u", "v", "w", "theta")
src, out = sys.argv[1], sys.argv[2]
want = {int(v) for v in sys.argv[3:]}
os.makedirs(out, exist_ok=True)
for path in sorted(glob.glob(os.path.join(src, "fire#*.vts"))):
    label = int(re.search(r"#(\d+)", path).group(1))
    if want and label not in want:
        continue
    r = vtk.vtkXMLStructuredGridReader()
    r.SetFileName(path)
    r.Update()
    g = r.GetOutput()
    d = [0, 0, 0]
    g.GetDimensions(d)
    nx, ny, nz = d
    P = vtk_to_numpy(g.GetPoints().GetData()).reshape(nz, ny, nx, 3).transpose(2, 1, 0, 3)
    inside = np.ones((nx, ny, nz), dtype=bool)
    for axis, (lo, hi) in enumerate(BOX):
        inside &= (P[..., axis] >= lo) & (P[..., axis] <= hi)
    ext = g.GetExtent()                      # the file's own index numbering (x starts at 251)
    voi = []
    for axis in range(3):
        hits = np.flatnonzero(inside.any(axis=tuple(a for a in range(3) if a != axis)))
        voi += [ext[2 * axis] + int(hits[0]), ext[2 * axis] + int(hits[-1])]
    cut = vtk.vtkExtractGrid()
    cut.SetInputData(g)
    cut.SetVOI(*voi)
    cut.Update()
    o = cut.GetOutput()
    for name in [o.GetPointData().GetArrayName(i) for i in range(o.GetPointData().GetNumberOfArrays())]:
        if name not in KEEP:
            o.GetPointData().RemoveArray(name)
    w = vtk.vtkXMLStructuredGridWriter()
    w.SetFileName(os.path.join(out, os.path.basename(path)))
    w.SetInputData(o)
    w.Write()
    print(f"#{label}: VOI {voi}")
