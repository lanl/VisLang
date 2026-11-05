"""Open a Sieve .vti in ParaView (pvpython) and report what ParaView sees: type, extent, origin,
spacing, bounds, arrays, the field data Sieve writes (placement marker, provenance).
Usage: pvpython check_vti_pv.py <file.vti>"""
import sys
from paraview.simple import XMLImageDataReader, UpdatePipeline
from paraview import servermanager
r = XMLImageDataReader(FileName=[sys.argv[1]])
UpdatePipeline(proxy=r)
d = servermanager.Fetch(r)
print("type:", d.GetClassName(), "dims:", d.GetDimensions(), "extent:", d.GetExtent())
print("origin:", d.GetOrigin(), "spacing:", d.GetSpacing(), "bounds:", d.GetBounds())
pd = d.GetPointData()
for i in range(pd.GetNumberOfArrays()):
    a = pd.GetArray(i)
    print(f"point array {a.GetName()!r}: {a.GetDataTypeAsString()} x{a.GetNumberOfTuples()}, range {a.GetRange()}")
fd = d.GetFieldData()
for i in range(fd.GetNumberOfArrays()):
    a = fd.GetAbstractArray(i)
    v = a.GetValue(0) if hasattr(a, "GetValue") else None
    text = str(v)
    print(f"field data {a.GetName()!r}: {text[:160]!r}{' …' if len(text) > 160 else ''}")
