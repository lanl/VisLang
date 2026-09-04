#!/usr/bin/env python3
import os

from mcp.server.mcpserver import MCPServer

# The engine logic lives in cli_core so the `sieve` terminal CLI and this MCP
# server are two thin front-ends onto ONE implementation. These tools just wrap
# the shared functions with the tool decorator + docstrings the model reads.
from cli_core import (do_inspect, do_execute, do_estimate_render_cost,
                      do_submit_adapter, do_submit_binding, do_connect,
                      do_disconnect)

# --- Guidance surfaced to the model -----------------------------------------
# The repo's root CLAUDE.md is the always-loaded index (Claude Code auto-loads
# it); the instructions/ folder holds the detailed docs, exposed as resources the
# model reads on demand and pointed to from CLAUDE.md. We deliberately do NOT
# ship a verbose startup-instructions blob — CLAUDE.md plays that role. A short
# pointer is still set so non-Claude-Code clients get their bearings.
_INSTRUCTIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "instructions")


def _read_instruction(name):
    with open(os.path.join(_INSTRUCTIONS_DIR, f"{name}.md"), encoding="utf-8") as f:
        return f.read()


mcp = MCPServer("VisLang Data Management", instructions=(
    "VisLang: a declarative DSL for reading, narrowing, and rendering scientific "
    "data. Author a small spec.py from forms and run it with run_pipeline; call "
    "inspect(filepath) to read a file's schema before writing the spec. Full "
    "guidance is in the repo's CLAUDE.md and the vislang://instructions/* "
    "resources."))


@mcp.resource("vislang://instructions", name="instructions-index",
              description="List of available VisLang guidance documents.",
              mime_type="text/markdown")
def _instructions_index():
    try:
        docs = sorted(f[:-3] for f in os.listdir(_INSTRUCTIONS_DIR)
                      if f.endswith(".md"))
    except OSError:
        return "No instructions/ folder found."
    lines = ["# VisLang instruction documents", "",
             "Read any with `vislang://instructions/<name>`:", ""]
    lines += [f"- `vislang://instructions/{d}`" for d in docs]
    return "\n".join(lines)


@mcp.resource("vislang://instructions/{doc}", name="instruction-doc",
              description="A VisLang guidance document (philosophy, DSL, "
                          "adapters, rendering, authoring, roadmap).",
              mime_type="text/markdown")
def _instruction_doc(doc):
    try:
        return _read_instruction(doc)
    except OSError:
        raise ValueError(f"No instruction document named {doc!r}. "
                         f"See vislang://instructions for the list.")


@mcp.tool()
def connect(host: str) -> str:
    """Open an ssh session to a remote host that needs an interactive login.

    Call this when `inspect` returns a `NEEDS_SESSION` handshake or a run reports
    `Status: NEEDS SESSION`. `host` may be a hostname, an ssh alias, or the whole
    source URI — connect("ssh://gpu-server/scratch/run") is fine.

    YOU NEVER HANDLE THE SECRET. A password dialog opens on the USER's screen;
    OpenSSH reads the answer directly from its own helper process. It does not
    pass through VisLang, this tool's result, or your context — all you get back
    is whether a session came up. Never ask the user to type a password to you,
    and never put one in a spec or a tool argument.

    The session is shared by every later inspect, estimate and run until it
    expires (8h by default), so call this once per host, not once per spec. If
    the host is UNREACHABLE this returns without prompting — that is a VPN or
    network problem, and a password cannot fix it.
    """
    return do_connect(host)


@mcp.tool()
def disconnect(host: str) -> str:
    """Close the ssh session for a host. Rarely needed — sessions expire on their
    own — but useful to force re-authentication or to release one deliberately."""
    return do_disconnect(host)


@mcp.tool()
def inspect(filepath: str, positions: str = None) -> str:
    """Read a file's schema — variables, dimensions, attributes — metadata only, no bulk data.

    Use this to write a spec: you need to know what fields and axes exist before
    you can `region`, `subsample`, `fields`, or `threshold` them. `inspect` is the
    engine behind the `source()` form; calling it here is the same read, just so
    you can see the schema while authoring.

    For a REMOTE source (ssh://…) with no live session, this returns a
    `NEEDS_SESSION` handshake instead of reading anything: call `connect(host)`,
    which prompts the user in a dialog, then inspect again.

    If no reader recognizes the file, this returns a `NEEDS_ADAPTER` handshake:
    follow it to write a reader module and call `submit_adapter`. If `filepath` is
    a FOLDER, it is a TIMESERIES (files named `…#N`, N = timestep): this returns a
    listing of the timesteps + the shared schema; narrow it with the usual forms
    and pick a range with `timesteps(node, start, stop)`.

    positions: optional "x,y,z" override naming the spatial-coordinate variables
    when auto-detection can't tell (e.g. particle data with unusual names).
    """
    return do_inspect(filepath, positions)


@mcp.tool()
def submit_binding(filepath: str, binding_json: str) -> str:
    """Verify and freeze a semantic binding you proposed for an HDF5 file.

    Call this after `inspect` returned a `BINDING_AVAILABLE` offer and you wrote a
    binding JSON ({"dimensions", "variables", "attributes_from"}) from the schema
    tree. It is the deterministic oracle: every claim is checked against the file's
    own metadata; on success the binding is frozen (keyed by the schema signature)
    so future inspects/runs of this schema get rich variable names, and on failure
    the exact violation is returned so you can fix and resubmit. Nothing is
    executed — bindings are inert declarative data.
    """
    return do_submit_binding(filepath, binding_json)


@mcp.tool()
def submit_adapter(filepath: str, module_code: str) -> str:
    """Validate and freeze a reader adapter you wrote for a file `inspect` didn't recognize.

    Call this after `inspect` returned a `NEEDS_ADAPTER` handshake and you wrote a
    module (FILETYPE, EXTENSIONS, inspect(filepath), read_array(filepath, location)).
    It is the deterministic trust step: the module is exec'd, its inspect() is run
    against THIS real file and structurally validated, and read_array() is checked
    to return real data. On success the module is frozen to generated_adapters/ and
    registered (future files of this format need no model). On failure it returns
    the violation/traceback — fix the module and call submit_adapter again.
    """
    return do_submit_adapter(filepath, module_code)


@mcp.tool()
def estimate_render_cost(filepath: str) -> str:
    """Predict the cost of rendering a dataset and recommend a subsample — BEFORE loading.

    Reads only metadata (no bulk data). Use this on an unfamiliar or large file
    before rendering: render is headless k3d and ships the array to the browser,
    so a full "show everything" view can be huge. The report gives the estimated
    browser payload, the disk-read cost (and whether narrowing reduces it), and a
    ready-to-use recommendation to keep the first overview responsive.
    """
    return do_estimate_render_cost(filepath)


@mcp.tool()
def run_pipeline(spec_path: str, confirm: bool = False) -> str:
    """Execute a declarative DSL spec at spec_path and return an execution report.

    Convention: keep the spec in a single file named `spec.py`, edited in place —
    pass spec_path="spec.py". Do not create a new/uniquely-named file per request.

    SESSION GATE: a remote source needs a live ssh session. Without one the run is
    HELD before anything is read and reports `Status: NEEDS SESSION` — call
    `connect(host)` (a dialog prompts the user; the secret never reaches you) and
    re-run. If it reports the host is UNREACHABLE, that is a VPN/network problem;
    say so rather than offering to authenticate.

    COST GATE: before any bulk read/transfer, the interpreter estimates the run's
    cost (bytes; plus a measured time band for remote transfers). If a pipeline is
    OVER BUDGET (env VISLANG_BUDGET_BYTES / VISLANG_BUDGET_SECONDS) the run is HELD
    — nothing is materialized — and the report shows `Status: NEEDS CONFIRM` with
    the estimate. Do NOT silently re-run with confirm=True: surface the estimate to
    the user and let them choose to (a) commit as-is, or (b) tell you how to narrow
    the spec further. Pass confirm=True only once the user has approved running as-is.

    A spec is built from FORMS (no imports needed). Each form is a declarative
    GOAL that builds a node; nothing reads data until a sink runs:

      source(uri, positions=None)        the dataset — ONE file (inspect under the hood)
      fields(node, keep)                 keep only these variables
      region(node, x=(a,b), ...)         spatial crop (grid: index ranges;
                                         points: world-coordinate bounding box)
      subsample(node, f) | (node, x=..)  reduce resolution (stride / fraction)
      threshold(node, "var > value")     keep where the predicate holds
                                         (points: drop rows; grids: NaN-mask voxels)
      compress(node, variables, error_bound[, mode])
      save(node, path)        [sink]     write the result to disk
      render(node, cmap=None, opacity=None)   [sink] serve the browser viewer

    The forms build an AST; an interpreter inspects the source, static-checks the
    request against the schema (before any bulk read), pushes the structural
    narrowing into one read, and applies value cuts post-read IN WRITTEN ORDER —
    `threshold` then `subsample` samples the survivors; the reverse thresholds
    the sample. `render`/`save` are the sinks that trigger execution — a spec
    with no sink is dry-run (its inferred plan is reported, nothing is
    materialized). Chain forms left-to-right:

        render(subsample(source("data.h5"), 2), cmap="green")

    Returns the inferred plan per pipeline, any printed URLs/paths, and errors.
    """
    return do_execute(spec_path, confirm=confirm)


if __name__ == "__main__":
    mcp.run()
