# Step 6 — replay (2026-10-31)

`python bench/replay_session.py .vislang/paraview/20261031-013204-652ebb --setups cold,warm
--out bench/PacificVis/cs2-wildfire/Test2/outputs/replay` (outputs and caches stay there,
gitignored). The session's two completed requests (overview, Detail) were replayed with
the think time removed; the cancelled Detail and the held Source are skipped.
`full-download` was not run: its bytes are the stat totals in the cost table.

| setup | step | over the wire | pieces from the cache | reproduces the session's output |
|---|---|---|---|---|
| cold (empty cache) | overview | 146,495,298 B | 0 / 440 | yes (data_sha256 43eecfabd13bb8a5) |
| cold | Detail | 1,083,152,914 B | 0 / 152 | yes (98fe2e84e8de179c) |
| warm (cold's cache) | overview | 0 B | 440 / 440 | yes |
| warm | Detail | 0 B | 152 / 152 | yes |

- Cold moves exactly the session's bytes again; every output reproduces bit for bit.
- Warm (a revisit): nothing crosses, no remote job runs (one ssh listing per step), nothing
  is read on gpu-server — `timings_warm.jsonl`: fetched_timesteps 0, cached 88 and 38.
- Estimate vs actual (cold): overview −27.3% (positions priced as float32: no cache yet),
  Detail +5.6% — `summarize_cold.txt`. Times in these files are not used (bytes only).
Files: `replay.json`, `replay.csv`, `summary.txt`, `timings_{cold,warm}.jsonl`,
`summarize_{cold,warm}.txt`, `csv_{cold,warm}/` (bench/summarize.py --csv).
