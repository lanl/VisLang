# Step 7 (post hoc): the large-scale field at two more heights, and the filter sensitivity (2026-11-05, run c5316d0c139c, Sieve commit d7b8612)

**Post hoc.** This step was added after step 4's large-scale result (55 % against 50 % chance;
`results/eval/largescale.txt`) and approved by the user on 2026-11-05. The pre-registered analysis
is unchanged. Results from this step must be labelled post hoc in the paper.

## The requests (`spec_as_run.py`)
The same request as step 3b (block means of one plane per wall, 16 x 16 cells), at:
- **y+ 281.38** (z = 147 / 1388): Mathis et al. (2009)'s outer site. It was already in the design
  (LITERATURE.md section 5) as a second reference height.
- **y+ 519.51** (z = 207 / 1328): y = 0.1 delta, Lenaers et al. (2012)'s height (y+ 100 at Re_tau
  1000) in outer units.

Outputs: `outputs/s7_{lower,upper}_yp{281,519}.vti`, each 640 x 480 blocks. ParaView opens all four
at their source cells (`paraview_check.txt`).

## Bytes and server time (`per_request.json`, from `timing_record.json`)

| Plane | Est. read on the server | Measured read on the server | Est. over the wire | Server time (`server_s`) |
|---|---|---|---|---|
| Lower, z = 147 | 629,145,600 B | 631,234,560 B (+0.33 %) | 2,457,600 B | 5.88 s |
| Upper, z = 1388 | 629,145,600 B | 0 B (in the page cache) | 2,457,600 B | 1.33 s |
| Lower, z = 207 | 629,145,600 B | 629,407,744 B (+0.04 %) | 2,457,600 B | 5.57 s |
| Upper, z = 1328 | 629,145,600 B | 0 B (in the page cache) | 2,457,600 B | 1.28 s |

- **Over the wire, all four:** 9,834,744 B (`wire_bytes`).
- **Page cache before the run** (`page_cache_before.txt`): 325.5 GiB of the file was resident.
  Step 4 read the file from the lower wall upwards, so the upper planes were still in memory.
  Lower planes cold: estimate matched to 0.04-0.33 %.

## Sensitivity (`largescale_posthoc.txt`, `.json`; `scripts/largescale_posthoc.py`)
The table gives the fraction of backflow beneath u'_L > 0, with chance in brackets.
- **Block columns:** step 4's blocks with minimum < 0 (n = 14,004 lower, 14,084 upper).
- **Area points:** the ground-truth area at z = 1 (n = 1,326).

| Height | Filter | Blocks, lower | Blocks, upper | Area points |
|---|---|---|---|---|
| y+ 100 | outer, sharp (pre-registered) | 55.3 % (50.3) | 54.4 % (49.5) | 54.0 % (50.3) |
| y+ 100 | viscous, sharp | 63.9 % (49.4) | 62.9 % (49.1) | 69.0 % (49.4) |
| y+ 100 | viscous, Gaussian | 62.8 % (49.6) | 61.8 % (49.2) | 69.6 % (49.6) |
| y+ 281 | outer, sharp | 60.0 % (50.2) | 60.0 % (50.1) | 61.3 % (50.2) |
| y+ 281 | viscous, sharp | 63.3 % (50.4) | 62.9 % (50.3) | 67.0 % (50.4) |
| y+ 519 | outer, sharp | 60.7 % (50.7) | 61.6 % (51.0) | 67.6 % (50.7) |
| y+ 519 | viscous, sharp | 61.0 % (51.3) | 61.1 % (51.3) | 67.5 % (51.3) |

The filters:
- **outer:** keeps lambda_x > delta, lambda_z > delta/4;
- **viscous:** keeps lambda_x+ > 1000, lambda_z+ > 250 (delta and delta/4 at Re_tau 1000);
- **Gaussian:** half power at the same cut-offs.

All twelve rows, including the stride's points (n = 297, closest to chance), are in the `.txt`.

## Reading (post hoc)
- Backflow lies beneath positive large-scale u at every height and with every filter: 54-64 % of
  wall blocks against about 50 % chance.
- **The association is strongest when the filter keeps motions down to about 1000 viscous units**
  (the viscous cut-off). There it reaches 62-64 % of blocks, and 67-70 % of area points, close to
  Lenaers' 69 %.
- With the filter fixed in outer units (lambda_x > delta = 5186 viscous units), the association
  appears at y+ 281 and 519 (60-62 % of blocks), but is weak at y+ 100 (54-55 %).
- So the scales that carry the association at Re_tau 1000 are, in viscous units, sub-delta motions
  at Re_tau 5186.
- **Caveats:**
  - post hoc;
  - one snapshot;
  - neighbouring blocks are not independent, so the block counts overstate the effective sample
    size;
  - the area points come from one 13,000 x 6,500 wall-unit patch.
