"""Where does a block-minimum pass spend its time on the server: reading, or reducing?
(Not a baseline; a measurement to explain section 7 of the brief.)

    python decompose_slab.py <raw file> <remote VisLang repo> sieve|base <out.npy>   (on gpu-server)

One slab of 16 planes in the middle of the channel (z 753-768, 9.4 GiB), read cold:
  sieve: the raw reader's read (vislang/formats/adapters.py RawAdapter.read_array: an order-F
         memmap of (nx, ny, nz), np.array of the z slab), then Sieve's
         block_reduce(slab, (16, 16, 16), "min") from the remote repo as it stands;
  base:  np.array of the slab from an order-C memmap of (nz, ny, nx) (blockmin.py), then its
         two-step minimum.
Each way runs in its own process: the file's pages are evicted (posix_fadvise DONTNEED) before
the memmap is opened. (A first version ran both in one process; the second read then came from
pages the first memmap still mapped, which DONTNEED does not evict: 0 B read.) Each phase is timed
with perf_counter, and its storage reads come from /proc/self/io read_bytes. The block minima are
saved as (x, y) to <out.npy> for the equality check. Prints one JSON object.
"""
import json
import os
import sys
import time

import numpy as np

F, REPO, WAY, OUT = sys.argv[1:5]
sys.path.insert(0, REPO)
from vislang.interpreter.narrowing import block_reduce  # noqa: E402

NX, NY, NZ, B, Z0 = 10240, 7680, 1536, 16, 753


def read_bytes():
    for line in open("/proc/self/io"):
        if line.startswith("read_bytes:"):
            return int(line.split(":")[1])


def timed(fn):
    r0, t0 = read_bytes(), time.perf_counter()
    out = fn()
    return out, round(time.perf_counter() - t0, 3), read_bytes() - r0


fd = os.open(F, os.O_RDONLY)
os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
os.close(fd)
res = {"way": WAY, "slab": f"z {Z0}-{Z0 + B - 1}", "slab_bytes": NX * NY * B * 8}
if WAY == "sieve":
    mm = np.memmap(F, dtype="<f8", mode="r", shape=(NX, NY, NZ), order="F")
    slab, res["read_s"], res["read_bytes"] = timed(lambda: np.array(mm[:, :, Z0:Z0 + B]))
    out, res["reduce_s"], _ = timed(lambda: block_reduce(slab, (B, B, B), "min"))
    out = out[:, :, 0]
else:
    mc = np.memmap(F, dtype="<f8", mode="r", shape=(NZ, NY, NX))
    slab, res["read_s"], res["read_bytes"] = timed(lambda: np.array(mc[Z0:Z0 + B]))
    out, res["reduce_s"], _ = timed(
        lambda: slab.min(axis=0).reshape(NY // B, B, NX // B, B).min(axis=(1, 3)))
    out = out.T
np.save(OUT, out)
res["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
print(json.dumps(res))
