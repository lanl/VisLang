"""Interpreter refactor verification (INTERPRETER_PLAN.md).

Plain-python asserts (no pytest on the cluster). Run from the repo root:
    python tests/test_interpreter.py
Covers: form cleanup (glob rejection, threshold rename), grid voxel masking,
written-order commutativity (threshold vs subsample), order-gated pushdown,
the read-set, grid crop+stride fusion, and the legacy subset/load path.
"""

import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Determinism: block the LLM schema-binding path so HDF5 inspection uses the
# generic listing (binding may rename variables, e.g. temp -> temperature).
sys.modules["schema_binding"] = None

from vislang.dsl import form_namespace, reset_sinks, collected_sinks
from vislang.dsl.forms import (source, fields, region, subsample, threshold,
                             compress, save, render)
from vislang.interpreter.planner import plan_pipeline
from vislang.formats.inspect import inspect_file
from vislang.interpreter.subset import subset
from vislang.interpreter.load import load, materialize

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(REPO, "csafe_heptane_302x302x302_uint8.raw")
TMP = tempfile.mkdtemp(prefix="vislang_test_")

PASS = []


def check(name, cond, detail=""):
    assert cond, f"{name}: {detail}"
    PASS.append(name)
    print(f"  ok  {name}")


# ---------------------------------------------------------------------------
# Synthetic particle HDF5: 1000 rows, deterministic values.
# ---------------------------------------------------------------------------
def make_particles():
    import h5py
    path = os.path.join(TMP, "particles.h5")
    n = 1000
    with h5py.File(path, "w") as f:
        f["x"] = np.linspace(0.0, 99.9, n)
        f["y"] = np.linspace(0.0, 99.9, n)[::-1].copy()
        f["z"] = np.tile(np.arange(10.0), n // 10)
        f["density"] = np.arange(n, dtype=np.float64)          # row i has density i
        f["temp"] = np.arange(n, dtype=np.float64) % 7         # 0..6 repeating
    return path, n


def run(node):
    """Plan+execute a chain ending in save(); return the saved dict of arrays."""
    out = os.path.join(TMP, f"out_{len(PASS)}.npz")
    reset_sinks()
    plan_pipeline(save(node, out), dry_run=False)
    with np.load(out) as z:
        return {k: z[k] for k in z.files}


def steps_of(node):
    reset_sinks()
    return plan_pipeline(node, dry_run=True)["steps"]


def main():
    print("== form-set cleanup ==")
    ns = form_namespace()
    check("threshold in namespace", "threshold" in ns)
    check("filter removed", "filter" not in ns)
    check("timestep removed", "timestep" not in ns)
    try:
        source("/some/dir/snap_*.h5")
        check("glob rejected", False, "glob was accepted")
    except ValueError:
        check("glob rejected", True)
    import vislang.dsl.nodes as N
    check("no TimestepNode", not hasattr(N, "TimestepNode"))
    check("ThresholdNode kind", N.ThresholdNode(upstream=None, var="v", op=">",
                                                value=0.0).kind == "threshold")

    print("== grid: crop+stride fusion is byte-correct ==")
    full = np.fromfile(RAW, dtype=np.uint8).reshape(302, 302, 302)
    d = source(RAW)
    got = run(subsample(region(d, x=(0, 200), y=(10, 110)), 2))
    (var, arr), = got.items()
    check("grid fuse shape", arr.shape == full[0:200:2, 10:110:2, ::2].shape,
          f"{arr.shape}")
    check("grid fuse values", np.array_equal(arr, full[0:200:2, 10:110:2, ::2]))

    print("== grid: threshold = voxel NaN-mask, shape preserved ==")
    got = run(threshold(subsample(source(RAW), 4), f"{var} > 100"))
    marr = got[var]
    ref = full[::4, ::4, ::4]
    check("grid threshold float cast", np.issubdtype(marr.dtype, np.floating),
          str(marr.dtype))
    check("grid threshold shape preserved", marr.shape == ref.shape)
    keep = ref > 100
    check("grid threshold keeps passers",
          np.array_equal(marr[keep], ref[keep].astype(marr.dtype)))
    check("grid threshold NaNs failers", np.all(np.isnan(marr[~keep])))
    # order vs stride must not matter on grids (shape-preserving mask commutes)
    got2 = run(subsample(threshold(source(RAW), f"{var} > 100"), 4))
    a, b = got2[var], marr
    check("grid threshold/subsample commute",
          np.array_equal(np.nan_to_num(a, nan=-1), np.nan_to_num(b, nan=-1)))

    print("== particles: written order is honored (the old fixed-order bug) ==")
    ppath, n = make_particles()
    # A: threshold first, then every-3rd OF THE SURVIVORS
    got_a = run(subsample(threshold(source(ppath), "density >= 500"), 3))
    # B: every-3rd first, then threshold THE SAMPLE
    got_b = run(threshold(subsample(source(ppath), 3), "density >= 500"))
    dens = np.arange(n, dtype=np.float64)
    ref_a = dens[dens >= 500][::3]
    ref_b = dens[::3][np.arange(n)[::3] >= 500]
    check("A = sample the survivors", np.array_equal(got_a["density"], ref_a),
          f"{got_a['density'][:5]} vs {ref_a[:5]}")
    check("B = threshold the sample", np.array_equal(got_b["density"], ref_b))
    check("A != B (orders differ)",
          not np.array_equal(got_a["density"], got_b["density"]))

    print("== particles: order-gated pushdown (plan annotations) ==")
    s = " | ".join(steps_of(subsample(threshold(source(ppath), "density > 1"), 3)))
    check("demoted subsample marked post-read", "post-read" in s, s)
    s = " | ".join(steps_of(threshold(subsample(source(ppath), 3), "density > 1")))
    check("leading subsample marked pushdown", "(pushdown)" in s, s)

    print("== particles: bbox region is a computed cut, order honored ==")
    got = run(subsample(region(source(ppath), x=(0.0, 50.0)), 2))
    xs = np.linspace(0.0, 99.9, n)
    inbox = xs <= 50.0
    inbox &= xs >= 0.0
    ref = dens[inbox][::2]
    check("bbox then sample", np.array_equal(got["density"], ref))

    print("== read-set: threshold var read for the mask, dropped from output ==")
    got = run(threshold(fields(source(ppath), ["density"]), "temp < 3"))
    check("projected var present", "density" in got)
    check("mask-only var dropped", "temp" not in got, str(list(got)))
    ref = dens[(np.arange(n) % 7) < 3]
    check("mask applied via dropped var", np.array_equal(got["density"], ref))

    print("== static checks fire before any read ==")
    try:
        steps_of(threshold(source(ppath), "nope > 1"))
        check("unknown threshold var raises", False)
    except ValueError as e:
        check("unknown threshold var raises", "nope" in str(e))
    try:
        steps_of(fields(source(ppath), ["ghost"]))
        check("unknown fields var raises", False)
    except ValueError as e:
        check("unknown fields var raises", "ghost" in str(e))
    try:
        steps_of(region(source(RAW), x=(0, 999)))
        check("out-of-bounds region raises", False)
    except ValueError as e:
        check("out-of-bounds region raises", "out of bounds" in str(e))

    print("== multiple thresholds AND together ==")
    got = run(threshold(threshold(source(ppath), "density >= 100"), "density < 200"))
    check("ANDed thresholds", np.array_equal(got["density"], dens[100:200]))

    print("== legacy subset/load path unchanged ==")
    info = inspect_file(RAW)
    narrowed = subset(info, dimensions={"grid": 0.5})
    loaded = load(narrowed)
    (lvar,) = loaded.variables
    check("legacy grid load", loaded.data[lvar].shape == full[::2, ::2, ::2].shape,
          f"{loaded.data[lvar].shape}")

    pinfo = inspect_file(ppath)
    ploaded = load(subset(pinfo, variables=["density"],
                          dimensions={"particles": slice(0, 100)}))
    check("legacy particle load", np.array_equal(ploaded.data["density"], dens[:100]))

    print(f"\nALL {len(PASS)} CHECKS PASSED")


if __name__ == "__main__":
    main()
