"""Evaluate the stride, block-mean and block-minimum overviews on the ground-truth area
(definitions fixed in DESIGN.md section 6 before any overview was seen).

    python evaluate_area.py <outputs dir> <channel5200-y.txt> <results dir>
    (cvision env: numpy, scipy, scikit-image; inputs are the .npy/.meta.json written by vti2npy.py)

Inputs: s2_truth (1024 x 1024 x 64 cells, z = 1..64), s1_stride16 (whole-domain stride overview),
s3_area_mean16 / s3_area_min16 (64 x 64 x 4 blocks of the area, from Sieve's cache).
Writes <results>/area_eval.json, area_eval.txt, events.csv.
"""
import csv
import json
import os
import sys

import numpy as np
from scipy import ndimage
from skimage.metrics import structural_similarity

OUT, YFILE, RES = sys.argv[1:4]
os.makedirs(RES, exist_ok=True)
RE_TAU = 5185.897
DX = 8 * np.pi / 10240 * RE_TAU          # streamwise cell, wall units (12.73)
DS = 3 * np.pi / 7680 * RE_TAU           # spanwise cell, wall units (6.36)
B = 16
yplus = (1 + np.loadtxt(YFILE)) * RE_TAU  # lower wall


def load(name):
    meta = json.load(open(os.path.join(OUT, f"{name}.meta.json")))
    return np.load(os.path.join(OUT, f"{name}.u.npy")), meta


truth, tm = load("s2_truth")
stride, sm = load("s1_stride16")
amean, _ = load("s3_area_mean16")
amin, _ = load("s3_area_min16")
x0, y0, z0 = (int(v) for v in tm["origin"])          # 4608, 3328, 1
nx, ny, nz = truth.shape
report, lines = {}, []


def say(s=""):
    print(s)
    lines.append(s)


# --- A. cross-checks: every overview agrees with the ground truth ------------------------------
i0, j0 = x0 // B, y0 // B                              # stride index of the area's first cell
k_n = nz // B
st_area = stride[i0:i0 + nx // B, j0:j0 + ny // B, 0:k_n]
blocks = truth.reshape(nx // B, B, ny // B, B, k_n, B)
loc_min = blocks.min(axis=(1, 3, 5))
loc_mean = blocks.mean(axis=(1, 3, 5))
report["crosscheck"] = {
    "stride_equals_truth_at_stride_cells": bool(np.array_equal(st_area, truth[::B, ::B, ::B])),
    "sieve_min_equals_local_block_min": bool(np.array_equal(amin, loc_min)),
    "sieve_mean_max_abs_diff_from_local_block_mean": float(np.abs(amean - loc_mean).max()),
}
say("== A. cross-checks ==")
for k, v in report["crosscheck"].items():
    say(f"  {k}: {v}")

# --- B. how often, and how far from the wall -----------------------------------------------------
neg = truth < 0
frac = neg.mean(axis=(0, 1))
reach = int(np.nonzero(frac)[0].max()) if neg.any() else None
report["fraction_per_level"] = [{"z": z0 + k, "yplus": float(yplus[z0 + k]),
                                 "fraction": float(frac[k]), "count": int(neg[:, :, k].sum())}
                                for k in range(nz) if frac[k] > 0 or k < 2]
report["wall_plane_fraction"] = float(frac[0])
report["reach"] = None if reach is None else {"z": z0 + reach, "yplus": float(yplus[z0 + reach])}
say("\n== B. fraction of cells with u < 0, by level (levels with any) ==")
for r in report["fraction_per_level"]:
    say(f"  z {r['z']:3d}  y+ {r['yplus']:7.3f}  {100 * r['fraction']:.4f} %  ({r['count']} cells)")
say(f"  highest level with any u < 0: {report['reach']}")

# --- C. regions of reverse flow (26-connected; 6-connected as sensitivity) -----------------------
lab26, n26 = ndimage.label(neg, structure=np.ones((3, 3, 3)))
lab6, n6 = ndimage.label(neg)
area_wu2 = nx * DX * ny * DS
objs = ndimage.find_objects(lab26)
events = []
for e, sl in enumerate(objs, start=1):
    sub = lab26[sl] == e
    idx = np.argwhere(sub) + [s.start for s in sl]
    vals = truth[tuple(idx.T)]
    kmin = idx[np.argmin(vals)]
    zs = idx[:, 2]
    events.append({
        "id": e, "cells": int(len(idx)),
        "Dx_plus": float((sl[0].stop - sl[0].start) * DX),
        "Dspan_plus": float((sl[1].stop - sl[1].start) * DS),
        "top_yplus": float(yplus[z0 + zs.max()]), "attached": bool(zs.min() == 0),
        "touches_side": bool(sl[0].start == 0 or sl[0].stop == nx or sl[1].start == 0 or sl[1].stop == ny),
        "min_u": float(vals.min()), "min_cell": [int(v) for v in kmin],
        "centroid": [float(v) for v in idx.mean(axis=0)],
    })
report["regions"] = {
    "n_26": int(n26), "n_6": int(n6), "area_million_wu2": area_wu2 / 1e6,
    "per_million_wu2_26": n26 / (area_wu2 / 1e6), "per_million_wu2_6": n6 / (area_wu2 / 1e6),
    "attached_fraction": float(np.mean([e["attached"] for e in events])) if events else None,
    "touching_sides": int(sum(e["touches_side"] for e in events)),
    "Dx_plus_mean": float(np.mean([e["Dx_plus"] for e in events])) if events else None,
    "Dspan_plus_mean": float(np.mean([e["Dspan_plus"] for e in events])) if events else None,
    "cells_median": float(np.median([e["cells"] for e in events])) if events else None,
    "top_yplus_max": float(max(e["top_yplus"] for e in events)) if events else None,
}
say("\n== C. regions of u < 0 in the area ==")
for k, v in report["regions"].items():
    say(f"  {k}: {v}")

# --- D. what each overview shows, per block ------------------------------------------------------
flags = {"stride": truth[::B, ::B, ::B] < 0, "mean": amean < 0, "min": amin < 0}
holds = loc_min < 0                                     # block holds >= 1 backflow cell
bi = lambda c: (c[0] // B, c[1] // B, c[2] // B)
ops = {}
for name, f in flags.items():
    tp = int((f & holds).sum())
    det, dist, bias = 0, [], []
    for e in events:
        sl = objs[e["id"] - 1]                          # the region's bounding slices
        cells = np.argwhere(lab26[sl] == e["id"]) + [t.start for t in sl]
        blk = {bi(c) for c in cells}
        hit = [b for b in blk if f[b]]
        if hit:
            det += 1
            cx, cy, cz = e["centroid"]
            d = min(np.hypot((b[0] * B + (B - 1) / 2 - cx) * DX, (b[1] * B + (B - 1) / 2 - cy) * DS)
                    for b in hit)
            dist.append(d)
        b = bi(e["min_cell"])
        value = {"stride": truth[b[0] * B, b[1] * B, b[2] * B], "mean": amean[b], "min": amin[b]}[name]
        bias.append(float(value - e["min_u"]))
    ops[name] = {
        "blocks_flagged": int(f.sum()), "blocks_holding_backflow": int(holds.sum()),
        "block_precision": tp / f.sum() if f.sum() else None,
        "block_recall": tp / holds.sum() if holds.sum() else None,
        "event_recall": det / len(events) if events else None, "events_detected": det,
        "localization_median_wu": float(np.median(dist)) if dist else None,
        "value_bias_median": float(np.median(bias)) if bias else None,
    }

# --- E. SSIM per wall-parallel plane of block row 0, against each overview upsampled ------------
row0 = truth[:, :, :B]
rng = float(row0.max() - row0.min())
up = lambda a: np.repeat(np.repeat(a, B, axis=0), B, axis=1)
view = {"stride": up(truth[::B, ::B, 0]), "mean": up(amean[:, :, 0]), "min": up(amin[:, :, 0])}
for name, img in view.items():
    s = [structural_similarity(row0[:, :, k], img, data_range=rng) for k in range(B)]
    ops[name]["ssim_row0_mean"] = float(np.mean(s))
    ops[name]["ssim_row0_per_plane"] = [float(v) for v in s]
report["operators"] = ops
say("\n== D/E. per operator (block row = 16 levels; blocks over all 4 rows) ==")
for name, o in ops.items():
    say(f"  {name:6s} event recall {o['event_recall']}  ({o['events_detected']} of {len(events)}), "
        f"block precision {o['block_precision']}, block recall {o['block_recall']}, "
        f"flagged {o['blocks_flagged']} / holding {o['blocks_holding_backflow']}, "
        f"localization median {o['localization_median_wu']} wu, value bias median "
        f"{o['value_bias_median']}, SSIM row 0 {o['ssim_row0_mean']:.4f}")

json.dump(report, open(os.path.join(RES, "area_eval.json"), "w"), indent=1)
open(os.path.join(RES, "area_eval.txt"), "w").write("\n".join(lines) + "\n")
with open(os.path.join(RES, "events.csv"), "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=[k for k in events[0] if k not in ("min_cell", "centroid")]
                       if events else ["id"], extrasaction="ignore")
    w.writeheader()
    w.writerows(events)
