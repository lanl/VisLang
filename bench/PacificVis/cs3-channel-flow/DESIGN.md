# Case Study 3 (turbulent channel flow): design

Status (2026-11-04, revision 2): **design agreed in outline; nothing run yet.** Step 0
(verifying the data) read about 3 GB on gpu-server in place; only printed text crossed the
wire. Records: `Test1/results/step0/README.md`.

Changes from revision 1, all agreed in discussion:
- **The feature changed.** The file is streamwise velocity, not pressure, so the case looks for
  reverse-flow (backflow) events instead of low-pressure cores (section 2).
- **One full read instead of two.** Only the block-minimum overview reads the whole file. Block
  means of the test area come from its cached full-resolution cut. The large-scale field comes
  from the stride overview, or from a thin mean layer (request 3b, proposed).
- **Three claims, not five:** cost estimation, reuse of cached results, provenance. The finding
  that the choice of reduction decides what the overview shows is the **motivation**, not a
  language claim. A script can name its operator too. What Sieve adds is a runtime that
  understands the operator, so it can price it, reuse it, and label its output.
- **Cost estimation is framed as analysis of the request**, not as a budget check (section 4).
- The Sieve features this needs were built and committed on 2026-11-04 (section 5).

---

## 1. Data facts

### Verified (2026-11-04)

| Fact | Value | Source |
|---|---|---|
| File | `dns_10240x7680x1536_float64.raw`, 966,367,641,600 B, mtime 2019-04-19 | `stat` (`Test1/results/step0/README.md`) |
| Name used in every spec | `u_10240x7680x1536_float64.raw`, a symbolic link to the file (approved, created 2026-11-04). Sieve's raw reader names the variable after the filename, so the variable is `u` | step 0 README, "Symlink" |
| Size check | 10240 · 7680 · 1536 · 8 B = 966,367,641,600 B, exactly | arithmetic on the name |
| Origin | Open SciVis Datasets mirror (`http://klacansky.com/open-scivis-datasets/dns/…`), fetched 2026-11-02/03 | `wget.log` beside the file |
| Reader | Sieve's built-in raw reader: one variable `u`, grid (10240, 7680, 1536), float64 little-endian, x fastest. Sieve reads size and date through the link (`stat -L`), so its cache identity and estimates use the 966 GB target | `inspect` on the link |
| Axes | x = streamwise (10240), y = spanwise (7680), z = wall-normal (1536) | `axis_profile.txt`: along z, 0 at both ends, 1.103 at the centre, symmetric, std peaking near both walls; along y, flat |
| Quantity | **Streamwise velocity u in units of the bulk velocity.** (i) u = 0 on both outer planes (no-slip; ±1e-18 is round-off). (ii) With u_τ = 0.0414872 (JHTDB), the centre value 1.103 gives U_c⁺ = 26.6 and the peak std 0.127 gives u′⁺ = 3.06. (iii) At z = 8 the measured U⁺ = 2.75 equals the grid's y⁺ = 2.74 (viscous sublayer). Signed values, so not a magnitude | `axis_profile.txt`, `yplus_check.txt` |
| Wall-normal grid | 1536 points, y ∈ [−1, 1], symmetric, stretched: y⁺ = 0.07 at z = 1, 8.1 at z = 16, 68 at z = 64, 764 at z = 256, 5191 at z = 768. Δx⁺ = 12.7, Δ(span)⁺ = 6.4 | JHTDB `channel5200-y.txt` (`Test1/data/`), `yplus_check.txt` |
| 16-cell block rows from z = 1 | Row 0: y⁺ 0.07–8.1; row 1: 8.9–22.8; row 2: 23.9–42.8; row 3: 44.2–68.0; row 8: 223.5–271.3. Upper wall's last row (14 cells): y⁺ 0.07–6.65 | `yplus_check.txt` |
| Server | gpu-server: 48 cores, 503 GB RAM, **no Slurm** (no allocation). The file is on NFS v4.2 | step 0 |
| Read rate (cold) | Sequential: 148 MB/s. Strided memory-map copy of 157 MB of rows: 16.2 s, and the kernel fetched 739 MB (4.7× read-ahead) | `read_rate.txt` |
| Page cache | 3.4 GB of the file resident after step 0. RAM is smaller than the file, so a full read is always cold | `page_cache.txt` |
| Remote checkout | gpu-server's VisLang at `b4a7a96` (all of section 5) | `git log` on the server |
| Velocity components | Only u is on the server; v, w, p exist only in JHTDB | `ls` |

### Unknown
- Which of JHTDB's 11 snapshots this is (the Open SciVis page could not be reached from here).
  Needed only to cite the snapshot.
- Whether the NFS rate holds for a ~2 h read.

---

## 2. The scientific question

**Where, and how often, does the near-wall flow reverse direction (u < 0) at Re_τ ≈ 5200,
and do the reverse-flow events sit beneath large-scale high-speed or low-speed regions?**

- **Lenaers, Li, Brethouwer, Schlatter & Örlü (2012),** *Phys. Fluids* 24, 035110,
  doi:10.1063/1.3696304:
  - backflow occurs at about 0.06 % of wall points at Re_τ = 1000;
  - it becomes more frequent and reaches farther from the wall as Re_τ grows (up to y⁺ ≈ 8.5);
  - most events lie beneath a positive (high-speed) large-scale structure, attributed to
    amplitude modulation.
- **Cardesa, Monty, Soria & Chong (2019),** *J. Fluid Mech.* 880, R3, doi:10.1017/jfm.2019.774:
  - backflow regions are attached to the wall;
  - their density per wall area grows with Re_τ;
  - they are roughly circular in the wall-parallel plane.

  (Abstract-level reading; check the full text before citing details.)
- **Mathis, Hutchins & Marusic (2009),** *J. Fluid Mech.* 628 (amplitude modulation; outer site
  y⁺ ≈ 3.9 Re_τ^½ ≈ 280). **Hutchins & Marusic (2007),** *J. Fluid Mech.* 579 (very large
  motions). Page numbers to be checked.
- **Data:** Lee & Moser (2015), *J. Fluid Mech.* 774; JHTDB channel5200, doi:10.7281/T1PV6HJV;
  distributed by the Open SciVis Datasets (acknowledge).

**Why this feature motivates the case.** The events are a few cells across and rare.
- A stride of 16 keeps 1 cell in 256 of each wall-parallel plane, so it samples few of them.
- A block mean is dominated by the forward flow around an event and stays positive.
- A block minimum is negative if and only if its block holds a reverse-flow cell.

The scientist therefore needs the expensive overview: the block minimum reads every cell, while
the stride reads a small fraction of the file. The first 16-cell block row above each wall
(y⁺ 0.07–8.1) is the layer where backflow is reported.

**Limits.** One snapshot gives a census and an arrangement, not lifetimes or dynamics. u alone
cannot identify vortices. The large-scale relation is one realization, compared with
Lenaers et al. at Re_τ = 1000; whatever it shows is reported.

---

## 3. The requests

`src = source("ssh://gpu-server//…/TurbulentChannelFLow/u_10240x7680x1536_float64.raw")`;
`inner = region(src, z=(1, 1535))` leaves out the two wall planes (u = ±1e-18 there; a block
minimum including them would be "negative" everywhere). Blocks therefore start at x = 0, y = 0,
z = 1: 640 × 480 × 96 blocks.

Estimates are from `sieve estimate` (dry run) on 2026-11-04 with the features of section 5.
Sieve prints binary units as "MB"/"GB". Exact byte counts go in the tables.

| # | Request | Estimated read on the server | Estimated over the wire | Claim | Approval |
|---|---|---|---|---|---|
| 1 | **Stride overview** `save(subsample(inner, 16), "s1_stride16.vti")` | ~3.5 GiB (46,080 x-rows of 10,225 cells) | ~225 MiB | cost estimation (the cheap side) | yes |
| 2 | **Ground-truth block** `save(region(src, x=(4608, 5632), y=(3328, 4352), z=(1, 65)), "s2_truth.vti")` | 512 MiB | 512 MiB | (ground truth) | yes |
| 3 | **Its block means and minima** `coarsen(region(src, …same box…), 16, how="mean")`, then `how="min"` | **0: computed from request 2's cached cut** | 0 | reuse of cached results | no (nothing read) |
| 3b | *(proposed, new)* **Large-scale layer** `save(coarsen(region(src, z=(129, 145)), 16, how="mean"), "s3b_layer_mean.vti")`: block row 8, y⁺ 223.5–271.3 | ~9.4 GiB (16 planes) | ~2.3 MiB | cost estimation (a mean is cheap where the region is thin) | yes |
| 4 | **Block-minimum overview** `low = coarsen(inner, 16, how="min")`, `save(low, "s4_min16.vti")` | **~898.8 GiB (every cell); held until approved** | ~225 MiB | cost estimation, reduction next to the data | **yes: the one full read, ~2 h** |
| 5 | **Backflow blocks** `save(threshold(low, "u < 0"), "s5_backflow.vti")` | **0: computed from request 4's cached result; the estimate says so beforehand** | 0 | reuse of cached results | no (nothing read) |
| 6 | **Reproduce from the file** (spec recovered from request 4's output by `provenance`) | 0 | 0 | provenance | no |

Before request 4 the estimate reports it **held over the read budget** (100 GiB by default),
although it returns the same 225 MiB as request 1. Before request 5, once request 4 has run,
the estimate reports **0 read, 0 over the wire, "served from the extent cache"**. Before
request 4 has run, the same estimate reports the full 898.8 GiB.

What each request gives the scientist:
1. The mean profile, near-wall streaks, and the large regions in the log layer. Few or no
   u < 0 samples at the wall, to be measured.
2. Every backflow event in a 1024 × 1024-cell area at the wall, at full resolution. The
   placement is fixed now, before any overview is seen (domain centre, aligned to the 16-cell
   blocks), so the evaluation is not biased by where the overview points.
3. The mean and minimum overviews of exactly that area, to compare with the stride.
   - The cached cut is the request's own start (`region`), and what follows (`coarsen`) runs
     on the values alone, so Sieve computes it locally.
   - Reuse keeps the written order: a threshold before a coarsen is a different request.
3b. The large-scale streamwise velocity near y⁺ ≈ 250 over the whole wall, by the operator that
   suits large features (a mean), at about 1 % of the file's read.
   - Without 3b, the large-scale field comes from the stride overview, low-pass filtered.
   - With 3b, the filtered stride becomes a cross-check.
4. Where backflow occurs, over both whole walls: block row 0 at each wall, 640 × 480 blocks,
   each block's most negative u.
5. The census: which blocks hold reverse flow. The output is a NaN-masked grid of the same
   shape; in ParaView use Threshold or NaN-transparent colouring.
6. The record says the values are block minima ("computed, not source values") and holds the
   spec; re-running it is served from the cache.

**Not claimed in CS3:** fusion (only region and stride pushed into one read; the cosmology case
carries it), the overview-then-detail workflow and the ParaView plugin (CS2 shows them), and
no-server-deployment (as in CS1 and CS2). An optional full-resolution Detail of the densest
backflow cluster can close the story in one sentence. It costs another ~512 MiB, and the
decision is left for after step 5.

**How requests are issued.** From `spec.py` with the CLI/MCP, one spec per step, a copy kept in
`Test1/results/stepN/`. The ParaView plugin can issue the same requests: Estimate, then Fetch,
with Status showing the remote reduce. Using it is optional; CS2 already shows the plugin.

---

## 4. The claims and how each is shown

### 4.1 Cost estimation: analysis of the request, not a budget check

The hold itself is a comparison with a budget; it is **not** the contribution. The contribution
is that Sieve derives, **before reading anything**, what a request will do on each side. It
works from three inputs:
- the lowered request (which operators, in which order, on which region);
- the source's metadata (shape, element size, how its reader reads: whole files, columns,
  strided slabs, contiguous rows);
- the contents of its extent cache.

From these it predicts the bytes read next to the data, the bytes sent over the network, and
what is already held locally. CS3 shows four things a size check cannot:

1. **Identical results, very different work.** Requests 1 and 4 both return 225 MiB, so a check
   on result or transfer size cannot tell them apart. Sieve predicts ~3.5 GiB vs. ~898.8 GiB read
   on the server, because it knows a stride reads selected rows and a block reduction reads
   every cell of its region.
2. **Operator and layout together.** On this file x is contiguous, so a stride reads whole
   x-rows. The same request on an HDF5 file is priced by element. A VTK file is read whole.
   A threshold also reads the variable it tests. (Unit-tested in
   `tests/test_server_read.py`; CS3 exercises the raw case.)
3. **Aware of the cache, in written order.** Request 5 is priced at nothing before it runs.
   Sieve proves from the cache's index that the request is the cached request 4 plus a
   threshold, and that the threshold runs on the cached values. A threshold *before* the block
   minimum would be priced in full.
4. **Checked against measurement.** For every request, the estimated read on the server is
   compared with the bytes the executor actually fetched from storage (the kernel's
   `read_bytes`, recorded beside `server_s`).
   - Expected: close for request 4.
   - For request 1, the measured read may be several times the estimate, because NFS reads
     ahead of each row (step 0's probe: ~4.7×).
   - The gap is reported as a limit of the model: Sieve prices what the reader asks for, not
     what the filesystem fetches.

Suggested sentence: *"Sieve's estimate is derived from the lowered request, the source's
metadata and the contents of its cache, before any data is read: for each operator and reader
it predicts the bytes read next to the data, the bytes sent over the network, and the bytes
already held locally."*

### 4.2 Reuse of cached results (semantic caching beyond exact match)

Section 3.2.6 of the paper promises that a cached result can serve a narrower request. CS2
shows only exact-match replay. CS3 shows two derivations:
- **Request 3:** a cached region serves its block means and minima.
- **Request 5:** a cached block minimum serves its threshold.

Measured: bytes over the wire (0), remote jobs (0), read on the server (0). The output is
identical to a fresh read, as checked in `tests/test_cache_derive.py`.

The rule is order-faithful. The cached chain must be the request's own start, form for form,
and only `threshold`/`coarsen` may follow. A looser cached threshold may be tightened. So
`[subsample, threshold]` never serves `[threshold, subsample]`. A one-sentence example of a
refused reuse belongs in the text.

### 4.3 Provenance: the output records what it is

A block-minimum field is biased low compared with any pointwise field. Opened later without
context, it would be misread: a colormap range or an isovalue taken from full-resolution
statistics would be wrong. The source file carries no metadata at all. Sieve's output carries:
- the spec;
- the source's fingerprint;
- the output's `data_sha256`;
- an explanation saying the values are block minima, computed, not source values.

Request 6 recovers the spec from the file and re-runs it (served from the cache). The paper
must not claim that the `.vti` places the data in physical wall units. It is placed in source
cell indices, and the record says so.

### 4.4 Motivation (measured, not a claim about Sieve)

On the ground-truth area, per operator: recall of backflow events, localization, value bias,
and SSIM (section 6). The hypothesis being tested is that SSIM ranks the mean highest while the
mean catches almost no events. If so, an overview chosen by image fidelity would show no
reverse flow. This is why the scientist pays for request 4.

---

## 5. Sieve features built for this case (2026-11-04, each with tests, all suites passing)

| Commit | What | Tests |
|---|---|---|
| `70e12cb` | Server read: the estimate predicts the bytes read next to the data (per reader kind; a declared contiguous axis for raw), a read budget holds a remote request over it (`VISLANG_BUDGET_READ_BYTES`, 100 GiB), and the executor records the bytes it fetched from storage (`server_read_bytes`, Linux `read_bytes`) beside `server_s` | `tests/test_server_read.py` (28) |
| `2544612` | The extent cache serves a request from a cached result of its written start: thresholds and coarsens after it run locally; a looser cached threshold is tightened; written order is part of the key; integer grids are guarded against an inexact float32 cast | `tests/test_cache_derive.py` |
| `36960aa` | The estimate asks the cache what the run asks it (from its index; no array read): a request it would serve is priced at nothing; a partly cached one at the rest | `tests/test_cache_derive.py` (67 in all) |
| `b4a7a96` | ParaView plugin: an Estimate button (Source, Detail, Spec); Status shows the remote reduce (server time and bytes read there, against the estimate); `sieve api` returns `server_s` and `server_read_bytes` | `tests/test_pv_core.py`, 64 in-ParaView checks |

**Still not built (and not needed):**
- CF `cell_methods` in netCDF output. Provenance carries the meaning; `cell_methods` is a
  nice-to-have.
- Attaching the stretched wall-normal grid as coordinates. Index-space caveat; y⁺ per block row
  is given in the paper.
- Several aggregates in one pass (future work).
- A detached remote job that survives a VPN drop.

---

## 6. Evaluation

Fixed before any overview is seen.

**Ground truth:** request 2's block. The operators are compared on its **4096 blocks of block
row 0** (z 1–16, y⁺ 0.07–8.1). Row 1 (y⁺ 8.9–22.8) is checked for backflow too, and evaluated
the same way if it has any.

**Definitions:**
- *Backflow cell:* u < 0 at z ≥ 1.
- *Event:* a 6-connected component of backflow cells. 26-connectivity is reported as a one-line
  sensitivity check. Events touching the block's sides are kept and flagged.
- *Block flagged:*
  - stride: its kept cell (the block's first) has u < 0;
  - mean: block mean < 0;
  - min: block minimum < 0.

**Where each operator's block values come from, and how they are checked:**
- stride: request 1's cells inside the area;
- mean and min: request 3 (from the cached cut).

Cross-checks:
- Request 3's minima equal request 4's cells for the same blocks exactly.
- Both equal a local block reduction of request 2. Means are compared to round-off; state max |Δ|.
- Request 1's cells equal request 2's cells at the stride positions exactly.

**Metrics per operator:**
1. **Event recall:** the fraction of events with a cell in a flagged block.
2. **Block precision/recall** against "block holds ≥ 1 backflow cell". For min both are 1 by
   construction; say so.
3. **Localization:** event centroid to flagged block centre, in wall units. For min it is
   bounded by the block: 204 × 102 wall units in plane, y⁺ 0–8.
4. **Value bias:** block value minus the event's true minimum.
5. **SSIM:** 2-D SSIM (`skimage.metrics.structural_similarity`, data range = ground truth's over
   row 0) between each z-plane 1–16 and the overview upsampled by nearest neighbour, averaged.

**Science (whole wall):**
- Census per wall from request 5: blocks holding backflow, out of 307,200, and the distribution
  of their minima.
- Large-scale field: u′_L = request 3b's layer minus its plane mean, Gaussian-filtered with
  σ = δ/2 in x and y (fixed now). Without 3b: request 1's row nearest y⁺ ≈ 250, same filter.
- The answer: the fraction of backflow blocks with u′_L > 0 above them, against the fraction of
  wall area with u′_L > 0. Also as a curve over streamwise shift (the structures are inclined).

**Baselines:**
- **(a) Download the whole file:** 966,367,641,600 B (`stat`), not run.
- **(b) Scripts on the server:** numpy over a memory map. `stride.py` (slicing) and `blocks.py`
  (z-slabs of 16 planes, reshape, min and mean in one pass), then `scp` back.
  - Count non-blank, non-comment lines.
  - Check equality with Sieve's arrays (stride and min exact, mean to round-off), with axis order
    aligned.
  - Time on the server with the script's own clock, excluding the copy.
  - **Decision pending:** an equality check on the first two block rows of each wall (~40 GB read
    on the server), or the full pass (a second ~2 h read). The full pass would be reported only
    if run, never extrapolated.

---

## 7. Planned figure and table

**Figure (one, two rows):**
- Row 1: the ground-truth area (64 × 64 blocks), block row 0, four panels, the same diverging
  colormap centred at u = 0:
  - (a) full resolution (minimum over z 1–16), events outlined;
  - (b) stride;
  - (c) block mean;
  - (d) block minimum.

  Events are marked as rings on every panel; recall and SSIM go under each.
- Row 2: the whole lower wall. u′_L in grey, backflow blocks as dots, the ground-truth area
  outlined.

**Table (per request; the three numbers the reporting rules require, plus the evaluation):**

| Request | Est. read on server | Actual read on server | Est. over the wire | Actual over the wire | Remote reduce time (`server_s`) | Event recall | SSIM |
|---|---|---|---|---|---|---|---|
| 1 Stride overview | 3.5 GiB | [X] | 225 MiB | [X] | [X s] | [X %] | [X] |
| 2 Ground truth | 512 MiB | [X] | 512 MiB | [X] | [X s] | (truth) | (truth) |
| 3 Means / minima of it (cache) | 0 | 0 | 0 | 0 | none | [X %] / 100 %* | [X] / [X] |
| 3b Large-scale layer (proposed) | 9.4 GiB | [X] | 2.3 MiB | [X] | [X s] | | |
| 4 Block-minimum overview | 898.8 GiB (held) | [X] | 225 MiB | [X] | [X h] | | |
| 5 Backflow blocks (cache) | 0 | 0 | 0 | 0 | none | | |
| 6 Reproduce from provenance | 0 | 0 | 0 | 0 | none | | |
| Baseline: download the file | n/a | n/a | n/a | 966.4 GB (`stat`) | n/a | | |
| Baseline: script on the server | n/a | [X] | n/a | [X] | [X] | | |

\* by construction. Spec and script lines go in a column or in the text.

---

## 8. Risks and caveats

- **Pressure → backflow.** The draft plan's pressure references (del Álamo et al. 2006; Panton,
  Lee & Moser 2017; Robinson 1991; Jeong & Hussain 1995) do not apply to this case.
- **Index space.**
  - In cell indices, block row 0 (y⁺ 0.07–8.1) is drawn as thick as a centre row spanning
    hundreds of wall units.
  - Show only wall-parallel views, and give y⁺ per row.
  - Outputs are placed in source cells, and the record says so.
- **Walls not symmetric in blocks.** The upper wall's first row holds 14 cells (y⁺ 0.07–6.65).
  Report walls separately; evaluate on the lower wall.
- **Read-ahead.** The stride's measured read can exceed its estimate several times over on NFS.
  Report both (section 4.1, point 4).
- **The one full read.**
  - ~1.8 h of reading at the measured 148 MB/s, plus the reduction. A shared NFS server may be
    slower.
  - The remote job stops when its ssh ends (by design), so a VPN drop costs a re-run.
  - One retry fits the schedule.
- **Large scales from a stride (if 3b is not run).** Aliasing of small scales into the filtered
  field. Filtering over ~25 × 25 stride samples suppresses it, but it remains a caveat. 3b
  removes it.
- **One snapshot, one ground-truth area.** If the area holds fewer than ~30 events, add the
  mirror area at the upper wall (fixed now: same x, y; z = (1471, 1535)).
- **Float64** throughout. `compress` is out of scope; one line in the paper.
- **Estimated times are not reported** (rule). Sieve's "est. transfer time" covers the network
  only.

---

## 9. Schedule (today Wed 2026-11-04; due Mon 2026-11-09 AoE)

| When | Work | Needs from you |
|---|---|---|
| Thu 11-05, morning | Requests 1, 2, then 3 (cache), and 3b if approved: about 10–15 min of server time in all. Start the evaluation code on the ground truth | **Approval of 1, 2 (and 3b)**; VPN up |
| Thu 11-05, evening | **Request 4: the one full read (~2 h)**, then 5 and 6 from the cache | **Approval of 4**; VPN stable ~2.5 h |
| Fri 11-06 | Evaluation numbers, census, large-scale relation; baseline scripts and their check (or the full pass overnight) | Decision on the baseline pass |
| Sat 11-07 | Figure; table; ParaView checks of every output (pvpython) | |
| Sun 11-08 | `CASE_STUDY_BRIEF.md`; Section 4.3 with numbers; commits | Review of the text |
| Mon 11-09 | Buffer (one retry of request 4 fits) | |

---

## 10. Draft text for Section 4.3

*(About the length of 4.2. Numbers in brackets are not yet measured.)*

> **4.3 Finding Rare Events in a Turbulent Channel Flow**
>
> **4.3.1 Data and Analysis Overview**
>
> The data is a snapshot of the streamwise velocity from a direct numerical simulation of
> turbulent channel flow at friction Reynolds number Re_τ ≈ 5200 [Lee & Moser 2015], distributed
> by the Johns Hopkins Turbulence Databases and the Open SciVis Datasets. It is a single
> headerless binary file of 966 GB (float64) on a 10240 × 7680 × 1536 grid (streamwise ×
> spanwise × wall-normal), reachable only over ssh. The wall-normal grid is stretched: the first
> 16 cells above each wall span less than nine viscous units.
>
> *Scientific Task.* Near the wall the flow occasionally reverses. These are small regions where
> the streamwise velocity is negative, covering well under a tenth of a percent of the wall at
> lower Reynolds numbers [Lenaers et al. 2012; Cardesa et al. 2019]. At Re_τ = 1000 most of them
> lie beneath large-scale high-speed regions [Lenaers et al. 2012]. A scientist asks whether this
> holds at Re_τ ≈ 5200, and first needs to see where the events are.
>
> The events are a few grid cells across, smaller than any useful reduction factor, so how the
> overview is reduced decides whether they appear in it. A stride skips them, and a block
> average blends them into the forward flow around them. A block minimum keeps every one, but
> it must read every cell of the 966 GB file, where the stride reads a small fraction of it.
> Without Sieve, the scientist downloads the file, or logs in and writes the reduction by hand.
> With Sieve, the requests read:
>
> ```python
> src   = source("ssh://host//path/u_10240x7680x1536_float64.raw")
> inner = region(src, z=(1, 1535))          # leave out the wall planes
> quick = subsample(inner, 16)              # every 16th cell
> low   = coarsen(inner, 16, how="min")     # each 16^3 block's minimum
> back  = threshold(low, "u < 0")           # blocks that hold reverse flow
> ```
>
> **4.3.2 Results**
>
> *What the overview must keep.* We fixed a 1024 × 1024-cell area at the wall in advance and
> fetched it at full resolution ([512 MiB]). It holds [N] reverse-flow events. Of them, the
> stride overview shows [n] and the block-mean overview [n]. The block-minimum overview shows
> all of them, by construction, but locates each only to its block (204 × 102 viscous units) and
> reports its most negative value rather than its extent. A standard image-fidelity metric ranks
> them [the other way]: SSIM is [X] for the mean, [X] for the stride and [X] for the minimum
> (Fig. [5]). [If confirmed:] An overview chosen by image fidelity would show the scientist no
> reverse flow at all. The block means and minima of this area were computed from the
> already-fetched cut; nothing more was read on the server.
>
> *Pricing the work, not the result.* The stride and block-minimum overviews produce the same
> 640 × 480 × 96 grid of [236 MB], so a check on result or transfer size cannot tell them apart.
> Sieve's estimate is derived from the lowered request, the source's metadata and the contents
> of its cache, before any data is read. For each operator and reader it predicts the bytes read
> next to the data as well as those sent over the network. It predicted [3.8 GB] read on the
> server for the stride, whose rows are contiguous along x, and [965 GB] for the block minimum,
> which reads every cell of its region; it held the latter for confirmation. Measured, the
> server read [X GB] and [X GB], taking [X s] and [X h]; in each case [236 MB] crossed the
> network. [The stride's measured read exceeded the estimate by [X]× because the file system
> reads ahead of each row; Sieve prices what its reader requests, not what the file system
> fetches.]
>
> *Not paying twice.* Thresholding the minimum overview at u < 0 gives the blocks that hold
> reverse flow: [N] of 307,200 at the lower wall and [N] at the upper. Before it ran, Sieve's
> estimate priced this request at nothing. The cached overview is the request's own start and
> the threshold reads only its values, so the threshold is applied locally; no remote job ran
> and [0 bytes] moved. Reuse follows the request's written order: a threshold applied *before*
> the block minimum is a different request and would be read again. Against the large-scale
> velocity at y⁺ ≈ 250, [X %] of these blocks lie beneath high-speed regions, which cover [X %]
> of the wall, [in agreement / not] with Lenaers et al. at Re_τ = 1000.
>
> *What the output records.* A field of block minima is biased low compared with any pointwise
> field, and the source file carries no metadata that could say so. The overview's provenance
> record states that its values are block minima, computed and not source values, together with
> the spec and the source's fingerprint. Re-running the spec recovered from the file moved
> [0 bytes]. A script on the server produces the same arrays [verified bit for bit] in [N]
> lines; downloading the file instead would move 966 GB.

---

## Decisions pending
1. **Request 3b** (new, proposed): a thin block-mean layer for the large-scale field,
   ~9.4 GiB read, ~2.3 MiB back.
2. **Baseline (b):** the equality check on a subset (~40 GB read) or the full pass (~2 h).
3. **Approval to run** requests 1 and 2 (and 3b), then request 4. Not before you say so.
