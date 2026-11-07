#!/usr/bin/env python3
"""Rebuild the .vts series (and its .pvd index) from what cut_series.py wrote on the
server and was copied back: compressed arrays per timestep, grid positions once.

    python assemble.py COPIED_DIR OUT
"""
import json
import os
import sys
import numpy as np
from cut_series import write_vts, write_pvd

src, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
manifest = json.load(open(os.path.join(src, "manifest.json")))
positions = {}
for label, key in sorted(manifest.items(), key=lambda kv: int(kv[0])):
    if key not in positions:
        positions[key] = np.load(os.path.join(src, f"positions_{key}.npz"))["P"]
    A = dict(np.load(os.path.join(src, f"fields#{label}.npz")))
    write_vts(os.path.join(out, f"timestep#{label}.vts"), positions[key], A, int(label))
write_pvd(out, sorted(int(n) for n in manifest))
