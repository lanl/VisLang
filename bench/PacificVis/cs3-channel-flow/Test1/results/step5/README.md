# Step 5: the blocks that hold reverse flow, from the cache (2026-11-05, run e4ec9a4b70b6, Sieve commit d73bf0c)

Spec (`spec_as_run.py`): step 4's chain, written the same way, followed by `threshold(low, "u < 0")`.
Saved as `outputs/s5_backflow.vti`: the 640 x 480 x 96 grid, with NaN where a block's minimum is 0 or
above.

## Estimate before the run (`estimate.txt`)
- **Read on the server ~0 MB, over the wire ~0 MB.**
- The estimate's note: "served from the extent cache (a cached result of its start, with threshold
  u < 0.0 applied here): nothing is read next to the data and nothing crosses the wire."
- The same request before step 4 ran was priced at 898.8 GiB read on the server.

## The run (`run_report.txt`, `numbers.json`, `timing_record.json`)
- The run report says: "catalog: derived from a cached extent of its start (2 form(s)); applied
  here: threshold u < 0.0 — nothing read next to the data".
- **No remote reduce job.** There is no `remote_exec` phase and no `server_s`.
- **Measured read on the server: 0 B.** No job ran next to the data.
- **No bytes over the wire.** There is no `wire_bytes` counter.
- The only ssh commands were 2 metadata queries (`ssh_query`).
- `route` `catalog_derived`; `cached_bytes` 235,929,600.

## Checks
- **ParaView** (`paraview_check.txt`): a 640 x 480 x 96 ImageData with step 4's placement. `u`
  ranges from -0.2317 to -3.1e-9, and the provenance is in the file's field data.
- **Equal to a fresh threshold** (`census.txt`): NaN exactly where step 4's minimum is 0 or above,
  and step 4's values everywhere else.

## Census (`census.txt`)
28,118 of 29,491,200 blocks hold reverse flow, all within two block rows of a wall:

| Block row | Blocks | Share of the row | Median minimum | Lowest minimum |
|---|---|---|---|---|
| 0 (lower wall) | 14,004 | 4.559 % | -0.0016 | -0.1396 |
| 1 | 10 | 0.003 % | -0.0116 | -0.0567 |
| 94 | 20 | 0.007 % | -0.0118 | -0.0411 |
| 95 (upper wall) | 14,084 | 4.585 % | -0.0016 | -0.2317 |
