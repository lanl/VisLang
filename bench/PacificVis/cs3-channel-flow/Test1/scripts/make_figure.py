"""Case Study 3 figure (DESIGN.md section 7): what each overview shows of the reverse flow.

    python make_figure.py <outputs dir> <results dir> <figures dir>   (cvision env)

Row 1, the ground-truth area (1024 x 1024 cells of the lower wall), block row 0 (z 1-16,
y+ 0.07-8.1), one shared diverging scale centred at u = 0 (blue = reverse flow):
  (a) full resolution: the minimum of u over z 1-16 per cell column;
  (b) stride: the samples at z = 1 (every 16th cell);
  (c) block mean;  (d) block minimum  (64 x 64 blocks, from Sieve's cache).
  Every reverse-flow region (26-connected, u < 0) is a ring at its centroid on every panel.
  Under each panel: regions shown (event recall) and SSIM, from results/eval/area_eval.json.
Row 2, the whole lower wall (640 x 480 blocks): the large-scale field u'_L at y+ ~ 100
(pre-registered filter: lambda_x > delta, lambda_z > delta/4) in grey; the blocks whose minimum
is < 0 (step 4) as red dots; the ground-truth area outlined.
"""
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm
from matplotlib.patches import Rectangle
from scipy import ndimage

OUT, RES, FIG = sys.argv[1:4]
os.makedirs(FIG, exist_ok=True)
RE_TAU = 5185.897
DX, DS = 8 * np.pi / 10240 * RE_TAU, 3 * np.pi / 7680 * RE_TAU       # wall units per cell
X0, Y0, N, B = 4608, 3328, 1024, 16
INK, MUTED = "#1f1f1e", "#6b6a66"
# diverging: blue (reverse flow) <- grey midpoint -> red; reference ramps (dataviz palette.md)
DIV = LinearSegmentedColormap.from_list(
    "div", ["#0d366b", "#2a78d6", "#9ec5f4", "#f0efec", "#f2b3a9", "#e34948", "#8a1f1f"])
NORM = TwoSlopeNorm(vmin=-0.006, vcenter=0.0, vmax=0.006)   # near the wall u ~ 0.003 U_b

load = lambda n: np.load(os.path.join(OUT, f"{n}.u.npy"))
truth, stride = load("s2_truth"), load("s1_stride16")
amean, amin, low = load("s3_area_mean16"), load("s3_area_min16"), load("s4_min16")
plane = load("s3b_lower_yp100")[:, :, 0]
ev = json.load(open(os.path.join(RES, "eval", "area_eval.json")))["operators"]

# regions and their centroids (as in evaluate_area.py), in wall units from the area's corner
lab, n = ndimage.label(truth < 0, structure=np.ones((3, 3, 3)))
cen = np.array(ndimage.center_of_mass(np.ones_like(lab), lab, range(1, n + 1)))
cx, cy = (cen[:, 0] + 0.5) * DX, (cen[:, 1] + 0.5) * DS

# large-scale field (largescale.py's pre-registered filter)
f = np.fft.fft2(plane - plane.mean())
mx = np.abs(np.fft.fftfreq(640, d=1 / 640))[:, None]
ms = np.abs(np.fft.fftfreq(480, d=1 / 480))[None, :]
ul = np.real(np.fft.ifft2(f * ((mx < 8 * np.pi) & (ms < 3 * np.pi * 4))))

plt.rcParams.update({"font.family": "sans-serif", "font.size": 8, "axes.edgecolor": MUTED,
                     "axes.labelcolor": INK, "xtick.color": MUTED, "ytick.color": MUTED,
                     "axes.linewidth": 0.6})
fig = plt.figure(figsize=(7.2, 5.0))
gs = fig.add_gridspec(2, 4, height_ratios=[1, 1.75], hspace=0.38, wspace=0.08,
                      left=0.07, right=0.875, top=0.96, bottom=0.08)
W, H = N * DX, N * DS
panels = [
    ("(a) full resolution", truth[:, :, :B].min(axis=2), None),
    ("(b) stride", stride[X0 // B:(X0 + N) // B, Y0 // B:(Y0 + N) // B, 0], "stride"),
    ("(c) block mean", amean[:, :, 0], "mean"),
    ("(d) block minimum", amin[:, :, 0], "min"),
]
for k, (title, img, op) in enumerate(panels):
    ax = fig.add_subplot(gs[0, k])
    im = ax.imshow(img.T, origin="lower", cmap=DIV, norm=NORM, extent=(0, W, 0, H),
                   interpolation="nearest", aspect="equal")
    ax.scatter(cx, cy, s=5, facecolors="none", edgecolors=INK, linewidths=0.35)
    ax.set_title(title, fontsize=8, color=INK, loc="left", pad=3)
    if op:
        o = ev[op]
        ax.set_xlabel(f"regions shown {o['events_detected']} / 208\nSSIM {o['ssim_row0_mean']:.3f}",
                      fontsize=7.5, color=INK, labelpad=3)
    else:
        ax.set_xlabel("208 regions (rings)\nreference", fontsize=7.5, color=INK, labelpad=3)
    ax.set_xticks([0, 5000, 10000])
    ax.set_xticklabels(["0", "5k", "10k"], fontsize=6.5)
    if k == 0:
        ax.set_yticks([0, 3000, 6000])
        ax.set_yticklabels(["0", "3k", "6k"], fontsize=6.5)
        ax.set_ylabel("spanwise $z^+$", fontsize=7.5)
    else:
        ax.set_yticks([])
cax = fig.add_axes([0.89, 0.70, 0.012, 0.24])
cb = fig.colorbar(im, cax=cax, ticks=[-0.006, -0.003, 0, 0.003, 0.006], extend="both")
cb.ax.tick_params(labelsize=6.5, colors=MUTED)
cb.set_label("$u/U_b$ (blue < 0)", fontsize=7, color=INK)
fig.text(0.07, 0.595, "(a)-(d): streamwise $x^+$ 0-13,035 by spanwise $z^+$ 0-6,513; block row 0 = z 1-16 ($y^+$ 0.07-8.1); rings: the 208 reverse-flow regions", fontsize=6.5, color=MUTED)

ax = fig.add_subplot(gs[1, :])
LX, LS = 8 * np.pi, 3 * np.pi
lim = np.abs(ul).max()
ax.imshow(ul.T, origin="lower", cmap="Greys_r", vmin=-lim, vmax=lim, extent=(0, LX, 0, LS),
          interpolation="nearest", aspect="equal")
bi, bj = np.nonzero(low[:, :, 0] < 0)
ax.scatter((bi + 0.5) * LX / 640, (bj + 0.5) * LS / 480, s=0.6, color="#e34948", linewidths=0,
           rasterized=True)
ax.add_patch(Rectangle((X0 / 10240 * LX, Y0 / 7680 * LS), N / 10240 * LX, N / 7680 * LS,
                       fill=False, edgecolor="#2a78d6", linewidth=1.2))
ax.set_xlabel("streamwise $x/\\delta$", fontsize=7.5)
ax.set_ylabel("spanwise $z/\\delta$", fontsize=7.5)
ax.tick_params(labelsize=6.5)
n_pos = int((ul[bi, bj] > 0).sum())
ax.set_title(f"(e) whole lower wall: {len(bi):,} blocks hold reverse flow (red); grey: large-scale $u'_L$ at $y^+\\approx 100$, light = high speed", fontsize=7.5, color=INK, loc="left", pad=3)
ax.text(X0 / 10240 * LX, (Y0 + N) / 7680 * LS + 0.08, "ground-truth area (a)-(d)", fontsize=6.5,
        color="#1c5cab", va="bottom", bbox=dict(facecolor="white", edgecolor="none", pad=1))
for ext in ("png", "pdf"):
    fig.savefig(os.path.join(FIG, f"fig_cs3_overviews.{ext}"), dpi=300)
print("regions", n, "wall blocks", len(bi), "under u'_L>0", n_pos)
