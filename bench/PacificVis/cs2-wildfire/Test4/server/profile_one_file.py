#!/usr/bin/env python3
"""Why Sieve's server time exceeds the script's for the same bytes read: one file,
already in the page cache (so the disk is out of it), cut both ways, profiled.

    python profile_one_file.py SERIES_DIR LABEL REPO KIND(overview|box) > profile.txt

Script path: cut_series.read + the cut (as the baseline does, without compression).
Sieve path: the reducer's own per-folder delta reduce (executor._run_folder_delta) on a
one-timestep manifest, as a remote reduce runs it, including its bundle compression.
Each is run once to warm, then timed 3 times; then each is profiled once.
"""
import cProfile
import io
import json
import os
import pstats
import sys
import tempfile
import time

src, label, repo, kind = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
sys.path.insert(0, repo)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import cut_series as cs
from vislang.dsl.forms import source, fields, subsample, region
from vislang.dsl.ast_serialize import to_plan_json
from vislang.dsl import reset_sinks
from vislang.formats.dataset_info import GEOMETRY_KEY
from vislang.remote import executor

path = os.path.join(src, f"fire#{label}.vts")
names = list(cs.FIELDS[kind])


def script_cut():
    P, A = cs.read(path, names)
    cut = (slice(None, None, 8),) * 3 if kind == "overview" else cs.box_slices(P)
    P, A = P[cut], {n: a[cut] for n, a in A.items()}
    return P, A


def script_cut_and_compress(out):
    P, A = script_cut()
    np.savez_compressed(os.path.join(out, "positions.npz"), P=P)
    np.savez_compressed(os.path.join(out, "fields.npz"), **A)


reset_sinks()
node = source(src)
node = subsample(node, 8) if kind == "overview" else region(
    node, x=(-100, 200), y=(-120, 120), z=(0, 350), coords="world")
plan = to_plan_json(node)
tmp = tempfile.mkdtemp(prefix="sieve_prof_")
manifest = os.path.join(tmp, "manifest.json")
json.dump({str(label): names + [GEOMETRY_KEY]}, open(manifest, "w"))


def sieve_cut():
    from vislang.formats import adapters
    adapters._VTK_LAST.clear()            # each run reads the file, as each timestep does
    out = tempfile.mkdtemp(dir=tmp)
    so = sys.stdout
    sys.stdout = io.StringIO()            # the reducer prints its meta JSON
    try:
        rc = executor._run_folder_delta(plan, manifest, out, dedupe_geometry=True)
    finally:
        sys.stdout = so
    assert rc == 0, rc


def timed(f, *a):
    f(*a)                                  # warm (page cache, imports)
    ts = []
    for _ in range(3):
        t = time.perf_counter(); f(*a); ts.append(time.perf_counter() - t)
    return ts


sout = tempfile.mkdtemp(dir=tmp)
res = {"file": path, "kind": kind,
       "script_read_cut_s": timed(script_cut),
       "script_read_cut_compress_s": timed(script_cut_and_compress, sout),
       "sieve_reduce_s": timed(sieve_cut)}
print(json.dumps(res, indent=1))
for name, f, a in (("SCRIPT read+cut+compress", script_cut_and_compress, (sout,)),
                   ("SIEVE reduce (one timestep)", sieve_cut, ())):
    pr = cProfile.Profile(); pr.enable(); f(*a); pr.disable()
    s = io.StringIO(); pstats.Stats(pr, stream=s).sort_stats("tottime").print_stats(25)
    print(f"\n===== {name}: top functions by own time =====\n{s.getvalue()}")
