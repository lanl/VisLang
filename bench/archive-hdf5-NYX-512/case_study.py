#!/usr/bin/env python3
"""Run the Case Study session and write the paper's tables + a readable report.

Every measurement goes through the real `run_pipeline` — the same entry point an
interactive session uses — so nothing here is a special benchmark path. The
harness (vislang_timing.py) records each run; `summarize.py` tabulates them.

Three run groups, in order, plus one table derived from them:

  E1 movement       one 6.98 GB remote snapshot, narrowed to 2 of 13 fields at
                    stride 2. Run twice: once unconfirmed (the cost gate HOLDS —
                    zero bytes move) and once confirmed (the actual transfer), so
                    predicted and actual sit in the same table.
  E2 iteration      five successive queries over a 3-snapshot (20.9 GB) remote
                    timeseries: cold, +1 field, narrower time range, verbatim
                    re-issue, and a sub-region of an already-cached extent. This
                    is the catalog/reuse evidence.
  E3 mistake cost   four invalid requests (bad field, out-of-bounds region, bad
                    axis, render over a timeseries), local and remote, measuring
                    the seconds and bytes spent BEFORE rejection.
  estimate table    not a group and not extra runs: every remote run above
                    records its predicted cost next to what it actually moved,
                    which summarize.py tabulates as the cost-gate accuracy row.

Isolation: the driver uses its OWN catalog root and timings file under
bench/results/, so a cold start is genuinely cold and the user's own
`.vislang/cache` is never touched or polluted.

Usage:
    python bench/case_study.py                  # everything (needs an allocation)
    python bench/case_study.py --only E2 E3     # selected groups
    python bench/case_study.py --keep-cache     # don't wipe the bench catalog first
    python bench/case_study.py --dry            # print the specs, run nothing
"""

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

RESULTS = os.path.join(REPO, "bench", "results")
SPECS = os.path.join(REPO, "bench", "specs")
OUT = os.path.join(RESULTS, "out")

# The remote datasets under test. NYX_SERIES exposes three REAL Nyx snapshots
# (redshifts z5, z42, z54 — 6.98 GB each) under Sieve's `#N` timestep convention
# via symlinks: no data is copied or synthesized, only named.
NYX_FILE = ("ssh://darwin/projects/autonomousvis/ashrestha/nyx_series/nyx512#1.hdf5")
NYX_SERIES = "ssh://darwin/projects/autonomousvis/ashrestha/nyx_series"
TEMP = "native_fields/temperature"
BARYON = "native_fields/baryon_density"

# The env a real session runs under (mirrors .mcp.json), plus this driver's
# isolation. VISLANG_REMOTE=force is a SAFETY setting as much as a measurement
# one: it fails loudly rather than silently falling back to fetching 20.9 GB.
ENV = {
    "VISLANG_SRUN_JOBID": "auto",
    "VISLANG_SRUN_NAME": "vislang",
    "VISLANG_REMOTE_PYTHON": "/vast/home/ashrestha/.conda/envs/autoviz/bin/python",
    "VISLANG_REMOTE_REPO": "/vast/projects/autonomousvis/ashrestha/code/VisLang",
    "VISLANG_REMOTE": "force",
    "VISLANG_TIMING": "1",
    "VISLANG_CACHE": os.path.join(RESULTS, "cache"),
    "VISLANG_TIMINGS_FILE": os.path.join(RESULTS, "timings.jsonl"),
    "VISLANG_TRACE_FILE": os.path.join(RESULTS, "trace.log"),
}


class Q:
    """One measured query: a spec, why it is in the paper, and what to expect.

    `expect` is prose, not an assertion — the point of the run is to find out. It
    is printed next to the outcome so a surprise is visible instead of buried."""

    def __init__(self, qid, group, title, spec, why, expect, confirm=False):
        self.qid, self.group, self.title = qid, group, title
        self.spec, self.why, self.expect, self.confirm = spec, why, expect, confirm


def _save(node_expr, name):
    return f'save({node_expr}, "{os.path.join(OUT, name)}")'


# --- E1: data movement, and the gate that precedes it -----------------------
E1 = [
    Q("E1a", "E1", "Narrowed snapshot — cost gate holds",
      _save(f'subsample(fields(source("{NYX_FILE}"), ["{TEMP}", "{BARYON}"]), 2)',
            "e1_sub2.hdf5"),
      "The gate prices the request from metadata before any bulk read. A HELD run "
      "is the claim that Sieve can refuse a request without paying for it.",
      "HELD over budget; 0 bytes over the wire; an estimate of ~128 MiB and a "
      "measured time band.",
      confirm=False),
    Q("E1b", "E1", "Narrowed snapshot — confirmed, executed",
      _save(f'subsample(fields(source("{NYX_FILE}"), ["{TEMP}", "{BARYON}"]), 2)',
            "e1_sub2.hdf5"),
      "The headline movement number: 2 of 13 fields at stride 2, reduced next to "
      "the data. The denominator is the 6.98 GB an ad-hoc copy would have moved.",
      "~128 MiB over the wire (256^3 x 4 B x 2 fields = 134,217,728 B), a ~52x "
      "reduction; wall clock dominated by the pull, not the remote read.",
      confirm=True),
]

# --- E2: the iterative session (the catalog claim) --------------------------
_F2 = f'["{TEMP}", "{BARYON}"]'
E2 = [
    Q("E2q1", "E2", "Cold: one field, three timesteps",
      _save(f'subsample(fields(timesteps(source("{NYX_SERIES}"), 0, 2), ["{TEMP}"]), 4)',
            "e2q1"),
      "The first question of a session. Nothing is cached, so this is the price "
      "of entry against which every later query is compared.",
      "3/3 (timestep,variable) extents fetched, 0 reused; ~24 MiB over the wire "
      "(128^3 x 4 B x 3 = 25,165,824 B).",
      confirm=True),
    Q("E2q2", "E2", "+1 field (per-variable delta)",
      _save(f'subsample(fields(timesteps(source("{NYX_SERIES}"), 0, 2), {_F2}), 4)',
            "e2q2"),
      "The commonest follow-up: same view, one more variable. File-keyed caching "
      "cannot express this — it would refetch both fields.",
      "3/6 extents reused; only baryon_density crosses (~24 MiB), not temperature.",
      confirm=True),
    Q("E2q3", "E2", "Narrower time range (per-timestep reuse)",
      _save(f'subsample(fields(timesteps(source("{NYX_SERIES}"), 1, 1), {_F2}), 4)',
            "e2q3"),
      "Zooming in on one timestep of a range already held. The time axis is part "
      "of the cache key at per-timestep granularity.",
      "2/2 extents reused; nothing crosses the wire; no allocation needed.",
      confirm=True),
    Q("E2q4", "E2", "Verbatim re-issue (exact hit)",
      _save(f'subsample(fields(timesteps(source("{NYX_SERIES}"), 0, 2), {_F2}), 4)',
            "e2q4"),
      "Re-running a query — the reload, the re-render, the second look. Should "
      "cost nothing on the network at all.",
      "6/6 extents reused; 0 bytes; seconds, not minutes.",
      confirm=True),
    Q("E2q5", "E2", "Sub-region of a cached extent (superset slicing)",
      _save(f'subsample(region(fields(timesteps(source("{NYX_SERIES}"), 0, 2), '
            f'{_F2}), x=(0, 256)), 4)',
            "e2q5"),
      "The strongest form of reuse and the one the paper claims in III-F: the "
      "request is NARROWER than what is cached, so it should be sliced locally "
      "out of the wider stored extent rather than refetched. fields() is kept so "
      "the request stays on the catalog-aware path (without a projection the "
      "folder reduce cannot know each file's variables and falls back to a "
      "non-catalog batch over all 13 fields).",
      "the cached stride-4 extents contain and phase-align with x=(0,256), so "
      "they are reused; 0 bytes over the wire.",
      confirm=True),
]

# --- E3: what a rejected request costs -------------------------------------
E3 = [
    Q("E3a", "E3", "Misspelled field (remote)",
      _save(f'fields(source("{NYX_FILE}"), ["native_fields/temperatur"])',
            "e3a.hdf5"),
      "The most frequent authoring error. Where the rejection lands — local "
      "metadata, remote metadata, or after a read — is the measurement.",
      "rejected on schema, 0 bulk bytes; some seconds if the check needs the "
      "remote's login-node inspect.",
      confirm=True),
    Q("E3b", "E3", "Region out of bounds (remote)",
      _save(f'region(fields(source("{NYX_FILE}"), ["{TEMP}"]), x=(0, 99999))',
            "e3b.hdf5"),
      "A plausible slip on an unfamiliar grid. validate_narrowing runs against "
      "the real extents before any read.",
      "rejected against the 512^3 extent; 0 bulk bytes.",
      confirm=True),
    Q("E3c", "E3", "Unknown axis (remote)",
      _save(f'subsample(fields(source("{NYX_FILE}"), ["{TEMP}"]), w=2)',
            "e3c.hdf5"),
      "A form-level error: the DSL admits x/y/z only. Should be caught without "
      "touching the network at all.",
      "rejected while building the AST; 0 bytes, sub-millisecond.",
      confirm=True),
    Q("E3d", "E3", "render() over a timeseries (remote folder)",
      f'render(subsample(fields(timesteps(source("{NYX_SERIES}"), 0, 2), '
      f'["{TEMP}"]), 4))',
      "An unsupported combination rather than a typo — the planner refuses it by "
      "construction instead of materializing 3 timesteps and then failing.",
      "refused at plan time; 0 bytes.",
      confirm=True),
]

GROUPS = {"E1": E1, "E2": E2, "E3": E3}


def run_one(q, run_pipeline):
    """Write the spec, run it, and return a result row. A raised exception is a
    result too (E3 depends on it), so nothing here is allowed to abort the session."""
    path = os.path.join(SPECS, f"{q.qid}.py")
    with open(path, "w") as f:
        f.write(f"# {q.qid}: {q.title}\n{q.spec}\n")
    print(f"\n{'=' * 78}\n[{q.qid}] {q.title}\n  spec: {q.spec}")
    t0 = time.perf_counter()
    try:
        report = run_pipeline(path, confirm=q.confirm)
        err = None
    except Exception as e:                      # never abort the session
        report, err = "", f"{type(e).__name__}: {e}"
    dt = time.perf_counter() - t0
    status = "EXCEPTION"
    for line in (report or "").splitlines():
        if line.startswith("Status:"):
            status = line.split(":", 1)[1].strip()
            break
    print(f"  -> {status} in {dt:.1f}s" + (f"  ({err})" if err else ""))
    return {"qid": q.qid, "group": q.group, "title": q.title, "spec": q.spec,
            "why": q.why, "expect": q.expect, "confirm": q.confirm,
            "status": status, "driver_s": round(dt, 2), "error": err,
            "report": report}


def write_session_md(rows, recs, path):
    """The human-readable companion to the CSVs: one narrated section per query,
    with the spec, what the run actually did, and the numbers pulled out of the
    timing record. The CSVs are for plotting; this is for reading and for writing
    the paper text from."""
    import summarize

    by_run = {}
    for r in recs:
        by_run.setdefault(r.get("spec"), []).append(r)

    def rec_for(qid):
        want = os.path.join(SPECS, f"{qid}.py")
        got = by_run.get(want) or []
        return got[-1] if got else None

    lines = [f"# Sieve case study — session of {datetime.now():%Y-%m-%d %H:%M}",
             "",
             "Every row was produced by `run_pipeline` (the same entry point an ",
             "interactive session uses) and measured by `vislang_timing.py`. ",
             "Bytes labelled *over the wire* are what actually crossed the network; ",
             "*source* is what a whole-file/whole-folder copy would have moved.",
             ""]
    groups = {}
    for r in rows:
        groups.setdefault(r["group"], []).append(r)

    titles = {"E1": "E1 — Data movement, and the gate before it",
              "E2": "E2 — An iterative session (catalog reuse)",
              "E3": "E3 — What a rejected request costs"}
    for g, rs in groups.items():
        lines += [f"## {titles.get(g, g)}", ""]
        for r in rs:
            rec = rec_for(r["qid"])
            lines += [f"### {r['qid']} — {r['title']}", "",
                      "```python", r["spec"], "```", "",
                      f"**Why it is in the paper.** {r['why']}", "",
                      f"**Expected.** {r['expect']}", "",
                      f"**Outcome.** `{r['status']}`"
                      + (f" — {r['error']}" if r["error"] else "")
                      + f", {r['driver_s']}s wall clock.", ""]
            if rec:
                lines += _fact_lines(rec, summarize)
            lines += [""]
    with open(path, "w") as f:
        f.write("\n".join(lines))


def _fact_lines(rec, summarize):
    """The measured facts of one run as a bullet list, in prose the paper can
    quote — derived entirely from the timing record, never hardcoded."""
    rows = summarize.rows([rec])
    if not rows:
        return []
    r = rows[0]
    out = []

    def mb(v):
        """MiB, labelled MiB — the CSVs use the same base (see bench/README.md)."""
        if not isinstance(v, (int, float)):
            return "n/a"
        return f"{v / 1024:.2f} GiB" if v >= 1024 else f"{v:.1f} MiB"

    if r["source_mib"]:
        out.append(f"- source: {mb(r['source_mib'])}"
                   + (f" across {r['n_timesteps']} timestep(s)"
                      if r["n_timesteps"] else ""))
    wire = r["wire_mib"] or 0.0
    out.append(f"- over the wire: {mb(wire)}"
               + (f" — **{r['reduction_x']}x less than a whole-source copy**"
                  if r["reduction_x"] else ""))
    if r["total_pairs"]:
        out.append(f"- catalog: reused {r['reused_pairs']}/{r['total_pairs']} "
                   f"(timestep,variable) extent(s)")
    elif r["want_vars"]:
        out.append(f"- catalog: reused {r['cached_vars']}/{r['want_vars']} "
                   f"variable extent(s)")
    if r["est_read_mib"] is not None:
        band = ""
        if r["est_time_lo_s"] and r["est_time_hi_s"]:
            band = (f", predicted {r['est_time_lo_s']:.0f}–"
                    f"{r['est_time_hi_s']:.0f} s")
        verdict = ""
        if r["pull_s"] and r["est_time_lo_s"] and r["est_time_hi_s"]:
            act = r["pull_s"]
            verdict = (f" — actual pull {act:.0f} s, "
                       + ("INSIDE the band"
                          if r["est_time_lo_s"] <= act <= r["est_time_hi_s"]
                          else "OUTSIDE the band"))
        out.append(f"- estimate: {mb(r['est_read_mib'])} over the wire{band}"
                   + ("  ⚠ over budget" if r["est_over_budget"] else "") + verdict)
    # EVERY phase, in the order it ran — for a rejected request the breakdown IS
    # the result (where the seconds went when no data moved).
    if rec.get("phases"):
        out.append("- phases: " + ", ".join(
            f"{p['name'].split('/')[-1]} {p['s']:.2f}s" for p in rec["phases"]
            if p.get("s") is not None and p.get("depth") == 1) or "(none)")
        top = [p for p in rec["phases"] if p.get("depth") == 0 and p.get("s")]
        if top:
            out.append("- top-level: " + ", ".join(
                f"{p['name']} {p['s']:.2f}s" for p in top))
    orch = [(k, r[k]) for k in ("ssh_exec", "ssh_query", "remote_jobs")
            if r.get(k)]
    if orch:
        out.append("- orchestration: " + ", ".join(f"{k}={v}" for k, v in orch))
    if r["route"]:
        out.append(f"- route: `{r['route']}`"
                   + (f", held on {r['held']}" if r["held"] else ""))
    if r["error"]:
        out.append(f"- **spent before rejection: {r['total_s']:.1f} s, "
                   f"{mb(wire)} moved**")
        out.append(f"- error: `{r['error'].splitlines()[0][:300]}`")
    return out


COLUMNS_MD = """# Column dictionary for the case-study CSVs

Written by `bench/case_study.py`; regenerate the tables any time with
`python bench/summarize.py --file bench/results/timings.jsonl --csv bench/results`.

Read `session.md` first — it narrates each query. These CSVs are the same runs in
plottable form.

## runs.csv — one row per pipeline
| column | meaning |
|---|---|
| `started` | ISO timestamp of the run |
| `site` | `local` or `remote` — where the data lived |
| `route` | what the planner chose: `remote_reduce`, `remote_folder_reduce`, `catalog_full_hit`, `whole_file_fetch`, `held_budget`, `held_allocation` |
| `forms` | the narrowing forms as written, in order |
| `n_timesteps` | timesteps selected |
| `source_mb` | size of the source a naive copy would have moved (the baseline denominator) |
| `wire_mb` | bytes that actually crossed the network |
| `reduction_x` | `source_mb / wire_mb` — the headline movement factor |
| `total_s` | end-to-end wall clock for the run |
| `inspect_s` | schema read (metadata only) |
| `remote_exec_s` | the reduce running next to the data |
| `pull_s` | transferring the reduced result home |
| `materialize_s` | assembling arrays in local memory |
| `sink_s` | writing the output file |
| `ssh_exec` | ssh command round trips — orchestration cost, hardware-independent |
| `remote_jobs` | scheduler (`srun`) steps launched — 1 means one batched job |
| `status` | `OK`, `FAILED`, `NEEDS CONFIRM`, `NEEDS ALLOCATION` |

## cache.csv — reuse across successive queries
`reused_pairs / total_pairs` counts (timestep, variable) extents on the folder
path; `cached_vars / want_vars` counts variables on the single-file path.
`reuse_pct` is the percentage served locally. Read down the rows in time order:
that column rising while `wire_mb` falls to zero *is* the iterative-workflow
result.

## estimate.csv — predicted vs actual
`est_read_mb` vs `wire_mb` with `err_pct` (positive = the estimate was high);
`est_time_lo_s`/`est_time_hi_s` vs the measured `pull_s`, with `in_band` =
`yes`/`low`/`high`. `probe_bw_mbps` is the link speed measured at gate time —
the input the time band was derived from, so a missed band can be attributed to
link variance rather than bad math. `est_over_budget` is whether the gate fired.

## errors.csv — cost of a rejected request
`total_s` is seconds spent before the rejection, `wire_mb` the bytes moved by
then (0.0 is the point), `error` the message the author saw. `site` tells you
whether the check was answered from local or remote metadata.
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="+", choices=list(GROUPS),
                    help="run only these groups (default: all)")
    ap.add_argument("--keep-cache", action="store_true",
                    help="keep the bench catalog (default: wipe it for a cold start)")
    ap.add_argument("--dry", action="store_true", help="print the specs, run nothing")
    ap.add_argument("--report-only", action="store_true",
                    help="rebuild the tables + session.md from existing results "
                         "(runs nothing, touches no remote)")
    ap.add_argument("--cold-each", action="store_true",
                    help="wipe the bench catalog before EVERY query. NO group here "
                         "needs this: it exists for an isolated cost-vs-N scaling "
                         "sweep, whose curve is confounded when query N reuses what "
                         "N-1 cached. It would make E2 meaningless (reuse across "
                         "queries IS the measurement), so leave it off.")
    args = ap.parse_args()

    queries = [q for g in (args.only or list(GROUPS)) for q in GROUPS[g]]
    if args.dry:
        for q in queries:
            print(f"[{q.qid}] {q.title}\n    {q.spec}\n")
        return 0
    if args.report_only:
        os.environ.update(ENV)
        import summarize
        qpath = os.path.join(RESULTS, "queries.json")
        if not os.path.exists(qpath):
            print(f"nothing to report: no runs recorded under {RESULTS}.\n"
                  f"Run the session first (needs a held allocation):\n"
                  f"  python bench/case_study.py --only E1 E2 E3")
            return 1
        with open(qpath) as f:
            rows = json.load(f)
        return _emit(rows, summarize)

    for d in (RESULTS, SPECS, OUT):
        os.makedirs(d, exist_ok=True)
    if not args.keep_cache:
        # A cold start must be genuinely cold, and only the BENCH catalog is
        # touched — the user's own .vislang/cache is never in play. The timings
        # file is deliberately NOT rotated: records accumulate so a session split
        # across several commands still yields one report (rotate by hand to start
        # a fresh series).
        shutil.rmtree(ENV["VISLANG_CACHE"], ignore_errors=True)
    os.environ.update(ENV)

    from mcp_server import run_pipeline
    import summarize

    print(f"case study: {len(queries)} query(ies); results -> {RESULTS}"
          + ("  [cold cache before each query]" if args.cold_each else ""))
    rows = []
    for q in queries:
        if args.cold_each:
            shutil.rmtree(ENV["VISLANG_CACHE"], ignore_errors=True)
        rows.append(run_one(q, run_pipeline))

    # MERGE into any previous invocation's rows (latest run of a qid wins), so a
    # session split across several commands — a group re-run after a fix, a cold
    # sweep of one group — still produces ONE report covering everything.
    path = os.path.join(RESULTS, "queries.json")
    merged = {}
    if os.path.exists(path):
        with open(path) as f:
            for r in json.load(f):
                merged[r["qid"]] = r
    for r in rows:
        merged[r["qid"]] = r
    order = [q.qid for g in GROUPS.values() for q in g]
    rows = sorted(merged.values(),
                  key=lambda r: order.index(r["qid"]) if r["qid"] in order else 999)
    with open(path, "w") as f:
        json.dump(rows, f, indent=2)
    return _emit(rows, summarize)


def _emit(rows, summarize):
    """Tables + the readable report, from whatever runs are already recorded.
    Separated from the session so `--report-only` can rebuild the paper artifacts
    without touching the remote (or the allocation)."""
    recs = summarize.load(ENV["VISLANG_TIMINGS_FILE"])
    all_rows = summarize.rows(recs)
    for name, (title, cols, sel) in summarize.TABLES.items():
        data = sel(all_rows)
        summarize.write_csv(os.path.join(RESULTS, f"{name}.csv"), cols, data)
        print(summarize.fmt_table(title, cols, data))
    # The three paper tables (E1/E2/E3) in one readable file.
    contrib = summarize.write_results(os.path.join(RESULTS, "results.csv"), all_rows)
    print(f"results.csv: E1 transfer band from {', '.join(contrib) or 'no transfers'}")
    write_session_md(rows, recs, os.path.join(RESULTS, "session.md"))
    with open(os.path.join(RESULTS, "COLUMNS.md"), "w") as f:
        f.write(COLUMNS_MD)
    print(f"\nwrote {RESULTS}/session.md (read this first), COLUMNS.md, results.csv, "
          f"runs.csv, cache.csv, estimate.csv, errors.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
