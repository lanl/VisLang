# SIVA Testing Guide

How to test new features and keep the test suite well structured.

All paths (`tests/`, `scripts/`, `renderer.py`, `datasets/`) are relative to
this repository, and the commands use its `.venv` (`.venv/bin/python`,
`.venv/bin/pytest`).

## Test levels

### Level 1: Unit tests on core logic

Pure Python tests of queries, filters, and DSL builder functions. No MCP
protocol, no rendering. These verify that the logic is correct: does
`get_statistics` return the right numbers? Does `threshold` produce the
expected number of points? Does the DSL builder construct the right node
graph?

- Fast, no infrastructure beyond VTK + a small test dataset
- Most tests should be at this level
- Location: `tests/test_*.py` (existing pattern)

### Level 2: Stateful integration tests

Execute sequences of operations that exercise server state: load data,
set pipeline, create views, switch views, restore versions. These test
that stateful interactions work correctly — not just individual
functions in isolation.

Key scenarios to cover:
- Multi-view: create view, set pipeline, switch back, verify first view
  is intact
- Version history: set pipeline, modify, restore version, verify state
- Combined operations: load data, describe data, set pipeline, query a
  filtered node
- State divergence: mutation tools + pipeline rebuild interactions

These call the Python functions directly (no MCP protocol) since the
statefulness is in the server's Python state, not in the protocol.

### Level 3: MCP protocol tests

Call tools through the actual MCP protocol and verify responses
serialize correctly. These don't need to re-test the logic (levels 1-2
do that) — they verify that the MCP SDK can serialize the tool's return
value without errors.

A parameterized test iterating over all `@mcp.tool` functions with
minimal valid inputs would catch type annotation mismatches
systematically. This is the layer that catches errors like a function
annotated `-> str` that actually returns `[str, Image]` — a mismatch the
MCP SDK rejects at serialization time.

Coverage goal: every MCP tool called once with valid args, response
is well-formed and matches the declared return type.

### Level 4: Manual interactive testing

Some issues can only be found through interactive use — concurrency
between the MCP thread and VTK's main thread, responsiveness during
long operations, visual correctness. These aren't automated but should
be exercised deliberately.

## Manual testing for interactive/concurrent features

Features that touch threading, rendering, or stateful interactions
should be manually tested for responsiveness and state consistency.
Think about what happens when operations overlap: a rebuild during user
interaction, rapid successive edits, MCP requests arriving during a
build.

### Testing interactive mode headlessly with Xvfb

Offscreen mode (`--offscreen`) bypasses the windowing code paths that
interactive mode uses (the server still renders on the main thread via the
work queue, but there are no windows or interactors). Bugs like multi-window
issues only manifest in interactive mode. **If your change
touches threading, the Renderer event loop, multi-view, or window
management, test in interactive mode — not just offscreen.**

Xvfb provides a virtual X display, so interactive mode works in headless
environments (CI, cloud, subagent sessions):

```bash
# Launch the server in interactive mode under Xvfb
xvfb-run -a python -m siva.server   # no --offscreen!
```

You can exercise the server via JSON-RPC over stdin/stdout (the MCP
stdio transport), sending `tools/call` messages for sequences like
new_view → set_pipeline → focus → set_pipeline.

To verify what VTK actually rendered to the display (multiple windows,
correct updates), capture the Xvfb framebuffer:

```bash
# Capture the whole virtual display
import -window root -display :99 framebuffer.png

# Capture just the SIVA window
import -window "SIVA" -display :99 window.png
```

To simulate mouse interaction (rotate, click) use xdotool:

```bash
# Click-drag to rotate the trackball camera
xdotool mousemove 500 400
xdotool mousedown 1
xdotool mousemove --delay 50 600 350
xdotool mouseup 1
```

This lets you test real concurrency: interleave xdotool mouse drags
with JSON-RPC pipeline rebuilds to exercise the main-thread work queue
under contention.

### Headless interactive mode (preferred for automated tests)

For testing the threading path without a display or Xvfb, use
`--headless-interactive`:

```bash
python -m siva.server --headless-interactive
```

This combines offscreen rendering (no window) with interactive-mode
threading (event loop on the main thread, work queue dispatch, MCP on a
background thread). It exercises the same code path as interactive mode
— including multi-view work queue dispatch — without needing X11.

**Use this mode for automated threading tests.** It works on macOS and
Linux without any extra dependencies. Reserve the Xvfb approach for
when you need to verify actual windowing behavior (multiple windows
opening, framebuffer contents, mouse input).

The desktop rendering modes (`RenderMode` enum in `renderer.py`; `TRAME`
is the fourth):
- `OFFSCREEN` — no window or interactor. Inline execution when used as a
  library; the server still claims the main thread and dispatches renders
  there, since on macOS even an offscreen `Render()` creates an `NSWindow`.
- `INTERACTIVE` — window + event loop + work queue. Production mode.
- `HEADLESS_INTERACTIVE` — offscreen + event loop + work queue. Test mode.

In every mode the server must exit when its MCP client disconnects (stdin
EOF) -- once any tool call in progress returns, since FastMCP runs sync
tools on the loop that reads stdin. `tests/test_server_exit.py` checks this
per mode, including with a build still computing at exit.

## Test datasets

Some tests require real datasets (wildfire, bonsai) that aren't checked
into the repo. Tests that need missing datasets are skipped automatically.

To run the full test suite including dataset-dependent tests:

```bash
bash scripts/download-test-data.sh   # ~1.2 GB total, one-time
python -m pytest tests/ -q
```

This is expensive (download time + the wildfire integration tests are
slow). For routine development, the fast tests that run without datasets
are usually sufficient. Download test data when you want exhaustive
coverage or are working on features that touch data loading, structured
grid handling, or dataset-specific behavior.

## Writing new tests

- Prefer level 1-2 tests. Only use level 3 when testing the protocol
  boundary specifically.
- Prefer small synthetic datasets (like `datasets/synthetic/`) over
  large real datasets for new tests. Use the wildfire or bonsai datasets
  only when testing behavior specific to their data characteristics
  (curvilinear grids, non-zero extents, CT volumes, etc.).
- Test error paths, not just happy paths. A test that verifies "invalid
  field name produces a clear error message" is often more valuable than
  another happy-path integration test.
- When fixing a bug, write a test that reproduces it before fixing it.
