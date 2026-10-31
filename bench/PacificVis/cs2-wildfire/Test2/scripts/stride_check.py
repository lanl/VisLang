#!/usr/bin/env python3
"""Is the fire front still found at the overview's stride?  Compares the
overview's fire extent (theta > 400 K) with fire_roi_bounds.csv, computed at
full resolution (its bounds are padded 30 m, so the fire itself is xmax - 30 …).

    python stride_check.py front_s8.csv fire_roi_bounds.csv [--stride-m 16]
"""
import argparse, csv
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("front"); ap.add_argument("full"); ap.add_argument("--stride-m", type=float, default=16.0)
a = ap.parse_args()
s8 = {int(r["label"]): r for r in csv.DictReader(open(a.front))}
full = {int(r["step"]): r for r in csv.DictReader(l for l in open(a.full) if not l.startswith("#"))}
rows, missed = [], []
for lab in sorted(s8):
    r = s8[lab]
    if lab not in full:
        if r["fire_xmax"]:
            missed.append((lab, "fire at stride 8, no full-res row"))
        continue
    if not r["fire_xmax"]:
        missed.append((lab, "full res has fire, stride 8 found none")); continue
    f = full[lab]
    rows.append((lab, float(f["xmax"]) - 30, float(r["fire_xmax"]), float(r["fire_xmax_interp"]),
                 float(f["ymin"]) + 30, float(r["fire_ymin"]), float(f["ymax"]) - 30, float(r["fire_ymax"])))
R = np.array(rows)
print(f"timesteps with fire at full resolution: {len(full)}; found at stride 8: {len(rows)}; "
      f"disagreements: {missed or 'none'}")
for name, i, j in (("downwind edge x, grid points", 1, 2), ("downwind edge x, interpolated", 1, 3),
                   ("y min", 4, 5), ("y max", 6, 7)):
    d = R[:, i] - R[:, j]
    print(f"{name:32s} full - stride8: mean {d.mean():6.1f} m, median {np.median(d):6.1f}, "
          f"max |d| {np.abs(d).max():5.1f}, |d| <= {a.stride_m:g} m in {np.mean(np.abs(d) <= a.stride_m):.0%}")
x_full, x_s8 = R[:, 1], R[:, 3]
print(f"downwind edge travel over the series: full {x_full.max() - x_full.min():.0f} m; "
      f"correlation full vs stride 8 (interp): {np.corrcoef(x_full, x_s8)[0, 1]:.3f}")
crest = -2.0
def crossing(lab, x):
    for k in range(1, len(x)):
        if x[k - 1] < crest <= x[k]:
            return lab[k - 1] + (crest - x[k - 1]) / (x[k] - x[k - 1]) * (lab[k] - lab[k - 1])
print(f"crest (x = {crest:g}) crossing: full res #{crossing(R[:, 0], x_full):.0f}, "
      f"stride 8 #{crossing(R[:, 0], x_s8):.0f}")
print("\nper timestep (full-res fire xmax, stride-8 interp, difference):")
for lab, xf, _, xs, *_ in rows:
    print(f"  #{int(lab):>5}  {xf:7.1f}  {xs:7.1f}  {xf - xs:6.1f}")
