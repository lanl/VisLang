#!/usr/bin/env pvbatch
"""Streamlines through the lee slope at full resolution, the Detail box in
context on the overview's terrain.

    pvbatch render_leeslope.py DETAIL.vts OVERVIEW.vts OUT.png --front X
        [--view side|oblique] [--title TEXT]

* terrain  -- the overview's lowest level (stride 8, whole domain), coloured by
              fuel left (rhof_1): the burn scar.
* box      -- the Detail's own outline (its terrain-following cut).
* fire     -- theta = 400 K in the Detail, orange.
* wind     -- streamlines (Detail, full resolution) seeded on three vertical
              sheets ahead of the front (y = -40, 0, 40 m; only y = 0 in the side view;
              x = front-10 ... front+100 m;
              1-50 m above ground), traced both ways, coloured by u: blue = blowing
              back toward the fire (-x), red = with the spread (+x).
"""
import argparse
from paraview.simple import *

p = argparse.ArgumentParser()
p.add_argument("detail"); p.add_argument("overview"); p.add_argument("out")
p.add_argument("--front", type=float, required=True)
p.add_argument("--view", default="side")
p.add_argument("--title", default="")
a = p.parse_args()

det = XMLStructuredGridReader(FileName=[a.detail]); det.UpdatePipeline()
ext = det.GetDataInformation().GetExtent()
b = det.GetDataInformation().GetBounds()
nx = ext[1] + 1
dx = (b[1] - b[0]) / (nx - 1)
i0 = max(0, int(round((a.front - 10 - b[0]) / dx)))
i1 = min(ext[1], int(round((a.front + 100 - b[0]) / dx)))
j_of = lambda yv: int(round((yv - b[2]) / ((b[3] - b[2]) / ext[3])))

vel = Calculator(Input=det, ResultArrayName="wind", Function="u*iHat+v*jHat+w*kHat")
seeds = [ExtractSubset(Input=det, VOI=[i0, i1, j_of(yv), j_of(yv), 1, 24],
                       SampleRateI=5, SampleRateK=3)
         for yv in ((0.0,) if a.view == "side" else (-40.0, 0.0, 40.0))]
seed = AppendDatasets(Input=seeds)
st = StreamTracerWithCustomSource(Input=vel, SeedSource=seed, Vectors=["POINTS", "wind"])
st.IntegrationDirection = "BOTH"; st.MaximumStreamlineLength = 600.0
st.IntegrationStepUnit = "Cell Length"; st.InitialStepLength = 0.3
tube = Tube(Input=st, Radius=0.6, NumberofSides=8)

ov = XMLStructuredGridReader(FileName=[a.overview]); ov.UpdatePipeline()
oe = ov.GetDataInformation().GetExtent()
ground = ExtractSubset(Input=ov, VOI=[oe[0], oe[1], oe[2], oe[3], 0, 0])
fire = Contour(Input=det, ContourBy=["POINTS", "theta"], Isosurfaces=[400.0])
box = Outline(Input=det)

rv = CreateView("RenderView"); rv.ViewSize = [1800, 1000]
rv.OrientationAxesVisibility = 0
rv.Background = [0, 0, 0]; rv.UseColorPaletteForBackground = 0

gd = Show(ground, rv); ColorBy(gd, ("POINTS", "rhof_1"))
fuel = GetColorTransferFunction("rhof_1")
fuel.ApplyPreset("Linear Green (Gr4L)", True)
fuel.AutomaticRescaleRangeMode = "Never"; fuel.RescaleTransferFunction(0.0, 0.6)
gd.SetScalarBarVisibility(rv, False)

ud = Show(tube, rv); ColorBy(ud, ("POINTS", "u"))
ucol = GetColorTransferFunction("u")
ucol.ApplyPreset("Cool to Warm", True)
ucol.AutomaticRescaleRangeMode = "Never"; ucol.RescaleTransferFunction(-3.0, 3.0)
ud.Specular = 0.3
ub = GetScalarBar(ucol, rv); ub.Title = "u (m/s): blue = back toward the fire"
ub.ComponentTitle = ""; ub.WindowLocation = "Any Location"; ub.Position = [0.02, 0.52]
ub.ScalarBarLength = 0.33; ub.TitleColor = ub.LabelColor = [1, 1, 1]; ub.Visibility = 1

fd = Show(fire, rv); fd.ColorArrayName = ["POINTS", ""]; fd.DiffuseColor = [1.0, 0.45, 0.1]; fd.Opacity = 0.85
bd = Show(box, rv); bd.ColorArrayName = ["POINTS", ""]; bd.DiffuseColor = [0.85, 0.85, 0.85]; bd.LineWidth = 1.5

if a.title:
    t = Text(Text=a.title); td = Show(t, rv); td.WindowLocation = "Upper Left Corner"
    td.FontSize = 24; td.Color = [1, 1, 1]

Render(rv)                 # the first render resets the camera; set it after
cam = rv.GetActiveCamera()
cx, cz = (b[0] + b[1]) / 2, b[4] + 110
if a.view == "side":       # looking along +y: the x-z plane, where a recirculation shows
    rv.CameraParallelProjection = 1
    cam.SetFocalPoint(cx, 0, cz); cam.SetPosition(cx, -900, cz); cam.SetViewUp(0, 0, 1)
    cam.SetParallelScale(105)
else:                      # oblique, from upwind-south: the box on the ridge
    rv.CameraParallelProjection = 0
    zc = (b[4] + b[5]) / 2               # the box's middle: frame the whole box
    cam.SetFocalPoint(cx - 30, 10, zc - 30); cam.SetPosition(cx - 620, -760, zc + 380)
    cam.SetViewUp(0, 0, 1)
Render(rv)
SaveScreenshot(a.out, rv, ImageResolution=[1800, 1000])
print("wrote", a.out)
