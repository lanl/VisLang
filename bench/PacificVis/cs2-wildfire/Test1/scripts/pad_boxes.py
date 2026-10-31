#!/usr/bin/env python3
"""Pad the fire boxes (theta > 400 K, from fire_roi_bounds.csv) into wind boxes.

The wind blows along +x, so `--up` pads the -x side (inflow), `--down` the +x side
(the leaning plume) and `--side` both y sides (the flank vortices). `--ztop`
caps the box in height; the plume and its inflow sit well below the domain top.

    python bench/PacificVis/cs2-wildfire/Test1/scripts/pad_boxes.py --up 150 --down 100 --side 100 --ztop 600 -o out.csv
"""
import argparse
import csv

p = argparse.ArgumentParser()
p.add_argument("--src", default="bench/PacificVis/cs2-wildfire/Test1/data/fire_roi_bounds.csv")
p.add_argument("--up", type=float, required=True)
p.add_argument("--down", type=float, required=True)
p.add_argument("--side", type=float, required=True)
p.add_argument("--ztop", type=float, default=None)
p.add_argument("-o", "--out", required=True)
a = p.parse_args()

with open(a.src) as f:
    rows = list(csv.DictReader(line for line in f if not line.startswith("#")))
with open(a.out, "w") as f:
    f.write(f"# wind box: fire box (theta > 400 K, padded 30 m) + {a.up:g} m upwind (-x), "
            f"{a.down:g} m downwind (+x), {a.side:g} m each side (y)"
            + (f", z up to {a.ztop:g} m\n" if a.ztop else ", z whole\n"))
    f.write("step,xmin,xmax,ymin,ymax" + (",zmin,zmax\n" if a.ztop else "\n"))
    for r in rows:
        box = [float(r["xmin"]) - a.up, float(r["xmax"]) + a.down,
               float(r["ymin"]) - a.side, float(r["ymax"]) + a.side]
        f.write(",".join([r["step"]] + [f"{v:g}" for v in box])
                + (f",,{a.ztop:g}\n" if a.ztop else "\n"))
