# Column dictionary for the case-study CSVs

Written by `bench/case_study.py`; regenerate the tables any time with
`python bench/summarize.py --file bench/results/timings.jsonl --csv bench/results`.

Read `session.md` first — it narrates each query. These CSVs are the same runs in
plottable form.

## runs.csv — one row per pipeline
| column | meaning |
|---|---|
| `started` | ISO timestamp of the run |
| `site` | `local` or `remote` — where the data lived |
| `route` | what the planner chose: `remote_reduce`, `remote_folder_reduce`, `catalog_full_hit`, `whole_file_fetch`, `held_budget`, `held_allocation` |
| `forms` | the narrowing forms as written, in order |
| `n_timesteps` | timesteps selected |
| `source_mb` | size of the source a naive copy would have moved (the baseline denominator) |
| `wire_mb` | bytes that actually crossed the network |
| `reduction_x` | `source_mb / wire_mb` — the headline movement factor |
| `total_s` | end-to-end wall clock for the run |
| `inspect_s` | schema read (metadata only) |
| `remote_exec_s` | the reduce running next to the data |
| `pull_s` | transferring the reduced result home |
| `materialize_s` | assembling arrays in local memory |
| `sink_s` | writing the output file |
| `ssh_exec` | ssh command round trips — orchestration cost, hardware-independent |
| `remote_jobs` | scheduler (`srun`) steps launched — 1 means one batched job |
| `status` | `OK`, `FAILED`, `NEEDS CONFIRM`, `NEEDS ALLOCATION` |

## cache.csv — reuse across successive queries
`reused_pairs / total_pairs` counts (timestep, variable) extents on the folder
path; `cached_vars / want_vars` counts variables on the single-file path.
`reuse_pct` is the percentage served locally. Read down the rows in time order:
that column rising while `wire_mb` falls to zero *is* the iterative-workflow
result.

## estimate.csv — predicted vs actual
`est_read_mb` vs `wire_mb` with `err_pct` (positive = the estimate was high);
`est_time_lo_s`/`est_time_hi_s` vs the measured `pull_s`, with `in_band` =
`yes`/`low`/`high`. `probe_bw_mbps` is the link speed measured at gate time —
the input the time band was derived from, so a missed band can be attributed to
link variance rather than bad math. `est_over_budget` is whether the gate fired.

## errors.csv — cost of a rejected request
`total_s` is seconds spent before the rejection, `wire_mb` the bytes moved by
then (0.0 is the point), `error` the message the author saw. `site` tells you
whether the check was answered from local or remote metadata.
