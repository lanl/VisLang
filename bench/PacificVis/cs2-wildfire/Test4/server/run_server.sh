#!/bin/bash
# Test4 server runs, one after the other, each from an evicted page cache:
#   1. the evaluation pass (full resolution, every timestep)
#   2. the script route's overview cut     3. the script route's box cut
# Launched detached from $W (setsid nohup bash run_server.sh &); not counted in any baseline.
set -u
W=/mnt/na1/eecs/research/harp/data/ashrestha/vislang/cs2_test4
PY=/home.na1/ad.wsu.edu/aayush.shrestha/pvt/miniconda3/envs/vislang/bin/python
S=/mnt/na1/eecs/research/harp/data/ashrestha/vislang/wildfire-sl/mountain_headcurve80
cd $W
stat -L -c '%s %n' $S/fire#*.vts > stat_series.txt
date > started.txt
$PY drop_cache.py $S > cache_before_eval.txt 2>&1
$PY run_measured.py eval_fullres.py $S eval > eval.json 2> eval.err
$PY drop_cache.py $S > cache_before_cut_overview.txt 2>&1
$PY run_measured.py cut_series.py $S cuts/overview overview > cut_overview.json 2> cut_overview.err
$PY drop_cache.py $S > cache_before_cut_box.txt 2>&1
$PY run_measured.py cut_series.py $S cuts/box box 9000 45000 > cut_box.json 2> cut_box.err
du -sb cuts/overview cuts/box > cut_sizes.txt
date > done.txt
