#!/usr/bin/env python3
"""Cut a FIRETEC .vts series as Case Study 2's two Sieve requests do.

    python cut_series.py SRC OUT overview              # all timesteps, every 8th point, 5 fields
    python cut_series.py SRC OUT box 9000 45000        # timesteps 9000-45000, the box, 4 fields
    add --vts to write .vts files directly (where the whole files were copied); otherwise
    write compressed arrays plus each distinct set of grid positions once, for the copy
    back (assemble.py rebuilds the .vts series).
"""
import hashlib
import json
import os
import re
import sys
import glob
import numpy as np
import vtk
from vtk.util.numpy_support import vtk_to_numpy, numpy_to_vtk

BOX = ((-100.0, 200.0), (-120.0, 120.0), (0.0, 350.0))   # world x, y, z (m)
FIELDS = {"overview": ("rhof_1", "theta", "u", "v", "w"), "box": ("theta", "u", "v", "w")}


def read(path, names):
    r = vtk.vtkXMLStructuredGridReader()
    r.SetFileName(path)
    r.UpdateInformation()
    for i in range(r.GetNumberOfPointArrays()):
        n = r.GetPointArrayName(i)
        r.SetPointArrayStatus(n, int(n in names))
    r.Update()
    g = r.GetOutput()
    nx, ny, nz = [g.GetExtent()[2 * a + 1] - g.GetExtent()[2 * a] + 1 for a in range(3)]
    P = vtk_to_numpy(g.GetPoints().GetData()).reshape(nz, ny, nx, 3)
    A = {n: vtk_to_numpy(g.GetPointData().GetArray(n)).reshape(nz, ny, nx) for n in names}
    return P, A


def box_slices(P):
    """The smallest block of grid indices holding every point inside BOX."""
    inside = np.ones(P.shape[:3], dtype=bool)
    for axis, (lo, hi) in enumerate(BOX):
        inside &= (P[..., axis] >= lo) & (P[..., axis] <= hi)
    out = []
    for axis in (0, 1, 2):                                # z, y, x in array order
        hit = np.flatnonzero(inside.any(axis=tuple(a for a in range(3) if a != axis)))
        out.append(slice(hit[0], hit[-1] + 1))
    return tuple(out)


def write_vts(path, P, A, label):
    g = vtk.vtkStructuredGrid()
    nz, ny, nx = P.shape[:3]
    g.SetDimensions(nx, ny, nz)
    pts = vtk.vtkPoints()
    pts.SetData(numpy_to_vtk(np.ascontiguousarray(P.reshape(-1, 3)), deep=True))
    g.SetPoints(pts)
    for n, a in A.items():
        arr = numpy_to_vtk(np.ascontiguousarray(a).ravel(), deep=True)
        arr.SetName(n)
        g.GetPointData().AddArray(arr)
    t = vtk.vtkDoubleArray()
    t.SetName("TimeValue")
    t.InsertNextValue(label)
    g.GetFieldData().AddArray(t)
    w = vtk.vtkXMLStructuredGridWriter()
    w.SetFileName(path)
    w.SetInputData(g)
    w.Write()


def write_pvd(out, labels):
    with open(os.path.join(out, os.path.basename(out) + ".pvd"), "w") as f:
        f.write('<?xml version="1.0"?>\n<VTKFile type="Collection" version="0.1">\n<Collection>\n')
        for n in labels:
            f.write(f'<DataSet timestep="{n}" file="timestep#{n}.vts"/>\n')
        f.write("</Collection>\n</VTKFile>\n")


def main(src, out, kind, first=None, last=None, vts=False):
    os.makedirs(out, exist_ok=True)
    names, positions, labels = FIELDS[kind], {}, []
    for path in sorted(glob.glob(os.path.join(src, "fire#*.vts")),
                       key=lambda p: int(re.search(r"#(\d+)", p).group(1))):
        label = int(re.search(r"#(\d+)", path).group(1))
        if first is not None and not first <= label <= last:
            continue
        P, A = read(path, names)
        cut = (slice(None, None, 8),) * 3 if kind == "overview" else box_slices(P)
        P, A = P[cut], {n: a[cut] for n, a in A.items()}
        labels.append(label)
        if vts:
            write_vts(os.path.join(out, f"timestep#{label}.vts"), P, A, label)
            continue
        key = hashlib.sha256(np.ascontiguousarray(P).tobytes()).hexdigest()[:16]
        if key not in positions.values():
            np.savez_compressed(os.path.join(out, f"positions_{key}.npz"), P=P)
        positions[label] = key
        np.savez_compressed(os.path.join(out, f"fields#{label}.npz"), **A)
    if vts:
        write_pvd(out, labels)
    else:
        json.dump(positions, open(os.path.join(out, "manifest.json"), "w"))


if __name__ == "__main__":
    a = [v for v in sys.argv[1:] if v != "--vts"]
    main(a[0], a[1], a[2], *(int(v) for v in a[3:5]), vts="--vts" in sys.argv)
