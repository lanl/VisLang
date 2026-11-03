"""coarsen(): one value per block of cells — mean, min or max — on a grid.

Every expected value is computed block by block here, with plain numpy on
slices, independently of the implementation. Covers: the three reductions,
per-axis factors, edge blocks cut short, order with region / subsample /
threshold, integer sources, the streamed (slab-by-slab) read giving the same
answer as the whole read, placement at block centres (cells and world
coordinates), points refused, the estimate, the provenance wording, the wire
format, a series, and the simulated remote with its cache.

    /opt/miniconda3/envs/vislang/bin/python tests/test_coarsen.py
"""
import itertools
import os
import shutil
import sys
import warnings

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_geometry as tg
from test_geometry import check, raises, read_grid, run, TMP
from test_raw import write_raw
from test_netcdf import write_nc3
from fakes_remote import EXECS, install_fake_remote, install_login_inspect
from vislang.dsl import reset_sinks
from vislang.dsl.forms import (source, region, fields, save, subsample, threshold,
                               timesteps, coarsen)
from vislang.dsl.ast_serialize import to_plan_json, from_plan_json, PlanValidationError
from vislang.formats.inspect import inspect_file
from vislang.interpreter.estimate import estimate_plan_cost
from vislang.interpreter.planner import _lower
from vislang.runtime import provenance

SHAPE = (13, 10, 7)
FN = {"mean": np.nanmean, "min": np.nanmin, "max": np.nanmax}


def ref(arr, factors, how):
    """Block by block, the slow obvious way."""
    a = np.asarray(arr)
    out_shape = tuple(-(-n // f) for n, f in zip(a.shape, factors))
    floaty = np.issubdtype(a.dtype, np.floating) or how == "mean"
    out = np.empty(out_shape, dtype=np.float64 if floaty else a.dtype)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for idx in itertools.product(*(range(n) for n in out_shape)):
            sl = tuple(slice(i * f, (i + 1) * f) for i, f in zip(idx, factors))
            block = a[sl].astype(np.float64) if floaty else a[sl]
            out[idx] = FN[how](block) if floaty else (block.min() if how == "min" else block.max())
    return out


def npz(path):
    with np.load(path) as z:
        return {k: z[k] for k in z.files if not k.startswith("sieve_")}


def same(a, b):
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    return a.shape == b.shape and np.allclose(a, b, equal_nan=True, rtol=1e-12, atol=0)


def test():
    rng = np.random.default_rng(7)
    arr = rng.normal(size=SHAPE)
    p = write_raw("pressure", arr)
    out = lambda name: os.path.join(TMP, name)

    print("== the three reductions, edge blocks cut short ==")
    for how in ("mean", "min", "max"):
        run(save(coarsen(source(p), 4, how=how), out(f"c_{how}.npz")))
        got = npz(out(f"c_{how}.npz"))["pressure"]
        check(f"{how}: 4×4×4 blocks of a 13×10×7 grid, edge blocks over what they hold",
              got.shape == (4, 3, 2) and same(got, ref(arr, (4, 4, 4), how)), got.shape)
    run(save(coarsen(source(p), x=4, y=2, how="max"), out("per.npz")))
    check("per-axis factors (z kept whole)",
          same(npz(out("per.npz"))["pressure"], ref(arr, (4, 2, 1), "max")))

    print("== order with region, subsample, threshold ==")
    run(save(coarsen(region(source(p), x=(1, 12), y=(2, 10)), 3), out("r_then_c.npz")))
    expect = ref(arr[1:12, 2:10, :], (3, 3, 3), "mean")
    check("region then coarsen: blocks start at the region's first cell",
          same(npz(out("r_then_c.npz"))["pressure"], expect))
    run(save(region(coarsen(source(p), 3), x=(1, 12), y=(2, 10)), out("c_then_r.npz")))
    check("coarsen then region: the region is a box of the source, the same result",
          same(npz(out("c_then_r.npz"))["pressure"], expect))
    run(save(coarsen(subsample(source(p), 2), 2, how="min"), out("s_then_c.npz")))
    check("subsample then coarsen: blocks of the strided cells",
          same(npz(out("s_then_c.npz"))["pressure"], ref(arr[::2, ::2, ::2], (2, 2, 2), "min")))
    raises("coarsen then subsample is refused, saying where to put it",
           lambda: run(save(subsample(coarsen(source(p), 2), 2), out("x.npz"))),
           "Put subsample before coarsen")
    masked = np.where(arr > 0.5, arr, np.nan)
    run(save(coarsen(threshold(source(p), "pressure > 0.5"), 4), out("t_then_c.npz")))
    check("threshold then coarsen: masked cells left out of each block (all-masked -> NaN)",
          same(npz(out("t_then_c.npz"))["pressure"], ref(masked, (4, 4, 4), "mean")))
    run(save(threshold(coarsen(source(p), 4), "pressure > 0.1"), out("c_then_t.npz")))
    m = ref(arr, (4, 4, 4), "mean")
    check("coarsen then threshold: the block means are what is tested",
          same(npz(out("c_then_t.npz"))["pressure"], np.where(m > 0.1, m, np.nan)))

    print("== integer sources ==")
    ints = rng.integers(0, 255, size=SHAPE).astype(np.uint8)
    pi = write_raw("density", ints)
    run(save(coarsen(source(pi), 4, how="min"), out("imin.npz")))
    got = npz(out("imin.npz"))["density"]
    check("min of an integer field keeps its type, edge blocks right",
          got.dtype == np.uint8 and np.array_equal(got, ref(ints, (4, 4, 4), "min")))
    run(save(coarsen(source(pi), 4, how="mean"), out("imean.npz")))
    got = npz(out("imean.npz"))["density"]
    check("mean of an integer field is float64", got.dtype == np.float64
          and same(got, ref(ints, (4, 4, 4), "mean")))

    print("== streamed slab by slab = read whole ==")
    os.environ["VISLANG_SLAB_MB"] = "0.002"         # a few planes per slab
    try:
        for name, node in (("s_mean", coarsen(source(p), 3)),
                           ("s_tmin", coarsen(threshold(source(p), "pressure > -0.2"), 2, how="min")),
                           ("s_reg", coarsen(region(source(p), x=(2, 13), z=(1, 7)), x=3, z=2, how="max"))):
            run(save(node, out(f"{name}.npz")))
        os.environ["VISLANG_SLAB_MB"] = "100000"
        for name, node in (("w_mean", coarsen(source(p), 3)),
                           ("w_tmin", coarsen(threshold(source(p), "pressure > -0.2"), 2, how="min")),
                           ("w_reg", coarsen(region(source(p), x=(2, 13), z=(1, 7)), x=3, z=2, how="max"))):
            run(save(node, out(f"{name}.npz")))
        for a, b in (("s_mean", "w_mean"), ("s_tmin", "w_tmin"), ("s_reg", "w_reg")):
            check(f"slabs and one read agree exactly ({a[2:]})",
                  np.array_equal(npz(out(f"{a}.npz"))["pressure"],
                                 npz(out(f"{b}.npz"))["pressure"], equal_nan=True))
        check("and match the block-by-block reference",
              same(npz(out("s_reg.npz"))["pressure"], ref(arr[2:13, :, 1:7], (3, 1, 2), "max")))
    finally:
        os.environ.pop("VISLANG_SLAB_MB", None)

    print("== placed at block centres ==")
    run(save(coarsen(region(source(p), x=(1, 13)), 4), out("placed.vti")))
    a, _, mesh = read_grid(out("placed.vti"))
    check("cells: origin at the first block's centre, spacing a block",
          np.allclose(mesh.origin, (1 + 1.5, 1.5, 1.5)) and np.allclose(mesh.spacing, (4, 4, 4)),
          (mesh.origin, mesh.spacing))
    even = write_nc3(os.path.join(TMP, "even.nc"), even=True)
    run(save(coarsen(fields(source(even), ["vx"]), x=2, z=3), out("world.vti")))
    a, _, mesh = read_grid(out("world.vti"))
    check("world coordinates: centre of the first block, spacing × f",
          np.allclose(mesh.origin, (-85 + 5.0, 3500.0, 0 + 10.0))
          and np.allclose(mesh.spacing, (20.0, 2800.0 / 19, 30.0)), (mesh.origin, mesh.spacing))

    print("== refused on points, priced, recorded, on the wire ==")
    pts = os.path.join(TMP, "pts.npz")
    np.savez(pts, x=rng.random(50), y=rng.random(50), z=rng.random(50), mass=rng.random(50))
    raises("coarsen on point data is refused", lambda: run(save(coarsen(source(pts), 2), out("y.npz"))),
           "applies to grids")
    raises("a block factor must be whole", lambda: coarsen(source(p), 2.5), "whole number")
    raises("how must be mean, min or max", lambda: coarsen(source(p), 2, how="median"), "one of")

    info = inspect_file(p)
    r = region(source(p), x=(0, 12))
    n, _ = _lower(info, [r, coarsen(r, 4)], [])
    est = estimate_plan_cost(info=info, narrowing=n, site="local")
    kept = 3 * 3 * 2
    check("estimate: the result is one value per block",
          abs(est.output_mb - kept * 8 / 2 ** 20) < 1e-12, est.output_mb)
    check("estimate: the read is every cell of the region",
          abs(est.read_mb - 12 * 10 * 7 * 8 / 2 ** 20) < 1e-12, est.read_mb)

    rec = provenance.record_for(out("c_min.npz"))
    text = rec["logical"]["spec_explanation"]
    check("provenance: says one value per block, and that values are block minima",
          "one value per block of 4×4×4 cells: the block's minimum" in text
          and "block minima" in text, text)

    plan = to_plan_json(save(coarsen(source(p), x=4, y=2, how="max"), out("w.npz")))
    back = from_plan_json(plan)
    check("the wire format round-trips", back.upstream.per_axis == (("x", 4), ("y", 2))
          and back.upstream.how == "max")
    bad = plan.replace('"max"', '"median"')
    raises("the wire validator refuses an unknown reduction",
           lambda: from_plan_json(bad), "'how' must be one of")

    print("== a series ==")
    d = os.path.join(TMP, "series")
    os.makedirs(d, exist_ok=True)
    per = {}
    for t in (1, 2, 3):
        per[t] = rng.normal(size=SHAPE)
        q = os.path.join(d, f"p#{t}_13x10x7_float64.raw")
        per[t].ravel(order="F").tofile(q)
    run(save(coarsen(timesteps(source(d), 2, 3), 5, how="max"), out("ser.npz")))
    for t in (2, 3):
        got = npz(os.path.join(TMP, "ser", f"timestep#{t}.npz"))
        (name,) = got
        check(f"series #{t}: that timestep's block maxima", same(got[name], ref(per[t], (5, 5, 5), "max")))

    print("== the simulated remote, and its cache ==")
    os.environ["VISLANG_CACHE"] = os.path.join(TMP, "ccache")
    os.environ["VISLANG_REMOTE"] = "force"
    os.environ["VISLANG_REMOTE_TMP"] = os.path.join(TMP, "ctmp")
    os.makedirs(os.environ["VISLANG_REMOTE_TMP"], exist_ok=True)
    install_fake_remote()
    install_login_inspect()
    try:
        far = f"u@fakehost:{p}"
        n0 = len(EXECS)
        run(save(coarsen(region(source(far), x=(1, 13)), 4, how="min"), out("rem.npz")))
        check("remote: reduced on the far side", len(EXECS) == n0 + 1, len(EXECS) - n0)
        check("remote: the block minima", same(npz(out("rem.npz"))["pressure"],
                                               ref(arr[1:13], (4, 4, 4), "min")))
        run(save(coarsen(region(source(far), x=(1, 13)), 4, how="min"), out("rem2.npz")))
        check("remote: the same request again comes from the cache", len(EXECS) == n0 + 1)
        run(save(coarsen(region(source(far), x=(1, 13)), 4, how="max"), out("rem3.npz")))
        check("remote: another reduction is not served from the cached minima",
              len(EXECS) == n0 + 2 and same(npz(out("rem3.npz"))["pressure"],
                                             ref(arr[1:13], (4, 4, 4), "max")))
        run(save(region(source(far), x=(1, 13)), out("rem4.npz")))
        check("remote: the uncoarsened region is not served from a coarsened extent",
              len(EXECS) == n0 + 3 and np.array_equal(npz(out("rem4.npz"))["pressure"], arr[1:13]))
        n1 = len(EXECS)
        run(save(coarsen(timesteps(source(f"u@fakehost:{d}"), 1, 3), 5), out("rser.npz")))
        check("remote series: one job, every timestep's block means",
              len(EXECS) == n1 + 1 and all(
                  same(next(iter(npz(os.path.join(TMP, "rser", f"timestep#{t}.npz")).values())),
                       ref(per[t], (5, 5, 5), "mean")) for t in (1, 2, 3)))
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
