"""The read next to the data: priced before a remote run, held over its own
budget, and measured after.

Plain-python asserts (no pytest on the cluster). Run from the repo root:
    python tests/test_server_read.py
Covers: the estimate's `server_read_mb` per reader kind (a memory map read in
whole rows, an element-granular strided read, full columns, a file read whole),
a coarsen reading every cell of its region while its result is small, the mask
variable a threshold reads, named boxes, a whole-file fetch; the read budget
(VISLANG_BUDGET_READ_BYTES) holding a request whose wire cost is small, end to
end through the simulated remote; and the executor's measured `read_bytes`
carried onto the caller's `remote_exec` record.
"""

import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from vislang.formats.dataset_info import DatasetInfo
from vislang.interpreter.narrowing import Narrowing, AxisRange, Coarsen, VoxelMask, Predicate
import vislang.interpreter.estimate as E

PASS = []
_MB = 1024 ** 2
CHANNEL = (10240, 7680, 1536)           # Case Study 3's file, x fastest, float64


def check(name, cond, detail=""):
    assert cond, f"{name}: {detail}"
    PASS.append(name)
    print(f"  ok  {name}")


def info(filetype, shape=CHANNEL, variables=("u",), itemsize=8):
    return DatasetInfo("f", filetype, list(variables), dimensions={"grid": shape},
                       itemsizes={v: itemsize for v in variables})


def ranges(z=(1, 1535), step=1):
    return [AxisRange(None, None, step), AxisRange(None, None, step),
            AxisRange(z[0], z[1], step)]


def remote(inf, narrowing, **kw):
    return E.estimate_plan_cost(info=inf, narrowing=narrowing, site="remote",
                                wire_vars=kw.pop("wire_vars", narrowing.project), **kw)


def test_estimate():
    print("== the estimate prices the read next to the data ==")
    raw = info("raw")
    stride = Narrowing(grid_ranges=ranges(step=16), project=("u",))
    low = Narrowing(grid_ranges=ranges(), project=("u",),
                    post_ops=(Coarsen(factors=(16, 16, 16), how="min"),))
    es, ec = remote(raw, stride), remote(raw, low)

    result = 640 * 480 * 96 * 8 / _MB
    check("stride and block minimum send the same bytes",
          abs(es.read_mb - result) < 1e-9 and abs(ec.read_mb - result) < 1e-9,
          (es.read_mb, ec.read_mb, result))
    rows, span = 480 * 96, 639 * 16 + 1
    check("a stride on a memory map reads whole rows along x (the contiguous axis)",
          abs(es.server_read_mb - rows * span * 8 / _MB) < 1e-9, es.server_read_mb)
    check("... and says why", any("contiguous axis" in n for n in es.notes), es.notes)
    check("a coarsen reads every cell of its region",
          abs(ec.server_read_mb - 10240 * 7680 * 1534 * 8 / _MB) < 1e-6, ec.server_read_mb)
    ratio = ec.server_read_mb / es.server_read_mb
    check("... about 256 times the stride's read", 250 < ratio < 260, ratio)

    print("== the read budget holds what the wire budget would not ==")
    check("the block minimum is over the read budget only", ec.over_budget
          and ec.budget_reason == "read", (ec.over_budget, ec.budget_reason))
    check("the stride is not held", not es.over_budget, es.budget_reason)
    os.environ["VISLANG_BUDGET_READ_BYTES"] = str(2 * 1024 ** 4)
    try:
        check("a larger read budget lets the block minimum through",
              not remote(raw, low).over_budget)
    finally:
        os.environ.pop("VISLANG_BUDGET_READ_BYTES")
    os.environ["VISLANG_BUDGET_BYTES"] = "1000"
    try:
        both = remote(raw, low)
        check("over the wire and the read budgets at once", both.budget_reason == "size+read",
              both.budget_reason)
    finally:
        os.environ.pop("VISLANG_BUDGET_BYTES")
    text = E.format_plan_estimate(ec)
    check("the report names the server read", "read on the server: ~" in text
          and "GB" in text and "over the wire: ~225 MB" in text, text)
    check("the report says which budget", "OVER BUDGET (read)" in text, text)
    loc = E.estimate_plan_cost(info=raw, narrowing=low, site="local")
    check("a local run has no server read: its disk read is read_mb, on the size budget",
          loc.server_read_mb is None and loc.budget_reason == "size"
          and abs(loc.read_mb - ec.server_read_mb) < 1e-6, loc)

    print("== each kind of reader ==")
    h5 = info("HDF5")
    eh = remote(h5, stride)
    check("a strided read that declares no contiguous axis is priced by element",
          abs(eh.server_read_mb - 640 * 480 * 96 * 8 / _MB) < 1e-9, eh.server_read_mb)
    crop = Narrowing(grid_ranges=[AxisRange(4608, 5632, 1), AxisRange(3328, 4352, 1),
                                  AxisRange(1, 65, 1)], project=("u",))
    ecrop = remote(raw, crop)
    check("an unstrided box reads its own cells", abs(ecrop.server_read_mb
          - 1024 * 1024 * 64 * 8 / _MB) < 1e-9 and ecrop.server_read_mb == ecrop.read_mb)
    vtk = info("VTK", shape=(60, 50, 6), variables=("u", "v", "w", "theta"))
    ev = remote(vtk, Narrowing(grid_ranges=[AxisRange(None, None, 8)] * 3, project=("u",)),
                n_timesteps=3)
    check("a format read whole reads every variable, whole, per file",
          abs(ev.server_read_mb - 3 * 4 * 60 * 50 * 6 * 8 / _MB) < 1e-9, ev.server_read_mb)
    two = info("raw", shape=(64, 64, 64), variables=("u", "p"))
    masked = Narrowing(grid_ranges=[AxisRange(None, None, 1)] * 3, project=("u",),
                       post_ops=(VoxelMask(predicates=(Predicate("p", "<", 0.0),)),))
    em = remote(two, masked)
    check("a threshold's variable is read too, though it is not sent",
          abs(em.server_read_mb - 2 * 64 ** 3 * 8 / _MB) < 1e-9
          and abs(em.read_mb - 64 ** 3 * 8 / _MB) < 1e-9, (em.server_read_mb, em.read_mb))
    fetch = E.estimate_plan_cost(info=DatasetInfo("f", "remote", []), narrowing=None,
                                 site="remote", read_mb_override=500.0)
    check("a whole-file fetch reads the file it copies", fetch.server_read_mb == 500.0)

    print("== named boxes ==")
    box = Narrowing(grid_ranges=[AxisRange(0, 32, 1)] * 3, project=("u",))
    eb = E.estimate_boxes_cost(info=raw, boxes=[(box, 3), (box, 2)], site="remote")
    check("a format read in part reads each box",
          abs(eb.server_read_mb - 5 * 32 ** 3 * 8 / _MB) < 1e-9, eb.server_read_mb)
    ebv = E.estimate_boxes_cost(info=vtk, boxes=[(box, 1), (box, 1)], site="remote",
                                n_timesteps=2)
    check("a format read whole is read once per file, whatever its boxes",
          abs(ebv.server_read_mb - 2 * 4 * 60 * 50 * 6 * 8 / _MB) < 1e-9, ebv.server_read_mb)


def test_held_end_to_end(tmp):
    print("== end to end: the simulated remote holds a block reduction, not a stride ==")
    from fakes_remote import EXECS, install_fake_remote, install_login_inspect
    from vislang.dsl import reset_sinks
    from vislang.dsl.forms import source, region, subsample, coarsen, save
    from vislang.interpreter.planner import plan_pipeline

    arr = np.arange(32 * 32 * 32, dtype=np.float64).reshape((32, 32, 32), order="F")
    p = os.path.join(tmp, "u_32x32x32_float64.raw")
    arr.ravel(order="F").tofile(p)
    env = {"VISLANG_CACHE": os.path.join(tmp, "cache"), "VISLANG_REMOTE": "force",
           "VISLANG_REMOTE_TMP": os.path.join(tmp, "rtmp"),
           # 32^3 float64 = 256 KiB: a budget between the stride's read and the whole
           "VISLANG_BUDGET_READ_BYTES": str(100 * 1024)}
    os.environ.update(env)
    os.makedirs(env["VISLANG_REMOTE_TMP"], exist_ok=True)
    install_fake_remote()
    install_login_inspect()
    try:
        far = f"u@fakehost:{p}"
        n0 = len(EXECS)
        reset_sinks()
        held = plan_pipeline(save(coarsen(source(far), 8, how="min"),
                                  os.path.join(tmp, "low.npz")))
        check("the block minimum is held before anything runs",
              held.get("needs_confirm") and not held.get("materialized")
              and len(EXECS) == n0, {k: held.get(k) for k in ("needs_confirm", "materialized")})
        est = held.get("estimate")
        check("... on the read, with the price in the report",
              est is not None and est.budget_reason == "read"
              and abs(est.server_read_mb - 32 ** 3 * 8 / _MB) < 1e-9, est)
        reset_sinks()
        ok = plan_pipeline(save(coarsen(source(far), 8, how="min"),
                                os.path.join(tmp, "low.npz")), confirm=True)
        check("confirm approves it, and it runs next to the data",
              ok.get("materialized") and len(EXECS) == n0 + 1, ok.get("materialized"))
        reset_sinks()
        st = plan_pipeline(save(subsample(region(source(far), z=(1, 32)), 8),
                                os.path.join(tmp, "quick.npz")))
        check("the stride is under the read budget and runs unasked",
              st.get("materialized") and not st.get("needs_confirm"))

        print("== a later run prices with the file's element size (from the cached schema) ==")
        from vislang.remote.catalog import ExtentCatalog
        from vislang.runtime.paths import cache_root
        schemas = ExtentCatalog(cache_root())._manifest["schemas"]
        check("the cached schema keeps the element size",
              any((v.get("itemsizes") or {}).get("u") == 8 for v in schemas.values()), schemas)
        reset_sinks()
        again = plan_pipeline(save(subsample(region(source(far), z=(1, 32)), 4),
                                   os.path.join(tmp, "quick4.npz")))
        est = again.get("estimate")
        kept = 8 * 8 * 8                        # 32 / 4 per axis (z: 31 cells from 1 -> 8)
        check("... so a run after the first prices 8 B per float64 value, not 4",
              est is not None and abs(est.read_mb - kept * 8 / 2 ** 20) < 1e-12
              and not any("dtype not fully known" in n for n in est.notes),
              None if est is None else (est.read_mb, kept * 8 / 2 ** 20, est.notes))
    finally:
        for k in env:
            os.environ.pop(k, None)
        reset_sinks()


def test_measured(tmp):
    print("== the executor measures what it read ==")
    import vislang.remote.executor as X
    import vislang.runtime.timing as timing
    fake = os.path.join(tmp, "io")
    with open(fake, "w") as f:
        f.write("rchar: 10\nwchar: 2\nsyscr: 1\nsyscw: 1\nread_bytes: 4096\n"
                "write_bytes: 0\ncancelled_write_bytes: 0\n")
    saved = X._PROC_IO
    X._PROC_IO = fake
    try:
        check("read_bytes is parsed from /proc/self/io", X._read_bytes() == 4096)
        X._clock.update(t0=0.0, io0=X._read_bytes(), rec=None)
        with open(fake, "w") as f:
            f.write("rchar: 10\nread_bytes: 1052672\n")
        t = X._server_timing()
        check("the server's timing carries the bytes read since entry",
              t.get("read_bytes") == 1052672 - 4096, t)
        X._PROC_IO = os.path.join(tmp, "absent")
        X._clock["io0"] = None
        check("no /proc (not Linux): nothing is claimed",
              X._read_bytes() is None and "read_bytes" not in X._server_timing())
    finally:
        X._PROC_IO = saved
        X._clock.update(t0=None, io0=None, rec=None)
    rec = {"name": "remote_exec", "s": 3.0}
    timing.note_server(rec, {"server_s": 2.0, "read_bytes": 1048576})
    check("the caller keeps it beside server_s", rec.get("server_read_bytes") == 1048576
          and rec.get("server_s") == 2.0, rec)
    rec = {"name": "remote_exec", "s": 3.0}
    timing.note_server(rec, {"server_s": 2.0})
    check("... and records nothing when the server could not measure",
          "server_read_bytes" not in rec, rec)


if __name__ == "__main__":
    for k in ("VISLANG_BUDGET_BYTES", "VISLANG_BUDGET_SECONDS", "VISLANG_BUDGET_READ_BYTES"):
        os.environ.pop(k, None)
    test_estimate()
    with tempfile.TemporaryDirectory() as tmp:
        test_held_end_to_end(tmp)
        test_measured(tmp)
    print(f"\nall {len(PASS)} checks passed")
