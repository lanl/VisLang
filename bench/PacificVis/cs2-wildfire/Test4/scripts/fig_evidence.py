#!/usr/bin/env python3
"""Paper figure: the answer, from the full-resolution data in the window.

    python fig_evidence.py EVAL_DIR HEIGHTS.csv OUT_PREFIX

HEIGHTS.csv (level, height_m) gives each grid level's height above the lowest level,
over the box (scripts/box_heights.py). All wind values are full resolution, over the
burning strip's rows (|y| <= 50 m), weighted by grid points.
(a) front speed (slope of the front over +-2000 steps).
(b) u from the front to 60 m ahead of it, by height and time; blue = against the
    spread; black contour u = 0.
(c) three timelines: the lowest grid level within 10 m of the front; levels 1-4 within
    60 m of it; levels 1-4 from 60 to 120 m ahead.
Shaded: the slowdown. Writes OUT_PREFIX.{png,pdf}.
"""
import csv
import os
import sys
from collections import defaultdict

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm

ev, hpath, out = sys.argv[1:4]
WINDOW, SLOW, CREST, HALF = (9000, 45000), (17000, 22000), -2.0, 2000
INK, MUTED, GRID, BAND = "#1f1f1e", "#6b6a64", "#e6e5e0", "#efeee9"
C0, C1, C2 = "#1f1f1e", "#eb6834", "#2a78d6"
H = {int(r["level"]): float(r["height_m"]) for r in csv.DictReader(open(hpath))}

fr = {int(r["label"]): float(r["front_x"]) for r in csv.DictReader(open(os.path.join(ev, "fronts.csv")))
      if r["stride"] == "1" and r["front_x"]}
prof = defaultdict(lambda: [0.0, 0])
for r in csv.DictReader(open(os.path.join(ev, "profiles.csv"))):
    if r["rows"] == "strip" and r["u_mean"] != "":
        prof[(int(r["label"]), r["bin"], int(r["level"]))] = [float(r["u_mean"]) * int(r["n"]), int(r["n"])]


def u(n, bins, levels):
    s = sum(prof[(n, b, k)][0] for b in bins for k in levels)
    c = sum(prof[(n, b, k)][1] for b in bins for k in levels)
    return s / c if c else np.nan


labs = [n for n in sorted(fr) if WINDOW[0] <= n <= WINDOW[1]]
near = ("0-10", "10-30", "30-60")
plt.rcParams.update({"font.size": 7.5, "axes.edgecolor": MUTED, "axes.labelcolor": INK,
                     "xtick.color": MUTED, "ytick.color": MUTED, "axes.linewidth": 0.7,
                     "pdf.fonttype": 42})
fig, (a1, a2, a3) = plt.subplots(3, 1, figsize=(3.45, 4.4), sharex=True,
                                 gridspec_kw={"height_ratios": [0.8, 1.3, 1.1], "hspace": 0.12})
t = np.array(labs) / 1000
for ax in (a1, a3):
    ax.axvspan(SLOW[0] / 1000, SLOW[1] / 1000, color=BAND, lw=0, zorder=0)
    ax.grid(axis="y", color=GRID, lw=0.5)
    ax.spines[["top", "right"]].set_visible(False)

L = np.array(sorted(fr))
X = np.array([fr[n] for n in L])
sp = [np.polyfit(L[np.abs(L - c) <= HALF] / 1000, X[np.abs(L - c) <= HALF], 1)[0] for c in labs]
a1.plot(t, sp, color=INK, lw=1.2, marker="o", ms=1.8)
cx = next(a + (CREST - fr[a]) / (fr[b] - fr[a]) * (b - a) for a, b in zip(L, L[1:])
          if fr[a] < CREST <= fr[b])
for ax in (a1, a2, a3):
    ax.axvline(cx / 1000, color=MUTED, lw=0.7, ls=":")
a1.text(cx / 1000 + 0.4, max(sp) * 0.95, "crest", color=MUTED, fontsize=6.5, va="top")
a1.text(SLOW[1] / 1000 + 0.4, max(sp) * 0.55, "slowdown", color=MUTED, fontsize=6.5)
a1.set_ylabel("front speed\n(m per 1000 steps)")
a1.set_ylim(bottom=0)

levels = [k for k in sorted(H) if H[k] <= 50]
Z = np.array([[u(n, near, (k,)) for n in labs] for k in levels])
edges_t = np.concatenate([[t[0] - 0.5], (t[1:] + t[:-1]) / 2, [t[-1] + 0.5]])
hz = np.array([H[k] for k in levels])
edges_h = np.concatenate([[0], (hz[1:] + hz[:-1]) / 2, [hz[-1] + (hz[-1] - hz[-2]) / 2]])
norm = TwoSlopeNorm(vmin=-3, vcenter=0, vmax=10)
pm = a2.pcolormesh(edges_t, edges_h, Z, cmap="RdBu_r", norm=norm, shading="flat")
a2.contour(t, hz, Z, levels=[0], colors=INK, linewidths=0.6)
a2.set_ylabel("height (m),\n0-60 m ahead of front")
cb = fig.colorbar(pm, ax=a2, pad=0.02, fraction=0.05)
cb.set_label("u (m/s); blue = against spread", fontsize=6.5)
cb.ax.tick_params(labelsize=6.5)

series = [("lowest grid level, 0-10 m ahead", ("0-10",), (0,), C0, "--"),
          (f"{H[1]:.1f}-{H[4]:.1f} m up, 0-60 m ahead", near, (1, 2, 3, 4), C1, "-"),
          (f"{H[1]:.1f}-{H[4]:.1f} m up, 60-120 m ahead", ("60-120",), (1, 2, 3, 4), C2, "-")]
for name, bins, lv, c, ls in series:
    a3.plot(t, [u(n, bins, lv) for n in labs], color=c, lw=1.1, ls=ls, label=name)
a3.axhline(0, color=MUTED, lw=0.6)
a3.set_ylabel("u ahead of front\n(m/s)")
a3.set_xlabel("timestep (thousands)")
a3.legend(loc="upper right", frameon=False, fontsize=6.2, handlelength=1.8)
a3.set_xlim(t[0] - 0.5, t[-1] + 0.5)
fig.savefig(out + ".png", dpi=300, bbox_inches="tight")
fig.savefig(out + ".pdf", bbox_inches="tight")
print(out)
