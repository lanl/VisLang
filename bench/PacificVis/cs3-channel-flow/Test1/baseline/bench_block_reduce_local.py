"""Sieve's block_reduce against the baseline's two-step minimum, on one quarter of a step-4 slab
held in memory (no I/O). A local measurement, not a baseline.

    python bench_block_reduce_local.py <VisLang repo>     (vislang env; needs ~5 GB of memory)

The slab is 2560 x 7680 x 16 float64 (2.5 GB) of standard-normal values, in order F as the raw
reader returns it. block_reduce reduces it in Sieve's way; the baseline reduces the same values
laid out (z, y, x) in order C, as blockmin.py reads them (that layout copy is not timed).
"""
import sys
import time

import numpy as np

sys.path.insert(0, sys.argv[1])
from vislang.interpreter.narrowing import block_reduce  # noqa: E402

nx, ny, nz, b = 2560, 7680, 16, 16
a = np.asfortranarray(np.random.default_rng(0).standard_normal((nx, ny, nz)))
t = time.perf_counter()
s = block_reduce(a, (b, b, b), "min")
t1 = time.perf_counter() - t
c = np.ascontiguousarray(a.transpose(2, 1, 0))
t = time.perf_counter()
m = c.min(axis=0).reshape(ny // b, b, nx // b, b).min(axis=(1, 3))
t2 = time.perf_counter() - t
print(f"slab {nx} x {ny} x {nz} float64, {a.nbytes:,} B")
print(f"equal: {np.array_equal(s[:, :, 0], m.T)}")
print(f"Sieve block_reduce: {t1:.2f} s; baseline two-step min: {t2:.2f} s; ratio {t1 / t2:.1f}")
