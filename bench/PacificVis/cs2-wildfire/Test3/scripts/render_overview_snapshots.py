#!/usr/bin/env pvbatch
"""Figure (a): the fire crossing the ridge, from the stride-8 overview -- a few
timesteps with one camera, the crest marked.

    pvbatch render_overview_snapshots.py OVERVIEW_DIR OUT_PREFIX [--labels 9000 18000 30000 45000]

One PNG per timestep, OUT_PREFIX_<label>.png:
* terrain -- the lowest level, coloured by fuel left (rhof_1, fixed 0-0.6): the scar
* fire    -- theta = 400 K (the front's own threshold), orange
* crest   -- a white line along the ridge top (x = -2 m on the overview grid)
"""
import argparse
import os
from paraview.simple import *

p = argparse.ArgumentParser()
p.add_argument("overview"); p.add_argument("out")
p.add_argument("--labels", type=int, nargs="+", default=[9000, 18000, 30000, 45000])
a = p.parse_args()

for lab in a.labels:
    ResetSession()
    src = XMLStructuredGridReader(FileName=[os.path.join(a.overview, f"timestep#{lab}.vts")])
    src.UpdatePipeline()
    e = src.GetDataInformation().GetExtent()
    ground = ExtractSubset(Input=src, VOI=[e[0], e[1], e[2], e[3], 0, 0])
    crest_i = int(round((-2.0 - (-498.0)) / 16.0))       # overview x = -498 + 16 i
    crest = ExtractSubset(Input=src, VOI=[crest_i, crest_i, e[2], e[3], 0, 0])
    fire = Contour(Input=src, ContourBy=["POINTS", "theta"], Isosurfaces=[400.0])

    rv = CreateView("RenderView"); rv.ViewSize = [1400, 900]
    rv.OrientationAxesVisibility = 0
    rv.Background = [0, 0, 0]; rv.UseColorPaletteForBackground = 0
    gd = Show(ground, rv); ColorBy(gd, ("POINTS", "rhof_1"))
    fuel = GetColorTransferFunction("rhof_1")
    fuel.ApplyPreset("Linear Green (Gr4L)", True)
    fuel.AutomaticRescaleRangeMode = "Never"; fuel.RescaleTransferFunction(0.0, 0.6)
    gd.SetScalarBarVisibility(rv, lab == a.labels[-1])
    if lab == a.labels[-1]:
        sb = GetScalarBar(fuel, rv); sb.Title = "fuel left (rhof_1)"; sb.ComponentTitle = ""
        sb.TitleColor = sb.LabelColor = [1, 1, 1]
    cd = Show(crest, rv); cd.ColorArrayName = ["POINTS", ""]
    cd.DiffuseColor = [1, 1, 1]; cd.AmbientColor = [1, 1, 1]; cd.LineWidth = 3
    fd = Show(fire, rv); fd.ColorArrayName = ["POINTS", ""]; fd.DiffuseColor = [1.0, 0.45, 0.1]
    t = Text(Text=f"#{lab}"); td = Show(t, rv); td.WindowLocation = "Upper Left Corner"
    td.FontSize = 30; td.Color = [1, 1, 1]
    Render(rv)
    cam = rv.GetActiveCamera()
    cam.SetFocalPoint(-40, 0, 120); cam.SetPosition(-900, -900, 750); cam.SetViewUp(0, 0, 1)
    rv.CameraParallelProjection = 0
    Render(rv)
    SaveScreenshot(f"{a.out}_{lab}.png", rv, ImageResolution=[1400, 900])
    print("wrote", f"{a.out}_{lab}.png")
