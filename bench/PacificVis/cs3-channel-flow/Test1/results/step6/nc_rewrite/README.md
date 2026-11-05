# Step 6, netCDF rewrite (2026-11-05, run 8a706c6ceb71, Sieve commit 13cf804)

Step 6's netCDF-4 copy had no coordinate variables. ParaView opened it unplaced, and its own
provenance record stated a placement the file did not hold (`../README.md`). Two Sieve commits fixed
this, each with tests and with all suites passing:
- `2171fdd` writes x, y, z coordinate variables for a placement in source cells. They are labelled
  as cell indices: `units = "1"`, `sieve_units = "source cells"`, global `sieve_in_cells = 1`.
- `13cf804` drops the CF `axis` attribute. ParaView's CF reader took `axis = X/Y` as
  longitude/latitude and bent the grid into a spherical shell (bounds +-1535). Seen on a first
  rewrite at `2171fdd`.

The copy was then written again (`spec_as_run.py`, the same request as step 4).

## Bytes
- **Estimate** (`estimate.txt`): ~0 MB read on the server, ~0 MB over the wire, "served from the
  extent cache".
- **Run** (`run_report.txt`, `numbers.json`):
  - route `catalog_full_hit`;
  - no remote job, no `server_s`;
  - measured read on the server 0 B; no `wire_bytes`;
  - `cached_bytes` 235,929,672.

## Checks
- **Data** (`checks.txt`):
  - `u` (x, y, z) equals step 4's `.vti` element for element;
  - `data_sha256` is `59228478a3bbf986`, as in step 4;
  - coordinates x = 7.5, 23.5, …, 10231.5, y = 7.5 … 7671.5, z = 8.5 … 1528.5: the block centres
    in source cells, as in the `.vti`;
  - the record's explanation names the coordinate variables and says they are cell indices.
- **ParaView** (`paraview_check_nc.txt`, `scripts/check_nc_pv.py`, generic `NetCDFReader` named
  explicitly): opens as ImageData with point data and range -0.2317 to 1.2099. It is placed at the
  source cells, but **with the axes in reverse order**: ParaView's X is the channel's z (bounds
  8.5-1528.5).
  - The reverse order comes from Sieve's netCDF writer, which stores the x axis first by design,
    so that the file agrees with the `region(x=…)` that cut it and with Sieve's read-back.
  - ParaView's CF reader takes the last dimension as its X.
  - Opening the file by extension still picks ParaView's EAM reader, which fails.
- **Decision:** the `.vti` remains the output for ParaView. The `.nc` serves readers without VTK
  (netCDF4, xarray, h5py), which see named dimensions and the coordinate variables.
