#!/usr/bin/env python3
"""Paper figure: front speed over the whole run, from Sieve's coarse pass (every
timestep) against every 5th file (the two-pass download's first pass). One panel.

    python cs2_speed_coverage.py front_s8.csv cost_table.csv -o OUT_PREFIX

Writes OUT_PREFIX.{png,pdf}. Speed is computed as in cs2_window.py: every
timestep, the slope of a line fit over +-4000 steps; every 5th file, the
difference of consecutive samples placed at their midpoint. The bytes in the
legend are read from cost_table.csv (scripts/cost_table.py).
"""
import argparse
import csv

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

CREST, FIT = -2.0, 4000
BLUE, ORANGE = "#2a78d6", "#eb6834"
INK, MUTED, GRID, BAND = "#1f1f1e", "#6b6a64", "#e6e5e0", "#efeee9"

ap = argparse.ArgumentParser()
ap.add_argument("front")
ap.add_argument("cost")
ap.add_argument("--window", type=int, nargs=2, default=(9000, 45000))
ap.add_argument("-o", "--out", required=True)
a = ap.parse_args()

rows = list(csv.DictReader(open(a.front)))
lab_all = np.array([int(r["label"]) for r in rows])
fx_all = np.array([float(r["front_x"]) if r["front_x"] else np.nan for r in rows])
every5 = np.zeros(len(rows), dtype=bool)
every5[::5] = True                           # the two-pass download's 18 files
fire = ~np.isnan(fx_all)
lab, fx, l5, x5 = lab_all[fire], fx_all[fire], lab_all[fire & every5], fx_all[fire & every5]


def fit_speed(c):
    m = (lab >= c - FIT) & (lab <= c + FIT)
    return np.polyfit(lab[m] / 1000.0, fx[m], 1)[0]


speed = np.array([fit_speed(c) for c in lab])
speed5 = np.diff(x5) / (np.diff(l5) / 1000.0)
mid5 = (l5[1:] + l5[:-1]) / 2
k = next(i for i in range(1, len(fx)) if fx[i - 1] < CREST <= fx[i])
cross = lab[k - 1] + (CREST - fx[k - 1]) / (fx[k] - fx[k - 1]) * (lab[k] - lab[k - 1])

cost = list(csv.DictReader(open(a.cost)))
sieve_gb = next(int(r["wire_bytes"]) for r in cost
                if r["route"].startswith("Sieve") and r["step"].startswith("overview")) / 1e9
pass1_gb = next(int(r["wire_bytes"]) for r in cost
                if r["route"] == "Two-pass download" and r["step"].startswith("pass 1")) / 1e9

plt.rcParams.update({"font.size": 8, "axes.edgecolor": MUTED, "axes.labelcolor": INK,
                     "xtick.color": MUTED, "ytick.color": MUTED, "axes.linewidth": 0.8,
                     "pdf.fonttype": 42})
fig, ax = plt.subplots(figsize=(3.5, 1.9))
w0, w1 = a.window
ax.axvspan(w0 / 1000, w1 / 1000, color=BAND, zorder=0, lw=0)
ax.text(w1 / 1000 - 0.6, -5.6, "full-resolution window", ha="right", va="bottom",
        color=MUTED, fontsize=7)
ax.axvline(cross / 1000, ymin=0.16, color=MUTED, lw=0.8, ls=":", zorder=1)
ax.text(cross / 1000 + 0.8, 24.5, "crest\ncrossing", color=MUTED, fontsize=7, va="top")
ax.axhline(0, color=MUTED, lw=0.6)
ax.grid(axis="y", color=GRID, lw=0.5)
ax.spines[["top", "right"]].set_visible(False)
ax.plot(lab / 1000, speed, color=BLUE, lw=1.6, marker="o", ms=2.2, zorder=3,
        label=f"Sieve coarse pass: all {len(lab_all)} timesteps, {sieve_gb:.2f} GB")
ax.plot(mid5 / 1000, speed5, color=ORANGE, lw=0, marker="o", ms=5.5, mfc="white",
        mew=1.6, zorder=4,
        label=f"download every 5th file: {int(every5.sum())} timesteps, {pass1_gb:.1f} GB")
ax.set_xlim(6, 92)
ax.set_ylim(-6, 25)
ax.set_xlabel("timestep (thousands)")
ax.set_ylabel("front speed\n(m per 1000 steps)")
ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), frameon=False, fontsize=7,
          handletextpad=0.4, borderaxespad=0.1)
for ext in ("png", "pdf"):
    fig.savefig(f"{a.out}.{ext}", dpi=300, bbox_inches="tight")
print(f"crossing #{cross:.0f}; Sieve pass 1 {sieve_gb:.3f} GB; download pass 1 {pass1_gb:.2f} GB")
print("wrote", f"{a.out}.png")
