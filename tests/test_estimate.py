"""Cost-estimate + budget-gate verification.

Plain-python asserts (no pytest on the cluster). Run from the repo root:
    python tests/test_estimate.py
Covers the shared plan cost model: selected-shape byte math (grid stride,
particle fraction), dtype itemsize, read reducibility (strided vs full-column vs
whole-file), n_timesteps scaling, local(size-only) vs remote(size+measured-time)
branch behavior, and the budget gate.
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datasetInfo import DatasetInfo
from narrowing import Narrowing, AxisRange
import my_estimate as E

PASS = []
_MB = 1024 ** 2


def check(name, cond, detail=""):
    assert cond, f"{name}: {detail}"
    PASS.append(name)
    print(f"  ok  {name}")


def approx(a, b, tol=1e-6):
    return abs(a - b) <= tol * max(1.0, abs(b))


def grid_info(shape=(100, 100, 100), itemsize=4, filetype="HDF5", var="temp"):
    return DatasetInfo("f.h5", filetype, [var], dimensions={"grid": shape},
                       itemsizes={var: itemsize})


def main():
    # -- full grid, reducible (HDF5): read == output == voxels*itemsize --------
    info = grid_info()
    e = E.estimate_plan_cost(info=info, narrowing=Narrowing(project=("temp",)),
                             site="local")
    expect = 100 * 100 * 100 * 4 / _MB
    check("grid_full_bytes", approx(e.read_mb, expect), f"{e.read_mb} vs {expect}")
    check("grid_full_read_eq_output", approx(e.read_mb, e.output_mb))
    check("grid_full_no_time_local", e.time_lo_s is None and e.time_hi_s is None)

    # -- grid strided by 2 on every axis -> 50^3 cells -------------------------
    nw = Narrowing(grid_ranges=[AxisRange(None, None, 2)] * 3, project=("temp",))
    e2 = E.estimate_plan_cost(info=info, narrowing=nw, site="local")
    expect2 = 50 * 50 * 50 * 4 / _MB
    check("grid_strided_bytes", approx(e2.read_mb, expect2), f"{e2.read_mb} vs {expect2}")

    # -- grid cropped [0:10) on x, full y/z -> 10*100*100 ----------------------
    nwc = Narrowing(grid_ranges=[AxisRange(0, 10, 1), AxisRange(None, None, 1),
                                 AxisRange(None, None, 1)], project=("temp",))
    ec = E.estimate_plan_cost(info=info, narrowing=nwc, site="local")
    check("grid_crop_bytes", approx(ec.read_mb, 10 * 100 * 100 * 4 / _MB))

    # -- dtype matters: float64 is exactly 2x float32 --------------------------
    info8 = grid_info(itemsize=8)
    e8 = E.estimate_plan_cost(info=info8, narrowing=Narrowing(project=("temp",)),
                              site="local")
    check("dtype_float64_double", approx(e8.read_mb, 2 * expect), f"{e8.read_mb}")

    # -- uint8 raw would be 1/4 of float32 (itemsize=1) ------------------------
    info1 = grid_info(itemsize=1)
    e1 = E.estimate_plan_cost(info=info1, narrowing=Narrowing(project=("temp",)),
                              site="local")
    check("dtype_uint8_quarter", approx(e1.read_mb, expect / 4))

    # -- missing itemsize -> 4-byte fallback + a note --------------------------
    info_nodt = DatasetInfo("f.h5", "HDF5", ["temp"], dimensions={"grid": (10, 10, 10)})
    en = E.estimate_plan_cost(info=info_nodt, narrowing=Narrowing(project=("temp",)),
                              site="local")
    check("missing_dtype_fallback", approx(en.read_mb, 10 * 10 * 10 * 4 / _MB))
    check("missing_dtype_noted", any("dtype" in n for n in en.notes))

    # -- non-reducible full-column read (GenericIO): read=full, output=selected -
    part = DatasetInfo("f.gio", "GenericIO", ["x"], dimensions={"particles": 1_000_000},
                       itemsizes={"x": 4})
    # subsample to 1/10 via a particle_index slice (every 10th)
    nwp = Narrowing(particle_index=slice(None, None, 10), total_particles=1_000_000,
                    project=("x",))
    ep = E.estimate_plan_cost(info=part, narrowing=nwp, site="local")
    check("genericio_output_is_subsample",
          approx(ep.output_mb, 100_000 * 4 / _MB), f"{ep.output_mb}")
    check("genericio_read_is_full_columns",
          approx(ep.read_mb, 1_000_000 * 4 / _MB), f"{ep.read_mb}")
    check("genericio_read_gt_output", ep.read_mb > ep.output_mb)

    # -- particle fraction count from a boolean mask ---------------------------
    mask = np.zeros(1000, dtype=bool)
    mask[:250] = True
    nwm = Narrowing(particle_index=mask, total_particles=1000, project=("x",))
    partk = DatasetInfo("f.gio", "GenericIO", ["x"], dimensions={"particles": 1000},
                        itemsizes={"x": 4})
    em = E.estimate_plan_cost(info=partk, narrowing=nwm, site="local")
    check("particle_bool_mask_count", approx(em.output_mb, 250 * 4 / _MB))

    # -- n_timesteps scales bytes linearly -------------------------------------
    et = E.estimate_plan_cost(info=info, narrowing=Narrowing(project=("temp",)),
                              site="local", n_timesteps=12)
    check("timesteps_scale", approx(et.read_mb, 12 * expect) and et.n_timesteps == 12)

    # -- remote with a measured link -> a time band, gates on size+time --------
    er = E.estimate_plan_cost(info=info, narrowing=Narrowing(project=("temp",)),
                              site="remote", net_bw_bps=50e6)
    mid = (expect * _MB) / 50e6
    check("remote_time_band", approx(er.time_lo_s, mid / 2) and approx(er.time_hi_s, mid * 2))
    check("remote_confidence_measured", er.confidence == "measured")

    # -- remote WITHOUT a measured link -> bytes only, no time -----------------
    er0 = E.estimate_plan_cost(info=info, narrowing=Narrowing(project=("temp",)),
                               site="remote", net_bw_bps=None)
    check("remote_no_bw_no_time", er0.time_lo_s is None)
    check("remote_no_bw_noted", any("network speed" in n for n in er0.notes))

    # -- budget gate: size ------------------------------------------------------
    os.environ["VISLANG_BUDGET_BYTES"] = str(int(1 * _MB))   # 1 MB budget
    os.environ.pop("VISLANG_BUDGET_SECONDS", None)
    eb = E.estimate_plan_cost(info=info, narrowing=Narrowing(project=("temp",)),
                              site="local")           # ~3.8 MB > 1 MB
    check("gate_over_size", eb.over_budget and eb.budget_reason == "size")

    # under budget when the stride shrinks it below 1 MB
    eu = E.estimate_plan_cost(info=info, narrowing=nw, site="local")   # ~0.48 MB
    check("gate_under_size", not eu.over_budget)

    # -- budget gate: time (remote), large bytes budget so only time trips -----
    os.environ["VISLANG_BUDGET_BYTES"] = str(int(10 * 1024 ** 3))
    os.environ["VISLANG_BUDGET_SECONDS"] = "0.001"
    ebt = E.estimate_plan_cost(info=info, narrowing=Narrowing(project=("temp",)),
                               site="remote", net_bw_bps=1e6)
    check("gate_over_time", ebt.over_budget and ebt.budget_reason == "time")
    os.environ.pop("VISLANG_BUDGET_BYTES", None)
    os.environ.pop("VISLANG_BUDGET_SECONDS", None)

    # -- local never trips the time half (no time computed) --------------------
    os.environ["VISLANG_BUDGET_SECONDS"] = "0.0000001"
    el = E.estimate_plan_cost(info=info, narrowing=nw, site="local")   # tiny bytes
    check("local_size_only_gate", not el.over_budget)
    os.environ.pop("VISLANG_BUDGET_SECONDS", None)

    # -- render sink: browser payload present for a volume ---------------------
    erd = E.estimate_plan_cost(info=info, narrowing=Narrowing(project=("temp",)),
                               site="local", sink_kind="render")
    check("render_payload_present", erd.browser_payload_mb is not None
          and approx(erd.browser_payload_mb, expect))    # 1 field, float32 cast

    # -- format renders without error ------------------------------------------
    txt = E.format_plan_estimate(eb)
    check("format_has_over_budget", "OVER BUDGET" in txt)

    print(f"\n{len(PASS)} checks passed.")


if __name__ == "__main__":
    main()
