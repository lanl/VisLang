# Step 6 — replay of the Test3 session (2026-10-31)

`python bench/replay_session.py .vislang/paraview/20261031-175301-ba2a2a --setups cold,warm
--out bench/PacificVis/cs2-wildfire/Test3/outputs/replay` (outputs and caches there, gitignored).

| setup | step | over the wire | pieces from the cache | reproduces the session's output |
|---|---|---|---|---|
| cold (empty cache) | overview | 47,138,910 B | 0 / 440 | yes (data_sha256 43eecfabd13bb8a5) |
| cold | Detail | 364,936,204 B | 0 / 152 | yes (98fe2e84e8de179c) |
| warm (cold's cache) | overview | 0 B | 440 / 440 | yes |
| warm | Detail | 0 B | 152 / 152 | yes |

- Cold moves exactly the session's bytes; both outputs reproduce bit for bit.
- The data fingerprints equal Test2's replay (same two hashes): sending positions once and
  compressing changes what crosses the wire, not what arrives.
- Warm, a revisit: nothing crosses, no remote job, nothing read on gpu-server.
- Estimate vs actual (cold): overview +126% (no cached positions yet, so they are priced per
  timestep — an upper bound), Detail +30% (compression is not priced) — `summarize_cold.txt`.
  Times in these files are not used (bytes only).
