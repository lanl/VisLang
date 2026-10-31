#!/usr/bin/env pvbatch
"""Render one wildfire timestep cut by Sieve: the burned ground, the fire, the wind.

    pvbatch bench/PacificVis/cs2-wildfire/Test2/scripts/render_wind.py IN.vts OUT.png [--crop=xmin,xmax,ymin,ymax] [--fire=xmin,xmax,ymin,ymax]
        [--view oblique|top|side] [--rake-dk N] [--vortex-seeds N]

* ground  -- the lowest grid layer, coloured by rhof_1 (fuel left): dark = burned.
* fire    -- theta = 500 K (flame) and a translucent theta = 340 K shell (plume).
* wind    -- streamline tubes coloured by theta, so air heated by the fire, and
             air at different heights, read as different temperatures. Seeds are
             a rake on the upwind (-x) face at several heights, plus points
             where the streamwise vorticity |wx| is strongest near the ground
             (the flank vortices), traced both ways.

--crop narrows the input to a smaller box first (an index cut, exactly what a
smaller Sieve region would return) so paddings can be compared from one fetch.
"""
import argparse
from paraview.simple import *

p = argparse.ArgumentParser()
p.add_argument("inp"); p.add_argument("out")
box = lambda v: [float(t) for t in v.split(",")]   # pvbatch mangles a bare negative number
p.add_argument("--crop", type=box, default=None)
p.add_argument("--view", default="oblique")
p.add_argument("--rake-dj", type=int, default=36)
p.add_argument("--rake-dk", type=int, default=5)
p.add_argument("--rake-kmax", type=int, default=30)
p.add_argument("--vortex-seeds", type=int, default=300)
p.add_argument("--wx", type=float, default=3.0)
p.add_argument("--flank-di", type=int, default=12)
p.add_argument("--zoom", type=float, default=1.0)
p.add_argument("--title", default="")
p.add_argument("--fire", type=box, default=None,
               help="fire box xmin xmax ymin ymax; vortex seeds are drawn only inside it")
a = p.parse_args()

data = XMLStructuredGridReader(FileName=[a.inp])
data.UpdatePipeline()
info = data.GetDataInformation()
ext = info.GetExtent()
x0, x1, y0, y1, _, _ = info.GetBounds()
nx, ny = ext[1] + 1, ext[3] + 1
dx, dy = (x1 - x0) / (nx - 1), (y1 - y0) / (ny - 1)
if a.crop:
    cx0, cx1, cy0, cy1 = a.crop
    i0, i1 = max(0, round((cx0 - x0) / dx)), min(nx - 1, round((cx1 - x0) / dx))
    j0, j1 = max(0, round((cy0 - y0) / dy)), min(ny - 1, round((cy1 - y0) / dy))
    data = ExtractSubset(Input=data, VOI=[i0, i1, j0, j1, ext[4], ext[5]])
    data.UpdatePipeline()
    ext = data.GetDataInformation().GetExtent()
nk = ext[5] + 1

vel = Calculator(Input=data, ResultArrayName="wind", Function="u*iHat+v*jHat+w*kHat")
grad = Gradient(Input=vel, ScalarArray=["POINTS", "wind"], ComputeGradient=0, ComputeVorticity=1)
wx = Calculator(Input=grad, ResultArrayName="wx_abs", Function="abs(Vorticity_X)")

# seeds 1: a rake on the upwind face, every rake-dj rows, every rake-dk levels
rake = ExtractSubset(Input=data, VOI=[ext[0] + 2, ext[0] + 2, ext[2], ext[3], 1, min(a.rake_kmax, nk - 1)],
                     SampleRateJ=a.rake_dj, SampleRateK=a.rake_dk)
# seeds 2: strongest streamwise vorticity in the lowest ~30 m (terrain-following levels 0..15)
def voi(bx0, bx1, by0, by1):
    return [max(ext[0], round((bx0 - x0) / dx)), min(ext[1], round((bx1 - x0) / dx)),
            max(ext[2], round((by0 - y0) / dy)), min(ext[3], round((by1 - y0) / dy))]
fv = voi(*a.fire) if a.fire else [ext[0], ext[1], ext[2], ext[3]]
low = ExtractSubset(Input=wx, VOI=fv + [1, min(15, nk - 1)])
vort = Threshold(Input=low, Scalars=["POINTS", "wx_abs"], LowerThreshold=a.wx, UpperThreshold=1e9)
vseed = MaskPoints(Input=vort, OnRatio=1, MaximumNumberofPoints=a.vortex_seeds,
                   RandomSampling=1, RandomSamplingMode="Randomized Id Strides", GenerateVertices=1)

flank = []
if a.fire:
    i0, i1, j0, j1 = fv
    for j in (j0, j1):
        flank.append(ExtractSubset(Input=data, VOI=[i0, i1, j, j, 1, 13], SampleRateI=a.flank_di, SampleRateK=4))

def trace(seed, both):
    s = StreamTracerWithCustomSource(Input=vel, SeedSource=seed, Vectors=["POINTS", "wind"])
    s.IntegrationDirection = "BOTH" if both else "FORWARD"
    s.MaximumStreamlineLength = 4000.0
    s.IntegrationStepUnit = "Cell Length"; s.InitialStepLength = 0.5
    t = Tube(Input=s, Radius=0.9 if both else 1.3, NumberofSides=8)
    return t

rv = CreateView("RenderView"); rv.ViewSize = [1800, 1100]
rv.OrientationAxesVisibility = 0
rv.Background = [1, 1, 1]; rv.UseColorPaletteForBackground = 0

ground = ExtractSubset(Input=data, VOI=[ext[0], ext[1], ext[2], ext[3], 0, 0])
gd = Show(ground, rv); ColorBy(gd, ("POINTS", "rhof_1"))
fuel = GetColorTransferFunction("rhof_1")
fuel.AutomaticRescaleRangeMode = "Never"
fuel.RGBPoints = [0.0, 0.10, 0.08, 0.07, 0.3, 0.42, 0.36, 0.22, 0.6, 0.55, 0.70, 0.38]
gd.SetScalarBarVisibility(rv, True)
fb = GetScalarBar(fuel, rv); fb.Title = "fuel left (rhof_1)"; fb.ComponentTitle = ""
fb.WindowLocation = "Any Location"; fb.Position = [0.02, 0.08]; fb.ScalarBarLength = 0.3
fb.TitleColor = fb.LabelColor = [0.1, 0.1, 0.1]

theta = GetColorTransferFunction("theta")
theta.ApplyPreset("Cool to Warm", True); theta.AutomaticRescaleRangeMode = "Never"
theta.RescaleTransferFunction(299.0, 340.0)
for seed, both in [(rake, False), (vseed, True)] + [(f, True) for f in flank]:
    sd = Show(trace(seed, both), rv); ColorBy(sd, ("POINTS", "theta")); sd.LookupTable = theta
    theta.RescaleTransferFunction(299.0, 340.0)
    sd.Specular = 0.3
tb = GetScalarBar(theta, rv); tb.Title = "wind temperature, theta (K)"; tb.ComponentTitle = ""
tb.WindowLocation = "Any Location"; tb.Position = [0.02, 0.55]; tb.ScalarBarLength = 0.3
tb.TitleColor = tb.LabelColor = [0.1, 0.1, 0.1]; tb.Visibility = 1

flame = Contour(Input=data, ContourBy=["POINTS", "theta"], Isosurfaces=[500.0])
fd = Show(flame, rv); ColorBy(fd, None); fd.DiffuseColor = [1.0, 0.35, 0.05]; fd.Opacity = 0.9
plume = Contour(Input=data, ContourBy=["POINTS", "theta"], Isosurfaces=[340.0])
pd = Show(plume, rv); ColorBy(pd, None); pd.DiffuseColor = [1.0, 0.75, 0.3]; pd.Opacity = 0.18

if a.title:
    t = Text(Text=a.title); td = Show(t, rv); td.WindowLocation = "Upper Center"; td.FontSize = 26
    td.Color = [0.1, 0.1, 0.1]

rv.ResetCamera()
cam = rv.GetActiveCamera()
b = list(data.GetDataInformation().GetBounds())
if a.fire:   # frame the fire, not the whole box
    b[0:4] = a.fire
cx, cy, cz = (b[0] + b[1]) / 2, (b[2] + b[3]) / 2, b[4] + 80
span = max(b[1] - b[0], b[3] - b[2]) / a.zoom
if a.view == "top":
    cam.SetFocalPoint(cx, cy, cz); cam.SetPosition(cx, cy, cz + 2.2 * span); cam.SetViewUp(0, 1, 0)
elif a.view == "side":
    cam.SetFocalPoint(cx, cy, cz + 60); cam.SetPosition(cx, cy - 2.0 * span, cz + 120); cam.SetViewUp(0, 0, 1)
elif a.view == "back":   # looking upwind, from downwind and above: the plume's vortex pair
    cam.SetFocalPoint(cx, cy, cz); cam.SetPosition(cx + 1.1 * span, cy - 0.45 * span, cz + 1.3 * span); cam.SetViewUp(0, 0, 1)
elif a.view == "front":   # looking downwind, from upwind
    cam.SetFocalPoint(cx, cy, cz); cam.SetPosition(cx - 1.6 * span, cy, cz + 0.55 * span); cam.SetViewUp(0, 0, 1)
else:   # oblique: from the upwind-south side, above
    cam.SetFocalPoint(cx + 0.05 * span, cy, cz); cam.SetPosition(cx - 1.05 * span, cy - 1.15 * span, cz + 0.9 * span)
    cam.SetViewUp(0, 0, 1)
rv.CameraParallelProjection = 0
Render(rv)
SaveScreenshot(a.out, rv, ImageResolution=[1800, 1100])
print("wrote", a.out, "extent", ext)
