# Step 4 — full-resolution analysis (2026-10-31)

Input: the Detail (`.vislang/paraview/20261031-013204-652ebb/005-detail-detail/out/`,
38 timesteps, 151 × 121 × 39 points, u v w theta) and the overview's front table.
Scripts: `scripts/cs2_leeslope.py` → `data/leeslope_full.csv`, `data/levels.csv`;
`scripts/cs2_evidence_plot.py` → `evidence.txt`, `evidence_plot.{png,pdf}`;
`scripts/render_leeslope.py` (pvbatch) → `figures/leeslope_{22000,40000}_{side,oblique}.png`.

Definitions: the front is the leading edge of theta > 400 K at the lowest level in
|y| ≤ 50 m (as the overview). "u ahead" = mean u over that strip from the front to 60 m
downwind, per level. Heights are above the ground below (levels.csv: 0, 1.2, 2.5, 3.8,
5.1, 6.6, 8.1, 9.8, 11.5, 13.5, 15.6, 17.8, 20.3, … 49.6 m). Level 0 is the surface
layer: u there is near zero at every timestep, fire or not, so the reversed-layer depth
is measured from level 1 (1.2 m) up.

## The question: does the near-ground wind on the lee slope reverse and oppose the spread?
(numbers: evidence.txt)

1. **The sharp slowdown is not a reversal.** The front slows from ~21 to ~3 m per 1000
   steps between #17000 and #22000. Over that time the air 1–5 m above the ground just
   ahead of the front still blows WITH the spread, but it weakens: +8.9 m/s (#16000),
   +5.9 (#18000, at the crest), +3.0 (#20000), +1.8 (#22000). The slowdown coincides with
   losing the strong forward near-ground wind the fire had on the windward slope.
2. **A reversed layer forms aloft first, then reaches the ground.** Ahead of the front,
   u at 13.5–20.3 m turns negative from #22000, between forward air below (1–8 m) and
   forward air above (> ~35 m). The air at 1–5 m turns negative only at #34000
   (reversed layer 2.4 m deep), deepening to 2.8–4.5 m (#35000–#37000) and to 33–49 m
   (#38000–#42000), then 3–5 m again (#43000–#45000). So reversed flow opposes the spread
   ~16,000 steps after the crossing, during the slow lee phase (~1.8–2.8 m/1000 steps);
   it may help keep the fire slow there, but it does not explain the initial drop.
3. **Mostly the fire's own circulation, not the ridge alone.** On the same lee ground
   (x 100–160 m, 1–5 m up), #21000–#45000: inside the burning strip u is negative at
   25/25 timesteps (mean −0.77 m/s); in the flanks (|y| ≥ 90 m) at 2/25 (mean +0.77).
   The ridge is uniform along y, so a terrain separation would not single out the
   strip. Between #9000 and #20000, with the fire still windward, the same ground
   carries weak forward flow in both (strip +0.63, flanks +0.27 m/s).
   **Caveats, plainly:** at #4000 (before ignition) that ground is reversed in both
   strip and flanks (−4.2 / −2.8 m/s) — a terrain-driven separation existed early, maybe
   while the flow was still spinning up; it was gone by #9000. There is no fire-free run
   of the later period to compare with, and the flanks sit near the fire's own edge late
   in the window (its y extent reaches about ±80 m by #45000). So: the reversal ahead of
   the lee front is concentrated where the fire is, which points to fire-induced flow,
   but terrain cannot be ruled out as a contributor.

## Why the full-resolution step was needed
The stride-8 overview keeps two near-ground levels (0 and 11.5 m). Its "u at the lowest
level is negative from #19000" is the surface layer (near zero at all times), and it
cannot see the 1–8 m air, which stays forward until #34000 — the overview alone would
have suggested a reversal at the crossing that the full-resolution data refutes.

## Figures
- `evidence_plot.png` — front speed; u ahead of the front by height and time (blue =
  reversed, black line u = 0); lee ground, strip vs flanks.
- `figures/leeslope_{22000,40000}_side.png` — streamlines on the y = 0 section (blue =
  back toward the fire). `_oblique.png` — the box in context on the overview terrain
  (framing to be polished in step 7).
