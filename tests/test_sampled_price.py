"""Pricing a world box on a curvilinear grid from cached strided positions.

A world box on a terrain-following grid is placed next to the data, on the
file's own points (bulk data). This side used to price it as the whole grid.
An overview of the same timesteps caches the points at a stride; interpolated,
they size the block the box selects, so the price follows the box — and the
points that cross with the cut are priced too. The cut itself is still exact.

    /opt/miniconda3/envs/vislang/bin/python tests/test_sampled_price.py
"""
import os
import shutil
import sys

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pyvista as pv

import test_geometry as tg
from test_geometry import check, read_grid, run, TMP
from test_region_tags import _install_fake_remote
from vislang.dsl import reset_sinks
from vislang.dsl.forms import source, region, fields, save, subsample, timesteps
from vislang.formats.dataset_info import (_sampled_box_to_cells, _structured_box_to_cells,
                                          sampled_geometry, world_box_to_cells)
from vislang.runtime import provenance


def ridge_points(shape):
    """(nx, ny, nz, 3): x, y every 2 m over a ridge along y; levels follow the
    ground and stretch with height, as the wildfire grid's do."""
    nx, ny, nz = shape
    i, j, k = np.meshgrid(np.arange(nx), np.arange(ny), np.arange(nz), indexing="ij")
    x, y = -nx + 2.0 * i, -ny + 2.0 * j
    ground = 0.3 * nx * np.exp(-(x / (0.4 * nx)) ** 2)
    z = ground + (k / (nz - 1)) ** 1.5 * 3 * nx * (1 - ground / (4 * nx)) + 0.75
    return np.stack([x, y, z], axis=-1)


def write_ridge(path, shape, t):
    g = pv.StructuredGrid()
    g.points = ridge_points(shape).transpose(2, 1, 0, 3).reshape(-1, 3)
    g.dimensions = shape
    n = int(np.prod(shape))
    g.point_data["w"] = (np.arange(n, dtype=np.float32) + 1000 * t)
    g.point_data["theta"] = (300.0 + np.sin(np.arange(n))).astype(np.float32)
    g.save(path)


def block_size(cells, grid):
    return int(np.prod([cells.get(a, (0, n))[1] - cells.get(a, (0, n))[0]
                        for a, n in enumerate(grid)]))


def contains(est, exact, grid):
    return all(est.get(a, (0, n))[0] <= exact.get(a, (0, n))[0]
               and est.get(a, (0, n))[1] >= exact.get(a, (0, n))[1]
               for a, n in enumerate(grid))


def test_estimate():
    print("== sampled points size the block a world box selects ==")
    grid = (150, 120, 31)
    P = ridge_points(grid)
    lo, hi = P.reshape(-1, 3).min(0), P.reshape(-1, 3).max(0)
    for steps in [(4, 4, 4), (8, 8, 8)]:
        sample = P[::steps[0], ::steps[1], ::steps[2]]
        rng = np.random.default_rng(1)
        held, ratios, declined = 0, [], 0
        for _ in range(200):
            a = rng.uniform(lo, hi)
            box = {i: (a[i], a[i] + w) for i, w in
                   enumerate(rng.uniform(0.05, 0.6, 3) * (hi - lo))}
            try:
                exact, _ = _structured_box_to_cells(P, grid, box)
            except ValueError:
                continue                    # the box holds no point
            try:
                est, _ = _sampled_box_to_cells(sample, steps, grid, box)
            except ValueError:
                declined += 1               # priced as the whole grid instead
                continue
            held += contains(est, exact, grid)
            ratios.append(block_size(est, grid) / block_size(exact, grid))
        check(f"stride {steps}: the estimate holds the exact block "
              f"({held}/{len(ratios)} boxes; {declined} past every sampled point "
              f"left to the whole-grid price)", held == len(ratios) > 100)
        check(f"stride {steps}: and is close to it (median "
              f"{np.median(ratios):.2f}x, worst {max(ratios):.2f}x)",
              np.median(ratios) < 1.4, ratios)
    box = {0: (0, 20), 2: (None, 80)}
    exact, _ = _structured_box_to_cells(P, grid, box)
    got, _ = world_box_to_cells(sampled_geometry(P, (1, 1, 1)), grid, box)
    check("stride 1: the sample is the grid, so the block is exact", got == exact,
          (got, exact))


def test_remote_price():
    print("== remote (simulated): an overview's positions size the Detail ==")
    shape = (60, 40, 16)
    os.environ["VISLANG_CACHE"] = os.path.join(TMP, "pcache")
    os.environ["VISLANG_REMOTE"] = "force"
    os.environ["VISLANG_REMOTE_TMP"] = os.path.join(TMP, "ptmp")
    os.makedirs(os.environ["VISLANG_REMOTE_TMP"], exist_ok=True)
    _install_fake_remote()
    # The login-node inspect (metadata only), which the fake does not answer:
    # in ParaView the Source's inspect has cached the schema before a Detail.
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
    d = os.path.join(TMP, "pseries")
    os.makedirs(d, exist_ok=True)
    for t in (1, 2, 3):
        write_ridge(os.path.join(d, f"fire#{t}.vts"), shape, t)
    P = ridge_points(shape)
    box = dict(x=(-20.0, 30.0), y=(-10.0, 20.0), z=(None, 60.0))
    exact, _ = _structured_box_to_cells(P, shape, {0: box["x"], 1: box["y"],
                                                   2: box["z"]})
    cells = block_size(exact, shape)
    # Two float32 fields + float64 positions (as written here), three files.
    actual_mb = 3 * cells * (2 * 4 + 3 * 8) / 1024 ** 2
    whole_mb = 3 * np.prod(shape) * 2 * 4 / 1024 ** 2
    detail = lambda o: save(region(fields(timesteps(source(f"u@fakehost:{d}"), 1, 3),
                                          ["w", "theta"]), **box), o)
    try:
        # Before any overview: no positions cached -> the whole grid, said so.
        os.environ["VISLANG_BUDGET_BYTES"] = str(int(0.5 * whole_mb * 1024 ** 2))
        res = run(detail(os.path.join(TMP, "p_first")))
        est = res.get("estimate")
        check("no overview yet: priced as the whole grid, and held",
              res.get("needs_confirm") and est is not None
              and est.read_mb >= whole_mb, (est, res["steps"][-6:]))
        check("no overview yet: the estimate says why",
              any("priced as the whole grid" in n for n in est.notes), est.notes)

        # The overview caches the points at stride 4 for every timestep.
        run(save(subsample(fields(source(f"u@fakehost:{d}"), ["w"]), 4),
                 os.path.join(TMP, "p_over")))
        res = run(detail(os.path.join(TMP, "p_detail")))
        est = res.get("estimate")
        check("after the overview: not held — the box is sized, not the grid",
              res.get("materialized") and est is not None, res["steps"][-6:])
        check(f"after the overview: the price ({est.read_mb:.3f} MiB) holds what "
              f"crossed, positions included ({actual_mb:.3f} MiB), well under the "
              f"grid ({whole_mb:.3f} MiB)",
              actual_mb <= est.read_mb < min(1.5 * actual_mb, whole_mb))
        check("after the overview: the estimate says how it was sized",
              any("cached at stride 4x4x4" in n for n in est.notes), est.notes)
        _, pts, _ = read_grid(os.path.join(TMP, "p_detail", "timestep#2.vts"))
        sl = tuple(slice(*exact.get(a, (0, n))) for a, n in enumerate(shape))
        check("after the overview: the cut itself is still exact",
              np.array_equal(pts, P[sl]))
        sample = P[::4, ::4, ::4]
        priced, _ = _sampled_box_to_cells(sample, (4, 4, 4), shape,
                                          {0: box["x"], 1: box["y"], 2: box["z"]})
        rec = repr(provenance.record_for(os.path.join(TMP, "p_detail",
                                                      "timestep#2.vts")))
        check("provenance: the priced block is not recorded as the cut",
              priced == exact or str(list(priced[0])) not in rec, rec[:300])
    finally:
        download.run_remote = real_run_remote
        for k in ("VISLANG_CACHE", "VISLANG_REMOTE", "VISLANG_REMOTE_TMP",
                  "VISLANG_BUDGET_BYTES"):
            os.environ.pop(k, None)
    reset_sinks()


if __name__ == "__main__":
    test_estimate()
    test_remote_price()
    shutil.rmtree(TMP, ignore_errors=True)
    import test_region_tags
    shutil.rmtree(test_region_tags.TMP, ignore_errors=True)
    print(f"\n{len(tg.PASS)} checks passed")
