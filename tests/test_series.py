"""A timeseries' labels in a few words (vislang/formats/series.py), as
`inspect` and the ParaView panel show them.

Plain-python asserts. Run from the repo root:
    python tests/test_series.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vislang.formats.series import describe_series  # noqa: E402

PASS = []


def check(name, cond, detail=""):
    assert cond, f"{name}: {detail}"
    PASS.append(name)
    print(f"  ok  {name}")


def main():
    print("== describing a series ==")
    d = describe_series
    check("one regular run", d(range(1000, 88001, 1000))
          == "88 timesteps: #1000 to #88000, every 1000", d(range(1000, 88001, 1000)))
    check("consecutive labels need no step", d(range(0, 20)) == "20 timesteps: #0 to #19")
    two = [x for x in range(0, 50001, 500) if x not in (12500, 33000)] + \
        list(range(51000, 70001, 1000))
    check("two runs, and the labels missing from the first",
          d(two) == "119 timesteps: #0 to #50000 every 500, then #51000 to "
                    "#70000 every 1000\nmissing #12500, #33000", d(two))
    check("short runs are listed one by one",
          d([2, 9, 10, 11, 40, 41, 300]) == "7 timesteps: #2, #9 to #11, #40, #41, #300",
          d([2, 9, 10, 11, 40, 41, 300]))
    check("a long irregular series is cut short, saying how much is left",
          d([2 ** k for k in range(11)]).endswith("… (3 more)"),
          d([2 ** k for k in range(11)]))
    check("one, two, none", d([5]) == "1 timestep: #5"
          and d([279, 499]) == "2 timesteps: #279, #499" and d([]) == "no timesteps")
    check("order and repeats do not matter", d([5, 3, 4, 4]) == "3 timesteps: #3 to #5")
    print(f"\nall {len(PASS)} checks passed")


if __name__ == "__main__":
    main()
