"""Generate a synthetic particle-style .npz file for testing the Tier-1 path.

Usage:
    python gen_npz.py                 # 10M particles (~320 MB), test_particles.npz
    python gen_npz.py 50000000        # 50M particles (~1.6 GB)
    python gen_npz.py 1000000 small.npz
"""

import sys
import numpy as np

N = int(sys.argv[1]) if len(sys.argv) > 1 else 10_000_000
OUT = sys.argv[2] if len(sys.argv) > 2 else "test_particles.npz"
BOX_SIZE = 100.0  # Mpc/h, say

rng = np.random.default_rng(42)

# Clustered positions: a few gaussian blobs on a uniform background,
# so the data looks vaguely cosmological rather than pure noise.
n_blobs = 8
frac_clustered = 0.6
n_clustered = int(N * frac_clustered)
n_uniform = N - n_clustered

centers = rng.uniform(0, BOX_SIZE, size=(n_blobs, 3))
which = rng.integers(0, n_blobs, size=n_clustered)
clustered = centers[which] + rng.normal(0, 3.0, size=(n_clustered, 3))
uniform = rng.uniform(0, BOX_SIZE, size=(n_uniform, 3))
pos = np.vstack([clustered, uniform]) % BOX_SIZE  # wrap into the box

vel = rng.normal(0, 200.0, size=(N, 3))           # km/s
mass = np.full(N, 1.2e9)                          # Msun/h, equal-mass particles
ids = np.arange(N, dtype=np.int64)

np.savez(
    OUT,
    x=pos[:, 0].astype(np.float32),
    y=pos[:, 1].astype(np.float32),
    z=pos[:, 2].astype(np.float32),
    vx=vel[:, 0].astype(np.float32),
    vy=vel[:, 1].astype(np.float32),
    vz=vel[:, 2].astype(np.float32),
    mass=mass.astype(np.float32),
    id=ids,
    box_size=np.float64(BOX_SIZE),
)

import os
print(f"wrote {OUT}: {N:,} particles, {os.path.getsize(OUT) / 1e6:.1f} MB")
