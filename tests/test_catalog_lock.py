"""The extent catalog shared by several processes at once.

Plain-python asserts (no pytest on the cluster). Run from the repo root:
    python tests/test_catalog_lock.py

Two processes store 50 extents each into ONE cache root at the same time (as
the MCP server, `sieve` and a ParaView session may). Every entry must survive —
before the catalog locked its read-modify-write, each process rewrote the
manifest from its own stale copy and the other's entries were lost. A third
catalog, opened before the writers started, must see their entries on its next
read.
"""

import os
import subprocess
import sys
import tempfile

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from vislang.remote.catalog import ExtentCatalog

PASS = []
WRITER = r"""
import sys, numpy as np
sys.path.insert(0, sys.argv[1])
from vislang.remote.catalog import ExtentCatalog
root, who = sys.argv[2], sys.argv[3]
cat = ExtentCatalog(root)
for i in range(50):
    cat.store(f"src-{who}", "rho", {"forms": [["region", [["x", i, i + 1]]]]},
              np.full((2, 2, 2), i, dtype=np.float32))
"""


def check(name, cond, detail=""):
    assert cond, f"{name}: {detail}"
    PASS.append(name)
    print(f"  ok  {name}")


def main():
    print("== two processes, one catalog ==")
    root = tempfile.mkdtemp(prefix="vislang_catlock_")
    early = ExtentCatalog(root)                     # opened before anything is stored
    procs = [subprocess.Popen([sys.executable, "-c", WRITER, REPO, root, who])
             for who in ("a", "b")]
    codes = [p.wait() for p in procs]
    check("both writers finished", codes == [0, 0], codes)
    cat = ExtentCatalog(root)
    n = {who: len(cat._manifest["extents"].get(f"src-{who}", {}).get("rho", []))
         for who in ("a", "b")}
    check("no entry lost", n == {"a": 50, "b": 50}, n)
    have, missing = early.delta("src-b", ["rho"],
                                {"forms": [["region", [["x", 7, 8]]]]})
    check("a catalog opened earlier sees entries stored since",
          not missing and np.all(have["rho"] == 7), missing)
    leftovers = [f for f in os.listdir(os.path.join(root, "extents")) if "tmp" in f]
    check("no half-written extent files left behind", not leftovers, leftovers)


if __name__ == "__main__":
    main()
    print(f"\nall {len(PASS)} checks passed")
