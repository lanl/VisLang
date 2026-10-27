"""Replaying a recorded session (bench/replay_session.py).

Plain-python asserts (no pytest on the cluster). Run from the repo root:
    python tests/test_replay.py

Records a small session the way the ParaView plugin does — the plugin's own
engine (sieve_pv_core) running `sieve api` on a simulated remote: an overview,
a box, and a smaller box inside it — then replays it under every setup. Checks
what each setup is for: cold pays for each step but cuts the inner box from
the cache; warm moves nothing; no-catalog moves every step; full-download
copies the file and narrows it here. Every replayed output reproduces the
session's own data bit for bit.
"""

import json
import os
import sys
import tempfile

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "plugins", "paraview"))
sys.path.insert(0, os.path.join(REPO, "bench"))

import h5py

import sieve_pv_core as core
import replay_session

TMP = tempfile.mkdtemp(prefix="vislang_replay_test_")
FAKE = os.path.join(REPO, "tests", "paraview", "fake_sieve")
PASS = []


def check(name, cond, detail=""):
    assert cond, f"{name}: {detail}"
    PASS.append(name)
    print(f"  ok  {name}")


def record_session():
    data = os.path.join(TMP, "data")
    os.makedirs(data)
    src = os.path.join(data, "plain.h5")
    with h5py.File(src, "w") as f:
        f["rho"] = np.arange(32 ** 3, dtype=np.float32).reshape(32, 32, 32)
    os.environ["VISLANG_PYTHON"] = sys.executable
    os.environ["VISLANG_REMOTE_TMP"] = os.path.join(TMP, "remote_tmp")
    os.makedirs(os.environ["VISLANG_REMOTE_TMP"])
    runner = core.Runner(sieve_cmd=FAKE, base_env={
        "VISLANG_CACHE": os.path.join(TMP, "session_cache"),
        "VISLANG_TIMINGS_FILE": os.path.join(TMP, "session_timings.jsonl")})
    sess = core.Session(root=os.path.join(TMP, "sessions"))
    uri = f"u@fakehost:{src}"

    def ask(op, request, action, name):
        step, workdir = sess.next_dir(f"{name}-{action}")
        if op == "run":
            request = {**request, "workdir": workdir,
                       "client": {"name": "paraview", "session": sess.id, "step": step}}
        job = runner.wait(runner.submit(name, core.Job(op, request, workdir)), timeout=300)
        job.action = action
        sess.trace(core.trace_record(name, action, job))
        return job.result

    desc = ask("inspect", {"uri": uri}, "inspect", "source")
    check("the session's source is described", desc["status"] == "ok", desc)
    for action, params in (("overview", {"stride": 4}),
                           ("detail", {"box": {"lo": [8, 8, 8], "hi": [23, 23, 23]}}),
                           ("detail", {"box": {"lo": [10, 10, 10], "hi": [19, 19, 19]}})):
        r = ask("run", {"descriptor": desc, "params": params}, action, "view")
        check(f"recorded: {action} {params}", r["status"] == "ok", r.get("error"))
    return sess.root, os.path.getsize(src)


def main():
    session, size = record_session()
    out = os.path.join(TMP, "replay")
    replay_session.main([session, "--out", out, "--sieve", FAKE])
    rows = json.load(open(os.path.join(out, "replay.json")))
    by = {(r["setup"], r["step"]): r for r in rows}
    check("every setup replays every step",
          len(rows) == 12 and all(r["status"] == "ok" for r in rows),
          [(r["setup"], r["step"], r["status"], r["error"]) for r in rows])
    check("cold: each step reduces next to the data, but the inner box is a cache hit",
          [by[("cold", s)]["route"] for s in (1, 2, 3)]
          == ["remote_reduce", "remote_reduce", "catalog_full_hit"]
          and by[("cold", 3)]["wire_bytes"] == 0,
          [(by[("cold", s)]["route"], by[("cold", s)]["wire_bytes"]) for s in (1, 2, 3)])
    check("warm: a revisit moves nothing",
          all(by[("warm", s)]["route"] == "catalog_full_hit"
              and by[("warm", s)]["wire_bytes"] == 0 for s in (1, 2, 3)))
    check("no-catalog: every step moves its data",
          all(by[("no-catalog", s)]["route"] == "remote_reduce"
              and by[("no-catalog", s)]["wire_bytes"] > 0 for s in (1, 2, 3)))
    check("full-download: the file is copied whole and narrowed here",
          all(by[("full-download", s)]["route"] == "whole_file_fetch" for s in (1, 2, 3))
          and by[("full-download", 1)]["wire_bytes"] >= size,
          [by[("full-download", s)]["wire_bytes"] for s in (1, 2, 3)])
    check("the reduce moves far less than the copy",
          by[("cold", 2)]["wire_bytes"] * 5 < by[("full-download", 2)]["wire_bytes"])
    check("every replayed output reproduces the session's data bit for bit",
          all(r["reproduced"] is True for r in rows),
          [(r["setup"], r["step"], r["reproduced"]) for r in rows])
    summary = open(os.path.join(out, "summary.txt")).read()
    check("the summary names every setup and the cumulative bytes",
          all(s in summary for s in replay_session.SETUPS)
          and "cumulative" in summary, summary)


if __name__ == "__main__":
    main()
    print(f"\nall {len(PASS)} checks passed")
