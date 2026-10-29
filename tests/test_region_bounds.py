"""Boxes given by their bounds: region(node, bounds=…).

Plain-python asserts (no pytest on the cluster). Run from the repo root:
    python tests/test_region_bounds.py

Covers: the form (every shape, every refusal, and mixing centre and bounds
tags) and the sandbox; the wire round-trip and its strict validator; min/max
track files (every header, the wrong header for the keyword, bad rows);
equivalence with the fixed box x=(lo, hi), … at every timestep — points, a grid
in cells, and a grid with geometry in world coordinates — including a particle
exactly on a bound, open-ended bounds and whole axes; clipping on a grid and a
box entirely outside it; coverage refused before any read; named boxes written
one series per tag (gaps, the ParaView index, TimeValue); the dry-run text and
the provenance record; the simulated remote (the transport is faked; the plan,
executor, catalog and planner are the real code); and the spec that motivated
the form.
"""

import json
import os
import re
import sys
import tempfile

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["VISLANG_NO_BINDING"] = "1"
os.environ["VISLANG_REMOTE_REPO"] = REPO

import h5py
import pyvista as pv

from test_region_tags import _install_fake_remote, EXECS, MANIFESTS
from vislang.dsl import reset_sinks, collected_sinks, form_namespace
from vislang.dsl.forms import source, region, fields, save, timesteps
from vislang.dsl.nodes import RegionNode
from vislang.dsl.ast_serialize import (to_plan, from_plan, to_plan_json,
                                       from_plan_json, describe_plan,
                                       PlanValidationError)
from vislang.interpreter import track, load
from vislang.interpreter.planner import plan_pipeline
from vislang.runtime import provenance

TMP = tempfile.mkdtemp(prefix="vislang_bounds_test_")
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


def run(sink_node, dry_run=False):
    reset_sinks()
    with provenance.run("spec.py", spec_code="# test\n"):
        return plan_pipeline(sink_node, dry_run=dry_run)


def grid_series(name="gseries", labels=(10, 11, 12), shape=(16, 16, 16)):
    """rho[i,j,k] = 256 i + 16 j + k + t: HDF5, no geometry (cells)."""
    d = os.path.join(TMP, name)
    os.makedirs(d, exist_ok=True)
    for t in labels:
        a = np.arange(int(np.prod(shape)), dtype=np.float32).reshape(shape) + t
        with h5py.File(os.path.join(d, f"snap#{t}.hdf5"), "w") as f:
            f.create_dataset("rho", data=a)
    return d


ORIGIN, SPACING, DIMS = (100.0, -4.0, 2.0), (0.5, 2.0, 1.0), (12, 10, 8)


def vti_series(name="vseries", labels=(0, 1)):
    """A uniform grid that states where it sits (world coordinates)."""
    d = os.path.join(TMP, name)
    os.makedirs(d, exist_ok=True)
    for t in labels:
        img = pv.ImageData(dimensions=DIMS, origin=ORIGIN, spacing=SPACING)
        img.point_data["rho"] = (np.arange(int(np.prod(DIMS)), dtype=np.float32)
                                 + 1000 * t)
        img.save(os.path.join(d, f"snap#{t}.vti"))
    return d


def particle_series(name="pseries", labels=(1, 2), n=3000):
    """Points in [0, 100)³, plus a few exactly on the bounds 10 and 20 on x."""
    d = os.path.join(TMP, name)
    os.makedirs(d, exist_ok=True)
    rng = np.random.default_rng(3)
    for t in labels:
        x = np.concatenate([rng.uniform(0, 100, n), [10.0, 20.0, 9.999, 20.001]])
        y = np.concatenate([rng.uniform(0, 100, n), [15.0] * 4])
        z = np.concatenate([rng.uniform(0, 100, n), [15.0] * 4])
        with h5py.File(os.path.join(d, f"halo#{t}.hdf5"), "w") as f:
            f.create_dataset("x", data=x)
            f.create_dataset("y", data=y)
            f.create_dataset("z", data=z)
            f.create_dataset("mass", data=np.arange(x.size, dtype=np.float64) + t)
    return d


def same_npz(a_path, b_path):
    with np.load(a_path) as a, np.load(b_path) as b:
        keys = sorted(k for k in a.files if not k.startswith("sieve_"))
        return (keys == sorted(k for k in b.files if not k.startswith("sieve_"))
                and all(np.array_equal(a[k], b[k]) for k in keys))


def write(name, text):
    p = os.path.join(TMP, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


# ---------------------------------------------------------------------------
def test_form():
    print("== form ==")
    s = source("/d/series")
    one = region(s, bounds=((10, 50), (0, 40), (5, 25)))
    check("one box is the fixed box x=, y=, z= writes",
          one == region(s, x=(10, 50), y=(0, 40), z=(5, 25)), one)
    check("open bounds kept, a whole axis left out",
          region(s, bounds=((500, None), (None, None), (770, 790))).ranges
          == (("x", 500, None), ("z", 770, 790)))
    check("2-D", region(s, bounds=((1, 2), (3, 4))).ranges == (("x", 1, 2), ("y", 3, 4)))
    r = region(s, bounds=[(5, (1, 2), (3, 4), (5, 6)), (3, (0, 1), (0, 1), (0, 1))])
    check("rows sorted by label, per_step, moves",
          r.step_ranges == ((3, (("x", 0, 1), ("y", 0, 1), ("z", 0, 1))),
                            (5, (("x", 1, 2), ("y", 3, 4), ("z", 5, 6))))
          and r.per_step and r.moves and not r.tagged, r)
    t = region(s, bounds={"halo": [(3, (1, 2), (1, 2), (1, 2))],
                          "field": ((0, 9), (0, 9), (0, 9))})
    check("named boxes, written order",
          track.tag_names(t) == ["halo", "field"] and t.tagged and t.tag_series
          and not t.per_step and t.tag_ranges[1][1] == ((None, (("x", 0, 9), ("y", 0, 9),
                                                               ("z", 0, 9))),), t)
    still = region(s, bounds={"a": ((0, 1), (0, 1))})
    check("named boxes that stay put need no series", still.tagged and not still.tag_series)
    csv = region(s, bounds="b.csv")
    check("a min/max file waits for the planner",
          csv.track == "b.csv" and csv.track_kind == "bounds" and csv.track_pending
          and csv.per_step)

    raises("size= with bounds=", lambda: region(s, bounds=((0, 1), (0, 1)), size=2),
           ValueError, "takes no size")
    raises("axis ranges with bounds=",
           lambda: region(s, bounds=((0, 1), (0, 1)), x=(0, 1)), ValueError, "not both")
    raises("lo >= hi", lambda: region(s, bounds=((5, 5), (0, 1))), ValueError, "lo < hi")
    raises("a bool bound", lambda: region(s, bounds=((True, 5), (0, 1))), TypeError)
    raises("a NaN bound", lambda: region(s, bounds=((float("nan"), 5), (0, 1))),
           TypeError, "finite")
    raises("every axis whole", lambda: region(s, bounds=((None, None), (None, None))),
           ValueError, "whole domain")
    raises("one axis is no box", lambda: region(s, bounds=[(1, (0, 1))]), ValueError,
           "two or three")
    raises("2-D and 3-D mixed",
           lambda: region(s, bounds=[(1, (0, 1), (0, 1)), (2, (0, 1), (0, 1), (0, 1))]),
           ValueError, "2-D vs 3-D")
    raises("2-D and 3-D across tags",
           lambda: region(s, bounds={"a": ((0, 1), (0, 1)), "b": ((0, 1), (0, 1), (0, 1))}),
           ValueError, "2-D vs 3-D")
    raises("duplicate timestep",
           lambda: region(s, bounds=[(1, (0, 1), (0, 1)), (1, (2, 3), (2, 3))]),
           ValueError, "more than one box")
    raises("a non-integer step", lambda: region(s, bounds=[(1.5, (0, 1), (0, 1))]),
           TypeError, "integer")
    raises("a {timestep: box} dict", lambda: region(s, bounds={3: ((0, 1), (0, 1))}),
           TypeError, "list of rows")
    raises("pairs and rows mixed",
           lambda: region(s, bounds=[(1, (0, 1), (0, 1)), (0, 1)]), TypeError)
    raises("a bad tag", lambda: region(s, bounds={"a/b": ((0, 1), (0, 1))}), ValueError,
           "tag")
    raises("no boxes", lambda: region(s, bounds={}), ValueError, "no boxes")

    # centre tags and bounds tags in one region
    m = region(s, center={"probe": (3, 3, 3)}, size=2,
               bounds={"halo": [(1, (0, 1), (0, 1), (0, 1))]})
    check("mixed: centre tags first, then bounds tags",
          track.tag_names(m) == ["probe", "halo"] and m.tags[0][1] == (2, 2, 2), m)
    raises("mixed: a tag in both",
           lambda: region(s, center={"a": (1, 2, 3)}, size=2,
                          bounds={"A": ((0, 1), (0, 1), (0, 1))}), ValueError, "both")
    raises("mixed: center= not a dict",
           lambda: region(s, center=(1, 2, 3), size=2,
                          bounds={"b": ((0, 1), (0, 1), (0, 1))}), ValueError, "{tag: …}")
    raises("mixed: a file on either side",
           lambda: region(s, center={"a": (1, 2, 3)}, size=2, bounds="b.csv"),
           ValueError, "track file is given alone")
    raises("mixed: size= naming a bounds tag",
           lambda: region(s, center={"a": (1, 2, 3)}, size={"a": 2, "b": 2},
                          bounds={"b": ((0, 1), (0, 1), (0, 1))}), ValueError,
           "needs no size")
    raises("track= with bounds=", lambda: region(s, bounds="b.csv", track="c.csv"),
           ValueError, "bounds=")


def test_sandbox():
    print("== sandbox ==")
    from vislang.runtime.sandbox import execute
    reset_sinks()
    execute("save(region(source('/d/s'), bounds={'halo': [(3, (515, 535), (164.5, 184.5),"
            " (771.4, 791.4))], 'field': ((460, 520), (130, 170), (740, 780))}), 'b.vtp')",
            form_namespace())
    r = collected_sinks()[0].upstream
    check("bounds cross the sandbox",
          isinstance(r, RegionNode) and track.tag_names(r) == ["halo", "field"], r)
    reset_sinks()


def test_wire():
    print("== wire ==")
    reset_sinks()
    nodes = {
        "rows": region(source("/d/s"), bounds=[(1, (0, 1), (None, 4), (2, 3)),
                                               (2, (1.5, 2.5), (0, 4), (2, 3))]),
        "tags": region(source("/d/s"), bounds={"a": [(1, (0, 1), (0, 1))],
                                               "b": ((0, 9), (0, 9))}),
        "mixed": region(source("/d/s"), center={"c": (1, 2)}, size=2,
                        bounds={"b": ((0, 9), (0, 9))}),
        "unread": region(source("/d/s"), bounds="b.csv", coords="cells"),
    }
    for name, n in nodes.items():
        sink = save(n, "o.vti")
        back = from_plan_json(to_plan_json(sink)).upstream
        check(f"round trip: {name}", back == n, (back, n))
    reset_sinks()
    plan = to_plan(save(nodes["rows"], "o.vti"))
    tplan = to_plan(save(nodes["mixed"], "o.vti"))

    def bad(p, edit, name, text=None):
        p = json.loads(json.dumps(p))
        edit(p["chain"][1])
        raises(f"wire: {name}", lambda: from_plan(p), PlanValidationError, text)
    bad(plan, lambda s: s.__setitem__("ranges", [["x", 0, 1]]), "ranges and step_ranges",
        "not both")
    bad(plan, lambda s: s["step_ranges"][0][1][0].__setitem__(2, 0), "lo >= hi", "lo < hi")
    bad(plan, lambda s: s["step_ranges"][0][1].__setitem__(0, ["x", 0]), "a bad pair")
    bad(plan, lambda s: s["step_ranges"][0][1][0].__setitem__(0, "w"), "unknown axis")
    bad(plan, lambda s: s["step_ranges"][0][1].append(["x", 0, 1]), "an axis twice")
    bad(plan, lambda s: s["step_ranges"][1].__setitem__(0, 1), "duplicate label",
        "more than one")
    bad(plan, lambda s: s["step_ranges"][0].__setitem__(0, None), "a null label",
        "plain 'ranges'")
    bad(plan, lambda s: s["step_ranges"][0].__setitem__(0, True), "a bool label")
    bad(plan, lambda s: s.__setitem__("step_ranges", []), "no boxes")
    bad(plan, lambda s: s.__setitem__("centers", []), "step_ranges and centres")
    bad(tplan, lambda s: s["tag_ranges"][0].__setitem__(0, "C"), "a tag in both lists",
        "twice")
    bad(tplan, lambda s: s["tag_ranges"][0].__setitem__(0, "../x"), "a bad bounds tag")
    bad(tplan, lambda s: s.__setitem__("step_ranges", []), "tags and step_ranges")
    uplan = to_plan(save(nodes["unread"], "o.vti"))
    bad(uplan, lambda s: s.__setitem__("track_kind", "centre"), "a bad track_kind",
        "track_kind")
    bad(uplan, lambda s: s.__setitem__("size", [4]), "track_kind with a size",
        "track_kind")
    reset_sinks()

    from vislang.remote.reduce import _stale_remote_hint
    check("an older remote is told to update",
          "predates boxes given by their bounds" in _stale_remote_hint(
              "chain[2] (region): (region) bad keys: missing [], unknown "
              "['step_ranges']"))


def test_track_file():
    print("== min/max track files ==")
    s = source("/d/s")

    def resolved(path):
        return track.resolve_tracks([region(s, bounds=path)])[0]
    p = write("b_step.csv", "﻿# a comment\n\nStep,XMin,XMax,ymin,ymax,zmin,zmax\n"
              "3,515,535,164.5,184.5,771.4,791.4\n1,,10,0,5,,\n")
    r = resolved(p)
    check("step,xmin…: rows as inline, a blank cell open",
          r.step_ranges == region(s, bounds=[(3, (515, 535), (164.5, 184.5), (771.4, 791.4)),
                                             (1, (None, 10), (0, 5), (None, None))]).step_ranges
          and r.track == p and len(r.track_sha or "") > 0 and not r.track_pending, r)
    p2 = write("b_2d.csv", "step,xmin,xmax,ymin,ymax\n1,0,1,0,1\n")
    check("step,xmin…ymax: 2-D", resolved(p2).step_ranges == ((1, (("x", 0, 1),
                                                                  ("y", 0, 1))),))
    p3 = write("b_tagstep.csv", "tag,step,xmin,xmax,ymin,ymax,zmin,zmax\n"
               "halo,3,0,1,0,1,0,1\nhalo,19,1,2,1,2,1,2\njet,3,5,6,5,6,5,6\n")
    r3 = resolved(p3)
    check("tag,step,…: named boxes over a series",
          track.tag_names(r3) == ["halo", "jet"] and r3.tag_series
          and [lab for lab, _ in r3.tag_ranges[0][1]] == [3, 19], r3)
    p4 = write("b_tag.csv", "tag,xmin,xmax,ymin,ymax,zmin,zmax\nfield,460,520,130,170,740,780\n")
    r4 = resolved(p4)
    check("tag,xmin…: named boxes that stay put",
          r4.tag_ranges == (("field", ((None, (("x", 460, 520), ("y", 130, 170),
                                              ("z", 740, 780))),)),), r4)

    c = write("c_step.csv", "step,x,y,z\n1,0,0,0\n")
    raises("a centre file given as bounds=", lambda: resolved(c), ValueError,
           f"region(center={c!r})")
    raises("a min/max file given as center=",
           lambda: track.resolve_tracks([region(s, center=p, size=2)]), ValueError,
           f"region(bounds={p!r})")
    raises("an unknown header", lambda: resolved(write("b_bad.csv", "step,lo,hi\n1,0,1\n")),
           ValueError, "xmin,xmax")
    raises("a bad number", lambda: resolved(write("b_num.csv",
                                                  "step,xmin,xmax,ymin,ymax\n1,a,1,0,1\n")),
           ValueError, "line 2")
    raises("a non-finite bound", lambda: resolved(write("b_inf.csv",
                                                        "step,xmin,xmax,ymin,ymax\n1,0,inf,0,1\n")),
           ValueError, "finite")
    raises("min >= max", lambda: resolved(write("b_order.csv",
                                                "step,xmin,xmax,ymin,ymax\n1,2,1,0,1\n")),
           ValueError, "min < max")
    raises("a duplicate row", lambda: resolved(write("b_dup.csv",
                                                     "tag,step,xmin,xmax,ymin,ymax\n"
                                                     "a,1,0,1,0,1\na,1,2,3,2,3\n")),
           ValueError, "more than one box at timestep 1")
    raises("a short row", lambda: resolved(write("b_short.csv",
                                                 "step,xmin,xmax,ymin,ymax\n1,0,1,0\n")),
           ValueError, "line 2")
    raises("no rows", lambda: resolved(write("b_empty.csv", "step,xmin,xmax,ymin,ymax\n")),
           ValueError, "no boxes")
    raises("a missing file", lambda: resolved(os.path.join(TMP, "nope.csv")),
           FileNotFoundError, "region(bounds=")


def test_points():
    print("== points: same as the fixed box, bounds inclusive ==")
    d = particle_series()
    pos = ("x", "y", "z")
    rows = [(1, (10.0, 20.0), (10.0, 20.0), (10.0, 20.0)),
            (2, (30.0, 55.5), (None, 40.0), (None, None))]
    out = os.path.join(TMP, "pts")
    run(save(region(source(d, positions=pos), bounds=rows), out + ".npz"))
    same = True
    for lab, (xl, xh), (yl, yh), zz in rows:
        ref = os.path.join(TMP, f"pts_fixed_{lab}")
        axes = dict(x=(xl, xh), y=(yl, yh))
        if zz != (None, None):
            axes["z"] = zz
        run(save(region(timesteps(source(d, positions=pos), [lab]), **axes), ref + ".npz"))
        same &= same_npz(os.path.join(out, f"timestep#{lab}.npz"),
                         os.path.join(ref, f"timestep#{lab}.npz"))
    check("every timestep byte-identical to region(x=…, y=…, z=…)", same)
    with np.load(os.path.join(out, "timestep#1.npz")) as z:
        x = z["x"]
        check("a particle on the min and on the max is kept",
              10.0 in x and 20.0 in x and 9.999 not in x and 20.001 not in x)
        check("nothing outside", bool(((x >= 10) & (x <= 20)).all()))
    with np.load(os.path.join(out, "timestep#2.npz")) as z:
        check("open-ended y, whole z",
              bool((z["y"] <= 40).all()) and z["z"].max() > 90 and z["z"].min() < 10)


def test_grid_cells():
    print("== grid in cells: same as the fixed box, clipped to the grid ==")
    d = grid_series()
    rows = [(10, (2, 6), (3, 7), (4, 8)), (11, (0, 16), (5, 9), (None, 3))]
    out = os.path.join(TMP, "cells")
    run(save(region(timesteps(source(d), 10, 11), bounds=rows), out + ".npz"))
    same = True
    for lab, xx, yy, zz in rows:
        ref = os.path.join(TMP, f"cells_fixed_{lab}")
        run(save(region(timesteps(source(d), [lab]), x=xx, y=yy, z=zz), ref + ".npz"))
        same &= same_npz(os.path.join(out, f"timestep#{lab}.npz"),
                         os.path.join(ref, f"timestep#{lab}.npz"))
    check("every timestep byte-identical to region(x=…, y=…, z=…)", same)
    with np.load(os.path.join(out, "timestep#10.npz")) as z:
        check("cells [lo, hi)", z["rho"].shape == (4, 4, 4)
              and float(z["rho"][0, 0, 0]) == 256 * 2 + 16 * 3 + 4 + 10)

    clip = os.path.join(TMP, "clip")
    res = run(save(region(timesteps(source(d), 10, 10),
                          bounds=[(10, (-2, 4), (0, 4), (14, 20))]), clip + ".npz"))
    with np.load(os.path.join(clip, "timestep#10.npz")) as z:
        check("clipped to the grid", z["rho"].shape == (4, 4, 2), z["rho"].shape)
    check("clipping reported per axis",
          any("clipped to the grid edge on x, z" in s for s in res["steps"]), res["steps"])
    raises("a box entirely outside names its timestep and axis",
           lambda: run(save(region(timesteps(source(d), 10, 11),
                                   bounds=[(10, (0, 4), (0, 4), (0, 4)),
                                           (11, (20, 24), (0, 4), (0, 4))]),
                            os.path.join(TMP, "outside.npz"))),
           ValueError, "timestep #11: the box on x")
    check("nothing written for the refused box",
          not os.path.exists(os.path.join(TMP, "outside")))


def test_grid_world():
    print("== grid with geometry: world coordinates, as the fixed box ==")
    d = vti_series()
    rows = [(0, (101.0, 103.0), (0.0, 6.0), (3.0, 6.0)),
            (1, (100.5, 104.0), (-4.0, 2.0), (None, 4.0))]
    out = os.path.join(TMP, "world")
    res = run(save(region(source(d), bounds=rows), out + ".vti"))
    same = True
    for lab, xx, yy, zz in rows:
        ref = os.path.join(TMP, f"world_fixed_{lab}")
        run(save(region(timesteps(source(d), [lab]), x=xx, y=yy, z=zz), ref + ".vti"))
        a = pv.read(os.path.join(out, f"timestep#{lab}.vti"))
        b = pv.read(os.path.join(ref, f"timestep#{lab}.vti"))
        same &= (np.array_equal(a["rho"], b["rho"]) and tuple(a.dimensions)
                 == tuple(b.dimensions) and np.allclose(a.origin, b.origin))
    check("every timestep identical to region(x=…, y=…, z=…), placed the same", same)
    m = pv.read(os.path.join(out, "timestep#0.vti"))
    check("world bounds inclusive: 101..103 at spacing 0.5 is 5 samples",
          m.dimensions[0] == 5 and np.isclose(m.origin[0], 101.0), (m.dimensions, m.origin))
    check("placed in world coordinates on the geometry",
          any("per-timestep bounds placed in world coordinates" in s
              for s in res["steps"]), res["steps"])
    res = run(save(region(timesteps(source(d), 0, 0),
                          bounds=[(0, (90.0, 102.0), (0.0, 6.0), (3.0, 6.0))]),
                   os.path.join(TMP, "wclip.vti")))
    check("world clipping reported", any("clipped to the grid edge on x" in s
                                         for s in res["steps"]), res["steps"])
    raises("a world box outside the grid",
           lambda: run(save(region(timesteps(source(d), 0, 0),
                                   bounds=[(0, (200.0, 210.0), (0.0, 6.0), (3.0, 6.0))]),
                            os.path.join(TMP, "wout.vti"))), ValueError, "timestep #0")


def test_coverage():
    print("== coverage, before any read ==")
    d = grid_series()
    reads = []
    orig = load._read
    load._read = lambda *a, **k: reads.append(a[0].filepath) or orig(*a, **k)
    try:
        raises("a selected timestep with no box",
               lambda: run(save(region(timesteps(source(d), 10, 12),
                                       bounds=[(10, (0, 4), (0, 4), (0, 4)),
                                               (11, (0, 4), (0, 4), (0, 4))]),
                                os.path.join(TMP, "cov.npz"))),
               ValueError, "no box for timestep(s) #12")
        raises("a box for a timestep with no file",
               lambda: run(save(region(source(d),
                                       bounds=[(t, (0, 4), (0, 4), (0, 4))
                                               for t in (10, 11, 12, 13)]),
                                os.path.join(TMP, "cov2.npz"))),
               ValueError, "no timestep file for #13")
        raises("a tag naming a timestep with no file",
               lambda: run(save(region(source(d), bounds={
                   "a": [(13, (0, 4), (0, 4), (0, 4))]}), os.path.join(TMP, "cov3.npz"))),
               ValueError, "bounds=): tag 'a' has no timestep file for #13")
        raises("a box per timestep on one file",
               lambda: run(save(region(source(os.path.join(d, "snap#10.hdf5")),
                                       bounds=[(10, (0, 4), (0, 4), (0, 4))]),
                                os.path.join(TMP, "one.npz"))),
               ValueError, "bounds=((xlo, xhi)")
        raises("named boxes per timestep on one file",
               lambda: run(save(region(source(os.path.join(d, "snap#10.hdf5")),
                                       bounds={"a": [(10, (0, 4), (0, 4), (0, 4))]}),
                                os.path.join(TMP, "one2.npz"))),
               ValueError, "bounds={tag: ((xlo, xhi)")
    finally:
        load._read = orig
    check("nothing read for any refusal", reads == [], reads)


def test_tags():
    print("== named boxes: one series per tag ==")
    d = grid_series()
    b = {"a": [(10, (2, 6), (2, 6), (2, 6)), (12, (8, 12), (8, 12), (8, 12))],
         "b": ((0, 4), (0, 4), (0, 4))}
    out = os.path.join(TMP, "troi")
    res = run(save(region(source(d), bounds=b), out + ".vti"))
    check("tags reported with their timesteps",
          res["tags"] == {"a": [10, 12], "b": [10, 11, 12]}, res.get("tags"))
    check("a tag with a gap writes only its own timesteps",
          sorted(os.listdir(os.path.join(out, "a"))) ==
          [".sieve-provenance.yaml", "timestep#10.vti", "timestep#12.vti"])
    m = pv.read(os.path.join(out, "a", "timestep#12.vti"))
    check("box placed, timed by its timestep",
          tuple(m.dimensions) == (4, 4, 4)
          and float(m["rho"][0]) == 256 * 8 + 16 * 8 + 8 + 12
          and float(m.field_data["TimeValue"][0]) == 12.0)
    pvd = open(os.path.join(out, "troi.pvd")).read()
    rows = re.findall(r'timestep="(\d+)" part="(\d)" name="(\w+)"', pvd)
    check("ParaView index: a block per tag, a box only where it exists",
          rows == [("10", "0", "a"), ("10", "1", "b"), ("11", "1", "b"),
                   ("12", "0", "a"), ("12", "1", "b")], rows)
    res = run(save(region(timesteps(source(d), 11, 11), bounds=b),
                   os.path.join(TMP, "tsel.npz")))
    check("a tag with no box in the selection is skipped and writes nothing",
          res["tags"] == {"b": [11]} and os.listdir(os.path.join(TMP, "tsel")) == ["b"],
          res.get("tags"))

    # mixed centre and bounds tags: centre tags first
    out = os.path.join(TMP, "mixed")
    res = run(save(region(source(d), center={"c": [(10, 4, 4, 4), (11, 5, 5, 5)]}, size=4,
                          bounds={"a": [(10, (2, 6), (2, 6), (2, 6))]}), out + ".vti"))
    check("mixed: outputs in order, centre tags first",
          list(res["tags"]) == ["c", "a"] and res["tags"] == {"c": [10, 11], "a": [10]},
          res.get("tags"))
    pvd = open(os.path.join(out, "mixed.pvd")).read()
    check("mixed: the index puts the centre tag first",
          re.findall(r'timestep="10" part="(\d)" name="(\w+)"', pvd) == [("0", "c"), ("1", "a")])
    with h5py.File(os.path.join(d, "snap#10.hdf5")) as f:
        rho = f["rho"][2:6, 2:6, 2:6]
    check("mixed: the centre box and the bounds box cut the same cells",
          np.array_equal(pv.read(os.path.join(out, "a", "timestep#10.vti"))["rho"],
                         pv.read(os.path.join(out, "c", "timestep#10.vti"))["rho"])
          and np.array_equal(pv.read(os.path.join(out, "a", "timestep#10.vti"))["rho"],
                             rho.ravel(order="F")))


def test_plan_and_record():
    print("== dry run, plan text and provenance ==")
    d = grid_series()
    rows = [(10, (2, 6), (3, 7), (4, 8)), (11, (0, 4), (0, 4), (14, 20))]
    node = region(timesteps(source(d), 10, 11), bounds=rows)
    check("describe", track.describe(node) == "region{bounds×2}", track.describe(node))
    sink = save(node, os.path.join(TMP, "rec.npz"))
    check("plan text", "region{bounds×2}" in describe_plan(to_plan(sink)),
          describe_plan(to_plan(sink)))
    tnode = region(source(d), bounds={"halo": [(10, (0, 1), (0, 1), (0, 1)),
                                               (11, (0, 1), (0, 1), (0, 1))],
                                      "field": ((0, 9), (0, 9), (0, 9))})
    check("describe named boxes",
          track.describe(tnode) == "region{tags=halo,field (3 boxes)}", track.describe(tnode))
    res = run(save(node, os.path.join(TMP, "rec_dry.npz")), dry_run=True)
    text = "\n".join(res["steps"])
    check("dry run lists each timestep's box, nothing read",
          "#10: x:(2,6), y:(3,7), z:(4,8)" in text and "#11: x:(0,4), y:(0,4), z:(14,16)"
          in text and "[clipped z]" in text and not res["materialized"], text)

    run(sink)
    out = os.path.join(TMP, "rec")
    one = provenance.record_for(os.path.join(out, "timestep#10.npz"))["logical"]
    check("per-timestep record: its own box as written",
          "keep the box at #10: x 2 to 6, y 3 to 7, z 4 to 8" in one["spec_explanation"]
          and "2 boxes given in the spec; cells [lo, hi)" in one["spec_explanation"]
          and one["resolved"]["region_boxes"][10]["box"]["x"] == [2, 6],
          one["spec_explanation"])
    folder = provenance.record_for(out)["logical"]
    check("folder record: each timestep's box, clips kept",
          "keep each timestep's own box, given by its bounds" in folder["spec_explanation"]
          and folder["resolved"]["region_boxes"][11]["box"]["z"] == [14, 16]
          and folder["resolved"]["region_boxes"][11]["clipped"] == ["z"],
          folder)
    check("the record says how the numbers were read",
          folder["resolved"]["region_coords"] == "cells", folder["resolved"])

    p = write("rec.csv", "step,xmin,xmax,ymin,ymax,zmin,zmax\n10,2,6,3,7,4,8\n11,0,4,0,4,1,5\n")
    run(save(region(timesteps(source(d), 10, 11), bounds=p), os.path.join(TMP, "rec_csv.npz")))
    rec = provenance.record_for(os.path.join(TMP, "rec_csv"))["logical"]
    check("record: track path and sha",
          rec["resolved"]["region_track"]["path"] == p
          and rec["resolved"]["region_track"]["sha256"]
          and "bounds from rec.csv" in rec["spec_explanation"], rec["spec_explanation"])
    check("a min/max file == the same rows inline",
          same_npz(os.path.join(TMP, "rec_csv", "timestep#10.npz"),
                   os.path.join(out, "timestep#10.npz")))

    tout = os.path.join(TMP, "trec")
    run(save(tnode, tout + ".npz"))
    r = provenance.record_for(os.path.join(tout, "halo", "timestep#11.npz"))["logical"]
    check("named-box record: its box and its siblings",
          "keep the box named halo at #11: x 0 to 1" in r["spec_explanation"]
          and "the others: field" in r["spec_explanation"]
          and r["resolved"]["region_tag"] == "halo", r["spec_explanation"])
    f = provenance.record_for(os.path.join(tout, "field"))["logical"]["spec_explanation"]
    check("named box that stays put",
          "keep the box named field: x 0 to 9, y 0 to 9, z 0 to 9 at each of its 3 "
          "timesteps here" in f, f)


def test_motivating_spec():
    print("== the spec this form was written for (local stand-in) ==")
    d = os.path.join(TMP, "STEP279")
    os.makedirs(d, exist_ok=True)
    rng = np.random.default_rng(11)
    labels = (3, 11, 19, 27, 33)
    for t in labels:
        a = rng.uniform((505, 155, 760), (545, 195, 800), (2000, 3))
        b = rng.uniform((450, 120, 730), (530, 180, 790), (2000, 3))
        p = np.vstack([a, b]).astype(np.float32)
        with h5py.File(os.path.join(d, f"m000.full.mpicosmo.{t}#{t}.hdf5"), "w") as f:
            for i, c in enumerate("xyz"):
                f.create_dataset(c, data=p[:, i])
            f.create_dataset("id", data=np.arange(len(p), dtype=np.int64))
            f.create_dataset("fof_halo_tag", data=np.full(len(p), t, dtype=np.int64))
    series = source(d, positions=("x", "y", "z"))
    boxes = region(
        fields(timesteps(series, 3, 33), ["x", "y", "z", "id", "fof_halo_tag"]),
        bounds={
            "halo":  [(3,  (515, 535), (164.5, 184.5), (771.4, 791.4)),
                      (19, (512.5, 537.5), (162, 187), (768.9, 793.9))],
            "field": ((460, 520), (130, 170), (740, 780)),
        },
    )
    out = os.path.join(TMP, "tagged_regions", "boxes")
    res = run(save(boxes, out + ".vtp"))
    check("halo at its two timesteps, field at every one",
          res["tags"] == {"halo": [3, 19], "field": list(labels)}, res.get("tags"))
    m = pv.read(os.path.join(out, "halo", "timestep#19.vtp"))
    pts = np.asarray(m.points)
    check("every halo point inside its #19 bounds",
          m.n_points > 0 and bool(((pts[:, 0] >= 512.5) & (pts[:, 0] <= 537.5)
                                   & (pts[:, 1] >= 162) & (pts[:, 1] <= 187)
                                   & (pts[:, 2] >= 768.9) & (pts[:, 2] <= 793.9)).all()))
    check("the ParaView index opens both", os.path.exists(os.path.join(out, "boxes.pvd")))


def test_remote():
    print("== remote: catalog folder and single file ==")
    _install_fake_remote()
    os.environ["VISLANG_CACHE"] = os.path.join(TMP, "cache")
    os.environ["VISLANG_REMOTE"] = "force"
    os.environ["VISLANG_REMOTE_TMP"] = os.path.join(TMP, "remote_tmp")
    try:
        d = grid_series("rseries")
        b = {"a": [(10, (2, 6), (2, 6), (2, 6)), (12, (8, 12), (8, 12), (8, 12))],
             "b": ((0, 4), (0, 4), (0, 4))}
        n0 = len(EXECS)
        res = run(save(region(fields(source(f"u@fakehost:{d}"), ["rho"]), bounds=b),
                       os.path.join(TMP, "r_tags.vti")))
        check("remote folder: one job for every box", len(EXECS) == n0 + 1, len(EXECS) - n0)
        check("remote folder: manifest lists (timestep, tag) pieces",
              [(e["label"], e["tag"]) for e in MANIFESTS[-1]] ==
              [(10, "a"), (10, "b"), (11, "b"), (12, "a"), (12, "b")], MANIFESTS[-1])
        local = os.path.join(TMP, "l_tags")
        run(save(region(fields(source(d), ["rho"]), bounds=b), local + ".vti"))
        same = all(np.array_equal(pv.read(os.path.join(TMP, "r_tags", t, f"timestep#{lab}.vti"))
                                  ["rho"],
                                  pv.read(os.path.join(local, t, f"timestep#{lab}.vti"))["rho"])
                   for t, labs in res["tags"].items() for lab in labs)
        check("remote folder == local", same and res["tags"] == {"a": [10, 12],
                                                                "b": [10, 11, 12]})
        res = run(save(region(fields(source(f"u@fakehost:{d}"), ["rho"]), bounds=b),
                       os.path.join(TMP, "r_tags2.vti")))
        check("remote folder: re-run is a full catalog hit",
              len(EXECS) == n0 + 1 and any("full hit" in s for s in res["steps"]),
              res["steps"])

        n0 = len(EXECS)
        rows = [(10, (1, 5), (1, 5), (1, 5)), (11, (3, 7), (3, 7), (3, 7))]
        run(save(region(fields(timesteps(source(f"u@fakehost:{d}"), 10, 11), ["rho"]),
                        bounds=rows), os.path.join(TMP, "r_rows.npz")))
        with np.load(os.path.join(TMP, "r_rows", "timestep#11.npz")) as z:
            check("remote folder: one box per timestep",
                  z["rho"].shape == (4, 4, 4)
                  and float(z["rho"][0, 0, 0]) == 256 * 3 + 16 * 3 + 3 + 11)
        check("remote folder: one job", len(EXECS) == n0 + 1, len(EXECS) - n0)

        pd = particle_series("rpseries")
        pos = ("x", "y", "z")
        pb = {"near": [(1, (10.0, 20.0), (10.0, 20.0), (10.0, 20.0))],
              "far": ((60.0, 80.0), (None, 30.0), (40.0, 90.0))}
        n0 = len(EXECS)
        res = run(save(region(fields(source(f"u@fakehost:{pd}", positions=pos),
                                     ["x", "y", "z", "mass"]), bounds=pb),
                       os.path.join(TMP, "r_pts.npz")))
        run(save(region(fields(source(pd, positions=pos), ["x", "y", "z", "mass"]),
                        bounds=pb), os.path.join(TMP, "l_pts.npz")))
        check("remote points: tags and one job",
              res["tags"] == {"near": [1], "far": [1, 2]} and len(EXECS) == n0 + 1,
              (res.get("tags"), len(EXECS) - n0))
        check("remote points == local, the on-bound particles kept",
              all(same_npz(os.path.join(TMP, "r_pts", t, f"timestep#{lab}.npz"),
                           os.path.join(TMP, "l_pts", t, f"timestep#{lab}.npz"))
                  for t, labs in res["tags"].items() for lab in labs)
              and 20.0 in np.load(os.path.join(TMP, "r_pts", "near", "timestep#1.npz"))["x"])

        f = os.path.join(d, "snap#10.hdf5")
        n0 = len(EXECS)
        run(save(region(fields(source(f"u@fakehost:{f}"), ["rho"]), center={"c": (4, 4, 4)},
                        size=4, bounds={"b": ((10, 14), (10, 14), (10, 14))}),
                 os.path.join(TMP, "r_one.npz")))
        check("remote file: one job for both kinds of box", len(EXECS) == n0 + 1)
        with np.load(os.path.join(TMP, "r_one", "b.npz")) as z:
            check("remote file: bounds box correct",
                  z["rho"].shape == (4, 4, 4)
                  and float(z["rho"][0, 0, 0]) == 256 * 10 + 16 * 10 + 10 + 10)
    finally:
        for k in ("VISLANG_CACHE", "VISLANG_REMOTE", "VISLANG_REMOTE_TMP"):
            os.environ.pop(k, None)
    reset_sinks()


if __name__ == "__main__":
    test_form()
    test_sandbox()
    test_wire()
    test_track_file()
    test_points()
    test_grid_cells()
    test_grid_world()
    test_coverage()
    test_tags()
    test_plan_and_record()
    test_motivating_spec()
    test_remote()
    print(f"\n{len(PASS)} checks passed")
