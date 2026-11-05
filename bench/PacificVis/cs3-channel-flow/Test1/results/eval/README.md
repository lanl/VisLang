# Evaluation on the ground-truth area (2026-11-05)

Script: `../../scripts/evaluate_area.py` (cvision env: numpy, scipy, scikit-image). Inputs are the
arrays written by `../../scripts/vti2npy.py` (vislang env) from `outputs/s2_truth.vti`,
`s1_stride16.vti`, `s3_area_mean16.vti` and `s3_area_min16.vti`. Definitions are as fixed in
DESIGN.md section 6 before any overview was seen. Output: `area_eval.txt` (readable),
`area_eval.json` (everything, including SSIM per plane), `events.csv` (one row per region).

Area: 1024 x 1024 cells of the lower wall at x 4608-5631, y 3328-4351, levels z 1-64 (y+ 0.07-68),
84.94 million viscous units squared.

## Cross-checks (the overviews agree with the ground truth)
- The stride overview's cells in the area equal the ground truth at the stride positions: **exact**.
- Sieve's block minima (step 3, from the cache) equal a local block minimum of the ground truth:
  **exact**.
- Sieve's block means: max |difference| from a local block mean = **1.1e-15**.

## How often, how high
- Wall plane z = 1 (y+ 0.071): **0.1265 %** of cells have u < 0 (1,326 of 1,048,576). The fraction
  falls with height: 0.0103 % at y+ 2.7, 0.0002 % at y+ 8.1.
- **Highest level with any u < 0: z = 18, y+ 9.67** (Lenaers et al.: y+ ~ 8.5 at Re_tau 1000).
- Whole lower wall, from the stride (step 1): 0.0967 % [0.0863, 0.1083] at z = 1. This area is
  above the wall average; it is one 2.5 delta x 1.3 delta patch.

## Regions (26-connected u < 0)
- **208 regions** (6-connected: 226). **2.45 per million viscous units squared** (Cardesa et al.:
  1.97 at Re_tau 2003, on a grid 1.5x finer in x and z).
- All attached to the wall. Mean bounding box **25.2 x 24.0** viscous units (streamwise x
  spanwise; Cardesa: ~19-20, so ours are biased up by the coarser grid, as expected). Median
  11 cells. Five touch the area's sides.

## What each overview shows (blocks of 16^3 cells; 64 x 64 x 4 blocks; 221 hold backflow)

| | Stride (every 16th cell) | Block mean | Block minimum |
|---|---|---|---|
| Regions shown (event recall) | **5 of 208 (2.4 %)** | **0 of 208 (0 %)** | **208 of 208 (100 %)** |
| Blocks flagged / precision / recall | 4 / 1.0 / 0.018 | 0 / n/a / 0 | 221 / 1.0 / 1.0 (by construction) |
| Localization, median (region centroid to block centre, in-plane) | 105 viscous units | n/a | 60 viscous units |
| Value at the region's block minus its true minimum, median | +0.0054 | +0.137 | **0 (exact)** |
| **SSIM**, mean over the 16 planes of block row 0 | 0.181 | **0.344 (highest)** | **0.136 (lowest)** |

**The hypothesis holds:** SSIM ranks the block mean highest and the block minimum lowest, while the
mean shows none of the 208 reverse-flow regions and the minimum shows all of them. An overview
chosen by image fidelity would show no reverse flow. The minimum pays with localization (to a
block: 204 x 102 viscous units in plane) and with values biased low against any pointwise field.
Value bias is in units of the bulk velocity (u_tau = 0.0415 U_b).
