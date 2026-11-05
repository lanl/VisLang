"""Copy one step's run record out of Sieve's timing log into the step's results folder.

    python record_step.py <results/stepN> [<lines before the run>]

Writes <stepN>/timing_record.json (the run's whole record, verbatim) and <stepN>/numbers.json:
the three quantities every request reports (estimated bytes, actual bytes, remote reduce time on
the server), copied from the record, never recomputed:
  est_server_read_bytes / est_wire_bytes   the estimate (MiB in the record -> bytes)
  server_read_bytes                        what the executor fetched from storage (Linux read_bytes)
  wire_bytes                               bytes over the network (run counter)
  server_s, server_phases                  the remote job's own clock and its breakdown
  route, cached_bytes, remote_jobs         how the request was served
Transfer times, ssh round trips, launch_s and total_s are deliberately left out (they depend on
the VPN link and are not reported).
"""
import json
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), *[".."] * 5))
MIB = 1024 ** 2


def main(step_dir, before=None):
    path = os.path.join(REPO, ".vislang", "timings.jsonl")
    lines = open(path).read().splitlines()
    new = lines[int(before):] if before is not None else lines[-1:]
    runs = [json.loads(ln) for ln in new if ln.strip()]
    runs = [r for r in runs if not all(p.get("dry_run") for p in r.get("pipelines") or [{}])] or runs
    rec = runs[-1]
    with open(os.path.join(step_dir, "timing_record.json"), "w") as f:
        json.dump(rec, f, indent=1)
    pipe = (rec.get("pipelines") or [{}])[0]
    rx = [p for p in rec.get("phases") or () if (p.get("name") or "").endswith("remote_exec")]
    est_srv, est_wire = pipe.get("est_server_read_mb"), pipe.get("est_read_mb")
    out = {
        "run_id": rec.get("run_id"), "status": rec.get("status"), "started": rec.get("started"),
        "commit": rec.get("commit"),
        "est_server_read_bytes": None if est_srv is None else round(est_srv * MIB),
        "est_wire_bytes": None if est_wire is None else round(est_wire * MIB),
        "est_result_bytes": None if pipe.get("est_output_mb") is None
        else round(pipe["est_output_mb"] * MIB),
        "est_over_budget": pipe.get("est_over_budget"),
        "server_read_bytes": sum(p["server_read_bytes"] for p in rx
                                 if p.get("server_read_bytes") is not None) if rx else None,
        "wire_bytes": (rec.get("counters") or {}).get("wire_bytes"),
        "probe_bytes": (rec.get("counters") or {}).get("probe_bytes"),
        "server_s": sum(p["server_s"] for p in rx if p.get("server_s") is not None) if rx else None,
        "server_phases": [p.get("server_phases") for p in rx],
        "remote_jobs": (rec.get("counters") or {}).get("remote_jobs"),
        "route": pipe.get("route"), "cached_bytes": pipe.get("cached_bytes"),
        "result_bytes": pipe.get("result_bytes"), "source_bytes": pipe.get("source_bytes"),
    }
    with open(os.path.join(step_dir, "numbers.json"), "w") as f:
        json.dump(out, f, indent=1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
