# `runtime/` — where things are written, and what a run leaves behind

| File | What it holds |
|---|---|
| `sandbox.py` | executes spec code under pydantic-monty, never a bare `exec` |
| `paths.py` | the one place that decides where caches live, plus `REPO_ROOT` |
| `trace.py` | human-readable narration of a run → `.vislang/trace.log` |
| `timing.py` | the same runs as data → `.vislang/timings.jsonl` |
| `provenance.py` | the derivation record → into the artifact, or beside it |

Spec code is **untrusted**. It is authored by a model and arrives over the same
channel a prompt injection would, so `sandbox.py` is a security boundary rather
than a convenience.

Anything needing a repo-relative path imports `REPO_ROOT` from `paths.py`
instead of counting `..` from its own `__file__` — that count is exactly what
breaks when a file moves.

`provenance.py` is the third run record, and the only one that does not live
beside the repo: it writes what an output IS — the spec, the input's
fingerprint, the output's `data_sha256`, the build, and a templated explanation
— as YAML into the artifact itself (or a dot-prefixed companion file), so the
derivation survives being copied away from the spec. Gated on
`VISLANG_PROVENANCE`, independently of timing.

`timing.py` records per-phase seconds and bytes, ssh round trips, remote job
launches, predicted-vs-actual, and catalog reuse. `VISLANG_TIMING=0` disables
it. `bench/summarize.py` turns the JSONL into tables and CSV.
