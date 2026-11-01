#!/bin/bash
# Two-pass baseline, pass 2: copy the chosen window's files whole (#4000 and
# #9000-#45000), skipping those pass 1 already holds.
#   bash pass2_download.sh [DEST]
set -euo pipefail
HOST=gpu-server
SRC=/mnt/na1/eecs/research/harp/data/ashrestha/vislang/wildfire-sl/mountain_headcurve80
DEST=${1:-./wildfire}
mkdir -p "$DEST"
for n in 4000 $(seq 9000 1000 45000); do
    [ -f "$DEST/fire#$n.vts" ] && continue
    rsync -aL --partial "$HOST:$SRC/fire#$n.vts" "$DEST/"
done
