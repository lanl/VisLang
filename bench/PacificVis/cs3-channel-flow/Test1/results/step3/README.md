# Step 3: block means and minima of the ground-truth area, from the cache (2026-11-05, run 95a87e87593a)

Spec (`spec_as_run.py`): the step 2 box, then `coarsen(area, 16, how="mean")` and
`coarsen(area, 16, how="min")`, two saves (`outputs/s3_area_mean16.vti`, `s3_area_min16.vti`;
64 x 64 x 4 blocks each).

## Estimate before the run (`estimate.txt`, Sieve commit b7e97c3)

Both requests: **read on the server ~0 MB, over the wire ~0 MB**. Note: "served from the extent cache
(a cached result of its start, with coarsen 16 mean / min applied here): nothing is read next to the
data and nothing crosses the wire."

(A first dry run, before commit b7e97c3, wrongly reported "nothing for this request is in the extent
cache". The catalog's schema version had just moved to 5 (commit 4ca9724), and the dry run did not
fall back to the older schema entry. Fixed, with a test in `tests/test_cache_derive.py`. The estimate
above is from after the fix.)

## The run (`run_report.txt`, `numbers.json`, `timing_record.json`)

- Each request: "catalog: derived from a cached extent of its start (1 form(s)); applied here:
  coarsen 16 — nothing read next to the data".
- **No remote reduce job** (no `remote_exec` phase); **no bytes over the wire** (no `wire_bytes`
  counter); `cached_bytes` 131,072 per request (64 x 64 x 4 float64).
- The only ssh commands were metadata: a stat/header probe and one login-node inspect (`ssh_exec` 1),
  which re-read the schema after the catalog version change.
- The run record labels the route `catalog_full_hit`. That is a labelling bug, fixed in Sieve commit
  7a657f9: a result derived from a cached start is now recorded as `catalog_derived`. The run report's
  steps show what happened.
