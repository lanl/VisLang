"""Where a grid sits in space, end to end: the npz reader, curvilinear (.vts)
grids, and geometry over the remote routes.

Plain-python asserts (no pytest on the cluster). Run from the repo root:
    python tests/test_geometry.py

Covers: the Tier-0 npz reader (grid / particles / mixed, reserved keys, a grid
whose names look like coordinates), a VisLang .npz cut again with its origin
kept; a terrain-following .vts — whose z differs per column, so no origin +
spacing describes it — cut, strided, thresholded and written as .vts (also by
default), legacy .vtk and .vtkhdf with every point where it was, refused as
.vti, kept through an .npz round trip, named boxes and a series (TimeValue +
.pvd); the extent catalog cutting a cached geometry for a smaller box; and the
simulated remote (single file, named boxes, a folder with and without fields(),
a cache filled before geometry was kept) — the transport is faked, the plan,
executor, catalog and planner are the real code. ParaView itself opens the
.vts series when it is installed.
"""

import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pyvista as pv

from test_region_tags import _install_fake_remote, EXECS, MANIFESTS
from vislang.dsl import reset_sinks
from vislang.dsl.forms import source, region, fields, save, subsample, threshold, timesteps
from vislang.formats.dataset_info import (GEOMETRY_KEY, encode_geometry, decode_geometry,
                                          uniform_geometry, structured_geometry)
from vislang.formats.inspect import inspect_file
from vislang.interpreter.planner import plan_pipeline
from vislang.remote.catalog import ExtentCatalog
from vislang.runtime import provenance

TMP = tempfile.mkdtemp(prefix="vislang_geom_test_")
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
    check(name, False, "did not raise")


def run(sink):
    """Execute one sink under a provenance run, as the CLI does."""
    reset_sinks()
    with provenance.run("spec.py", spec_code="# test\n"):
        return plan_pipeline(sink)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
SHAPE = (12, 10, 8)


def terrain_points(shape=SHAPE):
    """(nx, ny, nz, 3): x, y every 2 m; z starts on a sloping, wavy ground and
    its layers stretch with height — so z depends on x and y."""
    nx, ny, nz = shape
    i, j, k = np.meshgrid(np.arange(nx), np.arange(ny), np.arange(nz), indexing="ij")
    ground = 3.0 * np.sin(i / 2.0) + 0.7 * j + 0.5 * i
    z = ground + (1.0 + 0.2 * i) * k ** 1.3
    return np.stack([-10.0 + 2.0 * i, 4.0 + 2.0 * j, z], axis=-1).astype(np.float64)


def terrain_fields(t=0, shape=SHAPE):
    n = int(np.prod(shape))
    w = (np.arange(n, dtype=np.float32).reshape(shape) + 1000 * t)
    theta = (300.0 + np.sin(np.arange(n)).reshape(shape)).astype(np.float32)
    return {"w": w, "theta": theta}


def write_vts(path, t=0, shape=SHAPE):
    g = pv.StructuredGrid()
    g.points = terrain_points(shape).transpose(2, 1, 0, 3).reshape(-1, 3)
    g.dimensions = shape
    for name, arr in terrain_fields(t, shape).items():
        g.point_data[name] = arr.ravel(order="F")
    g.save(path)
    return path


def read_grid(path):
    """(arrays {name: [x,y,z]}, points (nx,ny,nz,3) or None, mesh)."""
    m = pv.read(path)
    nx, ny, nz = m.dimensions
    arrays = {n: np.asarray(m.point_data[n]).reshape((nx, ny, nz), order="F")
              for n in m.point_data.keys()}
    pts = None
    if type(m).__name__ == "StructuredGrid":
        pts = np.asarray(m.points).reshape(nz, ny, nx, 3).transpose(2, 1, 0, 3)
    return arrays, pts, m


# ---------------------------------------------------------------------------
# The npz reader
# ---------------------------------------------------------------------------
def test_npz_reader():
    print("== npz reader ==")
    d = os.path.join(TMP, "npz")
    os.makedirs(d)
    g = os.path.join(d, "grid.npz")
    np.savez(g, rho=np.zeros((4, 5, 6), np.float32), vx=np.ones((4, 5, 6)),
             vy=np.ones((4, 5, 6)), vz=np.ones((4, 5, 6)), time=np.array(2.5),
             label=np.array(["run7"]), sieve_provenance=np.array("x"))
    info = inspect_file(g)
    check("npz: Tier-0 reader", info.filetype == "npz", info.filetype)
    check("npz: one shared 3-D shape is a grid", info.dimensions == {"grid": (4, 5, 6)},
          info.dimensions)
    check("npz: vx/vy/vz on a grid are not taken for coordinates", info.positions is None,
          info.positions)
    check("npz: scalars are attributes, sieve_* hidden",
          sorted(info.variables) == ["rho", "vx", "vy", "vz"]
          and info.attributes == {"time": 2.5, "label": "run7"},
          (info.variables, info.attributes))

    p = os.path.join(d, "parts.npz")
    rng = np.random.default_rng(1)
    np.savez(p, x=rng.uniform(0, 10, 50), y=rng.uniform(0, 10, 50),
             z=rng.uniform(0, 10, 50), mass=np.arange(50.0))
    info = inspect_file(p)
    check("npz: equal-length columns are particles",
          info.dimensions == {"particles": 50} and info.positions == ("x", "y", "z"),
          (info.dimensions, info.positions))
    run(save(region(source(p), x=(0, 5)), os.path.join(d, "parts_cut.npz")))
    with np.load(os.path.join(d, "parts_cut.npz")) as z:
        check("npz: a point box on particles", (z["x"] <= 5).all() and len(z["x"]) > 0)

    m = os.path.join(d, "mixed.npz")
    np.savez(m, a=np.zeros((4, 4, 4)), b=np.zeros(10))
    info = inspect_file(m)
    check("npz: mixed shapes get no dimension", info.dimensions == {}, info.dimensions)
    raises("npz: region refuses an unknown shape rather than guess",
           lambda: run(save(region(source(m), x=(0, 2)), os.path.join(d, "m.npz"))),
           "grid dimensions")

    # A VisLang .npz is cut again and still knows where it sits.
    src = os.path.join(d, "img.vti")
    img = pv.ImageData(dimensions=(20, 20, 20), origin=(100.0, 200.0, 300.0),
                       spacing=(0.5, 0.5, 2.0))
    vals = np.arange(8000, dtype=np.float32).reshape((20, 20, 20))
    img.point_data["rho"] = vals.ravel(order="F")
    img.save(src)
    first = os.path.join(d, "first.npz")
    run(save(subsample(region(source(src), x=(4, 16), y=(2, 18)), 2), first))
    with np.load(first) as z:
        check("npz: save keeps origin + spacing under reserved keys",
              np.allclose(z["sieve_origin"], [102.0, 201.0, 300.0])
              and np.allclose(z["sieve_spacing"], [1.0, 1.0, 4.0]),
              (z.get("sieve_origin"), z.get("sieve_spacing")))
    info = inspect_file(first)
    check("npz: geometry read back", info.geometry["kind"] == "uniform"
          and np.allclose(info.geometry["origin"], [102.0, 201.0, 300.0]), info.geometry)
    again = os.path.join(d, "again.vti")
    run(save(region(source(first), x=(1, 4), z=(2, 6)), again))
    arrays, _, mesh = read_grid(again)
    check("npz: cut again lands in place",
          np.allclose(mesh.origin, [103.0, 201.0, 308.0])
          and np.allclose(mesh.spacing, [1.0, 1.0, 4.0])
          and np.array_equal(arrays["rho"], vals[4:16:2, 2:18:2, ::2][1:4, :, 2:6]),
          (mesh.origin, mesh.spacing))
    rec = provenance.record_for(again)
    check("npz: derived output links its parent",
          rec["logical"]["input"].get("derived_from")
          == provenance.record_for(first)["logical"]["output"]["fingerprint"]["data_sha256"])


# ---------------------------------------------------------------------------
# Curvilinear grids, locally
# ---------------------------------------------------------------------------
def test_structured_local():
    print("== curvilinear (.vts), local ==")
    d = os.path.join(TMP, "vts")
    os.makedirs(d)
    src = write_vts(os.path.join(d, "fire.vts"))
    P, F = terrain_points(), terrain_fields()
    info = inspect_file(src)
    check("vts: inspect marks listed points, reads none",
          info.geometry == {"kind": "structured", "points": None}, info.geometry)

    cut = np.s_[2:10:2, 1:9, 0:6:3]
    out = os.path.join(d, "cut.vts")
    run(save(subsample(region(fields(source(src), ["w"]), x=(2, 10), y=(1, 9), z=(0, 6)),
                       x=2, y=1, z=3), out))
    arrays, pts, m = read_grid(out)
    check("vts: data cut", np.array_equal(arrays["w"], F["w"][cut]))
    check("vts: every point where it was in the source", np.array_equal(pts, P[cut]),
          np.abs(pts - P[cut]).max() if pts is not None else None)

    res = run(save(region(source(src), x=(0, 4)), os.path.join(d, "noext")))
    check("vts: no extension keeps the curvilinear format",
          res["output"].endswith("noext.vts")
          and os.path.exists(os.path.join(d, "noext.vts")), res.get("output"))
    _, pts, _ = read_grid(os.path.join(d, "noext.vts"))
    check("vts: default output keeps the points", np.array_equal(pts, P[0:4]))

    raises("vts: refused as .vti (it would sit in the wrong place)",
           lambda: run(save(region(source(src), x=(0, 4)), os.path.join(d, "bad.vti"))),
           "write .vts")
    check("vts: nothing written for the refusal", not os.path.exists(os.path.join(d, "bad.vti")))

    for ext in (".vtk", ".vtkhdf"):
        o = os.path.join(d, "legacy" + ext)
        run(save(region(source(src), y=(2, 7)), o))
        arrays, pts, m = read_grid(o)
        check(f"vts: {ext} holds the points too",
              type(m).__name__ == "StructuredGrid" and np.array_equal(pts, P[:, 2:7])
              and np.array_equal(arrays["theta"], F["theta"][:, 2:7]))

    o = os.path.join(d, "masked.vts")
    run(save(threshold(region(source(src), x=(0, 6)), "theta > 300.2"), o))
    arrays, pts, _ = read_grid(o)
    want = F["w"][0:6].copy()
    want[~(F["theta"][0:6] > 300.2)] = np.nan
    check("vts: threshold masks values, points untouched",
          np.array_equal(arrays["w"], want, equal_nan=True) and np.array_equal(pts, P[0:6]))

    # .npz keeps the points, and a re-read cuts them again.
    n = os.path.join(d, "cut.npz")
    run(save(region(source(src), x=(3, 11), z=(1, 7)), n))
    with np.load(n) as z:
        check("vts -> npz: positions kept beside the data",
              np.array_equal(z["sieve_points"], P[3:11, :, 1:7]) and "w" in z.files)
    o = os.path.join(d, "from_npz.vts")
    run(save(region(source(n), x=(2, 6), y=(0, 5)), o))
    arrays, pts, _ = read_grid(o)
    check("vts -> npz -> vts: still in place",
          np.array_equal(pts, P[3:11, :, 1:7][2:6, 0:5])
          and np.array_equal(arrays["w"], F["w"][3:11, :, 1:7][2:6, 0:5]))

    h = os.path.join(d, "cut.hdf5")
    run(save(region(source(src), x=(0, 4)), h))
    text = provenance.record_text(h)
    check("vts -> hdf5: the record says the positions were not kept",
          "point positions are not stored" in text, text)
    text = provenance.record_text(out)
    rec = provenance.record_for(out)
    check("vts: the record names the points and where they span",
          "each point at its own position" in text and "StructuredGrid" in text
          and rec["logical"]["output"]["geometry"]["points"] == "listed per point"
          and np.isclose(rec["logical"]["output"]["geometry"]["bounds"]["z"][1],
                         P[cut][..., 2].max()), text)

    # Named boxes on one file: each .vts in place, the index lists them.
    o = os.path.join(d, "boxes.vts")
    run(save(region(source(src), center={"a": (4, 4, 4), "b": (9, 6, 3)}, size=(4, 4, 4)), o))
    _, pa, _ = read_grid(os.path.join(d, "boxes", "a.vts"))
    _, pb, _ = read_grid(os.path.join(d, "boxes", "b.vts"))
    pvd = open(os.path.join(d, "boxes", "boxes.pvd")).read()
    check("vts: named boxes each in place, indexed",
          np.array_equal(pa, P[2:6, 2:6, 2:6]) and np.array_equal(pb, P[7:11, 4:8, 1:5])
          and 'file="a.vts"' in pvd and 'file="b.vts"' in pvd)


def test_structured_series():
    print("== curvilinear series, local ==")
    d = os.path.join(TMP, "fire_series")
    os.makedirs(d)
    for t in (1000, 2000, 3000):
        write_vts(os.path.join(d, f"fire#{t}.vts"), t=t)
    P = terrain_points()
    out = os.path.join(TMP, "fire_roi")
    res = run(save(region(fields(timesteps(source(d), 1000, 2000), ["w"]),
                          center={1000: (4, 4, 4), 2000: (6, 5, 4)}, size=4), out))
    files = sorted(f for f in os.listdir(out) if not f.startswith("."))
    check("series: .vts per timestep + index",
          files == ["fire_roi.pvd", "timestep#1000.vts", "timestep#2000.vts"], files)
    arrays, pts, m = read_grid(os.path.join(out, "timestep#2000.vts"))
    check("series: each box at its own place",
          np.array_equal(pts, P[4:8, 3:7, 2:6])
          and np.array_equal(arrays["w"], terrain_fields(2000)["w"][4:8, 3:7, 2:6])
          and float(m.field_data["TimeValue"][0]) == 2000.0)
    check("series: the folder record", provenance.record_for(out) is not None)
    return out


# ---------------------------------------------------------------------------
# The extent catalog
# ---------------------------------------------------------------------------
def test_catalog_geometry():
    print("== catalog: a cached geometry cut for a smaller box ==")
    cat = ExtentCatalog(os.path.join(TMP, "cat"))
    big = {"grid_ranges": [[0, 12, 1], [0, 10, 1], [0, 8, 1]]}
    small = {"grid_ranges": [[2, 10, 2], [3, 7, 1], [4, 8, 1]]}
    P = terrain_points()
    cat.store("s1", GEOMETRY_KEY, big, encode_geometry(structured_geometry(P)))
    got = decode_geometry(cat.lookup("s1", GEOMETRY_KEY, small))
    check("catalog: listed points sliced like the data",
          got is not None and np.array_equal(got["points"], P[2:10:2, 3:7, 4:8]))
    cat.store("s2", GEOMETRY_KEY, big,
              encode_geometry(uniform_geometry((10, 20, 30), (0.5, 1, 2))))
    got = decode_geometry(cat.lookup("s2", GEOMETRY_KEY, small))
    check("catalog: a uniform origin moves with the cut",
          got is not None and np.allclose(got["origin"], (11, 23, 38))
          and np.allclose(got["spacing"], (1, 1, 2)), got)
    cat.store("s3", GEOMETRY_KEY, big, encode_geometry(None))
    got = cat.lookup("s3", GEOMETRY_KEY, small)
    check("catalog: 'no geometry' is cached too (index space, not a miss)",
          got is not None and decode_geometry(got) is None)


# ---------------------------------------------------------------------------
# The simulated remote
# ---------------------------------------------------------------------------
def test_remote():
    print("== remote (simulated): geometry travels with the cut ==")
    os.environ["VISLANG_CACHE"] = os.path.join(TMP, "rcache")
    os.environ["VISLANG_REMOTE"] = "force"
    os.environ["VISLANG_REMOTE_TMP"] = os.path.join(TMP, "rtmp")
    os.makedirs(os.environ["VISLANG_REMOTE_TMP"])
    _install_fake_remote()
    # The login-node inspect (metadata only) — the fake above does not answer
    # it, and with no fields() the series' variables come from it.
    import shlex
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
        d = os.path.join(TMP, "rseries")
        os.makedirs(d)
        for t in (1, 2, 3):
            write_vts(os.path.join(d, f"fire#{t}.vts"), t=t)
        P = terrain_points()
        f = os.path.join(d, "fire#2.vts")

        # -- single file
        n0 = len(EXECS)
        o = os.path.join(TMP, "r_one")
        res = run(save(region(fields(source(f"u@fakehost:{f}"), ["w"]), x=(2, 9), z=(1, 5)), o))
        arrays, pts, _ = read_grid(o + ".vts")
        check("remote file: written as .vts, points in place",
              res["output"].endswith(".vts") and np.array_equal(pts, P[2:9, :, 1:5])
              and np.array_equal(arrays["w"], terrain_fields(2)["w"][2:9, :, 1:5]))
        check("remote file: provenance written", provenance.record_for(o + ".vts") is not None)
        n1 = len(EXECS)
        res = run(save(region(fields(source(f"u@fakehost:{f}"), ["w"]), x=(4, 8), z=(2, 4)),
                       os.path.join(TMP, "r_sub.vts")))
        _, pts, _ = read_grid(os.path.join(TMP, "r_sub.vts"))
        check("remote file: a smaller box comes from the cache, points cut too",
              len(EXECS) == n1 and np.array_equal(pts, P[4:8, :, 2:4]), res["steps"])
        check("remote file: exactly one job so far", n1 == n0 + 1)

        # -- named boxes on one file
        o = os.path.join(TMP, "r_tags.vts")
        run(save(region(fields(source(f"u@fakehost:{f}"), ["theta"]),
                        center={"a": (4, 4, 4), "b": (9, 6, 3)}, size=4), o))
        _, pb, _ = read_grid(os.path.join(TMP, "r_tags", "b.vts"))
        check("remote named boxes: in place", np.array_equal(pb, P[7:11, 4:8, 1:5]))

        # -- folder, no fields(): every variable, saved locally with a record
        n0 = len(EXECS)
        o = os.path.join(TMP, "r_all")
        res = run(save(region(timesteps(source(f"u@fakehost:{d}"), 1, 2),
                              x=(0, 5), y=(2, 6)), o))
        check("remote folder, no fields: one job, every variable",
              len(EXECS) == n0 + 1
              and sorted(MANIFESTS[-1]["1"]) == sorted(["w", "theta", GEOMETRY_KEY]),
              MANIFESTS[-1])
        arrays, pts, m = read_grid(os.path.join(o, "timestep#2.vts"))
        check("remote folder, no fields: .vts with both variables, in place, timed",
              sorted(arrays) == ["theta", "w"] and np.array_equal(pts, P[0:5, 2:6])
              and float(m.field_data["TimeValue"][0]) == 2.0)
        check("remote folder, no fields: every file carries its record",
              provenance.record_for(o) is not None
              and all(provenance.record_for(p) is not None
                      for p in glob.glob(os.path.join(o, "*.vts"))))

        # -- a uniform (.vti) series: its origin survives the remote route too
        u = os.path.join(TMP, "useries")
        os.makedirs(u)
        for t in (1, 2):
            img = pv.ImageData(dimensions=(10, 10, 10), origin=(50.0, -20.0, 7.0),
                               spacing=(0.5, 2.0, 1.0))
            img.point_data["rho"] = np.arange(1000, dtype=np.float32) + t
            img.save(os.path.join(u, f"cube#{t}.vti"))
        o = os.path.join(TMP, "r_uniform")
        run(save(subsample(region(fields(source(f"u@fakehost:{u}"), ["rho"]),
                                  x=(2, 8), y=(4, 10)), 2), o))
        m = pv.read(os.path.join(o, "timestep#2.vti"))
        check("remote uniform series: origin + spacing follow the cut",
              np.allclose(m.origin, (51.0, -12.0, 7.0)) and np.allclose(m.spacing, (1, 4, 2)),
              (m.origin, m.spacing))

        # -- a cache filled before geometry was kept: fetch only that piece
        cache = ExtentCatalog(os.environ["VISLANG_CACHE"])
        for sid, per_var in cache._manifest["extents"].items():
            per_var.pop(GEOMETRY_KEY, None)
        cache._save_manifest()
        n0 = len(EXECS)
        res = run(save(region(timesteps(source(f"u@fakehost:{d}"), 1, 2),
                              x=(0, 5), y=(2, 6)), os.path.join(TMP, "r_all2")))
        _, pts, _ = read_grid(os.path.join(TMP, "r_all2", "timestep#1.vts"))
        check("old cache: one job asks only for the positions",
              len(EXECS) == n0 + 1 and MANIFESTS[-1] == {"1": [GEOMETRY_KEY],
                                                         "2": [GEOMETRY_KEY]},
              MANIFESTS[-1])
        check("old cache: output in place", np.array_equal(pts, P[0:5, 2:6]))
        check("old cache: says what it fetched",
              any("grid position(s) only" in s for s in res["steps"])
              and any("only the grid positions crossed the wire" in s
                      for s in res["steps"]), res["steps"])
    finally:
        download.run_remote = real_run_remote
        for k in ("VISLANG_CACHE", "VISLANG_REMOTE", "VISLANG_REMOTE_TMP"):
            os.environ.pop(k, None)
    reset_sinks()


# ---------------------------------------------------------------------------
# ParaView
# ---------------------------------------------------------------------------
def test_paraview(series_dir):
    print("== ParaView ==")
    pvpy = os.environ.get("VISLANG_PVPYTHON") or next(iter(sorted(glob.glob(
        "/Applications/ParaView*.app/Contents/bin/pvpython"))), None)
    if not pvpy or not os.path.exists(pvpy):
        print("  skip ParaView not found (set VISLANG_PVPYTHON)")
        return
    probe = os.path.join(TMP, "probe.py")
    with open(probe, "w") as f:
        f.write("import sys, json\nfrom paraview.simple import PVDReader\n"
                "from paraview import servermanager as sm\n"
                "r = PVDReader(FileName=sys.argv[1])\nout = {}\n"
                "for t in r.TimestepValues:\n"
                "    r.UpdatePipeline(t)\n    d = sm.Fetch(r)\n"
                "    out[int(t)] = [d.GetClassName(), list(d.GetBounds())]\n"
                "print('RESULT ' + json.dumps(out))\n")
    pvd = os.path.join(series_dir, os.path.basename(series_dir) + ".pvd")
    r = subprocess.run([pvpy, probe, pvd], capture_output=True, text=True, timeout=300)
    line = next((l for l in r.stdout.splitlines() if l.startswith("RESULT ")), None)
    got = json.loads(line[7:]) if line else None
    P = terrain_points()[4:8, 3:7, 2:6].reshape(-1, 3)
    want = [P[:, 0].min(), P[:, 0].max(), P[:, 1].min(), P[:, 1].max(),
            P[:, 2].min(), P[:, 2].max()]
    check("ParaView: opens the .vts series as a StructuredGrid where the box was",
          got is not None and got["2000"][0] == "vtkStructuredGrid"
          and np.allclose(got["2000"][1], want), (got, r.stderr[-1500:]))


if __name__ == "__main__":
    test_npz_reader()
    test_structured_local()
    series = test_structured_series()
    test_catalog_geometry()
    test_remote()
    test_paraview(series)
    shutil.rmtree(TMP, ignore_errors=True)
    import test_region_tags
    shutil.rmtree(test_region_tags.TMP, ignore_errors=True)
    print(f"\n{len(PASS)} checks passed")
