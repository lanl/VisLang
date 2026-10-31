# Step 3 — the Detail (2026-10-31)

ParaView session `.vislang/paraview/20261031-013204-652ebb/`, step `005-detail-detail`
(run id `0cd7848242ca`). Source Timesteps `4000,9000-45000` (38); Sieve Detail on Data:
Box Corner (−100, −120, 0), Box Size (300, 240, 350), Use Reference Bounds off,
Stride 1, Fields u, v, w, theta. No hold. Spec as run: `detail_spec_as_run.py`.

## Bytes (detail_result.json / detail_timings.jsonl)
| | bytes |
|---|---|
| over the wire (data + grid positions) | 1,083,152,914 (1,033.0 MiB) |
| bandwidth probe | 4,194,304 |
| read on the server (38 whole files) | 41,724,110,390 |
| Sieve's price before the run (sized on the overview's cached positions) | 1,090.6 MiB (+5.6%) |
| the same box under the old pricing (whole grid) | ~11 GB → would have been held |

The cut: 151 × 121 × 39 points per timestep (x −100…200, y −120…120 m; levels up to
209 m above the crest). 712,569 points × (4 fields × 4 B + 3 × 8 B positions) × 38 =
1,083,104,880 B. Positions are 60% of it: they are sent with every timestep though the
terrain does not move (future work: send them once).
Near the ground: levels at 0, 1.2, 2.4, 3.7, 5.0, 6.4, 7.9, 9.5, 11.3 m above the crest
— 8 levels below 11.3 m, where the stride-8 overview has 2 (0 and 11.3 m).

## Asides in the session (kept as records; not part of the case study's numbers)
- `003-detail-detail` (`aside_003-detail-detail_spec.py`): the first Detail Apply, with
  the Detail's Fields left at their default (all nine variables) — the Detail does not
  take the Source's fields. Cancelled while the server read; nothing crossed the wire.
- `004-source-overview` (`aside_004-source-overview_spec.py`): the Source switched to
  u, v, w, theta at stride 1 over the 38 timesteps — the whole grid at full resolution,
  priced 18,569 MiB and held over budget; not approved, nothing moved.
Plugin follow-up: a Detail's Fields should default to its Source's.

## Screenshots
`figures/screenshot2_detail_working.png` (the box on the overview, Detail panel),
`screenshot2b_detail_running.png` (Fields/Stride/Status while fetching),
`screenshot2c_detail_done.png` (Status after: 38 timesteps; the cut's real, terrain-
following outline beside the drawn box).
