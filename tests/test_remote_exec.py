"""vislang_exec + site-aware planner dispatch, verified without a real remote.

Run from the repo root: python tests/test_remote_exec.py
- vislang_exec is exercised for real, as a subprocess on a local file: the
  exact path the reducer runs remotely (plan.json in, reduced.npz + meta out).
- planner._plan_remote's dispatch (dry-run / cost gate / fallback / force) is
  exercised with a stubbed remote_reduce and a stubbed whole-file fetch — the
  wiring, not the wire. True end-to-end still needs a live ssh host.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from dsl_forms import reset_sinks
from dsl_forms.forms import source, subsample, threshold, save
from ast_serialize import to_plan_json
from my_inspect import inspect_file
from planner import plan_pipeline
import planner

# vislang_exec runs here as a LOCAL subprocess (the fakes stand in for ssh), so
# use the interpreter running the test; override with VISLANG_TEST_PYTHON.
PY = os.environ.get("VISLANG_TEST_PYTHON", sys.executable)
RAW = os.path.join(REPO, "csafe_heptane_302x302x302_uint8.raw")
EXEC = os.path.join(REPO, "vislang_exec.py")
TMP = tempfile.mkdtemp(prefix="vislang_rexec_")

PASS = []


def check(name, cond, detail=""):
    assert cond, f"{name}: {detail}"
    PASS.append(name)
    print(f"  ok  {name}")


def parse_meta(stdout):
    chunk = stdout.split("===VISLANG_META_BEGIN===", 1)[1] \
                  .split("===VISLANG_META_END===", 1)[0]
    return json.loads(chunk.strip())


def main():
    var = inspect_file(RAW).variables[0]

    # These tests all target single remote FILES. The planner now probes remote
    # folder-ness before dispatch; stub it False so no real ssh is attempted (the
    # probe itself is unit-tested in test_remote_helpers.py).
    import my_inspect
    my_inspect.remote_is_dir = lambda uri: False

    print("== vislang_exec: the reducer entry point, run locally ==")
    reset_sinks()
    prefix = threshold(subsample(source(RAW), 4), f"{var} > 100")
    plan = to_plan_json(prefix)
    out = os.path.join(TMP, "reduced.npz")

    r = subprocess.run([PY, EXEC, "--stdin", "--out", out],
                       input=plan.encode(), capture_output=True, cwd=REPO)
    check("exec exits 0", r.returncode == 0, r.stderr.decode()[-500:])
    meta = parse_meta(r.stdout.decode())
    check("meta ok", meta.get("ok") is True, str(meta))
    check("meta schema vars", var in meta["schema"]["variables"])
    check("meta saved vars", meta["saved_variables"] == [var])
    check("meta steps mention threshold",
          any("threshold" in s for s in meta["steps"]))

    # byte-identical to running the same pipeline in-process
    ref_out = os.path.join(TMP, "ref.npz")
    reset_sinks()
    plan_pipeline(save(threshold(subsample(source(RAW), 4), f"{var} > 100"),
                       ref_out))
    with np.load(out) as a, np.load(ref_out) as b:
        check("reduced equals in-process result",
              np.array_equal(np.nan_to_num(a[var], nan=-1),
                             np.nan_to_num(b[var], nan=-1)))

    print("== vislang_exec: rejections ==")
    reset_sinks()
    sink_plan = to_plan_json(save(source(RAW), os.path.join(TMP, "no.npz")))
    reset_sinks()
    r = subprocess.run([PY, EXEC, "--stdin", "--out", out],
                       input=sink_plan.encode(), capture_output=True, cwd=REPO)
    check("sink in prefix rejected", r.returncode == 1
          and "sink" in parse_meta(r.stdout.decode())["error"])
    r = subprocess.run([PY, EXEC, "--stdin", "--out", out],
                       input=b'{"vislang_plan":1,"chain":[{"kind":"exec"}]}',
                       capture_output=True, cwd=REPO)
    meta = parse_meta(r.stdout.decode())
    check("hostile plan rejected", r.returncode == 1
          and "rejected" in meta["error"], str(meta))

    print("== planner dispatch: dry run is zero-network ==")
    fake_uri = "nosuchuser@nosuchhost:/data/f.raw"
    reset_sinks()
    res = plan_pipeline(subsample(source(fake_uri), 2), dry_run=True)
    check("dry run returns", res["materialized"] is False)
    check("dry run mentions remote", any("remote" in s for s in res["steps"]))
    check("dry run mentions deferral", any("dry run" in s for s in res["steps"]))

    print("== planner dispatch: stubbed remote_reduce + stubbed fetch ==")
    import types
    calls = {"reduce": 0, "fetch": 0}

    class StubUnavailable(Exception):
        pass

    def stub_reduce_unavailable(src, middle):
        calls["reduce"] += 1
        raise StubUnavailable("stub says no")

    stub_mod = types.ModuleType("remote_reduce")
    stub_mod.remote_reduce = stub_reduce_unavailable
    stub_mod.RemoteUnavailable = StubUnavailable

    def stub_fetch(uri):
        calls["fetch"] += 1
        local = os.path.join(TMP, "fetched.raw")
        # keep the shape-encoding filename convention the raw adapter needs
        local = os.path.join(TMP, os.path.basename(RAW))
        shutil.copyfile(RAW, local)
        return local

    real_fetch = planner._fetch_remote
    real_mod = sys.modules.get("remote_reduce")
    sys.modules["remote_reduce"] = stub_mod
    planner._fetch_remote = stub_fetch
    old_env = os.environ.get("VISLANG_REMOTE")
    try:
        # auto + narrowing -> tries remote, falls back to fetch, still executes
        os.environ["VISLANG_REMOTE"] = "auto"
        reset_sinks()
        out2 = os.path.join(TMP, "fallback.npz")
        res = plan_pipeline(save(subsample(source(fake_uri), 4), out2))
        check("fallback tried remote", calls["reduce"] == 1)
        check("fallback fetched whole file", calls["fetch"] == 1)
        check("fallback executed locally", res["materialized"] is True
              and os.path.exists(out2))
        check("fallback steps explain why",
              any("unavailable" in s for s in res["steps"]), str(res["steps"]))

        # no narrowing -> cost gate skips remote entirely
        reset_sinks()
        out3 = os.path.join(TMP, "gate.npz")
        res = plan_pipeline(save(source(fake_uri), out3))
        check("cost gate skips remote (no narrowing)", calls["reduce"] == 1)
        check("cost gate explains itself",
              any("no narrowing" in s for s in res["steps"]), str(res["steps"]))

        # off -> never touches remote_reduce
        os.environ["VISLANG_REMOTE"] = "off"
        reset_sinks()
        plan_pipeline(save(subsample(source(fake_uri), 4),
                           os.path.join(TMP, "off.npz")))
        check("off mode skips remote", calls["reduce"] == 1)

        # force -> surfaces the failure instead of falling back
        os.environ["VISLANG_REMOTE"] = "force"
        reset_sinks()
        try:
            plan_pipeline(save(subsample(source(fake_uri), 4),
                               os.path.join(TMP, "force.npz")))
            check("force surfaces failure", False, "no error raised")
        except RuntimeError as e:
            check("force surfaces failure", "force" in str(e))
    finally:
        planner._fetch_remote = real_fetch
        if real_mod is not None:
            sys.modules["remote_reduce"] = real_mod
        else:
            sys.modules.pop("remote_reduce", None)
        if old_env is None:
            os.environ.pop("VISLANG_REMOTE", None)
        else:
            os.environ["VISLANG_REMOTE"] = old_env

    shutil.rmtree(TMP)
    print(f"\nALL {len(PASS)} CHECKS PASSED")


if __name__ == "__main__":
    main()
