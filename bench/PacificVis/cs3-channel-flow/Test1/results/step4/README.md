# Step 4: block-minimum overview of the whole channel (2026-11-05, run bc3ed21606b4, Sieve commit f765b3f)

Spec (`spec_as_run.py`): `coarsen(region(src, z=(1, 1535)), 16, how="min")`. Every interior level, in
blocks of 16 x 16 x 16 cells, keeping each block's minimum. Saved as `outputs/s4_min16.vti`: 640 x 480 x 96
blocks, 236 MB, from a 966 GB file.

The run was held by the read budget before it ran (`held_report.txt`, `held_timing_record.json`). The
user approved that hold ("confirm"). The first attempt (`attempt1_interrupted/`) was killed after
about 12 minutes, when the Claude session that started it ended. This run was started detached from
the session (`started_local.txt`: 04:13:22 to 07:06:15 local, exit 0, `run_report.txt`).

## Bytes and server time (`numbers.json`, from `timing_record.json`)

| | Estimated | Measured |
|---|---|---|
| Read on the server | 965,109,350,400 B (898.8 GiB) | 943,219,060,736 B (-2.3 %) |
| Over the wire | 235,929,600 B | 235,930,686 B |
| Server time (`server_s`) | n/a | 10,344.78 s (`materialize` 10,344.33 s) |

- **Page cache before the run:** 41,555,308,544 B of the file was already resident
  (`page_cache_before.txt`, 04:13:11). These pages came from steps 1 to 3b and from the interrupted
  attempt.
- **Estimate vs measured:** the measured read plus the resident pages is 984.8 GB, which is 2.0 %
  above the estimate. That is consistent with the small read-ahead overhead of a near-sequential
  read (the contiguous plane in step 3b measured 1.0004x).
- **Read rate:** about 91 MB/s on average (943.2 GB / 10,344.8 s). This is the NFS mount's
  sequential rate.

## Checks
- **ParaView** (`paraview_check.txt`): a 640 x 480 x 96 ImageData. Origin (7.5, 7.5, 8.5), spacing 16,
  at the block centres in source cells. `u` ranges from -0.2317 to 1.2099, and the provenance is in
  the file's field data.
- **Against the ground truth:** the 64 x 64 x 4 blocks over the step-2 area equal step 3's
  cache-derived block minima exactly.
- **Against the stride overview:** every block minimum is at or below that block's stride sample.

## What it shows
- **Blocks with a minimum below 0:**
  - block row 0 (lower wall): 14,004 of 307,200 (4.56 %);
  - block row 95 (upper wall): 14,084 (4.58 %);
  - row 1: 10;
  - row 94: 20;
  - no other row.

  Reverse flow stays within about 30 cells of either wall. This matches the area result (reach
  z = 18, y+ 9.67).
- **The stride overview** finds 297 negative samples on the wall plane, against these 14,004 blocks.
- **Large-scale association** (`results/eval/largescale.txt`, measure c): 55.3 % of the lower wall's
  negative blocks lie beneath u'_L > 0 (n = 14,004), and 54.4 % on the upper wall (n = 14,084).
  Chance is 50.3 % and 49.5 %. The spanwise cut-offs delta/8 and delta/2 give 52.4-55.6 %.
  Lenaers et al. (2012) report 69 % of backflow points.
