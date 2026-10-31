#!/bin/bash
# Two-pass baseline, pass 1: copy every 5th timestep's file whole (18 of 88).
#   bash pass1_download.sh [DEST]
set -euo pipefail
HOST=gpu-server
SRC=/mnt/na1/eecs/research/harp/data/ashrestha/vislang/wildfire-sl/mountain_headcurve80
DEST=${1:-./wildfire}
mkdir -p "$DEST"
labels=$(ssh "$HOST" "ls $SRC" | sed -n 's/^fire#\([0-9]*\)\.vts$/\1/p' | sort -n | awk 'NR % 5 == 1')
for n in $labels; do
    rsync -aL --partial "$HOST:$SRC/fire#$n.vts" "$DEST/"
done
