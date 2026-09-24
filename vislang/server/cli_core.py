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

from vislang.formats.inspect import inspect_source, is_remote, remote_schema_tree
from vislang.interpreter.estimate import estimate_render_cost as _estimate_render_cost, format_estimate
from vislang.formats.adapters import NeedsAdapterError

from vislang.dsl import form_namespace, reset_sinks, collected_sinks, leaf_nodes
from vislang.interpreter.planner import plan_pipeline, format_result
from vislang.runtime.sandbox import execute, SandboxError


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
        from vislang.formats import schema_binding
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


def _session_handshake(uri):
    """NEEDS_SESSION handshake for a remote source we cannot reach or drive
    non-interactively, or '' when the session is fine.

    Authentication is settled HERE, at authoring time, for the same reason
    adapters and bindings are: `inspect` is the step that always precedes a spec,
    so the run path can assume what authoring established instead of discovering
    it half-way through a materialize."""
    from vislang.remote.reduce import session_check
    hold = session_check(uri)
    if hold is None:
        return ""
    if not hold.reachable:
        # Unreachable and unauthenticated fail identically at the ssh layer but
        # need opposite fixes. Saying "type your password" to someone whose VPN
        # is down sends them to a dialog that cannot possibly succeed.
        return (
            "NEEDS_SESSION\n"
            f"{hold.host} is not reachable — no TCP connection to it at all. This "
            f"is a network problem, not a credential one: check the VPN (WSU "
            f"hosts are restricted off-campus) or the hostname.\n\n"
            f"Nothing was read. Tell the user; do not offer to authenticate."
        )
    return (
        "NEEDS_SESSION\n"
        f"{hold.host} needs an interactive login and no VisLang session is open.\n\n"
        f"Open one, then re-run this inspect:\n"
        f"  1. Call connect({hold.target!r})  —  CLI: {hold.connect_cmd}\n"
        f"  2. A password dialog appears on the USER's screen. They type it there;\n"
        f"     it goes straight to ssh. It never reaches VisLang, this transcript,\n"
        f"     or you. You only learn whether a session opened.\n"
        f"  3. Re-run inspect once connect reports the session is live.\n\n"
        f"The session then serves every later inspect, estimate and run until it "
        f"expires. Nothing was read."
    )


def do_inspect(filepath, positions=None):
    """Read a source's schema (metadata only). `positions` is an optional
    "x,y,z" string naming the spatial-coordinate variables. A FOLDER is a
    timeseries — return its listing instead of reading it as one file."""
    from vislang.remote.download import clear_remote_caches
    clear_remote_caches()                  # one inspect is its own run (see do_execute)
    # A remote source with no session cannot be inspected next to the data, and
    # the fallback (fetch the whole file, inspect it locally) needs the same
    # session. Ask for one instead of pulling gigabytes or failing obscurely.
    if is_remote(filepath):
        handshake = _session_handshake(filepath)
        if handshake:
            return handshake
    # A FOLDER is a TIMESERIES — locally (an os.path check) or on a remote host
    # (a metadata-only `stat` over ssh). Either way, list its timesteps + shared
    # schema instead of trying to read the directory as one file.
    if is_remote(filepath):
        from vislang.formats.inspect import remote_is_dir, remote_folder_listing
        if remote_is_dir(filepath):
            return remote_folder_listing(filepath)
    elif os.path.isdir(filepath):
        from vislang.formats.inspect import folder_listing
        return folder_listing(filepath)
    try:
        pos = tuple(p.strip() for p in positions.split(",")) if positions else None
        info = inspect_source(filepath, positions=pos)
    except NeedsAdapterError as e:
        return _adapter_handshake(e)
    except Exception as e:
        return f"ERROR inspecting {filepath}: {type(e).__name__}: {e}"
    return str(info) + _binding_offer(filepath, info)


# --- sessions ----------------------------------------------------------------

def _target_of(host):
    """The ssh target for a bare host, an alias, or any remote URI.

    `connect("ssh://gpu-server/scratch/run1")` and `connect("gpu-server")` mean
    the same thing — the model has a URI in hand, not a hostname, and making it
    strip the path itself is a needless step to get wrong."""
    from vislang.interpreter.planner import _normalize_remote
    from vislang.remote.download import _parse_remote
    text = (host or "").strip()
    if is_remote(text):
        try:
            user, hostname, _ = _parse_remote(_normalize_remote(text))
            return f"{user}@{hostname}" if user else hostname
        except ValueError:
            pass
    return text.rstrip("/")


def do_connect(host, timeout=120):
    """Open (or confirm) a session for `host`, then report what it can do.

    The secret never passes through here. `open_master` hands OpenSSH an
    SSH_ASKPASS helper; ssh forks it, a dialog appears on the USER's screen, and
    the answer goes back over ssh's own pipe. This function learns one bit —
    whether a session came up — and the model that called it learns the same."""
    from vislang.remote.download import (clear_remote_caches, master_alive, host_reachable,
                             open_master, establish_connection)
    from vislang.remote.hosts import preflight, hosts_file
    target = _target_of(host)
    if not target:
        return "ERROR: connect needs a host, e.g. connect('gpu-server')."
    clear_remote_caches()          # a session may have opened since the last probe

    if master_alive(target):
        opened = f"Session already live for {target}."
    else:
        # Reachability first. An unreachable host and an unauthenticated one fail
        # identically at the ssh layer, and showing a password dialog to someone
        # whose VPN is down wastes their time on something that cannot work.
        if not host_reachable(target):
            return (f"UNREACHABLE: no TCP connection to {target}.\n\n"
                    f"This is a network problem, not a credential one — check the "
                    f"VPN (WSU hosts are restricted off-campus) or the hostname. "
                    f"No password was requested, because one could not have helped.")
        ok, detail = open_master(target, timeout=timeout)
        if not ok:
            return (f"NOT CONNECTED: could not open a session to {target}.\n\n"
                    f"{detail}\n\n"
                    f"Nothing was read. If no dialog appeared, open the session "
                    f"from a terminal instead: sieve connect {target}")
        opened = f"Session opened for {target}."

    conn = establish_connection(f"{target}:/")
    if not conn.batch_ok:
        return (f"{opened}\nBut a BatchMode command still fails, so the session "
                f"is not usable. Check ~/.ssh permissions and that "
                f"VISLANG_SSH_MUX is not set to 0.")

    lines = [opened, "Non-interactive commands now work — inspect, estimate and "
                     "run will use it until it expires."]
    check = preflight(conn)
    if check["ok"]:
        lines.append(f"Reducer ready: python={check['python']} repo={check['repo']}")
        lines.append("Ready. Re-run the inspect or spec that asked for this.")
    else:
        lines.append("")
        lines.append("The session works, but this host cannot run the reducer yet:")
        lines += [f"  - {p}" for p in check["problems"]]
        lines.append(f"Configure it in {hosts_file()}. Until then, remote work "
                     f"falls back to fetching whole files.")
    return "\n".join(lines)


def do_disconnect(host):
    """Close the session for `host` (a no-op if none is open)."""
    from vislang.remote.download import close_master, clear_remote_caches
    target = _target_of(host)
    clear_remote_caches()
    if close_master(target):
        return f"Session closed for {target}."
    return f"No session was open for {target}."


# --- execute / estimate ------------------------------------------------------

def _run_one(node, dry_run, confirm=False):
    """Plan+execute one pipeline; return (ok, hold_kind, formatted_text) where
    hold_kind is None, 'session' (no live ssh session), 'confirm' (over budget),
    or 'allocation' (no Slurm alloc)."""
    try:
        result = plan_pipeline(node, dry_run=dry_run, confirm=confirm)
        hold = ('session' if result.get("needs_session")
                else 'allocation' if result.get("needs_allocation")
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

    from vislang.runtime.trace import session_banner, log_run
    from vislang.remote.download import clear_remote_caches
    import vislang.runtime.timing as timing
    import vislang.runtime.provenance as provenance
    session_banner()                       # delimit this session in the log (once)
    # Connection auth and per-path identity (size/mtime/header hash) are cached
    # for the duration of ONE run and re-read on the next: within a run the facts
    # cannot meaningfully change, and across runs a stale identity would let the
    # extent catalog answer from a file that has since been rewritten.
    clear_remote_caches()

    # One timings.jsonl record per run — the machine-readable twin of the prose
    # report (timing.py). `meta` is the open record: the status below and
    # every phase/counter inside plan_pipeline land in it.
    # The derivation record that travels with each output (provenance.py). Opened
    # here rather than derived from `meta` because timing short-circuits entirely
    # under VISLANG_TIMING=0 and would take provenance down with it.
    with timing.run(spec_path, spec_code=spec_code, confirm=confirm) as meta, \
            provenance.run(spec_path, spec_code=spec_code):
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
        any_session_hold = False

        with redirect_stdout(buf), redirect_stderr(buf):
            for t in targets:
                passed, hold, text = _run_one(t, dry_run=dry, confirm=confirm)
                any_failed = any_failed or not passed
                any_budget_hold = any_budget_hold or (hold == 'confirm')
                any_alloc_hold = any_alloc_hold or (hold == 'allocation')
                any_session_hold = any_session_hold or (hold == 'session')
                results.append(text)
        output = buf.getvalue().rstrip()

        # A missing session outranks the other holds: without one nothing can be
        # priced or scheduled, so reporting "over budget" or "no allocation"
        # would name a downstream symptom instead of the cause.
        status = ("FAILED" if any_failed else
                  "NEEDS SESSION" if any_session_hold else
                  "NEEDS ALLOCATION" if any_alloc_hold else
                  "NEEDS CONFIRM" if any_budget_hold else "OK")
        meta["status"] = status
    parts = [f"Status: {status}", f"Spec: {spec_path}"]
    if any_session_hold:
        parts.append("\n(NO SESSION — one or more pipelines were HELD before "
                     "anything was read. The remote needs an interactive login. "
                     "Call connect(host) (the command is shown above): a password "
                     "dialog opens on the USER's screen and the secret goes "
                     "straight to ssh — never through VisLang, this report, or "
                     "you. Then re-run the spec. If the report says the host is "
                     "UNREACHABLE, do not offer to authenticate — say the network "
                     "or VPN is the problem.)")
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
    from vislang.formats.llm_adapter import conform_and_freeze
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
    from vislang.formats.schema_binding import verify_and_freeze_binding
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


# ---------------------------------------------------------------------------
# Provenance: read a record back, and re-execute the spec it carries
# ---------------------------------------------------------------------------
def _verify_source(rec):
    """(status, detail) comparing the recorded source identity against the file
    as it stands now. Remote sources are not probed — that would need a live
    session, and this command must stay cheap and offline."""
    from vislang.runtime import provenance
    src = rec.get("source") or {}
    ident = src.get("identity") or {}
    uri, site = src.get("uri"), src.get("site")
    if not ident:
        return "UNKNOWN", "no identity recorded"
    if site == "remote" or is_remote(str(uri or "")):
        return "UNVERIFIABLE", "remote source; not probed"
    path = src.get("read_from") or uri
    if not path or not os.path.exists(path):
        return "MISSING", f"{path} is gone"
    diffs = provenance.compare_identity(ident, provenance.local_identity(path))
    if not diffs:
        return "UNCHANGED", ident.get("method", "")
    return "CHANGED", "; ".join(f"{f}: {a} -> {b}" for f, a, b in diffs)


def do_provenance(path, as_json=False, spec_only=False):
    """Print the derivation record for a Sieve output, wherever it is stored."""
    from vislang.runtime import provenance
    rec = provenance.record_for(path)
    if rec is None:
        return (f"ERROR: no provenance record in {path}\n\n"
                f"Records are written by `sieve execute`; a file produced some "
                f"other way, or before provenance existed, carries none.")
    if spec_only:
        spec = (rec.get("transform") or {}).get("spec")
        return spec or "ERROR: this record carries no spec text"
    if as_json:
        return provenance.to_json(rec)

    prod, run = rec.get("producer") or {}, rec.get("run") or {}
    src, tr = rec.get("source") or {}, rec.get("transform") or {}
    out = rec.get("output") or {}
    status, detail = _verify_source(rec)
    dirty = " (uncommitted changes)" if prod.get("commit_dirty") else ""
    lines = [
        f"{os.path.basename(path)} — {rec.get('summary') or ''}",
        f"  written  {rec.get('created')} by sieve {prod.get('version')} "
        f"({prod.get('commit')}){dirty}",
        f"  format   {out.get('format')}"
        + (f", via {out.get('embedding')}" if out.get("embedding") else "")
        + (f"  [degraded from {out['degraded_from']}]" if out.get("degraded_from") else ""),
        "",
        f"  source   {src.get('uri')}",
        f"           {status}  {detail}",
        "",
        f"  did      {tr.get('summary')}",
    ]
    low = tr.get("lowered") or {}
    if low.get("grid_ranges"):
        lines.append(f"  read     grid_ranges={low['grid_ranges']}")
    if low.get("post_ops"):
        lines.append(f"  after    {low['post_ops']}")
    if tr.get("seed") is not None:
        lines.append(f"  seed     {tr['seed']}  (random subsample; rerun reproduces it)")
    variables = rec.get("variables") or {}
    if variables:
        lines += ["", "  variables"]
        for name, v in list(variables.items())[:20]:
            shape = "×".join(str(s) for s in v.get("shape") or [])
            of = "×".join(str(s) for s in v.get("source_shape") or [])
            lines.append(f"    {name:<24} {shape}"
                         + (f"  of {of}" if of else "")
                         + f"  [{v.get('origin')}]")
    chain = rec.get("derived_from") or []
    if chain:
        lines += ["", f"  derived through {len(chain)} earlier step(s):"]
        for anc in chain:
            lines.append(f"    <- {((anc.get('source') or {}).get('uri'))}")
    spec = tr.get("spec")
    lines += ["", f"  spec     {len((spec or '').splitlines())} lines embedded "
                  f"(sha {run.get('spec_sha')}) — see --spec"]
    return "\n".join(lines)


def do_rerun(path, out=None, new_source=None, force=False, dry_run=False,
             confirm=False):
    """Re-execute the spec embedded in a Sieve output."""
    import json as _json
    from vislang.runtime import provenance
    from vislang.dsl.ast_serialize import from_plan, PlanValidationError

    rec = provenance.record_for(path)
    if rec is None:
        return f"ERROR: no provenance record in {path}"
    plan = (rec.get("transform") or {}).get("plan")
    if not plan:
        why = (rec.get("transform") or {}).get("plan_error") or "no plan recorded"
        return f"ERROR: {path} is not re-runnable: {why}"

    status, detail = _verify_source(rec)
    if status == "CHANGED" and not force:
        return (f"Status: SOURCE CHANGED\n\n"
                f"{(rec.get('source') or {}).get('uri')}\n  {detail}\n\n"
                f"Re-running would produce a different result under the same "
                f"provenance. Pass --force to proceed (the new output records "
                f"the new identity), or --source to point at the moved data.")
    if status == "MISSING" and not new_source:
        return (f"ERROR: the source is gone — {detail}\n\n"
                f"Pass --source <uri> if it moved.")

    plan = _json.loads(_json.dumps(plan))          # don't mutate the record
    chain = plan.get("chain") or []
    if new_source and chain:
        chain[0]["uri"] = new_source
    target = out or (rec.get("output") or {}).get("path")
    if chain and chain[-1].get("kind") == "save":
        # A dry run writes nothing, so the overwrite guard does not apply to it.
        if (not dry_run and not out and not force
                and os.path.abspath(target) == os.path.abspath(path)):
            return (f"ERROR: rerunning would overwrite {path}, the file holding "
                    f"the record. Pass --out <path>, or --force.")
        chain[-1]["path"] = target

    # The seed that produced the original draw, so a fractional subsample comes
    # back identical rather than merely statistically similar.
    seed = (rec.get("transform") or {}).get("seed")
    if seed is not None:
        os.environ["VISLANG_SAMPLE_SEED"] = str(seed)

    # Rebuilding a plan registers its sink as a module-level side effect. Reset
    # before, and again after: this may be running inside a long-lived MCP
    # process, where a stray registration would be collected by the next run.
    reset_sinks()
    try:
        terminal = from_plan(plan)
    except PlanValidationError as e:
        reset_sinks()
        return f"ERROR: the embedded plan is not valid: {e}"
    reset_sinks()

    if dry_run:
        from vislang.dsl.ast_serialize import describe_plan
        return (f"Status: OK (dry run)\nSpec: {path}\n\n"
                f"would run: {describe_plan(plan)}\n"
                f"source:    {status}  {detail}")

    if force and status == "CHANGED":
        # Extents cached against the old bytes would otherwise answer the rerun.
        try:
            from vislang.remote.catalog import ExtentCatalog
            from vislang.runtime.paths import cache_root
            sid = ((rec.get("source") or {}).get("identity") or {}).get("source_id")
            if sid:
                ExtentCatalog(cache_root()).invalidate(sid)
        except Exception:
            pass

    import vislang.runtime.timing as timing
    import vislang.runtime.provenance as prov
    spec_text = (rec.get("transform") or {}).get("spec")
    reset_sinks()
    with timing.run(f"rerun:{path}", spec_code=spec_text), \
            prov.run(f"rerun:{path}", spec_code=spec_text) as prun:
        prun["rerun_of"] = {"record_id": rec.get("record_id"), "from": path}
        _, _, text = _run_one(terminal, dry_run=False, confirm=confirm)
    reset_sinks()
    return f"Status: OK (rerun of {os.path.basename(path)})\n\n{text}"
