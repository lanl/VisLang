"""Per-timestep regions: region(series, center={N: ...} | track="f.csv", size=S).

Plain-python asserts (no pytest on the cluster). Run from the repo root:
    python tests/test_region_track.py

Covers: form validation, the int-keyed centre dict crossing the sandbox, the
wire round-trip (and that a fixed region keeps its old shape), track-file
parsing + hashing, per-timestep boxes on a grid series (index space, clipped)
and a particle series (world coordinates), coverage errors raised before any
read, the provenance record, and the remote pieces (per-timestep catalog keys,
the delta reducer's re-rooting, the folder plan carrying centres inline).
"""

import json
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vislang.dsl import reset_sinks, collected_sinks, form_namespace
from vislang.dsl.forms import source, region, fields, save, timesteps
from vislang.dsl.nodes import RegionNode
from vislang.dsl.ast_serialize import (to_plan, from_plan, to_plan_json,
                                       from_plan_json, PlanValidationError)
from vislang.interpreter.planner import plan_pipeline
from vislang.interpreter import planner, track
from vislang.runtime import provenance

TMP = tempfile.mkdtemp(prefix="vislang_track_test_")
PASS = []


def check(name, cond, detail=""):
    assert cond, f"{name}: {detail}"
    PASS.append(name)
    print(f"  ok  {name}")


def raises(name, fn, exc=Exception, text=None):
    try:
        fn()
    except exc as e:
        check(name, text is None or text in str(e), str(e))
        return
    check(name, False, "did not raise")


def grid_series(name="gseries", labels=(0, 1, 2), shape=(16, 16, 16)):
    import h5py
    d = os.path.join(TMP, name)
    os.makedirs(d, exist_ok=True)
    for t in labels:
        a = np.arange(int(np.prod(shape)), dtype=np.float32).reshape(shape) + t
        with h5py.File(os.path.join(d, f"snap#{t}.hdf5"), "w") as f:
            f.create_dataset("rho", data=a)
    return d


def particle_series(name="pseries", labels=(279, 300)):
    import h5py
    d = os.path.join(TMP, name)
    os.makedirs(d, exist_ok=True)
    rng = np.random.default_rng(0)
    for t in labels:
        with h5py.File(os.path.join(d, f"halo#{t}.hdf5"), "w") as f:
            for c in ("x", "y", "z"):
                f.create_dataset(c, data=rng.uniform(0, 100, 5000).astype(np.float32))
            f.create_dataset("id", data=np.arange(5000, dtype=np.int64))
    return d


def run(sink_node, spec_text="# test\n"):
    with provenance.run("spec.py", spec_code=spec_text):
        return plan_pipeline(sink_node)


# ---------------------------------------------------------------------------
def test_form():
    print("== form ==")
    src = source("/data/series")
    r = region(src, center={300: (1, 2, 3), 279: (4.5, 5, 6)}, size=10)
    check("centres sorted by label", r.centers == ((279, 4.5, 5, 6), (300, 1, 2, 3)),
          r.centers)
    check("scalar size expands", r.size == (10, 10, 10), r.size)
    check("per_step flag", r.per_step and not region(src, x=(0, 1)).per_step)
    r2 = region(src, center={0: (1, 2)}, size=(4, None))
    check("2-D centre, None keeps an axis", r2.size == (4, None), r2.size)
    t = region(src, track="halo.csv", size=30)
    check("track kept unresolved", t.track == "halo.csv" and t.size == (30,), t)

    raises("ranges and center exclusive",
           lambda: region(src, x=(0, 1), center={0: (1, 2)}, size=1), ValueError)
    raises("center and track exclusive",
           lambda: region(src, center={0: (1, 2)}, track="a.csv", size=1), ValueError)
    raises("size required", lambda: region(src, center={0: (1, 2)}), ValueError,
           "needs size")
    raises("size without centre", lambda: region(src, x=(0, 1), size=3), ValueError)
    raises("bad size", lambda: region(src, center={0: (1, 2)}, size=0), ValueError)
    raises("size length", lambda: region(src, center={0: (1, 2)}, size=(1, 2, 3)),
           ValueError)
    raises("all-None size", lambda: region(src, center={0: (1, 2)}, size=(None, None)),
           ValueError)
    raises("non-int label", lambda: region(src, center={"a": (1, 2)}, size=1), TypeError)
    raises("bool coordinate", lambda: region(src, center={0: (True, 2)}, size=1),
           TypeError)
    raises("mixed dimension",
           lambda: region(src, center={0: (1, 2), 1: (1, 2, 3)}, size=1), ValueError)


def test_sandbox():
    print("== sandbox ==")
    from vislang.runtime.sandbox import execute
    reset_sinks()
    execute("save(region(source('/d/s'), center={279: (1.0, 2.0, 3.0), "
            "300: (4.0, 5.0, 6.0)}, size=30), 'out.npz')", form_namespace())
    r = collected_sinks()[0].upstream
    check("int-keyed dict crosses the sandbox",
          isinstance(r, RegionNode) and r.centers == ((279, 1.0, 2.0, 3.0),
                                                      (300, 4.0, 5.0, 6.0)),
          getattr(r, "centers", r))
    reset_sinks()


def test_wire():
    print("== wire ==")
    reset_sinks()
    fixed = save(region(source("/d/s"), x=(0, 4)), "o.npz")
    check("fixed region keeps the old wire shape",
          set(to_plan(fixed)["chain"][1]) == {"kind", "ranges"})
    moving = save(region(fields(source("/d/s"), ["x"]),
                         center={1: (1.0, 2.0, 3.0), 2: (2.0, 3.0, 4.0)},
                         size=(5, 5, None)), "o.npz")
    back = from_plan_json(to_plan_json(moving)).upstream
    check("centres round-trip", back.centers == ((1, 1.0, 2.0, 3.0), (2, 2.0, 3.0, 4.0))
          and back.size == (5, 5, None), back)
    plan = to_plan(moving)
    bad = json.loads(json.dumps(plan))
    bad["chain"][2]["ranges"] = [["x", 0, 1]]
    raises("ranges plus centres rejected", lambda: from_plan(bad), PlanValidationError)
    bad = json.loads(json.dumps(plan))
    bad["chain"][2]["centers"][0][1] = True
    raises("bool coordinate rejected", lambda: from_plan(bad), PlanValidationError)
    bad = json.loads(json.dumps(plan))
    del bad["chain"][2]["track_sha"]
    raises("partial per-step keys rejected", lambda: from_plan(bad), PlanValidationError)
    bad = json.loads(json.dumps(plan))
    bad["chain"][2]["size"] = [5, 5]
    raises("size/centre mismatch rejected", lambda: from_plan(bad), PlanValidationError)
    reset_sinks()


def test_track_file():
    print("== track file ==")
    p = os.path.join(TMP, "t.csv")
    with open(p, "w") as f:
        f.write("# halo 11521140891\nstep,x,y,z\n300,4,5,6\n279,1,2,3\n")
    rows, sha = track.load_track(p)
    check("track rows sorted", rows == ((279, 1.0, 2.0, 3.0), (300, 4.0, 5.0, 6.0)), rows)
    with open(p, "a") as f:
        f.write("301,7,8,9\n")
    check("hash follows content", track.load_track(p)[1] != sha)
    resolved = track.resolve_tracks([region(source("/d"), track=p, size=8)])[0]
    check("resolve inlines rows + expands size",
          len(resolved.centers) == 3 and resolved.size == (8, 8, 8)
          and resolved.track_sha)
    for name, body, text in [
            ("bad header", "t,x,y\n0,1,2\n", "header"),
            ("extra column", "step,x,y\n0,1,2,3\n", "value(s)"),
            ("non-int step", "step,x,y\n0.5,1,2\n", "integer"),
            ("duplicate step", "step,x,y\n0,1,2\n0,3,4\n", "more than one"),
            ("empty file", "", "empty")]:
        q = os.path.join(TMP, "bad.csv")
        with open(q, "w") as f:
            f.write(body)
        raises(f"track: {name}", lambda: track.load_track(q), Exception, text)
    raises("track: missing file", lambda: track.load_track(os.path.join(TMP, "nope.csv")),
           FileNotFoundError)
    raises("track size vs dimension",
           lambda: track.resolve_tracks([region(source("/d"), track=p, size=(1, 2))]),
           ValueError, "3-D centres")


def test_grid_series():
    print("== grid series ==")
    d = grid_series()
    out = os.path.join(TMP, "g_out")
    c = {0: (4, 4, 8), 1: (8, 8, 8), 2: (14, 14, 8)}
    reset_sinks()
    res = run(save(region(source(d), center=c, size=(6, 6, None)), out + ".npz"),
              "save(region(source(d), center=..., size=(6, 6, None)), 'g_out.npz')")
    shapes, firsts = {}, {}
    for t in (0, 1, 2):
        with np.load(os.path.join(out, f"timestep#{t}.npz")) as z:
            shapes[t], firsts[t] = z["rho"].shape, float(z["rho"][0, 0, 0])
    check("each timestep its own box", shapes == {0: (6, 6, 16), 1: (6, 6, 16),
                                                  2: (5, 5, 16)}, shapes)
    # rho[i,j,k] = 256 i + 16 j + k + t: the first cell names where the box began.
    check("box placed at its own centre",
          firsts == {0: 272.0 + 0, 1: 1360.0 + 1, 2: 2992.0 + 2}, firsts)
    check("clip reported", any("clipped" in s and "#2" in s for s in res["steps"]),
          res["steps"])

    rec = provenance.record_for(out)
    boxes = rec["logical"]["resolved"]["region_boxes"]
    check("provenance: box per timestep",
          boxes[0]["box"] == {"x": [1, 7], "y": [1, 7]}
          and boxes[2].get("clipped") == ["x", "y"], boxes)
    check("provenance: explanation names the moving box",
          "centred on each timestep's own centre" in rec["logical"]["spec_explanation"],
          rec["logical"]["spec_explanation"])

    # Same track from a file: identical output, and the hash is recorded.
    p = os.path.join(TMP, "g.csv")
    with open(p, "w") as f:
        f.write("step,x,y,z\n" + "".join(f"{t},{x},{y},{z}\n"
                                          for t, (x, y, z) in c.items()))
    out2 = os.path.join(TMP, "g_out2")
    reset_sinks()
    run(save(region(source(d), track=p, size=(6, 6, None)), out2 + ".npz"))
    same = all(np.array_equal(np.load(os.path.join(out, f"timestep#{t}.npz"))["rho"],
                              np.load(os.path.join(out2, f"timestep#{t}.npz"))["rho"])
               for t in (0, 1, 2))
    check("track file == inline centres", same)
    tr = provenance.record_for(out2)["logical"]["resolved"]["region_track"]
    check("provenance: track path + sha", tr["path"] == p
          and tr["sha256"] == track.load_track(p)[1], tr)

    # timesteps() narrows the selection; a longer track is fine.
    reset_sinks()
    res = plan_pipeline(save(region(timesteps(source(d), 1, 2), center=c, size=4),
                             os.path.join(TMP, "g_sel.npz")))
    check("track longer than selection is fine", res["timesteps"] == [1, 2],
          res.get("timesteps"))

    # dry run lists each box and reads nothing
    reset_sinks()
    res = plan_pipeline(save(region(source(d), center=c, size=6),
                             os.path.join(TMP, "g_dry.npz")), dry_run=True)
    check("dry run lists boxes", any(s.strip().startswith("#2: x:(11,16)")
                                     for s in res["steps"]), res["steps"])


def test_coverage_before_read():
    print("== coverage ==")
    d = grid_series()
    calls = []
    orig = planner.materialize
    planner.materialize = lambda *a, **k: calls.append(1) or orig(*a, **k)
    try:
        reset_sinks()
        raises("missing centre raises",
               lambda: plan_pipeline(save(region(source(d), center={0: (4, 4, 4)},
                                                 size=4), os.path.join(TMP, "c1.npz"))),
               ValueError, "#1, #2")
        reset_sinks()
        raises("centre for a timestep that does not exist",
               lambda: plan_pipeline(save(region(source(d),
                                                 center={t: (4, 4, 4) for t in (0, 1, 2, 9)},
                                                 size=4), os.path.join(TMP, "c2.npz"))),
               ValueError, "#9")
        reset_sinks()
        raises("box entirely outside the grid",
               lambda: plan_pipeline(save(region(source(d),
                                                 center={t: (40, 4, 4) for t in (0, 1, 2)},
                                                 size=4), os.path.join(TMP, "c3.npz"))),
               ValueError, "entirely outside")
        check("nothing was read", not calls, calls)
    finally:
        planner.materialize = orig
    reset_sinks()
    raises("single-file source refused",
           lambda: plan_pipeline(save(region(source(os.path.join(d, "snap#0.hdf5")),
                                             center={0: (4, 4, 4)}, size=4),
                                      os.path.join(TMP, "c4.npz"))),
           ValueError, "folder")
    reset_sinks()


def test_particle_series():
    print("== particle series ==")
    d = particle_series()
    out = os.path.join(TMP, "p_out")
    c = {279: (20.0, 20.0, 20.0), 300: (70.0, 70.0, 70.0)}
    reset_sinks()
    run(save(region(fields(source(d, positions=("x", "y", "z")), ["x", "y", "z"]),
                    center=c, size=20), out + ".npz"))
    ok = True
    for t, (cx, cy, cz) in c.items():
        with np.load(os.path.join(out, f"timestep#{t}.npz")) as z:
            xyz = np.stack([z["x"], z["y"], z["z"]])
        lo = np.array([cx, cy, cz])[:, None] - 10
        ok &= xyz.shape[1] > 0 and bool(((xyz >= lo) & (xyz <= lo + 20)).all())
    check("points inside each timestep's world box", ok)
    boxes = provenance.record_for(out)["logical"]["resolved"]["region_boxes"]
    check("provenance: world-space boxes",
          boxes[300]["box"]["x"] == [60.0, 80.0], boxes)


def test_remote_pieces():
    print("== remote pieces ==")
    from vislang.remote.reduce import _narrow_key, _rebuild_folder_chain
    from vislang.remote.executor import _reroot
    from vislang.formats.inspect import inspect_file
    reset_sinks()
    r = region(source("/d"), center={1: (4, 4, 4), 2: (8, 8, 8)}, size=4)
    k1, k2 = _narrow_key([r], 1), _narrow_key([r], 2)
    check("catalog key per timestep", k1 != k2 and k1["forms"][0][0] == "region_at",
          (k1, k2))
    check("fixed region key unchanged",
          _narrow_key([region(source("/d"), x=(0, 4))])
          == {"forms": [["region", [["x", 0, 4]]]]})
    from vislang.remote.catalog import _fuse_forms
    check("moving key is exact-match only", _fuse_forms(k1["forms"], (16, 16, 16)) is None)

    chain = _rebuild_folder_chain("/remote/dir", None, [fields(r, ["rho"]).upstream], [])
    plan = json.loads(to_plan_json(save(chain, "o.npz")))
    step = [s for s in plan["chain"] if s["kind"] == "region"][0]
    check("folder plan carries centres inline", step["centers"] == [[1, 4, 4, 4],
                                                                     [2, 8, 8, 8]], step)

    d = grid_series()
    path = os.path.join(d, "snap#2.hdf5")
    node = _reroot([r], path, None, ["rho"], label=2, info=inspect_file(path))
    reg = node.upstream
    check("delta reducer cuts its own timestep's box",
          isinstance(reg, RegionNode) and not reg.per_step
          and reg.ranges == (("x", 6, 10), ("y", 6, 10), ("z", 6, 10)), reg)
    reset_sinks()


if __name__ == "__main__":
    test_form()
    test_sandbox()
    test_wire()
    test_track_file()
    test_grid_series()
    test_coverage_before_read()
    test_particle_series()
    test_remote_pieces()
    print(f"\n{len(PASS)} checks passed")
