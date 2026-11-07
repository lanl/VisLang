# Case Study 2 (wildfire): objectives for Test4

Written 2026-11-07, before Test4 ran. Numbers marked *(T3)* come from Test3
(`../CASE_STUDY_BRIEF.md`) and will be replaced by Test4's measured values.

## 1. What CS2 shows that the other case studies do not

| | CS1 cosmology | **CS2 wildfire** | CS3 channel flow |
|---|---|---|---|
| Unknown in advance | nothing (halo centres given) | **when** the event happens | which reduction keeps the feature |
| Main selection | space + fields | **time**, then space + fields | values (block minimum, threshold) |
| How the user works | one request | **overview first, then details on demand** (Shneiderman 1996), in ParaView | six scripted steps |

**Thesis of CS2.** When the time of an event in a remote time series is unknown, Sieve lets
the scientist (1) fetch an overview that keeps *every* timestep at reduced spatial
resolution, cheaply enough to take all of it; (2) choose the time window and the region by
looking at that overview; (3) fetch only that window and region at full resolution; and
(4) open both in ParaView, where they overlay as saved. The scientist never commits to a
sampling interval before seeing the data, and never moves the series.

**Owned elsewhere, kept out of CS2:** cost on the server's disk and the budget gate
(CS3), provenance in depth (CS3), cache reuse (CS3), format conversion (CS1). CS2 uses
each only where the workflow needs it: the overview's price before it moves, and one
provenance excerpt in the teaser.

## 2. The scientific question, and why each part of the answer needs a Sieve selection

**Question** (posed after Dupuy et al. 2011): near-ground air ahead of a head fire can
reverse and flow back toward it, an indraft. Dupuy et al. find this in the lowest grid
cell, typically within 10 m of the fire line; only downslope does it reach farther (their
Table 6: < 30 m in shrubland, < 40 m in grassland under a weak wind, < 10 m in grassland
at 6 m/s). In this run the fire crosses a ridge crest and spreads down the lee
slope, where it slows. *When the spread slows, does the near-ground wind ahead of the
front reverse and blow against it?*

**What the Dupuy citation gives the paper (A3).** It is not added as extra science. It sets
the **resolution requirement** that forces the second request. The flow in question
lives in the lowest metres and the first ~10 m ahead of the front. The overview (stride 8)
samples every 16 m horizontally and keeps two levels in the lowest 11.5 m. It cannot
resolve that flow by construction, so the full-resolution request follows from the
literature, not from our convenience. One sentence in the results compares the extent we
find with Dupuy's.

**Answer (the three-phase timeline, Test2 data; Test4 re-measures it).** Each phase is
tied to the selection that makes it visible:

| Phase | Finding *(T2/T3)* | Needs | Sieve selection |
|---|---|---|---|
| 1. Slowdown begins | the front speed drops (#17000→#22000) while the air 1–5 m up ahead of the front still blows forward (+8.9 → +5.9 m/s) | every timestep, to time the drop; full vertical resolution, to measure 1–5 m | overview of all timesteps; full-resolution box |
| 2. Reversal appears | first aloft (13–20 m) and farther downslope (x 100–160 m), from #21000–#22000 | the box must reach 50 m up and 160 m downslope | box `x=(-100,200)`, `z=(0,350)` |
| 3. Reversal reaches the front | 1–5 m ahead of the front first negative at #34000; strip 25/25 timesteps, flanks 2/25 | the window must run past #34000; the box must include both flanks | window `9000–45000`; box `y=(-120,120)` |

**Conclusion the paper may state:** the reversal does not cause the onset of the slowdown;
it follows it, starts aloft and downslope, and is confined to where the fire burns. The
cause is not established. No claim is made at grid level 0: the files do not record its
height (ground surface or first cell centre), and Test4 measures from level 1 (1.25 m) up.
Time is given in simulation steps, because dt is not in the files (asked of the data's
authors).

## 3. Claims about Sieve, and the evidence each needs

| # | Claim | Evidence (Test4) | Where in the paper |
|---|---|---|---|
| C1 | The overview is cheap enough to take whole: all 88 timesteps for a small fraction of the series | wire bytes; estimate before moving *(T3: 47 MB vs 96.6 GB; priced 102 MiB)* | text + Table 1 |
| C2 | The overview is **sufficient** to find *when* | full-resolution front for all 88 timesteps vs stride 8: front error, crest-crossing time, slowdown interval, and the same window chosen | Fig. speed (grey reference curve) + one sentence |
| C3 | Stride 8 is a measured choice, not a lucky one | stride sweep 16/8/4/2: front error vs bytes | one sentence (or an inset), numbers in the supplement |
| C4 | Sampling files instead of space misses the transition | every 5th file brackets the drop only to 5,000 steps; the drop happens inside one gap | Fig. speed (redesigned, see §5) |
| C5 | The overview is **insufficient** for the near-ground wind, so the detail is needed | levels kept below 11.5 m: 2 vs 8; the 1–5 m quantity cannot be formed from the overview | text (with the Dupuy requirement) |
| C6 | The detail is cheap: one window × one box at full resolution | wire bytes vs the window's whole files *(T3: 0.36 GB vs 34 GB)* | Table 1 |
| C7 | Same result as the alternatives, with far less effort | Table 1: the four routes; the exact-equivalent script moves ~the same bytes; lines of code; server time | Table 1 + one paragraph |
| C8 | The outputs compose: the detail sits inside the overview in ParaView with no alignment step | the teaser; outputs carry the curvilinear grid's own positions | teaser + one sentence |
| C9 | The scientist chose window and box from the overview alone | the window rationale uses only overview quantities | text (one paragraph, §4 step 2) |

Each paragraph of the case study must serve one of C1–C9 or the answer in §2.
Anything that serves neither is cut.

## 4. The workflow as the paper will tell it (numbered steps, as in CS3)

1. **Overview.** `fields` (rhof_1, theta, u, v, w), `subsample(…, 8)`, all 88 timesteps. Priced, then fetched.
   Opened in ParaView as a time series through the plugin.
2. **Choose the window and the box from the overview** (nothing moves). From the front
   position and speed in the overview: the drop runs from about #17000 to #22000. The window
   #9000–#45000 starts well before it and runs long after it. The box spans the crest and the
   lee slope, drawn with ParaView's box widget on the overview.
3. **Details on demand.** `timesteps(src, 9000, 45000)`, `fields` (theta, u, v, w), `region(…,
   coords="world")`, full resolution. Priced on the overview's cached grid positions, then fetched.
4. **Analysis** on the box: the three phases (§2).

The ParaView loop gets two sentences: the plugin issued both requests, and the box was
drawn on the overview. The plugin's design is described in the system section.

## 5. Figures and tables (all produced inside Test4/)

| | Content | Fixes over Test2/3 |
|---|---|---|
| **Teaser** | ParaView image (the overview with the full-resolution box inside it) + the box spec + a provenance excerpt; caption: "issued on the overview already loaded in ParaView (§4.2)" | spec printed = spec run (`timesteps(src, 9000, 45000)`) |
| **Fig. speed** | (a) front position vs time: grey = full resolution every timestep (reference), blue = Sieve overview, orange rings = every 5th file. (b) front speed, **one estimator for every curve**, each value drawn over the interval it measures: the every-5th speeds as horizontal bars spanning 5,000 steps | the old rings sat off the blue line because they used a different estimator (5,000-step differences vs a ±4,000-step fit) and were computed from stride-8 data. Now every curve uses one estimator, the rings come from the full-resolution files the two-pass route would actually download, and in (a) they lie on the grey reference by construction |
| **Fig. evidence** | front speed; u ahead of the front by height × time (levels ≥ 1); u at 1–5 m, burning strip vs flanks | level 0 dropped; window 9000–45000; phase markers 1–3 |
| **Table 1** | routes × {timesteps seen, GB over the network (overview / detail / total), × Sieve, lines of code, server time} | the exact-equivalent script; one footnote: VTK XML files cannot be read in part, so every route reads whole files (read cost: §4.3) |

## 6. Test4 runs

| Run | Measures | Reads on gpu-server |
|---|---|---|
| `sieve_cold` | overview + box specs (as printed): wire bytes, estimates, server reduce time, server reads | ~97 + ~41 GB |
| `sieve_warm` | revisit, 0 B (for the record; not claimed in CS2) | 0 |
| `script_equivalent` | server: same cuts, compressed arrays, positions once if identical (hash); `scp`; local: rebuild `.vts` + `.pvd`. Bytes, server time, lines | ~97 + ~41 GB |
| `two_pass` / `download_all` | byte counts by `stat` of the files each copies (not run end to end) | 0 |
| `eval_fullres` | for all 88 timesteps at full resolution: front (level 1, theta ≥ 400 K, \|y\| ≤ 50 m); stride sweep 16/8/4/2 from the same read; u by level × distance ahead (0–10, 10–30, 30–60, 60–120 m) × strip/flanks; ambient wind at 10 m upwind | ~97 GB |

Rules: Test4/ is standalone (specs, scripts, data, results, figures, README with the
commands to reproduce each run). Shared code is copied in, not imported from Test2/.
Wall-clock time is reported only as server-side time (`remote_exec`) and labelled as
read + reduce + write on the server.

## 7. Open, not blocking

- dt (seconds per step), and whether level 0 is the ground or the first cell centre: ask the run's authors.
- Final wording of the teaser caption and the provenance excerpt: after Test4.

## 8. Revisions after Test4 ran (2026-11-07)

- **Front definition stays at grid level 0** (not level 1 as planned in §2): the overview
  keeps only levels 0 and 8, so a level-1 front could not be formed from it. Temperature at
  the lowest level locates the fire whatever that level's exact height; the wind claims use
  levels 1–4 (1.25–5.25 m over the box) and name level 0 only as "the lowest grid level".
- **Speed estimator (§5):** a single estimator cannot serve both samplings without
  misleading. The every-timestep curves use the slope of a line over ±2,000 steps; the
  every-5th speeds are drawn as bars across the 5,000 steps each averages. The position
  panel carries the alignment: the every-5th rings lie on the full-resolution line.
- **The lowest-level indraft is real** (§2, C5). Within 10 m of the front the lowest grid
  level turns negative from #19000, during the slowdown, while 1.25–5.25 m stays forward.
  The overview keeps level 0, so it shows the hint; only the full-resolution box shows the
  reversal is confined to that level. This replaces the draft's "near zero, fluctuations"
  paragraph.
- **The answer, re-measured (full resolution, burning strip):** the slowdown
  (#17000–#22000; 21.8 → 8.4 → 2.7 m per 1000 steps) coincides with the 1.25–5.25 m air
  within 60 m ahead *weakening* (+8.9 m/s at #16000 to +1.8 at #22000), not reversing (first
  negative #34000). Farther ahead (60–120 m) it reaches zero at #21000; aloft (13.8–18.2 m)
  it reverses from #22000. Lee ground #21000–#45000: strip negative 25/25, flanks 0/25.
- **Dropped:** the ambient-wind comparison with Dupuy et al. (the near-ground flow at the
  upwind edge is ~0 m/s, with 14–18 m/s aloft; not comparable to their 10 m inflow).
- **Caveat found:** before the fire (#1000) the lee slope's near-ground flow is already
  reversed across all y (terrain); by #9000 it is near zero. The paper must not call the
  reversal fire-made; it can say it is confined to the burning strip after the crossing.
- **Sieve read whole files on the server** in the first run (95.5 GB for the overview vs
  the script's 70.9 GB; estimate 54.0 GB). Cause: the VTK adapter read with `pv.read`
  (every array). **Fixed in `ba71991`** (only the requested arrays, one read per request,
  header-only inspect; the estimate now prices the grid positions). Rerun: overview read
  70.89 GB (estimate 70.86) in 567 s, box 27.09 GB (estimate 27.08) in 237 s; the script
  route reads the same bytes in 505 s and 213 s. Outputs still bit-identical to the
  script's. First run kept under `results/*_wholefile_reader/`.
