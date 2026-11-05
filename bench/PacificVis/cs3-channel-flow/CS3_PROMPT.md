# Task: design Case Study 3 (turbulent channel flow) for the Sieve PacificVis 2027 paper

You are working in the VisLang repository (`/Users/agoosh11/Projects/VisLang`, branch
`agoosh-ddsl-mcp`). VisLang is the code name of **Sieve**, a declarative DSL and runtime for
visualization-driven data movement: a scientist states which variables, timesteps, region,
resolution and values they need from remote simulation data, and the runtime checks the
request against metadata, prices it, reduces the data next to where it lives (over plain ssh),
caches what arrives, and writes a provenance record into every output.

I am writing a **systems paper** on Sieve for the **PacificVis 2027 conference track; the
paper is due 2026-11-09 (AoE)**. Section 4 of the paper is a set of application scenarios.
Your job is to **design Case Study 3 (Section 4.3)**, a turbulent channel-flow scenario, and
then, once I approve the design, carry it out and write it up.

**Do not run any data-moving or expensive step until I have approved the design.**
The first deliverable is a design document, not results.

---

## 1. Read these first, in this order

1. `CLAUDE.md`: how Sieve works, the forms, the MCP tools, and the rules (approval gates,
   no auto-allocation, soundness).
2. The paper draft: `paper/sieve_pacific_vis_2027.pdf` (extract it with `pdftotext -layout`).
   Note the four contributions in Section 1 (requests as first-class objects; cost estimation;
   remote reduction with fusion; semantic caching; provenance; no server deployment) and the
   grammar in Listing 1. **Section 4.3 is currently an empty stub.** Section 4.2 (wildfire) is
   the model for length, structure and tone.
3. `bench/PacificVis/cs2-wildfire/CASE_STUDY_BRIEF.md`: the complete record of Case Study 2.
   This shows how a case study is designed, tested and recorded in this project: a scientific
   question, a sequence of Sieve requests, real baseline scripts that are checked against
   Sieve's output, a bytes table, a science figure, and a replay. Follow the same approach.
4. `bench/PacificVis/cs3-channel-flow/PLAN_draft.md`: **my draft plan for this case study.**
   Treat it as the starting hypothesis, not as fact. Section 3 below lists where it is
   ahead of what Sieve does today.
5. `instructions/dsl-reference.md` (especially `coarsen`, `threshold`, `region`, `save`),
   `instructions/provenance.md`, and `vislang/remote/catalog.py` for caching.

---

## 2. The data

- Location: `ssh://gpu-server//mnt/na1/eecs/research/harp/data/ashrestha/vislang/TurbulentChannelFLow/`
  (note the capital `L` in `FLow`; use the path exactly).
- Contents, according to my plan (**not yet verified**): one raw, headerless binary file of
  **pressure** from the JHTDB channel-flow DNS at Re_τ ≈ 5200 (Lee & Moser 2015), a single
  snapshot, about **966 GB**. The published grid is 10240 × 1536 × 7680 (streamwise ×
  wall-normal × spanwise). If the file is float64, 10240·1536·7680·8 B = 966.4 GB, which
  matches.
- Reaching gpu-server: plain `ssh` needs an interactive login. Use the `connect` MCP tool
  (a password dialog opens for me), then `inspect`. If `connect` reports UNREACHABLE, the VPN
  is down: tell me and stop. Don't retry in a loop.

**First, verify the data and report back** (metadata only; nothing large moves):
- the exact filename(s) and sizes (`inspect`, or a `stat` over the connected session);
- whether the name follows Sieve's raw convention `<name>_<nx>x<ny>x<nz>_<dtype>.raw`.
  Sieve's built-in raw reader (commit `3e9d533`) maps the file, takes **x as the fastest
  axis** (the Open SciVis layout), and refuses a file whose size disagrees with its name.
  If the name lists 10240 × 7680 × 1536, Sieve's axes are x = streamwise, **y = spanwise,
  z = wall-normal**. Confirm this from the data, not the name: a profile of pressure variance
  along the wall-normal axis should be symmetric about the centreline and peak near both walls.
  A tiny strided read is enough.
- whether the **remote** VisLang checkout has the raw reader and `coarsen` (commits `3e9d533`
  and `f3349ae`). If not, tell me. Don't update the remote repo without asking.
- whether velocity is available anywhere (it would allow λ₂, see the plan).

---

## 3. What Sieve does today vs. what the draft plan assumes

I checked these in the code on 2026-11-03. Re-check anything you rely on, and fix this list
if it is wrong.

| Draft plan assumes | Sieve today | Consequence for the design |
|---|---|---|
| `coarsen(src, 16, how="min"/"mean")` exists | **Yes** (commit `f3349ae`): grids only; mean/min/max; per-axis factors (`coarsen(d, x=16, y=16, z=4)`); runs next to the data; streamed in slabs (`VISLANG_SLAB_MB`, default 1024); priced as reading every cell of the region; values placed at block centres | Steps 1 and 3 are supported. Per-axis coarsening makes the stretched-grid option (less coarsening along wall-normal) possible |
| Stride reads ~3.8 GB on the server | Plausible: a stride of 16 on an x-fastest memory map touches 1/256 of the file (3.77 GB). **Measure it; don't assume it.** Check what `estimate` reports as server read for both | This is the headline number of the cost-estimation claim; it must come from Sieve's estimate and the timing record, not from arithmetic |
| Step 4: `threshold(coarsen(...))` is served from a cached `coarsen(...)` with zero bytes moved | **Not supported.** The extent catalog reuses a coarsened extent **on an exact match only** (`dsl-reference.md`, `coarsen`). A threshold after a cached coarsen would trigger another full 966 GB read | Either (a) extend the catalog so a cached prefix of the chain plus the remaining post-read forms is served locally, with tests, or (b) drop or reword the semantic-caching step. **Propose which, with an estimate of the work; I decide** |
| `threshold` result shape | On grids, failing cells are **NaN-masked; the grid keeps its shape** | Candidate blocks appear in ParaView as a masked grid (use Threshold or NaN-aware colouring), not a list of cells |
| netCDF-4 output with CF `cell_methods` (e.g. `x: y: z: minimum`) | `save(..., "x.nc")` writes netCDF-4 with the provenance record as attributes, and the provenance text says the values are "block minima … (computed, not source values)". **No `cell_methods` attribute is written** | Adding `cell_methods` on coarsened variables is small. Propose it; don't claim it until it exists |
| The full-resolution region keeps its position as geometry | A raw grid has no geometry, so a cut is placed **in source cell indices** (origin = first cell, spacing = stride, `sieve_in_cells`). `.vti` and `.npz` carry that placement. **`.nc` writes no coordinate variables for an in-cells grid**, so in netCDF the offset is only in the provenance record | For ParaView overlay, save the region as `.vti`. If the paper says the `.nc` file carries its position, that must be built first |
| Attach the stretched wall-normal grid from Lee & Moser as coordinates | **No mechanism** to attach coordinates from outside the file | Either propose a small feature (it touches the format boundary, `DatasetInfo.geometry`, and soundness rules: the coordinates must come from a cited, verifiable source) or state the index-space caveat in the paper |
| Mean and min overviews are two separate 966 GB reads | True: there is no multi-aggregate single pass | Either name it as future work, or, if cheap, propose `how=["min","mean"]` in one pass. Your call to propose; mine to approve |

Also keep in mind:
- **Fusion is weak in this case study.** Don't force it (the plan explains why).
- **Approval gates.** `confirm=True`, `without_allocation=True` and `allow_fetch=True` each
  need my explicit approval for that specific hold. A 966 GB server-side read must be priced
  and shown to me before it runs. If a run reports `NEEDS ALLOCATION`, propose the `salloc`
  command from `CLAUDE.md` and wait for me.

---

## 4. What a case study in this paper must do

Each case study is evidence for the paper's contributions, not a tutorial. Design so that:

1. **There is a real scientific question**, grounded in the literature, that the data can
   answer at least partly. The plan's question: *where are the most intense low-pressure
   (vortical) events in the Re_τ ≈ 5200 channel, and are they attached to the wall or spread
   through the outer flow?* (del Álamo et al. 2006; Lee & Moser 2015; Panton, Lee & Moser 2017;
   Robinson 1991; Jeong & Hussain 1995). State the limits plainly: pressure minima are a
   first-pass locator, not vortex identification.
2. **Each Sieve claim it supports is shown by a measurement**, mapped to a contribution in
   Section 1. For CS3 the candidate claims are: (i) the reduction operator is a visible,
   named part of the request (language); (ii) the cost of a better overview is shown before
   it is paid (cost estimation: same 236 MB result, very different server read); (iii) the
   966 GB file never moves (remote reduction); (iv) the output records what it is (provenance,
   and `cell_methods` if built); (v) semantic caching, only if Section 3's gap is closed.
   CS3's distinct contribution, which CS1 and CS2 lack, is **the choice of reduction
   operator as a visualization decision**: a stride misses features smaller than the
   stride, a mean blurs them, a block minimum keeps them. Compare M4 (Jugel et al. 2014).
3. **The comparison is fair and checked.** Baselines are real scripts: (a) download the whole
   file, as a `stat` byte count, not actually run; (b) a script on the server (numpy over a
   memory map, block reduction by hand). Count non-blank, non-comment lines. Check each
   baseline's output against Sieve's (array hashes or exact equality), as in CS2.
4. **There is a quantitative feature-preservation result.** The plan proposes this: take a
   full-resolution ground-truth block at the wall (e.g. 512 × 512 × 256, ~0.54 GB), fix the
   definition of a "core" **before** measuring (e.g. local pressure minima below −k·p_rms,
   after removing any mean that depends on wall distance), then report, per operator
   (stride, mean, min): **core recall**, localization error, value bias, and **SSIM**.
   The hypothesis worth testing is that SSIM ranks the mean highest while it loses most cores,
   i.e. the standard image-fidelity metric would pick the wrong overview. Report whatever the
   data shows, even if it contradicts the hypothesis. Min's recall is 100% by construction,
   so report its cost instead (block-level localization, values biased low).
5. **It ends in one figure and one table** that fit a PacificVis page: e.g. a figure with
   the same wall-parallel or side slice under stride / mean / min / full resolution, cores
   marked; and a table per route of estimated vs. actual bytes (server read, over the wire),
   remote reduce time on the server, recall and SSIM.

**Reporting rules (non-negotiable):**
- **For each Sieve request, report three things:**
  1. **Estimated bytes:** what Sieve's cost estimate predicted before anything ran (server
     read and result / over the wire). The estimate is reported **in bytes only**. Its
     predicted *times* (`est_time_lo_s`/`est_time_hi_s`, the bandwidth-probe transfer time)
     are not reported.
  2. **Actual bytes:** bytes read on the server and bytes over the wire, from Sieve's run
     records (`.vislang/timings.jsonl`, the run result) and `stat`.
  3. **Remote reduce time:** how long the job took to execute on the server: the
     `server_s` field of the `remote_exec` phase in `.vislang/timings.jsonl` (executor entry
     to result written: imports, read, reduction, write). Use the remote planner's
     `server_phases` breakdown if the read/reduce split is useful.
- **Exclude transfer time**, and anything else that depends on the VPN link: ssh round trips,
  `launch_s`, the pull, end-to-end wall time (`total_s`), and extrapolated transfer times.
  The link runs over a VPN, so these are unreliable.
- For a baseline script run on the server, time its execution there the same way
  (on the server, excluding any copy). A download baseline is a byte count (`stat`) only.
- Repeat a server-side timing if it looks noisy (e.g. a cold vs. warm page cache on the
  966 GB file), and state which you report.
- Every number in the brief cites the file it was copied from.
- Outputs must open correctly in ParaView (pvpython:
  `/Applications/ParaView-6.1.0-RC2.app/Contents/bin/pvpython`); check them there.

---

## 5. Where things go

Everything for this case study lives under `bench/PacificVis/cs3-channel-flow/`. Each attempt
is its own record and is never overwritten: `Test1/`, `Test2/`, … Inside each:
`scripts/`, `baseline/`, `data/`, `results/stepN/` (each with a short README), `figures/`,
`outputs/` (Sieve-written data, GBs; gitignored, kept on disk only). The case study's final
record is `bench/PacificVis/cs3-channel-flow/CASE_STUDY_BRIEF.md`, in the same form as CS2's.

The spec you run lives in the single `spec.py` at the repo root, edited in place (per
`CLAUDE.md`). Keep a copy of each spec as run in the step's results folder.

Commits: commit each finished stage directly on `agoosh-ddsl-mcp` (no feature branches).
Don't push unless I ask. Any Sieve change (catalog prefix reuse, `cell_methods`, etc.) gets
tests in `tests/` (standalone scripts run with the `vislang` conda env's python; no pytest)
and its own commit, separate from the case-study records.

---

## 6. What I want from you first: the design document

Write `bench/PacificVis/cs3-channel-flow/DESIGN.md` and stop for my review. It should contain:

1. **Data facts** you verified (Section 2), with what is still unknown.
2. **The scientific question and its sources**, and what the data can and cannot answer.
3. **The steps**, each with: the Sieve spec (exact forms), what the scientist learns from it,
   which paper claim it supports, what is measured (estimated bytes, actual bytes, remote
   reduce time), and what Sieve's `estimate` says it will cost in bytes (dry run only: run
   `estimate` / a spec with no sink; nothing heavy).
4. **Sieve gaps** from Section 3 that the design depends on: for each, the options, the
   rough size of the work, and your recommendation. Given the deadline (six days), separate
   **must-have** from **nice-to-have**, and give a version of the case study that still works
   if none of the new features are built.
5. **The evaluation**: core definition, metrics, how ground truth is obtained, how the
   baselines are checked.
6. **The planned figure(s) and table**, sketched (columns, panels).
7. **Risks and caveats** for the paper: the stretched wall-normal grid (index space
   exaggerates the near-wall region; check which y⁺ range the first coarse layers cover),
   pressure as a vortex proxy, two full reads, float64 precision, and anything you find.
8. **A schedule** to 2026-11-09, including when each 966 GB read would run and what it
   needs from me (approvals, an allocation, VPN time).
9. **Draft text for Section 4.3**, around the length of 4.2, with placeholders such as
   `[X GB]` for numbers not yet measured. Never invent a number.

**Writing register:** professional and academic, for readers who know visualization and HPC
but not Sieve's internals. No project jargon (say "the extent cache", not "the catalog";
"pieces cached from earlier requests", not "need − have"). Plain, precise sentences; no hype.
Keep your messages to me short. Put the detail in the files.

If something in this prompt or in the plan turns out to be wrong once you look at the code
or the data, say so plainly and design around what is true.
