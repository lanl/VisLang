"""Named boxes: region(node, center={tag: …} | track="f.csv" with a tag column).

Plain-python asserts (no pytest on the cluster). Run from the repo root:
    python tests/test_region_tags.py

Covers: form validation and the sandbox, the wire round-trip and its strict
validator, tagged track files, a grid series written one series per tag (with
gaps, the ParaView index and TimeValue), a single file written one output per
tag, every refusal raised before a read, the shared read (one read per file
for point data), equivalence with an unnamed moving box, the provenance record
per tag, and the three remote routes — single file, catalog folder, and the
plain folder batch — against a simulated remote (the transport is faked; the
plan, executor, catalog and planner are the real code).
"""

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
# Same remote fixture discipline as test_remote_reduce: generic HDF5 listing on
# both sides, this repo as the "remote" checkout.
os.environ["VISLANG_NO_BINDING"] = "1"
os.environ["VISLANG_REMOTE_REPO"] = REPO

import h5py

from vislang.dsl import reset_sinks, collected_sinks, form_namespace
from vislang.dsl.forms import source, region, fields, save, render, timesteps, threshold
from vislang.dsl.nodes import RegionNode
from vislang.dsl.ast_serialize import (to_plan, from_plan, to_plan_json,
                                       from_plan_json, PlanValidationError)
from vislang.interpreter import planner, track, load
from vislang.interpreter.planner import plan_pipeline
from vislang.runtime import provenance

TMP = tempfile.mkdtemp(prefix="vislang_tags_test_")
PY = os.environ.get("VISLANG_TEST_PYTHON", sys.executable)
EXEC = os.path.join(REPO, "vislang_exec.py")
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


def grid_series(name="gseries", labels=(10, 11, 12), shape=(16, 16, 16)):
    d = os.path.join(TMP, name)
    os.makedirs(d, exist_ok=True)
    for t in labels:
        a = np.arange(int(np.prod(shape)), dtype=np.float32).reshape(shape) + t
        with h5py.File(os.path.join(d, f"snap#{t}.hdf5"), "w") as f:
            f.create_dataset("rho", data=a)
    return d


def particle_series(name="pseries", labels=(1, 2), n=4000):
    d = os.path.join(TMP, name)
    os.makedirs(d, exist_ok=True)
    rng = np.random.default_rng(7)
    for t in labels:
        with h5py.File(os.path.join(d, f"halo#{t}.hdf5"), "w") as f:
            for c in ("x", "y", "z"):
                f.create_dataset(c, data=rng.uniform(0, 100, n).astype(np.float32))
            f.create_dataset("mass", data=np.arange(n, dtype=np.float64) + t)
    return d


def run(sink_node, spec_text="# test\n", **kw):
    with provenance.run("spec.py", spec_code=spec_text):
        return plan_pipeline(sink_node, **kw)


# rho[i,j,k] = 256 i + 16 j + k + t on a 16³ grid: a box's first cell names it.
def first_cell(lo, t):
    return float(256 * lo[0] + 16 * lo[1] + lo[2] + t)


C = {"v1": {10: (4, 4, 4), 11: (5, 5, 5), 12: (6, 6, 6)},
     "v2": {10: (12, 12, 12), 12: (14, 14, 14)}}


# ---------------------------------------------------------------------------
def test_form():
    print("== form ==")
    src = source("/data/series")
    r = region(src, center=C, size=4)
    check("tags keep the order written", track.tag_names(r) == ["v1", "v2"])
    check("tagged, not per_step", r.tagged and r.tag_series and not r.per_step)
    check("rows per tag, sorted by label",
          r.tags[1] == ("v2", (4, 4, 4), ((10, 12, 12, 12), (12, 14, 14, 14))), r.tags)
    one = region(src, center={"core": (1, 2, 3), "jet": (4, 5, 6)},
                 size={"core": 10, "jet": (2, 2, 8)})
    check("single-file boxes carry a null label",
          one.tags[0] == ("core", (10, 10, 10), ((None, 1, 2, 3),)) and not one.tag_series,
          one.tags)
    check("size per tag", one.tags[1][1] == (2, 2, 8))
    t = region(src, track="f.csv", size={"a": 4, "b": (1, 2, 3)})
    check("track + size dict waits for the file",
          t.track == "f.csv" and t.tag_sizes == (("a", (4,)), ("b", (1, 2, 3))) and t.per_step)

    raises("bad tag", lambda: region(src, center={"a/b": (1, 2)}, size=1), ValueError, "tag")
    raises("leading dot", lambda: region(src, center={".hide": (1, 2)}, size=1), ValueError)
    raises("hash in tag", lambda: region(src, center={"v#1": (1, 2)}, size=1), ValueError)
    raises("tags differing by case",
           lambda: region(src, center={"V1": (1, 2), "v1": (3, 4)}, size=1), ValueError,
           "differ only in case")
    raises("file and series boxes mixed",
           lambda: region(src, center={"a": (1, 2), "b": {0: (1, 2)}}, size=1), ValueError,
           "not a mix")
    raises("tags of different dimension",
           lambda: region(src, center={"a": (1, 2), "b": (1, 2, 3)}, size=1), ValueError,
           "same number of coordinates")
    raises("size dict missing a tag",
           lambda: region(src, center={"a": (1, 2), "b": (3, 4)}, size={"a": 1}),
           ValueError, "no entry for tag(s) 'b'")
    raises("size dict with an extra tag",
           lambda: region(src, center={"a": (1, 2)}, size={"a": 1, "z": 2}),
           ValueError, "'z'")
    raises("size dict without tags",
           lambda: region(src, center={0: (1, 2)}, size={"a": 1}), ValueError, "named boxes")
    raises("empty timestep dict",
           lambda: region(src, center={"a": {}}, size=1), ValueError, "no centres")
    raises("bad centre type", lambda: region(src, center={"a": 3}, size=1), TypeError)
    raises("size still required", lambda: region(src, center={"a": (1, 2)}), ValueError,
           "needs size")


def test_sandbox():
    print("== sandbox ==")
    from vislang.runtime.sandbox import execute
    reset_sinks()
    execute("save(region(source('/d/s'), size=4, center={'v1': {10: (1.0, 2.0, 3.0)},"
            " 'v2': {12: (4.0, 5.0, 6.0)}}), 'out.vti')", form_namespace())
    r = collected_sinks()[0].upstream
    check("tag-keyed dict crosses the sandbox",
          isinstance(r, RegionNode) and track.tag_names(r) == ["v1", "v2"], r)
    reset_sinks()


def test_wire():
    print("== wire ==")
    reset_sinks()
    node = save(region(fields(source("/d/s"), ["rho"]), center=C, size=(4, 4, None)),
                "o.vti")
    back = from_plan_json(to_plan_json(node)).upstream
    check("named boxes round-trip", back.tags == node.upstream.tags, back)
    one = save(region(source("/d/f.h5"), center={"a": (1, 2, 3)}, size=2), "o.vti")
    check("single-file boxes round-trip",
          from_plan(to_plan(one)).upstream.tags == one.upstream.tags)
    unread = save(region(source("/d"), track="f.csv", size={"a": 4}), "o.vti")
    back = from_plan(to_plan(unread)).upstream
    check("unread track's tag sizes round-trip",
          back.tag_sizes == (("a", (4,)),) and back.track == "f.csv", back)

    plan = to_plan(node)
    def bad(edit, name, text=None):
        p = json.loads(json.dumps(plan))
        edit(p["chain"][2])
        raises(f"wire: {name}", lambda: from_plan(p), PlanValidationError, text)
    bad(lambda s: s["tags"][0].__setitem__(0, "../x"), "bad tag")
    bad(lambda s: s["tags"][1].__setitem__(0, "V1"), "case-duplicate tag", "twice")
    bad(lambda s: s["tags"][0][2].append([10, 1, 1, 1]), "duplicate label", "more than one")
    bad(lambda s: s["tags"][0][2][0].__setitem__(0, None), "mixed null label")
    bad(lambda s: s["tags"][0].__setitem__(1, [4, 4]), "size/centre mismatch", "entries")
    bad(lambda s: s["tags"][0][2][0].__setitem__(1, True), "bool coordinate")
    bad(lambda s: s.__setitem__("ranges", [["x", 0, 1]]), "ranges plus tags")
    bad(lambda s: s.__setitem__("centers", []), "tags plus centres")
    bad(lambda s: s.__setitem__("tags", []), "no tags")
    reset_sinks()


def test_track_file():
    print("== tagged track file ==")
    p = os.path.join(TMP, "tags.csv")
    with open(p, "w") as f:
        f.write("# two vortices\ntag,step,x,y,z\nv1,10,4,4,4\nv2,10,12,12,12\n"
                "v1,11,5,5,5\nv1,12,6,6,6\nv2,12,14,14,14\n")
    kind, rows, sha = track.read_track(p)
    check("tag,step header", kind == "tag_step" and len(rows) == 5 and sha)
    r = track.resolve_tracks([region(source("/d"), track=p, size=4)])[0]
    inline = region(source("/d"), center=C, size=4)
    check("track == inline centres", r.tags == inline.tags and r.track_sha == sha, r.tags)
    r = track.resolve_tracks([region(source("/d"), track=p, size={"v1": 2, "v2": 6})])[0]
    check("size per tag from a track", [s for _, s, _ in r.tags] == [(2, 2, 2), (6, 6, 6)])

    q = os.path.join(TMP, "one.csv")
    with open(q, "w") as f:
        f.write("tag,x,y,z\ncore,1,2,3\njet,4,5,6\n")
    r = track.resolve_tracks([region(source("/d/f.h5"), track=q, size=2)])[0]
    check("tag,x,y,z header: boxes on one file",
          r.tags[1] == ("jet", (2, 2, 2), ((None, 4.0, 5.0, 6.0),)) and not r.tag_series)
    raises("load_track refuses a tagged file", lambda: track.load_track(q), ValueError, "tag")
    for name, body, text in [
            ("duplicate tag+step", "tag,step,x,y\na,0,1,2\na,0,3,4\n", "more than one"),
            ("duplicate tag on a file", "tag,x,y\na,1,2\na,3,4\n", "more than one"),
            ("bad tag in a track", "tag,x,y\na/b,1,2\n", "tag"),
            ("header", "tag,t,x,y\na,0,1,2\n", "header")]:
        b = os.path.join(TMP, "bad_tags.csv")
        with open(b, "w") as f:
            f.write(body)
        raises(f"track: {name}",
               lambda: track.resolve_tracks([region(source("/d"), track=b, size=1)]),
               ValueError, text)
    u = os.path.join(TMP, "untagged.csv")
    with open(u, "w") as f:
        f.write("step,x,y,z\n10,1,2,3\n")
    raises("size dict on an untagged track",
           lambda: track.resolve_tracks([region(source("/d"), track=u, size={"a": 1})]),
           ValueError, "no `tag` column")
    raises("size dict missing a track's tag",
           lambda: track.resolve_tracks([region(source("/d"), track=p, size={"v1": 1})]),
           ValueError, "'v2'")


def test_grid_series():
    print("== grid series: one series per tag ==")
    d = grid_series()
    out = os.path.join(TMP, "roi")
    reset_sinks()
    res = run(save(region(source(d), center=C, size=4), out + ".vti"),
              "save(region(source(d), center=C, size=4), 'roi.vti')")
    check("output is the folder", res["output"] == out, res["output"])
    check("tags reported with their timesteps",
          res["tags"] == {"v1": [10, 11, 12], "v2": [10, 12]}, res.get("tags"))
    check("each tag its own series, v2 with a gap",
          sorted(os.listdir(os.path.join(out, "v1"))) ==
          [".sieve-provenance.yaml", "timestep#10.vti", "timestep#11.vti", "timestep#12.vti"]
          and sorted(os.listdir(os.path.join(out, "v2"))) ==
          [".sieve-provenance.yaml", "timestep#10.vti", "timestep#12.vti"],
          os.listdir(out))
    import pyvista as pv
    ok = True
    for tag, by_t in C.items():
        for t, c in by_t.items():
            m = pv.read(os.path.join(out, tag, f"timestep#{t}.vti"))
            lo = [ci - 2 for ci in c]
            ok &= tuple(m.dimensions) == (4, 4, 4)
            ok &= float(m.point_data["rho"][0]) == first_cell(lo, t)
            ok &= float(m.field_data["TimeValue"][0]) == float(t)
    check("every box placed at its own centre, timed by its timestep", ok)
    from vislang.formats.inspect import timestep_files
    check("a tag folder reads back as a timeseries",
          [lab for lab, _ in timestep_files(os.path.join(out, "v2"))] == [10, 12])
    raises("the root says which folders to source",
           lambda: timestep_files(out), ValueError, "v1")

    pvd = open(os.path.join(out, "roi.pvd")).read()
    rows = re.findall(r'timestep="(\d+)" part="(\d)" name="(\w+)" file="([^"]+)"', pvd)
    check("ParaView index: a block per tag, a box only where it exists",
          rows == [("10", "0", "v1", "v1/timestep#10.vti"),
                   ("10", "1", "v2", "v2/timestep#10.vti"),
                   ("11", "0", "v1", "v1/timestep#11.vti"),
                   ("12", "0", "v1", "v1/timestep#12.vti"),
                   ("12", "1", "v2", "v2/timestep#12.vti")], rows)

    rec = provenance.record_for(os.path.join(out, "v2"))
    resolved = rec["logical"]["resolved"]
    check("record names its tag", resolved["region_tag"] == "v2", resolved)
    check("record holds only its own boxes",
          sorted(resolved["region_boxes"]) == [10, 12]
          and resolved["region_boxes"][12]["box"]["x"] == [12, 16], resolved)
    check("record fingerprints only its own inputs",
          sorted(rec["logical"]["input"]["timestep_fingerprints"]) == [10, 12])
    expl = rec["logical"]["spec_explanation"]
    check("explanation names the box and its siblings",
          "keep the box named v2" in expl and "the others: v1" in expl, expl)
    step_rec = provenance.record_for(os.path.join(out, "v1", "timestep#11.vti"))
    check("each file embeds its tag's record",
          step_rec["logical"]["resolved"]["region_tag"] == "v1")

    # A track file gives the identical result, and its hash rides along.
    p = os.path.join(TMP, "tags.csv")
    out2 = os.path.join(TMP, "roi_track")
    reset_sinks()
    run(save(region(source(d), track=p, size=4), out2 + ".vti"))
    same = all(np.array_equal(pv.read(os.path.join(out, tag, f"timestep#{t}.vti"))["rho"],
                              pv.read(os.path.join(out2, tag, f"timestep#{t}.vti"))["rho"])
               for tag, by_t in C.items() for t in by_t)
    check("track file == inline centres", same)
    rec2 = provenance.record_for(os.path.join(out2, "v1"))
    check("record: track path + its boxes",
          rec2["logical"]["resolved"]["region_track"]["path"] == p
          and "keep the box named v1" in rec2["logical"]["spec_explanation"]
          and "each of its 3 timesteps here" in rec2["logical"]["spec_explanation"],
          rec2["logical"]["spec_explanation"])

    # timesteps() skips a tag with no box in range; its timesteps are not read.
    out3 = os.path.join(TMP, "roi_sel")
    reset_sinks()
    res = plan_pipeline(save(region(timesteps(source(d), 11, 11), center=C, size=4),
                             out3 + ".npz"))
    check("tag outside the selection skipped",
          res["tags"] == {"v1": [11]} and os.listdir(out3) == ["v1"], res.get("tags"))
    check("skip is reported", any("'v2'" in s and "nothing written" in s
                                  for s in res["steps"]), res["steps"])

    # Each record says what IT holds: a per-timestep file its own box, the
    # folder only the timesteps written.
    out4 = os.path.join(TMP, "roi_two")
    reset_sinks()
    run(save(region(timesteps(source(d), 10, 11), center=C, size=4), out4 + ".npz"))
    one = provenance.record_for(os.path.join(out4, "v1", "timestep#11.npz"))["logical"]
    check("per-timestep record: its own centre and key",
          "keep the box named v1 at #11: size (4, 4, 4) centred at (5, 5, 5)"
          in one["spec_explanation"] and "resolved.region_boxes" in one["spec_explanation"]
          and list(one["resolved"]["region_boxes"]) == [11], one["spec_explanation"])
    folder = provenance.record_for(os.path.join(out4, "v1"))["logical"]["spec_explanation"]
    check("folder record counts only what it holds", "each of its 2 timesteps" in folder,
          folder)

    # Dry run: every box listed, priced as boxes, nothing read.
    reset_sinks()
    res = plan_pipeline(save(region(source(d), center=C, size=4),
                             os.path.join(TMP, "dry.vti")), dry_run=True)
    text = "\n".join(res["steps"])
    check("dry run lists boxes per tag",
          "v2 (2 timestep(s)):" in text and "#12: x:(12,16)" in text, text)
    check("dry run prices boxes", "named boxes: 5" in text and not res["materialized"])


def test_reviewed_edges():
    print("== edges: changing grids, mixed regions, one sample per file ==")
    # A series whose grid changes: each box is checked against its own file.
    d = os.path.join(TMP, "growing")
    os.makedirs(d, exist_ok=True)
    for t, n in ((1, 16), (2, 32)):
        with h5py.File(os.path.join(d, f"s#{t}.hdf5"), "w") as f:
            f["rho"] = np.arange(n ** 3, dtype=np.float32).reshape(n, n, n)
    for name, node in [
            ("unnamed", region(source(d), center={1: (8, 8, 8), 2: (24, 24, 24)}, size=4)),
            ("named", region(source(d), center={"a": {1: (8, 8, 8), 2: (24, 24, 24)}},
                             size=4))]:
        out = os.path.join(TMP, f"grow_{name}")
        reset_sinks()
        plan_pipeline(save(node, out + ".npz"))
        path = os.path.join(out, *(("a",) if name == "named" else ()), "timestep#2.npz")
        with np.load(path) as z:
            check(f"changing grid, {name} box cut from its own file",
                  z["rho"].shape == (4, 4, 4) and float(z["rho"][0, 0, 0])
                  == 22 * 1024 + 22 * 32 + 22, float(z["rho"][0, 0, 0]))

    # An unnamed moving box beside named ones keeps its own strict rules.
    g = grid_series()
    reset_sinks()
    raises("unnamed box's unknown timestep still refused",
           lambda: plan_pipeline(save(region(region(
               source(g), center={10: (8, 8, 8), 11: (8, 8, 8), 12: (8, 8, 8),
                                  99: (8, 8, 8)}, size=10),
               center={"a": {10: (8, 8, 8)}}, size=4), os.path.join(TMP, "mix.npz"))),
           ValueError, "#99")
    reset_sinks()
    raises("unnamed box's missing timestep still refused",
           lambda: plan_pipeline(save(region(region(
               source(g), center={10: (8, 8, 8)}, size=10),
               center={"a": {10: (8, 8, 8), 11: (8, 8, 8)}}, size=4),
               os.path.join(TMP, "mix2.npz"))),
           ValueError, "#11")

    # A random subsample written before the boxes samples the file once.
    from vislang.dsl.forms import subsample
    p = particle_series("sampled", labels=(1,))
    reads = []
    orig = load._read
    load._read = lambda *a, **k: reads.append(1) or orig(*a, **k)
    try:
        reset_sinks()
        plan_pipeline(save(region(subsample(source(p, positions=("x", "y", "z")), 0.5),
                                  center={"a": {1: (50.0, 50.0, 50.0)},
                                          "b": {1: (50.0, 50.0, 50.0)}}, size=30),
                           os.path.join(TMP, "sampled_out.npz")))
    finally:
        load._read = orig
    with np.load(os.path.join(TMP, "sampled_out", "a", "timestep#1.npz")) as a, \
            np.load(os.path.join(TMP, "sampled_out", "b", "timestep#1.npz")) as b:
        check("one sample per file: identical boxes, identical rows",
              a["mass"].size > 0 and np.array_equal(a["mass"], b["mass"]))
    check("one sample per file: one read", len(reads) == 1, reads)


def test_refusals_before_read():
    print("== refusals, before any read ==")
    d = grid_series()
    calls = []
    orig_m, orig_mb = planner.materialize, planner.materialize_boxes
    planner.materialize = lambda *a, **k: calls.append(1) or orig_m(*a, **k)
    planner.materialize_boxes = lambda *a, **k: calls.append(1) or orig_mb(*a, **k)
    o = os.path.join(TMP, "never.npz")
    try:
        cases = [
            ("timestep with no file",
             region(source(d), center={"a": {10: (4, 4, 4), 99: (4, 4, 4)}}, size=4), "#99"),
            ("no tag in the selection",
             region(timesteps(source(d), 11, 11), center={"a": {10: (4, 4, 4)}}, size=4),
             "no tag has a box"),
            ("a box entirely outside the grid",
             region(source(d), center={"a": {10: (4, 4, 4)}, "b": {12: (40, 4, 4)}}, size=4),
             "box 'b'"),
            ("one centre per tag on a folder",
             region(source(d), center={"a": (4, 4, 4)}, size=4), "needs its timesteps"),
            ("timesteps on a single file",
             region(source(os.path.join(d, "snap#10.hdf5")), center={"a": {10: (4, 4, 4)}},
                    size=4), "single file"),
            ("two named-box regions",
             region(region(source(d), center={"a": {10: (4, 4, 4)}}, size=8),
                    center={"b": {10: (4, 4, 4)}}, size=2), "only one region"),
        ]
        for name, node, text in cases:
            reset_sinks()
            raises(name, lambda: plan_pipeline(save(node, o)), Exception, text)
        reset_sinks()
        raises("render refused",
               lambda: plan_pipeline(render(region(source(os.path.join(d, "snap#10.hdf5")),
                                                   center={"a": (4, 4, 4), "b": (8, 8, 8)},
                                                   size=4))),
               NotImplementedError, "render a plain region")
        check("nothing was read", not calls, calls)
    finally:
        planner.materialize, planner.materialize_boxes = orig_m, orig_mb
    reset_sinks()


def test_single_file():
    print("== single file: one output per tag ==")
    d = grid_series()
    f = os.path.join(d, "snap#10.hdf5")
    out = os.path.join(TMP, "one")
    reset_sinks()
    res = run(save(region(source(f), center={"core": (4, 4, 4), "jet": (15, 8, 8)},
                          size={"core": 4, "jet": (4, 2, 6)}), out + ".vti"))
    check("one file per tag + index",
          sorted(os.listdir(out)) == ["core.vti", "jet.vti", "one.pvd"], os.listdir(out))
    import pyvista as pv
    jet = pv.read(os.path.join(out, "jet.vti"))
    check("per-tag size, clipped at the grid edge",
          tuple(jet.dimensions) == (3, 2, 6), jet.dimensions)
    check("clip reported for the box", any("box 'jet'" in s and "clipped" in s
                                           for s in res["steps"]), res["steps"])
    pvd = open(os.path.join(out, "one.pvd")).read()
    check("index: one block per tag, no timesteps",
          'part="0" name="core" file="core.vti"' in pvd
          and 'part="1" name="jet"' in pvd and "timestep=" not in pvd, pvd)
    rec = provenance.record_for(os.path.join(out, "jet.vti"))
    r = rec["logical"]["resolved"]
    check("record: the tag and its one box",
          r["region_tag"] == "jet" and r["region_box"]["box"]["x"] == [13, 16]
          and r["region_box"]["clipped"] == ["x"], r)
    check("explanation: a box on one file",
          "keep the box named jet: size (4, 2, 6) centred at (15, 8, 8)"
          in rec["logical"]["spec_explanation"], rec["logical"]["spec_explanation"])

    # Preserving the source format: no index for a format ParaView cannot list.
    out2 = os.path.join(TMP, "one_h5")
    reset_sinks()
    plan_pipeline(save(region(source(f), center={"a": (4, 4, 4), "b": (8, 8, 8)}, size=2),
                       out2))
    check("format preserved per tag, no index",
          sorted(os.listdir(out2)) == ["a.hdf5", "b.hdf5"], os.listdir(out2))


def test_equivalence_and_shared_read():
    print("== same boxes as unnamed regions; one read per file ==")
    d = particle_series()
    pos = ("x", "y", "z")
    c = {"near": {1: (20.0, 20.0, 20.0), 2: (25.0, 25.0, 25.0)},
         "far": {1: (70.0, 70.0, 70.0), 2: (60.0, 60.0, 60.0)}}
    reads = []
    orig = load._read
    load._read = lambda *a, **k: reads.append(a[0].filepath) or orig(*a, **k)
    try:
        reset_sinks()
        res = plan_pipeline(save(region(threshold(source(d, positions=pos), "mass > 100"),
                                        center=c, size=20),
                                 os.path.join(TMP, "pts.npz")))
    finally:
        load._read = orig
    check("point data: each file read once for both boxes",
          len(reads) == 2 and len(set(reads)) == 2, reads)
    check("estimate says the read is shared",
          any("read once" in s for s in res["steps"]), res["steps"])
    for tag, by_t in c.items():
        reset_sinks()
        plan_pipeline(save(region(threshold(source(d, positions=pos), "mass > 100"),
                                  center=by_t, size=20),
                           os.path.join(TMP, f"pts_{tag}.npz")))
        same = True
        for t in by_t:
            with np.load(os.path.join(TMP, "pts", tag, f"timestep#{t}.npz")) as a, \
                    np.load(os.path.join(TMP, f"pts_{tag}", f"timestep#{t}.npz")) as b:
                same &= sorted(a.files) == sorted(b.files) and all(
                    np.array_equal(a[k], b[k]) for k in a.files)
                same &= a["x"].size > 0
        check(f"tag {tag!r} == the same centres as an unnamed region", same)

    g = grid_series()
    info = __import__("vislang.formats.inspect", fromlist=["x"]).inspect_file(
        os.path.join(g, "snap#10.hdf5"))
    check("strided grid reader keeps a read per box", not load.shares_read(info))


# ---------------------------------------------------------------------------
# The simulated remote (as in test_remote_reduce, with a recursive dir pull)
# ---------------------------------------------------------------------------
EXECS, MANIFESTS = [], []


def _install_fake_remote():
    from vislang.remote import reduce
    from vislang.remote.download import Connection
    from vislang.formats import inspect

    def fake_establish(remote_source):
        return Connection(user="u", host="fakehost", target="u@fakehost", batch_ok=True)

    def fake_stat(conn, path):
        st = os.stat(path)
        return int(st.st_size), int(st.st_mtime)

    def fake_header_hash(conn, path, nbytes=65536):
        import hashlib
        with open(path, "rb") as f:
            return hashlib.md5(f.read(nbytes)).hexdigest()

    def fake_run_remote(conn, command, stdin_bytes=None, timeout=None):
        if command.startswith("rm -"):
            for tok in command.split()[2:]:
                shutil.rmtree(tok, ignore_errors=True)
                try:
                    os.remove(tok)
                except OSError:
                    pass
            return 0, "", ""
        if "cat >" in command:
            rpath = command.split("cat >", 1)[1].strip().split()[0]
            os.makedirs(os.path.dirname(rpath) or ".", exist_ok=True)
            with open(rpath, "wb") as f:
                f.write(stdin_bytes)
            if "manifest" in rpath:
                MANIFESTS.append(json.loads(stdin_bytes.decode()))
            return 0, "", ""
        if "vislang_exec.py" in command:
            flag = "--outdir" if "--outdir" in command else "--out"
            rout = command.split(flag, 1)[1].strip().split()[0]
            extra = []
            if "--manifest" in command:
                extra = ["--manifest", command.split("--manifest", 1)[1].strip().split()[0]]
            EXECS.append(json.loads(stdin_bytes.decode()))
            r = subprocess.run([PY, EXEC, "--stdin", flag, rout, *extra],
                               input=stdin_bytes, capture_output=True, cwd=REPO)
            return r.returncode, r.stdout.decode(errors="replace"), r.stderr.decode(errors="replace")
        return 1, "", f"unexpected remote command: {command}"

    def fake_transfer(conn, remote_path, local_path, size_warn_mb=500):
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        shutil.copyfile(remote_path, local_path)
        return local_path

    def fake_transfer_dir(conn, remote_dir, local_dir):
        shutil.copytree(remote_dir, local_dir, dirs_exist_ok=True)   # rsync -a recurses
        return local_dir

    reduce.establish_connection = fake_establish
    reduce.remote_stat = fake_stat
    reduce.remote_header_hash = fake_header_hash
    reduce.run_remote = fake_run_remote
    reduce.transfer = fake_transfer
    reduce.transfer_dir = fake_transfer_dir
    reduce.measure_bandwidth = lambda conn: None
    reduce.session_check = lambda uri: None

    def local_path(uri):
        return reduce._parse_remote(reduce._normalize_remote(uri))[2]

    def fake_files_stat(uri):
        path, base = local_path(uri), uri.rstrip("/")
        out = []
        for name in os.listdir(path):
            m = re.search(r"#(\d+)", name)
            p = os.path.join(path, name)
            if m and os.path.isfile(p):
                st = os.stat(p)
                out.append((int(m.group(1)), f"{base}/{name}", int(st.st_size),
                            int(st.st_mtime)))
        return sorted(out)

    inspect.remote_is_dir = lambda uri: os.path.isdir(local_path(uri))
    inspect.remote_timestep_files_stat = fake_files_stat
    # The login-node inspect behind `estimate`: metadata of the "remote" file.
    inspect._inspect_remote = (lambda uri, positions=None, allow_fetch=True:
                               inspect.inspect_file(local_path(uri), positions=positions))


def test_remote():
    print("== remote: single file, catalog folder, plain folder batch ==")
    _install_fake_remote()
    os.environ["VISLANG_CACHE"] = os.path.join(TMP, "cache")
    os.environ["VISLANG_REMOTE"] = "force"
    os.environ["VISLANG_REMOTE_TMP"] = os.path.join(TMP, "remote_tmp")
    try:
        d = grid_series("rseries")
        f = os.path.join(d, "snap#10.hdf5")
        boxes = {"core": (4, 4, 4), "jet": (12, 12, 12)}

        # -- single file: ONE job cuts every box -----------------------------
        n0 = len(EXECS)
        reset_sinks()
        res = run(save(region(fields(source(f"u@fakehost:{f}"), ["rho"]), center=boxes,
                              size=4), os.path.join(TMP, "r_one.npz")))
        check("remote file: one job for both boxes", len(EXECS) == n0 + 1, len(EXECS) - n0)
        check("remote file: manifest names each box",
              MANIFESTS[-1] == [{"label": None, "tag": "core", "vars": ["rho"]},
                                {"label": None, "tag": "jet", "vars": ["rho"]}],
              MANIFESTS[-1])
        with np.load(os.path.join(TMP, "r_one", "jet.npz")) as z:
            check("remote file: box correct",
                  z["rho"].shape == (4, 4, 4) and float(z["rho"][0, 0, 0])
                  == first_cell((10, 10, 10), 10))
        check("remote file: record per tag",
              provenance.record_for(os.path.join(TMP, "r_one", "core.npz"))
              ["logical"]["resolved"]["region_box"]["box"]["x"] == [2, 6])
        reset_sinks()
        res = plan_pipeline(save(region(fields(source(f"u@fakehost:{f}"), ["rho"]),
                                        center={"renamed": (4, 4, 4), "jet": (12, 12, 12)},
                                        size=4), os.path.join(TMP, "r_one2.npz")))
        check("renaming a tag reuses its extent: no job",
              len(EXECS) == n0 + 1 and any("full hit" in s for s in res["steps"]),
              res["steps"])
        check("the renamed output exists",
              sorted(os.listdir(os.path.join(TMP, "r_one2"))) == ["jet.npz", "renamed.npz"])

        # -- catalog folder: (timestep, tag) units in one job ---------------
        n0 = len(EXECS)
        reset_sinks()
        res = run(save(region(fields(source(f"u@fakehost:{d}"), ["rho"]), center=C,
                              size=4), os.path.join(TMP, "r_series.vti")))
        check("remote folder: one job for every box", len(EXECS) == n0 + 1)
        check("remote folder: manifest lists (timestep, tag) pieces",
              MANIFESTS[-1] == [{"label": 10, "tag": "v1", "vars": ["rho"]},
                                {"label": 10, "tag": "v2", "vars": ["rho"]},
                                {"label": 11, "tag": "v1", "vars": ["rho"]},
                                {"label": 12, "tag": "v1", "vars": ["rho"]},
                                {"label": 12, "tag": "v2", "vars": ["rho"]}],
              MANIFESTS[-1])
        check("remote folder: tags reported", res.get("tags") == {"v1": [10, 11, 12],
                                                                  "v2": [10, 12]}, res)
        import pyvista as pv
        root = os.path.join(TMP, "r_series")
        m = pv.read(os.path.join(root, "v2", "timestep#12.vti"))
        check("remote folder: box correct + timed",
              float(m["rho"][0]) == first_cell((12, 12, 12), 12)
              and float(m.field_data["TimeValue"][0]) == 12.0)
        check("remote folder: ParaView index", os.path.exists(os.path.join(root, "r_series.pvd")))
        rec = provenance.record_for(os.path.join(root, "v1"))
        check("remote folder: record per tag",
              rec["logical"]["resolved"]["region_tag"] == "v1"
              and sorted(rec["logical"]["resolved"]["region_boxes"]) == [10, 11, 12], rec)
        reset_sinks()
        res = plan_pipeline(save(region(fields(source(f"u@fakehost:{d}"), ["rho"]), center=C,
                                        size=4), os.path.join(TMP, "r_series2.vti")))
        check("remote folder: re-run is a full catalog hit",
              len(EXECS) == n0 + 1 and any("full hit" in s for s in res["steps"]),
              res["steps"])

        # -- plain folder batch (no fields): the remote runs the same planner --
        n0 = len(EXECS)
        reset_sinks()
        res = plan_pipeline(save(region(source(f"u@fakehost:{d}"), center=C, size=4),
                                 os.path.join(TMP, "r_batch.vti")))
        root = os.path.join(TMP, "r_batch")
        check("batch: one job", len(EXECS) == n0 + 1)
        check("batch: the extension converts, the folder drops it",
              res["output"] == root and sorted(os.listdir(root)) ==
              ["r_batch.pvd", "v1", "v2"], (res["output"], os.listdir(TMP)))
        check("batch: tag folders pulled whole",
              sorted(glob.glob(os.path.join(root, "v2", "*.vti"))) ==
              [os.path.join(root, "v2", f"timestep#{t}.vti") for t in (10, 12)])
        check("batch: tags reported", res.get("tags") == {"v1": [10, 11, 12], "v2": [10, 12]},
              res.get("tags"))

        # -- estimate: priced per box, nothing shipped ----------------------
        n0 = len(EXECS)
        reset_sinks()
        res = plan_pipeline(save(region(fields(source(f"u@fakehost:{d}"), ["rho"]), center=C,
                                        size=4), os.path.join(TMP, "r_est.vti")),
                            dry_run=True)
        text = "\n".join(res["steps"])
        check("remote estimate: boxes listed and priced, nothing shipped",
              len(EXECS) == n0 and "named boxes: 5" in text and "#12: x:(12,16)" in text,
              text)
    finally:
        for k in ("VISLANG_CACHE", "VISLANG_REMOTE", "VISLANG_REMOTE_TMP"):
            os.environ.pop(k, None)
    reset_sinks()


def test_paraview():
    """Open the index in ParaView itself, when it is installed: the claim the
    .pvd exists for is that a box absent at a timestep is absent there."""
    print("== ParaView ==")
    pv = os.environ.get("VISLANG_PVPYTHON") or next(iter(sorted(glob.glob(
        "/Applications/ParaView*.app/Contents/bin/pvpython"))), None)
    if not pv or not os.path.exists(pv):
        print("  skip ParaView not found (set VISLANG_PVPYTHON)")
        return
    probe = os.path.join(TMP, "probe.py")
    with open(probe, "w") as f:
        f.write("import sys, json\nfrom paraview.simple import PVDReader\n"
                "from paraview import servermanager as sm\n"
                "r = PVDReader(FileName=sys.argv[1])\nout = {}\n"
                "for t in r.TimestepValues:\n"
                "    r.UpdatePipeline(t)\n    d = sm.Fetch(r)\n"
                "    out[int(t)] = [d.GetMetaData(i).Get(d.NAME()) for i in range(d.GetNumberOfBlocks())"
                " if d.GetBlock(i) is not None and d.GetBlock(i).GetNumberOfBlocks()]\n"
                "print('RESULT ' + json.dumps(out))\n")
    r = subprocess.run([pv, probe, os.path.join(TMP, "roi", "roi.pvd")],
                       capture_output=True, text=True, timeout=300)
    line = next((l for l in r.stdout.splitlines() if l.startswith("RESULT ")), None)
    got = json.loads(line[7:]) if line else None
    check("ParaView: v2 absent at #11, present at #10 and #12",
          got is not None and [len(got[k]) for k in ("10", "11", "12")] == [2, 1, 2]
          and all("v1" in n for n in got["11"]), r.stdout[-2000:] + r.stderr[-2000:])


if __name__ == "__main__":
    test_form()
    test_sandbox()
    test_wire()
    test_track_file()
    test_grid_series()
    test_reviewed_edges()
    test_refusals_before_read()
    test_single_file()
    test_equivalence_and_shared_read()
    test_remote()
    test_paraview()
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{len(PASS)} checks passed")
