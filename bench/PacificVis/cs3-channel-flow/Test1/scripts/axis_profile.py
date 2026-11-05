"""Step 0 axis check (runs ON gpu-server, reads ~0.25 GB of the 966 GB file in place).

Reads whole x-rows (x is the fastest axis) at every 8th index of the last axis and
16 evenly spaced indices of the middle axis, then prints, per index of the last axis,
the mean and standard deviation of pressure over the rows read. A channel's wall-normal
axis shows a profile symmetric about the centreline with the variance peaking near both
walls; a homogeneous (spanwise) axis shows a flat profile.
Usage: python axis_profile.py <path to dns_10240x7680x1536_float64.raw>
"""
import sys, time
import numpy as np

NX, NY, NZ = 10240, 7680, 1536
t0 = time.perf_counter()
a = np.memmap(sys.argv[1], dtype="<f8", mode="r", shape=(NZ, NY, NX))  # C order: x fastest
ys = np.linspace(0, NY - 1, 16).astype(int)
zs = np.arange(0, NZ, 8)
mean_z, std_z = [], []
for z in zs:
    rows = np.asarray(a[z, ys, :])
    mean_z.append(rows.mean()); std_z.append(rows.std())
t1 = time.perf_counter()
# middle axis profile at a few levels of the last axis (z), every 64th y, whole x rows
zs2 = np.array([4, 96, 768, 1440, 1531])
yy = np.arange(0, NY, 64)
prof_y = np.array([[np.asarray(a[z, y, :]).std() for y in yy] for z in zs2])
mean_x = np.asarray(a[768, ys, :]).mean(axis=0)  # streamwise mean at the centre plane
t2 = time.perf_counter()
print(f"# read z-profile in {t1-t0:.1f} s, y-profile in {t2-t1:.1f} s (server)")
print("# z  mean  std")
for z, m, s in zip(zs, mean_z, std_z):
    print(f"{z} {m:.6g} {s:.6g}")
print("# y-profile std (rows: z=" + ",".join(map(str, zs2)) + "); min/median/max over y")
for z, p in zip(zs2, prof_y):
    print(f"z={z}: min {p.min():.4g} median {np.median(p):.4g} max {p.max():.4g}")
print("# streamwise mean at z=768 over 16 y rows, in 10 x-bins:")
print(" ".join(f"{v:.4g}" for v in mean_x.reshape(10, -1).mean(axis=1)))
print("# global sample: min", float(np.min(mean_z)), "first values a[0,0,:4] =", np.asarray(a[0, 0, :4]))
