# Step 1 — the overview (2026-10-31)

ParaView session `.vislang/paraview/20261031-013204-652ebb/`, step `002-source-overview`
(run id `6486dfcfb69c`). Sieve Source over the series folder, Timesteps empty (all 88),
fields u, v, w, theta, rhof_1, Stride 8, default budget. No hold.
The spec Sieve ran: `overview_spec_as_run.py`. Records copied here: `overview_result.json`,
`overview_timings.jsonl`, `session.json`. Output (88 .vts) stays in the session folder.

## Bytes (from overview_result.json / overview_timings.jsonl)
| | bytes |
|---|---|
| over the wire (data + grid positions) | 146,495,298 (139.7 MiB) |
| bandwidth probe (sent before the fetch) | 4,194,304 |
| read on the server (88 whole files) | 96,624,255,640 |
| one whole file, for scale | 1,098,002,905 |

Over the wire = 0.133 of one whole file, for all 88 timesteps.
Sieve predicted 101.5 MiB: the grid positions were priced as float32 because none were
cached yet; this file stores them as float64 (37,800 points × (5×4 + 3×8) B × 88 =
146,361,600 B of arrays). Later requests size positions from the cache.

## Is the front found at stride 8? (`stride_check.txt`, `scripts/stride_check.py`)
Compared with `data/fire_roi_bounds.csv` (full resolution, theta > 400 K, padded 30 m):
- Fire found at all 83 timesteps that have fire at full resolution; none where it has none.
- Downwind edge, interpolated between grid points: median 4.8 m behind full resolution
  (mean 7.8, max 37.9 m); within one stride (16 m) at 84% of timesteps. Correlation
  0.996 over 390 m of travel.
- Crest (x = −2 m) crossing of that edge: #16923 at full resolution, #17993 at stride 8.
Verdict: stride 8 is enough to find "when"; the window is chosen with margin around it.

## Screenshot
`figures/screenshot1_overview_working.png` — the working record (timestep #91000). To be
retaken for the paper once the Status line shows bytes only.
