#!/usr/bin/env python3
"""The case study's cost table, in bytes, from recorded files only.

    python cost_table.py TEST2_DIR

Reads:
  data/stat_series.tsv                 stat -L of every series file (gpu-server)
  results/step1/overview_result.json   Sieve overview: wire + probe bytes
  results/step3/detail_result.json     Sieve Detail: wire + probe bytes
  results/step1/overview_timings.jsonl, results/step3/detail_timings.jsonl
                                       bytes Sieve read on the server (source_bytes)
  data/server_cut_sizes.txt            the 38 cuts pass2_cut.py wrote on gpu-server
Writes results/cost_table.csv and results/cost_table.md.

Which files each baseline copies is derived here exactly as its script picks them:
pass 1 = every 5th label of the sorted series (pass1_download.sh), pass 2 = #4000 and
#9000-#45000 not already held (pass2_download.sh); download-all = every file.
"""
import csv
import json
import os
import sys

T = sys.argv[1]
J = lambda *p: os.path.join(T, *p)

size = {}
for line in open(J("data", "stat_series.tsv")):
    if line.startswith("fire#"):
        name, _target, nbytes, _mtime = line.rstrip("\n").split("\t")
        size[int(name[5:-4])] = int(nbytes)
labels = sorted(size)
pass1 = labels[::5]
window = [4000] + list(range(9000, 46000, 1000))
pass2 = [n for n in window if n not in pass1]
B = lambda ls: sum(size[n] for n in ls)


def sieve(step, name):
    r = json.load(open(J("results", step, f"{name}_result.json")))
    t = json.loads(open(J("results", step, f"{name}_timings.jsonl")).readline())
    return r["wire_bytes"], r["probe_bytes"], t["pipelines"][0]["source_bytes"]


ov_w, ov_p, ov_r = sieve("step1", "overview")
de_w, de_p, de_r = sieve("step3", "detail")
cuts = [int(l.split()[0]) for l in open(J("data", "server_cut_sizes.txt")) if l.strip()]

rows = [
    # route, step, timesteps seen in the overview pass, wire bytes, probe bytes, server read, code lines written, data steps
    ("Sieve (ParaView)", "overview: all timesteps, stride 8, 5 fields", 88, ov_w, ov_p, ov_r, 0, 1),
    ("Sieve (ParaView)", "Detail: box, full resolution, 38 timesteps, 4 fields", "", de_w, de_p, de_r, 0, 1),
    ("Sieve (ParaView)", "total", 88, ov_w + de_w, ov_p + de_p, ov_r + de_r, 0, 2),
    ("Two-pass download", f"pass 1: every 5th file whole ({len(pass1)} files)", len(pass1), B(pass1), 0, B(pass1), 9 + 29, 2),
    ("Two-pass download", f"pass 2: window files not held ({len(pass2)} files), then cut", "", B(pass2), 0, B(pass2), 9 + 42, 2),
    ("Two-pass download", f"total ({len(pass1) + len(pass2)} files)", len(pass1), B(pass1 + pass2), 0, B(pass1 + pass2), 89, 4),
    ("Download everything", f"all {len(labels)} files whole, then the same scripts", len(labels), B(labels), 0, B(labels), 29 + 42 + 2, 3),
    ("Work on the server (mention only)", f"scripts run on gpu-server over all {len(labels)} files and the {len(window)} window files; copy back the {len(cuts)} cuts", len(labels), sum(cuts), 0, B(labels) + B(window), 29 + 42 + 1, 3),
]
sieve_total = ov_w + de_w
with open(J("results", "cost_table.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["route", "step", "timesteps_seen_overview", "wire_bytes", "probe_bytes",
                "server_read_bytes", "code_lines_written", "data_steps", "wire_vs_sieve_total"])
    for r in rows:
        w.writerow(list(r) + ["" if r[0].startswith("Sieve") else round(r[3] / sieve_total, 2)])

gb = lambda b: "—" if b is None else f"{b / 1e9:,.2f} GB"
md = ["| route | step | timesteps seen (overview) | over the wire | read on the server | lines of code written | wire bytes vs Sieve total |",
      "|---|---|---|---|---|---|---|"]
for r in rows:
    md.append(f"| {r[0]} | {r[1]} | {r[2]} | {gb(r[3])} ({r[3]:,} B) | {gb(r[5])} | {r[6]} | {'—' if r[0].startswith('Sieve') else f'{r[3] / sieve_total:,.1f}×'} |")
md += ["", f"Sieve's probes (a fixed 4 MiB bandwidth probe before each fetch): {ov_p + de_p:,} B, not in the wire column.",
       f"Pass 1 files: {', '.join(f'#{n}' for n in pass1)}",
       f"Pass 2 files: {', '.join(f'#{n}' for n in pass2)}",
       "Lines of code: non-blank, non-comment lines of the route's own data-handling scripts "
       "(the 74-line front/wind analysis, scripts/cs2_front.py, is the same in every route). "
       "Sieve's specs (4 + 5 lines) are written by the ParaView plugin, not by the user."]
open(J("results", "cost_table.md"), "w").write("\n".join(md) + "\n")
print("\n".join(md))
