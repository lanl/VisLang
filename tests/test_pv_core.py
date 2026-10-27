"""The ParaView plugin's engine, without ParaView (plugins/paraview/sieve_pv_core.py).

Plain-python asserts (no pytest on the cluster). Run from the repo root:
    python tests/test_pv_core.py

A fake `sieve` (a few lines of Python, via SIEVE_CMD) stands in for the real
one so the queue can be timed: one request at a time, oldest first; a newer
request replaces an owner's waiting one, never its running one; cancel stops a
process; a crashed process still yields a result. Also: the environment a
request runs in drops the host's Python settings, request keys ignore where a
result is written, the memo keeps only good results, the session trace and the
panel's status text for every outcome.
"""

import json
import os
import stat
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "plugins", "paraview"))

import sieve_pv_core as core

TMP = tempfile.mkdtemp(prefix="vislang_pvcore_test_")
PASS = []

FAKE = """#!{py}
import json, os, sys, time
args = sys.argv[1:]
op = args[1]
req = json.load(open(args[args.index("--request") + 1]))
out = args[args.index("--result") + 1]
open(args[args.index("--events") + 1], "a").write(
    json.dumps({{"kind": "phase", "name": "remote_reduce/pull", "state": "start"}}) + "\\n")
with open(os.path.join(os.path.dirname(out), "env.json"), "w") as f:
    json.dump(dict(os.environ), f)
if req.get("crash"):
    print("boom", file=sys.stderr)
    sys.exit(3)
time.sleep(req.get("sleep", 0))
json.dump({{"status": "ok", "op": op, "n": req.get("n"),
           "files": [{{"label": None, "path": "/x.vti"}}]}}, open(out, "w"))
"""


def check(name, cond, detail=""):
    assert cond, f"{name}: {detail}"
    PASS.append(name)
    print(f"  ok  {name}")


def fake_sieve():
    path = os.path.join(TMP, "fake_sieve")
    with open(path, "w") as f:
        f.write(FAKE.format(py=sys.executable))
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


def job(n, **req):
    return core.Job("run", {"n": n, **req}, os.path.join(TMP, f"job{n}-{time.time_ns()}"))


def test_runner(sieve):
    print("== the runner ==")
    r = core.Runner(sieve_cmd=sieve)
    a = r.submit("A", job(1, sleep=0.6))
    b1 = r.submit("B", job(2, sleep=0.1))
    b2 = r.submit("B", job(3, sleep=0.1))
    check("one at a time: the first runs, the rest wait",
          a.state == "running" and b2.state == "queued", (a.state, b2.state))
    check("a newer request replaces the owner's waiting one", b1.state == "superseded")
    a2 = r.submit("A", job(4))
    check("a newer request never kills the owner's running one", a.state == "running")
    r.wait(b2, timeout=20)
    check("waiting jobs run in the order asked",
          a.result["n"] == 1 and b2.result["n"] == 3 and a2.state in ("running", "queued",
                                                                          "done"))
    r.wait(a2, timeout=20)
    check("each job reads its own result", a2.result["n"] == 4)
    t = a.timings()
    check("queue and process time recorded", t["process_s"] >= 0.5, t)
    check("progress read from the events file", a.progress() == "pull")

    c = r.submit("C", job(5, sleep=30))
    time.sleep(0.3)
    r.cancel("C")
    check("cancel stops the process", c.state == "cancelled" and c.proc.poll() is not None)
    d = r.wait(r.submit("D", job(6, crash=True)), timeout=20)
    check("a process that dies still yields a result",
          d.result["status"] == "error" and "boom" in d.result["error"], d.result)
    e = core.Runner(sieve_cmd="/no/such/sieve")
    f = e.wait(e.submit("E", job(7)), timeout=5)
    check("a missing sieve is an error result, not an exception",
          f.result["status"] == "error" and "could not start" in f.result["error"])


def test_env(sieve):
    print("== the environment a request runs in ==")
    os.environ["PYTHONHOME"] = "/Applications/ParaView.app/Contents"
    os.environ["PYTHONPATH"] = "/pv/site-packages"
    os.environ["DYLD_LIBRARY_PATH"] = "/pv/lib"
    os.environ["VISLANG_REMOTE"] = "auto"
    try:
        r = core.Runner(sieve_cmd=sieve, base_env={"VISLANG_BUDGET_BYTES": "5"})
        j = r.wait(r.submit("A", job(1)), timeout=20)
        env = json.load(open(os.path.join(j.workdir, "env.json")))
        check("the host's Python settings are dropped",
              not any(k.startswith(("PYTHONHOME", "PYTHONPATH", "DYLD_")) for k in env), env)
        check("Sieve's own settings and the session's pass through",
              env.get("VISLANG_REMOTE") == "auto" and env.get("VISLANG_BUDGET_BYTES") == "5")
        check("system tools stay on PATH", "/usr/bin" in env["PATH"].split(":"))
    finally:
        for k in ("PYTHONHOME", "PYTHONPATH", "DYLD_LIBRARY_PATH", "VISLANG_REMOTE"):
            os.environ.pop(k, None)


def test_keys_memo_session():
    print("== keys, memo, session ==")
    k1 = core.request_key("source", "/d", {"stride": 4, "output": "/a/out.vti"})
    k2 = core.request_key("source", "/d", {"output": "/b/out.vti", "stride": 4})
    k3 = core.request_key("source", "/d", {"stride": 2, "output": "/a/out.vti"})
    check("a key names what was asked, not where it was written", k1 == k2 != k3)
    m = core.Memo()
    m.put("held", {"status": "held", "files": []})
    m.put("ok", {"status": "ok", "files": [{"path": "/x.vti"}]})
    check("the memo keeps only results that have data", len(m) == 1 and m.get("ok"))
    s = core.Session(root=os.path.join(TMP, "sessions"), info={"paraview": "6.1"})
    step, d = s.next_dir("source overview")
    s.trace({"source": "source", "action": "overview", "status": "ok"})
    meta = json.load(open(os.path.join(s.root, "session.json")))
    line = json.loads(open(s.trace_path).read().strip())
    check("the session folder: numbered steps, what was in force, a trace",
          step == 1 and d.endswith("001-source-overview") and meta["paraview"] == "6.1"
          and line["session"] == s.id and line["action"] == "overview", (d, line))


def test_status():
    print("== what the panel says ==")
    est = {"site": "remote", "read_mb": 2048.0, "output_mb": 2048.0,
           "time_lo_s": 300, "time_hi_s": 1200, "budget_reason": "size"}
    t = core.status_text(None, {"status": "held", "holds": {"budget": {"estimate": est}}})
    check("over budget: the cost, and what to do",
          "Over the size budget: 2.00 GB, ~5.0 min–20.0 min" in t and "Fetch anyway" in t, t)
    t = core.status_text(None, {"status": "held", "holds": {"allocation": {
        "salloc_cmd": "ssh h 'salloc --no-shell -J vislang'"}}})
    check("no allocation: the command to ask for, never run",
          "salloc --no-shell" in t and "shared HPC time" in t, t)
    t = core.status_text(None, {"status": "held", "holds": {"session": {"reason": "x"}}})
    check("no session: press Connect", "Connect" in t)
    t = core.status_text(None, {"status": "held", "holds": {"fetch": {"estimate": est}}})
    check("fallback copy: why, and that it needs its own approval",
          "cannot run next to the data" in t and "Fetch anyway" in t)
    t = core.status_text(None, {"status": "ok", "estimate": est, "total_s": 12.5,
                                "wire_bytes": 50 * 1024 ** 2,
                                "reuse": {"reused_pairs": 2, "total_pairs": 3}})
    check("fetched: bytes against the prediction, and cache reuse",
          "Over the wire: 50.0 MB (predicted 2.00 GB" in t and "reused 2/3" in t, t)
    check("failed: the reason", "Failed: no such field" in
          core.status_text(None, {"status": "error", "error": "no such field"}))


if __name__ == "__main__":
    sieve = fake_sieve()
    test_runner(sieve)
    test_env(sieve)
    test_keys_memo_session()
    test_status()
    print(f"\nall {len(PASS)} checks passed")
