#!/usr/bin/env python3
"""Choose the time window from the overview: front position and speed, and the
near-ground wind ahead of the front, over every timestep (what Sieve's overview
sees) against every 5th file (what a scientist downloading whole files sees).

    python cs2_window.py front_s8.csv --window 9000 45000 --out-dir DIR

Writes DIR/window.txt (the numbers) and DIR/window_plot.{png,pdf}.

Speed, every timestep: slope of a straight-line fit of front x against step over
the timesteps within ±FIT steps (default 4000). Speed, every 5th file: the
difference between consecutive samples (all a two-file comparison can give),
placed at their midpoint. Crest crossing: where front x passes the crest
(x = -2 m on the overview grid), interpolated linearly between timesteps.
"""
import argparse
import csv
import os

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

CREST = -2.0
BLUE, ORANGE = "#2a78d6", "#eb6834"          # categorical slots 1, 2
INK, MUTED, GRID = "#1f1f1e", "#6b6a64", "#e6e5e0"

ap = argparse.ArgumentParser()
ap.add_argument("front")
ap.add_argument("--window", type=int, nargs=2, default=(9000, 45000))
ap.add_argument("--fit", type=int, default=4000)
ap.add_argument("--out-dir", required=True)
a = ap.parse_args()

rows = list(csv.DictReader(open(a.front)))
num = lambda r, k: float(r[k]) if r[k] != "" else np.nan
lab_all = np.array([int(r["label"]) for r in rows])
fx_all = np.array([num(r, "front_x") for r in rows])
u0_all = np.array([num(r, "u_ahead_k0") for r in rows])
u1_all = np.array([num(r, "u_ahead_k1") for r in rows])
every5 = np.zeros(len(rows), dtype=bool)
every5[::5] = True                           # the two-pass scientist's files

fire = ~np.isnan(fx_all)
lab, fx, u0, u1 = lab_all[fire], fx_all[fire], u0_all[fire], u1_all[fire]
s5 = every5[fire]


def crossing(l, x):
    for k in range(1, len(x)):
        if x[k - 1] < CREST <= x[k]:
            return l[k - 1] + (CREST - x[k - 1]) / (x[k] - x[k - 1]) * (l[k] - l[k - 1])
    return np.nan


def fit_speed(l, x, lo, hi):
    m = (l >= lo) & (l <= hi)
    return np.polyfit(l[m] / 1000.0, x[m], 1)[0], int(m.sum())


speed = np.array([fit_speed(lab, fx, c - a.fit, c + a.fit)[0] for c in lab])
l5, x5, u05 = lab[s5], fx[s5], u0[s5]
speed5 = np.diff(x5) / (np.diff(l5) / 1000.0)
mid5 = (l5[1:] + l5[:-1]) / 2


def first_negative(l, u, after):
    m = (l >= after) & (u < 0)
    return int(l[m][0]) if m.any() else None


cross, cross5 = crossing(lab, fx), crossing(l5, x5)
w0, w1 = a.window
out = []
p = out.append
p(f"timesteps with a front: every timestep {len(lab)}, every 5th file {len(l5)} "
  f"(of {len(lab_all)} / {every5.sum()} files)")
p(f"every-5th files: {', '.join(f'#{v}' for v in lab_all[every5])}")
p(f"crest crossing (front x = {CREST:g} m): every timestep #{cross:.0f}; every 5th #{cross5:.0f}")
for name, lo, hi in (("windward", 8000, 17000), ("lee, early", 20000, 45000),
                     ("lee, late", 45000, 91000)):
    v, n = fit_speed(lab, fx, lo, hi)
    m5 = (l5 >= lo) & (l5 <= hi)
    v5 = np.polyfit(l5[m5] / 1000.0, x5[m5], 1)[0] if m5.sum() >= 2 else np.nan
    p(f"speed {name} (#{lo}-#{hi}): every timestep {v:.1f} m/1000 steps ({n} pts); "
      f"every 5th {v5:.1f} ({int(m5.sum())} pts)")
for name, lo, hi in (("windward", 8000, 17000), ("lee", 19000, 45000), ("lee late", 45000, 91000)):
    m = (lab >= lo) & (lab <= hi)
    p(f"u ahead {name} (#{lo}-#{hi}): 0.75 m level mean {np.nanmean(u0[m]):+.2f} m/s "
      f"[{np.nanmin(u0[m]):+.2f}, {np.nanmax(u0[m]):+.2f}], negative at {int((u0[m] < 0).sum())}/{int(m.sum())}; "
      f"next level kept mean {np.nanmean(u1[m]):+.2f}")
p(f"u ahead at 0.75 m first negative after the windward run: every timestep #{first_negative(lab, u0, 8000)}; "
  f"every 5th #{first_negative(l5, u05, 8000)}")
p(f"chosen window #{w0}-#{w1}: {int(((lab_all >= w0) & (lab_all <= w1)).sum())} files, of which "
  f"{int(((lab_all >= w0) & (lab_all <= w1) & every5).sum())} are every-5th files")
text = "\n".join(out)
print(text)
os.makedirs(a.out_dir, exist_ok=True)
open(os.path.join(a.out_dir, "window.txt"), "w").write(text + "\n")

# ---- the figure: three panels, one time axis --------------------------------
plt.rcParams.update({"font.size": 9, "axes.edgecolor": MUTED, "axes.labelcolor": INK,
                     "xtick.color": MUTED, "ytick.color": MUTED, "axes.linewidth": 0.8})
fig, ax = plt.subplots(3, 1, figsize=(7.0, 6.4), sharex=True,
                       gridspec_kw={"hspace": 0.12})
k = lambda v: np.asarray(v) / 1000.0
for x_ in ax:
    x_.axvspan(w0 / 1000, w1 / 1000, color="#efeee9", zorder=0, lw=0)
    x_.grid(axis="y", color=GRID, lw=0.6)
    x_.spines[["top", "right"]].set_visible(False)
    x_.axvline(cross / 1000, color=MUTED, lw=0.8, ls=":", zorder=1)


def series(x_, xs, ys, xs5, ys5):
    x_.plot(k(xs), ys, color=BLUE, lw=2, marker="o", ms=3, zorder=3,
            label="every timestep (Sieve overview, 88 files)")
    x_.plot(k(xs5), ys5, color=ORANGE, lw=0, marker="o", ms=8, mfc="white",
            mew=2, zorder=4, label="every 5th file (two-pass download, 18 files)")


series(ax[0], lab, fx, l5, x5)
ax[0].axhline(CREST, color=INK, lw=0.8, ls="--")
ax[0].text(90, CREST + 6, "ridge crest", ha="right", va="bottom", color=INK)
ax[0].set_ylabel("front position x (m)")
ax[0].text(w0 / 1000 + 0.6, ax[0].get_ylim()[1], "chosen window", va="top", color=MUTED)
ax[0].text(cross / 1000 + 0.6, -150, f"crosses crest\n#{cross:.0f}", color=MUTED, va="center")
ax[0].legend(loc="lower right", frameon=False)

series(ax[1], lab, speed, mid5, speed5)
ax[1].set_ylabel("front speed\n(m per 1000 steps)")
ax[1].axhline(0, color=MUTED, lw=0.8)

series(ax[2], lab, u0, l5, u05)
ax[2].axhline(0, color=INK, lw=0.8)
ax[2].set_ylabel("u ahead of front\nat 0.75 m (m/s)")
ax[2].set_ylim(-1.05, 2.55)
ax[2].text(91, -0.98, "below 0: reversed, blowing against the spread", ha="right",
           va="bottom", color=MUTED)
ax[2].set_xlabel("timestep (thousands)")
ax[2].set_xlim(6, 92)
for ext in ("png", "pdf"):
    fig.savefig(os.path.join(a.out_dir, f"window_plot.{ext}"), dpi=200, bbox_inches="tight")
print("wrote", os.path.join(a.out_dir, "window_plot.png"))
