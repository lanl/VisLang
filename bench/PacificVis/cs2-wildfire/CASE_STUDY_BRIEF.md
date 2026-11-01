# Case Study 2 (wildfire, temporal subsetting): what was tested and what the results are

Factual record of the testing behind Case Study 2 of the Sieve/VisLang PacificVis 2027 paper.
All paths are relative to `/Users/agoosh11/Projects/VisLang/bench/PacificVis/cs2-wildfire/`
unless they start with `.vislang/` (relative to the repo root `/Users/agoosh11/Projects/VisLang/`).
Every number below is copied from a file listed next to it. **Only bytes are reported;
wall-clock times were not measured for reporting** (the link to the server runs over a VPN).
"MB/MiB" in Sieve's own outputs means MiB (2^20 B); "GB" in the tables means 10^9 B.

---

## 1. The scientific question and the data

**Question:** In this simulation, why does the fire slow sharply once it crosses the ridge crest?
Is the near-ground wind on the lee slope reversing and opposing the spread?

**Data** (on `gpu-server`, read over ssh; `Test2/data/stat_series.tsv`, `Test2/results/step0/README.md`):
- FIRETEC run `mountain_headcurve80`: a fire spreading over a ridge, a time series of
  88 files `fire#N.vts` (VTK XML structured grid): #1000–#5000 and #8000–#91000 every 1000
  steps, #50000 absent.
- Every file is 1,098,002,905 B; the series is 96,624,255,640 B (96.62 GB).
- Grid: 600 × 500 × 61 points, curvilinear and terrain-following (every point's position is
  stored, float64). The ridge is uniform along y; its crest is at x ≈ −2 m. The wind blows
  in +x (the fire spreads from x < 0 upslope, over the crest, and down the lee slope).
- Nine variables per file. The study uses u, v, w (wind, m/s), theta (potential temperature, K)
  and rhof_1 (fuel density left).
- `.vts` (VTK XML) cannot be read in part: any reader, Sieve included, reads whole files.

---

## 2. The three test runs

| Run | What it is | Status |
|---|---|---|
| `Test1/` | Early trials before the study design was fixed | Kept as a record only; not used for numbers |
| `Test2/` | First full run of the study (overview, window, Detail, analysis, baselines, replay, figures) | Complete. **The science, figures and baselines come from here** |
| `Test3/` | The same study rerun after one Sieve change (below) | Complete. **The byte numbers for Sieve come from here** |

**The change between Test2 and Test3** (Sieve commit `1e9155b`): a grid whose point positions
do not change between timesteps now sends those positions over the wire once per series instead
of once per timestep (matched by content hash, never assumed), and the remote result is compressed.
**The data that arrives is unchanged:** every output's `data_sha256` in Test3 equals Test2's
(overview 88/88 timesteps, Detail 38/38). So Test2's analysis, numbers and figures hold for Test3.

Test3 overview: run log reports "87 piece(s) share positions already in the bundle — sent once";
Detail: "37 piece(s) …" (`Test3/README.md`).

---

## 3. The workflow that was tested (what the user did, in ParaView)

Sieve was used through its ParaView plugin (Sieve Source = overview, Sieve Detail = box at full
resolution). The plugin writes each request as a Sieve spec; the specs below are as Sieve ran them.

### Step 0: pre-flight (metadata only, nothing moved), `Test2/results/step0/`
`sieve estimate` priced the overview at strides 8, 6 and 4 (all 88 timesteps, 5 fields):

| stride | points per timestep | priced over the wire (MiB) | vs one whole file (1,047.1 MiB) |
|---|---|---|---|
| 8 | 75 × 63 × 8 | ~102 | ~0.10× |
| 6 | 100 × 84 × 11 | ~248 | ~0.24× |
| 4 | 150 × 125 × 16 | ~806 | ~0.77× |

Stride 8 was chosen.

### Step 1: overview of every timestep, `Test3/results/step1/` (Test2: `Test2/results/step1/`)
Spec (`Test3/results/step1/overview_spec_as_run.py`):
```python
src = source('ssh://gpu-server//…/wildfire-sl/mountain_headcurve80/')
view = fields(src, ['rhof_1', 'theta', 'u', 'v', 'w'])
view = subsample(view, 8)
save(view, '…/004-source-overview/out.vts')
```
All 88 timesteps, stride 8, five fields. No hold by the budget gate.

**Is stride 8 enough to find the front?** (`Test2/results/step1/stride_check.txt`, comparing with
`Test2/data/fire_roi_bounds.csv`, the full-resolution bounding box of theta > 400 K per timestep):
- Fire found at all 83 timesteps that have fire at full resolution; none where there is none.
- Downwind edge of the fire: median 4.8 m behind full resolution (mean 7.8, max 37.9 m), within
  one stride (16 m) at 84% of timesteps; correlation 0.996 over 390 m of travel.
- Crest crossing of that downwind edge (any y): #16923 at full resolution, #17993 at stride 8.

### Step 2: choosing the time window from the overview only, `Test2/results/step2/`
Scripts: `Test2/scripts/cs2_front.py` → `Test2/data/front_s8.csv`; `Test2/scripts/cs2_window.py`
→ `window.txt`, `window_plot.{png,pdf}`. Nothing moved.

Definitions: front = largest x where theta crosses 400 K at the lowest grid level, within |y| ≤ 50 m,
interpolated between grid points. Wind ahead = mean u at that level from the front to 60 m downwind.
Speed (every timestep) = line fit over ±4000 steps; (every 5th file) = difference of consecutive samples.
Units: m per 1000 steps.

Comparison of the Sieve overview (every timestep) with what a scientist sampling every 5th file sees
(`Test2/results/step2/window.txt`):

| | every timestep (88 files) | every 5th file (18 files) |
|---|---|---|
| crest crossing (front x = −2 m) | #18020 | #18049 |
| speed, windward #8000–#17000 | 19.0 (10 pts) | 19.4 (2 samples) |
| speed, lee #20000–#45000 | 2.8 (26 pts) | 3.0 (5 samples) |
| speed, lee #45000–#91000 | 1.8 (46 pts) | 2.0 (9 samples) |
| wind ahead at the lowest level first negative after the windward run | #19000 | #23000 |

- Every 5th file gives the crossing time and average speeds about as well.
- The transition is not resolved by every 5th file: the speed falls from ~21 to ~3 m/1000 steps
  between #17000 and #22000, and the wind ahead peaks at +2.35 m/s at #16000 and turns negative
  by #19000, all inside one gap of the every-5th sampling (#18000 → #23000).
- Chosen window: **#9000–#45000** (37 timesteps, 7 of them every-5th files), plus **#4000**
  (before ignition) as a control. 38 timesteps total.

### Step 3: the Detail, full resolution in a box, `Test3/results/step3/` (Test2: `Test2/results/step3/`)
Spec (`Test3/results/step3/detail_spec_as_run.py`):
```python
src = source('ssh://gpu-server//…/wildfire-sl/mountain_headcurve80/')
view = timesteps(src, [4000, 9000, 10000, …, 45000])      # 38 labels, listed in full in the file
view = fields(view, ['theta', 'u', 'v', 'w'])
view = region(view, x=(-100.0, 200.0), y=(-120.0, 120.0), z=(0.0, 350.0), coords='world')
save(view, '…/005-detail-detail/out.vts')
```
- Box: x −100…200 m, y −120…120 m, z 0…350 m (world coordinates, drawn with ParaView's box widget
  on the overview). Stride 1, four fields. No hold.
- Cut per timestep: 151 × 121 × 39 points. Near the ground the Detail has 8 levels below ~11.5 m;
  the stride-8 overview has 2 there (level 0 and ~11.5 m). Level heights: `Test2/data/levels.csv`.
- Before the run, Sieve priced the box using the positions cached from the overview (so the
  terrain-following box is sized on the real geometry).

### Step 4: full-resolution analysis, `Test2/results/step4/`
Scripts: `Test2/scripts/cs2_leeslope.py` → `Test2/data/leeslope_full.csv`, `Test2/data/levels.csv`;
`Test2/scripts/cs2_evidence_plot.py` → `evidence.txt`, `evidence_plot.{png,pdf}`.
Definitions: as in step 2; "u ahead" per level, from level 1 up (level 0 is the surface layer,
where u is near zero at every timestep). "Reversed layer" = depth of negative u ahead of the front.
Results in section 6.

### Step 5: baselines, `Test2/results/step5/README.md`, scripts in `Test2/baseline/`
Three routes a scientist without Sieve could take, with real scripts:

| script | what it does | lines* |
|---|---|---|
| `pass1_download.sh` | rsync every 5th file of the series whole (18 files) | 9 |
| `pass1_overview.py` | read each whole file with VTK; front + wind ahead | 29 |
| `pass2_download.sh` | rsync #4000 and #9000–#45000, skipping the files pass 1 holds (31 files) | 9 |
| `pass2_cut.py` | cut the same world box, keep u v w theta, write .vts | 42 |

*non-blank, non-comment lines. The analysis script (`scripts/cs2_front.py`, 74 lines) is shared
by every route and not counted against any.

How they were checked (on gpu-server, files read in place):
- `pass1_overview.py` on #13000, #18000, #30000 gives the same fronts as the full-resolution Detail
  at #18000 and #30000 (`Test2/results/step5_baseline_check_server.txt`).
- `pass2_cut.py` on #30000: 151 × 121 × 39 points, and the sha256 of u, v, w, theta and the points
  are identical to Sieve's Detail at #30000 (`Test2/results/step5/step5_sieve_detail_30000_hashes.txt`).
  Its first version cut the wrong block: the source files number x indices from 251 (extent
  251–850) and VTK's cut uses that numbering. Fixed by offsetting by the file's extent.
- With rsync stubbed to print, pass 1 selects exactly 18 files (`Test2/results/step5/pass1_would_copy.txt`)
  and pass 2 the remaining 31 (`pass2_would_copy.txt`; 38 window timesteps − 7 already held).
- `pass2_cut.py` over all 38 window files on gpu-server: 38 cuts totalling 606,297,192 B
  (`Test3/data/server_cut_sizes.txt`), which is what the "work on the server" route copies back.
- Download byte counts are `stat` totals of the files each route copies; the full downloads were
  not run.

### Step 6: replay, `Test3/results/step6/` (Test2: `Test2/results/step6/`)
`bench/replay_session.py` re-ran the recorded ParaView session's specs, each setup with its own cache:

| setup | step | over the wire | pieces from the cache | reproduces the session's output |
|---|---|---|---|---|
| cold (empty cache) | overview | 47,138,910 B | 0 / 440 | yes (data_sha256 43eecfabd13bb8a5) |
| cold | Detail | 364,936,204 B | 0 / 152 | yes (data_sha256 98fe2e84e8de179c) |
| warm (cold's cache, a revisit) | overview | 0 B | 440 / 440 | yes |
| warm | Detail | 0 B | 152 / 152 | yes |

- Cold moves exactly the session's bytes; both outputs reproduce bit for bit.
- The two hashes equal Test2's replay: the Test3 change altered what crosses the wire, not what arrives.
- Warm: nothing crosses, no remote job runs, nothing is read on gpu-server.

### Step 7: specs and provenance, `Test2/results/step7/`
- `spec_listing.py`: the two specs side by side (paths shortened).
- `spec_overview_as_run.py`, `spec_detail_as_run.py`: as Sieve ran them (Test2; Test3's are in
  `Test3/results/step1/` and `step3/`, identical except output paths and field order).
- `provenance_detail_30000.yaml`: the provenance record (`sieve-provenance/2`) carried inside one
  Detail output: spec, input fingerprint, output `data_sha256`, Sieve build, explanation.

---

## 4. Bytes: Sieve (Test3) and Test2 → Test3

Sources: `Test3/results/step1/overview_result.json`, `Test3/results/step3/detail_result.json`,
the `*_timings.jsonl` beside them, `Test3/README.md`.

| | Test2 | Test3 |
|---|---|---|
| overview over the wire | 146,495,298 B | **47,138,910 B** |
| Detail over the wire | 1,083,152,914 B | **364,936,204 B** |
| Sieve total over the wire | 1,229,648,212 B | **412,075,114 B** (3.0× less than Test2) |
| read on gpu-server (overview 96.62 GB + Detail 41.72 GB) | 138,348,366,030 B | same |
| bandwidth probe before each fetch (not in "over the wire") | 2 × 4,194,304 B | same |

- Test3 overview for all 88 timesteps = 0.043 of one whole file (1/23).
- In Test2, grid positions (float64, sent every timestep) were 60% of the Detail's bytes.

**Sieve's price before moving data vs what moved** (Test3; `Test3/results/step6/summarize_cold.txt`):

| | priced | moved | |
|---|---|---|---|
| overview | 101.5 MiB | 45.0 MiB | +126%: no positions cached yet, so priced per timestep (an upper bound) |
| Detail | 453.7 MiB | 348.0 MiB | +30%: compression is not priced |

Both estimates erred high. (Test2: overview 101.5 predicted / 139.7 moved, −27%, positions assumed
float32 but stored float64; Detail 1,090.6 / 1,033.0, +5.6%.)

**Budget gate** (Test2, `Test2/results/step3/README.md`, "Asides"): during the session the Source
was accidentally switched to u v w theta at stride 1 over the 38 timesteps, i.e. the whole grid at
full resolution. Sieve priced it at 18,569 MiB and held it over budget before anything moved; it was
not approved. Under Sieve's earlier pricing (whole grid), the Detail box itself would have been priced
at ~11 GB and held; with pricing on the overview's cached positions it was priced at 1,090.6 MiB.

---

## 5. Cost comparison with the baselines (Test3)

Source: `Test3/results/cost_table.csv` / `.md`, built by `Test3/scripts/cost_table.py` from the
recorded files. LaTeX version: `Test3/results/cost_table.tex` (booktabs + makecell), rendered
preview `Test3/results/cost_table_preview.pdf`.

| Route | Pass 1 (look over the series): timesteps seen | Pass 1 GB moved | Pass 2 (window, full res.) GB moved | Total GB moved | × Sieve's bytes | Lines of code |
|---|---|---|---|---|---|---|
| **Sieve** (ParaView) | **88** | 0.05 (47,138,910 B) | 0.36 (364,936,204 B) | **0.41** (412,075,114 B) | 1× | 0 |
| Two-pass download | 18 | 19.76 | 34.04 | 53.80 (53,802,142,345 B) | 130.6× | 89 (38 + 51) |
| Download everything | 88 | 96.62 | 0 | 96.62 (96,624,255,640 B) | 234.5× | 73 |
| Work on the server | 88 | 0 | 0.61 | 0.61 (606,297,192 B) | 1.5× | 72 |

- × Sieve's bytes = route's total ÷ 412,075,114 B.
- Two-pass: pass 1 copies every 5th file whole (#1000, #8000, #13000, … #89000; 18 files); pass 2
  copies the 31 window files not already held, then cuts the box locally.
- Download everything: all 88 files whole; nothing more needed for pass 2.
- Work on the server: the scientist logs in, runs the scripts on gpu-server over all 88 files and
  the 38 window files, and copies back only the 38 cuts.
- Pass 1 comparison: two-pass moves 19.76 GB and sees 18 timesteps; Sieve's overview moves 0.047 GB
  and sees 88 (419× fewer bytes).
- **Read on the server:** Sieve 138.35 GB (whole files: overview reads all 88, Detail the 38),
  two-pass 53.80 GB, download everything 96.62 GB, work on the server 138.35 GB. Sieve reads more on
  the server than the two-pass route.
- Sieve's specs (4 and 5 lines) were written by the ParaView plugin, not by the user.

---

## 6. Science results (Test2 data; identical in Test3)

Source: `Test2/results/step4/evidence.txt`, `Test2/results/step4/README.md`, `Test2/data/leeslope_full.csv`.

### 6.1 Front position and speed (overview, every timestep)
Windward ~19 m/1000 steps; crosses the crest at #18020; lee ~2.8 m/1000 steps (#20000–#45000) then
~1.8 (#45000–#91000). The sharp drop is between #17000 and #22000 (~21 → ~3 m/1000 steps).

### 6.2 Wind ahead of the front at full resolution (Detail)
Mean u over levels 1–4 (1.2–5.1 m above ground) from the front to 60 m downwind, |y| ≤ 50 m:

| timestep | front x (m) | u at 1–5 m (m/s) | reversed layer |
|---|---|---|---|
| #14000 | −82.5 | +7.23 | none |
| #16000 | −37.5 | +8.86 | none |
| #17000 | −14.5 | +7.42 | none |
| #18000 | 1.9 | +5.93 | none |
| #20000 | 17.4 | +3.00 | none |
| #22000 | 27.7 | +1.75 | none |
| #26000 | 40.2 | +1.49 | none |
| #30000 | 54.1 | +1.13 | none |
| #33000 | 63.7 | +0.42 | none |
| #34000 | 63.8 | −0.02 | 2.4 m |
| #37000 | 75.9 | −0.30 | 4.5 m |
| #38000 | 77.4 | −1.07 | 49.4 m |
| #40000 | 77.9 | −1.31 | 34.0 m |
| #42000 | 83.7 | −1.43 | 47.2 m |
| #43000 | 82.9 | −0.48 | 5.3 m |
| #45000 | 89.7 | −0.22 | 3.7 m |

(All 32 timesteps #14000–#45000 are in `evidence.txt`.)

Findings, as recorded:
1. **The sharp slowdown (#17000–#22000) happens without reversal.** The air 1–5 m up ahead of the
   front blows with the spread throughout, weakening from +8.9 m/s (#16000) to +5.9 (#18000, at the
   crest), +3.0 (#20000) and +1.8 (#22000).
2. **A reversed layer forms aloft first, then reaches the ground.** Ahead of the front, mean u at
   13.5–20.3 m first goes negative at #22000 (forward air below it at 1–8 m and above ~35 m). The 1–5 m
   mean first goes negative at #34000 (reversed layer 2.4 m deep), deepening to 2.8–4.5 m
   (#35000–#37000) and 33–49 m (#38000–#42000), then 3–5 m (#43000–#45000). This is ~16,000 steps after
   the crossing, during the slow lee phase.
3. **Concentrated where the fire burns.** On the same lee ground (x 100–160 m, levels 1–4),
   #21000–#45000: inside the burning strip (|y| ≤ 50 m) u is negative at 25/25 timesteps (mean
   −0.77 m/s); in the flanks (|y| ≥ 90 m) at 2/25 (mean +0.77 m/s). The ridge is uniform along y.
   #9000–#20000 (fire still windward): strip +0.63, flanks +0.27 m/s.
4. **Caveats recorded:**
   - At #4000 (before ignition) that lee ground is reversed in both strip and flanks (−4.23 / −2.77 m/s),
     i.e. a terrain-driven separation existed early, possibly while the flow was spinning up; gone by #9000.
   - There is no fire-free run of the later period to compare with.
   - Late in the window the flanks are near the fire's edge (its y extent reaches about ±80 m by #45000).
   - The analysis shows timing (the slowdown coincides with the loss of strong forward near-ground
     wind); it does not separate causes (downslope spread, terrain sheltering, the fire's own flow).

### 6.3 What the overview alone shows vs full resolution
The stride-8 overview keeps two near-ground levels (0 and ~11.5 m). Its lowest-level wind ahead of
the front turns negative from #19000 (`window.txt`), right after the crossing. At full resolution the
1–8 m air stays forward until #34000. Taken alone, the overview would suggest a reversal at the crossing
that the full-resolution data does not show.

### 6.4 Unreconciled detail (open)
`Test2/results/step2/README.md`, `window.txt` and fig_c label the lowest grid level "≈0.75 m above
ground"; `Test2/data/levels.csv` (and step 4) put level 0 at 0.00 m, measuring heights from the level-0
grid point. The two have not been reconciled; heights in section 6.2 follow `levels.csv`.
The crest-crossing time also depends on the front definition: #18020 (overview front, |y| ≤ 50 m) vs
#16923 / #17993 (downwind edge of the fire anywhere in y, full resolution / stride 8, step 1).

---

## 7. Figures

All in `Test2/figures/` (Test3's data is identical). PNG + PDF where listed.

| File | What it shows | Made by |
|---|---|---|
| `fig_a_overview_snapshots.png` | 2×2 overview renders at #9000, #18000, #30000, #45000: terrain coloured by fuel left (rhof_1, 0–0.6, green; burned = dark), fire as the theta = 400 K surface (orange), ridge crest as a white line; black background, same camera | `Test2/scripts/render_overview_snapshots.py` (pvbatch); singles `overview_<N>.png` |
| `fig_b_leeslope_streamlines.png` | 2×2: rows #22000 and #40000. Left: oblique view of the Detail box (white outline) on the overview terrain with streamlines inside it. Right: side view of streamlines on the y = 0 section with the fire surface. Streamlines coloured by u, −3…+3 m/s, blue = back toward the fire | `Test2/scripts/render_leeslope.py` (pvbatch); singles `leeslope_<N>_{oblique,side}.png` |
| `fig_c_front_speed_wind_every5.{png,pdf}` | Three panels vs timestep: front position x (crest dashed, crossing #18020), front speed, u ahead of the front at the lowest level. Blue line = every timestep (Sieve overview, 88 files); orange rings = every 5th file (two-pass, 18 files). Chosen window #9000–#45000 shaded | `Test2/scripts/cs2_window.py` (same as `results/step2/window_plot`) |
| `fig_d_leeslope_evidence.{png,pdf}` | Three panels, #4000–#45000: front speed (crest crossing dotted); heatmap of u ahead of the front by height (0–50 m) and time, blue = reversed, black contour u = 0 (starts ~#13000, when the front enters the Detail box at x = −100 m); u at 1–5 m on the lee slope x 100–160 m, burning strip (orange) vs flanks (blue) | `Test2/scripts/cs2_evidence_plot.py` (same as `results/step4/evidence_plot`) |
| `Test3/results/cost_table_preview.pdf` | The cost table of section 5, typeset | `Test3/results/cost_table.tex` |

**ParaView screenshots (working records, not final):**
- `Test2/figures/screenshot1_overview_working.png`: overview session, #91000.
- `Test2/figures/screenshot2_detail_working.png`: Detail box on the overview with the Detail panel;
  `screenshot2b_detail_running.png`: Fields/Stride/Status while fetching; `screenshot2c_detail_done.png`:
  Status after the 38 timesteps, the cut's terrain-following outline beside the drawn box.
- `Test3/figures/screenshot1_overview_working.png`, `screenshot2_detail_working.png`: Test3 session.
- The Status text in these screenshots predates two display fixes. Test2 screenshots show wall times
  and a "result" size that is Sieve's estimate. The Test3 Detail screenshot's "result 15.3 MB" is one
  timestep's file (the slider position), not the request (fixed afterwards: the request's written total
  is ~580 MB). The "Over the wire" line is correct in all of them (Test3 Detail: 348.0 MB = MiB).
  The other byte numbers in this brief do not come from the Status line.

---

## 8. Records and reproducibility

- ParaView sessions (each request's spec, holds, bytes, cache reuse, run id):
  Test2 `.vislang/paraview/20261031-013204-652ebb/`; Test3 `.vislang/paraview/20261031-175301-ba2a2a/`
  (copied: `Test3/results/session.json`, `session.jsonl`).
- Saved ParaView states: `Test2/paraview/cs2_test2_step1_overview_state.pvsm`,
  `Test3/paraview/cs2_test3_overview_start.pvsm`.
- Per-run READMEs: `Test2/README.md` (with "Reproduce" commands), `Test3/README.md`, and each
  `Test2/results/stepN/README.md`.
- Analysis data: `Test2/data/front_s8.csv` (per-timestep front and wind from the overview),
  `Test2/data/leeslope_full.csv` (per-timestep, per-level u ahead of the front / fixed lee ground /
  flanks from the Detail), `Test2/data/levels.csv` (level heights), `Test2/data/fire_roi_bounds.csv`
  (full-resolution fire bounding boxes), `Test2/data/stat_series.tsv` (file sizes),
  `Test3/data/server_cut_sizes.txt` (the 38 server-side cuts).
- Outputs (the .vts files) are in the session folders and `*/outputs/` (not committed).
- Test3 false starts (cancelled before data moved; not in any number) are listed in `Test3/README.md`,
  "On the way".

## 9. Sieve mechanisms the study exercised

- Overview of a remote time series with a stride, computed next to the data (only the strided cut
  crosses the wire).
- Detail: a world-coordinate box on a curvilinear terrain-following grid, at full resolution, over a
  chosen list of timesteps.
- Cost estimate before any data moves, and the budget gate that holds an over-budget request.
- Pricing a box on a curvilinear grid from the overview's cached point positions.
- Sending a static grid's positions once per series (Test3), detected by content.
- Extent catalog: a revisit reuses cached pieces (warm replay: 0 B).
- Provenance record in every output; session log and replay reproducing outputs bit for bit.
