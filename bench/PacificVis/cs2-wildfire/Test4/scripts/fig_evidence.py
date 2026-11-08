#!/usr/bin/env python3
"""Paper figure: the answer, from the full-resolution data in the window.

    python fig_evidence.py EVAL_DIR HEIGHTS.csv OUT_PREFIX

HEIGHTS.csv (level, height_m) gives each grid level's height above the lowest level,
over the box (scripts/box_heights.py). All wind values are full resolution, over the
burning strip's rows (|y| <= 50 m), weighted by grid points.
(a) front speed between consecutive saved timesteps (1000 steps), unsmoothed.
(b) the wind along the spread ahead of the front: the lowest grid level within 10 m of
    the front, and 1.3-5.2 m above the ground (levels 1-4) within 60 m of it.
Shaded: the slowdown. Band: the 1000 steps in which the full-resolution front crosses
the crest (between two saved timesteps; no step inside is claimed). Writes OUT_PREFIX.{png,pdf}.
"""
import csv
import os
import sys
from collections import defaultdict

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ev, hpath, out = sys.argv[1:4]
WINDOW, SLOW, CREST, HALF = (9000, 45000), (17000, 22000), -2.0, 2000
INK, MUTED, GRID, BAND = "#1f1f1e", "#6b6a64", "#e6e5e0", "#efeee9"
C0, C1, C2 = "#1f1f1e", "#eb6834", "#2a78d6"
CRESTBAND = "#d9d7cf"
def ylab(ax, main, sub=None, pad=9):
    """Axis label with its qualifier in smaller type, on its own line nearer the axis."""
    ax.set_ylabel(main, labelpad=pad if sub else 4)
    if sub:
        ax.annotate(sub, xy=(1, 0.5), xycoords=ax.yaxis.label, xytext=(1.5, 0),
                    textcoords="offset points", rotation=90, ha="left", va="center",
                    fontsize=5.6, color=INK)


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
fig, (a1, a3) = plt.subplots(2, 1, figsize=(3.45, 3.1), sharex=True,
                             gridspec_kw={"height_ratios": [0.85, 1.15], "hspace": 0.12})
t = np.array(labs) / 1000
L = np.array(sorted(fr))
X = np.array([fr[n] for n in L])
ca, cb_ = next((a, b) for a, b in zip(L, L[1:]) if fr[a] < CREST <= fr[b])
for ax in (a1, a3):
    ax.axvspan(SLOW[0] / 1000, SLOW[1] / 1000, color=BAND, lw=0, zorder=0)
    ax.axvspan(ca / 1000, cb_ / 1000, color=CRESTBAND, lw=0, zorder=0.5)
    ax.grid(axis="y", color=GRID, lw=0.5)
    ax.spines[["top", "right"]].set_visible(False)

# Speed between consecutive saved timesteps, drawn across the 1000 steps it measures
# (no smoothing: a fitted slope would start the drop before the crest).
iv = [(a, b) for a, b in zip(labs, labs[1:])]
sp = [(fr[b] - fr[a]) / (b - a) * 1000 for a, b in iv]
a1.stairs(sp, [iv[0][0] / 1000] + [b / 1000 for _, b in iv], color=INK, lw=1.2, baseline=None)
top = a1.get_xaxis_transform()
a1.text((ca + cb_) / 2000, 1.01, "crest", transform=top, color=MUTED, fontsize=6.5,
        ha="center", va="bottom")
a1.text((SLOW[0] + SLOW[1]) / 2000 + 1.2, 1.01, "slowdown", transform=top, color=MUTED,
        fontsize=6.5, ha="left", va="bottom")
ylab(a1, "front speed", "(m per 1000 steps)")
a1.set_ylim(bottom=0)

series = [("lowest grid level, 0-10 m ahead", ("0-10",), (0,), C0, "--"),
          (f"{H[1]:.1f}-{H[4]:.1f} m above the ground, 0-60 m ahead", near, (1, 2, 3, 4), C1, "-")]
for name, bins, lv, c, ls in series:
    a3.plot(t, [u(n, bins, lv) for n in labs], color=c, lw=1.1, ls=ls, label=name)
a3.axhline(0, color=MUTED, lw=0.6)
ylab(a3, "wind ahead of front", "(m/s; below 0: against the spread)")
a3.set_ylim(top=12.5)
a3.set_xlabel("timestep (thousands)")
a3.legend(loc="upper right", frameon=False, fontsize=6.2, handlelength=1.8, borderaxespad=0.2)
a3.set_xlim(t[0] - 0.5, t[-1] + 0.5)
for _ax, _l in zip((a1, a3), "ab"):
    _ax.text(-0.36, 1.02, f"({_l})", transform=_ax.transAxes, fontsize=8, fontweight="bold", va="top", ha="left", color=INK)
fig.savefig(out + ".png", dpi=300, bbox_inches="tight")
fig.savefig(out + ".pdf", bbox_inches="tight")
print(out)
