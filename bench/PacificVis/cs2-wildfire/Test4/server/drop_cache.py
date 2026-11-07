"""Evict the series' files from the server's page cache (no root needed), so the next
measured run reads from storage. Prints the cached bytes before and after.

    python drop_cache.py SERIES_DIR
"""
import glob
import os
import subprocess
import sys

files = sorted(glob.glob(os.path.join(sys.argv[1], "fire#*.vts")))


def cached():
    out = subprocess.run(["fincore", "-b", "-n", "-o", "RES"] + files,
                         capture_output=True, text=True).stdout.split()
    return sum(int(v) for v in out)


before = cached()
for f in files:
    fd = os.open(f, os.O_RDONLY)
    os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    os.close(fd)
print(f"page cache: {before} B before, {cached()} B after, over {len(files)} files")
