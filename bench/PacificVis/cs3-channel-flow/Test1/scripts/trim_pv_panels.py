"""Trim the ParaView panels for print: keep at most 12 px of white margin on each side, and
narrow every run of all-white columns inside the image (e.g. between a colour bar and the scene)
to 24 px. No pixel of the scene is changed, only empty columns and rows are removed.

    python trim_pv_panels.py <figures dir>      (cvision env, Pillow)
"""
import sys

import numpy as np
from PIL import Image

MARGIN, GAP = 12, 24

for name in ("pv_channel", "pv_closeup"):
    path = f"{sys.argv[1]}/{name}.png"
    a = np.asarray(Image.open(path).convert("RGB"))
    ink = (a < 245).any(axis=2)
    rows = np.nonzero(ink.any(axis=1))[0]
    a, ink = a[max(rows[0] - MARGIN, 0):rows[-1] + MARGIN + 1], ink.any(axis=0)
    first, last = np.nonzero(ink)[0][[0, -1]]
    keep, run = [], 0
    for j in range(max(first - MARGIN, 0), min(last + MARGIN + 1, a.shape[1])):
        run = 0 if ink[j] else run + 1
        if run <= GAP or j < first or j > last:
            keep.append(j)
    Image.fromarray(np.ascontiguousarray(a[:, keep])).save(path)
    print(f"{name}: {a.shape[1]} -> {len(keep)} columns, {a.shape[0]} rows")
