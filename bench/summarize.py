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


def _phase_bytes(rec, leaf, pipe):
    """Bytes summed over phases of ONE pipeline whose last path component is
    `leaf`. Keyed on the pipeline index because a multi-sink spec records a
    materialize per sink, and attributing all of them to the first would
    overstate the result size of the very row a paper table reads."""
    total = None
    for p in rec.get("phases") or ():
        if ((p.get("name") or "").split("/")[-1] == leaf
                and p.get("bytes") is not None and p.get("pipeline") == pipe):
            total = (total or 0) + p["bytes"]
    return total


def _mb(n):
    """Bytes -> MiB (1024**2), matching my_estimate's own byte math so predicted
    and actual are directly comparable. Columns are named `_mib` for that reason:
    the same 134,217,728 B reads as 128 MiB here and as "134.2 MB" in the prose
    steps, and a paper table must not straddle the two."""
    return None if n in (None, "") else round(n / MB, 2)


def _r1(v):
    return None if v is None else round(v, 1)


def _num(v, fmt):
    """`fmt`-formatted cell for a measured number, "" for one that is missing —
    so a blank in a paper table always means "not measured" and never a real 0."""
    return "" if v is None else fmt.format(v)


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
                # `result_bytes` is set by the reduce path; on the fetch path the
                # size of what was actually kept is only in the materialize phase's
                # own byte count, so fall back to it rather than leave the cell
                # blank on the one route where over-fetch is the whole point.
                "result_mib": _mb(p.get("result_bytes")
                                  or _phase_bytes(r, "materialize", i)
                                  or _phase_bytes(r, "materialize_timeseries", i)),
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
                # The whole-file/whole-folder FETCH route (VISLANG_REMOTE=off, or
                # a fallback) spends nearly all its wall clock in these two, and
                # without them a fetch run reports a large total_s with no phase
                # accounting for any of it. `transfer_s` is the inner rsync/scp
                # alone; `fetch_s` adds the size + md5 + bandwidth probes around it.
                "fetch_s": (_phase_s(r, "fetch_whole_file")
                            or _phase_s(r, "fetch_whole_folder")) if first else None,
                "transfer_s": (_phase_s(r, "transfer")
                               or _phase_s(r, "transfer_dir")) if first else None,
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
             "pull_s", "fetch_s", "transfer_s", "materialize_s", "sink_s",
             "ssh_exec", "remote_jobs", "status"]

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
    # E1: the SAME spec on both halves, so one request string serves both columns
    # and neither can drift from the other. "13" is the Nyx file's variable count
    # (not recorded per run); the box is index-space on the original 512³ grid.
    # Not {want}-formatted: the fetch route never records want_vars, so a shared
    # annotation is the only way both columns can state the same request.
    "E1a": {"request": "fields(x, y, z, rho — 4/17) → subsample(3) → save",
            # The uri names a 2,064 B header, so neither the size nor the shape of
            # the data can be read off it — SOURCE_BYTES_OVERRIDE supplies the
            # former and this note the latter.
            # No format label here — _fmt_from_uri already supplies "GenericIO".
            "source_note": "header + 8 rank partitions; "
                           "17 variables; 268,435,456 particles"},
    "E1b": {"request": "fields(x, y, z, rho — 4/17) → subsample(3) → save"},
    # E1local: the SAME narrowing with VISLANG_REMOTE=off, so it routes through a
    # whole-file fetch and narrows locally. "13" is the Nyx file's variable count.
    # The projected count is prose here, not {want}: the fetch route never records
    # want_vars (that field is set by the reduce path), so the run log genuinely
    # cannot carry it and this is the annotation table's whole reason to exist.
    "E1a_local": {"request": "fields(temperature, baryon_density — 2/13) → "
                             "subsample(2) → save"},
    "E1b_local": {"request": "fields(temperature, baryon_density — 2/13) → "
                             "subsample(2) → save",
                  # Schema facts the run log does not carry per run.
                  "source_note": "13 variables; 512³ grid"},
    # E2 five-query session: a label and a short description per query.
    "E2q1": {"label": "q1", "request": "add uu — is any gas hot?"},
    "E2q2": {"label": "q2", "request": "add zmet — baseline enrichment (control)"},
    "E2q3": {"label": "q3", "request": "filter hot + diffuse (wind lineage)"},
    "E2q4": {"label": "q4", "request": "add vx,vy,vz — outflowing?"},
    "E2q5": {"label": "q5", "request": "figure: x,y,z,zmet only (fewer vars)"},
    "E2q6": {"label": "q6", "request": "retune to a strictly tighter uu cut"},
    "E2q7": {"label": "q7", "request": "add vx,vy,vz in the retuned lineage"},
    "E2q8": {"label": "q8", "request": "contrast: cold + dense (halo lineage)"},
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


# A pull carries a fixed cost of roughly 15 s (session setup, plan handshake)
# regardless of size, so `wire / pull_s` only approaches true throughput once the
# transfer is large enough to dominate it. Measured on this session: 1,365 MiB in
# 79.0 s reads as 17.3 MiB/s, but 1.7 MiB in 15.7 s reads as 0.11 MiB/s — the same
# link, two orders of magnitude apart, because the second is timing the overhead.
# Subtracting ~15 s from each pull gives a consistent ~19-23 MiB/s across every
# transfer in the session, which is the real link speed; the floor below keeps the
# reported band to transfers where that correction would not change the story.
_BAND_FLOOR_MIB = 256.0


def _transfer_band(picked):
    """MiB/s over runs with a genuinely throughput-dominated transfer — the
    figure the E1 `Network bandwidth` row reports. Excludes cache hits (0 wire)
    and any transfer small enough to be latency-bound (< _BAND_FLOOR_MIB), since
    those measure per-run fixed cost rather than the link. Returns
    (lo, hi, [contributor names]).

    `pull_s` (the reduce route's result pull) or, failing that, `transfer_s` (the
    fetch route's rsync/scp): both are the same link moving bulk bytes, and a
    session that only took the fetch route would otherwise report no band at all.
    `transfer_s` needs no fixed-cost correction — it times the transfer alone,
    with the probes accounted separately in `fetch_s`."""
    rates = []
    for name, r in picked.items():
        w = r.get("wire_mib")
        p = r.get("pull_s") or r.get("transfer_s")
        if w and p and w >= _BAND_FLOOR_MIB:
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
    """E1 — ONE request, run twice, side by side: reduced next to the data (E1a)
    against fetched whole and narrowed here (E1b).

    Both halves are confirmed, so the pair isolates ROUTE from policy. Every cell
    comes from this session's own timing records — unlike _results_compare, which
    has to reach into another session's timings.jsonl for its denominator, this
    table's baseline was measured minutes after its numerator, on the same link.

    Two rows need their definitions stated, because the phase tree names them
    differently per route. `Pull time` is the phase that moves bulk bytes at the
    top level: `pull` on the reduce route, `fetch_whole_folder` on the fetch route.
    `Network bandwidth` divides the wire by `transfer_dir` alone — the rsync
    without the probes around it — so it is a link rate on both routes rather than
    a rate on one and a rate-plus-fixed-cost on the other. The session-wide band
    (lo, hi) is no longer used here: with two columns, a per-run rate is what lets
    a reader check the wall clocks against each other."""
    a, r = picked.get("E1a"), picked.get("E1b")
    w.writerow(["E1 — One request, two routes: reduce next to the data vs "
                "fetch whole and narrow locally"])
    w.writerow(["Quantity", "E1a — remote reduce", "E1b — no remote reduce"])
    if not (a or r):
        w.writerow(["(no E1a/E1b run recorded)", "", ""])
        return

    def _cell(rec, key, fmt, absent=""):
        return "" if rec is None else _num(rec.get(key), fmt) or absent

    def _both(key, fmt, absent=""):
        return [_cell(a, key, fmt, absent), _cell(r, key, fmt, absent)]

    def _pull(rec):
        """The top-level bulk-transfer phase, whatever the route calls it."""
        if rec is None:
            return ""
        return _num(rec.get("pull_s") or rec.get("fetch_s"), "{:,.2f} s")

    def _band(rec):
        """Wire ÷ the rsync alone, so both routes report a comparable link rate.

        Flagged when the payload is under _BAND_FLOOR_MIB: below that the quotient
        times the fixed per-transfer cost rather than throughput (see the floor's
        comment — 1.7 MiB reads as 0.11 MiB/s on a link doing 17.3 MiB/s). The
        reduce route's whole point is a small payload, so its cell will nearly
        always carry this flag, and an unflagged number here would read as a slow
        link and quietly discredit the wall clock beside it."""
        if rec is None:
            return ""
        wire, t = rec.get("wire_mib"), rec.get("transfer_s")
        if not (wire and t):
            return ""
        rate = f"{wire / t:,.2f} MiB/s"
        return rate if wire >= _BAND_FLOOR_MIB else f"{rate} (latency-bound)"

    def _local(rec):
        """Every phase that ran HERE: inspect, lower, materialize, save. On the
        fetch route this is the narrowing itself; on the reduce route it is only
        the schema read and the save, because the narrowing happened remotely."""
        if rec is None:
            return ""
        v = sum(x for x in (rec.get("inspect_s"), rec.get("lower_s"),
                            rec.get("materialize_s"), rec.get("sink_s"))
                if x is not None) or None
        return _num(v, "{:,.2f} s")

    def _source(rec):
        """Size plus the schema facts the run log cannot carry. The uri is a
        FOLDER here, so it has no extension and _fmt_from_uri yields nothing —
        the format comes from the annotation instead."""
        if rec is None or rec.get("source_mib") is None:
            return ""
        size = f"{rec['source_mib']:,.2f} MiB {_fmt_from_uri(rec.get('uri'))}".strip()
        note = RESULT_ANNOT["E1a"].get("source_note")
        return "; ".join(x for x in (size, note) if x)

    req = RESULT_ANNOT["E1a"]["request"]
    rows_out = [
        # The fetch route records wire_bytes but no source_bytes for a FOLDER, so
        # E1b's cell is empty on the measurement alone. It is the same folder by
        # construction (one spec, two env overlays), which is a fact about the
        # experiment rather than a number pulled from the other column.
        ["Source", _source(a), _source(r) or ("same source" if _source(a) else "")],
        ["Request", req, "identical"],
        ["Output transferred", *_both("wire_mib", "{:,.2f} MiB")],
        ["Network bandwidth", _band(a), _band(r)],
        ["Remote reduction time",
         _cell(a, "remote_exec_s", "{:,.2f} s"),
         _cell(r, "remote_exec_s", "{:,.2f} s", "none — nothing ran remotely")],
        ["Local work time", _local(a), _local(r)],
        ["Pull time", _pull(a), _pull(r)],
        ["End-to-end Sieve time", *_both("total_s", "{:,.2f} s")],
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
    # Which direction the rate asymmetry cuts, stated in the table rather than
    # left for a reader to assume. A flagged cell is NOT a slower link: the two
    # runs are minutes apart on one link, and the only unflagged rate is the real
    # one. Because the fetch route is the column measuring true throughput, any
    # wall-clock gap in the reduce route's favour is understated, not inflated.
    if any(rec and 0 < (rec.get("wire_mib") or 0) < _BAND_FLOOR_MIB
           for rec in (a, r)):
        w.writerow([f"(note) a payload under {_BAND_FLOOR_MIB:,.0f} MiB times the "
                    f"fixed per-transfer cost, not the link — a (latency-bound) "
                    f"rate above is not evidence of a slower connection",
                    "same link, minutes apart",
                    "the unflagged rate is the session's real link speed"])
    # When BOTH payloads clear the floor and their rates still diverge, the wall
    # clocks are not directly comparable and the gap is not purely the route. Say
    # so, and say which way it cuts: a slower baseline FLATTERS the reduce, the
    # opposite of the latency-bound case above. Silence here would let a reader
    # read the end-to-end ratio as though one number caused it.
    def _rate(rec):
        if rec is None:
            return None
        wire, t = rec.get("wire_mib"), rec.get("transfer_s")
        return (wire / t) if (wire and t and wire >= _BAND_FLOOR_MIB) else None

    ra, rr = _rate(a), _rate(r)
    if ra and rr and max(ra, rr) / min(ra, rr) >= 1.5:
        # What the slower side's wall clock would have been at the faster rate.
        adj = (r.get("total_s") - r.get("transfer_s")
               + r.get("wire_mib") / max(ra, rr)) if rr < ra else None
        w.writerow(["(note) the two routes did NOT see the same effective rate, so "
                    "the wall clocks are not a clean route comparison",
                    f"{ra:,.2f} MiB/s", f"{rr:,.2f} MiB/s "
                    + (f"— {ra / rr:,.1f}x slower per byte; at E1a's rate E1b "
                       f"would be ~{adj:,.0f} s, not {r.get('total_s'):,.0f} s, "
                       f"so the gap below is FLATTERED"
                       if adj else f"— {rr / ra:,.1f}x faster per byte, so the gap "
                                   f"below is conservative")])


def _results_e1local(w, picked):
    """E1local — the same request with the reduce turned OFF (VISLANG_REMOTE=off):
    the whole file crosses the wire and the narrowing runs locally afterwards.

    A different row set from _results_e1 on purpose. There is no remote reduction
    and no result pull to report; what there IS, and what the reduce route has no
    equivalent of, is a transfer of the ENTIRE source followed by local work on
    it — so the table reports where the wall clock went, which is the finding.

    `Over-fetch` is reported here even though _results_e1 deliberately drops
    `Byte reduction`: that row was excluded as a ratio against a COUNTERFACTUAL
    (a whole-file copy that never ran). On this route the whole-file copy is
    exactly what DID run, so wire ÷ result is two measured quantities from one
    run — the bytes that crossed against the bytes that were kept.

    Nothing from another session is folded in. Comparing this against the reduce
    route's numbers is a job for the paper text, where the two sessions can be
    named; a hardcoded denominator in here would be the very thing the E1 table's
    comment warns against."""
    held, r = picked.get("E1a_local"), picked.get("E1b_local")
    if not (held or r):
        return
    w.writerow(["E1local — Same request without remote reduce "
                "(whole-file fetch, local narrowing)"])
    w.writerow(["Quantity", "Value"])
    if held:
        # The gate's own prediction, from the run where nothing was materialized.
        # Read it HERE rather than from the executed run: on the fetch path the
        # post-fetch local plan overwrites est_* with its own (small) local-read
        # estimate, so the executed row no longer carries what the gate predicted.
        est, band = held.get("est_read_mib"), None
        if held.get("est_time_lo_s") is not None:
            band = _span(held["est_time_lo_s"], held["est_time_hi_s"],
                         lambda v: f"{v / 60:.0f}") + " min"
        w.writerow(["Gate, unconfirmed run", f"{held.get('status')} — "
                    f"{_num(held.get('wire_mib'), '{:,.2f} MiB')} crossed"])
        w.writerow(["Gate predicted over the wire", _num(est, "{:,.2f} MiB")])
        w.writerow(["Gate predicted time", band or ""])
        w.writerow(["Gate decision", "HELD (over budget)"
                    if held.get("held") else ""])
    if not r:
        w.writerow(["(no confirmed E1b_local run recorded)", ""])
        return
    src, wire = r.get("source_mib"), r.get("wire_mib")
    res, tr = r.get("result_mib"), r.get("transfer_s")
    fmt = _fmt_from_uri(r.get("uri"))
    req = RESULT_ANNOT["E1b_local"]["request"]
    # Local work is every phase that ran AFTER the bytes landed — the entire cost
    # of the narrowing itself, against which the transfer above should be read.
    local_s = sum(v for v in (r.get("inspect_s"), r.get("lower_s"),
                              r.get("materialize_s"), r.get("sink_s"))
                  if v is not None) or None
    note = RESULT_ANNOT["E1b_local"].get("source_note")
    w.writerows([
        ["Source", "; ".join(x for x in
                             [f"{src:,.2f} MiB {fmt}".strip() if src is not None else "",
                              note] if x)],
        ["Request", req],
        ["Over the wire", _num(wire, "{:,.2f} MiB")],
        ["Result kept", _num(res, "{:,.2f} MiB")],
        ["Over-fetch (wire ÷ result)",
         f"{wire / res:,.1f}x" if wire and res else ""],
        ["Wire ÷ source", _num(r.get("reduction_x"), "{:,.1f}x")],
        ["Transfer time", _num(tr, "{:,.2f} s")],
        ["Transfer throughput",
         f"{wire / tr:,.2f} MiB/s" if wire and tr else ""],
        ["Fetch phase (transfer + probes)", _num(r.get("fetch_s"), "{:,.2f} s")],
        ["Local inspect", _num(r.get("inspect_s"), "{:,.2f} s")],
        ["Local narrowing (materialize)", _num(r.get("materialize_s"), "{:,.2f} s")],
        ["Local save", _num(r.get("sink_s"), "{:,.2f} s")],
        ["Local work, total", _num(local_s, "{:,.2f} s")],
        ["End-to-end Sieve time", _num(r.get("total_s"), "{:,.2f} s")],
        ["Share of wall clock spent transferring",
         f"{tr / r['total_s']:.1%}" if tr and r.get("total_s") else ""],
    ])


def _compare_data(picked, base):
    """Every measured quantity behind the E1-vs-E1local comparison, computed once
    and formatted by _results_compare.

    Each value is a (remote_reduce, whole_file_fetch) pair. None means "this route
    has no such quantity" (there is no remote reduction on a fetch), which the
    formatters render as a dash rather than a zero."""
    r, b = picked.get("E1b_local"), base.get("E1b")
    if not (r and b):
        return None

    def rate(x):
        """Effective bulk rate: bytes that crossed ÷ the rsync that carried them."""
        w_, t_ = x.get("wire_mib"), x.get("transfer_s")
        return w_ / t_ if w_ and t_ else None

    def local_s(x):
        return sum(v for v in (x.get("inspect_s"), x.get("lower_s"),
                               x.get("materialize_s"), x.get("sink_s"))
                   if v is not None) or None

    def over(x):
        w_, res = x.get("wire_mib"), x.get("result_mib")
        return w_ / res if w_ and res else None

    return {
        "source_mib": r.get("source_mib"),
        "fmt": _fmt_from_uri(r.get("uri")),
        "source_note": RESULT_ANNOT["E1b_local"].get("source_note"),
        "request": RESULT_ANNOT["E1b_local"]["request"],
        "route": (b.get("route"), r.get("route")),
        "wire": (b.get("wire_mib"), r.get("wire_mib")),
        "result": (b.get("result_mib"), r.get("result_mib")),
        "over": (over(b), over(r)),
        # The fetch route runs nothing remotely, so this is absent, not zero.
        "remote_exec": (b.get("remote_exec_s"), None),
        "transfer": (b.get("transfer_s"), r.get("transfer_s")),
        # pull_s and fetch_s are the same level of the phase tree: the phase that
        # WRAPS my_download.transfer() on each route.
        "pull": (b.get("pull_s"), r.get("fetch_s")),
        "rate": (rate(b), rate(r)),
        "local": (local_s(b), local_s(r)),
        "rts": ((b.get("ssh_query") or 0) + (b.get("ssh_exec") or 0),
                (r.get("ssh_query") or 0) + (r.get("ssh_exec") or 0)),
        "srun": (b.get("remote_jobs") or 0, r.get("remote_jobs") or 0),
        "total": (b.get("total_s"), r.get("total_s")),
    }


def _pen(pair, unit="x"):
    """The fetch route's cost as a multiple of the reduce route's."""
    b, r = pair
    return f"{r / b:,.1f}{unit}" if b and r else ""


def _results_compare(w, picked, base, base_label):
    """E1 vs E1local, row for row: the SAME request on the SAME file, reduced next
    to the data versus fetched whole and narrowed here.

    Both columns are read from timing records — `base` comes from another session's
    timings.jsonl (named in `base_label`, so a reader can never mistake it for this
    session's measurement). That is the one honest way to state a movement ratio:
    the denominator was measured by the same runtime on the same file, not assumed.

    The two runs saw DIFFERENT link speeds, so the effective-rate row is reported
    for both and the wall-clock ratio must be read with it in hand — see the note
    the caller writes underneath."""
    d = _compare_data(picked, base)
    if not d:
        return
    w.writerow([f"E1 vs E1local — same request, same file: reduce next to the data "
                f"vs fetch the whole file"])
    w.writerow(["Quantity", f"remote reduce ({base_label})",
                "no remote reduce (this session)", "penalty"])

    def _cells(key, fmt, pen="", absent="—"):
        b_, r_ = d[key]
        return [absent if b_ is None else fmt.format(b_),
                absent if r_ is None else fmt.format(r_), pen]

    src = (f"{d['source_mib']:,.2f} MiB {d['fmt']}".strip()
           if d["source_mib"] else "")
    rows_out = [
        ["Source", "; ".join(x for x in (src, d["source_note"]) if x),
         "same file", ""],
        ["Request", d["request"], "identical", ""],
        ["Route", *d["route"], ""],
        ["Output transferred", *_cells("wire", "{:,.2f} MiB",
                                       _pen(d["wire"]) + " the bytes")],
        ["Result kept", *_cells("result", "{:,.2f} MiB", "same result")],
        ["Over-fetch (wire ÷ result)", *_cells("over", "{:,.1f}x")],
        ["Remote reduction time",
         *_cells("remote_exec", "{:,.2f} s", absent="none — nothing ran remotely")],
        ["Transfer time (rsync alone)",
         *_cells("transfer", "{:,.2f} s", _pen(d["transfer"]))],
        ["Pull / fetch phase (transfer + probes)",
         *_cells("pull", "{:,.2f} s", _pen(d["pull"]))],
        ["Effective transfer rate", *_cells("rate", "{:,.2f} MiB/s")],
        ["Local work (inspect + narrow + save)", *_cells("local", "{:,.2f} s")],
        # query + exec, the same definition _results_e3 uses. Counting only
        # ssh_query would read 3 vs 3 and hide the four exec round trips the
        # reduce route spends shipping the plan and launching the step — the
        # cost that buys the 52x, and the honest debit against it.
        ["ssh round trips (query + exec)", *_cells("rts", "{:d}")],
        ["srun jobs", *_cells("srun", "{:d}")],
        ["End-to-end Sieve time",
         *_cells("total", "{:,.2f} s", _pen(d["total"]) + " the wall clock")],
    ]
    w.writerows(rows_out)
    # The caveat belongs IN the table, not in a commit message: the fetch run saw a
    # faster link, so the wall-clock penalty above is if anything conservative.
    rb, rr = d["rate"]
    if rb and rr:
        w.writerow(["(note) link speed differed between the sessions",
                    f"{rb:,.2f} MiB/s", f"{rr:,.2f} MiB/s",
                    f"the fetch run's link was {rr / rb:,.2f}x "
                    f"{'faster' if rr > rb else 'slower'}, so the wall-clock "
                    f"penalty is {'conservative' if rr > rb else 'flattered'}"])


def _results_e2(w, picked):
    w.writerow(["E2 — Eight-question investigation of one 8.31 GiB HACC snapshot"])
    w.writerow(["Query", "Request", "Reuse", "Hit (%)", "Wire", "srun",
                "Pull (s)", "Total (s)"])
    wire_tot = srun_tot = pull_tot = total_tot = 0.0
    seen = False
    for name in ("E2q1", "E2q2", "E2q3", "E2q4", "E2q5",
                 "E2q6", "E2q7", "E2q8"):
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


def write_results(path, all_rows, baseline_rows=None, baseline_label=""):
    """Write the paper tables (E1 / E1local / E2 / E3) into one readable
    results.csv. Returns the list of runs that fed the E1 transfer band (logged,
    not silent).

    A section appears only when its runs exist, so a study that ran one group does
    not emit tables of "(no runs recorded)" for the others — except E1/E2, which
    are the paper's two standing tables and say so explicitly when empty."""
    picked = _latest_by_spec(all_rows)
    lo, hi, contrib = _transfer_band(picked)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        _results_e1(wr, picked, lo, hi)
        if picked.get("E1a_local") or picked.get("E1b_local"):
            wr.writerow([])
            _results_e1local(wr, picked)
            if baseline_rows:
                wr.writerow([])
                _results_compare(wr, picked, _latest_by_spec(baseline_rows),
                                 baseline_label or "baseline session")
        wr.writerow([])
        _results_e2(wr, picked)
        # E3 only when the session actually ran rejections — a study whose
        # groups are E1+E2 should not emit a table saying "(no E3 runs)".
        if any(picked.get(n) for n in ("E3a", "E3b", "E3c", "E3d")):
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
