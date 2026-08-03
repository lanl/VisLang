#!/usr/bin/env python3
"""Turn `.vislang/timings.jsonl` into the Case Study tables.

The harness (vislang_timing.py) records one JSON object per run. This reads them
back and prints the four tables the paper needs, or writes them as CSV:

  runs      one row per pipeline: site, route, source vs wire bytes, the
            reduction factor, and the phase breakdown (inspect / remote_exec /
            pull / materialize / sink)
  estimate  predicted vs actual — wire MB and the measured time band, with the
            in-band verdict, for the cost-gate accuracy table
  cache     successive runs over the same source: extents reused vs fetched,
            bytes crossed, seconds — the iterative-workflow table
  errors    rejected/failed runs: seconds and bytes spent BEFORE the error, i.e.
            what a mistake costs

Usage:
    python bench/summarize.py                          # all runs, all tables
    python bench/summarize.py --table cache            # one table
    python bench/summarize.py --since 2026-07-30       # ISO date prefix filter
    python bench/summarize.py --spec-sha 3f2a          # one spec's runs
    python bench/summarize.py --csv paper/data         # write runs.csv, ...

Stdlib only, so it runs under any interpreter that can read the repo.
"""

import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MB = 1024 ** 2


def load(path):
    """Records, oldest first. A truncated final line (a run still in flight, or a
    crashed process) is skipped rather than fatal."""
    out = []
    with open(path) as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
    return out


def _phase_s(rec, leaf):
    """Seconds summed over phases whose LAST path component is `leaf` — so
    'pull' catches remote_reduce/pull and remote_folder_reduce/pull alike."""
    total = None
    for p in rec.get("phases") or ():
        if (p.get("name") or "").split("/")[-1] == leaf and p.get("s") is not None:
            total = (total or 0) + p["s"]
    return total


def _mb(n):
    """Bytes -> MiB (1024**2), matching my_estimate's own byte math so predicted
    and actual are directly comparable. Columns are named `_mib` for that reason:
    the same 134,217,728 B reads as 128 MiB here and as "134.2 MB" in the prose
    steps, and a paper table must not straddle the two."""
    return None if n in (None, "") else round(n / MB, 2)


def _r1(v):
    return None if v is None else round(v, 1)


# The recorded `source_bytes` is the size of the path we handed Sieve. For a
# multi-part GenericIO snapshot that path is a ~2 KB HEADER and the data lives in
# eight sibling `#N` partitions that pygio follows — so the recorded value is not
# the denominator a whole-file copy would have moved, and every reduction factor
# derived from it is wrong by ~4.3 million. Correct it here, keyed by a substring
# of the URI, with the total measured by `stat -Lc %s` over header + parts.
#
# This is a REPORTING correction, deliberately visible rather than a silent fix in
# the read path: the harness still records what it observed, and this table says
# what that observation misses. Remove the entry once source_bytes sums the parts.
SOURCE_BYTES_OVERRIDE = {
    "m000p.full.mpicosmo.624": 8_925_812_397,      # 8.31 GiB, 8 partitions
}


def _true_source_bytes(p):
    """Recorded source_bytes, corrected for formats whose path is not their data."""
    uri = p.get("uri") or ""
    for frag, total in SOURCE_BYTES_OVERRIDE.items():
        if frag in uri:
            n = p.get("n_timesteps") or p.get("selected_timesteps") or 1
            return total * n
    return p.get("source_bytes")


def rows(recs):
    """Flatten to one row per pipeline. Run-level counters (ssh round trips, wire
    bytes) are attributed to the FIRST pipeline of a run: they are measured per
    process, and a multi-sink spec cannot be split without double counting —
    `n_pipes` flags those rows so a reader knows not to sum them naively."""
    out = []
    for r in recs:
        c = r.get("counters") or {}
        pipes = r.get("pipelines") or [{}]
        for i, p in enumerate(pipes):
            first = (i == 0)
            wire = c.get("wire_bytes") if first else None
            # On a REMOTE run, "no transfer recorded" means zero bytes crossed —
            # the whole point of a cache hit, so report 0.0 rather than a blank
            # that reads as missing data. On a local run there is no wire at all,
            # so it stays blank.
            if wire is None and first and p.get("site") == "remote" and not p.get("dry_run"):
                wire = 0
            src_b = _true_source_bytes(p)
            out.append({
                "run_id": r.get("run_id"),
                "started": r.get("started"),
                "commit": r.get("commit"),
                "spec_sha": r.get("spec_sha"),
                # basename of the spec (E1b, E2q1, E3a…) — the key results.csv and
                # its annotations select on; the run log accumulates across runs so
                # results.csv keeps the most recent row per name.
                "spec_name": (os.path.splitext(os.path.basename(r.get("spec")))[0]
                              if r.get("spec") else None),
                "uri": p.get("uri"),
                "status": r.get("status"),
                "n_pipes": len(pipes),
                "kind": p.get("kind"),
                "site": p.get("site"),
                "route": p.get("route"),
                "forms": "+".join(p.get("forms") or []),
                "dry_run": p.get("dry_run"),
                "held": p.get("held"),
                "n_timesteps": p.get("n_timesteps") or p.get("selected_timesteps"),
                "source_mib": _mb(src_b),
                "wire_mib": _mb(wire),
                "result_mib": _mb(p.get("result_bytes")),
                # The headline: how many times less data crossed the network than
                # a whole-file(/folder) fetch of the same source would have moved.
                "reduction_x": (round(src_b / wire, 1)
                                if src_b and wire else None),
                "total_s": r.get("total_s"),
                "pipeline_s": p.get("s"),
                "inspect_s": _phase_s(r, "inspect") if first else None,
                "lower_s": _phase_s(r, "lower") if first else None,
                "estimate_s": _phase_s(r, "cost_estimate") if first else None,
                "remote_exec_s": _phase_s(r, "remote_exec") if first else None,
                "pull_s": _phase_s(r, "pull") if first else None,
                "materialize_s": (_phase_s(r, "materialize")
                                  or _phase_s(r, "materialize_timeseries")) if first else None,
                "sink_s": (_phase_s(r, "sink_save") or _phase_s(r, "sink_render")
                           or _phase_s(r, "sink_save_timeseries")) if first else None,
                "ssh_exec": c.get("ssh_exec") if first else None,
                "ssh_query": c.get("ssh_query") if first else None,
                "remote_jobs": c.get("remote_jobs") if first else None,
                "transfers": c.get("transfers") if first else None,
                # predicted (est_*) vs measured, for the accuracy table. The
                # record's `est_read_mb` is MiB despite the name (my_estimate
                # divides by 1024**2), so the column is renamed, not reconverted.
                "est_read_mib": p.get("est_read_mb"),
                "est_time_lo_s": _r1(p.get("est_time_lo_s")),
                "est_time_hi_s": _r1(p.get("est_time_hi_s")),
                "est_over_budget": p.get("est_over_budget"),
                "probe_bw_mibps": (round(p.get("probe_bw_bps") / MB, 3)
                                  if p.get("probe_bw_bps") else None),
                # catalog reuse, (timestep, variable) granularity on the folder
                # path and variables on the single-file path
                "reused_pairs": p.get("reused_pairs"),
                "total_pairs": p.get("total_pairs"),
                "cached_vars": p.get("cached_vars"),
                "want_vars": p.get("want_vars"),
                "error": r.get("error") or p.get("error"),
            })
    return out


# ---------------------------------------------------------------------------
# Tables: each is (title, column list, row filter/transform)
# ---------------------------------------------------------------------------
RUNS_COLS = ["started", "site", "route", "forms", "n_timesteps", "source_mib",
             "wire_mib", "reduction_x", "total_s", "inspect_s", "remote_exec_s",
             "pull_s", "materialize_s", "sink_s", "ssh_exec", "remote_jobs",
             "status"]

EST_COLS = ["started", "site", "route", "est_read_mib", "wire_mib", "err_pct",
            "est_time_lo_s", "est_time_hi_s", "pull_s", "in_band",
            "probe_bw_mibps", "est_over_budget"]

CACHE_COLS = ["started", "route", "n_timesteps", "reused_pairs", "total_pairs",
              "reuse_pct", "cached_vars", "want_vars", "wire_mib", "total_s",
              "ssh_exec", "remote_jobs"]

ERR_COLS = ["started", "status", "site", "forms", "total_s", "wire_mib",
            "materialized", "error"]


def table_runs(rs):
    return [r for r in rs if not r["dry_run"]]


def table_estimate(rs):
    out = []
    for r in rs:
        if r["est_read_mib"] is None:
            continue
        d = dict(r)
        if r["wire_mib"]:
            d["err_pct"] = round(100 * (r["est_read_mib"] - r["wire_mib"]) / r["wire_mib"], 1)
        else:
            d["err_pct"] = None
        lo, hi, act = r["est_time_lo_s"], r["est_time_hi_s"], r["pull_s"]
        d["in_band"] = (None if None in (lo, hi, act)
                        else "yes" if lo <= act <= hi
                        else "low" if act < lo else "high")
        out.append(d)
    return out


def table_cache(rs):
    """Successive completed runs only. A rejected or held run reused nothing
    because it never got that far — its row belongs in `errors`, and leaving it
    here would read as a cache miss that never happened."""
    out = []
    for r in rs:
        if r["total_pairs"] is None and r["want_vars"] is None:
            continue
        if r["status"] != "OK" or r["error"] or r["held"]:
            continue
        d = dict(r)
        tot, reu = r["total_pairs"], r["reused_pairs"]
        if tot in (None, 0):
            tot, reu = r["want_vars"], r["cached_vars"]
        d["reuse_pct"] = (round(100 * reu / tot, 1)
                          if tot not in (None, 0) and reu is not None else None)
        out.append(d)
    return out


def table_errors(rs):
    out = []
    for r in rs:
        if r["status"] in ("OK",) and not r["held"] and not r["error"]:
            continue
        d = dict(r)
        d["materialized"] = "no" if (r["held"] or r["error"]) else "yes"
        # A held/failed run's cost IS the interesting number: seconds spent and
        # bytes moved before the request was rejected.
        d["wire_mib"] = r["wire_mib"] or 0.0
        # Held runs carry no exception — say why they stopped, so every row in
        # this table states its own reason.
        if not d["error"] and r["held"]:
            d["error"] = f"HELD ({r['held']}) — nothing materialized"
        # One line, bounded: the full text lives in trace.log and session.md, and
        # a 700-character cell makes the table unreadable.
        if d["error"]:
            e = d["error"].splitlines()[0]
            d["error"] = e if len(e) <= 150 else e[:147] + "..."
        out.append(d)
    return out


# Insertion order is the print order (runs first — it is the table everything
# else annotates).
TABLES = {
    "runs": ("Runs — data movement and phase breakdown", RUNS_COLS, table_runs),
    "cache": ("Catalog reuse across successive runs", CACHE_COLS, table_cache),
    "estimate": ("Cost estimate — predicted vs actual", EST_COLS, table_estimate),
    "errors": ("Cost of a rejected request", ERR_COLS, table_errors),
}


def fmt_table(title, cols, data):
    if not data:
        return f"\n{title}\n  (no rows)\n"
    def cell(v):
        return "" if v is None else str(v)
    widths = {c: max(len(c), *(len(cell(r.get(c))) for r in data)) for c in cols}
    head = "  ".join(c.ljust(widths[c]) for c in cols)
    lines = [f"\n{title}", "-" * len(head), head, "-" * len(head)]
    for r in data:
        lines.append("  ".join(cell(r.get(c)).ljust(widths[c]) for c in cols))
    return "\n".join(lines) + "\n"


def write_csv(path, cols, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(data)


# ---------------------------------------------------------------------------
# results.csv — the three paper tables (E1 / E2 / E3) in one readable file.
#
# Every number is filled from timings.jsonl; only the English cells the run log
# can't carry (the request wording, where a rejection was caught) live in
# RESULT_ANNOT below. Keyed by spec name, so re-running against a *different*
# file keeps the numbers correct — you only edit the prose here when the request
# text changes. The two E1 baseline rows (whole-file copy time, wall-clock
# improvement) are re-derived each run from the session's own measured transfer
# band, not a hardcoded link speed.
# ---------------------------------------------------------------------------
RESULT_ANNOT = {
    # E1 single-snapshot. The "17" is the file's variable count (not recorded per
    # run); the projected count is filled from want_vars.
    "E1b": {"request": "fields({want}/17) → subsample(100) → save"},
    # E2 five-query session: a label and a short description per query.
    "E2q1": {"label": "q1", "request": "add uu (internal energy)"},
    "E2q2": {"label": "q2", "request": "q1 re-issued verbatim"},
    "E2q3": {"label": "q3", "request": "filter: uu > cut AND rho < cut"},
    "E2q4": {"label": "q4", "request": "add zmet, inside the filter"},
    "E2q5": {"label": "q5", "request": "retune to a strictly tighter uu cut"},
    # E3 rejected requests: what was malformed, and where it was caught. E3d is
    # the control: equally ill-posed, but nothing in the schema can refute it.
    "E3a": {"request": "variable zmett (misspelled)",
            "rejected_by": "remote schema resolution"},
    "E3b": {"request": "threshold on temperature, which does not exist",
            "rejected_by": "validate_narrowing, predicate vars"},
    "E3c": {"request": "subsample(x=2) — per-axis, on point data",
            "rejected_by": "point-data modality guard"},
    "E3d": {"request": "region(x=(9000,9999)) on a 128 Mpc/h box",
            "rejected_by": "NOT REJECTED — no extent to violate"},
}

_FMT_BY_EXT = {"hdf5": "HDF5", "h5": "HDF5", "npz": "npz", "fits": "FITS",
               "raw": "raw", "bp": "ADIOS"}


def _latest_by_spec(rs):
    """Most recent row per spec_name. timings.jsonl accumulates across runs, so a
    name can appear several times; results.csv reports the latest of each."""
    out = {}
    for r in rs:
        name = r.get("spec_name")
        if not name:
            continue
        if name not in out or (r.get("started") or "") >= (out[name].get("started") or ""):
            out[name] = r
    return out


def _fmt_from_uri(uri):
    """Source format label from the uri extension. The #N timestep tag can sit
    before the extension (nyx512#1.hdf5), so take the extension of the whole last
    path component rather than splitting on '#'."""
    if not uri:
        return ""
    tail = uri.rsplit("/", 1)[-1]
    ext = tail.rsplit(".", 1)[-1].lower() if "." in tail else ""
    # A GenericIO snapshot's trailing component is the STEP NUMBER, not an
    # extension (m000p.full.mpicosmo.624), so an extension-based guess yields
    # "624". Recognise the family by name before falling back to the extension.
    if "mpicosmo" in tail or ext.isdigit():
        return "GenericIO"
    return _FMT_BY_EXT.get(ext, ext.upper())


def _transfer_band(picked):
    """MiB/s over runs with a real sustained transfer — the throughput the E1
    baseline is derived from. Excludes cache hits (0 wire) and tiny latency-bound
    pulls (< 1 MiB), matching REPORT §4. Returns (lo, hi, [contributor names])."""
    rates = []
    for name, r in picked.items():
        w, p = r.get("wire_mib"), r.get("pull_s")
        if w and p and w >= 1.0:
            rates.append((name, w / p))
    if not rates:
        return None, None, []
    vals = [v for _, v in rates]
    return min(vals), max(vals), sorted(n for n, _ in rates)


def _span(lo, hi, fmt):
    """`fmt(lo)` when the band is a point, else `fmt(lo)–fmt(hi)`."""
    a, b = fmt(lo), fmt(hi)
    return a if a == b else f"{a}–{b}"


def _results_e1(w, picked, lo, hi):
    r = picked.get("E1b")
    w.writerow(["E1 — Single-snapshot remote reduction"])
    w.writerow(["Quantity", "Value"])
    if not r:
        w.writerow(["(no E1b run recorded)", ""])
        return
    src, wire = r.get("source_mib"), r.get("wire_mib")
    fmt = _fmt_from_uri(r.get("uri"))
    req = RESULT_ANNOT["E1b"]["request"].format(want=r.get("want_vars"))
    rows_out = [
        ["Source", f"{src:,.2f} MiB {fmt}".strip() if src is not None else ""],
        ["Request", req],
        ["Output transferred", f"{wire:,.2f} MiB" if wire is not None else ""],
        ["Network bandwidth",
         _span(lo, hi, lambda v: f"{v:.2f}") + " MiB/s" if lo is not None else ""],
        ["Remote reduction time",
         f"{r.get('remote_exec_s'):.2f} s" if r.get("remote_exec_s") is not None else ""],
        ["Pull time", f"{r.get('pull_s'):.2f} s" if r.get("pull_s") is not None else ""],
        ["End-to-end Sieve time",
         f"{r.get('total_s'):.2f} s" if r.get("total_s") is not None else ""],
    ]
    # DELIBERATELY NOT REPORTED: "Byte reduction", "Whole-file copy time" and
    # "Wall-clock improvement".
    #
    # All three are ratios against a counterfactual, and none of them measures the
    # system. Byte reduction is source ÷ wire — arithmetic that falls straight out
    # of the selectivity the request states, and it degenerates without bound as
    # the wire goes to zero (a query returning no rows scores millions-fold). The
    # other two divide the source by a transfer band assembled from this session's
    # own pulls, whose slow end is dominated by fixed per-run ssh cost rather than
    # throughput, so both lean pessimistic in the direction that flatters us.
    #
    # The measured quantities they were derived from are all still in runs.csv
    # (`reduction_x`) and estimate.csv; only the paper table drops them.
    w.writerows(rows_out)


def _results_e2(w, picked):
    w.writerow(["E2 — Five-query session over one 8.31 GiB HACC snapshot"])
    w.writerow(["Query", "Request", "Reuse", "Hit (%)", "Wire", "srun",
                "Pull (s)", "Total (s)"])
    wire_tot = srun_tot = pull_tot = total_tot = 0.0
    seen = False
    for name in ("E2q1", "E2q2", "E2q3", "E2q4", "E2q5"):
        r = picked.get(name)
        if not r:
            continue
        seen = True
        a = RESULT_ANNOT[name]
        # Reuse is counted per (timestep, variable) pair on the FOLDER path and
        # per variable on the single-file path. A session on one snapshot only
        # ever produces the latter, so fall back to it rather than leaving the
        # table's two most important columns blank.
        reu, tot_p = r.get("reused_pairs"), r.get("total_pairs")
        if tot_p is None:
            reu, tot_p = r.get("cached_vars"), r.get("want_vars")
        reuse = f"{reu}/{tot_p}" if tot_p is not None else ""
        hit = (round(100 * reu / tot_p) if tot_p else 0) if reu is not None else ""
        wire_mib, pull, srun = r.get("wire_mib"), r.get("pull_s"), r.get("remote_jobs") or 0
        wire = f"{wire_mib:.2f} MiB" if wire_mib else "0"
        w.writerow([a["label"], a["request"], reuse, hit, wire, srun,
                    f"{pull:.2f}" if pull is not None else "–",
                    f"{r.get('total_s'):.2f}" if r.get("total_s") is not None else ""])
        wire_tot += wire_mib or 0.0
        srun_tot += srun
        pull_tot += pull or 0.0
        total_tot += r.get("total_s") or 0.0
    if seen:
        w.writerow(["Session total", "", "", "", f"{wire_tot:.2f} MiB",
                    int(srun_tot), f"{pull_tot:.2f}", f"{total_tot:.2f}"])
    else:
        w.writerow(["(no E2 runs recorded)"])


def _results_e3(w, picked):
    w.writerow(["E3 — Rejected requests"])
    w.writerow(["Run", "Malformed request", "Rejected by", "RTs", "srun",
                "Time (s)", "Wire (B)"])
    any_row = False
    for name in ("E3a", "E3b", "E3c", "E3d"):
        r = picked.get(name)
        if not r:
            continue
        any_row = True
        a = RESULT_ANNOT[name]
        rts = (r.get("ssh_query") or 0) + (r.get("ssh_exec") or 0)
        w.writerow([name, a["request"], a["rejected_by"], rts, r.get("remote_jobs") or 0,
                    f"{r.get('total_s'):.2f}" if r.get("total_s") is not None else "", 0])
    if not any_row:
        w.writerow(["(no E3 runs recorded)"])


def write_results(path, all_rows):
    """Write the three paper tables (E1/E2/E3) into one readable results.csv.
    Returns the list of runs that fed the E1 transfer band (logged, not silent)."""
    picked = _latest_by_spec(all_rows)
    lo, hi, contrib = _transfer_band(picked)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        _results_e1(wr, picked, lo, hi)
        wr.writerow([])
        _results_e2(wr, picked)
        wr.writerow([])
        _results_e3(wr, picked)
    return contrib


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", help="timings.jsonl (default: the run log location)")
    ap.add_argument("--table", choices=list(TABLES), action="append",
                    help="only this table (repeatable; default: all)")
    ap.add_argument("--since", help="keep runs whose ISO timestamp sorts >= this")
    ap.add_argument("--spec-sha", help="keep runs of this spec (prefix match)")
    ap.add_argument("--csv", metavar="DIR", help="also write <table>.csv here")
    ap.add_argument("--results", metavar="PATH",
                    help="also write the E1/E2/E3 paper tables to this results.csv")
    args = ap.parse_args()

    path = args.file
    if not path:
        from vislang_timing import timings_file
        path = timings_file()
    if not os.path.exists(path):
        print(f"no timings at {path} — run a spec first (VISLANG_TIMING=1 is the "
              f"default), or pass --file")
        return 1

    recs = load(path)
    if args.since:
        recs = [r for r in recs if (r.get("started") or "") >= args.since]
    if args.spec_sha:
        recs = [r for r in recs if (r.get("spec_sha") or "").startswith(args.spec_sha)]
    rs = rows(recs)
    print(f"{len(recs)} run(s), {len(rs)} pipeline(s) from {path}")

    for name in (args.table or list(TABLES)):
        title, cols, sel = TABLES[name]
        data = sel(rs)
        print(fmt_table(title, cols, data))
        if args.csv:
            out = os.path.join(args.csv, f"{name}.csv")
            write_csv(out, cols, data)
            print(f"  -> {out}")

    if args.results:
        contrib = write_results(args.results, rs)
        print(f"\n  -> {args.results}  (E1 band from: {', '.join(contrib) or 'no transfers'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
