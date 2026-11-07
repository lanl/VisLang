#!/bin/bash
# Test4 Sieve runs from the repo root, over a connected session (./sieve connect gpu-server).
#   bash run_sieve.sh cold|warm
# cold: an empty extent cache (.vislang/cache-test4 removed first) and the series evicted
# from the server's page cache before each request; warm: the same specs again, same cache.
# Each request's record is copied by record_step.py into results/sieve_<mode>/<step>/.
set -eu
MODE=$1
T=bench/PacificVis/cs2-wildfire/Test4
SRV=/mnt/na1/eecs/research/harp/data/ashrestha/vislang
PY=/home.na1/ad.wsu.edu/aayush.shrestha/pvt/miniconda3/envs/vislang/bin/python
SOCK=$(/opt/miniconda3/envs/vislang/bin/python -c "from vislang.remote.download import master_path; print(master_path('gpu-server'))")
export VISLANG_CACHE=$PWD/.vislang/cache-test4
[ "$MODE" = cold ] && rm -rf "$VISLANG_CACHE" "$T/outputs/sieve"
for step in overview box; do
    out=$T/results/sieve_$MODE/$step
    mkdir -p "$out"
    cp "$T/specs/$step.py" "$out/spec_as_run.py"
    [ "$MODE" = cold ] && ssh -o ControlPath="$SOCK" gpu-server \
        "$PY $SRV/cs2_test4/drop_cache.py $SRV/wildfire-sl/mountain_headcurve80" > "$out/page_cache.txt" 2>/dev/null
    before=$(wc -l < .vislang/timings.jsonl)
    ./sieve execute "$T/specs/$step.py" > "$out/report.txt" 2>&1
    /opt/miniconda3/envs/vislang/bin/python "$T/scripts/record_step.py" "$out" "$before" > /dev/null
done
