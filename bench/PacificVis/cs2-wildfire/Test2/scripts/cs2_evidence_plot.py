#!/usr/bin/env python3
"""The full-resolution evidence: front speed, and the along-wind u just ahead of
the front by height above ground, over the window; plus the same lee-slope
ground inside the burning strip vs its flanks.

    python cs2_evidence_plot.py leeslope_full.csv levels.csv front_s8.csv --out-dir DIR

Writes DIR/evidence.txt and DIR/evidence_plot.{png,pdf}.
"""
import argparse
import csv
import os

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm, LinearSegmentedColormap

INK, MUTED, GRID = "#1f1f1e", "#6b6a64", "#e6e5e0"
BLUE, ORANGE = "#2a78d6", "#eb6834"
NK = 21

ap = argparse.ArgumentParser()
ap.add_argument("lee"); ap.add_argument("levels"); ap.add_argument("front")
ap.add_argument("--out-dir", required=True)
a = ap.parse_args()

L = list(csv.DictReader(open(a.lee)))
H = np.array([float(r["height_above_ground_m"]) for r in csv.DictReader(open(a.levels))])[:NK]
F = {int(r["label"]): r for r in csv.DictReader(open(a.front))}
num = lambda v: float(v) if v != "" else np.nan
lab = np.array([int(float(r["label"])) for r in L])
fx = np.array([num(r["front_x"]) for r in L])
U = np.array([[num(r[f"ahead_u_k{k}"]) for k in range(NK)] for r in L])
fixed = np.array([[num(r[f"fixed_u_k{k}"]) for k in range(NK)] for r in L])
flank = np.array([[num(r[f"flank_u_k{k}"]) for k in range(NK)] for r in L])
depth = np.array([num(r["ahead_zero_m"]) for r in L])

# front speed, every overview timestep (as cs2_window.py: line fit over ±4000 steps)
fl = np.array(sorted(k for k in F if F[k]["front_x"] != ""))
ff = np.array([float(F[k]["front_x"]) for k in fl])
spd = np.array([np.polyfit(fl[(fl >= c - 4000) & (fl <= c + 4000)] / 1000,
                           ff[(fl >= c - 4000) & (fl <= c + 4000)], 1)[0] for c in fl])

# ---- numbers --------------------------------------------------------------------
out = []
p = out.append
p("levels (m above ground): " + ", ".join(f"k{k} {h:.2f}" for k, h in enumerate(H)))
band = slice(1, 5)                           # levels 1-4: 1.2-5.1 m, above the lowest level
p(f"u ahead of the front, mean over levels 1-4 ({H[1]:.1f}-{H[4]:.1f} m) and at level 0:")
for i, l in enumerate(lab):
    if np.isnan(U[i, 0]):
        continue
    d = depth[i]
    p(f"  #{l:>5} front {fx[i]:7.1f} m | 1-5 m {np.nanmean(U[i, band]):+5.2f} | level 0 "
      f"{U[i, 0]:+5.2f} | reversed layer "
      + ("none" if d == 0 else f"{d:.1f} m" if not np.isnan(d) else f"> {H[-1]:.0f} m"))
up = slice(9, 13)                            # levels 9-12: 13.5-20.3 m
first_up = next((l for i, l in enumerate(lab) if l > 18000 and not np.isnan(U[i, 9]) and np.nanmean(U[i, up]) < 0), None)
p(f"first timestep after the crossing with the {H[9]:.1f}-{H[12]:.1f} m mean ahead of the front below 0: #{first_up}")
first_rev = next((l for i, l in enumerate(lab) if not np.isnan(U[i, 1]) and np.nanmean(U[i, band]) < 0), None)
p(f"first timestep with the 1-5 m mean ahead of the front below 0: #{first_rev}")
lee = (lab >= 21000)
p(f"lee ground x 100-160 m, levels 1-4, #21000-#45000: burning strip mean "
  f"{np.nanmean(fixed[lee][:, band]):+.2f} m/s (negative at {int((fixed[lee][:, band].mean(1) < 0).sum())}/{int(lee.sum())}); "
  f"flanks |y| >= 90 m mean {np.nanmean(flank[lee][:, band]):+.2f} m/s "
  f"(negative at {int((flank[lee][:, band].mean(1) < 0).sum())}/{int(lee.sum())})")
pre = (lab >= 9000) & (lab <= 20000)
p(f"same ground, #9000-#20000 (fire upwind of the crest): strip {np.nanmean(fixed[pre][:, band]):+.2f}, "
  f"flanks {np.nanmean(flank[pre][:, band]):+.2f} m/s")
p(f"#4000 (before ignition) same ground: strip {np.nanmean(fixed[lab == 4000][:, band]):+.2f}, "
  f"flanks {np.nanmean(flank[lab == 4000][:, band]):+.2f} m/s")
text = "\n".join(out)
print(text)
os.makedirs(a.out_dir, exist_ok=True)
open(os.path.join(a.out_dir, "evidence.txt"), "w").write(text + "\n")

# ---- figure ----------------------------------------------------------------------
plt.rcParams.update({"font.size": 9, "axes.edgecolor": MUTED, "axes.labelcolor": INK,
                     "xtick.color": MUTED, "ytick.color": MUTED, "axes.linewidth": 0.8})
fig, ax = plt.subplots(3, 1, figsize=(7.0, 7.0), sharex=True,
                       gridspec_kw={"hspace": 0.14, "height_ratios": [1, 1.5, 1]})
k = lambda v: np.asarray(v) / 1000.0
w = (fl >= 9000) & (fl <= 45000)
ax[0].plot(k(fl[w]), spd[w], color=INK, lw=2, marker="o", ms=3)
ax[0].set_ylabel("front speed\n(m per 1000 steps)")
ax[0].axhline(0, color=MUTED, lw=0.8)

div = LinearSegmentedColormap.from_list("div", ["#1c5cab", "#86b6ef", "#f2f1ec", "#f3a07f", "#b33d10"])
m = ~np.isnan(U[:, 0])
X, Y = np.meshgrid(k(lab[m]), H)
cm = ax[1].pcolormesh(X, Y, U[m].T, cmap=div, norm=TwoSlopeNorm(0, -3, 10), shading="nearest")
ax[1].contour(X, Y, U[m].T, levels=[0], colors=INK, linewidths=1.0)
ax[1].set_ylabel("height above ground (m)\nu ahead of the front")
cb = fig.colorbar(cm, ax=list(ax), pad=0.015, fraction=0.03, shrink=0.45, anchor=(0, 0.5))
cb.set_label("u (m/s); blue = reversed")
cb.outline.set_visible(False)

ax[2].plot(k(lab), fixed[:, band].mean(1), color=ORANGE, lw=2, marker="o", ms=3,
           label="burning strip |y| ≤ 50 m")
ax[2].plot(k(lab), flank[:, band].mean(1), color=BLUE, lw=2, marker="o", ms=3,
           label="flanks |y| ≥ 90 m")
ax[2].axhline(0, color=INK, lw=0.8)
ax[2].set_ylabel("u at 1–5 m on the\nlee slope, x 100–160 m")
ax[2].legend(loc="lower left", frameon=False, ncol=2)
ax[2].set_xlabel("timestep (thousands)")
for x_ in ax:
    x_.spines[["top", "right"]].set_visible(False)
    x_.axvline(18.02, color=MUTED, lw=0.8, ls=":")
ax[0].text(18.4, ax[0].get_ylim()[1] * 0.9, "front crosses crest", color=MUTED, va="top")
ax[2].set_xlim(3, 46)
for ext in ("png", "pdf"):
    fig.savefig(os.path.join(a.out_dir, f"evidence_plot.{ext}"), dpi=200, bbox_inches="tight")
print("wrote", os.path.join(a.out_dir, "evidence_plot.png"))
