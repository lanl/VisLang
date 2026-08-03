"""Machine-readable run measurements — the numbers half of the run log.

`vislang_trace.py` writes the human narration (what the planner decided, what
crossed the wire) as prose you can tail. That is unreadable as *data*: a case
study needs "how many seconds in which phase, how many bytes, how many ssh
round trips, predicted vs actual" as rows you can aggregate. This module writes
exactly that — ONE JSON object per run, appended to `<home>/timings.jsonl` —
alongside the prose, from the same call sites.

Shape of a run record (bench/summarize.py turns these into tables):

    {"run_id", "started", "spec", "spec_sha", "commit", "status", "total_s",
     "env": {...the VISLANG_* knobs in force...},
     "pipelines": [{"kind","uri","site","est_read_mb","est_time_lo_s",
                    "wire_bytes","reused_pairs","total_pairs", ...}],
     "phases":    [{"name","pipeline","s","bytes", ...}],
     "counters":  {"ssh_exec","ssh_query","transfers","wire_bytes","remote_jobs"}}

Three properties matter and are load-bearing:

  * **Never break a run.** Every entry point swallows its own errors; a phase
    context manager re-raises the *body's* exception (recording it) but never
    invents one. Measurement is free to fail; the run is not.
  * **Off means gone.** `VISLANG_TIMING=0` makes every call a no-op.
  * **Detached calls are silent.** With no run open (the remote executor, a unit
    test importing the planner) phases still time correctly but record nothing,
    so the module can be wired anywhere without a caller contract.

`counters` are the hardware-independent metrics: ssh round trips and remote job
launches distinguish an O(N)-per-timestep loop from one batched job regardless
of how fast the link happened to be that afternoon.
"""

import hashlib
import json
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime

# The env knobs that change what a run does — snapshotted per run so a table row
# carries its own configuration (which arm of an experiment it belongs to).
_ENV_KEYS = ("VISLANG_REMOTE", "VISLANG_SRUN_JOBID", "VISLANG_SRUN_NAME",
             "VISLANG_SRUN_PARTITION", "VISLANG_BUDGET_BYTES",
             "VISLANG_BUDGET_SECONDS", "VISLANG_NO_BINDING", "VISLANG_SSH_MUX",
             "VISLANG_CACHE", "VISLANG_HOME", "VISLANG_REMOTE_PYTHON")

_run = None          # the open run record, or None (detached: record nothing)
_pipe = None         # the open pipeline record within _run
_stack = []          # open phase names, so nested phases get a "a/b" path
_commit = False      # lazily resolved git commit ("" if unavailable)


def enabled():
    return os.environ.get("VISLANG_TIMING", "1") != "0"


def timings_file():
    """Where the JSONL lands. `$VISLANG_TIMINGS_FILE` overrides; else
    `<home>/timings.jsonl` next to trace.log in the gitignored cache root."""
    env = os.environ.get("VISLANG_TIMINGS_FILE")
    if env:
        return env
    from vislang_paths import home
    return os.path.join(home(), "timings.jsonl")


def _git_commit():
    """Short commit of the repo under measurement, resolved once per process.
    Recorded so a table row can be traced back to the code that produced it."""
    global _commit
    if _commit is not False:
        return _commit
    _commit = ""
    try:
        import subprocess
        repo = os.path.dirname(os.path.abspath(__file__))
        out = subprocess.run(["git", "-C", repo, "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            _commit = out.stdout.strip()
    except Exception:
        pass
    return _commit


def _flush(record):
    path = timings_file()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except (OSError, TypeError, ValueError):
        pass                                # measurement must never break a run


# ---------------------------------------------------------------------------
# The run: one record per run_pipeline (or per driver iteration in bench/)
# ---------------------------------------------------------------------------
@contextmanager
def run(spec_path, spec_code=None, **fields):
    """Open a run record; on exit, append it to the JSONL as one line.

    `status` defaults to 'OK' and 'error' on an exception — the MCP layer knows
    better (FAILED / NEEDS CONFIRM / NEEDS ALLOCATION) and overwrites it via the
    yielded record. Re-entrant calls yield the already-open record rather than
    nesting, so a bench driver may wrap a run that opens its own."""
    global _run, _pipe, _stack
    if not enabled() or _run is not None:
        yield _run if _run is not None else {}
        return
    rec = {
        "run_id": uuid.uuid4().hex[:12],
        "started": datetime.now().isoformat(timespec="seconds"),
        "spec": spec_path,
        "spec_sha": (hashlib.sha256(spec_code.encode()).hexdigest()[:12]
                     if spec_code else None),
        "commit": _git_commit(),
        "status": "OK",
        "total_s": None,
        "env": {k: os.environ[k] for k in _ENV_KEYS if k in os.environ},
        "pipelines": [],
        "phases": [],
        "counters": {},
    }
    rec.update(fields)
    _run, _pipe, _stack = rec, None, []
    t0 = time.perf_counter()
    try:
        yield rec
    except BaseException as e:
        rec["status"] = "error"
        rec["error"] = f"{type(e).__name__}: {e}"
        raise
    finally:
        rec["total_s"] = round(time.perf_counter() - t0, 4)
        _run, _pipe, _stack = None, None, []
        _flush(rec)


@contextmanager
def pipeline(**fields):
    """One sink's plan+execute within a run. Phases opened inside are tagged with
    its index, and `note()` attaches to it — so a two-sink spec yields two
    comparable rows instead of one blended total."""
    global _pipe
    if not enabled() or _run is None:
        yield {}
        return
    rec = {"index": len(_run["pipelines"]), "status": "ok"}
    rec.update(fields)
    _run["pipelines"].append(rec)
    prev, _pipe = _pipe, rec
    t0 = time.perf_counter()
    try:
        yield rec
    except BaseException as e:
        rec["status"] = "error"
        rec["error"] = f"{type(e).__name__}: {e}"
        raise
    finally:
        rec["s"] = round(time.perf_counter() - t0, 4)
        _pipe = prev


@contextmanager
def phase(name, **fields):
    """Time one phase (inspect, remote_exec, pull, materialize, sink…).

    Yields the phase's own record: set `rec["bytes"]` or any field from inside
    the body once the quantity is known (`with phase('pull') as p: … p['bytes']
    = n`). Nested phases record a slash path, so `remote_reduce/pull` sits under
    `remote_reduce` and both are summable without double counting (analysis
    filters on depth)."""
    if not enabled() or _run is None:
        yield {}
        return
    _stack.append(name)
    rec = {"name": "/".join(_stack), "depth": len(_stack) - 1,
           "pipeline": _pipe["index"] if _pipe is not None else None}
    rec.update(fields)
    t0 = time.perf_counter()
    try:
        yield rec
    except BaseException as e:
        rec["error"] = f"{type(e).__name__}: {e}"
        raise
    finally:
        rec["s"] = round(time.perf_counter() - t0, 4)
        _stack.pop()
        _run["phases"].append(rec)


def note(**fields):
    """Attach measured facts to the current pipeline (or the run when no pipeline
    is open) — the estimate, the catalog delta, the site actually chosen."""
    if not enabled() or _run is None:
        return
    (_pipe if _pipe is not None else _run).update(fields)


def count(name, n=1):
    """Increment a run counter: ssh round trips, transfers, remote job launches,
    bytes over the wire. Hardware-independent, so these are the numbers that
    survive a noisy shared link."""
    if not enabled() or _run is None:
        return
    c = _run["counters"]
    c[name] = c.get(name, 0) + n


def note_estimate(est):
    """Record a CostEstimate on the current pipeline as flat, tabulatable fields
    — the predicted half of the predicted-vs-actual table."""
    if not enabled() or _run is None or est is None:
        return
    note(est_site=getattr(est, "site", None),
         est_read_mb=getattr(est, "read_mb", None),
         est_output_mb=getattr(est, "output_mb", None),
         est_payload_mb=getattr(est, "browser_payload_mb", None),
         est_time_lo_s=getattr(est, "time_lo_s", None),
         est_time_hi_s=getattr(est, "time_hi_s", None),
         est_confidence=getattr(est, "confidence", None),
         est_over_budget=getattr(est, "over_budget", None),
         est_n_timesteps=getattr(est, "n_timesteps", None))


def dir_bytes(path):
    """Total bytes under `path` (a file or a directory tree), or None. Used to
    size a directory transfer, whose byte count no single stat reports."""
    try:
        if os.path.isfile(path):
            return os.path.getsize(path)
        total = 0
        for root, _, names in os.walk(path):
            for nm in names:
                try:
                    total += os.path.getsize(os.path.join(root, nm))
                except OSError:
                    pass
        return total
    except OSError:
        return None
