# Step 0 — pre-flight (2026-10-31), metadata only

## The series (`../../data/stat_series.tsv`, from `stat -L` on gpu-server)
- 88 files `fire#N.vts` (symlinks to `wildfire/mountain_headcurve80/output.N.vts`):
  #1000–#5000 every 1000, #8000–#91000 every 1000, #50000 absent.
- Every file 1,098,002,905 bytes; the series 96,624,255,640 bytes.
- #5000 is the repaired copy (1,098,002,905 bytes, 2026-10-30); the truncated one
  (`output.5000.vts.truncated`, 291,804,142 bytes) is outside the series.

## Overview price (`sieve estimate`, nothing moved; `spec_strideN.py` → `estimate_strideN.txt`)
All 88 timesteps, fields u, v, w, theta, rhof_1. Sieve's "MB" is MiB (2^20 bytes).

| stride | points per timestep | over the wire (MiB) | of which data (MiB) | vs one whole file (1,047.1 MiB) |
|---|---|---|---|---|
| 8 | 75 × 63 × 8   | ~102 | ~63  | ~0.10× |
| 6 | 100 × 84 × 11 | ~248 | ~155 | ~0.24× |
| 4 | 150 × 125 × 16| ~806 | ~504 | ~0.77× |

"Over the wire" includes the grid's point positions (3 numbers per point, priced
at float32 since none are cached yet). Times in the estimate files are not used
(bytes only).

The spec carries `timesteps(fire, 1000, 91000)`: a dry run treats a remote source
as a series only when `timesteps()` says so (it does not probe the folder); the
ParaView Source writes the same form.
