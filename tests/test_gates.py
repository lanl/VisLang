"""The gates every remote route passes before anything moves, and what lifts
each one (vislang/interpreter/approval.py).

Plain-python asserts (no pytest on the cluster). Run from the repo root:
    python tests/test_gates.py

Covers, against the simulated remote (tests/fakes_remote.py):
  * a single file: `confirm` approves the priced reduce only — with no Slurm
    allocation the run is held, and the whole-file fetch it would fall back to
    is priced and held on its own unless allow_fetch approves it;
  * a folder: the batched reduce is priced on the (timestep, variable) pairs it
    would fetch and held over budget; srun auto mode with no allocation holds;
    the fallback copies only the selected timesteps, gated on their size;
  * the run report names the fallback hold and how to lift it.
"""

import os
import sys
import tempfile

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import h5py

from fakes_remote import EXECS, FETCHES, install_fake_remote, install_login_inspect
from vislang.dsl import reset_sinks
from vislang.dsl.forms import source, region, fields, save, timesteps
from vislang.interpreter.approval import Approval
from vislang.interpreter.planner import plan_pipeline

TMP = tempfile.mkdtemp(prefix="vislang_gates_test_")
PASS = []
N = 32


def check(name, cond, detail=""):
    assert cond, f"{name}: {detail}"
    PASS.append(name)
    print(f"  ok  {name}")


def write_grid(path, t=0):
    with h5py.File(path, "w") as f:
        f["rho"] = (np.arange(N ** 3, dtype=np.float32) + 1000 * t).reshape(N, N, N)
    return path


def run(sink, approval=None):
    reset_sinks()
    return plan_pipeline(sink, confirm=approval or Approval())


class Env:
    """Set env vars for a block, restoring them after."""
    def __init__(self, **kv):
        self.kv, self.old = kv, {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.old[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = str(v)

    def __exit__(self, *exc):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def no_allocation():
    """srun auto mode on a cluster where the user holds no allocation."""
    from vislang.remote import reduce
    reduce.allocation_status = lambda conn: {"present": False,
                                             "reason": "no RUNNING allocation"}

    def none_held(conn, steps):
        raise reduce.RemoteUnavailable("VISLANG_SRUN_JOBID=auto but no RUNNING "
                                       "allocation; run `salloc --no-shell ...` first")
    reduce._discover_jobid = none_held


def test_single_file():
    print("== single file: confirm approves the priced plan, nothing else ==")
    f = write_grid(os.path.join(TMP, "one.hdf5"))
    size = os.path.getsize(f)
    uri = f"u@fakehost:{f}"
    small_box = lambda out: save(region(fields(source(uri), ["rho"]),
                                        x=(0, 8), y=(0, 8), z=(0, 8)),
                                 os.path.join(TMP, out))
    # Budget: the 8^3 box (2 KiB) fits, the whole file does not.
    budget = size // 2
    with Env(VISLANG_SRUN_JOBID="auto", VISLANG_BUDGET_BYTES=budget,
             VISLANG_REMOTE="auto"):
        n0, f0 = len(EXECS), len(FETCHES)
        res = run(small_box("a.npz"), Approval(budget=True))
        check("no allocation holds even with confirm",
              res.get("needs_allocation") and not res["materialized"]
              and len(EXECS) == n0 and len(FETCHES) == f0, res["steps"][-3:])
        check("the hold proposes salloc and names the other way out",
              "salloc" in res["salloc_cmd"]
              and any("without_allocation=True" in s for s in res["steps"]))

        res = run(small_box("b.npz"), Approval(budget=True, without_allocation=True))
        check("confirm does not carry over to the fallback fetch",
              res.get("needs_fetch_approval") and res.get("needs_confirm")
              and not res["materialized"] and len(FETCHES) == f0, res["steps"][-3:])
        check("the fallback hold says how to lift it",
              any("allow_fetch=True" in s for s in res["steps"]))

        res = run(small_box("c.npz"), Approval(without_allocation=True, fetch=True))
        check("allow_fetch approves the fallback fetch",
              res["materialized"] and FETCHES[f0:] == [f], FETCHES[f0:])
        with np.load(res["output"]) as z:
            check("fallback result is the same cut",
                  np.array_equal(z["rho"], np.arange(N ** 3, dtype=np.float32)
                                 .reshape(N, N, N)[:8, :8, :8]))


def make_series(name, labels=(3, 4, 5)):
    d = os.path.join(TMP, name)
    os.makedirs(d)
    for t in labels:
        write_grid(os.path.join(d, f"snap#{t}.hdf5"), t)
    return d


def test_folder():
    print("== folder: the batched reduce is priced, gated, and falls back narrowly ==")
    d = make_series("series")
    uri = f"u@fakehost:{d}"
    box = lambda node: region(fields(node, ["rho"]), x=(0, 16), y=(0, 16), z=(0, 16))
    one_pair = 16 ** 3 * 4                              # one timestep, one variable

    with Env(VISLANG_CACHE=os.path.join(TMP, "cache1"), VISLANG_SRUN_JOBID=None,
             VISLANG_REMOTE="auto", VISLANG_BUDGET_BYTES=2 * one_pair):
        n0 = len(EXECS)
        res = run(save(box(source(uri)), os.path.join(TMP, "f1")))
        check("three timesteps over a two-pair budget are held",
              res.get("needs_confirm") and not res["materialized"]
              and len(EXECS) == n0, res["steps"][-4:])
        est = res["estimate"]
        check("priced on the pairs it would fetch",
              abs(est.read_mb * 1024 ** 2 - 3 * one_pair) < 1, est.read_mb)

        res = run(save(box(timesteps(source(uri), 3, 4)), os.path.join(TMP, "f2")))
        check("two timesteps fit and run as one job",
              res["materialized"] and len(EXECS) == n0 + 1, res["steps"][-3:])
        res = run(save(box(source(uri)), os.path.join(TMP, "f3")))
        check("with two cached, only the third is priced: under budget, runs",
              res["materialized"] and len(EXECS) == n0 + 2
              and abs(res["estimate"].read_mb * 1024 ** 2 - one_pair) < 1,
              res.get("estimate"))
        res = run(save(box(source(uri)), os.path.join(TMP, "f4")))
        check("a full catalog hit ships nothing and is not gated",
              res["materialized"] and len(EXECS) == n0 + 2)

    with Env(VISLANG_CACHE=os.path.join(TMP, "cache2"), VISLANG_SRUN_JOBID="auto",
             VISLANG_REMOTE="auto", VISLANG_BUDGET_BYTES=10 ** 9):
        n0, f0 = len(EXECS), len(FETCHES)
        res = run(save(box(timesteps(source(uri), 4, 4)), os.path.join(TMP, "g1")),
                  Approval(budget=True))
        check("folder: no allocation holds, confirm or not",
              res.get("needs_allocation") and len(EXECS) == n0 and len(FETCHES) == f0,
              res["steps"][-3:])
        res = run(save(box(timesteps(source(uri), 4, 4)), os.path.join(TMP, "g2")),
                  Approval(without_allocation=True))
        check("folder fallback copies only the selected timestep",
              res["materialized"]
              and FETCHES[f0:] == [os.path.join(d, "snap#4.hdf5")], FETCHES[f0:])
        with h5py.File(os.path.join(TMP, "g2", "timestep#4.hdf5"), "r") as z:
            want = (np.arange(N ** 3, dtype=np.float32) + 4000).reshape(N, N, N)
            check("folder fallback result is the same cut",
                  np.array_equal(z["rho"][()], want[:16, :16, :16]))

    with Env(VISLANG_CACHE=os.path.join(TMP, "cache3"), VISLANG_SRUN_JOBID="auto",
             VISLANG_REMOTE="auto",
             VISLANG_BUDGET_BYTES=os.path.getsize(os.path.join(d, "snap#3.hdf5")) // 2):
        f0 = len(FETCHES)
        res = run(save(box(timesteps(source(uri), 3, 3)), os.path.join(TMP, "h1")),
                  Approval(budget=True, without_allocation=True))
        check("folder fallback over budget is held; confirm does not cover it",
              res.get("needs_fetch_approval") and len(FETCHES) == f0,
              res["steps"][-3:])

    # timesteps() by a list of labels: exactly those files, remote and local.
    with Env(VISLANG_CACHE=os.path.join(TMP, "cache4"), VISLANG_SRUN_JOBID=None,
             VISLANG_REMOTE="auto", VISLANG_BUDGET_BYTES=10 ** 9):
        res = run(save(box(timesteps(source(uri), [3, 5])), os.path.join(TMP, "k1")))
        check("a list of timesteps reduces exactly those, remotely",
              res["materialized"] and sorted(os.listdir(os.path.join(TMP, "k1")))
              == ["timestep#3.hdf5", "timestep#5.hdf5"],
              os.listdir(os.path.join(TMP, "k1")) if res["materialized"] else res["steps"])
        res = run(save(box(timesteps(source(d), [5, 3])), os.path.join(TMP, "k2")))
        check("… and locally (order does not matter)",
              res["materialized"] and sorted(os.listdir(os.path.join(TMP, "k2")))
              == ["timestep#3.hdf5", "timestep#5.hdf5"])
        try:
            run(save(box(timesteps(source(d), [6, 7])), os.path.join(TMP, "k3")))
            check("labels that are not in the folder are an error", False)
        except ValueError as e:
            check("labels that are not in the folder are an error, naming what is there",
                  "#6, #7" in str(e) and "#3…#5" in str(e), str(e))


def test_report():
    print("== the run report names the hold ==")
    from vislang.server.cli_core import do_execute
    f = write_grid(os.path.join(TMP, "rep.hdf5"))
    spec = os.path.join(TMP, "spec.py")
    with open(spec, "w") as fh:
        fh.write(f'save(region(source("u@fakehost:{f}"), x=(0, 8)), '
                 f'"{os.path.join(TMP, "rep_out.npz")}")\n')
    with Env(VISLANG_SRUN_JOBID="auto", VISLANG_REMOTE="auto",
             VISLANG_CACHE=os.path.join(TMP, "cache4"),
             VISLANG_BUDGET_BYTES=os.path.getsize(f) // 2):
        text = do_execute(spec, confirm=True, without_allocation=True)
    check("report: NEEDS CONFIRM with the fallback paragraph",
          text.startswith("Status: NEEDS CONFIRM")
          and "FALLBACK FETCH OVER BUDGET" in text, text[:600])


def test_inspect_never_copies():
    print("== inspect reports, never copies ==")
    from vislang.formats import inspect
    from vislang.server.cli_core import do_inspect
    f = write_grid(os.path.join(TMP, "unread.hdf5"))
    from fakes_remote import ORIGINALS
    real, faked = inspect._run_remote_inspect, inspect._inspect_remote
    inspect._inspect_remote = ORIGINALS["inspect._inspect_remote"]   # the real path
    inspect._run_remote_inspect = lambda conn, path: None    # no reducer on the host
    try:
        f0 = len(FETCHES)
        text = do_inspect(f"u@fakehost:{f}")
        check("a schema only a copy could read is reported with its size",
              text.startswith("SCHEMA UNAVAILABLE") and "MiB" in text
              and "allow_fetch=True" in text and len(FETCHES) == f0, text)
        text = do_inspect(f"u@fakehost:{f}", allow_fetch=True)
        check("allow_fetch copies it and inspects the copy",
              "rho" in text and FETCHES[f0:] == [f], (text[:200], FETCHES[f0:]))
    finally:
        inspect._run_remote_inspect, inspect._inspect_remote = real, faked


if __name__ == "__main__":
    install_fake_remote()
    undo = install_login_inspect()
    no_allocation()
    try:
        test_single_file()
        test_folder()
        test_report()
        test_inspect_never_copies()
    finally:
        undo()
    print(f"\nall {len(PASS)} checks passed")
