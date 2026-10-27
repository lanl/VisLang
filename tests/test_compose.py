"""Specs written for a front end (vislang/dsl/compose.py).

Plain-python asserts (no pytest on the cluster). Run from the repo root:
    python tests/test_compose.py

Covers: the spec a request composes (one concern per line, fixed order, the
explicit coords=), that it parses in the sandbox, a box turned into region()
for each kind of data — world bounds rounded only outward, cells half-open and
whole — agreeing with the resolver's own world→cells rule on random boxes, and
the refusals (a box that holds nothing).
"""

import os
import random
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from vislang.dsl.compose import compose_spec, view_box_to_region, check_spec, _round
from vislang.formats.dataset_info import uniform_geometry, world_box_to_cells
from decimal import ROUND_CEILING, ROUND_FLOOR

PASS = []


def check(name, cond, detail=""):
    assert cond, f"{name}: {detail}"
    PASS.append(name)
    print(f"  ok  {name}")


def raises(name, fn, text=None):
    try:
        fn()
    except Exception as e:
        check(name, text is None or text in str(e), str(e))
        return
    raise AssertionError(f"{name}: did not raise")


WORLD = {"uri": "/d/cube.vti", "modality": "grid", "grid": [64, 48, 32],
         "coords": "world", "bounds": [[100.0, 131.5], [-4.0, 90.0], [2.0, 33.0]],
         "timesteps": None, "out_ext": ".vti"}
CELLS = {"uri": "/d/plain.h5", "modality": "grid", "grid": [32, 32, 32],
         "coords": "cells", "bounds": [[0, 31], [0, 31], [0, 31]],
         "timesteps": None, "out_ext": ".vti"}
PLACED = {**CELLS, "uri": "/d/cut.vti", "grid": [8, 32, 32],
          "bounds": [[8, 22], [0, 31], [0, 31]]}            # source cells 8, 10, … 22
POINTS = {"uri": "/d/halo.gio", "modality": "particles", "grid": None,
          "coords": "world", "bounds": None, "timesteps": None, "out_ext": ".vtp"}
SERIES = {**WORLD, "uri": "/d/series", "timesteps": [3, 4, 5]}


def test_compose():
    print("== composed specs ==")
    text = compose_spec(SERIES, {"output": "/o/out.vti", "timestep": 4,
                                 "fields": ["rho"], "box": {"lo": [101, 0, 0],
                                                            "hi": [110, 20, 99]},
                                 "threshold": "rho > 1.5", "stride": 2})
    lines = text.strip().split("\n")
    check("one form per line, in the fixed order",
          [ln.split("(")[0].split("= ")[-1] for ln in lines]
          == ["source", "timesteps", "fields", "region", "threshold", "subsample", "save"],
          text)
    check("the region names its coordinates",
          "region(view, x=(101.0, 110.0), y=(0.0, 20.0), coords='world')" in text, text)
    parsed = check_spec(text)
    check("the composed spec parses in the sandbox",
          parsed["ok"] and [s["kind"] for s in parsed["plans"][0]["chain"]]
          == ["source", "timesteps", "fields", "region", "threshold", "subsample",
              "save"], parsed)
    reg = [s for s in parsed["plans"][0]["chain"] if s["kind"] == "region"][0]
    check("coords travels in the plan", reg.get("coords") == "world", reg)

    text = compose_spec(CELLS, {"output": "/o/o.vti", "stride": 4})
    check("an overview is a stride over the source",
          text.strip().split("\n")[1:] == ["view = subsample(src, 4)",
                                          "save(view, '/o/o.vti')"], text)
    text = compose_spec(POINTS, {"output": "/o/p.vtp", "positions": ["x", "y", "z"],
                                 "stride": 1})
    check("positions named when given; stride 1 adds nothing",
          text.strip().split("\n") == ["src = source('/d/halo.gio', positions=('x', 'y', 'z'))",
                                       "save(src, '/o/p.vtp')"], text)
    check("a broken spec is reported, not raised",
          check_spec("save(region(source('/d'), x=(5, 1)), 'o.npz')")["ok"] is False)


def test_boxes():
    print("== a box as region() ==")
    axes, coords = view_box_to_region(CELLS, {"lo": [3.2, 0, -5], "hi": [10.0, 31, 99]})
    check("cells: samples inside [lo, hi] -> [ceil(lo), floor(hi)+1); whole axes dropped",
          coords == "cells" and axes == [("x", (4, 11))], axes)
    axes, _ = view_box_to_region(PLACED, {"lo": [9.5, 0, 0], "hi": [15.2, 31, 31]})
    check("a placed cut: clipped to the source cells it holds", axes == [("x", (10, 16))],
          axes)
    axes, _ = view_box_to_region(PLACED, {"lo": [0, 0, 0], "hi": [100, 31, 31]})
    check("a box over the whole placed cut cuts nothing", axes == [], axes)
    raises("cells: a box between cells holds nothing",
           lambda: view_box_to_region(CELLS, {"lo": [3.2, 0, 0], "hi": [3.8, 1, 1]}),
           "holds no cell")
    raises("world: a box beside the data misses it",
           lambda: view_box_to_region(WORLD, {"lo": [0, 0, 0], "hi": [50, 1, 3]}),
           "misses the data")
    axes, coords = view_box_to_region(POINTS, {"lo": [-1.23456789, 0, 0],
                                               "hi": [2.98765432, 1, 1]})
    check("points: world, every axis kept (no bounds known), rounded outward",
          coords == "world" and axes[0] == ("x", (-1.23457, 2.98766)), axes)

    rng = random.Random(7)
    grid = (32, 32, 32)
    ident = uniform_geometry((0, 0, 0), (1, 1, 1))
    agree = True
    for _ in range(300):
        lo = [rng.uniform(-3, 33) for _ in range(3)]
        hi = [v + rng.uniform(0.01, 12) for v in lo]
        try:
            axes, _ = view_box_to_region(CELLS, {"lo": lo, "hi": hi})
        except ValueError:
            continue
        want, _ = world_box_to_cells(ident, grid, {a: (lo[a], hi[a]) for a in range(3)})
        got = dict(axes)
        for a, name in enumerate("xyz"):
            full = (0, grid[a])
            if got.get(name, full) != want.get(a, full):
                agree = False
    check("cells rounding = the resolver's world->cells rule on 300 random boxes", agree)

    inward = False
    for _ in range(2000):
        v = rng.uniform(-1e4, 1e4) * 10 ** rng.randint(-6, 6)
        if _round(v, ROUND_FLOOR) > v or _round(v, ROUND_CEILING) < v:
            inward = True
    check("world bounds never rounded inward", not inward)


if __name__ == "__main__":
    test_compose()
    test_boxes()
    print(f"\nall {len(PASS)} checks passed")
