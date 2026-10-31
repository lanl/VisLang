#!/usr/bin/env pvbatch
"""How much padding do the wind boxes need?  Traces one fixed seed set in a
series of boxes cut from the widest fetch and reports, per box, the share of
streamline length the widest box shows that survives, next to the box's points.

Seeds: grid points inside the fire box (theta > 400 K, padded 30 m), in the
lowest 40 m, where the streamwise vorticity is strongest -- the flank vortices
and the inflow into the head -- plus points in the plume (theta > 330 K).

    pvbatch bench/PacificVis/cs2-wildfire/Test1/scripts/padding_sweep.py WIDE.vts --fire=xmin,xmax,ymin,ymax
"""
import argparse, sys
import numpy as np
from paraview.simple import *
from paraview import servermanager as sm
from vtk.util.numpy_support import vtk_to_numpy as n

box = lambda v: [float(t) for t in v.split(",")]
p = argparse.ArgumentParser()
p.add_argument("inp"); p.add_argument("--fire", type=box, required=True)
p.add_argument("--pads", default="0/0/0 50/100/50 50/150/100 100/150/100 60/200/120 100/200/150 250/200/200")
p.add_argument("--nseeds", type=int, default=400)
a = p.parse_args()

src = XMLStructuredGridReader(FileName=[a.inp]); src.UpdatePipeline()
info = src.GetDataInformation(); ext = info.GetExtent(); bx = info.GetBounds()
nx, ny = ext[1] + 1, ext[3] + 1
dx, dy = (bx[1] - bx[0]) / (nx - 1), (bx[3] - bx[2]) / (ny - 1)
ij = lambda x, y: (int(round((x - bx[0]) / dx)), int(round((y - bx[2]) / dy)))

vel = Calculator(Input=src, ResultArrayName="wind", Function="u*iHat+v*jHat+w*kHat")
g = Gradient(Input=vel, ScalarArray=["POINTS", "wind"], ComputeGradient=0, ComputeVorticity=1)
G = sm.Fetch(g); d = [0, 0, 0]; G.GetDimensions(d)
P = n(G.GetPoints().GetData()).reshape(d[2], d[1], d[0], 3)
wx = np.abs(n(G.GetPointData().GetArray("Vorticity"))[:, 0]).reshape(d[2], d[1], d[0])
th = n(G.GetPointData().GetArray("theta")).reshape(d[2], d[1], d[0])
h = P[..., 2] - P[0:1, ..., 2]
fx0, fx1, fy0, fy1 = a.fire
inside = (P[..., 0] >= fx0) & (P[..., 0] <= fx1) & (P[..., 1] >= fy0) & (P[..., 1] <= fy1)
cand_v = inside & (h < 40) & (h > 1) & (wx > np.percentile(wx[inside & (h < 40)], 98))
cand_p = inside & (th > 330)
rng = np.random.default_rng(0)
pick = lambda m, k: rng.choice(np.flatnonzero(m), size=min(k, int(m.sum())), replace=False)
ids = np.concatenate([pick(cand_v, a.nseeds // 2), pick(cand_p, a.nseeds // 2)])
pts = P.reshape(-1, 3)[ids]

import vtk
pd = vtk.vtkPolyData(); vp = vtk.vtkPoints()
for q in pts: vp.InsertNextPoint(*q)
pd.SetPoints(vp)
seed = PVTrivialProducer(); seed.GetClientSideObject().SetOutput(pd)

def length(inp):
    s = StreamTracerWithCustomSource(Input=inp, SeedSource=seed, Vectors=["POINTS", "wind"])
    s.IntegrationDirection = "BOTH"; s.MaximumStreamlineLength = 4000.0
    s.IntegrationStepUnit = "Cell Length"; s.InitialStepLength = 0.5
    r = IntegrateVariables(Input=s); r.UpdatePipeline()
    o = sm.Fetch(r); return float(n(o.GetCellData().GetArray("Length"))[0])

full = None
print(f"seeds {len(pts)}  grid {d}  dx {dx:g} m")
print(f"{'pad up/down/side (m)':>22} {'points (M)':>10} {'vs widest':>9} {'length kept':>11}")
rows = []
for pad in a.pads.split():
    up, dn, sd = map(float, pad.split("/"))
    i0, j0 = ij(max(bx[0], fx0 - up), max(bx[2], fy0 - sd))
    i1, j1 = ij(min(bx[1], fx1 + dn), min(bx[3], fy1 + sd))
    cut = ExtractSubset(Input=vel, VOI=[i0, i1, j0, j1, ext[4], ext[5]])
    L = length(cut); npts = (i1 - i0 + 1) * (j1 - j0 + 1) * d[2]
    rows.append((pad, npts, L))
wide_pts, wide_L = rows[-1][1], rows[-1][2]
for pad, npts, L in rows:
    print(f"{pad:>22} {npts/1e6:10.2f} {npts/wide_pts:9.0%} {L/wide_L:11.0%}")
