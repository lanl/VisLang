#!/usr/bin/env python3
"""Run the Case Study session and write the paper's tables + a readable report.

Every measurement goes through the real `run_pipeline` — the same entry point an
interactive session uses — so nothing here is a special benchmark path. The
harness (vislang_timing.py) records each run; `summarize.py` tabulates them.

Three run groups, in order, plus one table derived from them:

  E1 movement       one 8.31 GiB remote HACC snapshot (268,435,456 particles,
                    17 variables), narrowed to 4 variables at stride 100. Run
                    twice: once unconfirmed (the cost gate HOLDS — zero bytes
                    move) and once confirmed (the actual transfer), so predicted
                    and actual sit in the same table.
  E2 iteration      five successive queries in one scientist's session: +1
                    variable, a verbatim re-issue, then the SAME question asked
                    under a two-predicate filter (a new cache lineage), a
                    per-variable delta inside that lineage, and finally a
                    retuned cut. This is the catalog/reuse evidence, and its
                    last row is the limit: a strictly narrower predicate is not
                    recognised as contained in a cached one.
  E3 mistake cost   four ill-posed requests (bad field, threshold on a variable
                    that does not exist, per-axis subsample on point data, and a
                    region far outside the box), measuring the seconds and bytes
                    spent BEFORE rejection — and, for the last, the cost of a
                    request that CANNOT be rejected from metadata.
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

# The remote dataset under test: one snapshot of a HACC hydro run from the
# SCIDAC/FLAMINGO subgrid-parameter sweep. The directory name IS the hypothesis —
# FSN_0.5387 (supernova feedback efficiency) and VEL_149.279 (wind velocity) are
# the parameters whose effect the session goes looking for.
#
# GenericIO writes a snapshot as a small header file plus eight ~1 GB rank
# partitions named `<base>#0 … <base>#7`. We point at the BASE file: pygio follows
# it to the parts. Note this collides with Sieve's own `…#N` = timestep
# convention, which is why the folder is NOT used as a timeseries here.
#
# SNAP_BYTES is the real denominator — the header is 2,064 B, so `source_bytes`
# as recorded by the timing harness is the header, NOT the data. Every
# reduction factor derived from the recorded value must be corrected against
# this constant (header + the eight parts, measured with `stat -Lc %s`).
_HACC = ("ssh://darwin/projects/exasky/data/hacc/SCIDAC_RUNS/"
         "128MPC_RUNS_FLAMINGO_DESIGN_3A/"
         "FSN_0.5387_VEL_149.279_TEXP_9.613_BETA_0.8710_SEED_1.387e5/output")
SNAP = f"{_HACC}/m000p.full.mpicosmo.624"      # final step: metals have moved
SNAP_BYTES = 8_925_812_397                     # 8.31 GiB across 8 partitions
N_PARTICLES = 268_435_456
STRIDE = 100                                   # int => deterministic row stride

# The survey projection and its two extensions. `subsample` MUST be an int: a
# float is an unseeded np.random.choice (narrowing.py:117), so a cached column
# and a freshly-fetched one would be drawn from different rows and silently
# misalign on the per-variable delta path.
F_BASE = ["x", "y", "z", "rho"]                 # where is the gas, how dense
F_UU = F_BASE + ["uu"]                          # + internal energy (temp proxy)
F_ZMET = F_UU + ["zmet"]                        # + metallicity (the science)

# The two cuts isolating feedback-ejected gas: HOT (shock-heated by the wind)
# and DIFFUSE (already out of the halo). Both are chosen from the percentiles of
# the survey data ALREADY pulled by E1/E2q1 — picking them costs nothing remote,
# which is the point. Filled in by `--derive-cuts` after the survey runs.
CUT_UU = None                                   # uu >  CUT_UU     (hot)
CUT_RHO = None                                  # rho < CUT_RHO    (diffuse)
CUT_UU2 = None                                  # a STRICTLY narrower retune
CUTS_FILE = os.path.join(RESULTS, "cuts.json")

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


if os.path.exists(CUTS_FILE):                   # written by --derive-cuts
    with open(CUTS_FILE) as _f:
        _cuts = json.load(_f)
    CUT_UU, CUT_RHO, CUT_UU2 = _cuts["uu"], _cuts["rho"], _cuts["uu2"]


def _save(node_expr, name):
    return f'save({node_expr}, "{os.path.join(OUT, name)}")'


def _filtered(fields, uu_cut):
    """The filtered request, written in the order the science means it: project,
    then the two ANDed cuts, THEN the stride — so the stride samples the
    survivors, not the other way round. Both thresholds and the projection are
    part of the catalog key (remote_reduce._narrow_key), so changing `uu_cut`
    starts a new lineage. NB the two thresholds are NOT fused: planner.py:588
    emits one RowMask per threshold and my_load.py:98 applies them in order."""
    return (f'subsample(threshold(threshold(fields(source("{SNAP}"), '
            f'{fields!r}), "uu > {uu_cut}"), "rho < {CUT_RHO}"), {STRIDE})')


# --- E1: data movement, and the gate that precedes it -----------------------
_SURVEY = f'subsample(fields(source("{SNAP}"), {F_BASE!r}), {STRIDE})'

E1 = [
    Q("E1a", "E1", "First look at the gas — cost gate holds",
      _save(_SURVEY, "e1_survey"),
      "The gate prices the request from metadata before any bulk read. A HELD run "
      "is the claim that Sieve can refuse a request without paying for it.",
      "HELD over budget (the 3 s time budget); 0 bytes over the wire; an estimate "
      "of ~41 MiB.",
      confirm=False),
    Q("E1b", "E1", "First look at the gas — confirmed, executed",
      _save(_SURVEY, "e1_survey"),
      "The headline movement number: 4 of 17 variables at stride 100, reduced "
      "next to the data. The denominator is the 8.31 GiB an ad-hoc copy moves — "
      "NOT the 2,064 B header the harness records (see SNAP_BYTES).",
      "~41 MiB over the wire (268,435,456/100 rows x 4 vars x 4 B = 42,949,672 B), "
      "a ~208x reduction against SNAP_BYTES. GenericIO is a COLUMN store "
      "(supports_column_pushdown=True), so the projection is pushed into the read "
      "and only the 4 requested columns are touched (~4.29 GB, not the full "
      "8.31 GiB). What it cannot do is skip rows "
      "(supports_strided_read=False), so every row of those columns is read and "
      "the stride is applied after.",
      confirm=True),
]

# --- E2: the iterative session (the catalog claim) --------------------------
# The story: the scientist has the gas distribution (E1b). Now they want to know
# whether supernova feedback ejected ENRICHED gas into the IGM, or left the metals
# locked in halos. They add the thermal variable, re-look, then impose the two
# cuts that isolate hot diffuse gas, add metallicity inside that filter, and
# finally retune the cut. Each step is a question a person actually asks next.
E2 = [
    Q("E2q1", "E2", "+internal energy (per-variable delta)",
      _save(f'subsample(fields(source("{SNAP}"), {F_UU!r}), {STRIDE})', "e2q1"),
      "The commonest follow-up: same view, one more variable. File-keyed caching "
      "cannot express this — it would refetch all five. `fields` is deliberately "
      "excluded from the catalog key (remote_reduce._narrow_key), which is what "
      "makes the projection a reusable axis rather than part of the identity.",
      "4/5 variables reused; only uu crosses (~10.7 MiB). The remote side must "
      "still read all 8.31 GiB to get that one column.",
      confirm=True),
    Q("E2q2", "E2", "Verbatim re-issue (exact hit)",
      _save(f'subsample(fields(source("{SNAP}"), {F_UU!r}), {STRIDE})', "e2q2"),
      "Re-running a query — the reload, the second look. Should cost nothing on "
      "the network and, more importantly here, should avoid the 8.31 GiB remote "
      "read entirely.",
      "5/5 reused; 0 bytes; no srun step; seconds, not minutes.",
      confirm=True),
    Q("E2q3", "E2", "Impose the filter — hot, diffuse gas (new lineage)",
      _save(_filtered(F_UU, CUT_UU), "e2q3"),
      "The actual science question: of the gas, which is hot AND diffuse — the "
      "signature of wind-ejected material? A threshold enters the catalog key "
      "verbatim (var, op, value), so this is a NEW lineage sharing nothing with "
      "the unfiltered survey, however similar it looks.",
      "0/5 reused — a full miss and a full 8.31 GiB remote read. The cut values "
      "themselves came from data already local, so choosing them cost no remote "
      "work; only applying them does.",
      confirm=True),
    Q("E2q4", "E2", "+metallicity, inside the filter (delta in a value lineage)",
      _save(_filtered(F_ZMET, CUT_UU), "e2q4"),
      "Per-variable reuse is not limited to purely structural requests: within a "
      "fixed predicate, adding a variable is still a delta. This is the query "
      "that answers the hypothesis — are the hot diffuse particles enriched?",
      "5/6 reused; only zmet crosses. Same lineage as E2q3, so the filter itself "
      "is not recomputed for the columns already held.",
      confirm=True),
    Q("E2q5", "E2", "Retune the cut — the containment limit",
      _save(_filtered(F_ZMET, CUT_UU2), "e2q5"),
      "The honest counterpart to the NYX study's superset-slicing row. There, a "
      "narrower REGION was sliced out of a wider cached extent. Here the retuned "
      "cut is strictly narrower too — {uu > CUT_UU2} is a subset of {uu > CUT_UU} "
      "— and yet it refetches, for TWO independent reasons worth separating:\n"
      "  (1) containment is decided geometrically (_grid_ranges_of / _axis_slice) "
      "and _fuse_forms rejects any key containing a threshold, so no predicate "
      "reasoning exists to appeal to;\n"
      "  (2) even with such reasoning, THIS request would still be unservable: "
      "the stride is written AFTER the cuts, so the cached extent is a positional "
      "sample of the WIDER survivor population. Masking it by uu > CUT_UU2 yields "
      "R1[::100] intersect R2, not R2[::100] — a different row set, which would "
      "violate _fuse_forms' stated invariant that a cached slice is bit-identical "
      "to a fresh reduce.",
      "0/6 reused; a full refetch for a result provably contained in what is "
      "already cached. The limitation is real but bounded: containment would be "
      "sound here only if no positional op followed the cut (no stride, or the "
      "stride written before the threshold).",
      confirm=True),
]

# --- E3: what a rejected request costs -------------------------------------
# The first three are caught from metadata. The fourth is the interesting one:
# it is ill-posed in exactly the way the first three are, and Sieve CANNOT see it.
E3 = [
    Q("E3a", "E3", "Misspelled variable (remote)",
      _save(f'fields(source("{SNAP}"), ["zmett"])', "e3a"),
      "The most frequent authoring error. Where the rejection lands — local "
      "metadata, remote metadata, or after a read — is the measurement.",
      "rejected on schema (validate_narrowing, n.project), 0 bulk bytes; the "
      "seconds are the remote login-node inspect, not a read.",
      confirm=True),
    Q("E3b", "E3", "Threshold on a variable that does not exist (remote)",
      _save(f'threshold(fields(source("{SNAP}"), {F_BASE!r}), '
            f'"temperature > 1e5")', "e3b"),
      "A domain slip rather than a typo: this dataset has no `temperature` — the "
      "thermal variable is `uu`, an internal energy. The predicate references a "
      "variable OUTSIDE the projection, which is exactly the case "
      "validate_narrowing's docstring warns must still be checked.",
      "rejected on the predicate's variable, 0 bulk bytes.",
      confirm=True),
    Q("E3c", "E3", "Per-axis subsample on point data (remote)",
      _save(f'subsample(fields(source("{SNAP}"), {F_BASE!r}), x=2)', "e3c"),
      "The DSL admits per-axis subsample on GRIDS only; on point data a single "
      "factor is the documented form. Note `x` IS a real coordinate here, so this "
      "is not an unknown-axis error — the form simply has no meaning for particles.",
      "rejected before any read by an explicit modality guard (planner.py:568), "
      "with a diagnostic that names the actual problem and the correct form. Note "
      "this is NOT the grid unknown-axis check at planner.py:215 — that one is "
      "only reached on the grid branch — but a separate check written for the "
      "point-data path.",
      confirm=True),
    Q("E3d", "E3", "Region far outside the box — the uncheckable request",
      _save(f'subsample(region(fields(source("{SNAP}"), {F_BASE!r}), '
            f'x=(9000, 9999)), {STRIDE})', "e3d"),
      "The counterpart to NYX's out-of-bounds region, which was rejected against "
      "the 512^3 extent for 0 bytes. On point data a `region` is a world-space "
      "bbox and validate_narrowing._check_bbox only verifies that the coordinate "
      "VARIABLES exist — there is no extent to violate. So an equally ill-posed "
      "request is not rejected at all.",
      "NOT rejected: a full 8.31 GiB remote read that returns zero particles. The "
      "measurement is what an uncheckable mistake costs versus a checkable one.",
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


def derive_cuts():
    """Choose the two cuts from the survey data ALREADY pulled, and write them to
    cuts.json for the E2 filtered queries to read.

    This is the methodological point, not a convenience: a scientist cannot know
    a sensible `uu` cut before seeing the distribution, and guessing one would be
    the unscientific move. E1b/E2q1 already brought 2.68 M sampled rows home, so
    the cut is chosen locally, at zero remote cost. `uu2` is deliberately a
    STRICTLY tighter cut than `uu`, so E2q5's request is provably contained in
    E2q3/q4's cached extents — which is what makes its refetch a limitation
    rather than an ordinary miss."""
    import glob
    import numpy as np

    cands = sorted(glob.glob(os.path.join(OUT, "e2q1*"))
                   + glob.glob(os.path.join(OUT, "e1_survey*")))
    if not cands:
        print(f"no survey output under {OUT} — run --only E1 E2 first "
              f"(E2q1 carries uu; E1b carries rho)")
        return 1

    def _columns(path):
        """{name: array} from whichever writer produced the survey output."""
        if os.path.isdir(path):
            for inner in sorted(glob.glob(os.path.join(path, "*"))):
                got = _columns(inner)
                if got:
                    return got
            return {}
        if path.endswith(".npz"):
            with np.load(path, allow_pickle=False) as z:
                return {k: z[k] for k in z.files}
        if path.endswith((".hdf5", ".h5")):
            import h5py
            out = {}
            with h5py.File(path, "r") as h:
                h.visititems(lambda n, o: out.__setitem__(n.split("/")[-1], o[:])
                             if isinstance(o, h5py.Dataset) else None)
            return out
        return {}

    cols = {}
    for p in cands:
        for k, v in _columns(p).items():
            cols.setdefault(k, v)
    if "uu" not in cols or "rho" not in cols:
        print(f"survey output has {sorted(cols)} — need both 'uu' and 'rho'. "
              f"Run E1 and E2q1 before deriving cuts.")
        return 1

    uu, rho = np.asarray(cols["uu"]).ravel(), np.asarray(cols["rho"]).ravel()

    def _sig2(v):
        """Two significant figures — a person picks a round cut, not a float.

        Cast to Python float FIRST: these columns are float32, and rounding a
        float32 leaves artifacts (3.3e10 comes out as 32999999488.0). The value
        ends up verbatim in the catalog key and in the paper, so it has to be the
        number a person would actually write."""
        from math import floor, log10
        v = float(v)
        if not np.isfinite(v) or v == 0:
            return v
        return float(round(v, -int(floor(log10(abs(v)))) + 1))

    cuts = {"uu": _sig2(np.percentile(uu, 90)),    # hot: top decile
            "rho": _sig2(np.percentile(rho, 25)),  # diffuse: bottom quartile
            "uu2": _sig2(np.percentile(uu, 97))}   # strictly tighter retune
    if not cuts["uu2"] > cuts["uu"]:               # rounding must not break it
        cuts["uu2"] = cuts["uu"] * 2
    os.makedirs(RESULTS, exist_ok=True)
    with open(CUTS_FILE, "w") as f:
        json.dump(cuts, f, indent=2)

    n = uu.size
    print(f"survey rows: {n:,} ({n / N_PARTICLES:.2%} of the snapshot)")
    print(f"  uu    p50={np.percentile(uu, 50):.4g}  p90={np.percentile(uu, 90):.4g}"
          f"  p97={np.percentile(uu, 97):.4g}")
    print(f"  rho   p25={np.percentile(rho, 25):.4g}  p50={np.percentile(rho, 50):.4g}")
    print(f"  x     [{np.min(cols['x']):.4g}, {np.max(cols['x']):.4g}]"
          if "x" in cols else "  (no x column — check E3d's region bounds by hand)")
    frac = float(np.mean((uu > cuts["uu"]) & (rho < cuts["rho"])))
    print(f"\ncuts -> {cuts}   (selects {frac:.2%} of sampled rows)")
    print(f"wrote {CUTS_FILE}; E2's filtered queries will use these.")
    return 0


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
    ap.add_argument("--qids", nargs="+", metavar="QID",
                    help="run only these query ids (e.g. E2q3 E2q4 E2q5). Needed "
                         "when resuming a session: results merge by qid and the "
                         "LATEST run wins, so re-running a whole group would "
                         "overwrite an earlier query's result with a warm-cache "
                         "repeat of it.")
    ap.add_argument("--derive-cuts", action="store_true",
                    help="choose the E2 threshold values from the survey data "
                         "already pulled (E1b/E2q1) and write results/cuts.json. "
                         "Runs nothing remote.")
    args = ap.parse_args()

    if args.derive_cuts:
        return derive_cuts()

    queries = [q for g in (args.only or list(GROUPS)) for q in GROUPS[g]]
    if args.qids:
        want = set(args.qids)
        queries = [q for q in queries if q.qid in want]
        unknown = want - {q.qid for q in queries}
        if unknown:
            print(f"unknown query id(s): {sorted(unknown)}")
            return 1
    # The filtered E2 queries are unauthorable until the cuts exist — and the
    # cuts come from the survey's own output, so on a cold start they CANNOT
    # exist yet. Drop them with a loud note rather than shipping a spec with a
    # literal `None` in its predicate; the second pass picks them up.
    _NEEDS_CUTS = ("E2q3", "E2q4", "E2q5")
    if CUT_UU is None:
        deferred = [q.qid for q in queries if q.qid in _NEEDS_CUTS]
        if deferred:
            queries = [q for q in queries if q.qid not in _NEEDS_CUTS]
            print(f"deferring {', '.join(deferred)}: their cuts are derived from "
                  f"the survey this run produces. After it finishes:\n"
                  f"  python bench/case_study.py --derive-cuts\n"
                  f"  python bench/case_study.py --only E2 --keep-cache\n")
    if not queries:
        print("nothing to run.")
        return 1
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
        # Refresh the AUTHORED fields (title/why/expect) from the current query
        # definitions. These are descriptions, not measurements: when the reading
        # of a result is sharpened after the fact, session.md should say the
        # better thing without re-running the query to change a comment. Measured
        # fields — status, driver_s, error, report — are never touched here.
        current = {q.qid: q for g in GROUPS.values() for q in g}
        for row in rows:
            q = current.get(row["qid"])
            if q is not None:
                row.update(title=q.title, why=q.why, expect=q.expect)
        with open(qpath, "w") as f:
            json.dump(rows, f, indent=2)
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
