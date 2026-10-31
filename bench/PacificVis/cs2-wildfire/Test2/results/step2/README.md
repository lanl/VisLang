# Step 2 — choosing the window (2026-10-31), from the overview only

`scripts/cs2_front.py` (per-timestep front and wind → `data/front_s8.csv`) and
`scripts/cs2_window.py` (→ `window.txt`, `window_plot.{png,pdf}`). Nothing moved.

Front = largest x where theta crosses 400 K at the lowest level (≈0.75 m above
ground), |y| ≤ 50 m, interpolated between grid points. Wind ahead = mean u at that
level from the front to 60 m downwind. Speed, every timestep: line fit over ±4000
steps; every 5th file: difference of consecutive samples. Units: m per 1000 steps.

## What the overview shows (window.txt)
| | every timestep (88 files) | every 5th file (18 files) |
|---|---|---|
| crest crossing | #18020 | #18049 |
| speed, windward #8000–#17000 | 19.0 | 19.4 (2 samples) |
| speed, lee #20000–#45000 | 2.8 | 3.0 (5 samples) |
| speed, lee #45000–#91000 | 1.8 | 2.0 (9 samples) |
| u ahead at 0.75 m first negative after the windward run | #19000 | #23000 |

Wind ahead at 0.75 m: windward +0.71 to +2.35 m/s (never negative, 0/10); lee
#19000–#45000 mean −0.30 m/s, negative at 26/27 timesteps.

Every 5th file gives the crossing time and the average speeds about as well (they
are slow, steady trends, and #18000 happens to be one of the five-step samples).
What it cannot resolve is the transition itself: the speed falls from ~21 to ~3
m/1000 steps between #17000 and #22000, the wind ahead peaks at +2.35 m/s at #16000
and turns negative by #19000 — inside one five-file gap (#18000 → #23000). So it
cannot say whether the reversal comes before, with, or after the slowdown, which
is the scientific question.

## Proposed window: #9000–#45000 (37 files; 7 of them are every-5th files)
Nine windward timesteps before the crossing (a steady-speed baseline), the
transition, and 27 lee timesteps where the slowdown and the negative wind are
established. Plus #4000 (pre-ignition control) for the Detail.
