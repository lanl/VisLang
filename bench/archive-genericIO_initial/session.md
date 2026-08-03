# Sieve case study — session of 2026-07-31 14:21

Every row was produced by `run_pipeline` (the same entry point an 
interactive session uses) and measured by `vislang_timing.py`. 
Bytes labelled *over the wire* are what actually crossed the network; 
*source* is what a whole-file/whole-folder copy would have moved.

## E1 — Data movement, and the gate before it

### E1a — First look at the gas — cost gate holds

```python
save(subsample(fields(source("ssh://darwin/projects/exasky/data/hacc/SCIDAC_RUNS/128MPC_RUNS_FLAMINGO_DESIGN_3A/FSN_0.5387_VEL_149.279_TEXP_9.613_BETA_0.8710_SEED_1.387e5/output/m000p.full.mpicosmo.624"), ['x', 'y', 'z', 'rho']), 100), "/Users/ashrestha/Projects/VisLang/bench/results/out/e1_survey")
```

**Why it is in the paper.** The gate prices the request from metadata before any bulk read. A HELD run is the claim that Sieve can refuse a request without paying for it.

**Expected.** HELD over budget (the 3 s time budget); 0 bytes over the wire; an estimate of ~41 MiB.

**Outcome.** `NEEDS CONFIRM`, 41.53s wall clock.

- source: 8.31 GiB
- over the wire: 0.0 MiB
- catalog: reused 0/4 variable extent(s)
- estimate: 41.0 MiB over the wire, predicted 48–192 s  ⚠ over budget
- phases: probe_source 0.00s, login_node_inspect 8.16s, catalog_delta 0.00s, static_check 0.00s, cost_estimate 9.38s
- top-level: remote_reduce 17.54s
- orchestration: ssh_exec=1, ssh_query=2
- route: `held_budget`, held on budget

### E1b — First look at the gas — confirmed, executed

```python
save(subsample(fields(source("ssh://darwin/projects/exasky/data/hacc/SCIDAC_RUNS/128MPC_RUNS_FLAMINGO_DESIGN_3A/FSN_0.5387_VEL_149.279_TEXP_9.613_BETA_0.8710_SEED_1.387e5/output/m000p.full.mpicosmo.624"), ['x', 'y', 'z', 'rho']), 100), "/Users/ashrestha/Projects/VisLang/bench/results/out/e1_survey")
```

**Why it is in the paper.** The headline movement number: 4 of 17 variables at stride 100, reduced next to the data. The denominator is the 8.31 GiB an ad-hoc copy moves — NOT the 2,064 B header the harness records (see SNAP_BYTES).

**Expected.** ~41 MiB over the wire (268,435,456/100 rows x 4 vars x 4 B = 42,949,672 B), a ~208x reduction against SNAP_BYTES. GenericIO has no strided read (supports_strided_read=False), so the remote side reads all 8.31 GiB and strides after — expect remote_exec to dominate, unlike the NYX runs.

**Outcome.** `OK`, 84.63s wall clock.

- source: 8.31 GiB
- over the wire: 41.0 MiB — **207.8x less than a whole-source copy**
- catalog: reused 0/4 variable extent(s)
- estimate: 41.0 MiB over the wire, predicted 44–175 s  ⚠ over budget — actual pull 23 s, OUTSIDE the band
- phases: probe_source 0.00s, catalog_delta 0.00s, static_check 0.00s, cost_estimate 8.54s, ship_plan 7.85s, remote_exec 14.43s, pull 22.96s
- top-level: remote_reduce 69.05s, sink_save 0.01s
- orchestration: ssh_exec=4, ssh_query=3, remote_jobs=1
- route: `remote_reduce`

## E2 — An iterative session (catalog reuse)

### E2q1 — +internal energy (per-variable delta)

```python
save(subsample(fields(source("ssh://darwin/projects/exasky/data/hacc/SCIDAC_RUNS/128MPC_RUNS_FLAMINGO_DESIGN_3A/FSN_0.5387_VEL_149.279_TEXP_9.613_BETA_0.8710_SEED_1.387e5/output/m000p.full.mpicosmo.624"), ['x', 'y', 'z', 'rho', 'uu']), 100), "/Users/ashrestha/Projects/VisLang/bench/results/out/e2q1")
```

**Why it is in the paper.** The commonest follow-up: same view, one more variable. File-keyed caching cannot express this — it would refetch all five. `fields` is deliberately excluded from the catalog key (remote_reduce._narrow_key), which is what makes the projection a reusable axis rather than part of the identity.

**Expected.** 4/5 variables reused; only uu crosses (~10.7 MiB). The remote side must still read all 8.31 GiB to get that one column.

**Outcome.** `OK`, 72.84s wall clock.

- source: 8.31 GiB
- over the wire: 10.2 MiB — **831.3x less than a whole-source copy**
- catalog: reused 4/5 variable extent(s)
- estimate: 10.2 MiB over the wire, predicted 10–41 s  ⚠ over budget — actual pull 16 s, INSIDE the band
- phases: probe_source 0.00s, catalog_delta 0.01s, static_check 0.00s, cost_estimate 8.01s, ship_plan 8.30s, remote_exec 9.30s, pull 16.40s
- top-level: remote_reduce 57.35s, sink_save 0.01s
- orchestration: ssh_exec=4, ssh_query=3, remote_jobs=1
- route: `remote_reduce`

### E2q2 — Verbatim re-issue (exact hit)

```python
save(subsample(fields(source("ssh://darwin/projects/exasky/data/hacc/SCIDAC_RUNS/128MPC_RUNS_FLAMINGO_DESIGN_3A/FSN_0.5387_VEL_149.279_TEXP_9.613_BETA_0.8710_SEED_1.387e5/output/m000p.full.mpicosmo.624"), ['x', 'y', 'z', 'rho', 'uu']), 100), "/Users/ashrestha/Projects/VisLang/bench/results/out/e2q2")
```

**Why it is in the paper.** Re-running a query — the reload, the second look. Should cost nothing on the network and, more importantly here, should avoid the 8.31 GiB remote read entirely.

**Expected.** 5/5 reused; 0 bytes; no srun step; seconds, not minutes.

**Outcome.** `OK`, 15.15s wall clock.

- source: 8.31 GiB
- over the wire: 0.0 MiB
- catalog: reused 5/5 variable extent(s)
- phases: probe_source 0.00s, catalog_delta 0.01s, static_check 0.00s
- top-level: remote_reduce 0.02s, sink_save 0.02s
- orchestration: ssh_query=2
- route: `remote_reduce`

### E2q3 — Impose the filter — hot, diffuse gas (new lineage)

```python
save(subsample(threshold(threshold(fields(source("ssh://darwin/projects/exasky/data/hacc/SCIDAC_RUNS/128MPC_RUNS_FLAMINGO_DESIGN_3A/FSN_0.5387_VEL_149.279_TEXP_9.613_BETA_0.8710_SEED_1.387e5/output/m000p.full.mpicosmo.624"), ['x', 'y', 'z', 'rho', 'uu']), "uu > 68000.0"), "rho < 33000000000.0"), 100), "/Users/ashrestha/Projects/VisLang/bench/results/out/e2q3")
```

**Why it is in the paper.** The actual science question: of the gas, which is hot AND diffuse — the signature of wind-ejected material? A threshold enters the catalog key verbatim (var, op, value), so this is a NEW lineage sharing nothing with the unfiltered survey, however similar it looks.

**Expected.** 0/5 reused — a full miss and a full 8.31 GiB remote read. The cut values themselves came from data already local, so choosing them cost no remote work; only applying them does.

**Outcome.** `OK`, 75.53s wall clock.

- source: 8.31 GiB
- over the wire: 0.9 MiB — **9838.2x less than a whole-source copy**
- catalog: reused 0/5 variable extent(s)
- estimate: 5.00 GiB over the wire, predicted 5002–20010 s  ⚠ over budget — actual pull 15 s, OUTSIDE the band
- phases: probe_source 0.00s, catalog_delta 0.00s, static_check 0.00s, cost_estimate 7.82s, ship_plan 7.49s, remote_exec 12.53s, pull 15.41s
- top-level: remote_reduce 59.21s, sink_save 0.00s
- orchestration: ssh_exec=4, ssh_query=3, remote_jobs=1
- route: `remote_reduce`

### E2q4 — +metallicity, inside the filter (delta in a value lineage)

```python
save(subsample(threshold(threshold(fields(source("ssh://darwin/projects/exasky/data/hacc/SCIDAC_RUNS/128MPC_RUNS_FLAMINGO_DESIGN_3A/FSN_0.5387_VEL_149.279_TEXP_9.613_BETA_0.8710_SEED_1.387e5/output/m000p.full.mpicosmo.624"), ['x', 'y', 'z', 'rho', 'uu', 'zmet']), "uu > 68000.0"), "rho < 33000000000.0"), 100), "/Users/ashrestha/Projects/VisLang/bench/results/out/e2q4")
```

**Why it is in the paper.** Per-variable reuse is not limited to purely structural requests: within a fixed predicate, adding a variable is still a delta. This is the query that answers the hypothesis — are the hot diffuse particles enriched?

**Expected.** 4/5 reused; only zmet crosses. Same lineage as E2q3, so the filter itself is not recomputed for the columns already held.

**Outcome.** `OK`, 72.99s wall clock.

- source: 8.31 GiB
- over the wire: 0.2 MiB — **49185.1x less than a whole-source copy**
- catalog: reused 5/6 variable extent(s)
- estimate: 1.00 GiB over the wire, predicted 998–3994 s  ⚠ over budget — actual pull 16 s, OUTSIDE the band
- phases: probe_source 0.00s, catalog_delta 0.00s, static_check 0.00s, cost_estimate 7.80s, ship_plan 7.58s, remote_exec 10.69s, pull 16.42s
- top-level: remote_reduce 57.76s, sink_save 0.00s
- orchestration: ssh_exec=4, ssh_query=3, remote_jobs=1
- route: `remote_reduce`

### E2q5 — Retune the cut — the containment limit

```python
save(subsample(threshold(threshold(fields(source("ssh://darwin/projects/exasky/data/hacc/SCIDAC_RUNS/128MPC_RUNS_FLAMINGO_DESIGN_3A/FSN_0.5387_VEL_149.279_TEXP_9.613_BETA_0.8710_SEED_1.387e5/output/m000p.full.mpicosmo.624"), ['x', 'y', 'z', 'rho', 'uu', 'zmet']), "uu > 250000.0"), "rho < 33000000000.0"), 100), "/Users/ashrestha/Projects/VisLang/bench/results/out/e2q5")
```

**Why it is in the paper.** The honest counterpart to the NYX study's superset-slicing row. There, a narrower REGION was sliced out of a wider cached extent. Here the retuned cut is strictly narrower too — {uu > CUT_UU2} is a subset of {uu > CUT_UU} for CUT_UU2 > CUT_UU — but containment is decided geometrically (_grid_ranges_of / _axis_slice) and a predicate has no geometry, so the key falls to exact match.

**Expected.** 0/6 reused; a full refetch and a full 8.31 GiB read, for a result provably contained in what is already cached. This is a limitation, not a result.

**Outcome.** `OK`, 72.53s wall clock.

- source: 8.31 GiB
- over the wire: 0.1 MiB — **160380.4x less than a whole-source copy**
- catalog: reused 0/6 variable extent(s)
- estimate: 6.00 GiB over the wire, predicted 6121–24484 s  ⚠ over budget — actual pull 16 s, OUTSIDE the band
- phases: probe_source 0.00s, catalog_delta 0.00s, static_check 0.00s, cost_estimate 7.97s, ship_plan 7.17s, remote_exec 11.37s, pull 15.94s
- top-level: remote_reduce 57.67s, sink_save 0.00s
- orchestration: ssh_exec=4, ssh_query=3, remote_jobs=1
- route: `remote_reduce`

## E3 — What a rejected request costs

### E3a — Misspelled variable (remote)

```python
save(fields(source("ssh://darwin/projects/exasky/data/hacc/SCIDAC_RUNS/128MPC_RUNS_FLAMINGO_DESIGN_3A/FSN_0.5387_VEL_149.279_TEXP_9.613_BETA_0.8710_SEED_1.387e5/output/m000p.full.mpicosmo.624"), ["zmett"]), "/Users/ashrestha/Projects/VisLang/bench/results/out/e3a")
```

**Why it is in the paper.** The most frequent authoring error. Where the rejection lands — local metadata, remote metadata, or after a read — is the measurement.

**Expected.** rejected on schema (validate_narrowing, n.project), 0 bulk bytes; the seconds are the remote login-node inspect, not a read.

**Outcome.** `FAILED`, 14.77s wall clock.

- source: 8.31 GiB
- over the wire: 0.0 MiB
- catalog: reused 0/1 variable extent(s)
- phases: probe_source 0.00s, catalog_delta 0.00s, static_check 0.00s
- top-level: remote_reduce 0.00s
- orchestration: ssh_query=2
- **spent before rejection: 14.8 s, 0.0 MiB moved**
- error: `ValueError: fields: ['zmett'] not available here; have ['hh', 'id', 'mask', 'mass', 'mu', 'phi', 'rho', 'status', 'uu', 'vx', 'vy', 'vz', 'x', 'y', 'yhe', 'z', 'zmet']`

### E3b — Threshold on a variable that does not exist (remote)

```python
save(threshold(fields(source("ssh://darwin/projects/exasky/data/hacc/SCIDAC_RUNS/128MPC_RUNS_FLAMINGO_DESIGN_3A/FSN_0.5387_VEL_149.279_TEXP_9.613_BETA_0.8710_SEED_1.387e5/output/m000p.full.mpicosmo.624"), ['x', 'y', 'z', 'rho']), "temperature > 1e5"), "/Users/ashrestha/Projects/VisLang/bench/results/out/e3b")
```

**Why it is in the paper.** A domain slip rather than a typo: this dataset has no `temperature` — the thermal variable is `uu`, an internal energy. The predicate references a variable OUTSIDE the projection, which is exactly the case validate_narrowing's docstring warns must still be checked.

**Expected.** rejected on the predicate's variable, 0 bulk bytes.

**Outcome.** `FAILED`, 14.89s wall clock.

- source: 8.31 GiB
- over the wire: 0.0 MiB
- catalog: reused 0/4 variable extent(s)
- phases: probe_source 0.00s, catalog_delta 0.00s, static_check 0.00s
- top-level: remote_reduce 0.00s
- orchestration: ssh_query=2
- **spent before rejection: 14.9 s, 0.0 MiB moved**
- error: `ValueError: threshold variable 'temperature' not in ['x', 'y', 'z', 'vx', 'vy', 'vz', 'mass', 'uu', 'hh', 'mu', 'rho', 'zmet', 'yhe', 'phi', 'id', 'mask', 'status']`

### E3c — Per-axis subsample on point data (remote)

```python
save(subsample(fields(source("ssh://darwin/projects/exasky/data/hacc/SCIDAC_RUNS/128MPC_RUNS_FLAMINGO_DESIGN_3A/FSN_0.5387_VEL_149.279_TEXP_9.613_BETA_0.8710_SEED_1.387e5/output/m000p.full.mpicosmo.624"), ['x', 'y', 'z', 'rho']), x=2), "/Users/ashrestha/Projects/VisLang/bench/results/out/e3c")
```

**Why it is in the paper.** The DSL admits per-axis subsample on GRIDS only; on point data a single factor is the documented form. Note `x` IS a real coordinate here, so this is not an unknown-axis error — the form simply has no meaning for particles.

**Expected.** rejected before any read, but NOT with an axis diagnostic: _grid_ranges (and its unknown-axis check at planner.py:215) is only reached on the grid branch, so this surfaces as `Invalid dimension selection type: NoneType` from narrowing.py:136. Rejected cheaply; the message names the wrong thing.

**Outcome.** `FAILED`, 14.8s wall clock.

- source: 8.31 GiB
- over the wire: 0.0 MiB
- catalog: reused 0/4 variable extent(s)
- phases: probe_source 0.00s, catalog_delta 0.00s, static_check 0.00s
- top-level: remote_reduce 0.00s
- orchestration: ssh_query=2
- **spent before rejection: 14.8 s, 0.0 MiB moved**
- error: `ValueError: per-axis subsample doesn't apply to point data; use a single factor, e.g. subsample(d, 0.1)`

### E3d — Region far outside the box — the uncheckable request

```python
save(subsample(region(fields(source("ssh://darwin/projects/exasky/data/hacc/SCIDAC_RUNS/128MPC_RUNS_FLAMINGO_DESIGN_3A/FSN_0.5387_VEL_149.279_TEXP_9.613_BETA_0.8710_SEED_1.387e5/output/m000p.full.mpicosmo.624"), ['x', 'y', 'z', 'rho']), x=(9000, 9999)), 100), "/Users/ashrestha/Projects/VisLang/bench/results/out/e3d")
```

**Why it is in the paper.** The counterpart to NYX's out-of-bounds region, which was rejected against the 512^3 extent for 0 bytes. On point data a `region` is a world-space bbox and validate_narrowing._check_bbox only verifies that the coordinate VARIABLES exist — there is no extent to violate. So an equally ill-posed request is not rejected at all.

**Expected.** NOT rejected: a full 8.31 GiB remote read that returns zero particles. The measurement is what an uncheckable mistake costs versus a checkable one.

**Outcome.** `OK`, 77.79s wall clock.

- source: 8.31 GiB
- over the wire: 0.0 MiB — **9278391.3x less than a whole-source copy**
- catalog: reused 0/4 variable extent(s)
- estimate: 4.00 GiB over the wire, predicted 4034–16138 s  ⚠ over budget — actual pull 16 s, OUTSIDE the band
- phases: probe_source 0.00s, catalog_delta 0.00s, static_check 0.00s, cost_estimate 7.88s, ship_plan 7.68s, remote_exec 11.72s, pull 15.76s
- top-level: remote_reduce 59.55s, sink_save 0.00s
- orchestration: ssh_exec=4, ssh_query=3, remote_jobs=1
- route: `remote_reduce`
