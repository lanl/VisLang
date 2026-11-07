#!/usr/bin/env python3
"""Every number Case Study 2 quotes about the fire and the wind, from the evaluation
pass (server/eval_fullres.py, full resolution) and Sieve's overview front
(front_overview.py). Nothing here is read from Test2/Test3.

    python analyze.py EVAL_DIR SIEVE_FRONT.csv BOX_HEIGHTS.csv OUT_DIR

Writes OUT_DIR/summary.json and OUT_DIR/summary.md.

Definitions
  front_x        see eval_fullres.py (grid level 0, |y| <= 50 m, theta = 400 K).
  crossing       the first time the front reaches the crest (x = -2 m), interpolated
                 linearly in time between the two timesteps around it.
  speed          front displacement between consecutive samples / steps between them,
                 in m per 1000 steps (one estimator for every sampling).
  interval speed net displacement over an interval / its length (m per 1000 steps).
  every 5th      the files a two-pass download copies first: the series sorted by
                 label, every 5th from the first (18 of 88).
  heights        each level's height above level 0, averaged over the box (crest and
                 lee slope; box_heights.py), where the wind is analysed.
  near ground    grid levels 1-4 (1.25-5.25 m above level 0). Level 0 is reported as
                 "the lowest grid level": its height above the ground is not recorded.
  u ahead        mean u over the burning strip's rows (|y| <= 50 m) at x - front_x in
                 the given band, weighted by the number of grid points.
  lee ground     fixed x 100-160 m, levels 1-4; strip |y| <= 50 m, flanks |y| >= 90 m.
"""
import csv
import json
import os
import sys
from collections import defaultdict

import numpy as np

CREST = -2.0
NEAR = (1, 2, 3, 4)
WINDOW = (9000, 45000)


def load_fronts(path, stride_col=True):
    out = defaultdict(dict)
    for r in csv.DictReader(open(path)):
        s = int(r["stride"]) if stride_col else 8
        out[s][int(r["label"])] = float(r["front_x"]) if r["front_x"] else None
    return out


def crossing(fr):
    labs = sorted(n for n in fr if fr[n] is not None)
    for a, b in zip(labs, labs[1:]):
        if fr[a] < CREST <= fr[b]:
            return a + (CREST - fr[a]) / (fr[b] - fr[a]) * (b - a), (a, b)
    return None, None


def speeds(fr, labs):
    labs = [n for n in labs if fr.get(n) is not None]
    return [((a + b) / 2, (fr[b] - fr[a]) / (b - a) * 1000, a, b)
            for a, b in zip(labs, labs[1:])]


def interval_speed(fr, a, b):
    return (fr[b] - fr[a]) / (b - a) * 1000


def main(ev, sieve_front, heights, out):
    os.makedirs(out, exist_ok=True)
    F = load_fronts(os.path.join(ev, "fronts.csv"))
    labels = sorted(F[1])
    FS = load_fronts(sieve_front, stride_col=False)[8]
    S = {}

    # Sieve's overview front equals the evaluation's stride-8 front (same sampling)
    common = [n for n in labels if n in FS]
    diff = [abs((FS[n] or 0) - (F[8][n] or 0)) for n in common
            if (FS[n] is None) == (F[8][n] is None)]
    S["sieve_vs_eval_stride8"] = {
        "timesteps": len(common), "max_abs_diff_m": max(diff) if diff else None,
        "presence_mismatch": sum((FS[n] is None) != (F[8][n] is None) for n in common)}

    # Stride sweep against full resolution
    fire = [n for n in labels if F[1][n] is not None]
    sweep = {}
    for s in sorted(F):
        both = [n for n in fire if F[s].get(n) is not None]
        err = np.array([abs(F[s][n] - F[1][n]) for n in both])
        cx, _ = crossing(F[s])
        nx, ny, nz = [len(range(0, d, s)) for d in (600, 500, 61)]
        sweep[s] = {
            "fire_timesteps_found": len(both), "fire_timesteps_full": len(fire),
            "false_fire": sum(F[s][n] is not None and F[1][n] is None for n in labels),
            "front_err_median_m": round(float(np.median(err)), 2) if err.size else None,
            "front_err_mean_m": round(float(err.mean()), 2) if err.size else None,
            "front_err_p90_m": round(float(np.percentile(err, 90)), 2) if err.size else None,
            "front_err_max_m": round(float(err.max()), 2) if err.size else None,
            "grid_spacing_m": 2 * s,
            "within_one_spacing_pct": round(100 * float((err <= 2 * s).mean()), 1) if err.size else None,
            "crossing_step": None if cx is None else round(cx),
            "points_per_timestep": nx * ny * nz,
            "raw_bytes_series": 88 * nx * ny * nz * 5 * 4 + nx * ny * nz * 3 * 8,
        }
    S["stride_sweep"] = sweep

    # Crossing and speeds: full resolution, Sieve overview, every 5th file
    every5 = labels[::5]
    S["every5_labels"] = every5
    for name, fr, labs in (("full", F[1], labels), ("sieve_s8", FS, labels),
                           ("every5_fullres", F[1], every5)):
        cx, br = crossing({n: fr.get(n) for n in labs})
        sp = speeds(fr, labs)
        S[name] = {"crossing_step": None if cx is None else round(cx), "crossing_bracket": br,
                   "speeds": [{"mid": m, "speed": round(v, 2), "from": a, "to": b}
                              for m, v, a, b in sp]}
    S["interval_speeds_fullres"] = {
        f"{a}-{b}": round(interval_speed(F[1], a, b), 2)
        for a, b in ((8000, 17000), (17000, 22000), (22000, 45000), (45000, 91000))}
    S["interval_speeds_sieve_s8"] = {
        f"{a}-{b}": round(interval_speed(FS, a, b), 2)
        for a, b in ((8000, 17000), (17000, 22000), (22000, 45000), (45000, 91000))}

    # Wind ahead of the front (full resolution)
    lev = {int(r["level"]): float(r["height_m"]) for r in csv.DictReader(open(heights))}
    S["level_heights_m"] = {k: round(h, 2) for k, h in lev.items() if k <= 29}
    S["overview_levels_below_12m"] = [k for k in range(0, 39, 8) if lev[k] <= 12.0]
    S["full_levels_below_12m"] = [k for k in lev if lev[k] <= 12.0]
    prof = defaultdict(lambda: [0.0, 0])          # (label, rows, bin, level) -> [sum, n]
    for r in csv.DictReader(open(os.path.join(ev, "profiles.csv"))):
        if r["u_mean"] == "":
            continue
        k = (int(r["label"]), r["rows"], r["bin"], int(r["level"]))
        prof[k] = [float(r["u_mean"]) * int(r["n"]), int(r["n"])]

    def u_ahead(n, bins, levels, rows="strip"):
        s = sum(prof[(n, rows, b, k)][0] for b in bins for k in levels)
        c = sum(prof[(n, rows, b, k)][1] for b in bins for k in levels)
        return s / c if c else None

    win = [n for n in labels if WINDOW[0] <= n <= WINDOW[1] and F[1][n] is not None]
    bands = {"0-10m_ahead_levels1-4": (("0-10",), NEAR),
             "0-60m_ahead_levels1-4": (("0-10", "10-30", "30-60"), NEAR),
             "60-120m_ahead_levels1-4": (("60-120",), NEAR),
             "0-10m_ahead_level0": (("0-10",), (0,))}
    aloft = [k for k in lev if 13.0 <= lev[k] <= 20.5]
    bands[f"0-60m_ahead_aloft_{lev[aloft[0]]:.1f}-{lev[aloft[-1]]:.1f}m"] = (
        ("0-10", "10-30", "30-60"), tuple(aloft))
    series = {}
    for name, (bins, levels) in bands.items():
        v = {n: u_ahead(n, bins, levels) for n in win}
        neg = [n for n in win if v[n] is not None and v[n] < 0]
        series[name] = {"first_negative": neg[0] if neg else None,
                        "negative_timesteps": len(neg), "of": len(win),
                        "u": {n: None if v[n] is None else round(v[n], 3) for n in win}}
    S["u_ahead_strip"] = series

    depth = {}
    for n in win:
        h = 0.0
        for k in range(1, 30):
            uk = u_ahead(n, ("0-10", "10-30", "30-60"), (k,))
            if uk is None or uk >= 0:
                break
            h = lev[k]
        depth[n] = round(h, 2)
    S["reversed_layer_from_level1_0-60m_m"] = depth

    lee = defaultdict(lambda: [0.0, 0])
    for r in csv.DictReader(open(os.path.join(ev, "lee.csv"))):
        if int(r["level"]) in NEAR:
            k = (int(r["label"]), r["rows"])
            lee[k][0] += float(r["u_mean"]) * int(r["n"])
            lee[k][1] += int(r["n"])
    leeu = {k: v[0] / v[1] for k, v in lee.items()}
    out_lee = {}
    for a, b in ((9000, 20000), (21000, 45000)):
        ns = [n for n in labels if a <= n <= b]
        for rows in ("strip", "flanks"):
            vals = [leeu[(n, rows)] for n in ns]
            out_lee[f"{rows}_{a}-{b}"] = {"negative": sum(v < 0 for v in vals), "of": len(vals),
                                         "mean_u": round(float(np.mean(vals)), 2)}
    S["lee_ground_levels1-4"] = out_lee
    S["lee_ground_series"] = {f"{n}": {r: round(leeu[(n, r)], 3) for r in ("strip", "flanks")}
                              for n in labels}

    amb = list(csv.DictReader(open(os.path.join(ev, "ambient.csv"))))
    S["ambient_u_upwind"] = {"level": int(amb[0]["level"]), "height_m": float(amb[0]["height"]),
                             "mean_all": round(float(np.mean([float(r["u_mean"]) for r in amb])), 2),
                             "mean_window": round(float(np.mean(
                                 [float(r["u_mean"]) for r in amb
                                  if WINDOW[0] <= int(r["label"]) <= WINDOW[1]])), 2)}
    S["first_fire_step"] = min(fire)

    json.dump(S, open(os.path.join(out, "summary.json"), "w"), indent=1)
    md = ["# Case Study 2, Test4: numbers from the evaluation pass", ""]
    md += [f"- Sieve overview front vs evaluation stride 8: {S['sieve_vs_eval_stride8']}",
           f"- crossing (step): full {S['full']['crossing_step']}, Sieve s8 "
           f"{S['sieve_s8']['crossing_step']}, every 5th {S['every5_fullres']['crossing_step']} "
           f"(bracket {S['every5_fullres']['crossing_bracket']})",
           f"- interval speeds, full: {S['interval_speeds_fullres']}",
           f"- interval speeds, Sieve s8: {S['interval_speeds_sieve_s8']}",
           f"- levels below 12 m: overview {S['overview_levels_below_12m']}, "
           f"full {S['full_levels_below_12m']}",
           f"- ambient u upwind (not used: near-ground flow at the upwind edge is weak): {S['ambient_u_upwind']}", "", "## Stride sweep", ""]
    md += [f"- stride {s}: {v}" for s, v in sweep.items()]
    md += ["", "## u ahead of the front (strip)", ""]
    md += [f"- {k}: first negative #{v['first_negative']}, negative {v['negative_timesteps']}/{v['of']}"
           for k, v in series.items()]
    md += ["", f"## Lee ground: {out_lee}", ""]
    open(os.path.join(out, "summary.md"), "w").write("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main(*sys.argv[1:5])
