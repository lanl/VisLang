"""AST <-> JSON wire layer verification (REMOTE_COMPUTE_PLAN.md Phase 2).

Run from the repo root: python tests/test_ast_serialize.py
Covers: full-chain round-trips through real JSON text, strict rejection of
malformed/hostile plans, tuple restoration on frozen nodes, and sink
registration on rebuild.
"""

import dataclasses
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dsl_forms import reset_sinks, collected_sinks
from dsl_forms.forms import (source, fields, region, subsample, threshold,
                             compress, save, render, timesteps)
from dsl_forms.nodes import upstream_of
from ast_serialize import (to_plan, to_plan_json, from_plan, from_plan_json,
                           PlanValidationError, PLAN_VERSION)

PASS = []


def check(name, cond, detail=""):
    assert cond, f"{name}: {detail}"
    PASS.append(name)
    print(f"  ok  {name}")


def _norm(v):
    """Sequences compare shape-blind: the wire rebuild re-tuples lists (frozen
    nodes hold immutables), which is documented normalization, not drift."""
    if isinstance(v, (list, tuple)):
        return tuple(_norm(x) for x in v)
    return v


def chains_equal(a, b):
    """Node-by-node structural equality, walking source-ward."""
    while a is not None or b is not None:
        if type(a) is not type(b):
            return False
        for f in dataclasses.fields(a):
            if f.name == "upstream":
                continue
            if _norm(getattr(a, f.name)) != _norm(getattr(b, f.name)):
                return False
        a, b = upstream_of(a), upstream_of(b)
    return True


def rejects(name, plan):
    try:
        (from_plan if isinstance(plan, dict) else from_plan_json)(plan)
        check(name, False, "was accepted")
    except PlanValidationError:
        check(name, True)


def main():
    reset_sinks()

    print("== round-trips ==")
    full = render(compress(threshold(subsample(region(fields(
        source("/data/run.h5", positions=("px", "py", "pz")),
        ["density", "temp"]), x=(0, 50), y=(None, 10.5)), 4),
        "density > 0.25"), ["density"], 1e-3, mode="sperr"), cmap="green",
        opacity=[0.0, 0.1, 1.0, 0.9])
    reset_sinks()
    rebuilt = from_plan_json(to_plan_json(full))
    check("full chain round-trip", chains_equal(full, rebuilt))
    check("rebuild registers the sink", len(collected_sinks()) == 1)

    reset_sinks()
    bare = source("/data/one.raw")
    check("bare source round-trip", chains_equal(bare, from_plan(to_plan(bare))))

    prefix = threshold(subsample(source("/d/f.h5"), 0.1), "rho >= 5")
    rb = from_plan_json(to_plan_json(prefix))
    check("no-sink prefix round-trip", chains_equal(prefix, rb))
    check("prefix registers no sink", len(collected_sinks()) == 0)
    check("threshold value is float", isinstance(rb.value, float) and rb.value == 5.0)
    check("fields/positions re-tupled",
          isinstance(from_plan(to_plan(fields(source("/x", positions=["a", "b", "c"]),
                                              ["v"]))).keep, tuple))

    # timesteps: the folder (timeseries) selector — a folder-reduce plan carries it
    reset_sinks()
    ts = subsample(fields(timesteps(source("/data/series"), 0, 19),
                          ["density"]), 2)
    check("timesteps folder chain round-trip",
          chains_equal(ts, from_plan_json(to_plan_json(ts))))
    tsr = from_plan(to_plan(timesteps(source("/data/series"), 3, 7)))
    check("timesteps start/stop preserved", tsr.start == 3 and tsr.stop == 7)

    sv = from_plan(to_plan(save(subsample(source("/d/f.h5"), 2), "/tmp/o.npz")))
    reset_sinks()
    check("save round-trip", chains_equal(save(subsample(source("/d/f.h5"), 2),
                                               "/tmp/o.npz"), sv))
    reset_sinks()

    print("== strict rejections ==")
    ok = to_plan(threshold(source("/d/f.h5"), "a > 1"))

    rejects("not a dict", [1, 2])
    rejects("garbage json", "{nope")
    rejects("missing version", {"chain": ok["chain"]})
    rejects("wrong version", {"vislang_plan": 99, "chain": ok["chain"]})
    rejects("bool version", {"vislang_plan": True, "chain": ok["chain"]})
    rejects("extra top-level key",
            {"vislang_plan": PLAN_VERSION, "chain": ok["chain"], "x": 1})
    rejects("empty chain", {"vislang_plan": PLAN_VERSION, "chain": []})
    rejects("head not source",
            {"vislang_plan": PLAN_VERSION,
             "chain": [{"kind": "fields", "keep": ["a"]}]})
    rejects("source mid-chain",
            {"vislang_plan": PLAN_VERSION,
             "chain": ok["chain"] + [{"kind": "source", "uri": "/x",
                                      "positions": None}]})
    rejects("unknown kind",
            {"vislang_plan": PLAN_VERSION,
             "chain": [{"kind": "exec", "cmd": "rm -rf /"}]})

    src_step = {"kind": "source", "uri": "/d/f.h5", "positions": None}
    rejects("extra step key",
            {"vislang_plan": PLAN_VERSION,
             "chain": [dict(src_step, sneaky=1)]})
    rejects("missing step key",
            {"vislang_plan": PLAN_VERSION, "chain": [{"kind": "source",
                                                      "uri": "/d/f.h5"}]})
    rejects("glob uri",
            {"vislang_plan": PLAN_VERSION,
             "chain": [dict(src_step, uri="/d/*.h5")]})
    rejects("bool where number expected",
            {"vislang_plan": PLAN_VERSION,
             "chain": [src_step, {"kind": "threshold", "var": "a", "op": ">",
                                  "value": True}]})
    rejects("bad threshold op",
            {"vislang_plan": PLAN_VERSION,
             "chain": [src_step, {"kind": "threshold", "var": "a",
                                  "op": "=~", "value": 1}]})
    rejects("region lo >= hi",
            {"vislang_plan": PLAN_VERSION,
             "chain": [src_step, {"kind": "region", "ranges": [["x", 5, 5]]}]})
    rejects("subsample uniform and per_axis",
            {"vislang_plan": PLAN_VERSION,
             "chain": [src_step, {"kind": "subsample", "uniform": 2,
                                  "per_axis": [["x", 2]]}]})
    rejects("subsample zero stride",
            {"vislang_plan": PLAN_VERSION,
             "chain": [src_step, {"kind": "subsample", "uniform": 0,
                                  "per_axis": []}]})
    rejects("subsample fraction > 1",
            {"vislang_plan": PLAN_VERSION,
             "chain": [src_step, {"kind": "subsample", "uniform": 1.5,
                                  "per_axis": []}]})
    rejects("opacity of strings",
            {"vislang_plan": PLAN_VERSION,
             "chain": [src_step, {"kind": "render", "cmap": None,
                                  "opacity": ["a"]}]})
    rejects("positions wrong arity",
            {"vislang_plan": PLAN_VERSION,
             "chain": [dict(src_step, positions=["x", "y"])]})
    rejects("timesteps bool start",
            {"vislang_plan": PLAN_VERSION,
             "chain": [src_step, {"kind": "timesteps", "start": True, "stop": 3}]})
    rejects("timesteps float label",
            {"vislang_plan": PLAN_VERSION,
             "chain": [src_step, {"kind": "timesteps", "start": 1.5, "stop": 3}]})
    rejects("timesteps start > stop",
            {"vislang_plan": PLAN_VERSION,
             "chain": [src_step, {"kind": "timesteps", "start": 5, "stop": 2}]})

    check("rejected plans register no sinks", len(collected_sinks()) == 0)

    print(f"\nALL {len(PASS)} CHECKS PASSED")


if __name__ == "__main__":
    main()
