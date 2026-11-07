#!/bin/bash
# Download everything: copy the whole series, then cut the overview and the box from it.
set -euo pipefail
HOST=gpu-server
SRC=/mnt/na1/eecs/research/harp/data/ashrestha/vislang/wildfire-sl/mountain_headcurve80
rsync -aL --partial "$HOST:$SRC/" whole/
python cut_series.py whole overview overview --vts
python cut_series.py whole box box 9000 45000 --vts
