"""Step 0: wall-normal grid in wall units, and a check of it against the data.

Reads the JHTDB channel5200 wall-normal grid (data/channel5200-y.txt, from
https://flow.pha.jhu.edu/docs/channel5200/channel5200-y.txt) and the mean profile measured
on the file (results/step0/axis_profile.txt). y+ = (1 + y) * Re_tau (lower wall), with
Re_tau = 5185.897 and u_tau = 0.0414872 at bulk velocity 1 (JHTDB Channel5200 page).
Check: in the viscous sublayer U+ = y+, so measured mean u / u_tau at the first levels should
match the grid's y+ there.
"""
import numpy as np
RE_TAU, U_TAU = 5185.897, 0.0414872
y = np.loadtxt("data/channel5200-y.txt")
print(f"grid points: {y.size}; y[0]={y[0]}, y[-1]={y[-1]}, symmetric: {np.allclose(y, -y[::-1])}")
yp = (1 + y) * RE_TAU
prof = {int(l.split()[0]): float(l.split()[1]) for l in open("results/step0/axis_profile.txt")
        if len(l.split()) == 3 and l.split()[0].isdigit()}
print("z  y+(grid)  U+(measured mean u / u_tau)")
for z in (8, 16, 24, 32, 64, 128, 256, 512, 768):
    print(f"{z:4d} {yp[z]:9.2f} {prof[z]/U_TAU:7.2f}")
print("16-cell blocks from z=1 (lower wall): block -> y+ range of its cells")
for b in range(6):
    lo, hi = 1 + 16 * b, 16 * b + 16
    print(f"  block {b}: z {lo}-{hi}: y+ {yp[lo]:.2f} - {yp[hi]:.2f}")
print("4-cell blocks from z=1: first four ->",
      [f"{yp[1+4*b]:.2f}-{yp[4*b+4]:.2f}" for b in range(4)])
print(f"levels with y+ <= 8.5 (lower wall): z 1..{int(np.searchsorted(yp, 8.5)) - 1}")
print(f"spacing: dx+ = {8*np.pi/10240*RE_TAU:.2f}, dspan+ = {3*np.pi/7680*RE_TAU:.2f}")
print(f"block row 8 (z 129-144): y+ {yp[129]:.1f} - {yp[144]:.1f}; "
      f"y+ = 3.9*Re_tau^0.5 = {3.9*RE_TAU**0.5:.0f}")
ypu = (1 - y) * RE_TAU
print(f"upper wall, last block row (z 1521-1534, 14 cells): y+ {ypu[1534]:.2f} - {ypu[1521]:.2f}")
for target in (100.0, 281.0):            # Lenaers et al. 2012 (y+ ~ 100); Mathis et al. 2009 (3.9 Re_tau^0.5)
    z = int(np.argmin(np.abs(yp[:768] - target)))
    zu = 1535 - z
    print(f"plane nearest y+ = {target:.0f}: z = {z} (y+ {yp[z]:.2f}); mirror at the upper wall: z = {zu} "
          f"(y+ {ypu[zu]:.2f})")
print(f"area of 1024 x 1024 cells in wall units: {1024*8*np.pi/10240*RE_TAU:.0f} x "
      f"{1024*3*np.pi/7680*RE_TAU:.0f} = {1024*8*np.pi/10240*RE_TAU*1024*3*np.pi/7680*RE_TAU/1e6:.1f} million")
print(f"one cell in the wall plane: {8*np.pi/10240*RE_TAU*3*np.pi/7680*RE_TAU:.1f} wall units^2; "
      f"a 20-unit-diameter disc covers {np.pi*100/(8*np.pi/10240*RE_TAU*3*np.pi/7680*RE_TAU):.1f} cells; "
      f"a stride of 16 keeps 1 cell in 256 of a plane")
