# Case Study 2 (wildfire): temporal subsetting — Test2, the paper run (2026-10-31)

Question: why does the fire slow sharply once it crosses the ridge crest — is the
near-ground wind on the lee slope reversing and opposing the spread?
Data: `ssh://gpu-server//…/vislang/wildfire-sl/mountain_headcurve80/`, 88 timesteps
`fire#N.vts` (#1000–#5000, #8000–#91000, no #50000), each 1,098,002,905 bytes
(96,624,255,640 in all); 600 × 500 × 61 terrain-following grid, ridge crest at x ≈ 0.
**Only bytes are reported** — no wall times (the link runs over a VPN).
Every number below comes from a file in this folder; each step's README says which.

## What Sieve did (ParaView session `.vislang/paraview/20261031-013204-652ebb/`)
| step | request | over the wire | read on gpu-server |
|---|---|---|---|
| 1 overview | Sieve Source: all 88 timesteps, u v w theta rhof_1, stride 8 | 146,495,298 B | 96,624,255,640 B |
| 3 Detail | Sieve Detail: box x −100…200, y −120…120, z 0…350 m, stride 1, u v w theta, 38 timesteps (#4000, #9000–#45000) | 1,083,152,914 B | 41,724,110,390 B |
| | total | **1,229,648,212 B** | 138,348,366,030 B |
Plus a 4,194,304-byte bandwidth probe before each fetch. Specs as Sieve ran them:
`results/step7/spec_overview_as_run.py`, `spec_detail_as_run.py` (listing: `spec_listing.py`).
Provenance of one Detail output: `results/step7/provenance_detail_30000.yaml`.

## Cost table (`results/cost_table.md`, built by `scripts/cost_table.py`)
| route | timesteps seen in the first pass | over the wire | vs Sieve | lines of code written |
|---|---|---|---|---|
| Sieve | 88 | 1.23 GB | — | 0 (plugin writes 4 + 5 lines of spec) |
| two-pass download (18 + 31 whole files) | 18 | 53.80 GB | 43.8× | 89 |
| download everything (88 files) | 88 | 96.62 GB | 78.6× | 73 |
| work on the server, copy back the cuts | 88 | 0.61 GB | 0.5× | 72 |
- Overview alone: 0.146 GB for 88 timesteps = 0.133 of one file; the two-pass first
  pass costs 19.76 GB for 18 timesteps (135× the bytes, 1/4.9 the timesteps).
- Not a win, said plainly: Sieve read 138 GB on gpu-server (whole files — `.vts` cannot
  be read in part) against the two-pass route's 54 GB; and working on the server moves
  fewer bytes than Sieve's Detail (compressed `.vts`, positions once per file; Sieve's
  transfer is uncompressed and repeats the static grid positions every timestep — 60% of
  the Detail's bytes). Byte comparisons are against the download routes only.
- Sieve's price before moving data: overview predicted 101.5 MiB, moved 139.7 MiB
  (−27%: positions assumed float32, stored float64; none cached yet); Detail predicted
  1,090.6 MiB, moved 1,033.0 MiB (+5.6%, sized on the overview's cached positions).
  The budget gate held an accidental 18.6 GB request in the session (step 3 README).

## Science (results/step2, results/step4)
- Overview (stride 8, every timestep): windward front ~19 m/1000 steps, crosses the crest
  at #18020, lee ~2.8 then ~1.8 m/1000 steps. Every 5th file finds the crossing as well
  (#18049) and similar average speeds, but the whole transition falls inside one gap
  (#18000→#23000).
- Full resolution (Detail): the slowdown (#17000–#22000) is not a reversal — the air
  1–5 m up ahead of the front still blows with the spread, weakening from +8.9 to +1.8 m/s.
  Reversed flow forms aloft (13–20 m) from #22000 and reaches 1–5 m at #34000, 33–49 m
  deep by #38000–#42000. On the same lee ground it is confined to the burning strip
  (negative 25/25 timesteps) while the flanks blow forward (2/25 negative): mostly the
  fire's own circulation. Caveats: reversed lee flow in strip and flanks before ignition
  (#4000, possibly spin-up); no fire-free run; late flanks near the fire's edge.
- The overview alone would have suggested a reversal at the crossing: its lowest level is
  the surface layer (u ≈ 0 always) and stride 8 cannot see the 1–8 m air.

## Figures (`figures/`)
- `fig_a_overview_snapshots.png` — overview at #9000, #18000, #30000, #45000; fuel left,
  theta = 400 K, crest line (`scripts/render_overview_snapshots.py`, pvbatch).
- `fig_b_leeslope_streamlines.png` — the Detail box in context (oblique) and the y = 0
  streamline section (side), #22000 and #40000; blue = back toward the fire
  (`scripts/render_leeslope.py`, pvbatch).
- `fig_c_front_speed_wind_every5.{png,pdf}` — front position, speed, wind ahead; every
  timestep vs every 5th file; chosen window shaded (`scripts/cs2_window.py`).
- `fig_d_leeslope_evidence.{png,pdf}` — front speed; u ahead of the front by height and
  time; lee ground strip vs flanks (`scripts/cs2_evidence_plot.py`).
- `screenshot*_working.png` — ParaView working records (to be retaken for the paper once
  Status shows bytes only).

## Replay (results/step6)
`bench/replay_session.py --setups cold,warm` on the session (results/step6/README.md):
- Cold, from an empty cache: the same bytes as the session (146,495,298 + 1,083,152,914 B),
  and both outputs reproduce the session's bit for bit (data_sha256).
- Warm, a revisit on cold's cache: **0 bytes**, every piece from the catalog (440/440 and
  152/152), no remote job, nothing read on gpu-server; outputs again bit-identical.
- full-download not run; its bytes are the stat totals above.

## Reproduce
1. `bash` — stat: see `results/step0/README.md`; prices: `sieve estimate` on `results/step0/spec_stride*.py`.
2. ParaView: Sieve Source + Sieve Detail as in steps 1 and 3 (or replay the session:
   `python bench/replay_session.py .vislang/paraview/20261031-013204-652ebb --setups cold,warm`).
3. `python scripts/cs2_front.py <overview out> -o data/front_s8.csv`;
   `python scripts/stride_check.py data/front_s8.csv data/fire_roi_bounds.csv`;
   `python scripts/cs2_window.py data/front_s8.csv --out-dir results/step2`.
4. `python scripts/cs2_leeslope.py <detail out> -o data/leeslope_full.csv`;
   `python scripts/cs2_evidence_plot.py data/leeslope_full.csv data/levels.csv data/front_s8.csv --out-dir results/step4`;
   `pvbatch scripts/render_leeslope.py …`, `pvbatch scripts/render_overview_snapshots.py …`.
5. Baselines: `baseline/*.sh|py` (checks in `results/step5/README.md`);
   `python scripts/cost_table.py .`.

## Follow-ups found on the way
- Send a static grid's positions once per series, and compress the transfer (the Detail's
  bytes would fall from 1.08 to ~0.47 GB before compression).
- A Detail's Fields should default to its Source's (step 3: a Detail fetched all nine).
- Status should be able to show bytes only (screenshots).
- Provenance of a remote world box names `resolved.region_cells` but does not carry it.
- `sieve estimate` treats a remote folder as a series only when the spec has `timesteps()`.
