"""POST HOC (written after largescale.py's result was seen, 2026-11-05): how sensitive is the
"reverse flow beneath u'_L > 0" fraction to the height of the large-scale plane and to the filter?

    python largescale_posthoc.py <outputs dir> <results dir>     (cvision env: numpy)

The pre-registered analysis is largescale.py (plane at y+ ~ 100, sharp cut-off lambda_x > delta,
lambda_z > delta/4). This script varies only what Lenaers et al. (2012) leave open or what differs
between their Re_tau 1000 and ours (5186):

  heights   y+ 100 (z 81 / 1454, step 3b: Lenaers' height in viscous units)
            y+ 281 (z 147 / 1388, step 7: Mathis et al. 2009's outer site, in the design beforehand)
            y+ 519 (z 207 / 1328, step 7: y = 0.1 delta, Lenaers' height in outer units)
  filters   outer, sharp:      lambda_x > delta, lambda_z > delta/4          (pre-registered)
            viscous, sharp:    lambda_x+ > 1000, lambda_z+ > 250   (delta, delta/4 at Re_tau 1000)
            outer, Gaussian:   H = exp(-ln2 (k/kc)^2), half power at the outer cut-offs
            viscous, Gaussian: the same at the viscous cut-offs
            (Lenaers used "a Gaussian low-pass filter" of unstated width.)

Backflow measures as in largescale.py: (a) stride points on the lower wall plane (z = 1), (b) the
ground-truth area's points at z = 1, (c) step 4's blocks with minimum < 0 next to each wall.
"""
import json
import os
import sys

import numpy as np

OUT, RES = sys.argv[1:3]
NX_B, NY_B = 640, 480
LX, LS = 8 * np.pi, 3 * np.pi            # domain lengths in delta
RE_TAU = 5185.897
lines = []


def say(s=""):
    print(s)
    lines.append(s)


def load(name):
    return np.load(os.path.join(OUT, f"{name}.u.npy"))


# cut-off wavelengths in delta: (lambda_x, lambda_z)
CUTS = {"outer": (1.0, 0.25), "viscous": (1000 / RE_TAU, 250 / RE_TAU)}


def large_scale(plane, cut, shape):
    lx, lz = CUTS[cut]
    f = np.fft.fft2(plane - plane.mean())
    kx = np.abs(np.fft.fftfreq(NX_B, d=1.0 / NX_B))[:, None] / LX     # 1 / wavelength, in 1/delta
    kz = np.abs(np.fft.fftfreq(NY_B, d=1.0 / NY_B))[None, :] / LS
    if shape == "sharp":
        h = (kx < 1 / lx) & (kz < 1 / lz)
    else:
        h = np.exp(-np.log(2) * ((kx * lx) ** 2 + (kz * lz) ** 2))
    return np.real(np.fft.ifft2(f * h))


def frac(ul, ii, jj):
    pos = ul[ii, jj] > 0
    return {"n": int(len(ii)), "beneath_positive": int(pos.sum()),
            "fraction": float(pos.mean()), "chance": float((ul > 0).mean())}


planes = {
    "y+100": ("s3b_lower_yp100", "s3b_upper_yp100"),
    "y+281": ("s7_lower_yp281", "s7_upper_yp281"),
    "y+519": ("s7_lower_yp519", "s7_upper_yp519"),
}
stride, truth, low = load("s1_stride16"), load("s2_truth"), load("s4_min16")
backflow = {
    "a_stride_points_lower": ("lower", np.nonzero(stride[:, :, 0] < 0)),
    "b_area_points_lower": ("lower", tuple((o + t) // 16 for o, t in
                                            zip((4608, 3328), np.nonzero(truth[:, :, 0] < 0)))),
    "c_blocks_lower": ("lower", np.nonzero(low[:, :, 0] < 0)),
    "c_blocks_upper": ("upper", np.nonzero(low[:, :, -1] < 0)),
}
report = {"definition": __doc__.split("\n\n")[0], "results": []}
say("fraction of backflow beneath u'_L > 0 (chance = share of the wall with u'_L > 0)")
say(f"{'height':6s} {'filter':17s} " + " ".join(f"{k:>24s}" for k in backflow))
for h, (lo_name, up_name) in planes.items():
    pl = {"lower": load(lo_name)[:, :, 0], "upper": load(up_name)[:, :, 0]}
    for cut in CUTS:
        for shape in ("sharp", "gaussian"):
            ul = {w: large_scale(p, cut, shape) for w, p in pl.items()}
            row = {k: frac(ul[w], *ij) for k, (w, ij) in backflow.items()}
            report["results"].append({"height": h, "cut": cut, "shape": shape, **row})
            say(f"{h:6s} {cut + ', ' + shape:17s} " + " ".join(
                f"{100 * r['fraction']:5.1f} % (ch {100 * r['chance']:4.1f}) n={r['n']:<5d}"
                for r in row.values()))
os.makedirs(RES, exist_ok=True)
json.dump(report, open(os.path.join(RES, "largescale_posthoc.json"), "w"), indent=1)
open(os.path.join(RES, "largescale_posthoc.txt"), "w").write("\n".join(lines) + "\n")
