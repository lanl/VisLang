"""Do reverse-flow events sit beneath large-scale high-speed or low-speed regions?
(Definitions fixed in DESIGN.md section 6 / LITERATURE.md section 5 before measuring.)

    python largescale.py <outputs dir> <results dir>     (cvision env: numpy)

Large-scale field u'_L at y+ ~ 100 (Lenaers et al. 2012's height): the step-3b block-mean plane of
each wall (640 x 480 blocks of 16 x 16 cells), minus its mean, low-pass filtered with a sharp
spectral cut-off keeping streamwise wavelengths > delta and spanwise wavelengths > delta/4. The DNS
is periodic in x and in the spanwise direction, so the FFT needs no padding. Sensitivity: spanwise
cut-offs delta/8 and delta/2.

Backflow, three ways (u < 0 next to the lower wall, z = 1, y+ 0.07, unless said):
  (a) points, whole lower wall: the stride overview's negative samples at z = 1 (every 16th cell
      of the plane), the closest match to Lenaers' "points with backflow";
  (b) points, ground-truth area: every cell with u < 0 at z = 1 (1024 x 1024 cells);
  (c) blocks, whole walls: blocks of the block-minimum overview (step 4) whose minimum is < 0, in
      the block row next to each wall (only if s4_min16 exists).
For each: the fraction lying beneath u'_L > 0, against the fraction of the wall's area with
u'_L > 0 (the chance level).
"""
import json
import os
import sys

import numpy as np

OUT, RES = sys.argv[1:3]
os.makedirs(RES, exist_ok=True)
NX_B, NY_B = 640, 480                    # blocks per plane (16 x 16 cells each)
LX, LS = 8 * np.pi, 3 * np.pi            # domain lengths in delta (streamwise, spanwise)
report, lines = {"definition": __doc__.split("\n\n")[1]}, []


def say(s=""):
    print(s)
    lines.append(s)


def load(name):
    p = os.path.join(OUT, f"{name}.u.npy")
    return np.load(p) if os.path.exists(p) else None


def large_scale(plane, span_div):
    """u' low-passed: keep |kx| < 2 pi / delta and |kz| < 2 pi / (delta / span_div)."""
    f = np.fft.fft2(plane - plane.mean())                    # axes: x (640), spanwise (480)
    mx = np.fft.fftfreq(NX_B, d=1.0 / NX_B)                  # mode numbers
    ms = np.fft.fftfreq(NY_B, d=1.0 / NY_B)
    keep = (np.abs(mx)[:, None] < LX / 1.0) & (np.abs(ms)[None, :] < LS / (1.0 / span_div))
    return np.real(np.fft.ifft2(f * keep))


def frac_beneath(ul, ii, jj):
    pos = ul[ii, jj] > 0
    return {"n": int(len(ii)), "beneath_positive": int(pos.sum()),
            "fraction_beneath_positive": float(pos.mean()) if len(ii) else None,
            "area_fraction_positive": float((ul > 0).mean())}


planes = {"lower": load("s3b_lower_yp100")[:, :, 0], "upper": load("s3b_upper_yp100")[:, :, 0]}
stride, truth, low = load("s1_stride16"), load("s2_truth"), load("s4_min16")
for span_div in (4, 8, 2):
    tag = f"lambda_x>delta, lambda_z>delta/{span_div}"
    ul = {w: large_scale(p, span_div) for w, p in planes.items()}
    r = {}
    ii, jj = np.nonzero(stride[:, :, 0] < 0)                              # stride cell = block (i, j)
    r["a_points_whole_lower_wall_stride"] = frac_beneath(ul["lower"], ii, jj)
    ti, tj = np.nonzero(truth[:, :, 0] < 0)                               # area cells, z = 1
    r["b_points_ground_truth_area"] = frac_beneath(ul["lower"], (4608 + ti) // 16, (3328 + tj) // 16)
    if low is not None:
        bi, bj = np.nonzero(low[:, :, 0] < 0)
        r["c_blocks_lower_wall"] = frac_beneath(ul["lower"], bi, bj)
        bi, bj = np.nonzero(low[:, :, -1] < 0)                            # row 95: z 1521-1534
        r["c_blocks_upper_wall"] = frac_beneath(ul["upper"], bi, bj)
    report[tag] = r
    say(f"== {tag} ==")
    for k, v in r.items():
        say(f"  {k}: {v['beneath_positive']} of {v['n']} beneath u'_L > 0 = "
            f"{100 * (v['fraction_beneath_positive'] or 0):.1f} % "
            f"(u'_L > 0 covers {100 * v['area_fraction_positive']:.1f} % of the wall)")
json.dump(report, open(os.path.join(RES, "largescale.json"), "w"), indent=1)
open(os.path.join(RES, "largescale.txt"), "w").write("\n".join(lines) + "\n")
