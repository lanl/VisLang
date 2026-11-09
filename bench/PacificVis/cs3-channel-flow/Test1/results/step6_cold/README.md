# Step 6 (cold): re-run step 1 from its provenance record (2026-11-09, run 261e048050c3)

Replaces the earlier step 6 (`../step6/`, a re-run of step 4 served whole from Sieve's cache, 0 B read)
as the paper's reproducibility check: this one reads the source again.

## Recovering the spec
- `provenance(outputs/s1_stride16.vti, spec_only=True)` returned step 1's spec.
- `spec_as_run.py` is that spec with one change: the save is renamed to
  `outputs/s6_cold_stride16.vti`, so step 1's file is kept.

## Cold conditions
- **Sieve's cache empty:** `VISLANG_CACHE` and `VISLANG_DOWNLOADS` pointed at new, empty directories.
  The estimate says "nothing for this request is in the extent cache" (`estimate.txt`); the run
  record has `cached_bytes` 0.
- **Server page cache evicted** for the file (`posix_fadvise(DONTNEED)`): 9.4 GB resident before,
  0 B after (`page_cache_before.txt`).

## Numbers (`numbers.json`, `timing_record.json`; via `../../scripts/record_step.py`)

| | Step 1 (2026-11-05) | This re-run |
|---|---|---|
| Estimated read on the server | 3,769,344,000 B | 3,769,344,000 B |
| Measured read on the server | 18,094,653,440 B | 18,119,561,216 B |
| Over the wire | 235,930,686 B | 235,930,686 B |
| Server time (`server_s`) | 376.36 s | 381.47 s |

## Result (`checks.txt`)
- `data_sha256` in both provenance records: `72338725d3eb38a3`.
- The `u` arrays are bitwise equal (sha256 `f61dd55b…` both); same dims, origin and spacing.
- The record's `input.fingerprint` (size, mtime, head-64 KiB hash) is unchanged, so the source
  did not change between the runs.
- The record notes `sieve.commit 354d716, uncommitted_changes: true`: the working tree had
  edits to `vislang/runtime/provenance.py` (record formatting only) and tests.
