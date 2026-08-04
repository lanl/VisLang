"""Shared engine logic behind BOTH front-ends.

VisLang has two doors onto the same interpreter:
  - the MCP server (`mcp_server.py`), driven by an LLM session, and
  - the `sieve` terminal CLI (`cli.py`), driven by a human.

Neither should own the logic. Every function here takes plain strings and
returns the same formatted string both front-ends print — the MCP tools wrap
these verbatim, and the CLI subcommands call them directly. Keep behavior here;
keep transport (tool decorators / argparse) in the front-ends.
"""
import io
import os
import traceback
from contextlib import redirect_stdout, redirect_stderr

from my_inspect import inspect_source, is_remote, remote_schema_tree
from my_estimate import estimate_render_cost as _estimate_render_cost, format_estimate
from adapters import NeedsAdapterError

from dsl_forms import form_namespace, reset_sinks, collected_sinks, leaf_nodes
from planner import plan_pipeline, format_result
from sandbox import execute, SandboxError


# --- inspect -----------------------------------------------------------------

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


def do_inspect(filepath, positions=None):
    """Read a source's schema (metadata only). `positions` is an optional
    "x,y,z" string naming the spatial-coordinate variables. A FOLDER is a
    timeseries — return its listing instead of reading it as one file."""
    from my_download import clear_remote_caches
    clear_remote_caches()                  # one inspect is its own run (see do_execute)
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


# --- execute / estimate ------------------------------------------------------

def _run_one(node, dry_run, confirm=False):
    """Plan+execute one pipeline; return (ok, hold_kind, formatted_text) where
    hold_kind is None, 'confirm' (over budget), or 'allocation' (no Slurm alloc)."""
    try:
        result = plan_pipeline(node, dry_run=dry_run, confirm=confirm)
        hold = ('allocation' if result.get("needs_allocation")
                else 'confirm' if result.get("needs_confirm") else None)
        return True, hold, format_result(result)
    except Exception as e:
        return False, None, f"[{getattr(node, 'kind', '?')}] FAILED: {type(e).__name__}: {e}"


def do_execute(spec_path, confirm=False, force_dry=False):
    """Execute (or, with force_dry, only plan+cost) the DSL spec at spec_path and
    return the formatted execution report.

    force_dry=True is the `estimate` path: forms are built and static-checked
    against the source schema and the run is costed, but NOTHING is materialized
    even when the spec has a render()/save() sink. force_dry=False runs sinks for
    real (subject to the budget cost gate; pass confirm=True to commit an
    over-budget run the user has approved). A spec with no sink is a dry run
    either way.
    """
    # The spec is ALWAYS a local Python file; remoteness belongs inside it, in
    # source("ssh://…"). Both mistakes below are easy to make and produce errors
    # that point at the wrong thing (a binary read as UTF-8; "not found" for a
    # path that was never meant to be opened), so name the actual problem.
    if is_remote(spec_path):
        return (f"ERROR: a spec is a LOCAL file, but this looks like a remote "
                f"source URI:\n  {spec_path}\n\n"
                f"The remote path goes INSIDE the spec, in source():\n"
                f"    # spec.py\n"
                f'    save(subsample(fields(source("{spec_path}"), ["var"]), 2), '
                f'"out.npz")\n\n'
                f"then run it locally:  sieve estimate spec.py\n"
                f"To read a remote source's schema instead:  sieve inspect <uri>")
    try:
        with open(spec_path) as f:
            spec_code = f.read()
    except FileNotFoundError:
        return f"ERROR: spec file not found: {spec_path}"
    except (UnicodeDecodeError, IsADirectoryError) as e:
        why = ("it is a directory" if isinstance(e, IsADirectoryError)
               else "it is binary, not Python source")
        return (f"ERROR: {spec_path} is not a spec — {why}.\n\n"
                f"`estimate`/`execute` take a spec file (a few lines of DSL forms), "
                f"not a data file. For a data file you want:\n"
                f"    sieve inspect {spec_path}       # its schema, metadata only\n"
                f"    sieve render-cost {spec_path}   # its render payload cost")
    except Exception as e:
        return f"ERROR reading spec: {type(e).__name__}: {e}"

    from vislang_trace import session_banner, log_run
    from my_download import clear_remote_caches
    import vislang_timing as timing
    session_banner()                       # delimit this session in the log (once)
    # Connection auth and per-path identity (size/mtime/header hash) are cached
    # for the duration of ONE run and re-read on the next: within a run the facts
    # cannot meaningfully change, and across runs a stale identity would let the
    # extent catalog answer from a file that has since been rewritten.
    clear_remote_caches()

    # One timings.jsonl record per run — the machine-readable twin of the prose
    # report (vislang_timing.py). `meta` is the open record: the status below and
    # every phase/counter inside plan_pipeline land in it.
    with timing.run(spec_path, spec_code=spec_code, confirm=confirm) as meta:
        reset_sinks()
        try:
            ctx = execute(spec_code, form_namespace())
        except (SandboxError, SyntaxError) as e:
            report = (f"Status: BUILD FAILED\nSpec: {spec_path}\n\n"
                      f"--- Error ---\n{e}")
            meta["status"] = "BUILD FAILED"
            meta["error"] = f"{type(e).__name__}: {e}"
            log_run(spec_path, report)
            return report

        sinks = collected_sinks()
        has_sinks = bool(sinks)
        # No sink -> dry run of the leaves. estimate (force_dry) -> plan the real
        # sink pipelines but never materialize.
        dry = force_dry or not has_sinks
        targets = sinks if has_sinks else leaf_nodes(ctx)
        meta["dry_run"] = dry
        meta["n_pipelines"] = len(targets)

        # Execute (or dry-run). This is where reads happen, so capture stdout here.
        buf = io.StringIO()
        results = []
        any_failed = False
        any_budget_hold = False
        any_alloc_hold = False

        with redirect_stdout(buf), redirect_stderr(buf):
            for t in targets:
                passed, hold, text = _run_one(t, dry_run=dry, confirm=confirm)
                any_failed = any_failed or not passed
                any_budget_hold = any_budget_hold or (hold == 'confirm')
                any_alloc_hold = any_alloc_hold or (hold == 'allocation')
                results.append(text)
        output = buf.getvalue().rstrip()

        status = ("FAILED" if any_failed else
                  "NEEDS ALLOCATION" if any_alloc_hold else
                  "NEEDS CONFIRM" if any_budget_hold else "OK")
        meta["status"] = status
    parts = [f"Status: {status}", f"Spec: {spec_path}"]
    if any_alloc_hold:
        parts.append("\n(NO SLURM ALLOCATION — one or more pipelines were HELD before "
                     "shipping the server-side reduce; nothing was materialized. Tell "
                     "the user there is no allocation and ASK PERMISSION to create the "
                     "proposed one (the salloc line above); on approval, run it — the "
                     "harness will still prompt for that specific command. Once it is "
                     "RUNNING, re-run the spec. Never run salloc without approval.)")
    if any_budget_hold:
        parts.append("\n(OVER BUDGET — one or more pipelines were HELD; nothing was "
                     "materialized for them. Show the estimate to the user and let "
                     "them choose: commit as-is (re-run with confirm=True) or narrow "
                     "the spec. Do not auto-confirm.)")
    if not has_sinks:
        parts.append("\n(no render()/save() sink — dry run: inferred plan only, "
                     "nothing materialized)")
    elif force_dry and not any_failed:
        # Only claim the check when it actually succeeded — a failed estimate
        # already says why, and asserting "static-checked" above it would be the
        # same false reassurance this path was fixed to remove.
        parts.append("\n(estimate — dry run: forms static-checked against the source "
                     "schema and the run costed; nothing materialized. Run `execute` "
                     "to materialize.)")
    if results:
        parts.append("\n--- Pipelines ---\n" + "\n\n".join(results))
    if output:
        parts.append(f"\n--- Output ---\n{output}")
    report = "\n".join(parts)
    log_run(spec_path, report)             # persist the full report (append) to the run log
    return report


def do_estimate(spec_path):
    """Check a spec against source metadata and estimate its cost, materializing
    nothing — the `sieve estimate` path. Thin alias for do_execute(force_dry)."""
    return do_execute(spec_path, confirm=False, force_dry=True)


# --- render-cost / handshake verifiers ---------------------------------------

def do_estimate_render_cost(filepath):
    """Predict a single file's render payload + disk-read cost (metadata only)."""
    try:
        return format_estimate(_estimate_render_cost(filepath))
    except Exception as e:
        return f"ERROR estimating {filepath}: {type(e).__name__}: {e}"


def do_submit_adapter(filepath, module_code):
    """Verify a reader adapter module against the real file; freeze + register on
    success. `module_code` is the module source as a string (the CLI reads it from
    a file). Returns the acceptance report or the rejection + traceback."""
    from llm_adapter import conform_and_freeze
    buf = io.StringIO()
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            report = conform_and_freeze(filepath, module_code)
    except Exception as e:
        tb = traceback.format_exc(limit=6).rstrip()
        return (f"ADAPTER REJECTED — failed conformance against the real file.\n"
                f"{type(e).__name__}: {e}\n\n"
                f"Fix the module and submit it again.\n\n"
                f"--- traceback ---\n{tb}")
    body = "\n".join(f"  {k}: {v}" for k, v in report.items())
    return (f"ADAPTER ACCEPTED — validated, frozen, and registered.\n{body}\n\n"
            f"Re-run inspect({filepath!r}) to author against it.")


def do_submit_binding(filepath, binding_json):
    """Verify a semantic binding against an HDF5 file's own metadata; freeze on
    success. `binding_json` is the binding as a JSON string. Returns the accepted
    schema or the rejection reason."""
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
                f"Fix the binding and submit it again.")
    return "BINDING ACCEPTED — verified and frozen.\n\n" + str(info)
