"""Progress a front end can follow while a run is under way.

A run's timing record (timing.py) is written once, when the run ends. A
front end waiting on a fetch wants to know sooner — the estimate as soon as it
is priced, then which phase is running — so it can say "fetching ~40 MB,
pulling…" instead of a bare spinner. This module appends those moments to a
JSON-lines file named by `VISLANG_EVENTS_FILE` (set per request by `sieve api
run --events F`): one object per line, `{"t", "kind", …}`. Kinds:

    estimate   the priced plan (CostEstimate fields), before anything moves
    phase      {"name", "state": "start" | "end", "s"} for each timed phase

The same call sites as timing.py feed it, so it is on exactly when timing is.
Writing must never break a run: every failure is swallowed.
"""

import json
import os
import time


def _path():
    return os.environ.get("VISLANG_EVENTS_FILE") or None


def emit(kind, **fields):
    """Append one event, if a sink is set. Never raises."""
    path = _path()
    if not path:
        return
    try:
        line = json.dumps({"t": round(time.time(), 3), "kind": kind, **fields},
                          default=str)
        with open(path, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def read(path):
    """Every event in `path`, oldest first; a torn last line is skipped."""
    out = []
    try:
        with open(path) as f:
            for ln in f:
                try:
                    out.append(json.loads(ln))
                except ValueError:
                    pass
    except OSError:
        pass
    return out
