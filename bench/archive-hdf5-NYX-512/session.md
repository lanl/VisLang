# Sieve case study — session of 2026-07-31 11:36

Every row was produced by `run_pipeline` (the same entry point an 
interactive session uses) and measured by `vislang_timing.py`. 
Bytes labelled *over the wire* are what actually crossed the network; 
*source* is what a whole-file/whole-folder copy would have moved.

## E1 — Data movement, and the gate before it

### E1a — Narrowed snapshot — cost gate holds

```python
save(subsample(fields(source("ssh://darwin/projects/autonomousvis/ashrestha/nyx_series/nyx512#1.hdf5"), ["native_fields/temperature", "native_fields/baryon_density"]), 2), "/Users/ashrestha/Projects/VisLang/bench/results/out/e1_sub2.hdf5")
```

**Why it is in the paper.** The gate prices the request from metadata before any bulk read. A HELD run is the claim that Sieve can refuse a request without paying for it.

**Expected.** HELD over budget; 0 bytes over the wire; an estimate of ~128 MiB and a measured time band.

**Outcome.** `NEEDS CONFIRM`, 39.33s wall clock.

- source: 6.50 GiB
- over the wire: 0.0 MiB
- catalog: reused 0/2 variable extent(s)
- estimate: 128.0 MiB over the wire, predicted 122–488 s  ⚠ over budget
- phases: probe_source 0.00s, login_node_inspect 8.35s, catalog_delta 0.00s, static_check 0.00s, cost_estimate 7.63s
- top-level: remote_reduce 15.98s
- orchestration: ssh_exec=1, ssh_query=2
- route: `held_budget`, held on budget

### E1b — Narrowed snapshot — confirmed, executed

```python
save(subsample(fields(source("ssh://darwin/projects/autonomousvis/ashrestha/nyx_series/nyx512#1.hdf5"), ["native_fields/temperature", "native_fields/baryon_density"]), 2), "/Users/ashrestha/Projects/VisLang/bench/results/out/e1_sub2.hdf5")
```

**Why it is in the paper.** The headline movement number: 2 of 13 fields at stride 2, reduced next to the data. The denominator is the 6.98 GB an ad-hoc copy would have moved.

**Expected.** ~128 MiB over the wire (256^3 x 4 B x 2 fields = 134,217,728 B), a ~52x reduction; wall clock dominated by the pull, not the remote read.

**Outcome.** `OK`, 84.43s wall clock.

- source: 6.50 GiB
- over the wire: 128.0 MiB — **52.0x less than a whole-source copy**
- catalog: reused 0/2 variable extent(s)
- estimate: 128.0 MiB over the wire, predicted 127–509 s  ⚠ over budget — actual pull 24 s, OUTSIDE the band
- phases: probe_source 0.00s, catalog_delta 0.00s, static_check 0.00s, cost_estimate 7.96s, ship_plan 7.81s, remote_exec 14.84s, pull 24.23s
- top-level: remote_reduce 69.94s, sink_save 0.06s
- orchestration: ssh_exec=4, ssh_query=3, remote_jobs=1
- route: `remote_reduce`

## E2 — An iterative session (catalog reuse)

### E2q1 — Cold: one field, three timesteps

```python
save(subsample(fields(timesteps(source("ssh://darwin/projects/autonomousvis/ashrestha/nyx_series"), 0, 2), ["native_fields/temperature"]), 4), "/Users/ashrestha/Projects/VisLang/bench/results/out/e2q1")
```

**Why it is in the paper.** The first question of a session. Nothing is cached, so this is the price of entry against which every later query is compared.

**Expected.** 3/3 (timestep,variable) extents fetched, 0 reused; ~24 MiB over the wire (128^3 x 4 B x 3 = 25,165,824 B).

**Outcome.** `OK`, 83.21s wall clock.

- source: 19.50 GiB across 3 timestep(s)
- over the wire: 24.0 MiB — **832.0x less than a whole-source copy**
- catalog: reused 0/3 (timestep,variable) extent(s)
- phases: probe_source 0.00s, list_timesteps 7.79s, catalog_delta 0.00s, login_node_inspect 8.39s, static_check 0.00s, ship_plan 15.20s, remote_exec 12.51s, pull 8.52s, sink_save_timeseries 0.01s
- top-level: remote_folder_reduce 68.26s
- orchestration: ssh_exec=7, ssh_query=2, remote_jobs=1
- route: `remote_folder_reduce`

### E2q2 — +1 field (per-variable delta)

```python
save(subsample(fields(timesteps(source("ssh://darwin/projects/autonomousvis/ashrestha/nyx_series"), 0, 2), ["native_fields/temperature", "native_fields/baryon_density"]), 4), "/Users/ashrestha/Projects/VisLang/bench/results/out/e2q2")
```

**Why it is in the paper.** The commonest follow-up: same view, one more variable. File-keyed caching cannot express this — it would refetch both fields.

**Expected.** 3/6 extents reused; only baryon_density crosses (~24 MiB), not temperature.

**Outcome.** `OK`, 77.3s wall clock.

- source: 19.50 GiB across 3 timestep(s)
- over the wire: 24.0 MiB — **832.0x less than a whole-source copy**
- catalog: reused 3/6 (timestep,variable) extent(s)
- phases: probe_source 0.00s, list_timesteps 7.28s, catalog_delta 0.01s, static_check 0.00s, ship_plan 15.46s, remote_exec 13.43s, pull 10.11s, sink_save_timeseries 0.02s
- top-level: remote_folder_reduce 61.50s
- orchestration: ssh_exec=6, ssh_query=2, remote_jobs=1
- route: `remote_folder_reduce`

### E2q3 — Narrower time range (per-timestep reuse)

```python
save(subsample(fields(timesteps(source("ssh://darwin/projects/autonomousvis/ashrestha/nyx_series"), 1, 1), ["native_fields/temperature", "native_fields/baryon_density"]), 4), "/Users/ashrestha/Projects/VisLang/bench/results/out/e2q3")
```

**Why it is in the paper.** Zooming in on one timestep of a range already held. The time axis is part of the cache key at per-timestep granularity.

**Expected.** 2/2 extents reused; nothing crosses the wire; no allocation needed.

**Outcome.** `OK`, 22.49s wall clock.

- source: 6.50 GiB across 1 timestep(s)
- over the wire: 0.0 MiB
- catalog: reused 2/2 (timestep,variable) extent(s)
- phases: probe_source 0.00s, list_timesteps 7.60s, catalog_delta 0.01s, static_check 0.00s, sink_save_timeseries 0.01s
- top-level: remote_folder_reduce 7.61s
- orchestration: ssh_exec=1, ssh_query=2
- route: `remote_folder_reduce`

### E2q4 — Verbatim re-issue (exact hit)

```python
save(subsample(fields(timesteps(source("ssh://darwin/projects/autonomousvis/ashrestha/nyx_series"), 0, 2), ["native_fields/temperature", "native_fields/baryon_density"]), 4), "/Users/ashrestha/Projects/VisLang/bench/results/out/e2q4")
```

**Why it is in the paper.** Re-running a query — the reload, the re-render, the second look. Should cost nothing on the network at all.

**Expected.** 6/6 extents reused; 0 bytes; seconds, not minutes.

**Outcome.** `OK`, 22.17s wall clock.

- source: 19.50 GiB across 3 timestep(s)
- over the wire: 0.0 MiB
- catalog: reused 6/6 (timestep,variable) extent(s)
- phases: probe_source 0.00s, list_timesteps 7.47s, catalog_delta 0.02s, static_check 0.00s, sink_save_timeseries 0.02s
- top-level: remote_folder_reduce 7.52s
- orchestration: ssh_exec=1, ssh_query=2
- route: `remote_folder_reduce`

### E2q5 — Sub-region of a cached extent (superset slicing)

```python
save(subsample(region(fields(timesteps(source("ssh://darwin/projects/autonomousvis/ashrestha/nyx_series"), 0, 2), ["native_fields/temperature", "native_fields/baryon_density"]), x=(0, 256)), 4), "/Users/ashrestha/Projects/VisLang/bench/results/out/e2q5")
```

**Why it is in the paper.** The strongest form of reuse and the one the paper claims in III-F: the request is NARROWER than what is cached, so it should be sliced locally out of the wider stored extent rather than refetched. fields() is kept so the request stays on the catalog-aware path (without a projection the folder reduce cannot know each file's variables and falls back to a non-catalog batch over all 13 fields).

**Expected.** the cached stride-4 extents contain and phase-align with x=(0,256), so they are reused; 0 bytes over the wire.

**Outcome.** `OK`, 23.62s wall clock.

- source: 19.50 GiB across 3 timestep(s)
- over the wire: 0.0 MiB
- catalog: reused 6/6 (timestep,variable) extent(s)
- phases: probe_source 0.00s, list_timesteps 7.50s, catalog_delta 0.02s, static_check 0.00s, sink_save_timeseries 0.01s
- top-level: remote_folder_reduce 7.53s
- orchestration: ssh_exec=1, ssh_query=2
- route: `remote_folder_reduce`

## E3 — What a rejected request costs

### E3a — Misspelled field (remote)

```python
save(fields(source("ssh://darwin/projects/autonomousvis/ashrestha/nyx_series/nyx512#1.hdf5"), ["native_fields/temperatur"]), "/Users/ashrestha/Projects/VisLang/bench/results/out/e3a.hdf5")
```

**Why it is in the paper.** The most frequent authoring error. Where the rejection lands — local metadata, remote metadata, or after a read — is the measurement.

**Expected.** rejected on schema, 0 bulk bytes; some seconds if the check needs the remote's login-node inspect.

**Outcome.** `FAILED`, 15.02s wall clock.

- source: 6.50 GiB
- over the wire: 0.0 MiB
- catalog: reused 0/1 variable extent(s)
- phases: probe_source 0.00s, catalog_delta 0.00s, static_check 0.00s
- top-level: remote_reduce 0.00s
- orchestration: ssh_query=2
- **spent before rejection: 15.0 s, 0.0 MiB moved**
- error: `ValueError: fields: ['native_fields/temperatur'] not available here; have ['derived_fields/HI_number_density', 'derived_fields/HeIII_number_density', 'derived_fields/tau_local', 'derived_fields/tau_real', 'derived_fields/tau_red', 'derived_fields/tau_red_heii', 'derived_fields/taubeta_red', 'native_`

### E3b — Region out of bounds (remote)

```python
save(region(fields(source("ssh://darwin/projects/autonomousvis/ashrestha/nyx_series/nyx512#1.hdf5"), ["native_fields/temperature"]), x=(0, 99999)), "/Users/ashrestha/Projects/VisLang/bench/results/out/e3b.hdf5")
```

**Why it is in the paper.** A plausible slip on an unfamiliar grid. validate_narrowing runs against the real extents before any read.

**Expected.** rejected against the 512^3 extent; 0 bulk bytes.

**Outcome.** `FAILED`, 14.79s wall clock.

- source: 6.50 GiB
- over the wire: 0.0 MiB
- catalog: reused 0/1 variable extent(s)
- phases: probe_source 0.00s, catalog_delta 0.00s, static_check 0.00s
- top-level: remote_reduce 0.00s
- orchestration: ssh_query=2
- **spent before rejection: 14.8 s, 0.0 MiB moved**
- error: `ValueError: region axis 0: [0:99999] out of bounds 0..512`

### E3c — Unknown axis (remote)

```python
save(subsample(fields(source("ssh://darwin/projects/autonomousvis/ashrestha/nyx_series/nyx512#1.hdf5"), ["native_fields/temperature"]), w=2), "/Users/ashrestha/Projects/VisLang/bench/results/out/e3c.hdf5")
```

**Why it is in the paper.** A form-level error: the DSL admits x/y/z only. Should be caught without touching the network at all.

**Expected.** rejected while building the AST; 0 bytes, sub-millisecond.

**Outcome.** `FAILED`, 15.52s wall clock.

- source: 6.50 GiB
- over the wire: 0.0 MiB
- catalog: reused 0/1 variable extent(s)
- phases: probe_source 0.00s, catalog_delta 0.00s, static_check 0.00s
- top-level: remote_reduce 0.00s
- orchestration: ssh_query=2
- **spent before rejection: 15.5 s, 0.0 MiB moved**
- error: `ValueError: unknown axis 'w'; this grid has 3 axes (x, y, z)`

### E3d — render() over a timeseries (remote folder)

```python
render(subsample(fields(timesteps(source("ssh://darwin/projects/autonomousvis/ashrestha/nyx_series"), 0, 2), ["native_fields/temperature"]), 4))
```

**Why it is in the paper.** An unsupported combination rather than a typo — the planner refuses it by construction instead of materializing 3 timesteps and then failing.

**Expected.** refused at plan time; 0 bytes.

**Outcome.** `FAILED`, 13.91s wall clock.

- over the wire: 0.0 MiB
- orchestration: ssh_query=2
- **spent before rejection: 13.9 s, 0.0 MiB moved**
- error: `NotImplementedError: render over a timeseries folder isn't supported — select one timestep with timesteps(node, N, N), or use save() to write the range.`
