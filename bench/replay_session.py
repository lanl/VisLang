"""Replay a recorded ParaView session under controlled setups.

An exploration session in the Sieve plugin is a sequence of specs: every
overview, box, timestep and prefetch the scientist asked for is a `spec.py` in
the session folder, with its request and result, listed in order in
`session.jsonl`. Replaying those specs with the think time removed gives the
same interaction sequence under each setup, side by side:

    cold           an empty cache: each step pays for what it moves; a box
                   inside an earlier box is cut from the cache
    warm           the same steps again on cold's cache: a revisit
    no-catalog     a fresh, empty cache for every step: what the catalog saves
    full-download  VISLANG_REMOTE=off: each source file copied whole (once —
                   later steps reuse the copy, as an ad hoc user would) and
                   narrowed here: the baseline Sieve is measured against

Each setup runs in isolation (its own cache, downloads, timings file). Every
step's output is checked against the session's own output by its data hash
(provenance `data_sha256`): a replay must reproduce the data bit for bit.

    python bench/replay_session.py <session dir> [--setups cold,warm,...]
                                   [--out DIR] [--sieve CMD]

Writes <out>/replay.json and replay.csv (one row per setup and step) and
prints the tables: time to first image, per-step latency, bytes moved
(cumulative), cache reuse, estimate against actual, reproduction.
"""

import argparse
import csv
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

SETUPS = ("cold", "warm", "no-catalog", "full-download")
ACTIONS = ("overview", "detail", "spec", "prefetch")


def load_steps(session_dir):
    """The session's data requests, in order: those Sieve actually ran
    (not panel memo hits, not metadata reads, not held requests)."""
    lines = []
    with open(os.path.join(session_dir, "session.jsonl")) as f:
        for ln in f:
            try:
                lines.append(json.loads(ln))
            except ValueError:
                pass
    steps = []
    for ln in lines:
        if ln.get("action") not in ACTIONS or ln.get("memo_hit") \
                or ln.get("status") != "ok" or not ln.get("spec_path"):
            continue
        step_dir = os.path.dirname(ln["spec_path"])
        with open(ln["spec_path"]) as f:
            text = f.read()
        approval = {}
        try:
            with open(os.path.join(step_dir, "request.json")) as f:
                approval = json.load(f).get("approval") or {}
        except (OSError, ValueError):
            pass
        steps.append({"n": len(steps) + 1, "action": ln["action"],
                      "source": ln.get("source"), "spec": text,
                      "approval": approval, "files": ln.get("files") or [],
                      "original": ln})
    return steps


def retarget(text, outdir):
    """The spec with its save() path moved into `outdir` (file name kept), so a
    replay never writes over the session's own outputs."""
    def repl(m):
        return f"{m.group(1)}{m.group(2)}{os.path.join(outdir, os.path.basename(m.group(3)))}" \
               f"{m.group(2)}{m.group(4)}"
    return re.sub(r"(save\(.*?,\s*)(['\"])([^'\"]+)\2(\s*\))", repl, text, flags=re.S)


def data_hash(path):
    """The output's data hash from its provenance record, or None."""
    try:
        from vislang.runtime import provenance
        rec = provenance.record_for(path)
        out = (rec or {}).get("logical", {}).get("output", {})
        fp = out.get("fingerprint") or {}
        if fp.get("data_sha256"):
            return fp["data_sha256"]
        ts = out.get("timestep_fingerprints") or {}
        return ",".join(f"{k}:{v.get('data_sha256')}" for k, v in sorted(ts.items())) or None
    except Exception:
        return None


def env_for(setup, root, n):
    base = os.path.join(root, setup)
    cache = os.path.join(root, "cold", "cache") if setup == "warm" else (
        os.path.join(base, f"cache-{n}") if setup == "no-catalog"
        else os.path.join(base, "cache"))
    env = {"VISLANG_CACHE": cache,
           "VISLANG_DOWNLOADS": os.path.join(base, "downloads"),
           "VISLANG_TIMINGS_FILE": os.path.join(base, "timings.jsonl")}
    if setup == "full-download":
        env["VISLANG_REMOTE"] = "off"
    return env


def replay(steps, setups, root, sieve):
    rows = []
    for setup in setups:
        if setup == "warm" and "cold" not in setups:
            raise SystemExit("warm replays on cold's cache: include cold before it")
        for st in steps:
            work = os.path.join(root, setup, f"{st['n']:03d}-{st['action']}")
            os.makedirs(work, exist_ok=True)
            req = {"workdir": work, "spec_text": retarget(st["spec"], work),
                   "approval": st["approval"],
                   "client": {"name": "replay", "session": setup, "step": st["n"]}}
            req_path = os.path.join(work, "request.json")
            res_path = os.path.join(work, "result.json")
            with open(req_path, "w") as f:
                json.dump(req, f, indent=1)
            env = {**os.environ, **env_for(setup, root, st["n"])}
            t0 = time.perf_counter()
            p = subprocess.run([sieve, "api", "run", "--request", req_path,
                                "--result", res_path], capture_output=True, text=True,
                               env=env)
            wall = time.perf_counter() - t0
            try:
                with open(res_path) as f:
                    r = json.load(f)
            except (OSError, ValueError):
                r = {"status": "error", "error": (p.stderr or "")[-500:]}
            est = r.get("estimate") or {}
            out_files = [f["path"] for f in r.get("files") or []]
            orig_hash = data_hash(st["files"][0]) if st["files"] else None
            new_hash = data_hash(out_files[0]) if out_files else None
            reuse = r.get("reuse") or {}
            rows.append({
                "setup": setup, "step": st["n"], "action": st["action"],
                "status": r.get("status"), "route": r.get("route"),
                "wall_s": round(wall, 3), "sieve_s": r.get("total_s"),
                "wire_bytes": r.get("wire_bytes") or 0,
                "probe_bytes": r.get("probe_bytes") or 0,
                "est_read_mb": est.get("read_mb"), "est_site": est.get("site"),
                "reused": reuse.get("reused_pairs", reuse.get("cached_vars")),
                "reusable": reuse.get("total_pairs", reuse.get("want_vars")),
                "data_sha256": new_hash, "reproduced": (new_hash == orig_hash
                                                         if orig_hash else None),
                "error": r.get("error"), "run_id": r.get("run_id")})
            print(f"  {setup:>13} step {st['n']:>2} {st['action']:<8} "
                  f"{r.get('status'):<6} {wall:6.2f} s  "
                  f"{(r.get('wire_bytes') or 0) / 1024 ** 2:9.2f} MiB  "
                  f"{r.get('route') or ''}")
    return rows


def tables(rows, setups):
    """The summary a paper table is made from."""
    lines = []
    head = f"{'setup':>13} | {'first image s':>13} | {'median s':>8} | {'p90 s':>6} | " \
           f"{'moved MiB':>9} | {'reproduced':>10}"
    lines += [head, "-" * len(head)]
    for setup in setups:
        rs = [r for r in rows if r["setup"] == setup and r["status"] == "ok"]
        if not rs:
            continue
        walls = sorted(r["wall_s"] for r in rs)
        p90 = walls[min(len(walls) - 1, int(round(0.9 * (len(walls) - 1))))]
        moved = sum(r["wire_bytes"] for r in rs) / 1024 ** 2
        rep = [r["reproduced"] for r in rs if r["reproduced"] is not None]
        lines.append(f"{setup:>13} | {rs[0]['wall_s']:13.2f} | "
                     f"{statistics.median(walls):8.2f} | {p90:6.2f} | {moved:9.2f} | "
                     f"{sum(rep):>4}/{len(rep):<5}")
    lines.append("")
    lines.append("bytes moved, cumulative by step (MiB):")
    steps = sorted({r["step"] for r in rows})
    for setup in setups:
        acc, cum = 0, []
        for s in steps:
            r = next((x for x in rows if x["setup"] == setup and x["step"] == s), None)
            acc += (r or {}).get("wire_bytes", 0)
            cum.append(f"{acc / 1024 ** 2:.1f}")
        lines.append(f"  {setup:>13}: " + " ".join(cum))
    cold = [r for r in rows if r["setup"] == "cold" and r["est_site"] == "remote"
            and r["est_read_mb"] is not None and r["wire_bytes"]]
    if cold:
        lines.append("")
        lines.append("estimate against actual (cold, remote steps):")
        for r in cold:
            act = r["wire_bytes"] / 1024 ** 2
            lines.append(f"  step {r['step']:>2}: predicted {r['est_read_mb']:.2f} MiB, "
                         f"moved {act:.2f} MiB ({100 * (r['est_read_mb'] - act) / act:+.0f}%)")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("session", help="a session folder (<sieve home>/paraview/<id>)")
    ap.add_argument("--setups", default=",".join(SETUPS))
    ap.add_argument("--out", help="results folder (default bench/results/replay-<id>)")
    ap.add_argument("--sieve", default=os.path.join(REPO, "sieve"))
    args = ap.parse_args(argv)
    setups = [s.strip() for s in args.setups.split(",") if s.strip()]
    bad = [s for s in setups if s not in SETUPS]
    if bad:
        raise SystemExit(f"unknown setup(s) {bad}; choose from {SETUPS}")
    session = os.path.abspath(args.session)
    out = os.path.abspath(args.out or os.path.join(
        REPO, "bench", "results", "replay-" + os.path.basename(session.rstrip("/"))))
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(out)
    steps = load_steps(session)
    if not steps:
        raise SystemExit(f"no replayable requests in {session}/session.jsonl")
    print(f"replaying {len(steps)} step(s) from {session}\n  under {setups} -> {out}")
    rows = replay(steps, setups, out, args.sieve)
    with open(os.path.join(out, "replay.json"), "w") as f:
        json.dump(rows, f, indent=1, default=str)
    with open(os.path.join(out, "replay.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    text = tables(rows, setups)
    with open(os.path.join(out, "summary.txt"), "w") as f:
        f.write(text + "\n")
    print("\n" + text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
