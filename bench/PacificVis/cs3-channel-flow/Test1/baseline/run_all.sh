#!/bin/bash
# Run both baselines on the server, one after the other, each from a cold page cache for the file.
# Launched detached (setsid nohup) from results/baseline; not counted in the baseline's lines.
set -u
D=/mnt/na1/eecs/research/harp/data/ashrestha/vislang/cs3_baseline
PY=/home.na1/ad.wsu.edu/aayush.shrestha/pvt/miniconda3/envs/vislang/bin/python
F=/mnt/na1/eecs/research/harp/data/ashrestha/vislang/TurbulentChannelFLow/u_10240x7680x1536_float64.raw
drop() {  # evict this file's clean pages from the page cache (no root needed), then show residency
  date; uptime; fincore "$F"
  $PY -c "import os,sys; fd=os.open(sys.argv[1], os.O_RDONLY); os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)" "$F"
  fincore "$F"
}
drop > $D/cache_before_stride.txt 2>&1
$PY $D/run_measured.py $D/stride.py "$F" $D/stride16.npy > $D/stride.json 2> $D/stride.err
drop > $D/cache_before_blockmin.txt 2>&1
$PY $D/run_measured.py $D/blockmin.py "$F" $D/blockmin16.npy > $D/blockmin.json 2> $D/blockmin.err
date > $D/done.txt
