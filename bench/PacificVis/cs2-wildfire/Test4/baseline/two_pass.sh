#!/bin/bash
# Two-pass download: copy every 5th file whole and cut the overview from them; after
# choosing the window, copy its files not already held and cut the box.
#   bash two_pass.sh 1    then, after looking at the overview,    bash two_pass.sh 2
set -euo pipefail
HOST=gpu-server
SRC=/mnt/na1/eecs/research/harp/data/ashrestha/vislang/wildfire-sl/mountain_headcurve80
mkdir -p whole
if [ "$1" = 1 ]; then
    labels=$(ssh "$HOST" "ls $SRC" | sed -n 's/^fire#\([0-9]*\)\.vts$/\1/p' | sort -n | awk 'NR % 5 == 1')
    for n in $labels; do rsync -aL --partial "$HOST:$SRC/fire#$n.vts" whole/; done
    python cut_series.py whole overview overview --vts
else
    for n in $(seq 9000 1000 45000); do
        [ -f "whole/fire#$n.vts" ] || rsync -aL --partial "$HOST:$SRC/fire#$n.vts" whole/
    done
    python cut_series.py whole box box 9000 45000 --vts
fi
