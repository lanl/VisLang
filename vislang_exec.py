"""The remote reducer entry point (REMOTE_COMPUTE_PLAN.md Phase 2).

Runs on the remote, next to the data (invoked as `python vislang_exec.py` over
ssh — no container in v1). It consumes a declarative plan.json (the serialized
narrowing prefix of a pipeline — data, never code), rebuilds the AST through
ast_serialize's strict validator, appends a save() sink, and hands it to the
*same* plan_pipeline the local interpreter uses. The reduced arrays land in --out (an .npz); a small meta JSON (schema +
plan steps + saved variables) is printed to stdout between sentinel lines so
the caller can parse it out of any adapter chatter.

Soundness: the executor is a fixed, audited artifact; each request is inert
data validated against an allowlist (ast_serialize.from_plan_json). The LLM
schema-binding path is disabled outright — the reducer must be deterministic
and must never call out to a model.

Usage (local test or remote via ssh):
    python vislang_exec.py --plan plan.json --out reduced.npz
    ... | python vislang_exec.py --stdin --out reduced.npz
"""

import argparse
import json
import os
import sys

# Run from anywhere: this file's directory is the repo root.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Soundness: the reducer never calls the LLM. But it MAY reuse a frozen binding
# (cache-only) so its schema — variable names, grid dims — agrees with whatever
# the local planner froze when it built this plan. A cache miss falls back to
# the generic listing; no generated code ever runs here.
os.environ.setdefault("VISLANG_BINDING_CACHE_ONLY", "1")

META_BEGIN = "===VISLANG_META_BEGIN==="
META_END = "===VISLANG_META_END==="
PLAN_VERSION = 1


def _fail(msg):
    meta = {"vislang_exec": PLAN_VERSION, "ok": False, "error": msg}
    print(f"{META_BEGIN}\n{json.dumps(meta)}\n{META_END}")
    return 1


def _inspect_report(path):
    """Metadata-only inspect on the remote (no bulk read, no data shipped): print
    the schema meta between the sentinels. For HDF5 the raw schema tree is
    included so the CALLER can bind locally (bindings are keyed by a
    filesystem-independent schema signature). Invoked with VISLANG_NO_BINDING=1,
    so the listing is generic; an unrecognized format reports needs_adapter (the
    caller then falls back to a whole-file fetch — no model runs here)."""
    from my_inspect import inspect_file
    import adapters
    try:
        info = inspect_file(path)
    except adapters.NeedsAdapterError as e:
        meta = {"vislang_exec": PLAN_VERSION, "ok": True, "needs_adapter": True,
                "evidence": e.evidence}
        print(f"{META_BEGIN}\n{json.dumps(meta)}\n{META_END}")
        return 0
    except Exception as e:
        return _fail(f"{type(e).__name__}: {e}")

    schema_tree = None
    if getattr(info, "filetype", None) == "HDF5":
        try:
            import schema_binding
            schema_tree = schema_binding.extract_schema(path)
        except Exception:
            schema_tree = None

    meta = {
        "vislang_exec": PLAN_VERSION, "ok": True, "needs_adapter": False,
        "schema": {
            "variables": list(info.variables),
            "dimensions": dict(info.dimensions or {}),
            "positions": list(info.positions) if info.positions else None,
            "attributes": dict(info.attributes or {}),
            "filetype": info.filetype,
        },
        "schema_tree": schema_tree,
    }
    print(f"{META_BEGIN}\n{json.dumps(meta, default=str)}\n{META_END}")
    return 0


def _rebuild(text):
    """Strictly rebuild the AST from a wire plan; return (terminal, err_rc).
    A sink in the plan is rejected — the reducer appends its own save()."""
    from dsl_forms import reset_sinks
    from ast_serialize import from_plan_json, PlanValidationError
    reset_sinks()
    try:
        terminal = from_plan_json(text)
    except PlanValidationError as e:
        return None, _fail(f"plan rejected: {e}")
    if getattr(terminal, "is_sink", False):
        return None, _fail("a remote prefix must not contain a sink (render/save); "
                           "the reducer appends its own save()")
    reset_sinks()
    return terminal, None


def _run_single(text, out):
    """SINGLE-file reduce (unchanged behavior): save the narrowed arrays to an
    .npz and report the reduced file + schema + saved variables to the caller."""
    from dsl_forms.forms import save
    from my_inspect import inspect_file
    from planner import plan_pipeline

    terminal, err = _rebuild(text)
    if err is not None:
        return err
    out = out if out.endswith(".npz") else out + ".npz"
    try:
        result = plan_pipeline(save(terminal, out), dry_run=False)
    except Exception as e:
        return _fail(f"{type(e).__name__}: {e}")

    # Schema for the caller's catalog: re-inspect is metadata-only and cheap.
    chain_head = terminal
    while getattr(chain_head, "upstream", None) is not None:
        chain_head = chain_head.upstream
    info = inspect_file(chain_head.uri, positions=chain_head.positions)

    import numpy as np
    with np.load(out) as z:
        saved = list(z.files)

    meta = {
        "vislang_exec": PLAN_VERSION,
        "ok": True,
        "out": out,
        "schema": {
            "variables": list(info.variables),
            "dimensions": dict(info.dimensions or {}),
            "positions": list(info.positions) if info.positions else None,
            "filetype": info.filetype,
        },
        "steps": result["steps"],
        "saved_variables": saved,
    }
    print(f"{META_BEGIN}\n{json.dumps(meta)}\n{META_END}")
    return 0


def _run_folder(text, outdir):
    """FOLDER (timeseries) reduce: the source is a *local* directory here (we run
    next to the data), so plan_pipeline dispatches to _plan_folder, which lists
    the timesteps, narrows each, and save_timeseries()-writes one file per step
    into `outdir`. We report the dir + per-timestep labels — NOT the arrays (the
    caller pulls the directory once). Must not np.load / inspect the source as a
    file: the source is a directory."""
    from dsl_forms.forms import save
    from planner import plan_pipeline

    terminal, err = _rebuild(text)
    if err is not None:
        return err
    try:
        result = plan_pipeline(save(terminal, outdir), dry_run=False)  # dir, not .npz
    except Exception as e:
        return _fail(f"{type(e).__name__}: {e}")

    meta = {
        "vislang_exec": PLAN_VERSION,
        "ok": True,
        "outdir": result.get("output") or outdir,
        "timesteps": result.get("timesteps", []),
        "steps": result["steps"],
    }
    print(f"{META_BEGIN}\n{json.dumps(meta, default=str)}\n{META_END}")
    return 0


def _reroot(narrowing_nodes, path, positions, keep):
    """Re-root the folder's narrowing forms (region/subsample/threshold, already
    validated) onto ONE timestep file, ending in a projection to `keep`. Used by
    the catalog delta path to fetch just the missing variables of one timestep."""
    from dsl_forms.nodes import (SourceNode, RegionNode, SubsampleNode,
                                 ThresholdNode, FieldsNode)
    node = SourceNode(uri=path, positions=positions)
    for nn in narrowing_nodes:
        if nn.kind == "region":
            node = RegionNode(upstream=node, ranges=nn.ranges)
        elif nn.kind == "subsample":
            node = SubsampleNode(upstream=node, uniform=nn.uniform, per_axis=nn.per_axis)
        elif nn.kind == "threshold":
            node = ThresholdNode(upstream=node, var=nn.var, op=nn.op, value=nn.value)
    return FieldsNode(upstream=node, keep=tuple(keep))


def _run_folder_delta(text, manifest_path, outdir):
    """Catalog DELTA reduce for a folder: fetch ONLY the (timestep, variable)
    pairs the caller's catalog is missing. The plan carries source(dir) plus the
    per-file narrowing (region/subsample/threshold — no fields); the manifest
    {"<label>": [vars]} names exactly what each timestep still needs. We narrow
    each listed file to its missing vars and bundle the results into one
    outdir/bundle.npz keyed "<label>/<var>", so a single directory pull carries
    only the delta. Fully-cached timesteps are absent from the manifest and never
    touched here."""
    import json as _json
    import numpy as np
    from dsl_forms import reset_sinks
    from dsl_forms.forms import save
    from dsl_forms.nodes import upstream_of
    from planner import plan_pipeline
    from my_inspect import timestep_files, inspect_file

    terminal, err = _rebuild(text)
    if err is not None:
        return err
    # Walk to the source; collect the narrowing forms (skip any timesteps node —
    # the manifest enumerates exact labels).
    chain, node = [], terminal
    while node is not None:
        chain.append(node)
        node = upstream_of(node)
    chain.reverse()
    src_node = chain[0]
    narrowing = [n for n in chain[1:]
                 if n.kind in ("region", "subsample", "threshold")]

    try:
        manifest = _json.loads(open(manifest_path).read())
    except (OSError, ValueError) as e:
        return _fail(f"cannot read manifest: {e}")

    try:
        files = dict(timestep_files(src_node.uri))     # {label: path}
    except Exception as e:
        return _fail(f"{type(e).__name__}: {e}")

    os.makedirs(outdir, exist_ok=True)
    bundle, schema, fetched = {}, None, {}
    tmp = os.path.join(outdir, "_scratch.npz")
    try:
        for label_str, keep in manifest.items():
            label = int(label_str)
            path = files.get(label)
            if path is None:
                return _fail(f"manifest names timestep #{label} not present in "
                             f"{src_node.uri}")
            if schema is None:
                info = inspect_file(path, positions=src_node.positions)
                schema = {"variables": list(info.variables),
                          "dimensions": dict(info.dimensions or {}),
                          "positions": list(info.positions) if info.positions else None,
                          "filetype": info.filetype}
            reset_sinks()
            terminal_file = save(_reroot(narrowing, path, src_node.positions, keep), tmp)
            try:
                plan_pipeline(terminal_file, dry_run=False)
                with np.load(tmp) as z:
                    for var in z.files:
                        bundle[f"{label}/{var}"] = z[var]
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
            fetched[label_str] = list(keep)
    except Exception as e:
        return _fail(f"{type(e).__name__}: {e}")

    np.savez(os.path.join(outdir, "bundle.npz"), **bundle)
    meta = {"vislang_exec": PLAN_VERSION, "ok": True,
            "outdir": outdir, "bundle": "bundle.npz",
            "fetched": fetched, "schema": schema}
    print(f"{META_BEGIN}\n{json.dumps(meta, default=str)}\n{META_END}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="VisLang remote reducer / inspector")
    src = ap.add_mutually_exclusive_group(required=False)
    src.add_argument("--plan", help="path to plan.json")
    src.add_argument("--stdin", action="store_true", help="read plan.json from stdin")
    out = ap.add_mutually_exclusive_group(required=False)
    out.add_argument("--out", help="output .npz path (single-file reduce)")
    out.add_argument("--outdir", help="output DIRECTORY (folder/timeseries reduce)")
    ap.add_argument("--manifest", help="catalog delta manifest {label: [vars]} "
                                       "(with --outdir: fetch only these per-file vars)")
    ap.add_argument("--inspect", metavar="PATH",
                    help="metadata-only inspect: print schema meta and exit "
                         "(no --plan/--out needed)")
    args = ap.parse_args(argv)

    if args.inspect:
        return _inspect_report(args.inspect)
    if not (args.plan or args.stdin) or not (args.out or args.outdir):
        return _fail("need (--plan | --stdin) and (--out | --outdir), "
                     "unless --inspect PATH")

    try:
        text = sys.stdin.read() if args.stdin else open(args.plan).read()
    except OSError as e:
        return _fail(f"cannot read plan: {e}")

    if args.outdir and args.manifest:
        return _run_folder_delta(text, args.manifest, args.outdir)
    return _run_folder(text, args.outdir) if args.outdir else _run_single(text, args.out)


if __name__ == "__main__":
    sys.exit(main())
