"""The extent cache serves a request from a cached extent of its written start:
the rest (threshold, coarsen) runs here, and order is part of the meaning.

Plain-python asserts (no pytest on the cluster). Run from the repo root:
    python tests/test_cache_derive.py
Covers the rule (`implies`, `derivation`): a cached start plus local forms, a
looser cached threshold tightened, and the misses that keep it sound: written
order ([subsample, threshold] never serves [threshold, subsample]), a stride the
cache cannot give, a looser request, another variable, a coarsen on the other
side of a threshold. End to end through the simulated remote: every derived
result equals the one a fresh remote read gives (grid, points, a coarsened grid
and its placement, a series), no remote job runs, a derived request is not held
by the read budget, and an integer grid whose mask cast could round is refused.
"""

import os
import shutil
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from vislang.remote.catalog import implies, derivation

PASS = []


def check(name, cond, detail=""):
    assert cond, f"{name}: {detail}"
    PASS.append(name)
    print(f"  ok  {name}")


S2, S3 = ["subsample", 2, []], ["subsample", 3, []]
CMIN = ["coarsen", 4, [], "min"]
REG = ["region", [["x", 0, 16]]]


def thr(op, v, var="x"):
    return ["threshold", var, op, v]


def test_rule():
    print("== which predicates imply which ==")
    for req, had, want in [
            (("<", 1), ("<", 2), True), (("<", 2), ("<", 2), True), (("<", 3), ("<", 2), False),
            (("<=", 2), ("<", 2), False), (("<=", 1), ("<", 2), True), (("<", 2), ("<=", 2), True),
            ((">", 5), (">=", 5), True), ((">=", 5), (">", 5), False), ((">", 6), (">", 5), True),
            (("==", 1), ("<", 2), True), (("==", 2), ("<", 2), False), (("==", 3), ("!=", 2), True),
            (("<", 2), ("!=", 2), True), (("<", 3), ("!=", 2), False), (("!=", 2), ("!=", 2), True),
            (("!=", 1), ("<", 2), False), (("<", 1), (">", 0), False)]:
        check(f"{req[0]}{req[1]} implies {had[0]}{had[1]}: {want}", implies(req, had) is want)

    print("== what a cached extent can serve ==")
    r = derivation
    check("stride 2 serves stride 2 then a threshold", r([S2, thr("<", 2)], [S2]) == ([thr("<", 2)], False))
    check("a looser cached threshold serves a stricter one",
          r([S2, thr("<", 1)], [S2, thr("<", 2)]) == ([thr("<", 1)], True))
    check("a coarsen serves its own threshold", r([REG, CMIN, thr("<", 0)], [REG, CMIN]) is not None)
    check("a cached start serves threshold then coarsen", r([S2, thr("<", 0), CMIN], [S2]) is not None)
    check("written order: [subsample, threshold] does not serve [threshold, subsample]",
          r([thr("<", 2), S2], [S2, thr("<", 2)]) is None)
    check("... nor the reverse", r([S2, thr("<", 2)], [thr("<", 2), S2]) is None)
    check("a stride the cache cannot give", r([S3, thr("<", 2)], [S2]) is None)
    check("a looser request than the cache", r([S2, thr("<", 3)], [S2, thr("<", 2)]) is None)
    check("a threshold on another variable", r([S2, thr("<", 1, "y")], [S2, thr("<", 2)]) is None)
    check("masking before a coarsen is not masking after it",
          r([thr("<", 0), CMIN], [CMIN]) is None and r([CMIN, thr("<", 0)], [thr("<", 0), CMIN]) is None)
    check("a region after the cached start is not run here", r([S2, REG], [S2]) is None)
    check("the same chain is an exact match, not a derivation", r([S2, thr("<", 2)], [S2, thr("<", 2)]) is None)
    check("a cached extent longer than the request serves nothing", r([S2], [S2, thr("<", 2)]) is None)


# ---------------------------------------------------------------------------
# End to end, through the simulated remote
# ---------------------------------------------------------------------------
class Remote:
    """The simulated remote with its own extent cache; `run` reports whether a
    remote job ran, and returns the saved arrays."""

    def __init__(self, tmp):
        from fakes_remote import EXECS, install_fake_remote, install_login_inspect
        self.tmp, self.execs = tmp, EXECS
        os.environ["VISLANG_REMOTE"] = "force"
        os.environ["VISLANG_REMOTE_TMP"] = os.path.join(tmp, "rtmp")
        os.environ["VISLANG_PROVENANCE"] = "0"
        os.makedirs(os.environ["VISLANG_REMOTE_TMP"], exist_ok=True)
        install_fake_remote()
        install_login_inspect()
        self.n = 0

    def cache(self, name):
        os.environ["VISLANG_CACHE"] = os.path.join(self.tmp, name)

    def run(self, node, confirm=True):
        from vislang.dsl import reset_sinks
        from vislang.dsl.forms import save
        from vislang.interpreter.planner import plan_pipeline
        self.n += 1
        out = os.path.join(self.tmp, f"out{self.n}.npz")
        reset_sinks()
        n0 = len(self.execs)
        res = plan_pipeline(save(node, out), confirm=confirm)
        ran = len(self.execs) > n0
        if not res.get("materialized"):
            return ran, None, res
        with np.load(out) as z:
            return ran, {k: z[k] for k in z.files}, res

    def fresh(self, node):
        """The same request against an empty cache: what the remote computes."""
        keep = os.environ["VISLANG_CACHE"]
        self.cache(f"fresh{self.n}")
        try:
            ran, arrays, _ = self.run(node)
        finally:
            os.environ["VISLANG_CACHE"] = keep
        assert ran
        return arrays


def same(a, b):
    return (a is not None and b is not None and set(a) == set(b)
            and all(np.array_equal(a[k], b[k], equal_nan=a[k].dtype.kind == "f") for k in a))


def test_end_to_end(tmp):
    from vislang.dsl.forms import source, subsample, threshold, coarsen, region, fields, timesteps
    rem = Remote(tmp)
    rng = np.random.default_rng(7)

    print("== a grid: a stride, then cuts of it ==")
    arr = rng.normal(size=(32, 32, 32))
    p = os.path.join(tmp, "x_32x32x32_float64.raw")
    arr.ravel(order="F").tofile(p)
    A = lambda: subsample(source(f"u@fakehost:{p}"), 2)
    rem.cache("grid")
    ran, _, _ = rem.run(A())
    check("the stride is read next to the data", ran)
    for label, node in [("a threshold of the cached stride", threshold(A(), "x < 0.5")),
                        ("a stricter threshold, from the cached x < 0.5", threshold(A(), "x < -0.5")),
                        ("a threshold, then a block minimum", coarsen(threshold(A(), "x < 0.5"), 4, how="min"))]:
        ran, got, res = rem.run(node)
        check(f"{label}: no remote job", not ran)
        check(f"{label}: the same arrays and placement a fresh read gives", same(got, rem.fresh(node)),
              None if got is None else sorted(got))
    ran, _, _ = rem.run(threshold(A(), "x < 1.5"))
    check("a threshold looser than the cached cut is served from the cached stride itself", not ran)
    ran, _, _ = rem.run(threshold(subsample(source(f"u@fakehost:{p}"), 3), "x < 0.5"))
    check("another stride goes to the remote", ran)

    print("== a coarsened grid and its threshold (Case Study 3's narrowing) ==")
    rem.cache("coarse")
    low = lambda: coarsen(region(source(f"u@fakehost:{p}"), z=(1, 31)), 8, how="min")
    os.environ["VISLANG_BUDGET_READ_BYTES"] = str(64 * 1024)        # below the region's 240 KiB
    try:
        ran, _, held = rem.run(low(), confirm=False)
        check("the block minimum is held on its read", held.get("needs_confirm") and not ran)
        ran, base, _ = rem.run(low())
        check("approved, it runs next to the data", ran and base is not None)
        os.environ["VISLANG_TIMING"] = "1"
        os.environ["VISLANG_TIMINGS_FILE"] = os.path.join(tmp, "derive_timings.jsonl")
        import vislang.runtime.timing as timing
        try:
            with timing.run("spec.py"):
                ran, got, res = rem.run(threshold(low(), "x < -2"), confirm=False)
        finally:
            os.environ.pop("VISLANG_TIMINGS_FILE")
        import json as _json
        rec = _json.loads(open(os.path.join(tmp, "derive_timings.jsonl")).read().splitlines()[-1])
        check("its threshold is not held and runs no remote job", not ran and got is not None
              and not res.get("needs_confirm"))
        check("... and the run record says it was derived from the cache",
              (rec.get("pipelines") or [{}])[0].get("route") == "catalog_derived", rec.get("pipelines"))
        check("... the blocks a fresh read keeps, placed where it places them",
              same(got, rem.fresh(threshold(low(), "x < -2"))))
    finally:
        os.environ.pop("VISLANG_BUDGET_READ_BYTES")
    ran, _, _ = rem.run(coarsen(threshold(region(source(f"u@fakehost:{p}"), z=(1, 31)), "x < -2"),
                                8, how="min"))
    check("masking before the block minimum is another request: remote", ran)

    print("== points: written order decides ==")
    n = 4000
    pts = {"x": rng.uniform(0, 10, n), "y": rng.uniform(0, 10, n), "z": rng.uniform(0, 10, n),
           "v": rng.normal(size=n)}
    q = os.path.join(tmp, "pts.npz")
    np.savez(q, **pts)
    P = lambda: source(f"u@fakehost:{q}")
    rem.cache("points")
    ran, _, _ = rem.run(threshold(subsample(P(), 2), "v < 0"))
    check("sample then cut: read next to the data", ran)
    ran, got, _ = rem.run(subsample(threshold(P(), "v < 0"), 2))
    check("cut then sample, same numbers: not served from it", ran)
    check("... and indeed a different answer", not same(got, rem.fresh(threshold(subsample(P(), 2), "v < 0"))))
    ran, got, _ = rem.run(threshold(subsample(P(), 2), "v < -1"))
    check("a stricter cut after the same sample: served here", not ran)
    check("... the rows a fresh read keeps", same(got, rem.fresh(threshold(subsample(P(), 2), "v < -1"))))
    ran, _, _ = rem.run(threshold(fields(subsample(P(), 2), ["x"]), "y < 5"))
    check("a cut on a variable the cache does not hold under that start: remote", ran)

    print("== a series: each timestep from its cached start ==")
    d = os.path.join(tmp, "series")
    os.makedirs(d)
    for t in (1, 2, 3):
        rng.normal(size=(16, 16, 16)).ravel(order="F").tofile(os.path.join(d, f"x#{t}_16x16x16_float64.raw"))
    S = lambda: subsample(timesteps(source(f"u@fakehost:{d}"), 1, 3), 2)
    from vislang.dsl import reset_sinks
    from vislang.dsl.forms import save
    from vislang.interpreter.planner import plan_pipeline
    rem.cache("series")
    reset_sinks()
    plan_pipeline(save(S(), os.path.join(tmp, "base")), confirm=True)
    outs = {}
    for name, cache in (("cached", "series"), ("fresh", "series_fresh")):
        rem.cache(cache)
        n0 = len(rem.execs)
        reset_sinks()
        plan_pipeline(save(threshold(S(), "x < 0"), os.path.join(tmp, name)), confirm=True)
        outs[name] = (len(rem.execs) > n0, {t: dict(np.load(os.path.join(tmp, name, f"timestep#{t}.npz")))
                                           for t in (1, 2, 3)})
    check("every timestep's threshold from its cached stride: no remote job", not outs["cached"][0])
    check("... equal to a fresh read, timestep by timestep",
          all(same(outs["cached"][1][t], outs["fresh"][1][t]) for t in (1, 2, 3)))

    print("== an integer grid: a mask's float32 cast must be exact to tighten ==")
    big = np.full((8, 8, 8), 2 ** 25, dtype=np.int32)
    big[::2] += 1                                   # 2**25 + 1 rounds to 2**25 in float32
    b = os.path.join(tmp, "k_8x8x8_int32.raw")
    big.ravel(order="F").tofile(b)
    rem.cache("ints")
    rem.run(threshold(source(f"u@fakehost:{b}"), "k < 50000000"))
    ran, got, _ = rem.run(threshold(source(f"u@fakehost:{b}"), "k < 33554433"))
    check("values the cast may have rounded: the tighter cut goes to the remote", ran)
    small = np.arange(512, dtype=np.int32).reshape(8, 8, 8)
    c = os.path.join(tmp, "m_8x8x8_int32.raw")
    small.ravel(order="F").tofile(c)
    rem.run(threshold(source(f"u@fakehost:{c}"), "m < 400"))
    ran, got, _ = rem.run(threshold(source(f"u@fakehost:{c}"), "m < 100"))
    check("small integers (exact in float32): served here", not ran)
    check("... as a fresh read gives them", same(got, rem.fresh(threshold(source(f"u@fakehost:{c}"), "m < 100"))))


def test_estimate(tmp):
    """The dry run asks the extent cache what the run would ask it, so the
    price shown before a request is the price the run pays."""
    from vislang.dsl import reset_sinks
    from vislang.dsl.forms import source, subsample, threshold, coarsen, region, fields, timesteps, save
    from vislang.interpreter.planner import plan_pipeline
    rem = Remote(tmp)
    rng = np.random.default_rng(11)

    def estimate(node, out="est.npz"):
        reset_sinks()
        res = plan_pipeline(save(node, os.path.join(tmp, out)), dry_run=True)
        return res["estimate"], "\n".join(res["steps"])

    print("== the estimate deducts what the cache holds ==")
    p = os.path.join(tmp, "u_32x32x32_float64.raw")
    rng.normal(size=(32, 32, 32)).ravel(order="F").tofile(p)
    low = lambda: coarsen(region(source(f"u@fakehost:{p}"), z=(1, 31)), 8, how="min")
    rem.cache("estimate")
    os.environ["VISLANG_BUDGET_READ_BYTES"] = str(64 * 1024)
    try:
        before, _ = estimate(threshold(low(), "u < -2"))
        check("before anything is cached: the whole read, held",
              before.server_read_mb > 0 and before.budget_reason == "read", before)
        check("... and it says the cache holds nothing of it",
              any("nothing for this request is in the extent cache" in n for n in before.notes),
              before.notes)
        rem.run(low())
        after, text = estimate(threshold(low(), "u < -2"))
        check("after the block minimum is cached: its threshold reads and moves nothing",
              after.server_read_mb == 0 and after.read_mb == 0 and not after.over_budget, after)
        check("... the result size is still the request's", after.output_mb == before.output_mb)
        check("... and the report says how", "served from the extent cache" in text
              and "threshold u < -2.0 applied here" in text and "read on the server: ~0 MB" in text,
              text)
        ran, _, res = rem.run(threshold(low(), "u < -2"), confirm=False)
        check("the run agrees: not held, no remote job", not ran and res.get("materialized"))
        exact, _ = estimate(low())
        check("the cached request itself: nothing to read", exact.server_read_mb == 0
              and any("cached results" in n for n in exact.notes), exact.notes)
        other, _ = estimate(coarsen(region(source(f"u@fakehost:{p}"), z=(1, 31)), 8, how="max"))
        check("another reduction is priced in full", other.server_read_mb == before.server_read_mb
              and other.over_budget, other)
    finally:
        os.environ.pop("VISLANG_BUDGET_READ_BYTES")

    print("== a schema from an older cache version still lets the estimate see the cache ==")
    from vislang.remote.catalog import ExtentCatalog, _SCHEMA_VERSION_KEY
    from vislang.runtime.paths import cache_root
    cat = ExtentCatalog(cache_root())
    with cat._update() as m:
        for entry in m["schemas"].values():
            entry[_SCHEMA_VERSION_KEY] = 1          # as if written by an older build
    stale, _ = estimate(threshold(low(), "u < -2"))
    check("the cached result is still found: nothing read, nothing moved",
          stale.server_read_mb == 0 and stale.read_mb == 0, stale)

    print("== part of a request cached: priced is the rest ==")
    n = 2000
    q = os.path.join(tmp, "pts2.npz")
    np.savez(q, x=rng.uniform(size=n), y=rng.uniform(size=n), z=rng.uniform(size=n),
             v=rng.normal(size=n), w=rng.normal(size=n))
    rem.run(fields(subsample(source(f"u@fakehost:{q}"), 2), ["v"]))
    both, _ = estimate(fields(subsample(source(f"u@fakehost:{q}"), 2), ["v", "w"]))
    w_only, _ = estimate(fields(subsample(source(f"u@fakehost:{q}"), 2), ["w"]))
    check("one of two variables cached: priced as the other alone",
          both.read_mb == w_only.read_mb and both.server_read_mb == w_only.server_read_mb
          and both.read_mb > 0, (both.read_mb, w_only.read_mb))
    check("... and it says so", any("1 of 2 variable(s)" in m for m in both.notes), both.notes)

    print("== a series: the timesteps still to read ==")
    d = os.path.join(tmp, "eseries")
    os.makedirs(d)
    for t in (1, 2, 3, 4):
        rng.normal(size=(16, 16, 16)).ravel(order="F").tofile(os.path.join(d, f"u#{t}_16x16x16_float64.raw"))
    S = lambda a, b: subsample(timesteps(source(f"u@fakehost:{d}"), a, b), 2)
    reset_sinks()
    plan_pipeline(save(S(1, 3), os.path.join(tmp, "ebase")), confirm=True)
    three, _ = estimate(threshold(S(1, 3), "u < 0"), "eser")
    check("every selected timestep derivable: nothing to read", three.server_read_mb == 0
          and three.read_mb == 0, three)
    four, _ = estimate(threshold(S(1, 4), "u < 0"), "eser")
    one, _ = estimate(threshold(S(4, 4), "u < 0"), "eser")
    check("one timestep not cached: priced as that one", abs(four.server_read_mb - one.server_read_mb) < 1e-9
          and four.n_timesteps == 4 and any("3 of 4 timestep(s)" in m for m in four.notes),
          (four.server_read_mb, one.server_read_mb, four.notes))


if __name__ == "__main__":
    test_rule()
    tmp = tempfile.mkdtemp(prefix="vislang_derive_test_")
    try:
        test_end_to_end(tmp)
        test_estimate(tmp)
    finally:
        for k in ("VISLANG_CACHE", "VISLANG_REMOTE", "VISLANG_REMOTE_TMP", "VISLANG_PROVENANCE"):
            os.environ.pop(k, None)
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\nall {len(PASS)} checks passed")
