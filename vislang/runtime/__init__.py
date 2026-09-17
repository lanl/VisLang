"""Supporting machinery: where things are written, and what a run leaves behind.

    paths.py    the one place that decides where caches live (`.vislang/`)
    sandbox.py  spec code is UNTRUSTED — it is agent-authored and arrives over
                the same channel a prompt injection would. It executes under
                pydantic-monty, not a bare `exec`.
    trace.py    the human-readable narration of a run -> `.vislang/trace.log`
    timing.py   the same runs as data -> `.vislang/timings.jsonl` (per-phase
                seconds and bytes, ssh round trips, predicted-vs-actual,
                catalog reuse). `VISLANG_TIMING=0` turns it off.

`bench/summarize.py` turns the JSONL into tables.
"""
