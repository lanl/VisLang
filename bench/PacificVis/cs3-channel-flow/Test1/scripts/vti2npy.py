"""Convert a Sieve .vti output to a plain array for the analysis environment.

    python vti2npy.py <file.vti> [<array name>]          (run with the vislang env: pyvista)

Writes, beside the .vti: <stem>.<array>.npy (float64, indexed [x, y, z], i.e. VTK's x-fastest
order unpacked) and <stem>.meta.json (origin, spacing, dimensions, sha256 of the array bytes).
The analysis scripts (cvision env: scipy, scikit-image) read only these, so neither environment
is changed. Placement is in the source's cells: cell index = origin + spacing * i.
"""
import hashlib
import json
import os
import sys

import numpy as np
import pyvista as pv

path = sys.argv[1]
name = sys.argv[2] if len(sys.argv) > 2 else "u"
img = pv.read(path)
arr = np.asarray(img.point_data[name]).reshape(img.dimensions, order="F")
stem = os.path.splitext(path)[0]
np.save(f"{stem}.{name}.npy", arr)
meta = {"source": os.path.basename(path), "array": name, "dimensions": list(img.dimensions),
        "origin": list(img.origin), "spacing": list(img.spacing),
        "sha256_xyz_C": hashlib.sha256(np.ascontiguousarray(arr).tobytes()).hexdigest()}
with open(f"{stem}.meta.json", "w") as f:
    json.dump(meta, f, indent=1)
print(json.dumps(meta))
