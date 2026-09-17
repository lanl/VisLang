# `runtime/` — where things are written, and what a run leaves behind

| File | What it holds |
|---|---|
| `sandbox.py` | executes spec code under pydantic-monty, never a bare `exec` |
| `paths.py` | the one place that decides where caches live, plus `REPO_ROOT` |
| `trace.py` | human-readable narration of a run → `.vislang/trace.log` |
| `timing.py` | the same runs as data → `.vislang/timings.jsonl` |

Spec code is **untrusted**. It is authored by a model and arrives over the same
channel a prompt injection would, so `sandbox.py` is a security boundary rather
than a convenience.

Anything needing a repo-relative path imports `REPO_ROOT` from `paths.py`
instead of counting `..` from its own `__file__` — that count is exactly what
breaks when a file moves.

`timing.py` records per-phase seconds and bytes, ssh round trips, remote job
launches, predicted-vs-actual, and catalog reuse. `VISLANG_TIMING=0` disables
it. `bench/summarize.py` turns the JSONL into tables and CSV.
