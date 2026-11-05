"""Step 1 analysis: how often u < 0 in the stride overview, per wall-normal level.

The stride keeps every 16th cell in x and y of the levels z = 1, 17, 33, ... (1 + 16k). The level
z = 1 (y+ 0.07) is the plane next to the lower wall; each level holds 640 x 480 = 307,200 samples,
an unbiased sample of that plane (every 16th cell, fixed phase). Reports, per level with any u < 0:
z, y+ (from the lower wall, or from the upper wall above the centre), the count and fraction of
negative samples, a 95 % Wilson interval, and the most negative value.
Usage: python stride_census.py <s1_stride16.vti> <channel5200-y.txt>
"""
import sys
import numpy as np
import pyvista as pv

RE_TAU = 5185.897
img = pv.read(sys.argv[1])
u = np.asarray(img.point_data["u"]).reshape(img.dimensions, order="F")    # (x, y, z)
y = np.loadtxt(sys.argv[2])
z0, dz = int(img.origin[2]), int(img.spacing[2])
n = u.shape[0] * u.shape[1]


def wilson(k, n, zc=1.96):
    p = k / n
    d = 1 + zc ** 2 / n
    c = (p + zc ** 2 / (2 * n)) / d
    h = zc * np.sqrt(p * (1 - p) / n + zc ** 2 / (4 * n * n)) / d
    return c - h, c + h


print(f"grid {u.shape}, levels z = {z0}, {z0 + dz}, ... {z0 + dz * (u.shape[2] - 1)}; "
      f"{n:,} samples per level")
print("level  z     y+ (from nearer wall)  negatives  fraction %   95% CI %            min u")
total = 0
for k in range(u.shape[2]):
    z = z0 + dz * k
    yplus = (1 + y[z]) * RE_TAU if z < 768 else (1 - y[z]) * RE_TAU
    neg = int((u[:, :, k] < 0).sum())
    total += neg
    if neg or k in (0, u.shape[2] - 1):
        lo, hi = wilson(neg, n)
        print(f"{k:5d}  {z:4d}  {yplus:10.2f} ({'lower' if z < 768 else 'upper'})  {neg:9d}  "
              f"{100 * neg / n:9.4f}   [{100 * lo:.4f}, {100 * hi:.4f}]   {u[:, :, k].min():+.5f}")
print(f"negative samples in the whole overview: {total} of {u.size:,}")
print(f"mean of u per level (first 3): {[round(float(u[:, :, k].mean()), 4) for k in range(3)]}")
