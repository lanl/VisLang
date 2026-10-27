"""The machine interface for front ends (vislang/server/api.py).

Plain-python asserts (no pytest on the cluster). Run from the repo root:
    python tests/test_api.py

Covers: a descriptor for each kind of source (uniform .vti, a grid with no
geometry, a curvilinear .vts, points, a folder of timesteps) and the statuses
inspect can report; `run` composing and running a request (an overview, a box,
one timestep), pricing it without running (estimate), holding it over budget
and running it once approved, a spec given as text (named boxes, files listed
per tag), a broken spec, the client fields on the timing record, the progress
events; the remote holds (session, allocation) against the simulated remote;
and the `sieve api` process boundary — result written, exit 0 even when held.
"""

import json
import os
import subprocess
import sys
import tempfile

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import h5py
import pyvista as pv

from test_geometry import write_vts, terrain_points
from vislang.server import api

TMP = tempfile.mkdtemp(prefix="vislang_api_test_")
PASS = []


def check(name, cond, detail=""):
    assert cond, f"{name}: {detail}"
    PASS.append(name)
    print(f"  ok  {name}")


def fixtures():
    d = os.path.join(TMP, "data")
    os.makedirs(d)
    img = pv.ImageData(dimensions=(64, 48, 32), origin=(100.0, -4.0, 2.0),
                       spacing=(0.5, 2.0, 1.0))
    img.point_data["rho"] = np.arange(64 * 48 * 32, dtype=np.float32)
    img.save(os.path.join(d, "cube.vti"))
    with h5py.File(os.path.join(d, "plain.h5"), "w") as f:
        f["rho"] = np.arange(32 ** 3, dtype=np.float32).reshape(32, 32, 32)
        f["T"] = np.ones((32, 32, 32), dtype=np.float32)
    write_vts(os.path.join(d, "fire.vts"))
    rng = np.random.default_rng(0)
    np.savez(os.path.join(d, "pts.npz"), x=rng.uniform(0, 10, 5000),
             y=rng.uniform(0, 10, 5000), z=rng.uniform(0, 10, 5000),
             m=np.arange(5000.0))
    s = os.path.join(d, "series")
    os.makedirs(s)
    for t in (3, 4, 5):
        with h5py.File(os.path.join(s, f"snap#{t}.h5"), "w") as f:
            f["rho"] = (np.arange(16 ** 3, dtype=np.float32) + 1000 * t).reshape(16, 16, 16)
    os.makedirs(os.path.join(d, "empty"))
    return d


def test_inspect(d):
    print("== inspect: a descriptor per kind of source ==")
    v = api.inspect(os.path.join(d, "cube.vti"))
    check("uniform grid: world coordinates, bounds from origin + spacing",
          v["status"] == "ok" and v["coords"] == "world" and v["out_ext"] == ".vti"
          and v["bounds"] == [[100.0, 131.5], [-4.0, 90.0], [2.0, 33.0]], v)
    h = api.inspect(os.path.join(d, "plain.h5"))
    check("grid with no geometry: cells, bounds are its indices",
          h["coords"] == "cells" and h["bounds"] == [[0, 31]] * 3
          and sorted(h["variables"]) == ["T", "rho"], h)
    s = api.inspect(os.path.join(d, "fire.vts"))
    check("curvilinear: world, written as .vts, bounds only the points know",
          s["coords"] == "world" and s["out_ext"] == ".vts" and s["bounds"] is None, s)
    p = api.inspect(os.path.join(d, "pts.npz"))
    check("points: world, .vtp, a count and a stride for the overview",
          p["modality"] == "particles" and p["out_ext"] == ".vtp"
          and p["particles"] == 5000 and p["suggested_stride"] == 1, p)
    f = api.inspect(os.path.join(d, "series"))
    check("a folder: its timesteps and the shared schema",
          f["timesteps"] == [3, 4, 5] and f["grid"] == [16, 16, 16]
          and f["source_bytes"] > 3 * 16 ** 3 * 4, f)
    e = api.inspect(os.path.join(d, "empty"))
    check("a folder with no timesteps is an error, not a schema", e["status"] == "error", e)
    m = api.inspect(os.path.join(d, "nope.vti"))
    check("a missing file is an error", m["status"] == "error", m)
    big = {"modality": "grid", "grid": [512, 512, 512], "itemsizes": {"rho": 8}}
    check("the overview stride keeps one field near 32 MiB",
          api._suggest_stride(big) == 4, api._suggest_stride(big))


def run(workdir, **request):
    return api.run({"workdir": os.path.join(TMP, workdir), **request})


def test_run(d):
    print("== run: compose, price, hold, approve ==")
    os.environ["VISLANG_TIMINGS_FILE"] = os.path.join(TMP, "timings.jsonl")
    os.environ["VISLANG_EVENTS_FILE"] = ""
    cube = api.inspect(os.path.join(d, "cube.vti"))
    r = run("ov", descriptor=cube, params={"stride": 4},
            client={"name": "test", "session": "S", "step": 1})
    m = pv.read(r["output"])
    check("an overview runs and writes where the client asked",
          r["status"] == "ok" and r["files"][0]["path"] == r["output"]
          and tuple(m.dimensions) == (16, 12, 8) and np.allclose(m.spacing, (2, 8, 4)),
          r)
    rec = [json.loads(ln) for ln in open(os.environ["VISLANG_TIMINGS_FILE"])][-1]
    check("the timing record carries the client, session and step",
          rec.get("client") == {"name": "test", "session": "S", "step": 1}
          and rec["run_id"] == r["run_id"], rec.get("client"))

    r = run("box", descriptor=cube,
            params={"box": {"lo": [110.2, 10.0, 0], "hi": [120.0, 30.0, 99]}})
    m = pv.read(r["output"])
    check("a box: the spec names it in world coordinates, the cut lands there",
          "coords='world'" in r["spec_text"] and r["status"] == "ok"
          and np.allclose(m.origin, (110.5, 10.0, 2.0)) and tuple(m.dimensions)[:2] == (20, 11),
          (r["spec_text"], m.origin, m.dimensions))

    r = run("est", descriptor=cube, params={"stride": 2}, mode="estimate")
    check("estimate: priced, nothing written",
          r["status"] == "ok" and r["estimate"]["output_mb"] > 0 and r["files"] == []
          and not os.path.exists(os.path.join(TMP, "est", "out.vti")), r)

    plain = api.inspect(os.path.join(d, "plain.h5"))
    r = run("held", descriptor=plain, params={"fields": ["rho"]}, budget_bytes=1000)
    check("over budget: held, with the estimate, nothing written",
          r["status"] == "held" and "budget" in r["holds"]
          and r["holds"]["budget"]["estimate"]["over_budget"] and r["files"] == [], r)
    r = run("held", descriptor=plain, params={"fields": ["rho"]}, budget_bytes=1000,
            approval={"budget": True})
    check("approved: the same request runs",
          r["status"] == "ok" and len(r["files"]) == 1, r)

    series = api.inspect(os.path.join(d, "series"))
    r = run("t4", descriptor=series, params={"timestep": 4, "stride": 2})
    check("one timestep of a series: its file listed with its label",
          r["status"] == "ok" and [f["label"] for f in r["files"]] == [4], r["files"])

    spec = (f"src = source({os.path.join(d, 'plain.h5')!r})\n"
            f"save(region(src, center={{'a': (4, 4, 4), 'b': (20, 20, 20)}}, size=4), "
            f"{os.path.join(TMP, 'tags', 'boxes.vti')!r})\n")
    r = run("tags", spec_text=spec)
    check("a spec given as text: named boxes listed by tag",
          r["status"] == "ok" and sorted(f["tag"] for f in r["files"]) == ["a", "b"],
          r["files"])
    r = run("bad", spec_text="save(region(source('/d'), x=(5, 1)), 'o.npz')\n")
    check("a broken spec: error with the reason", r["status"] == "error"
          and "lo < hi" in (r["error"] or ""), r["error"])

    events = os.path.join(TMP, "events.jsonl")
    os.environ["VISLANG_EVENTS_FILE"] = events
    run("ev", descriptor=cube, params={"stride": 8})
    os.environ["VISLANG_EVENTS_FILE"] = ""
    kinds = [e["kind"] for e in map(json.loads, open(events))]
    check("progress events: the estimate, then the phases",
          "estimate" in kinds and kinds.count("phase") >= 4, kinds)


def test_remote(d):
    print("== remote holds ==")
    from fakes_remote import install_fake_remote, install_login_inspect
    from vislang.remote import reduce
    install_fake_remote()
    undo = install_login_inspect()
    os.environ["VISLANG_CACHE"] = os.path.join(TMP, "rcache")
    os.environ["VISLANG_REMOTE"] = "auto"
    os.environ["VISLANG_REMOTE_TMP"] = os.path.join(TMP, "rtmp")
    os.makedirs(os.environ["VISLANG_REMOTE_TMP"])
    uri = f"u@fakehost:{os.path.join(d, 'plain.h5')}"
    try:
        desc = api.inspect(uri)
        check("a remote file: described from the login node",
              desc["status"] == "ok" and desc["coords"] == "cells", desc)
        r = run("r1", descriptor=desc, params={"box": {"lo": [0, 0, 0],
                                                       "hi": [7, 7, 7]}})
        # (The faked transport counts no wire bytes; a real one does.)
        check("a remote box runs next to the data, shipped in the file's indices",
              r["status"] == "ok" and r["route"] == "remote_reduce"
              and any('"coords": "index"' in s for s in r["steps"]), r["steps"])

        real = reduce.session_check
        reduce.session_check = lambda u: reduce.SessionHold(
            "no live session", "sieve connect u@fakehost", [])
        try:
            check("no session: inspect says so, with the command",
                  api.inspect(uri)["status"] == "needs_session")
            r = run("r2", descriptor=desc, params={"stride": 2})
            check("no session: a run is held with the connect command",
                  r["status"] == "held"
                  and r["holds"]["session"]["connect_cmd"] == "sieve connect u@fakehost",
                  r["holds"])
        finally:
            reduce.session_check = real

        os.environ["VISLANG_SRUN_JOBID"] = "auto"
        # (A box inside one already cached needs no allocation: it is a cache hit.)
        reduce.allocation_status = lambda conn: {"present": False, "reason": "none"}
        r = run("r3", descriptor=desc, params={"box": {"lo": [12, 12, 12],
                                                       "hi": [20, 20, 20]}})
        check("no allocation: held, with the salloc to propose",
              r["status"] == "held" and "salloc" in r["holds"]["allocation"]["salloc_cmd"],
              r["holds"])
    finally:
        os.environ.pop("VISLANG_SRUN_JOBID", None)
        undo()


def test_process(d):
    print("== the process boundary ==")
    req = os.path.join(TMP, "req.json")
    res = os.path.join(TMP, "res.json")
    plain = api.inspect(os.path.join(d, "plain.h5"))
    with open(req, "w") as f:
        json.dump({"workdir": os.path.join(TMP, "proc"), "descriptor": plain,
                   "params": {}, "budget_bytes": 10}, f)
    p = subprocess.run([os.path.join(REPO, "sieve"), "api", "run", "--request", req,
                        "--result", res], capture_output=True, text=True,
                       env={**os.environ, "VISLANG_PYTHON": sys.executable})
    out = json.load(open(res))
    check("held, yet exit 0 and the result written", p.returncode == 0
          and out["status"] == "held" and not os.path.exists(res + ".tmp"),
          (p.returncode, p.stderr[-300:]))
    p = subprocess.run([os.path.join(REPO, "sieve"), "api", "doctor"],
                       capture_output=True, text=True,
                       env={**os.environ, "VISLANG_PYTHON": sys.executable})
    doc = json.loads(p.stdout)
    check("doctor reports to stdout as JSON", doc["status"] == "ok"
          and doc["checks"]["h5py"] == "ok", p.stdout[:300])


if __name__ == "__main__":
    data = fixtures()
    test_inspect(data)
    test_run(data)
    test_process(data)
    test_remote(data)
    print(f"\nall {len(PASS)} checks passed")
