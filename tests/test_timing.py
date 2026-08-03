"""Timing harness verification (vislang_timing.py).

Run from the repo root: python tests/test_timing.py
Covers: one JSONL line per run, nested phase paths, counters, estimate
flattening, detached (no-run) safety, the VISLANG_TIMING=0 kill switch, and the
load-bearing promise that measurement never breaks a run — plus an end-to-end
local run through the real planner, so the wiring is checked, not just the module.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = []


def check(name, cond, detail=""):
    assert cond, f"{name}: {detail}"
    PASS.append(name)
    print(f"  ok  {name}")


def _records(path):
    with open(path) as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def test_run_record(tmp):
    """One run -> exactly one JSONL object, carrying identity + phases."""
    path = os.path.join(tmp, "t1.jsonl")
    os.environ["VISLANG_TIMINGS_FILE"] = path
    import vislang_timing as timing

    with timing.run("spec.py", spec_code="save(source('x'),'y')") as rec:
        with timing.pipeline(kind="save", uri="x"):
            with timing.phase("inspect"):
                pass
            with timing.phase("remote_reduce"):
                with timing.phase("pull") as p:
                    p["bytes"] = 4096
            timing.note(site="remote", source_bytes=1000)
            timing.count("ssh_exec", 3)
            timing.count("wire_bytes", 4096)
        rec["status"] = "OK"

    recs = _records(path)
    check("one line per run", len(recs) == 1, f"got {len(recs)}")
    r = recs[0]
    check("run identity", bool(r["run_id"]) and r["spec"] == "spec.py"
          and len(r["spec_sha"]) == 12, r)
    check("total_s recorded", isinstance(r["total_s"], float))
    check("status kept", r["status"] == "OK", r["status"])
    names = [p["name"] for p in r["phases"]]
    check("nested phase path", "remote_reduce/pull" in names, names)
    check("phase depth", [p["depth"] for p in r["phases"] if p["name"] == "pull"] == []
          and any(p["depth"] == 1 for p in r["phases"]), r["phases"])
    pull = [p for p in r["phases"] if p["name"] == "remote_reduce/pull"][0]
    check("phase bytes", pull["bytes"] == 4096)
    check("phase seconds", isinstance(pull["s"], float))
    check("counters summed", r["counters"]["ssh_exec"] == 3
          and r["counters"]["wire_bytes"] == 4096, r["counters"])
    check("pipeline note", r["pipelines"][0]["site"] == "remote"
          and r["pipelines"][0]["source_bytes"] == 1000, r["pipelines"])
    check("phase tagged with pipeline", all(p["pipeline"] == 0 for p in r["phases"]))


def test_estimate_flattened(tmp):
    """A CostEstimate lands as flat columns — the predicted half of the table."""
    path = os.path.join(tmp, "t2.jsonl")
    os.environ["VISLANG_TIMINGS_FILE"] = path
    import vislang_timing as timing
    from my_estimate import CostEstimate

    est = CostEstimate(site="remote", read_mb=128.0, output_mb=128.0,
                       time_lo_s=204.0, time_hi_s=828.0, confidence="measured",
                       over_budget=True, budget_reason="time")
    with timing.run("spec.py"):
        with timing.pipeline(kind="save"):
            timing.note_estimate(est)
    p = _records(path)[0]["pipelines"][0]
    check("estimate flattened", (p["est_read_mb"] == 128.0
                                 and p["est_time_lo_s"] == 204.0
                                 and p["est_time_hi_s"] == 828.0
                                 and p["est_confidence"] == "measured"
                                 and p["est_over_budget"] is True), p)
    check("note_estimate(None) is safe", timing.note_estimate(None) is None)


def test_error_recorded_and_reraised(tmp):
    """A failing run is still recorded (the mistake-cost row) and the original
    exception still reaches the caller unchanged."""
    path = os.path.join(tmp, "t3.jsonl")
    os.environ["VISLANG_TIMINGS_FILE"] = path
    import vislang_timing as timing

    raised = None
    try:
        with timing.run("bad.py"):
            with timing.pipeline(kind="render"):
                with timing.phase("inspect"):
                    raise ValueError("no such variable")
    except ValueError as e:
        raised = e
    check("original exception propagates", isinstance(raised, ValueError)
          and str(raised) == "no such variable", raised)
    r = _records(path)[0]
    check("run marked error", r["status"] == "error" and "ValueError" in r["error"], r)
    check("failing phase recorded", r["phases"][0]["error"].startswith("ValueError"),
          r["phases"])
    check("seconds-to-error present", isinstance(r["total_s"], float))
    check("zero bytes moved", "wire_bytes" not in r["counters"], r["counters"])


def test_detached_and_disabled(tmp):
    """No run open -> nothing recorded, no crash. VISLANG_TIMING=0 -> no file."""
    path = os.path.join(tmp, "t4.jsonl")
    os.environ["VISLANG_TIMINGS_FILE"] = path
    import vislang_timing as timing

    with timing.phase("orphan") as p:            # the remote executor's situation
        p["bytes"] = 1
    timing.count("ssh_exec")
    timing.note(site="nowhere")
    check("detached calls write nothing", not os.path.exists(path))

    os.environ["VISLANG_TIMING"] = "0"
    try:
        with timing.run("spec.py"):
            with timing.pipeline(kind="save"):
                with timing.phase("inspect"):
                    pass
                timing.count("ssh_exec")
        check("kill switch writes nothing", not os.path.exists(path))
    finally:
        del os.environ["VISLANG_TIMING"]


def test_flush_failure_is_survivable(tmp):
    """An unwritable timings file must not break the run it is measuring."""
    blocker = os.path.join(tmp, "blocker")       # a FILE where a dir must go
    with open(blocker, "w") as f:
        f.write("")
    os.environ["VISLANG_TIMINGS_FILE"] = os.path.join(blocker, "sub", "x.jsonl")
    import vislang_timing as timing
    with timing.run("spec.py"):
        with timing.phase("inspect"):
            pass
    check("unwritable target is survived", True)


def test_end_to_end_local(tmp):
    """A real local run through the real planner: phases and byte counts come out
    of the instrumented code path, not the test."""
    path = os.path.join(tmp, "t5.jsonl")
    os.environ["VISLANG_TIMINGS_FILE"] = path
    import h5py
    import numpy as np
    import vislang_timing as timing
    from dsl_forms import reset_sinks, collected_sinks
    from dsl_forms.forms import source, fields, subsample, save
    from planner import plan_pipeline

    src = os.path.join(tmp, "cube.h5")                # HDF5: a real grid + pushdown
    with h5py.File(src, "w") as f:
        f["temperature"] = np.arange(64 ** 3, dtype=np.float32).reshape(64, 64, 64)
        f["density"] = np.ones((64, 64, 64), dtype=np.float32)
    out = os.path.join(tmp, "out.h5")

    reset_sinks()
    save(subsample(fields(source(src), ["temperature"]), 2), out)
    with timing.run("spec.py", spec_code="save(...)"):
        plan_pipeline(collected_sinks()[0])

    r = _records(path)[0]
    names = [p["name"] for p in r["phases"]]
    for want in ("inspect", "lower", "materialize", "sink_save"):
        check(f"phase {want} timed", want in names, names)
    mat = [p for p in r["phases"] if p["name"] == "materialize"][0]
    check("materialized bytes measured",              # 32^3 float32 = 131072
          mat["bytes"] == 32 ** 3 * 4, mat)
    pipe = r["pipelines"][0]
    check("site local", pipe["site"] == "local" and pipe["folder"] is False, pipe)
    check("materialized flag", pipe["materialized"] is True, pipe)
    check("local estimate recorded", pipe["est_read_mb"] is not None, pipe)
    check("no wire bytes locally", "wire_bytes" not in r["counters"], r["counters"])


if __name__ == "__main__":
    saved = {k: os.environ.get(k) for k in ("VISLANG_TIMINGS_FILE", "VISLANG_TIMING")}
    os.environ["VISLANG_TIMING"] = "1"     # this suite owns the switch it tests
    with tempfile.TemporaryDirectory() as tmp:
        try:
            for fn in (test_run_record, test_estimate_flattened,
                       test_error_recorded_and_reraised, test_detached_and_disabled,
                       test_flush_failure_is_survivable, test_end_to_end_local):
                print(f"\n{fn.__name__}:")
                fn(tmp)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    print(f"\n{len(PASS)} checks passed.")
