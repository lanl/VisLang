#!/bin/bash
# Scripts on the server: cut both requests next to the data, copy the cuts back,
# rebuild the .vts series locally.
set -euo pipefail
HOST=gpu-server
SRC=/mnt/na1/eecs/research/harp/data/ashrestha/vislang/wildfire-sl/mountain_headcurve80
WORK=/tmp/cs2_cuts
ssh "$HOST" "mkdir -p $WORK" && scp cut_series.py "$HOST:$WORK/"
ssh "$HOST" "cd $WORK && python cut_series.py $SRC overview overview"
scp -r "$HOST:$WORK/overview" overview_cut
python assemble.py overview_cut overview
ssh "$HOST" "cd $WORK && python cut_series.py $SRC box box 9000 45000"
scp -r "$HOST:$WORK/box" box_cut
python assemble.py box_cut box
