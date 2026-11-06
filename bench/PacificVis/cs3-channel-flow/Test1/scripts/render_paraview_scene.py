"""Sieve's outputs in one ParaView scene, as each file places itself (source cells).

    pvpython render_paraview_scene.py <outputs dir> <figures dir>

Writes, into <figures dir>:
  pv_channel.png   (a) the lower half of the channel: the 14,004 blocks of step 5 next to the lower
                   wall (red cubes) and, over the back half, step 3b's block means of u at
                   y+ ~ 100 (grey); the ground-truth area (step 2) and the close-up are outlined.
  pv_closeup.png   (b) a 128 x 128-cell part of the ground-truth area: step 2's wall plane z = 1
                   (diverging map, blue < 0), its surfaces u = 0 at full resolution (blue), and the
                   outline of every block of step 4 (block rows 0 and 1) whose minimum is < 0
                   (black).
Nothing is moved or resampled: each file is read with ParaView's own XML reader, and the
files' origins and spacings put them in place. The glyph boxes are built here from the files'
points (the threshold output keeps a value only where u < 0).
"""
import os
import sys

import numpy as np
from paraview import servermanager
from paraview.simple import (Contour, CreateView, ExtractSubset, GetColorTransferFunction,
                             GetOpacityTransferFunction, GetScalarBar, Outline, PVTrivialProducer,
                             Render, SaveScreenshot, Show, XMLImageDataReader)
from vtkmodules.numpy_interface import dataset_adapter as dsa
from vtkmodules.vtkCommonCore import vtkPoints
from vtkmodules.vtkCommonDataModel import vtkPolyData
from vtkmodules.vtkFiltersCore import vtkAppendPolyData, vtkGlyph3D
from vtkmodules.vtkFiltersSources import vtkCubeSource, vtkOutlineSource

OUT, FIG = sys.argv[1], sys.argv[2]
os.makedirs(FIG, exist_ok=True)
B = 16
AREA = (4608, 5632, 3328, 4352, 1, 65)            # step 2's box, source cells (end exclusive)
WIN = (5120, 5248, 4224, 4352)                     # the close-up: 32 of the area's 208 regions
RED, BLUE, GREY = (0.84, 0.15, 0.16), (0.13, 0.40, 0.67), (0.25, 0.25, 0.25)


def fetch(proxy):
    return servermanager.Fetch(proxy)


def producer(poly):
    p = PVTrivialProducer()
    p.GetClientSideObject().SetOutput(poly)
    return p


def block_points(img, k):
    """Block centres of block row k whose value is set (u < 0), from a threshold output."""
    d = dsa.WrapDataObject(img)
    u = np.asarray(d.PointData["u"])
    nx, ny, nz = img.GetDimensions()
    u = u.reshape(nz, ny, nx)[k]                   # VTK point order: x fastest
    j, i = np.nonzero(np.isfinite(u))
    o, s = img.GetOrigin(), img.GetSpacing()
    return np.c_[o[0] + s[0] * i, o[1] + s[1] * j, np.full(i.size, o[2] + s[2] * k)]


def polydata_points(xyz):
    pts = vtkPoints()
    for p in xyz:
        pts.InsertNextPoint(*p)
    pd = vtkPolyData()
    pd.SetPoints(pts)
    return pd


def cubes(xyz, size):
    cube = vtkCubeSource()
    cube.SetXLength(size), cube.SetYLength(size), cube.SetZLength(size)
    g = vtkGlyph3D()
    g.SetSourceConnection(cube.GetOutputPort())
    g.SetInputData(polydata_points(xyz))
    g.ScalingOff()
    g.Update()
    return g.GetOutput()


def boxes(bounds_list):
    app = vtkAppendPolyData()
    for b in bounds_list:
        o = vtkOutlineSource()
        o.SetBounds(*b)
        o.Update()
        app.AddInputData(o.GetOutput())
    app.Update()
    return app.GetOutput()


def view(size):
    v = CreateView("RenderView")
    v.ViewSize = size
    v.OrientationAxesVisibility = 0
    v.Background = [1, 1, 1]
    try:
        v.UseColorPaletteForBackground = 0
    except AttributeError:
        pass
    return v


def solid(src, v, color, **kw):
    r = Show(src, v)
    r.ColorArrayName = [None, ""]
    r.AmbientColor = r.DiffuseColor = list(color)
    for k, val in kw.items():
        setattr(r, k, val)
    return r


def scalar_bar(lut, v, title, pos, ticks, scale=1.0):
    bar = GetScalarBar(lut, v)
    bar.Title, bar.ComponentTitle = title, ""
    bar.TitleColor = bar.LabelColor = [0, 0, 0]
    bar.TitleFontSize, bar.LabelFontSize = round(46 * scale), round(42 * scale)
    bar.ScalarBarThickness = round(30 * scale)
    bar.WindowLocation = "Any Location"
    bar.Position = pos
    bar.ScalarBarLength = 0.33 * scale ** 0.5
    bar.UseCustomLabels, bar.CustomLabels, bar.AddRangeLabels = 1, ticks, 0
    bar.LabelFormat = "{:g}"
    bar.Visibility = 1


# ---------------- (a) the lower half of the channel ----------------
s5 = XMLImageDataReader(FileName=[os.path.join(OUT, "s5_backflow.vti")])
lower = cubes(block_points(fetch(s5), 0), B)
plane = XMLImageDataReader(FileName=[os.path.join(OUT, "s3b_lower_yp100.vti")])
back = ExtractSubset(Input=plane, VOI=[0, 639, 300, 479, 0, 0])   # y >= 4807.5: the back part

va = view([1800, 1000])
solid(producer(boxes([(0, 10239, 0, 7679, 0, 1535)])), va, GREY, LineWidth=2.0)
solid(producer(lower), va, RED)
rp = Show(back, va)
rp.Representation = "Surface"
rp.ColorArrayName = ["POINTS", "u"]
lut = GetColorTransferFunction("u_plane")
rp.LookupTable = lut
lut.ApplyPreset("Grayscale", True)
lut.RescaleTransferFunction(0.55, 0.85)
lut.AutomaticRescaleRangeMode = "Never"
GetOpacityTransferFunction("u_plane")
scalar_bar(lut, va, "u/Ub, y+ 100", [0.86, 0.45], [0.55, 0.65, 0.75, 0.85])
x0, x1, y0, y1, z0, z1 = AREA
solid(producer(boxes([(x0 - .5, x1 - .5, y0 - .5, y1 - .5, z0 - .5, z1 - .5)])), va, BLUE,
      LineWidth=4.0)
solid(producer(boxes([(WIN[0] - .5, WIN[1] - .5, WIN[2] - .5, WIN[3] - .5, 0.5, 32.5)])), va,
      (0, 0, 0), LineWidth=3.0)
cam = va.GetActiveCamera()
cam.SetFocalPoint(4950, 3700, 500)
cam.SetPosition(-300, -6300, 6100)
cam.SetViewUp(0.17, 0.5, 0.85)
cam.SetViewAngle(30)
cam.Dolly(1.45)
va.CameraParallelProjection = 0
Render(va)
SaveScreenshot(os.path.join(FIG, "pv_channel.png"), va, ImageResolution=[1800, 1000],
               TransparentBackground=0)

# ---------------- (b) the close-up: overview blocks over the full-resolution cut ----------------
s2 = XMLImageDataReader(FileName=[os.path.join(OUT, "s2_truth.vti")])
ox, oy = WIN[0] - AREA[0], WIN[2] - AREA[2]
voi = ExtractSubset(Input=s2, VOI=[ox, ox + 127, oy, oy + 127, 0, 31])
floor = ExtractSubset(Input=s2, VOI=[ox, ox + 127, oy, oy + 127, 0, 0])
iso = Contour(Input=voi, ContourBy=["POINTS", "u"], Isosurfaces=[0.0])
s5img = fetch(s5)
flagged = [p for k in (0, 1) for p in block_points(s5img, k)
           if WIN[0] <= p[0] < WIN[1] and WIN[2] <= p[1] < WIN[3]]
blk = boxes([(p[0] - 8, p[0] + 8, p[1] - 8, p[1] + 8, p[2] - 8, p[2] + 8) for p in flagged])

vb = view([1600, 1000])
rf = Show(floor, vb)
rf.Representation = "Surface"
rf.ColorArrayName = ["POINTS", "u"]
lut2 = GetColorTransferFunction("u_wall")
rf.LookupTable = lut2
lut2.ApplyPreset("Cool to Warm", True)
lut2.RescaleTransferFunction(-0.006, 0.006)
lut2.AutomaticRescaleRangeMode = "Never"
scalar_bar(lut2, vb, "u/Ub, y+ 0.07", [0.02, 0.30], [-0.006, -0.003, 0, 0.003, 0.006],
           scale=1.4)   # (b) prints smaller
solid(iso, vb, BLUE)
solid(producer(blk), vb, (0, 0, 0), LineWidth=2.5)
solid(producer(boxes([(WIN[0] - .5, WIN[1] - .5, WIN[2] - .5, WIN[3] - .5, 0.5, 32.5)])), vb,
      GREY, LineWidth=1.0)
cam = vb.GetActiveCamera()
cam.SetFocalPoint(5172, 4290, 8)
cam.SetPosition(5172 - 55, 4290 - 205, 165)
cam.SetViewUp(0.1, 0.55, 0.83)
cam.SetViewAngle(30)
Render(vb)
SaveScreenshot(os.path.join(FIG, "pv_closeup.png"), vb, ImageResolution=[1600, 1000],
               TransparentBackground=0)
print(f"(a) {lower.GetNumberOfCells() // 6} cubes; (b) {len(flagged)} flagged blocks in the close-up")
