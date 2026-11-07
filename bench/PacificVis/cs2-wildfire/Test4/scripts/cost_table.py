#!/usr/bin/env python3
"""Table 1 of Case Study 2: what each route moves over the network to get the same two
results (the overview series and the box series), from the recorded files only.

    python cost_table.py TEST4_DIR

Reads
  results/sieve_cold/{overview,box}/numbers.json   Sieve's run records (record_step.py)
  results/server_route/{cut_overview,cut_box}.json  the server script's time and reads
  results/server_route/cut_sizes.txt                bytes the server script copies back
  data/stat_series.txt                              size of every file in the series
  results/lines.txt                                 lines of code per route (count_lines.py)
Writes results/cost_table.{csv,md,tex}.
"""
import csv
import json
import os
import re
import sys

T = sys.argv[1]
R = os.path.join(T, "results")
GB = 1e9


def jload(*p):
    return json.load(open(os.path.join(R, *p)))


sizes = {}
for ln in open(os.path.join(T, "data", "stat_series.txt")):
    b, path = ln.split(maxsplit=1)
    sizes[int(re.search(r"#(\d+)", path).group(1))] = int(b)
labels = sorted(sizes)
every5 = labels[::5]
window = [n for n in labels if 9000 <= n <= 45000]
lines = {ln.split("\t")[0]: int(ln.split("\t")[1]) for ln in open(os.path.join(R, "lines.txt"))}

so, sb = jload("sieve_cold", "overview", "numbers.json"), jload("sieve_cold", "box", "numbers.json")
cut = {ln.split()[1].rstrip("/").split("/")[-1]: int(ln.split()[0])
       for ln in open(os.path.join(R, "server_route", "cut_sizes.txt"))}
co, cb = jload("server_route", "cut_overview.json"), jload("server_route", "cut_box.json")

rows = [
    {"route": "Sieve", "seen": len(labels), "overview": so["wire_bytes"], "detail": sb["wire_bytes"],
     "server_s": so["server_s"] + sb["server_s"], "lines": lines["sieve"]},
    {"route": "Scripts on the server", "seen": len(labels), "overview": cut["overview"],
     "detail": cut["box"], "server_s": co["server_s"] + cb["server_s"], "lines": lines["server_route"]},
    {"route": "Two-pass download", "seen": len(every5), "overview": sum(sizes[n] for n in every5),
     "detail": sum(sizes[n] for n in window if n not in every5), "server_s": None,
     "lines": lines["two_pass"]},
    {"route": "Download everything", "seen": len(labels), "overview": sum(sizes.values()),
     "detail": 0, "server_s": None, "lines": lines["download_all"]},
]
base = rows[0]["overview"] + rows[0]["detail"]
for r in rows:
    r["total"] = r["overview"] + r["detail"]
    r["x_sieve"] = r["total"] / base

with open(os.path.join(R, "cost_table.csv"), "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)


def gb(b):
    return f"{b / GB:.2f}" if b < 100 * GB else f"{b / GB:.1f}"


md = ["| Route | Timesteps in overview | Overview GB | Detail GB | Total GB | x Sieve | Server s | Lines |",
      "|---|---|---|---|---|---|---|---|"]
tex = [r"\begin{tabular}{@{}lrrrrrrr@{}}", r"\toprule",
       r" & Timesteps & \multicolumn{3}{c}{GB over the network} & Bytes vs & Server & Lines \\",
       r"\cmidrule(lr){3-5}",
       r"Route & in overview & overview & detail & total & \Sname{} & time (s) & of code \\",
       r"\midrule"]
for r in rows:
    srv = "---" if r["server_s"] is None else f"{r['server_s']:,.0f}"
    x = f"{r['x_sieve']:.1f}" if r["x_sieve"] < 10 else f"{r['x_sieve']:.0f}"
    md.append(f"| {r['route']} | {r['seen']} | {gb(r['overview'])} | {gb(r['detail'])} | "
              f"{gb(r['total'])} | {x}x | {srv} | {r['lines']} |")
    name = r"\Sname{}" if r["route"] == "Sieve" else r["route"]
    tex.append(f"{name} & {r['seen']} & {gb(r['overview'])} & {gb(r['detail'])} & "
               f"{gb(r['total'])} & {x}$\\times$ & {srv.replace(',', '{,}')} & {r['lines']} \\\\")
tex += [r"\bottomrule", r"\end{tabular}"]
open(os.path.join(R, "cost_table.md"), "w").write("\n".join(md) + "\n")
open(os.path.join(R, "cost_table.tex"), "w").write("\n".join(tex) + "\n")
print("\n".join(md))
