import sys
from paraview.simple import NetCDFReader, UpdatePipeline
from paraview import servermanager as sm
r = NetCDFReader(FileName=[sys.argv[1]]); print("reader: NetCDFReader (vtkNetCDFCFReader), chosen explicitly")
UpdatePipeline(proxy=r); d = sm.Fetch(r)
print("type:", d.GetClassName(), "extent:", d.GetExtent())
print("bounds:", d.GetBounds())
for arrs, lab in ((d.GetPointData(), "point"), (d.GetCellData(), "cell")):
    for i in range(arrs.GetNumberOfArrays()):
        a = arrs.GetArray(i); print(lab, "array", a.GetName(), a.GetNumberOfTuples(), a.GetRange())
