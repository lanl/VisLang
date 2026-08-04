# Sieve case study — session of 2026-08-04 15:18

Every row was produced by `run_pipeline` (the same entry point an 
interactive session uses) and measured by `vislang_timing.py`. 
Bytes labelled *over the wire* are what actually crossed the network; 
*source* is what a whole-file/whole-folder copy would have moved.

## E1 — Data movement: one request, two routes (reduce next to the data vs fetch whole and narrow locally)

### E1a — Reduced next to the data — one batched remote job

```python
save(threshold(region(fields(timesteps(source("ssh://darwin//projects/autonomousvis/ashrestha/nyx_series/"), 0, 2), ['native_fields/baryon_density', 'native_fields/dark_matter_density']), x=(100, 220), y=(40, 160), z=(40, 160)), "native_fields/baryon_density > 1.0"), "/Users/ashrestha/Projects/VisLang/bench/results/out/e1_region")
```

**Why it is in the paper.** The headline movement number, on a timeseries rather than one snapshot: 2 of 13 fields, a 120^3 sub-box and a value cut, evaluated on darwin so only the surviving voxels cross. The denominator is E1b below — the same spec, same runtime, remote reduce switched off.

**Expected.** ~41 MB over the wire (120^3 x 4 B x 2 fields x 3 timesteps = 41,472,000 B before the NaN-mask, which preserves shape), reused 0/6 extents on a cold catalog, ONE srun step and ONE directory pull for all three timesteps.

**Outcome.** `OK`, 97.38s wall clock.

- source: 19.50 GiB across 3 timestep(s)
- over the wire: 39.5 MiB — **504.8x less than a whole-source copy**
- catalog: reused 0/6 (timestep,variable) extent(s)
- phases: probe_source 0.00s, list_timesteps 7.57s, catalog_delta 0.00s, login_node_inspect 8.87s, static_check 0.00s, ship_plan 15.24s, remote_exec 14.90s, pull 10.44s, sink_save_timeseries 0.06s
- top-level: remote_folder_reduce 72.62s
- orchestration: ssh_exec=7, ssh_query=2, remote_jobs=1
- route: `remote_folder_reduce`

### E1b — Fetched whole, reduced locally — the baseline

```python
save(threshold(region(fields(timesteps(source("ssh://darwin//projects/autonomousvis/ashrestha/nyx_series/"), 0, 2), ['native_fields/baryon_density', 'native_fields/dark_matter_density']), x=(100, 220), y=(40, 160), z=(40, 160)), "native_fields/baryon_density > 1.0"), "/Users/ashrestha/Projects/VisLang/bench/results/out/e1_region")
```

**Why it is in the paper.** What an ad-hoc copy costs, produced by the same runtime and the same spec rather than asserted as a denominator. VISLANG_REMOTE=off routes the identical chain down the whole-FOLDER fetch (planner._plan_remote_folder falls through to _fetch_remote_folder), so the narrowing runs here.

**Expected.** ~20.94 GB over the wire — the entire series, every field, every timestep — for the same ~41 MB result, a ~505x transfer penalty; wall clock dominated entirely by the rsync, plus 20.94 GB of local disk under .vislang/downloads/. Note the local materialize reads a file rsync wrote seconds earlier, so it is served warm from the page cache (cf. E1c_local).

**Outcome.** `OK`, 1262.76s wall clock.

- over the wire: 19.50 GiB
- estimate: 39.6 MiB over the wire
- phases: transfer_dir 1246.39s
- top-level: fetch_whole_folder 1246.39s, inspect 0.01s, lower 0.00s, materialize_timeseries 0.31s, sink_save_timeseries 0.01s
- orchestration: ssh_query=2
- route: `whole_folder_fetch`

## E1local — The same request without remote reduce (whole-file fetch, local narrowing)

### E1a_local — Whole-file fetch — cost gate holds

```python
save(subsample(fields(source("ssh://darwin/projects/autonomousvis/ashrestha/nyx_series/nyx512#1.hdf5"), ['native_fields/temperature', 'native_fields/baryon_density']), 2), "/Users/ashrestha/Projects/VisLang/bench/results/out/e1_local_sub2.hdf5")
```

**Why it is in the paper.** On the fetch path the gate prices the WHOLE file from one stat plus a bandwidth probe — there is no narrowing to discount, because the narrowing happens after the bytes land. A HELD run is the claim that Sieve refuses the expensive route before paying for it, not after.

**Expected.** HELD over budget (1 GiB / 3 s defaults); 0 bulk bytes; an estimate of ~6.98 GB over the wire against a ~128 MB result.

**Outcome.** `NEEDS CONFIRM`, 30.16s wall clock.

- over the wire: 0.0 MiB
- estimate: 6.50 GiB over the wire, predicted 6174–24697 s  ⚠ over budget
- orchestration: ssh_query=2
- route: `held_budget`, held on budget

### E1b_local — Whole-file fetch — confirmed, reduced locally

```python
save(subsample(fields(source("ssh://darwin/projects/autonomousvis/ashrestha/nyx_series/nyx512#1.hdf5"), ['native_fields/temperature', 'native_fields/baryon_density']), 2), "/Users/ashrestha/Projects/VisLang/bench/results/out/e1_local_sub2.hdf5")
```

**Why it is in the paper.** The baseline the headline number is measured against: the same 2-of-13 fields at stride 2, but reduced HERE instead of there. This is what an ad-hoc copy costs, produced by the same runtime and the same spec rather than asserted as a denominator.

**Expected.** ~6.98 GB over the wire (the entire file) for the same ~128 MB result E1b of the NYX study got for ~128 MB — a ~52x transfer penalty; wall clock dominated entirely by the download, plus 6.98 GB of local disk.

**Outcome.** `OK`, 748.21s wall clock.

- source: 6.50 GiB
- over the wire: 6.50 GiB — **1.0x less than a whole-source copy**
- estimate: 128.0 MiB over the wire
- phases: transfer 710.09s
- top-level: fetch_whole_file 716.77s, inspect 0.11s, materialize 0.49s, sink_save 0.02s
- orchestration: ssh_query=3
- route: `whole_file_fetch`
