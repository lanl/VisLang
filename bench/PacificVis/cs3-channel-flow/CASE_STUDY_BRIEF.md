# Case Study 3 (turbulent channel flow, reverse flow at the wall): what was tested and what the results are

Factual record of the testing behind Case Study 3 (Section 4.3) of the Sieve/VisLang PacificVis 2027
paper.
- **Paths:** relative to `/Users/agoosh11/Projects/VisLang/bench/PacificVis/cs3-channel-flow/`,
  unless they start with `.vislang/` (repo root) or are Sieve source paths (repo root).
- **Sources:** every number below is copied from a file listed next to it.
- **What is reported** for each Sieve request (the case's reporting rules):
  - estimated bytes, read on the server and over the wire;
  - actual bytes: read on the server (the executor's Linux `read_bytes`) and over the wire;
  - the remote reduce time (`server_s`, the job's own clock on the server).
- **What is not reported:** transfer times, ssh round trips, `launch_s` and `total_s`. They depend
  on the VPN link.
- **Units:** "MB/GB" in Sieve's own outputs are MiB/GiB (2^20, 2^30 B). In this brief "MB/GB" are
  10^6/10^9 B and exact byte counts are given.
- **Design and literature:** the design (claims, definitions fixed before measuring) is
  `DESIGN.md`. What the cited papers claim is in `LITERATURE.md`.

---

## 1. The scientific question and the data

**Question** (asked before by Lenaers et al. 2012 at Re_tau 1000 and Cardesa et al. 2019 at Re_tau
up to 2003; answered here at Re_tau 5186):
- How often does the flow next to the wall of a turbulent channel reverse, and how far from the
  wall does it reach?
- How large and how numerous are the regions of reverse flow?
- Do they sit beneath large-scale high-speed or low-speed motions further from the wall?

**Data** (`Test1/results/step0/README.md`):
- **Source:** one snapshot of the streamwise velocity u from the JHTDB channel flow at Re_tau 5186
  (Lee & Moser 2015, doi:10.1017/jfm.2015.268). Obtained from the Open SciVis Datasets as
  `dns_10240x7680x1536_float64.raw`.
- **Size and location:** 966,367,641,600 B (966.4 GB) on `gpu-server` (NFS v4.2), read over ssh.
- **Layout:** headerless; x (streamwise) fastest, then the spanwise direction, then the wall-normal
  direction (1536 points, stretched).
- **Name:** a symlink `u_10240x7680x1536_float64.raw` names the variable `u` for Sieve's raw reader.
- **Checked on the data** (step 0):
  - the 1536 axis is wall-normal and the quantity is u in units of the bulk velocity;
  - at z = 8 the JHTDB grid gives y+ = 2.74 and the measured mean gives U+ = 2.75 (viscous
    sublayer).
- **Grid:**
  - block row 0 (z 1-16) spans y+ 0.07-8.12;
  - cell sizes are Delta x+ = 12.7 and Delta z+ = 6.4 (spanwise).
- **Server:** 48 cores, 503 GB RAM, no Slurm. A cold sequential read ran at 148 MB/s in the probe.

---

## 2. The test run

One run, `Test1/`. Each step has a folder `Test1/results/stepN/` with:
- `README.md`;
- the spec as run (`spec_as_run.py`);
- the dry-run estimate (`estimate.txt`);
- the run report (`run_report.txt`);
- the run's line from `.vislang/timings.jsonl` (`timing_record.json`) and the reported numbers
  copied from it (`numbers.json`, by `Test1/scripts/record_step.py`);
- the server's page cache for the file before the run, where it matters (`page_cache_before.txt`);
- a ParaView check of each output (`paraview_check*.txt`, pvpython 6.1.0-RC2).

Outputs are in `Test1/outputs/` (not in git).

**The requests** (the source is `src = source("ssh://gpu-server//…/u_10240x7680x1536_float64.raw")`;
`inner = region(src, z=(1, 1535))` leaves out the two wall planes):

| Step | Request | Output |
|---|---|---|
| 1 | `subsample(inner, 16)`: stride overview of the whole channel | `s1_stride16.vti` (640 x 480 x 96) |
| 2 | `region(src, x=(4608, 5632), y=(3328, 4352), z=(1, 65))`: ground-truth area at full resolution | `s2_truth.vti` (1024 x 1024 x 64) |
| 3 | `coarsen(<step 2's box>, 16, how="mean")` and `how="min"` | `s3_area_{mean,min}16.vti` (64 x 64 x 4) |
| 3b | `coarsen(region(src, z=(81, 82)), x=16, y=16, how="mean")`, and z = (1454, 1455): the large-scale plane at y+ 100 per wall | `s3b_{lower,upper}_yp100.vti` (640 x 480) |
| 4 | `low = coarsen(inner, 16, how="min")`: block-minimum overview of the whole channel | `s4_min16.vti` (640 x 480 x 96) |
| 5 | `threshold(low, "u < 0")`: the blocks that hold reverse flow | `s5_backflow.vti` |
| 6 | Step 4's spec recovered from `s4_min16.vti` by `provenance` and re-run; the same `low` also saved as netCDF-4 | `s6_replay_min16.vti`, `s6_min16.nc` |
| 7 (post hoc) | as 3b at y+ 281 (z = 147 / 1388) and y+ 519 (z = 207 / 1328) | `s7_{lower,upper}_yp{281,519}.vti` |

Each spec is 4 or 5 lines; step 4's is:
```python
src = source("ssh://gpu-server//mnt/na1/…/TurbulentChannelFLow/u_10240x7680x1536_float64.raw")
inner = region(src, z=(1, 1535))
low = coarsen(inner, 16, how="min")
save(low, "…/Test1/outputs/s4_min16.vti")
```

Step 7 was added after step 4's large-scale result had been seen. It is labelled post hoc
everywhere.

---

## 3. Bytes and server time, every request

Sources: each step's `numbers.json` (step 3b and step 7: per plane, `README.md` and
`per_request.json`), copied from `timing_record.json`.

| Step | Est. read on the server | Measured read on the server | Est. over the wire | Measured over the wire | `server_s` |
|---|---|---|---|---|---|
| 1 stride overview | 3,769,344,000 B | 18,094,653,440 B (4.80x) | 235,929,600 B | 235,930,686 B | 376.36 s |
| 2 ground-truth area | 536,870,912 B | 5,259,358,208 B (9.80x; partly warm) | 536,870,912 B | 536,871,998 B | 60.93 s |
| 3 block mean, block min of step 2 | **0** | **0** (no remote job) | **0** | **0** | none |
| 3b plane y+ 100, lower | 629,145,600 B | 440,668,160 B (partly warm) | 2,457,600 B | (both) 4,917,372 B | 9.17 s |
| 3b plane y+ 100, upper | 629,145,600 B | 629,407,744 B (+0.04 %, cold) | 2,457,600 B | | 6.21 s |
| 4 block-minimum overview | 965,109,350,400 B (898.8 GiB; **held** over the 100 GiB read budget, then approved) | 943,219,060,736 B (41.6 GB already in the page cache) | 235,929,600 B | 235,930,686 B | 10,344.78 s |
| 5 threshold of step 4 | **0** | **0** (no remote job) | **0** | **0** | none |
| 6 replay of step 4 + netCDF copy | **0** each | **0** (no remote job) | **0** | **0** | none |
| 7 planes y+ 281, 519 (4) | 629,145,600 B each | 631,234,560 / 0 / 629,407,744 / 0 B (upper planes still cached from step 4) | 2,457,600 B each | 9,834,744 B (all four) | 5.88 / 1.33 / 5.57 / 1.28 s |
| **Total** | | **968,903,790,592 B** | | **1,023,485,486 B** | **10,811.51 s** |

- **Measured over the wire** is the run counter `wire_bytes`. Each pull adds 1,086 B for the
  grid's placement.
- **The bandwidth probe** before each fetch (4,194,304 B) is not counted in "over the wire".
- **Steps 3, 5 and 6** ran no remote job. The only ssh commands were metadata queries (`ssh_query`).

---

## 4. Claim 1: cost estimation is an analysis of the request

**What the estimate looks at** (`Test1/results/step0/estimates/`, each step's `estimate.txt`):
- **The operator.**
  - A stride on a raw file reads whole x-rows: 46,080 rows of 10,225 cells for step 1.
  - A block reduction reads every cell of the region. Step 4's note says "coarsen reads all
    120,638,668,800 cells of the region per timestep and keeps 29,491,200 (one per block)".
- **The bytes it returns:** steps 1 and 4 return the same 225 MiB and read 3.5 GiB and 898.8 GiB on
  the server.
- **The budget:** step 4 is held over the server-read budget (100 GiB by default) before anything
  runs, although its result is small. It ran after approval (`Test1/results/step4/held_report.txt`).
- **The cache:** before step 5 runs, its estimate is 0 B read and 0 B moved, "served from the
  extent cache (a cached result of its start, with threshold u < 0.0 applied here)". Before step 4
  had run, the same request was priced at 898.8 GiB.

**Estimate vs measured read on the server** (the kernel's `read_bytes` for the reducer process):

| Access pattern | Step | Measured / estimated |
|---|---|---|
| Contiguous plane, cold | 3b upper, 7 lower (2 planes) | 1.0004x, 1.0033x, 1.0004x |
| Whole file, sequential (41.6 GB cached before) | 4 | 0.977x measured; 1.020x with the cached pages added |
| The same planes, cold (the block-minimum script, not Sieve; section 7) | baseline | 1.0001x |
| Strided rows (every 16th 80 KiB row) | 1 | 4.80x |
| Full-resolution box (65,536 pieces of 8 KiB) | 2 | 9.80x (partly warm) |

- The estimate counts the bytes Sieve's reader requests.
- The NFS client reads ahead of every request, which costs most where requests are many and small.
  Step 0's probe measured 4.7x for strided rows outside Sieve (`step0/read_rate.txt`).
- The estimate is exact for contiguous reads and a lower bound for scattered ones. **The factor is
  not modelled.**

**Estimate vs measured over the wire:** every pull equals its estimate plus 1,086 B of placement.

---

## 5. Claim 2: reuse of cached results beyond exact match

Sieve's extent cache keeps the result of every remote request. A new request is served from it
without contacting the data:
- **Exact match** (shown in CS2): step 6's replay and its netCDF copy (route `catalog_full_hit`).
- **Derived from a cached start** (new for CS3, Sieve commit `2544612`): the cached chain must be the
  new request's own written start, form for form, and only `threshold`/`coarsen` may follow.
  - **Step 3:** step 2's cached cut plus `coarsen(…, 16, "mean")` and `"min"`. The run report says
    "derived from a cached extent of its start (1 form(s)); applied here: coarsen 16 — nothing read
    next to the data".
  - **Step 5:** step 4's cached block minima plus `threshold(u < 0)` (route `catalog_derived`,
    `cached_bytes` 235,929,600).
- **Order is part of the key.** A threshold before a block minimum is a different answer and is not
  served from step 4's cache. Tests: `tests/test_cache_derive.py` (69 checks).
- **Correctness of the derived results:**
  - step 3's block minima equal a local block minimum of step 2's array exactly; the block means
    differ by at most 1.1e-15 (`Test1/results/eval/area_eval.txt`);
  - step 4's minima over the area equal step 3's exactly (`Test1/results/step4/README.md`);
  - step 5 equals a fresh threshold of step 4 (`Test1/results/step5/census.txt`).

---

## 6. Claim 3: the output records what it is (provenance)

`provenance(s4_min16.vti)` (`Test1/results/step6/README.md`):
- **The spec:** returned exactly as run; `spec_only=True` gives `recovered_spec.py`.
- **The input:** path, size, modification time, sha256 of the first 64 KiB.
- **The output:** `data_sha256` `59228478a3bbf986`.
- **The placement:** origin (7.5, 7.5, 8.5), spacing 16, "source cells".
- **The explanation:** "Values: block minima of the source cells (computed, not source values)".
  Without it, a block-minimum field opened later would be misread as pointwise u, since it is
  biased low.
- **The replay:** re-running the recovered spec (output renamed) was served from the cache. It
  produced the same array and the same `data_sha256`.
- **The netCDF-4 copy:** the same data and the same `data_sha256`, with the record in its
  attributes.
- **Recorded discrepancy:** the record's Sieve commit (`03052c0`) differs from the run record's
  (`f765b3f`). The former was committed during the run and changed only `DESIGN.md`.

---

## 7. Baselines (without Sieve)

Scripts: `Test1/baseline/` (non-blank, non-comment lines):
- `stride.py` (6): every 16th cell, via a NumPy memmap;
- `blockmin.py` (11): block minima, 16 planes at a time;
- run on the server by `run_all.sh` through `run_measured.py`, which records `server_s` and the
  process's `read_bytes` but is not counted.

Before each script the file's pages were evicted from the page cache (`posix_fadvise(DONTNEED)`).
`fincore` before and after: 326.6 GiB, then 0 B (stride); 16.9 GiB, then 0 B (block minimum). Both
baselines started cold.

Results: `Test1/results/baseline/` (README, `stride.json`, `blockmin.json`, `equality.txt`).

| Route | Lines | Read on the server | `server_s` | Copied to the scientist | Equal to Sieve |
|---|---|---|---|---|---|
| Download the file | n/a | 966,367,641,600 B | n/a | **966,367,641,600 B** (`stat`) | n/a |
| Script on the server: stride | 6 | 18,119,434,240 B | 304.95 s | 235,929,728 B (`.npy`) | yes: sha256 of the array equals `s1_stride16.vti`'s |
| Script on the server: block minimum | 11 | 965,194,276,864 B | 6,836.04 s | 235,929,728 B (`.npy`) | yes: sha256 of the array equals `s4_min16.vti`'s |
| Sieve: stride (step 1) | 4 (spec) | 18,094,653,440 B | 376.36 s | 235,930,686 B | (reference) |
| Sieve: block minimum (step 4) | 4 (spec) | 943,219,060,736 B (41.6 GB already cached) | 10,344.78 s | 235,930,686 B | (reference) |

- **Sieve's estimate against a cold pass.** Sieve estimated step 4's read at 965,109,350,400 B. The
  block-minimum script read the same planes from a cold cache: 965,194,276,864 B (+0.009 %).
- **The scripts on the server are faster than Sieve.**
  - Stride: 304.95 s against 376.36 s, at the same bytes.
  - Block minimum: 6,836.04 s against 10,344.78 s (about 141 against 91 MB/s over the pass).
- **The cause, measured on one slab** (`Test1/results/baseline/decompose.jsonl`). One slab of 16
  planes (z 753-768) was read cold, then block-reduced, in each way: two runs each, alternating, each
  in its own process.
  - **Reading is the same:** 10,066,591,744 B in 63-67 s either way.
  - **Reducing is not:** Sieve's `block_reduce` (`vislang/interpreter/narrowing.py`) took 16.0 s
    against 0.9-1.2 s for the script's two-step minimum, with equal results.
  - **Why:** `block_reduce` copies each slab twice, so that every block's cells are reduced in one
    fixed order (a mean then gives the same bits however the data arrived).
  - **How much of the gap:** over step 4's 96 slabs, the extra time is about 1,440 s, about 41 % of
    the 3,509 s gap.
    - The rest is not explained by this measurement.
    - The two full passes ran at different times on a shared server and file system.
  - **Without I/O, on a laptop** (`block_reduce_local.txt`): 3.70 s against 0.46 s on a quarter
    slab (8.1x).
- **Not changed for this paper.** A faster minimum and maximum (no fixed order is needed, since
  they are exact in any order) is a possible change to Sieve, not made here.
- **Where Sieve differs from the scripts:**
  - its spec states the request (4 lines; the plugin can write it);
  - the request is priced and held before it runs;
  - its results are cached and reused (steps 3, 5, 6 at 0 B);
  - its outputs carry provenance;
  - they open in ParaView at their place in the source.

  The scripts write a bare `.npy` in (z, y, x) order, with no placement and no record.

---

## 8. Science results

### 8.1 How often, and how far from the wall
Sources: `Test1/results/step1/stride_census.txt`, `Test1/results/eval/area_eval.txt`,
`Test1/results/step5/census.txt`.
- **At the wall plane** (z = 1, y+ 0.07), u < 0 in:
  - **0.0967 %** of the whole lower wall (stride, 297 of 307,200 samples; 95 % Wilson interval
    [0.0863, 0.1083] %);
  - **0.1265 %** of the ground-truth area (1,326 of 1,048,576 cells).

  Lenaers et al.: 0.06 % at Re_tau 1000 (negative wall shear stress). The plane at y+ 0.07 stands in
  for the wall.
- **Reach:** in the area, the highest level with u < 0 is **z = 18, y+ 9.67** (Lenaers: y+ ~ 8.5).
  The fraction falls from 0.1265 % at y+ 0.07 to 0.0103 % at y+ 2.7 and 0.0002 % at y+ 8.1.
- **Whole channel, from the block minima:**
  - 28,118 of 29,491,200 blocks hold u < 0;
  - next to the lower wall: 14,004 blocks (4.56 % of block row 0); next to the upper wall: 14,084
    (4.58 %);
  - in the second block row from each wall: 10 and 20 blocks;
  - none further from the walls.

### 8.2 Size and number (ground-truth area, 84.94 million wall units^2)
- **208 regions** (26-connected u < 0; 226 with 6-connectivity), **2.45 per million wall
  units^2**. Cardesa et al.: 1.97 at Re_tau 2003, on a grid 1.5x finer in x and z.
- All attached to the wall.
- Mean bounding box **25.2 x 24.0** wall units (streamwise x spanwise). Cardesa: about 19-20. Our
  coarser grid biases sizes up and counts down.
- Median 11 cells.

### 8.3 What each overview shows (the motivation; `Test1/results/eval/area_eval.txt`)
The area holds 64 x 64 x 4 blocks of 16^3 cells; 221 of them hold reverse flow.

| | Stride (every 16th cell) | Block mean | Block minimum |
|---|---|---|---|
| Regions shown (event recall) | 5 of 208 (2.4 %) | **0 of 208** | **208 of 208** |
| Block precision / recall | 1.0 / 0.018 | n/a / 0 | 1.0 / 1.0 (by construction) |
| Localization, median (region centroid to block centre) | 105 wall units | n/a | 60 wall units |
| Value at the region's block minus its true minimum, median | +0.0054 | +0.137 | 0 (exact) |
| **SSIM**, mean over the 16 planes of block row 0 | 0.181 | **0.344 (highest)** | **0.136 (lowest)** |

**SSIM ranks the block mean highest, and the mean shows none of the 208 regions.** The minimum shows
all of them and ranks lowest. An overview chosen by image fidelity would show no reverse flow; this
is why step 4's full read is paid.

The minimum's costs are:
- localization to a block (204 x 102 wall units in plane);
- values biased low against any pointwise field (step 6's record says so).

### 8.4 Beneath high- or low-speed large-scale motions
Sources: `Test1/results/eval/largescale.txt` (pre-registered) and `Test1/results/step7/README.md`
(post hoc).

**Pre-registered** (`DESIGN.md` §6, `LITERATURE.md` §5):
- **Large-scale field:** u'_L on the block-mean plane at y+ 100 (step 3b), low-passed with a sharp
  spectral cut-off keeping lambda_x > delta and lambda_z > delta/4.
- **Measure:** the fraction of backflow beneath u'_L > 0, against the share of the wall with
  u'_L > 0 (chance).

| Backflow measure | n | Beneath u'_L > 0 | Chance |
|---|---|---|---|
| (a) stride points, lower wall (z = 1) | 297 | 50.2 % | 50.3 % |
| (b) area points (z = 1) | 1,326 | 54.0 % | 50.3 % |
| (c) blocks with minimum < 0, lower wall | 14,004 | 55.3 % | 50.3 % |
| (c) the same, upper wall | 14,084 | 54.4 % | 49.5 % |

- Spanwise cut-offs delta/8 and delta/2 give 44.1-56.9 % (a, b) and 52.4-55.6 % (c).
- **A weak lean, far from Lenaers' 69 %** (69 % of backflow points beneath positive large-scale u
  at y+ ~ 100, Re_tau 1000, Gaussian filter of unstated width).

**Post hoc (step 7):** varies the height and the filter (`largescale_posthoc.txt`, all 12 rows):

| Height | Filter | Blocks, lower | Blocks, upper | Area points |
|---|---|---|---|---|
| y+ 100 | outer, sharp (pre-registered) | 55.3 % | 54.4 % | 54.0 % |
| y+ 100 | viscous, sharp (lambda_x+ > 1000, lambda_z+ > 250) | 63.9 % | 62.9 % | 69.0 % |
| y+ 281 | outer, sharp | 60.0 % | 60.0 % | 61.3 % |
| y+ 519 (= 0.1 delta) | outer, sharp | 60.7 % | 61.6 % | 67.6 % |

Chance is 49-51 % in every row.

- **When the filter keeps motions down to about 1000 wall units** (Lenaers' delta, in wall units),
  61.0-63.9 % of backflow blocks and 67.0-70.2 % of area points lie beneath u'_L > 0, close to
  Lenaers' 69 %.
  This holds at all three heights and for both filter shapes.
- **With the filter fixed at the outer scale** (lambda_x > delta = 5186 wall units), the association
  is weak at y+ 100 (54.3-55.3 % of blocks) and moderate at y+ 281 and 519 (59.5-61.6 % of
  blocks).
- **Reading:** the motions that carry the association at Re_tau 1000 are sub-delta motions at
  Re_tau 5186.
- **Caveats:**
  - post hoc;
  - one snapshot;
  - neighbouring blocks are not independent;
  - the area points come from one 13,035 x 6,513 wall-unit patch.

---

## 9. Figure

`Test1/figures/fig_cs3_overviews.{pdf,png}`, made by `Test1/scripts/make_figure.py` (cvision env).
- **Row 1** (panels a-d): the ground-truth area, block row 0, one diverging scale centred at u = 0
  (±0.006 U_b, clipped). The panels are:
  - (a) full resolution (minimum over z 1-16);
  - (b) stride;
  - (c) block mean;
  - (d) block minimum.

  Rings mark the 208 regions on every panel, and the regions shown and the SSIM are under each.
  The block mean is positive everywhere; the block minimum is negative in every ringed block.
- **Row 2** (e): the whole lower wall. The large-scale u'_L at y+ 100 (pre-registered filter) is in
  grey, the 14,004 reverse-flow blocks are red dots, and the ground-truth area is outlined.

`Test1/figures/pv_{channel,closeup}.png`: the outputs in one ParaView scene, as each file places
itself (`Test1/scripts/render_paraview_scene.py` in pvpython 6.1.0-RC2; `trim_pv_panels.py` only
removes empty margins).
- **(a)** The channel outline, with the 14,004 lower-wall blocks of step 5 (red cubes) and, over
  the back third, step 3b's block means at y+ 100 (grey). Step 2's area is outlined in blue.
- **(b)** A 128 x 128-cell part of step 2's area (source cells x 5120-5247, y 4224-4351). It shows:
  - the wall plane, coloured by u;
  - the full-resolution surfaces u = 0 around its 32 regions;
  - the outline of each of step 4's 32 negative blocks there (31 in block row 0, 1 in row 1).

  Every cell with u < 0 lies in an outlined block.

---

## 10. Outputs in ParaView

Every `.vti` opens in ParaView 6.1.0-RC2 as ImageData placed at its source cells (`sieve_in_cells`),
with the provenance record in its field data. Each step's `paraview_check*.txt` records:
- step 1: origin (0, 0, 1), spacing 16;
- step 2: origin (4608, 3328, 1), spacing 1;
- block outputs: block centres, origin (7.5, 7.5, 8.5) for steps 4-6.

Overview and detail cuts therefore overlay in one ParaView scene.

**The netCDF-4 copy** (`Test1/results/step6/nc_rewrite/README.md`):
- After two Sieve fixes (below), it has x, y, z coordinate variables in source cells.
- ParaView's generic netCDF reader opens it as ImageData at those positions, **with the axes in
  reverse order**. Sieve writes x first by design; ParaView's CF reader takes the last dimension
  as X.
- Opening by extension picks ParaView's EAM reader, which fails.
- The `.vti` is the output for ParaView; the `.nc` serves readers without VTK.

---

## 11. Sieve changes made for this case (each with tests; all suites pass)

| Commit | Change |
|---|---|
| `d5b3486` | The remote reducer times itself: `server_s` (work next to the data) separate from `launch_s` |
| `70e12cb` | The estimate prices the read on the server; the gate holds it over its own budget (`VISLANG_BUDGET_READ_BYTES`); the run records `server_read_bytes` |
| `2544612` | Extent cache serves a request from a cached extent of its written start (threshold/coarsen after it; a looser cached threshold tightened; order is part of the key) |
| `36960aa` | The estimate asks the cache what the run asks it: a request the cache would serve is priced at 0 |
| `b4a7a96` | ParaView plugin: Estimate button; Status shows the remote reduce (server time, bytes read there) |
| `4ca9724` | The extent cache keeps element sizes (a cached schema had priced float64 at 4 B; found in step 2) |
| `b7e97c3` | The estimate still sees the cache after a cache-schema version change (found before step 3) |
| `7a657f9` | Run record: a derived result keeps route `catalog_derived` (step 3 was mislabelled `catalog_full_hit`) |
| `2171fdd` | netCDF-4 output: coordinate variables for a placement in source cells (found in step 6) |
| `13cf804` | netCDF-4 output: no CF `axis` attribute (ParaView bent the grid into a spherical shell) |

---

## 12. Caveats and open items

- **Warm page cache:**
  - steps 2 and 3b (lower) were partly warm, so their measured reads are low;
  - step 4 started with 41.6 GB of the file cached;
  - step 7's upper planes were fully cached;
  - each case is stated where it occurs.
- **Read-ahead amplification** (4.8x strided, 9.8x small pieces) is measured, not modelled by the
  estimate.
- **Sieve's block reduction is slower than a hand-written one** (section 7): 10,344.78 s against
  6,836.04 s for step 4.
  - Reading the same slab costs the same either way.
  - The reduction's two copies cost about 15 s per slab, about 41 % of the gap.
  - The rest is not explained.
- **The block minimum** locates reverse flow only to a block, 204 x 102 x up to 8 wall units.
- **The netCDF copy** has the axes in reverse order in ParaView (section 10).

## 13. Records and reproducibility

- Per-step READMEs: `Test1/results/step0` … `step7`, `eval/`, `baseline/`.
- Analysis scripts (`Test1/scripts/`):
  - `stride_census.py`;
  - `evaluate_area.py`;
  - `largescale.py` (pre-registered);
  - `largescale_posthoc.py`;
  - `make_figure.py`;
  - `vti2npy.py` (vislang env; the others run in the cvision env with NumPy, SciPy and
    scikit-image);
  - checks: `check_vti_pv.py`, `check_nc_pv.py` (pvpython).
- Run records: `.vislang/timings.jsonl` (each step's line copied as `timing_record.json`).
- Provenance: inside every `.vti` and the `.nc`; read with the `provenance` MCP tool.
