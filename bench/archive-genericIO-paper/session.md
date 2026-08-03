# Sieve case study — session of 2026-07-31 15:57

Every row was produced by `run_pipeline` (the same entry point an 
interactive session uses) and measured by `vislang_timing.py`. 
Bytes labelled *over the wire* are what actually crossed the network; 
*source* is what a whole-file/whole-folder copy would have moved.

## E1 — Data movement, and the gate before it

### E1a — First look at the gas — cost gate holds

```python
save(subsample(fields(source("ssh://darwin/projects/exasky/data/hacc/SCIDAC_RUNS/128MPC_RUNS_FLAMINGO_DESIGN_3A/FSN_0.5387_VEL_149.279_TEXP_9.613_BETA_0.8710_SEED_1.387e5/output/m000p.full.mpicosmo.624"), ['x', 'y', 'z', 'rho']), 3), "/Users/ashrestha/Projects/VisLang/bench/results/out/e1_survey")
```

**Why it is in the paper.** The gate prices the request from metadata before any bulk read. A HELD run is the claim that Sieve can refuse a request without paying for it.

**Expected.** HELD over budget (the 3 s time budget); 0 bytes over the wire; an estimate of ~41 MiB.

**Outcome.** `NEEDS CONFIRM`, 41.62s wall clock.

- source: 8.31 GiB
- over the wire: 0.0 MiB
- catalog: reused 0/4 variable extent(s)
- estimate: 1.33 GiB over the wire, predicted 1344–5378 s  ⚠ over budget
- phases: probe_source 0.00s, login_node_inspect 8.19s, catalog_delta 0.00s, static_check 0.00s, cost_estimate 7.88s
- top-level: remote_reduce 16.07s
- orchestration: ssh_exec=1, ssh_query=2
- route: `held_budget`, held on budget

### E1b — First look at the gas — confirmed, executed

```python
save(subsample(fields(source("ssh://darwin/projects/exasky/data/hacc/SCIDAC_RUNS/128MPC_RUNS_FLAMINGO_DESIGN_3A/FSN_0.5387_VEL_149.279_TEXP_9.613_BETA_0.8710_SEED_1.387e5/output/m000p.full.mpicosmo.624"), ['x', 'y', 'z', 'rho']), 3), "/Users/ashrestha/Projects/VisLang/bench/results/out/e1_survey")
```

**Why it is in the paper.** The headline movement number: 4 of 17 variables at stride 100, reduced next to the data. The denominator is the 8.31 GiB an ad-hoc copy moves — NOT the 2,064 B header the harness records (see SNAP_BYTES).

**Expected.** ~41 MiB over the wire (268,435,456/100 rows x 4 vars x 4 B = 42,949,672 B), a ~208x reduction against SNAP_BYTES. GenericIO is a COLUMN store (supports_column_pushdown=True), so the projection is pushed into the read and only the 4 requested columns are touched (~4.29 GB, not the full 8.31 GiB). What it cannot do is skip rows (supports_strided_read=False), so every row of those columns is read and the stride is applied after.

**Outcome.** `OK`, 141.66s wall clock.

- source: 8.31 GiB
- over the wire: 1.33 GiB — **6.2x less than a whole-source copy**
- catalog: reused 0/4 variable extent(s)
- estimate: 1.33 GiB over the wire, predicted 1341–5366 s  ⚠ over budget — actual pull 79 s, OUTSIDE the band
- phases: probe_source 0.00s, catalog_delta 0.00s, static_check 0.00s, cost_estimate 7.86s, ship_plan 7.80s, remote_exec 14.13s, pull 79.02s
- top-level: remote_reduce 125.73s, sink_save 0.19s
- orchestration: ssh_exec=4, ssh_query=3, remote_jobs=1
- route: `remote_reduce`

## E2 — An iterative session (catalog reuse)

### E2q1 — Is any of the gas hot?

```python
save(subsample(fields(source("ssh://darwin/projects/exasky/data/hacc/SCIDAC_RUNS/128MPC_RUNS_FLAMINGO_DESIGN_3A/FSN_0.5387_VEL_149.279_TEXP_9.613_BETA_0.8710_SEED_1.387e5/output/m000p.full.mpicosmo.624"), ['x', 'y', 'z', 'rho', 'uu']), 3), "/Users/ashrestha/Projects/VisLang/bench/results/out/e2q1")
```

**Why it is in the paper.** The commonest follow-up: same view, one more variable. `fields` is deliberately excluded from the catalog key (remote_reduce._narrow_key), which is what makes the projection a reusable axis rather than part of the identity.

**Expected.** 4/5 reused; only uu crosses (~10.7 MiB).

**Outcome.** `OK`, 87.9s wall clock.

- source: 8.31 GiB
- over the wire: 341.3 MiB — **24.9x less than a whole-source copy**
- catalog: reused 4/5 variable extent(s)
- estimate: 341.3 MiB over the wire, predicted 334–1336 s  ⚠ over budget — actual pull 31 s, OUTSIDE the band
- phases: probe_source 0.00s, catalog_delta 0.26s, static_check 0.00s, cost_estimate 7.83s, ship_plan 7.76s, remote_exec 9.61s, pull 30.61s
- top-level: remote_reduce 72.41s, sink_save 0.26s
- orchestration: ssh_exec=4, ssh_query=3, remote_jobs=1
- route: `remote_reduce`

### E2q2 — How enriched is the gas in general? (the control)

```python
save(subsample(fields(source("ssh://darwin/projects/exasky/data/hacc/SCIDAC_RUNS/128MPC_RUNS_FLAMINGO_DESIGN_3A/FSN_0.5387_VEL_149.279_TEXP_9.613_BETA_0.8710_SEED_1.387e5/output/m000p.full.mpicosmo.624"), ['x', 'y', 'z', 'rho', 'uu', 'zmet']), 3), "/Users/ashrestha/Projects/VisLang/bench/results/out/e2q2")
```

**Why it is in the paper.** The control population. Without unfiltered metallicity the session can report that some wind gas carries metals, but NOT that wind gas is PREFERENTIALLY enriched — which is the actual hypothesis. This row is what makes q3-q8 interpretable.

**Expected.** 5/6 reused; only zmet crosses.

**Outcome.** `OK`, 85.3s wall clock.

- source: 8.31 GiB
- over the wire: 341.3 MiB — **24.9x less than a whole-source copy**
- catalog: reused 5/6 variable extent(s)
- estimate: 341.3 MiB over the wire, predicted 322–1287 s  ⚠ over budget — actual pull 30 s, OUTSIDE the band
- phases: probe_source 0.00s, catalog_delta 0.27s, static_check 0.00s, cost_estimate 7.54s, ship_plan 8.59s, remote_exec 9.23s, pull 30.14s
- top-level: remote_reduce 69.93s, sink_save 0.29s
- orchestration: ssh_exec=4, ssh_query=3, remote_jobs=1
- route: `remote_reduce`

### E2q3 — Isolate the wind candidates (new lineage)

```python
save(subsample(threshold(threshold(fields(source("ssh://darwin/projects/exasky/data/hacc/SCIDAC_RUNS/128MPC_RUNS_FLAMINGO_DESIGN_3A/FSN_0.5387_VEL_149.279_TEXP_9.613_BETA_0.8710_SEED_1.387e5/output/m000p.full.mpicosmo.624"), ['x', 'y', 'z', 'rho', 'uu', 'zmet']), "uu > 68000.0"), "rho < 33000000000.0"), 3), "/Users/ashrestha/Projects/VisLang/bench/results/out/e2q3")
```

**Why it is in the paper.** The science question: which gas is hot AND diffuse — the signature of wind-ejected material? This misses, and the miss is REQUIRED, not a caching shortfall: the survey holds all[::100] while this asks for filter(all)[::100]. See _cut's docstring for why substituting the one for the other would answer a different question.

**Expected.** 0/6 reused. The cut values came from data already local (q1/q2), so CHOOSING the filter cost nothing remote; only applying it does.

**Outcome.** `OK`, 86.84s wall clock.

- source: 8.31 GiB
- over the wire: 34.6 MiB — **246.3x less than a whole-source copy**
- catalog: reused 0/6 variable extent(s)
- estimate: 6.00 GiB over the wire, predicted 6061–24242 s  ⚠ over budget — actual pull 18 s, OUTSIDE the band
- phases: probe_source 0.00s, catalog_delta 0.00s, static_check 0.00s, cost_estimate 7.89s, ship_plan 7.64s, remote_exec 13.32s, pull 17.52s
- top-level: remote_reduce 61.50s, sink_save 0.01s
- orchestration: ssh_exec=4, ssh_query=3, remote_jobs=1
- route: `remote_reduce`

### E2q4 — Are they actually outflowing?

```python
save(subsample(threshold(threshold(fields(source("ssh://darwin/projects/exasky/data/hacc/SCIDAC_RUNS/128MPC_RUNS_FLAMINGO_DESIGN_3A/FSN_0.5387_VEL_149.279_TEXP_9.613_BETA_0.8710_SEED_1.387e5/output/m000p.full.mpicosmo.624"), ['x', 'y', 'z', 'rho', 'uu', 'zmet', 'vx', 'vy', 'vz']), "uu > 68000.0"), "rho < 33000000000.0"), 3), "/Users/ashrestha/Projects/VisLang/bench/results/out/e2q4")
```

**Why it is in the paper.** Hot and diffuse is suggestive; hot, diffuse and MOVING is a wind. This is the delta claim the NYX study could not make: per-variable reuse inside a VALUE-DEPENDENT lineage, not merely a structural one.

**Expected.** 6/9 reused; only the three velocity components cross. The two thresholds are not recomputed for the six columns already held.

**Outcome.** `OK`, 82.62s wall clock.

- source: 8.31 GiB
- over the wire: 17.3 MiB — **492.6x less than a whole-source copy**
- catalog: reused 6/9 variable extent(s)
- estimate: 3.00 GiB over the wire, predicted 2995–11980 s  ⚠ over budget — actual pull 17 s, OUTSIDE the band
- phases: probe_source 0.00s, catalog_delta 0.01s, static_check 0.00s, cost_estimate 7.80s, ship_plan 8.09s, remote_exec 18.94s, pull 17.06s
- top-level: remote_reduce 67.51s, sink_save 0.02s
- orchestration: ssh_exec=4, ssh_query=3, remote_jobs=1
- route: `remote_reduce`

### E2q5 — Make the metallicity figure (fewer variables than cached)

```python
save(subsample(threshold(threshold(fields(source("ssh://darwin/projects/exasky/data/hacc/SCIDAC_RUNS/128MPC_RUNS_FLAMINGO_DESIGN_3A/FSN_0.5387_VEL_149.279_TEXP_9.613_BETA_0.8710_SEED_1.387e5/output/m000p.full.mpicosmo.624"), ['x', 'y', 'z', 'zmet']), "uu > 68000.0"), "rho < 33000000000.0"), 3), "/Users/ashrestha/Projects/VisLang/bench/results/out/e2q5")
```

**Why it is in the paper.** Reuse in the direction nobody demonstrates: asking for LESS. Having pulled nine columns, the scientist now wants a position-vs-metallicity figure from four of them. It is a genuinely different request — not a re-issue — and because `fields` is not part of the key it is a total hit.

**Expected.** 4/4 reused; 0 bytes; no srun step.

**Outcome.** `OK`, 15.11s wall clock.

- source: 8.31 GiB
- over the wire: 0.0 MiB
- catalog: reused 4/4 variable extent(s)
- phases: probe_source 0.00s, catalog_delta 0.00s, static_check 0.00s
- top-level: remote_reduce 0.00s, sink_save 0.01s
- orchestration: ssh_query=2
- route: `remote_reduce`

### E2q6 — Does the signal strengthen when hotter? (retune)

```python
save(subsample(threshold(threshold(fields(source("ssh://darwin/projects/exasky/data/hacc/SCIDAC_RUNS/128MPC_RUNS_FLAMINGO_DESIGN_3A/FSN_0.5387_VEL_149.279_TEXP_9.613_BETA_0.8710_SEED_1.387e5/output/m000p.full.mpicosmo.624"), ['x', 'y', 'z', 'rho', 'uu', 'zmet']), "uu > 250000.0"), "rho < 33000000000.0"), 3), "/Users/ashrestha/Projects/VisLang/bench/results/out/e2q6")
```

**Why it is in the paper.** Tightening the temperature cut. The request is strictly narrower than q3/q4 — same variable, same direction, larger constant — and provably contained in what is cached, yet it refetches. Same root cause as q3: the stride is positional over the filtered rows, so containment could not be exploited here even if predicate reasoning existed.

**Expected.** 0/6 reused. A limitation, and a bounded one: containment would be sound only if no positional op followed the cut.

**Outcome.** `OK`, 74.26s wall clock.

- source: 8.31 GiB
- over the wire: 1.7 MiB — **4937.1x less than a whole-source copy**
- catalog: reused 0/6 variable extent(s)
- estimate: 6.00 GiB over the wire, predicted 5909–23638 s  ⚠ over budget — actual pull 16 s, OUTSIDE the band
- phases: probe_source 0.00s, catalog_delta 0.00s, static_check 0.00s, cost_estimate 7.69s, ship_plan 7.65s, remote_exec 12.11s, pull 15.65s
- top-level: remote_reduce 59.19s, sink_save 0.00s
- orchestration: ssh_exec=4, ssh_query=3, remote_jobs=1
- route: `remote_reduce`

### E2q7 — Are the hottest ones outflowing too?

```python
save(subsample(threshold(threshold(fields(source("ssh://darwin/projects/exasky/data/hacc/SCIDAC_RUNS/128MPC_RUNS_FLAMINGO_DESIGN_3A/FSN_0.5387_VEL_149.279_TEXP_9.613_BETA_0.8710_SEED_1.387e5/output/m000p.full.mpicosmo.624"), ['x', 'y', 'z', 'rho', 'uu', 'zmet', 'vx', 'vy', 'vz']), "uu > 250000.0"), "rho < 33000000000.0"), 3), "/Users/ashrestha/Projects/VisLang/bench/results/out/e2q7")
```

**Why it is in the paper.** The same delta as q4, in a second, independently created value lineage. Showing the mechanism twice — in two lineages built by different predicates — is much stronger evidence than showing it once.

**Expected.** 6/9 reused; only the velocity components cross.

**Outcome.** `OK`, 74.24s wall clock.

- source: 8.31 GiB
- over the wire: 0.9 MiB — **9874.1x less than a whole-source copy**
- catalog: reused 6/9 variable extent(s)
- estimate: 3.00 GiB over the wire, predicted 3299–13198 s  ⚠ over budget — actual pull 15 s, OUTSIDE the band
- phases: probe_source 0.00s, catalog_delta 0.00s, static_check 0.00s, cost_estimate 8.59s, ship_plan 7.63s, remote_exec 12.23s, pull 15.26s
- top-level: remote_reduce 59.35s, sink_save 0.00s
- orchestration: ssh_exec=4, ssh_query=3, remote_jobs=1
- route: `remote_reduce`

### E2q8 — Where did the metals that stayed behind end up?

```python
save(subsample(threshold(threshold(fields(source("ssh://darwin/projects/exasky/data/hacc/SCIDAC_RUNS/128MPC_RUNS_FLAMINGO_DESIGN_3A/FSN_0.5387_VEL_149.279_TEXP_9.613_BETA_0.8710_SEED_1.387e5/output/m000p.full.mpicosmo.624"), ['x', 'y', 'z', 'rho', 'uu', 'zmet']), "uu < 68000.0"), "rho > 86000000000.0"), 3), "/Users/ashrestha/Projects/VisLang/bench/results/out/e2q8")
```

**Why it is in the paper.** The contrast population: gas that is NOT hot (below the wind's own temperature cut) and denser than the uniform background — the halo material feedback failed to eject. Its metallicity against q3's, both measured against q2's unfiltered control, is the actual test of the hypothesis, and it closes the investigation on a comparison rather than on a cache statistic.

**Expected.** 0/6 reused — a third lineage, a third distinct question.

**Outcome.** `OK`, 88.99s wall clock.

- source: 8.31 GiB
- over the wire: 149.4 MiB — **57.0x less than a whole-source copy**
- catalog: reused 0/6 variable extent(s)
- estimate: 6.00 GiB over the wire, predicted 6198–24791 s  ⚠ over budget — actual pull 23 s, OUTSIDE the band
- phases: probe_source 0.00s, catalog_delta 0.00s, static_check 0.00s, cost_estimate 8.07s, ship_plan 8.31s, remote_exec 16.67s, pull 22.85s
- top-level: remote_reduce 72.27s, sink_save 0.05s
- orchestration: ssh_exec=4, ssh_query=3, remote_jobs=1
- route: `remote_reduce`
