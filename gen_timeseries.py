#!/usr/bin/env python3
"""Generate a timeseries folder of particle files for VisLang.

Writes N HDF5 files named `<stem>#<t>.hdf5` (t = 1..N, the timestep), each with
particle fields x, y, z, temperature, density, mass populated with random values:
  - x, y, z        in [1, 128]      (positions in a 128^3 box)
  - temperature,
    density, mass   in [0, 1000)

A folder like this is a VisLang *timeseries* source — the interpreter maps the
chain over the timesteps, and `timesteps(...)` picks a range:

    save(subsample(timesteps(source("timeseries_data/"), 8, 11), 2), "out/")

Usage:
    python gen_timeseries.py [out_dir] [-n 20] [-p 1000] [--stem particles] [--seed 0]
"""
import argparse
import os

import numpy as np


def generate(out_dir, n_files=20, n_particles=1000, stem="particles", seed=0):
    """Write `n_files` HDF5 timesteps into `out_dir`; return the list of paths."""
    import h5py
    os.makedirs(out_dir, exist_ok=True)
    rng = np.random.default_rng(seed)
    paths = []
    for t in range(1, n_files + 1):
        path = os.path.join(out_dir, f"{stem}#{t}.hdf5")
        with h5py.File(path, "w") as f:
            for axis in ("x", "y", "z"):                       # positions in [1, 128]
                f.create_dataset(axis, data=rng.uniform(1.0, 128.0, n_particles))
            for field in ("temperature", "density", "mass"):   # other fields < 1000
                f.create_dataset(field, data=rng.uniform(0.0, 1000.0, n_particles))
        paths.append(path)
    return paths


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out_dir", nargs="?", default="timeseries_data",
                    help="output folder (default: timeseries_data)")
    ap.add_argument("-n", "--n-files", type=int, default=20, help="number of timesteps")
    ap.add_argument("-p", "--particles", type=int, default=1000,
                    help="particles per file")
    ap.add_argument("--stem", default="particles", help="filename stem before #N")
    ap.add_argument("--seed", type=int, default=0, help="RNG seed")
    args = ap.parse_args()

    paths = generate(args.out_dir, args.n_files, args.particles, args.stem, args.seed)
    print(f"Wrote {len(paths)} files to {args.out_dir}/")
    print(f"  {os.path.basename(paths[0])} … {os.path.basename(paths[-1])}")
    print(f"  fields: x, y, z (1–128), temperature, density, mass (<1000); "
          f"{args.particles} particles each")
