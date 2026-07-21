#!/usr/bin/env python3
import io
import os
import traceback
from contextlib import redirect_stdout, redirect_stderr

from mcp.server.fastmcp import FastMCP
from my_inspect import inspect_source, is_remote, remote_schema_tree
from my_estimate import estimate_render_cost as _estimate_render_cost, format_estimate
from adapters import NeedsAdapterError

from dsl_forms import form_namespace, reset_sinks, collected_sinks, leaf_nodes
from planner import plan_pipeline, format_result

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


mcp = FastMCP("VisLang Data Management", instructions=(
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


def _adapter_handshake(err):
    """Turn a NeedsAdapterError into instructions for the session model: write a
    reader for this format and submit it. No API/second model is involved — you
    (the model reading this) are the generator; `submit_adapter` is the verifier."""
    return (
        "NEEDS_ADAPTER\n"
        f"No installed reader recognizes {err.filepath!r}. Write a reader adapter "
        "for this format and submit it:\n"
        "  1. Read the guide: instructions/writing-adapters.md.\n"
        "  2. Write a self-contained Python module defining FILETYPE, EXTENSIONS,\n"
        "     inspect(filepath), and read_array(filepath, location) — use an\n"
        "     installed reader library, never hand-parse bytes.\n"
        "  3. Call submit_adapter(filepath, module_code). It runs the module\n"
        "     against THIS real file; on success it is frozen + registered so\n"
        "     future files of this format skip the model, and on failure it\n"
        "     returns the violation so you can fix and resubmit.\n\n"
        "--- File evidence ---\n"
        f"{err.evidence or '(evidence unavailable — is the path readable?)'}"
    )


@mcp.tool()
def inspect(filepath: str, positions: str = None) -> str:
    """Read a file's schema — variables, dimensions, attributes — metadata only, no bulk data.

    Use this to write a spec: you need to know what fields and axes exist before
    you can `region`, `subsample`, `fields`, or `threshold` them. `inspect` is the
    engine behind the `source()` form; calling it here is the same read, just so
    you can see the schema while authoring.

    If no reader recognizes the file, this returns a `NEEDS_ADAPTER` handshake:
    follow it to write a reader module and call `submit_adapter`. If `filepath` is
    a FOLDER, it is a TIMESERIES (files named `…#N`, N = timestep): this returns a
    listing of the timesteps + the shared schema; narrow it with the usual forms
    and pick a range with `timesteps(node, start, stop)`.

    positions: optional "x,y,z" override naming the spatial-coordinate variables
    when auto-detection can't tell (e.g. particle data with unusual names).
    """
    # A FOLDER is a TIMESERIES — locally (an os.path check) or on a remote host
    # (a metadata-only `stat` over ssh). Either way, list its timesteps + shared
    # schema instead of trying to read the directory as one file.
    if is_remote(filepath):
        from my_inspect import remote_is_dir, remote_folder_listing
        if remote_is_dir(filepath):
            return remote_folder_listing(filepath)
    elif os.path.isdir(filepath):
        from my_inspect import folder_listing
        return folder_listing(filepath)
    try:
        pos = tuple(p.strip() for p in positions.split(",")) if positions else None
        info = inspect_source(filepath, positions=pos)
    except NeedsAdapterError as e:
        return _adapter_handshake(e)
    except Exception as e:
        return f"ERROR inspecting {filepath}: {type(e).__name__}: {e}"
    return str(info) + _binding_offer(filepath, info)


def _binding_offer(filepath, info):
    """For an HDF5 shown as a generic listing, append an offer to enrich it with a
    semantic binding via submit_binding. Returns '' when not applicable (not HDF5,
    binding force-disabled, or a binding is already frozen — then the listing
    above is already the rich one). Works for a remote source via the schema tree
    the remote inspect already shipped (info._remote_schema_tree)."""
    if getattr(info, "filetype", None) != "HDF5" or os.environ.get("VISLANG_NO_BINDING"):
        return ""
    try:
        import schema_binding
        tree = getattr(info, "_remote_schema_tree", None)
        if tree is not None:
            evidence = schema_binding.format_schema(tree)     # remote: no data moved
        elif is_remote(filepath):
            return ""   # remote + a cached binding was already applied -> no offer
        elif schema_binding.has_cached_binding(filepath):
            return ""
        else:
            evidence = schema_binding.schema_evidence(filepath)
    except Exception:
        return ""
    return (
        "\n\nBINDING_AVAILABLE\n"
        "This HDF5 shows only a generic dataset listing (raw paths). You can "
        "enrich it with a semantic binding (physical names, dimensions, where "
        "attributes live) so specs read cleanly:\n"
        "  1. Read the guide: instructions/writing-bindings.md.\n"
        "  2. Propose a binding JSON from the schema tree below.\n"
        "  3. Call submit_binding(filepath, binding_json) — it verifies every "
        "claim against the file's own schema and freezes it on success, or "
        "returns the violation to fix. Binding is optional; the listing works.\n\n"
        "--- HDF5 schema tree ---\n"
        f"{evidence}"
    )


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
    from schema_binding import verify_and_freeze_binding
    schema = None
    if is_remote(filepath):
        schema = remote_schema_tree(filepath)   # shipped tree; no data moved
        if schema is None:
            return ("BINDING error: could not fetch the remote schema (needs ssh "
                    "key auth). Inspect the file first, or bind a local copy.")
    try:
        info = verify_and_freeze_binding(filepath, binding_json, schema=schema)
    except Exception as e:
        return (f"BINDING REJECTED: {type(e).__name__}: {e}\n\n"
                f"Fix the binding and call submit_binding again.")
    return "BINDING ACCEPTED — verified and frozen.\n\n" + str(info)


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
    from llm_adapter import conform_and_freeze
    buf = io.StringIO()
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            report = conform_and_freeze(filepath, module_code)
    except Exception as e:
        tb = traceback.format_exc(limit=6).rstrip()
        return (f"ADAPTER REJECTED — failed conformance against the real file.\n"
                f"{type(e).__name__}: {e}\n\n"
                f"Fix the module and call submit_adapter again.\n\n"
                f"--- traceback ---\n{tb}")
    body = "\n".join(f"  {k}: {v}" for k, v in report.items())
    return (f"ADAPTER ACCEPTED — validated, frozen, and registered.\n{body}\n\n"
            f"Re-run inspect({filepath!r}) to author against it.")


@mcp.tool()
def estimate_render_cost(filepath: str) -> str:
    """Predict the cost of rendering a dataset and recommend a subsample — BEFORE loading.

    Reads only metadata (no bulk data). Use this on an unfamiliar or large file
    before rendering: render is headless k3d and ships the array to the browser,
    so a full "show everything" view can be huge. The report gives the estimated
    browser payload, the disk-read cost (and whether narrowing reduces it), and a
    ready-to-use recommendation to keep the first overview responsive.
    """
    try:
        return format_estimate(_estimate_render_cost(filepath))
    except Exception as e:
        return f"ERROR estimating {filepath}: {type(e).__name__}: {e}"


def _run_one(node, dry_run):
    """Plan+execute one pipeline; return (ok, formatted_text)."""
    try:
        return True, format_result(plan_pipeline(node, dry_run=dry_run))
    except Exception as e:
        return False, f"[{getattr(node, 'kind', '?')}] FAILED: {type(e).__name__}: {e}"


@mcp.tool()
def run_pipeline(spec_path: str) -> str:
    """Execute a declarative DSL spec at spec_path and return an execution report.

    Convention: keep the spec in a single file named `spec.py`, edited in place —
    pass spec_path="spec.py". Do not create a new/uniquely-named file per request.

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
    try:
        with open(spec_path) as f:
            spec_code = f.read()
    except FileNotFoundError:
        return f"ERROR: spec file not found: {spec_path}"
    except Exception as e:
        return f"ERROR reading spec: {type(e).__name__}: {e}"

    from vislang_trace import session_banner, log_run
    session_banner()                       # delimit this MCP session in the log (once)

    reset_sinks()
    ctx = form_namespace()
    try:
        exec(compile(spec_code, spec_path, "exec"), ctx)
    except Exception:
        report = (f"Status: BUILD FAILED\nSpec: {spec_path}\n\n"
                  f"--- Error ---\n{traceback.format_exc().rstrip()}")
        log_run(spec_path, report)
        return report

    sinks = collected_sinks()
    dry = not sinks
    targets = leaf_nodes(ctx) if dry else sinks

    # Execute (or dry-run). This is where reads happen, so capture stdout here.
    buf = io.StringIO()
    results = []
    any_failed = False
    
    with redirect_stdout(buf), redirect_stderr(buf):
        for t in targets:
            passed, text = _run_one(t, dry_run=dry)
            any_failed = any_failed or not passed
            results.append(text)
    output = buf.getvalue().rstrip()

    parts = [f"Status: {'FAILED' if any_failed else 'OK'}", f"Spec: {spec_path}"]
    if dry:
        parts.append("\n(no render()/save() sink — dry run: inferred plan only, "
                     "nothing materialized)")
    if results:
        parts.append("\n--- Pipelines ---\n" + "\n\n".join(results))
    if output:
        parts.append(f"\n--- Output ---\n{output}")
    report = "\n".join(parts)
    log_run(spec_path, report)             # persist the full report (append) to the run log
    return report


if __name__ == "__main__":
    mcp.run()
