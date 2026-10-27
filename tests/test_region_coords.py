"""region() in the data's own coordinates, and coords= to pin them.

Plain-python asserts (no pytest on the cluster). Run from the repo root:
    python tests/test_region_coords.py

Covers: the form and the wire (coords= validated, carried, rebuilt); the
world -> cells conversion (uniform: inclusive bounds, clipping, an empty box,
a negative spacing; curvilinear: the smallest block holding the box); the
default per kind of data (a grid with geometry reads world, a grid without
reads cells, points read world) and what coords= refuses; centre-placed and
named boxes in world coordinates; the dry run and the provenance record saying
how the numbers were read; and the simulated remote — a uniform grid's world
box converted on the host (keyed and reused as cells, so a smaller world box
comes from the cache), a curvilinear one placed next to the data, and a uniform
series placed per file. The transport is faked; the plan, executor, catalog and
planner are the real code.
"""

import json
import os
import sys
import tempfile

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pyvista as pv

from test_geometry import terrain_points, write_vts, read_grid
from test_region_tags import _install_fake_remote, EXECS
from vislang.dsl import reset_sinks
from vislang.dsl.ast_serialize import (from_plan_json, to_plan_json,
                                       PlanValidationError)
from vislang.dsl.forms import source, region, fields, save, subsample, timesteps
from vislang.formats.dataset_info import (structured_geometry, uniform_geometry,
                                          world_box_to_cells)
from vislang.interpreter.planner import plan_pipeline
from vislang.runtime import provenance

TMP = tempfile.mkdtemp(prefix="vislang_coords_test_")
PASS = []


def check(name, cond, detail=""):
    assert cond, f"{name}: {detail}"
    PASS.append(name)
    print(f"  ok  {name}")


def raises(name, fn, text=None):
    try:
        fn()
    except Exception as e:
        check(name, text is None or text in str(e), str(e))
        return
    raise AssertionError(f"{name}: did not raise")


def run(sink, dry_run=False):
    reset_sinks()
    with provenance.run("spec.py", spec_code="# test\n"):
        return plan_pipeline(sink, dry_run=dry_run)


ORIGIN, SPACING, DIMS = (100.0, -4.0, 2.0), (0.5, 2.0, 1.0), (12, 10, 8)


def write_vti(path, t=0):
    img = pv.ImageData(dimensions=DIMS, origin=ORIGIN, spacing=SPACING)
    img.point_data["rho"] = (np.arange(int(np.prod(DIMS)), dtype=np.float32)
                             + 1000 * t)
    img.save(path)
    return path


def vti_rho(path):
    m = pv.read(path)
    return (np.asarray(m.point_data["rho"]).reshape(tuple(m.dimensions), order="F"),
            tuple(m.origin), tuple(m.dimensions))


# ---------------------------------------------------------------------------
def test_form_and_wire():
    print("== form + wire ==")
    s = source("/d.vti")
    raises("coords must be world or cells",
           lambda: region(s, x=(0, 1), coords="index"), "expected one of")
    check("default leaves it open", region(s, x=(0, 1)).coords is None)
    for c in ("world", "cells"):
        for n in (region(s, x=(0.5, 1.5), coords=c),
                  region(s, center=(1.0, 2.0, 3.0), size=2.0, coords=c),
                  region(s, center={"a": (1.0, 2.0, 3.0)}, size=2.0, coords=c)):
            back = from_plan_json(to_plan_json(save(n, "/o.npz")))
            check(f"wire round trip keeps coords={c} ({'tagged' if n.tagged else 'per_step' if n.per_step else 'ranges'})",
                  back.upstream.coords == c, back.upstream)
    plain = json.loads(to_plan_json(save(region(s, x=(0, 1)), "/o.npz")))
    check("an open region keeps the old wire shape",
          "coords" not in plain["chain"][1], plain["chain"][1])
    bad = json.loads(to_plan_json(save(region(s, x=(0, 1), coords="world"), "/o.npz")))
    bad["chain"][1]["coords"] = "pixels"
    raises("the validator refuses an unknown coords",
           lambda: from_plan_json(json.dumps(bad)), "'coords' must be one of")


def test_conversion():
    print("== world -> cells ==")
    g = uniform_geometry(ORIGIN, SPACING)
    cells, clipped = world_box_to_cells(g, DIMS, {0: (100.4, 101.0)})
    # samples at 100.0, 100.5, 101.0, ... -> 100.5 and 101.0 inside (inclusive)
    check("uniform: bounds inclusive", cells == {0: (1, 3)} and not clipped, cells)
    cells, clipped = world_box_to_cells(g, DIMS, {0: (90.0, 100.6), 1: (None, 0.0)})
    check("uniform: clipped to the grid, open bound",
          cells == {0: (0, 2), 1: (0, 3)} and clipped == ["x"], (cells, clipped))
    raises("uniform: a box between samples holds none",
           lambda: world_box_to_cells(g, DIMS, {0: (100.6, 100.9)}), "holds no grid sample")
    raises("uniform: a box outside the grid says where the grid is",
           lambda: world_box_to_cells(g, DIMS, {2: (50.0, 60.0)}), "spans 2 to 9")
    neg = uniform_geometry((10.0, 0, 0), (-1.0, 1, 1))     # samples 10, 9, 8, ...
    cells, _ = world_box_to_cells(neg, (6, 2, 2), {0: (7.0, 8.5)})
    check("uniform: a negative spacing walks backwards", cells == {0: (2, 4)}, cells)

    P = terrain_points()
    sg = structured_geometry(P)
    cells, clipped = world_box_to_cells(sg, P.shape[:-1], {0: (-6.0, 0.0), 2: (None, 6.0)})
    inside = ((P[..., 0] >= -6) & (P[..., 0] <= 0) & (P[..., 2] <= 6))
    idx = np.argwhere(inside)
    want = {a: (int(idx[:, a].min()), int(idx[:, a].max()) + 1) for a in range(3)}
    want = {a: v for a, v in want.items() if v != (0, P.shape[a])}
    check("structured: smallest block holding every point inside",
          cells == want and not clipped, (cells, want))
    raises("structured: no point inside",
           lambda: world_box_to_cells(sg, P.shape[:-1], {2: (500.0, 600.0)}),
           "holds no grid point")


def test_local():
    print("== local: the data's own coordinates ==")
    d = os.path.join(TMP, "local")
    os.makedirs(d)
    vti = write_vti(os.path.join(d, "cube.vti"))
    full, _, _ = vti_rho(vti)

    # A grid with geometry: numbers are world coordinates by default.
    res = run(save(region(source(vti), x=(100.4, 101.0), y=(0.0, 4.0)),
                   os.path.join(d, "w.vti")))
    rho, origin, dims = vti_rho(res["output"])
    check("geometry grid: default is world, cut where the box is",
          np.allclose(origin, (100.5, 0.0, 2.0)) and dims == (2, 3, 8)
          and np.array_equal(rho, full[1:3, 2:5, :]), (origin, dims))
    check("the plan says how it read the numbers",
          any("(world) -> cells x [1:3], y [2:5]" in s for s in res["steps"]),
          res["steps"])
    rec = provenance.record_for(res["output"])
    text = json.dumps(rec)
    check("provenance: coords filled in though the spec left it open",
          rec["logical"]["resolved"].get("region_coords") == "world",
          rec["logical"]["resolved"])
    check("provenance: the cells it selected",
          rec["logical"]["resolved"]["region_cells"]["cells"] == {"x": [1, 3], "y": [2, 5]},
          rec["logical"]["resolved"])
    check("provenance: explanation names world coordinates",
          "world coordinates" in text)

    # coords="cells" crops by index on the same file.
    res = run(save(region(source(vti), x=(1, 3), y=(2, 5), coords="cells"),
                   os.path.join(d, "c.vti")))
    rho_c, origin_c, _ = vti_rho(res["output"])
    check("coords='cells' crops by index, same block",
          np.array_equal(rho_c, rho) and np.allclose(origin_c, origin))
    check("provenance: a pinned coords is the spec's, not resolved",
          "region_coords" not in provenance.record_for(res["output"])["logical"]
          .get("resolved", {}))

    raises("a world box that holds no sample is refused before the read",
           lambda: run(save(region(source(vti), x=(0.0, 5.0)), os.path.join(d, "x.vti"))),
           "holds no grid sample")
    res = run(save(region(source(vti), z=(5.0, 100.0)), os.path.join(d, "clip.vti")))
    check("a box past the edge is clipped, and says so",
          vti_rho(res["output"])[2] == (12, 10, 5)
          and any("clipped to the grid on z" in s for s in res["steps"]), res["steps"])

    # Centre-placed: centre and size in world units, a fixed cell count.
    res = run(save(region(source(vti), center=(101.0, 0.0, 5.0), size=(1.0, 4.0, 2.0)),
                   os.path.join(d, "ctr.vti")))
    rho_b, origin_b, dims_b = vti_rho(res["output"])
    # x: centre index 2, 2 cells -> [1:3]; y: index 2, 2 cells -> [1:3];
    # z: index 3, 2 cells -> [2:4]
    check("centre-placed world box", dims_b == (2, 2, 2)
          and np.array_equal(rho_b, full[1:3, 1:3, 2:4]), (origin_b, dims_b))

    # Named boxes, world.
    res = run(save(region(source(vti), center={"a": (101.0, 0.0, 5.0),
                                               "b": (104.0, 8.0, 3.0)}, size=2.0),
                   os.path.join(d, "tags.vti")))
    a, oa, _ = vti_rho(os.path.join(d, "tags", "a.vti"))
    b, ob, _ = vti_rho(os.path.join(d, "tags", "b.vti"))
    # a: x centre index 2, 2.0/0.5 = 4 cells -> [0:4]; y index 2, 1 cell;
    #    z index 3, 2 cells -> [2:4].  b: x index 8 -> [6:10]; y index 6; z [0:2]
    check("named boxes in world coordinates land where named",
          np.allclose(oa, (100.0, 0.0, 4.0)) and np.allclose(ob, (103.0, 8.0, 2.0))
          and a.shape == (4, 1, 2) and np.array_equal(b, full[6:10, 6:7, 0:2]),
          (oa, ob, a.shape))

    # A grid without geometry: its world coordinates ARE its indices.
    npz = os.path.join(d, "plain.npz")
    np.savez(npz, rho=np.arange(6 * 6 * 6, dtype=np.float32).reshape(6, 6, 6))
    res = run(save(region(source(npz), x=(1, 4)), os.path.join(d, "p_out.npz")))
    with np.load(res["output"]) as z:
        check("no geometry: default is cells, [1:4)", z["rho"].shape == (3, 6, 6))
    raises("no geometry: coords='world' is refused, not guessed",
           lambda: run(save(region(source(npz), x=(1, 4), coords="world"),
                            os.path.join(d, "p2.npz"))),
           "does not say where it sits")

    # Points: always world; no cells to crop.
    pts = os.path.join(d, "pts.npz")
    rng = np.random.default_rng(0)
    np.savez(pts, x=rng.uniform(0, 10, 200), y=rng.uniform(0, 10, 200),
             z=rng.uniform(0, 10, 200), m=np.arange(200.0))
    res = run(save(region(source(pts), x=(2.0, 3.0)), os.path.join(d, "pts_out.npz")))
    with np.load(res["output"]) as z:
        check("points: world by default", ((z["x"] >= 2) & (z["x"] <= 3)).all())
    raises("points: coords='cells' is refused",
           lambda: run(save(region(source(pts), x=(2, 3), coords="cells"),
                            os.path.join(d, "pts2.npz"))),
           "point data has no cells")

    # A curvilinear grid: the smallest block holding the box.
    vts = write_vts(os.path.join(d, "fire.vts"))
    P = terrain_points()
    res = run(save(region(source(vts), x=(-6.0, 0.0), z=(None, 6.0)),
                   os.path.join(d, "fire_cut.vts")))
    _, pts_out, _ = read_grid(res["output"])
    cells, _ = world_box_to_cells(structured_geometry(P), P.shape[:-1],
                                  {0: (-6.0, 0.0), 2: (None, 6.0)})
    cut = tuple(slice(*cells.get(a, (0, P.shape[a]))) for a in range(3))
    check("curvilinear: cut is the covering block, points in place",
          np.array_equal(pts_out, P[cut]), pts_out.shape)

    # A dry run reports the conversion and reads no data.
    res = run(save(region(source(vti), x=(100.4, 101.0)), os.path.join(d, "dry.vti")),
              dry_run=True)
    check("dry run reports the conversion",
          any("(world) -> cells x [1:3]" in s for s in res["steps"]), res["steps"])


def test_remote():
    print("== remote: converted on the host when the schema places it ==")
    _install_fake_remote()
    os.environ["VISLANG_CACHE"] = os.path.join(TMP, "cache")
    os.environ["VISLANG_REMOTE"] = "force"
    os.environ["VISLANG_REMOTE_TMP"] = os.path.join(TMP, "remote_tmp")
    os.makedirs(os.environ["VISLANG_REMOTE_TMP"])
    # The login-node inspect (metadata only), which the fake above does not
    # answer: it is how the host learns where the grid sits.
    import shlex
    import subprocess
    import vislang.remote.download as download
    real_run_remote = download.run_remote

    def run_remote_with_inspect(conn, command, stdin_bytes=None, timeout=None):
        if "--inspect" not in command:
            return real_run_remote(conn, command, stdin_bytes, timeout)
        r = subprocess.run([sys.executable, os.path.join(REPO, "vislang_exec.py"),
                            "--inspect", shlex.split(command)[-1]],
                           capture_output=True, text=True, cwd=REPO,
                           env={**os.environ, "VISLANG_NO_BINDING": "1"})
        return r.returncode, r.stdout, r.stderr
    download.run_remote = run_remote_with_inspect
    try:
        d = os.path.join(TMP, "rdata")
        os.makedirs(d)
        vti = write_vti(os.path.join(d, "cube.vti"))
        full, _, _ = vti_rho(vti)

        n0 = len(EXECS)
        res = run(save(region(fields(source(f"u@fakehost:{vti}"), ["rho"]),
                              x=(100.0, 102.0), y=(0.0, 8.0)),
                       os.path.join(TMP, "r_w.vti")))
        rho, origin, _ = vti_rho(res["output"])
        shipped = EXECS[-1]["chain"]
        reg = next(s for s in shipped if s["kind"] == "region")
        check("uniform remote: the host shipped cells",
              len(EXECS) == n0 + 1 and reg.get("coords") == "cells"
              and reg["ranges"] == [["x", 0, 5], ["y", 2, 7]], reg)
        check("uniform remote: same cut as local",
              np.array_equal(rho, full[0:5, 2:7, :]) and np.allclose(origin, (100.0, 0.0, 2.0)))
        n1 = len(EXECS)
        res = run(save(region(fields(source(f"u@fakehost:{vti}"), ["rho"]),
                              x=(100.5, 101.5), y=(2.0, 6.0)),
                       os.path.join(TMP, "r_w2.vti")))
        rho2, origin2, _ = vti_rho(res["output"])
        check("uniform remote: a smaller world box is served from the cache",
              len(EXECS) == n1 and np.array_equal(rho2, full[1:4, 3:6, :])
              and np.allclose(origin2, (100.5, 2.0, 2.0)), res["steps"])

        print("== remote: curvilinear placed next to the data ==")
        vts = write_vts(os.path.join(d, "fire.vts"))
        P = terrain_points()
        n0 = len(EXECS)
        res = run(save(region(fields(source(f"u@fakehost:{vts}"), ["w"]),
                              x=(-6.0, 0.0), z=(None, 6.0)),
                       os.path.join(TMP, "r_fire.vts")))
        reg = next(s for s in EXECS[-1]["chain"] if s["kind"] == "region")
        _, pts_out, _ = read_grid(res["output"])
        cells, _ = world_box_to_cells(structured_geometry(P), P.shape[:-1],
                                      {0: (-6.0, 0.0), 2: (None, 6.0)})
        cut = tuple(slice(*cells.get(a, (0, P.shape[a]))) for a in range(3))
        check("curvilinear remote: shipped in world coordinates",
              len(EXECS) == n0 + 1 and reg.get("coords") == "world", reg)
        check("curvilinear remote: cut next to the data, same block as local",
              np.array_equal(pts_out, P[cut]), pts_out.shape)

        print("== remote: a uniform series, each file placed on its own ==")
        u = os.path.join(d, "useries")
        os.makedirs(u)
        for t in (1, 2):
            write_vti(os.path.join(u, f"cube#{t}.vti"), t=t)
        o = os.path.join(TMP, "r_series")
        n0 = len(EXECS)
        run(save(subsample(region(fields(source(f"u@fakehost:{u}"), ["rho"]),
                                  x=(101.0, 104.0)), 2), o))
        reg = next(s for s in EXECS[-1]["chain"] if s["kind"] == "region")
        rho_t, origin_t, _ = vti_rho(os.path.join(o, "timestep#2.vti"))
        check("series remote: shipped in world coordinates, one job",
              len(EXECS) == n0 + 1 and reg.get("coords") == "world", reg)
        full2 = full + 1000 * 2
        check("series remote: each file cut where the box is",
              np.array_equal(rho_t, full2[2:9:2, ::2, ::2])
              and np.allclose(origin_t, (101.0, -4.0, 2.0)), (origin_t, rho_t.shape))
    finally:
        download.run_remote = real_run_remote
        for k in ("VISLANG_CACHE", "VISLANG_REMOTE", "VISLANG_REMOTE_TMP"):
            os.environ.pop(k, None)


if __name__ == "__main__":
    test_form_and_wire()
    test_conversion()
    test_local()
    test_remote()
    print(f"\nall {len(PASS)} checks passed")
