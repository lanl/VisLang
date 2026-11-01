"""A fixed grid's positions cross the wire once per series, not once per timestep.

A terrain-following grid lists every point's position; when the terrain does
not move, every timestep's cut carries the same positions. The remote reducer
(asked with --dedupe-geometry) sends each distinct geometry once and lists the
pieces that share it; this side puts the one copy back for each. A grid that
moves still sends each timestep its own positions. Outputs are the same either
way.

    /opt/miniconda3/envs/vislang/bin/python tests/test_geometry_once.py
"""
import json
import os
import shutil
import subprocess
import sys

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pyvista as pv

import test_geometry as tg
from test_geometry import check, read_grid, run, TMP
from test_sampled_price import ridge_points
from fakes_remote import EXECS, MANIFESTS, install_fake_remote, install_login_inspect
from vislang.dsl import reset_sinks
from vislang.dsl.forms import source, region, fields, save, timesteps
from vislang.formats.dataset_info import _structured_box_to_cells
from vislang.remote.reduce import _executor_cmd, _stale_remote_hint

SHAPE = (60, 40, 16)
BOX = dict(x=(-20.0, 30.0), y=(-10.0, 20.0), z=(None, 60.0))


def write(path, t, moving):
    P = ridge_points(SHAPE)
    if moving:
        P = P.copy()
        P[..., 2] += 2.0 * t                 # the whole grid rises each timestep
    g = pv.StructuredGrid()
    g.points = P.transpose(2, 1, 0, 3).reshape(-1, 3)
    g.dimensions = SHAPE
    n = int(np.prod(SHAPE))
    g.point_data["w"] = np.arange(n, dtype=np.float32) + 1000 * t
    g.point_data["theta"] = (300.0 + np.sin(np.arange(n) + t)).astype(np.float32)
    g.save(path)
    return P


def series(name, moving):
    d = os.path.join(TMP, name)
    os.makedirs(d, exist_ok=True)
    return d, {t: write(os.path.join(d, f"fire#{t}.vts"), t, moving) for t in (1, 2, 3)}


def bundle_of(plan, manifest, dedupe):
    """Run the reducer as the remote would, with or without the flag."""
    out = os.path.join(TMP, f"rx_{int(dedupe)}")
    shutil.rmtree(out, ignore_errors=True)
    man = os.path.join(TMP, "manifest.json")
    json.dump(manifest, open(man, "w"))
    r = subprocess.run([sys.executable, os.path.join(REPO, "vislang_exec.py"), "--stdin",
                        "--outdir", out, "--manifest", man]
                       + (["--dedupe-geometry"] if dedupe else []),
                       input=json.dumps(plan).encode(), capture_output=True, cwd=REPO)
    from vislang.remote.reduce import _parse_meta
    meta = _parse_meta(r.stdout.decode())
    with np.load(os.path.join(out, "bundle.npz")) as z:
        arrays = {k: z[k] for k in z.files}
    return meta, arrays, os.path.getsize(os.path.join(out, "bundle.npz"))


def test():
    print("== a fixed grid's positions cross once; a moving grid's every time ==")
    os.environ["VISLANG_CACHE"] = os.path.join(TMP, "ocache")
    os.environ["VISLANG_REMOTE"] = "force"
    os.environ["VISLANG_REMOTE_TMP"] = os.path.join(TMP, "otmp")
    os.makedirs(os.environ["VISLANG_REMOTE_TMP"], exist_ok=True)
    install_fake_remote()
    install_login_inspect()
    try:
        for name, moving in (("fixed", False), ("moving", True)):
            d, P = series(name, moving)
            o = os.path.join(TMP, f"o_{name}")
            res = run(save(region(fields(timesteps(source(f"u@fakehost:{d}"), 1, 3),
                                         ["w", "theta"]), **BOX), o))
            shared = [s for s in res["steps"] if s.startswith("grid positions:")]
            if moving:
                check("moving grid: every timestep sends its own positions", not shared,
                      shared)
            else:
                check("fixed grid: two of three timesteps share the first's positions",
                      shared and shared[0].startswith("grid positions: 2 piece(s)"),
                      res["steps"])
            for t in (1, 2, 3):
                exact, _ = _structured_box_to_cells(P[t], SHAPE, {0: BOX["x"], 1: BOX["y"],
                                                                  2: BOX["z"]})
                sl = tuple(slice(*exact.get(a, (0, n))) for a, n in enumerate(SHAPE))
                arrays, pts, _ = read_grid(os.path.join(o, f"timestep#{t}.vts"))
                n = int(np.prod(SHAPE))
                w = (np.arange(n, dtype=np.float32) + 1000 * t).reshape(SHAPE, order="F")
                check(f"{name} grid #{t}: positions and values are this timestep's own",
                      np.array_equal(pts, P[t][sl]) and np.array_equal(arrays["w"], w[sl]))

            # The same request, run by the reducer with and without the flag.
            plan, manifest = EXECS[-1], MANIFESTS[-1]
            m0, b0, size0 = bundle_of(plan, manifest, False)
            m1, b1, size1 = bundle_of(plan, manifest, True)
            refs = m1.get("geometry_refs") or {}
            for k, same in refs.items():
                b1[k] = b1[same]
            check(f"{name} grid: the restored bundle equals the full one, array by array",
                  sorted(b0) == sorted(b1) and all(np.array_equal(b0[k], b1[k]) for k in b0))
            if moving:
                check("moving grid: no piece is sent as a reference", refs == {}, refs)
            else:
                check(f"fixed grid: the bundle shrinks ({size0:,} -> {size1:,} bytes)",
                      len(refs) == 2 and size1 < size0, (refs, size0, size1))
            check(f"{name} grid: without the flag the reducer sends every copy",
                  "geometry_refs" not in m0)

        cmd = _executor_cmd("/tmp/o", folder=True, manifest_path="/tmp/m.json",
                            host="fakehost", dedupe_geometry=True)
        check("the reducer is asked for it on the delta route", "--dedupe-geometry" in cmd, cmd)
        check("a remote checkout too old to know the flag says how to fix it",
              "git pull" in _stale_remote_hint(
                  "vislang_exec.py: error: unrecognized arguments: --dedupe-geometry"))
    finally:
        for k in ("VISLANG_CACHE", "VISLANG_REMOTE", "VISLANG_REMOTE_TMP"):
            os.environ.pop(k, None)
    reset_sinks()


if __name__ == "__main__":
    test()
    shutil.rmtree(TMP, ignore_errors=True)
    import test_region_tags
    shutil.rmtree(test_region_tags.TMP, ignore_errors=True)
    print(f"\n{len(tg.PASS)} checks passed")
