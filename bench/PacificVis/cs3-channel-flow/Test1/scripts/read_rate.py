"""Step 0 read-rate probe (runs ON gpu-server): reads 2 GiB sequentially from the middle
of the file, then a stride-16 sample of a 64-row slab via a memory map, and prints
seconds and the process's /proc/self/io counters before and after each, to learn
(a) the sequential NFS read rate, (b) whether read_bytes accounts memory-mapped reads.
Usage: python read_rate.py <raw file>
"""
import sys, time
import numpy as np

def io():
    d = dict(l.split(": ") for l in open("/proc/self/io").read().split("\n") if l)
    return {k: int(v) for k, v in d.items()}

path = sys.argv[1]
off = 400 * 2**30
i0, t0 = io(), time.perf_counter()
with open(path, "rb", buffering=0) as f:
    f.seek(off); n = 0; buf = bytearray(64 * 2**20)
    while n < 2 * 2**30:
        n += f.readinto(buf)
t1, i1 = time.perf_counter(), io()
print(f"sequential: {n} B in {t1-t0:.2f} s = {n/(t1-t0)/1e6:.0f} MB/s; "
      f"rchar +{i1['rchar']-i0['rchar']}, read_bytes +{i1['read_bytes']-i0['read_bytes']}")
NX, NY, NZ = 10240, 7680, 1536
a = np.memmap(path, dtype="<f8", mode="r", shape=(NZ, NY, NX))
i0, t0 = io(), time.perf_counter()
s = np.array(a[1104:1104 + 64:16, ::16, ::16])  # np.array copies; np.asarray on a memmap reads nothing  # 4 z-levels x 480 y-rows: 1920 rows of 80 KiB
t1, i1 = time.perf_counter(), io()
print(f"memmap stride-16 sample: shape {s.shape}, rows touched {4*480} x 81920 B = {4*480*81920} B; "
      f"{t1-t0:.2f} s; rchar +{i1['rchar']-i0['rchar']}, read_bytes +{i1['read_bytes']-i0['read_bytes']}")
