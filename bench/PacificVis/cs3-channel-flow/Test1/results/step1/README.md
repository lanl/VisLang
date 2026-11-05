# Step 1: the stride overview (2026-11-05, run d8e4371009e9, Sieve commit 383a3a0)

Spec (`spec_as_run.py`): `subsample(region(src, z=(1, 1535)), 16)` on
`ssh://gpu-server//…/TurbulentChannelFLow/u_10240x7680x1536_float64.raw`, saved as
`outputs/s1_stride16.vti` (not in git).

## Bytes and server time (`numbers.json`, copied from `timing_record.json` = the run's line in `.vislang/timings.jsonl`)

| | Estimated (before the run) | Measured |
|---|---|---|
| Read on the server | 3,769,344,000 B (46,080 x-rows of 10,225 cells; `estimate.txt`) | **18,094,653,440 B** (executor's Linux `read_bytes`): 4.80x the estimate |
| Over the wire | 235,929,600 B | 235,930,686 B (+1,086 B: the grid's placement) |
| Result | 235,929,600 B | 235,929,600 B |
| Remote reduce time (`server_s`) | n/a | **376.36 s** (of which `materialize`, the read and cut, 375.90 s) |

- Not held: under every budget (`estimate.txt`). Bandwidth probe before the pull: 4,194,304 B
  (`probe_bytes`, not in "over the wire").
- Page cache before the run: 4,158,783,488 B of the file resident, from step 0's probes
  (`page_cache_before.txt`). So the read was cold apart from those pages.
- Read-ahead: the estimate counts the bytes of the rows the reader asks for. The NFS client
  fetched 4.8x as much, consistent with step 0's probe (4.7x).

## The output in ParaView (`paraview_check.txt`, pvpython 6.1.0-RC2)

- `vtkImageData`, 640 x 480 x 96 points, origin (0, 0, 1), spacing 16: placed at the source's
  cells (field data `sieve_in_cells` = 1).
- One array `u` (double), range [-0.012587, 1.299658].
- Provenance record embedded (`sieve_provenance`).

## What it shows (`stride_census.txt`, script `../../scripts/stride_census.py`)

- **Lower-wall plane z = 1 (y+ 0.07): 297 of 307,200 samples have u < 0: 0.0967 %,
  95 % Wilson interval [0.0863, 0.1083] %**, most negative -0.00902. Compare Lenaers et al.
  (2012): 0.06 % at Re_tau = 1000 (wall shear stress; u < 0 at y+ 0.07 stands in for it).
- Every other level: no u < 0, except one sample at z = 1521 (y+ 6.65 from the upper wall),
  -0.01259. The stride's levels nearest the upper wall are at y+ 6.65 and beyond (blocks start
  at z = 1), so the upper wall has no y+ ~ 0.07 stride plane.
- Mean u at z = 1 is 0.003 (U+ ~ 0.07 = y+, the viscous sublayer, as expected).

What the stride cannot show is **where** the events are: 1 cell in 256 of each plane is kept,
and a region ~20 viscous units across covers ~4 cells.
