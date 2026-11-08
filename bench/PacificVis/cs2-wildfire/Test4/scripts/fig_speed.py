#!/usr/bin/env python3
"""Paper figure: where the front is and how fast it moves, as each route sees it.

    python fig_speed.py EVAL_FRONTS.csv SIEVE_FRONT.csv COST_TABLE.csv OUT_PREFIX

(a) front position. Grey: full resolution, every timestep (the evaluation pass, a
    reference no route moves). Blue: Sieve's overview, every timestep, every 8th point.
    Orange rings: every 5th file at full resolution, what a two-pass download's first
    pass holds; they lie on the grey line because they are the same files.
(b) front speed (m per 1000 steps). Grey and blue: the slope of a straight line fitted
    to the front over +-2000 steps. Orange: the average speed between two consecutive
    every-5th files, drawn as a bar across the 5000 steps it averages.
Shaded: the window fetched at full resolution. Writes OUT_PREFIX.{png,pdf}.
"""
import csv
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

CREST, HALF = -2.0, 2000
WINDOW = (9000, 45000)
BLUE, ORANGE, GREY = "#2a78d6", "#eb6834", "#a9a8a2"
CRESTBAND = "#d9d7cf"
INK, MUTED, GRID, BAND = "#1f1f1e", "#6b6a64", "#e6e5e0", "#efeee9"

ev, sv, cost, out = sys.argv[1:5]
full = {int(r["label"]): float(r["front_x"]) for r in csv.DictReader(open(ev))
        if r["stride"] == "1" and r["front_x"]}
sieve = {int(r["label"]): float(r["front_x"]) for r in csv.DictReader(open(sv)) if r["front_x"]}
labels_all = sorted(int(r["label"]) for r in csv.DictReader(open(ev)) if r["stride"] == "1")
every5 = [n for n in labels_all[::5] if n in full]
rows = {r["route"]: r for r in csv.DictReader(open(cost))}
sieve_gb = int(rows["Sieve"]["overview"]) / 1e9
pass1_gb = int(rows["Two-pass download"]["overview"]) / 1e9


def ylab(ax, main, sub=None, pad=9):
    """Axis label with its qualifier in smaller type, on its own line nearer the axis."""
    ax.set_ylabel(main, labelpad=pad if sub else 4)
    if sub:
        ax.annotate(sub, xy=(1, 0.5), xycoords=ax.yaxis.label, xytext=(1.5, 0),
                    textcoords="offset points", rotation=90, ha="left", va="center",
                    fontsize=5.6, color=INK)


def fit_speed(fr):
    labs = np.array(sorted(fr))
    xs = np.array([fr[n] for n in labs])
    sp = []
    for c in labs:
        m = np.abs(labs - c) <= HALF
        sp.append(np.polyfit(labs[m] / 1000, xs[m], 1)[0] if m.sum() >= 2 else np.nan)
    return labs, np.array(sp)


def crossing(fr):
    """The two saved timesteps the front crosses the crest between."""
    labs = sorted(fr)
    for a, b in zip(labs, labs[1:]):
        if fr[a] < CREST <= fr[b]:
            return a, b


plt.rcParams.update({"font.size": 7.5, "axes.edgecolor": MUTED, "axes.labelcolor": INK,
                     "xtick.color": MUTED, "ytick.color": MUTED, "axes.linewidth": 0.7,
                     "pdf.fonttype": 42})
fig, (a1, a2) = plt.subplots(2, 1, figsize=(3.45, 3.3), sharex=True,
                             gridspec_kw={"height_ratios": [1, 1], "hspace": 0.12})
cx = crossing(full)
for ax in (a1, a2):
    ax.axvspan(WINDOW[0] / 1000, WINDOW[1] / 1000, color=BAND, zorder=0, lw=0)
    ax.axvspan(cx[0] / 1000, cx[1] / 1000, color=CRESTBAND, lw=0, zorder=0.5)
    ax.grid(axis="y", color=GRID, lw=0.5)
    ax.spines[["top", "right"]].set_visible(False)

lf = sorted(full)
a1.axhline(CREST, color=MUTED, lw=0.6, ls="--")
a1.text(91, CREST + 8, "ridge crest", ha="right", va="bottom", color=MUTED, fontsize=6.5)
a1.plot([n / 1000 for n in lf], [full[n] for n in lf], color=GREY, lw=2.6, zorder=2,
        label="full resolution (reference)")
ls_ = sorted(sieve)
a1.plot([n / 1000 for n in ls_], [sieve[n] for n in ls_], color=BLUE, lw=1.0, marker="o",
        ms=1.8, zorder=3, label=f"Sieve overview: all {len(labels_all)} timesteps, {sieve_gb:.2f} GB")
a1.plot([n / 1000 for n in every5], [full[n] for n in every5], lw=0, marker="o", ms=4.5,
        mfc="white", mec=ORANGE, mew=1.3, zorder=4,
        label=f"every 5th file: {len(labels_all[::5])} timesteps, {pass1_gb:.1f} GB")
a1.set_ylabel("front position x (m)")
a1.text(cx[1] / 1000 + 0.8, a1.get_ylim()[0] + 10, "front\ncrosses\ncrest", color=MUTED,
        fontsize=6.5, va="bottom")
a1.legend(loc="lower left", bbox_to_anchor=(0, 1.0), frameon=False, fontsize=6.5,
          handletextpad=0.4, borderaxespad=0.1)

lab, sp = fit_speed(full)
a2.plot(lab / 1000, sp, color=GREY, lw=2.6, zorder=2)
lab, sp = fit_speed(sieve)
a2.plot(lab / 1000, sp, color=BLUE, lw=1.0, marker="o", ms=1.8, zorder=3)
for a, b in zip(every5, every5[1:]):
    v = (full[b] - full[a]) / (b - a) * 1000
    a2.plot([a / 1000, b / 1000], [v, v], color=ORANGE, lw=1.6, zorder=4,
            solid_capstyle="butt")
    a2.plot([a / 1000, b / 1000], [v, v], lw=0, marker="|", ms=4, color=ORANGE, zorder=4)
a2.axhline(0, color=MUTED, lw=0.6)
ylab(a2, "front speed", "(m per 1000 steps)")
a2.set_xlabel("timestep (thousands)")
a2.set_xlim(6, 92)
a2.text(WINDOW[1] / 1000 - 0.6, a2.get_ylim()[0] + 0.6, "full-resolution window",
        ha="right", va="bottom", color=MUTED, fontsize=6.5)
for _ax, _l in zip((a1, a2), "abc"):
    _ax.text(-0.36, 1.02, f"({_l})", transform=_ax.transAxes, fontsize=8, fontweight="bold", va="top", ha="left", color=INK)
fig.savefig(out + ".png", dpi=300, bbox_inches="tight")
fig.savefig(out + ".pdf", bbox_inches="tight")
print(out)
