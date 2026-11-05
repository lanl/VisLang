# Step 6: reproduce step 4 from its output, plus a netCDF-4 copy (2026-11-05, run 55135d3ac77b, Sieve commit 53ef52f)

## Recovering the spec
- `provenance(outputs/s4_min16.vti, spec_only=True)` returned step 4's spec exactly
  (`recovered_spec.py`; identical to `results/step4/spec_as_run.py`).
- The full record names:
  - the source (path, size 966,367,641,600 B, modification time, sha256 of its first 64 KiB);
  - the output's `data_sha256` (`59228478a3bbf986`);
  - the run (`bc3ed21606b4`);
  - the placement (origin 7.5, 7.5, 8.5, spacing 16, "source cells");
  - the explanation: "Values: block minima of the source cells (computed, not source values)".
- **One discrepancy:** the record's Sieve commit is `03052c0`, while the run record says `f765b3f`.
  The run started at `f765b3f`, and `03052c0` was committed during the run. It changed only
  DESIGN.md: `git diff f765b3f 03052c0 -- vislang plugins` is empty.

## The spec (`spec_as_run.py`)
The recovered spec, with two changes:
- step 4's save is renamed to `outputs/s6_replay_min16.vti`, so the original file is kept;
- a second `save(low, "outputs/s6_min16.nc")` writes the same result as netCDF-4.

## Estimate before the run (`estimate.txt`)
- **Each save: read on the server ~0 MB, over the wire ~0 MB.**
- The estimate's note: "served from the extent cache (cached results): nothing is read next to
  the data and nothing crosses the wire."

## The run (`run_report.txt`, `numbers.json`, `timing_record.json`)
- The run report says, for each save: "catalog: cached ['u'] for this narrowing … full hit —
  nothing crossed the wire".
- `route` `catalog_full_hit`, an exact match of step 4's request.
- **No remote reduce job** and no `server_s`.
- **Measured read on the server: 0 B.**
- **No bytes over the wire.** There is no `wire_bytes` counter.
- The only ssh commands were 2 metadata queries.
- `cached_bytes` 235,929,672 per save.
- Step 4 took 10,344.78 s on the server. The replay's provenance gives `took_s` 3 s for the
  `.vti` and 8 s for the `.nc`; these are local times, not server times.

## Checks (`checks.txt`)
- **Replay `.vti` equals step 4's `.vti`:**
  - the array is equal (sha256 of the [x, y, z] array `cd85c162…` both);
  - both provenance records hold `data_sha256` `59228478a3bbf986`.
- **The `.nc` equals step 4's `.vti`:**
  - variable `u` (640, 480, 96) float64, dimensions x, y, z, equal element for element;
  - its record holds the same `data_sha256`;
  - the provenance record is in the root attribute `sieve_provenance`, with a CF-style `history`
    line;
  - the provenance tool reads it, and it says the columns came from the cache.
- **ParaView, `.vti`** (`paraview_check.txt`): the same as step 4.
- **ParaView, `.nc`** (`paraview_check_nc.txt`):
  - opening by extension picks the EAM reader, which fails. The generic netCDF reader
    (`NetCDFReader`) has to be named explicitly (`scripts/check_nc_pv.py`);
  - it then opens as ImageData with dimensions (96, 480, 640): axes reversed, origin 0, spacing 1;
  - the file has no coordinate variables, only dimensions, so the placement in source cells that
    the `.vti` carries is not in the `.nc`. The record states it (`geometry: … units: "source
    cells"`), but the file's own coordinates do not.

  The `.vti` stays the output for ParaView. The `.nc` is the copy for readers without VTK
  (h5py/xarray).
