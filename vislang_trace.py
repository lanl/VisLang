"""Append-only run log — the durable, detailed record of what each run did.

The MCP return value shows the plan, but it scrolls away and the remote side's
work is easy to miss. This writes the SAME narration (spec as authored, the
inspect schema, how the planner lowered/optimized the chain, what shipped to the
remote, what the catalog reused vs fetched, the final sink) to a file you can
tail while iterating — `.vislang/trace.log` by default, or $VISLANG_TRACE_FILE.

One MCP session APPENDS to the file across every run (a banner delimits each
session), so a "download 3 fields, then 4" sequence shows both runs back to back
— including that the second only pulled the delta. VISLANG_TRACE=0 disables it.
"""

import os
from datetime import datetime

_BANNER_WRITTEN = False


def _enabled():
    return os.environ.get("VISLANG_TRACE", "1") != "0"


def trace_file():
    """Where the run log lives. $VISLANG_TRACE_FILE overrides; else
    <home>/trace.log (the consolidated, gitignored cache root)."""
    env = os.environ.get("VISLANG_TRACE_FILE")
    if env:
        return env
    from vislang_paths import home
    return os.path.join(home(), "trace.log")


def _append(text):
    if not _enabled():
        return
    path = trace_file()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a") as f:
            f.write(text)
            if not text.endswith("\n"):
                f.write("\n")
    except OSError:
        pass                                    # logging must never break a run


def session_banner():
    """Delimit a new MCP session in the log — written once per process, so all
    the runs of one session sit under a single dated banner (append, not
    rewrite). Safe to call on every run_pipeline; it no-ops after the first."""
    global _BANNER_WRITTEN
    if _BANNER_WRITTEN or not _enabled():
        return
    _BANNER_WRITTEN = True
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _append(f"\n{'=' * 78}\n=== VisLang session  pid={os.getpid()}  {stamp}\n{'=' * 78}")


def log_run(spec_path, report):
    """Append one run_pipeline's full report under a timestamped sub-header."""
    if not _enabled():
        return
    stamp = datetime.now().strftime("%H:%M:%S")
    _append(f"\n----- run {stamp}  spec={spec_path} -----\n{report}")
