# Case Study 2 (wildfire) — Test3: the paper run with a fixed grid's positions sent once

Same study as Test2 — same data, overview (all 88 timesteps, stride 8, u v w theta rhof_1),
window (#4000, #9000–#45000), box (x −100…200, y −120…120, z 0…350 m), four fields,
same analysis — with one change in Sieve (commit 1e9155b): a fixed grid's point positions
cross the wire once per series instead of once per timestep (matched by content, never
assumed), and the remote bundle is compressed. **Bytes only**, every number from a file here.

ParaView session `.vislang/paraview/20261031-175301-ba2a2a/` (copied: `results/session.json[l]`),
started with `VISLANG_CACHE=.vislang/cache-test3` (empty) and `VISLANG_STATUS_TIMES=0`, from
`paraview/cs2_test3_overview_start.pvsm`. gpu-server checkout at 1e9155b (`git log` there).

## Bytes (results/step1, results/step3; `results/cost_table.md` by scripts/cost_table.py)
| | Test2 | Test3 | Test3 vs Test2 |
|---|---|---|---|
| overview over the wire | 146,495,298 | **47,138,910** | 3.1× less |
| Detail over the wire | 1,083,152,914 | **364,936,204** | 3.0× less |
| Sieve total | 1,229,648,212 | **412,075,114** | 3.0× less |
| read on gpu-server | 138,348,366,030 | 138,348,366,030 | same |
| Sieve's price, overview / Detail | 101.5 / 1,090.6 MiB | 101.5 / 453.7 MiB | |
- The run logs: "87 piece(s) share positions already in the bundle — sent once" (overview),
  "37 piece(s) …" (Detail).
- **The data is unchanged:** every output's data_sha256 equals Test2's — overview 88/88,
  Detail 38/38 timesteps (and the Detail's points at #30000). So Test2's science, figures and
  numbers (front, speeds, the lee-slope wind) hold for Test3 as they are.
- Price vs actual: overview 101.5 MiB predicted, 45.0 moved (before any overview, positions
  are priced per timestep — an upper bound); Detail 453.7 predicted (positions once, sized on
  the overview's cache), 348.0 moved (compression is not priced). Both err high.

## Against the baselines (results/cost_table.md)
| route | over the wire | vs Sieve (0.41 GB) |
|---|---|---|
| two-pass download (18 + 31 whole files) | 53.80 GB | 130.6× |
| download everything (88 files) | 96.62 GB | 234.5× |
| work on the server, copy back the 38 cuts | 0.61 GB | 1.5× |
Overview alone: 0.047 GB for 88 timesteps = 1/23 of one 1.1 GB file; the two-pass first pass
costs 19.76 GB for 18 timesteps (419× the bytes). Still true and said plainly: Sieve reads
138 GB on gpu-server (whole files), more than the two-pass route's 54 GB.

## Replay (results/step6)
Cold, from an empty cache: the same bytes as the session (47,138,910 + 364,936,204 B), both
outputs bit-identical to the session's — and to Test2's (same data_sha256). Warm, a revisit:
0 bytes, every piece from the catalog (440/440, 152/152), no remote job.

## On the way (session folders kept as records; not part of the numbers)
- `20261031-173133-ee0d88`, `-174133-504952`, `-174233-b1828a`, `-174820-a08d04`: Test3 starts
  stopped before data moved. Two fetched all nine fields: loading a state wiped the Source's
  saved field selection (fixed, ad72ec1). The first Detail's Fields list was empty until its
  first request; set from the Python Shell this time (plugin fix pending).
- Plugin, also fixed today: Status shows bytes only with VISLANG_STATUS_TIMES=0 (cc0f514);
  "result" is what was written, for the whole request (bef22b8 — after these screenshots).
- `figures/screenshot1_overview_working.png`, `screenshot2_detail_working.png`.
