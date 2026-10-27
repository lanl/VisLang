"""The remote reducer entry point.

Runs on the remote, next to the data (invoked as `python vislang_exec.py` over
ssh — no container in v1; the repo root keeps a thin `vislang_exec.py` launcher
that delegates here, so the remote invocation is a stable path). It consumes a
declarative plan.json (the serialized narrowing prefix of a pipeline — data,
never code), rebuilds the AST through ast_serialize's strict validator, appends
a save() sink, and hands it to the *same* plan_pipeline the local interpreter
uses. The reduced arrays land in --out (an .npz); a small meta JSON (schema +
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

# Run from anywhere. This file may be launched as a plain script over ssh
# (`python .../executor.py`), which puts only its OWN directory on sys.path — so
# put the repo root there too, three levels up, before importing `vislang.*`.
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

# Soundness: the reducer never calls the LLM. But it MAY reuse a frozen binding
# (cache-only) so its schema — variable names, grid dims — agrees with whatever
# the local planner froze when it built this plan. A cache miss falls back to
# the generic listing; no generated code ever runs here.
os.environ.setdefault("VISLANG_BINDING_CACHE_ONLY", "1")
# The reducer writes a TRANSIENT npz that is pulled and deleted; a provenance
# record here would describe a temp sink and a cluster-local path. Nothing opens
# a provenance run on this side, so records are already suppressed — this is the
# second lock, so a future refactor that does open one still cannot emit.
os.environ.setdefault("VISLANG_PROVENANCE", "0")

META_BEGIN = "===VISLANG_META_BEGIN==="
META_END = "===VISLANG_META_END==="
PLAN_VERSION = 1


def _fail(msg):
    meta = {"vislang_exec": PLAN_VERSION, "ok": False, "error": msg}
    print(f"{META_BEGIN}\n{json.dumps(meta)}\n{META_END}")
    return 1


def _jsonable_attrs(attributes):
    """Schema attributes as plain JSON types. The outer json.dumps uses
    default=str, which would turn an array-valued attribute into its repr — and
    some attributes are DATA the caller reads back (GenericIO's phys_scale /
    phys_origin, which a format-preserving save needs), so lists must stay lists."""
    import numpy as np
    out = {}
    for key, value in (attributes or {}).items():
        if isinstance(value, np.ndarray):
            out[key] = value.tolist()
        elif isinstance(value, (list, tuple)):
            out[key] = [v.item() if isinstance(v, np.generic) else v for v in value]
        elif isinstance(value, np.generic):
            out[key] = value.item()
        else:
            out[key] = value
    return out


def _geometry_desc(info):
    """The schema's description of where `info`'s grid sits (no points)."""
    from vislang.formats.dataset_info import geometry_descriptor
    return geometry_descriptor(getattr(info, "geometry", None))


def _inspect_report(path):
    """Metadata-only inspect on the remote (no bulk read, no data shipped): print
    the schema meta between the sentinels. For HDF5 the raw schema tree is
    included so the CALLER can bind locally (bindings are keyed by a
    filesystem-independent schema signature). Invoked with VISLANG_NO_BINDING=1,
    so the listing is generic; an unrecognized format reports needs_adapter (the
    caller then falls back to a whole-file fetch — no model runs here)."""
    from vislang.formats.inspect import inspect_file
    from vislang.formats import adapters
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
            from vislang.formats import schema_binding
            schema_tree = schema_binding.extract_schema(path)
        except Exception:
            schema_tree = None

    meta = {
        "vislang_exec": PLAN_VERSION, "ok": True, "needs_adapter": False,
        "schema": {
            "variables": list(info.variables),
            "dimensions": dict(info.dimensions or {}),
            "positions": list(info.positions) if info.positions else None,
            "attributes": _jsonable_attrs(info.attributes),
            # Live metadata for the caller's cost estimate (honest byte math
            # instead of a 4 B/element guess). NOT persisted to the catalog —
            # remote_reduce._catalog_schema strips it before store_schema.
            "itemsizes": {k: int(v) for k, v in (info.itemsizes or {}).items()},
            "filetype": info.filetype,
            # Where the grid sits (origin + spacing, or "points listed") — what
            # the caller needs to read a region in the file's own coordinates.
            "geometry": _geometry_desc(info),
        },
        "schema_tree": schema_tree,
    }
    print(f"{META_BEGIN}\n{json.dumps(meta, default=str)}\n{META_END}")
    return 0


def _rebuild(text):
    """Strictly rebuild the AST from a wire plan; return (terminal, err_rc).
    A sink in the plan is rejected — the reducer appends its own save()."""
    from vislang.dsl import reset_sinks
    from vislang.dsl.ast_serialize import from_plan_json, PlanValidationError
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


# The reducer runs DOWNSTREAM of the caller's cost gate: a plan only reaches this
# machine because the local planner already priced it and the user confirmed (or it
# held locally and nothing shipped). Re-gating here would re-litigate a decision
# already made, against a budget this side happens to configure differently — and
# the two sides need not agree, since the local budget prices a NETWORK transfer
# while this one would price a local read. So the remote leg is always confirmed.
_CONFIRMED = True


def _held_reason(result):
    """A message when `result` came back un-materialized, else None.

    A HOLD is not an exception: the planner returns a result dict with
    needs_confirm/needs_allocation and no output. Callers that assume a written
    file must check, or the hold surfaces far downstream as a FileNotFoundError on
    the output path — which is exactly how a low remote budget once masqueraded as
    a missing reduce file."""
    if result is None or result.get("materialized"):
        return None
    if result.get("needs_confirm"):
        return ("remote plan HELD over budget and produced no output — the remote "
                "budget (VISLANG_BUDGET_BYTES/SECONDS) is stricter than the "
                "caller's, which already confirmed this request")
    if result.get("needs_allocation"):
        return "remote plan HELD: no Slurm allocation on the remote"
    return "remote plan produced no output (not materialized, no reason given)"


def _run_single(text, out):
    """SINGLE-file reduce (unchanged behavior): save the narrowed arrays to an
    .npz and report the reduced file + schema + saved variables to the caller."""
    from vislang.dsl.forms import save
    from vislang.formats.inspect import inspect_file
    from vislang.interpreter.planner import plan_pipeline

    terminal, err = _rebuild(text)
    if err is not None:
        return err
    out = out if out.endswith(".npz") else out + ".npz"
    try:
        result = plan_pipeline(save(terminal, out), dry_run=False, confirm=_CONFIRMED)
    except Exception as e:
        return _fail(f"{type(e).__name__}: {e}")
    held = _held_reason(result)
    if held:
        return _fail(held)

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
            # The caller stores this schema over the login-node one, so leaving
            # attributes out erased GenericIO's phys_scale/dtypes from the catalog
            # and the loaded result — and a .gio save of it then refused.
            "attributes": _jsonable_attrs(info.attributes),
            # Likewise the geometry: without it the next run could not tell a
            # world region from a cell one.
            "geometry": _geometry_desc(info),
        },
        "steps": result["steps"],
        "saved_variables": saved,
    }
    print(f"{META_BEGIN}\n{json.dumps(meta)}\n{META_END}")
    return 0


def _manifest_entries(manifest):
    """[(label|None, tag|None, vars|None)] from a delta manifest, validated —
    it crossed the wire. Two forms: the folder form {"<label>": [vars]}, and the
    named-box form [{"label": N|null, "tag": "v1", "vars": [...]|null}] (a null
    label is the one box on a single file; null vars means every variable)."""
    from vislang.dsl.forms import valid_tag
    if isinstance(manifest, dict):
        out = []
        for k, v in manifest.items():
            if not (isinstance(v, list) and v and all(isinstance(x, str) for x in v)):
                raise ValueError(f"manifest: timestep {k!r} needs a list of variables")
            out.append((int(k), None, list(v)))
        return out
    if not isinstance(manifest, list):
        raise ValueError("manifest must be {label: [vars]} or a list of entries")
    out = []
    for e in manifest:
        if not isinstance(e, dict) or set(e) != {"label", "tag", "vars"}:
            raise ValueError(f"manifest: bad entry {e!r}")
        label, tag, vars_ = e["label"], e["tag"], e["vars"]
        if label is not None and (isinstance(label, bool) or not isinstance(label, int)):
            raise ValueError(f"manifest: label must be an integer or null, got {label!r}")
        if not valid_tag(tag):
            raise ValueError(f"manifest: bad tag {tag!r}")
        if vars_ is not None and not (isinstance(vars_, list) and vars_
                                      and all(isinstance(x, str) for x in vars_)):
            raise ValueError(f"manifest: vars must be null or a list of names, got {vars_!r}")
        out.append((label, tag, vars_))
    return out


def _unit_middle(narrowing_nodes, keep, tag=None):
    """The chain for ONE manifest entry: the plan's narrowing forms (with a named
    box's own view in place of the named-box region), then a projection to
    `keep` (None keeps every variable). Its per-timestep box is cut by the
    planner (_boxes_at) against the file's own schema."""
    from vislang.dsl.nodes import FieldsNode
    from vislang.interpreter.track import for_tag
    nodes = list(for_tag(narrowing_nodes, tag) if tag is not None else narrowing_nodes)
    if keep:
        nodes.append(FieldsNode(upstream=None, keep=tuple(keep)))
    return nodes


def _run_folder_delta(text, manifest_path, outdir):
    """Catalog DELTA reduce: fetch ONLY the pieces the caller's catalog is
    missing. The plan carries the source plus its narrowing (region/subsample/
    threshold — no fields); the manifest names exactly what each piece still
    needs — per timestep of a folder ({"<label>": [vars]}), or per named box
    (entries with a label, or a null label on a single file, and a tag). Each
    file is inspected once and all its pieces are cut together (one read where
    the format slices in memory), bundled into ONE outdir/bundle.npz keyed as
    reduce.delta_key, so a single directory pull carries only the delta.
    Fully-cached pieces are absent from the manifest and never touched here.

    A piece's vars may name GEOMETRY_KEY: the cut's place in space (a grid's
    origin + spacing, or every point's position), returned as one array under
    that name. A piece that needs only that still cuts one column to carry it,
    but does not send the column back. A null vars list (every variable) also
    gets the geometry of a grid."""
    import json as _json
    import numpy as np
    from vislang.dsl.nodes import upstream_of
    from vislang.formats.dataset_info import GEOMETRY_KEY, encode_geometry
    from vislang.formats.inspect import timestep_files, inspect_file
    from vislang.interpreter.planner import _materialize_units
    from vislang.interpreter.track import tagged_region
    from vislang.remote.reduce import delta_key

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
        entries = _manifest_entries(_json.loads(open(manifest_path).read()))
        tagged = tagged_region(narrowing)
    except (OSError, ValueError) as e:
        return _fail(f"cannot read manifest: {e}")
    if any((t is None) != (tagged is None) for _, t, _ in entries):
        return _fail("manifest entries must name a tag exactly when the plan's "
                     "region names its boxes")

    by_label = {}
    for label, tag, keep in entries:
        by_label.setdefault(label, []).append((tag, keep))
    try:
        if os.path.isdir(src_node.uri):
            files = dict(timestep_files(src_node.uri))     # {label: path}
        else:
            files = {None: src_node.uri}                   # named boxes on one file
    except Exception as e:
        return _fail(f"{type(e).__name__}: {e}")

    os.makedirs(outdir, exist_ok=True)
    bundle, schema, fetched = {}, None, []
    try:
        for label, pieces in by_label.items():
            path = files.get(label)
            if path is None:
                where = "a single file" if None in files else src_node.uri
                return _fail(f"manifest names timestep #{label} not present in {where}")
            info = inspect_file(path, positions=src_node.positions)
            if schema is None:
                schema = {"variables": list(info.variables),
                          "dimensions": dict(info.dimensions or {}),
                          "positions": list(info.positions) if info.positions else None,
                          "filetype": info.filetype,
                          "attributes": _jsonable_attrs(info.attributes),
                          "geometry": _geometry_desc(info)}
            grid = "grid" in (info.dimensions or {})
            units = []                       # (tag, keep, columns to read, send geometry)
            for tag, keep in pieces:
                cols = None if keep is None else [v for v in keep if v != GEOMETRY_KEY]
                geom = grid and (keep is None or GEOMETRY_KEY in keep)
                read = cols if cols != [] else list(info.variables[:1])
                units.append((tag, keep, cols, read, geom))
            cut = _materialize_units(info, [_unit_middle(narrowing, read, tag)
                                            for tag, _, _, read, _ in units],
                                     label, tags=[u[0] for u in units])
            for (tag, keep, cols, _, geom), (loaded, _) in zip(units, cut):
                if cols != []:
                    for var, arr in loaded.data.items():
                        bundle[delta_key(label, tag, var)] = np.asarray(arr)
                if geom:
                    bundle[delta_key(label, tag, GEOMETRY_KEY)] = \
                        encode_geometry(loaded.geometry)
                fetched.append({"label": label, "tag": tag, "vars": keep})
    except Exception as e:
        where = f"timestep #{label}: " if label is not None else ""
        return _fail(f"{where}{type(e).__name__}: {e}")

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
    out.add_argument("--outdir", help="output DIRECTORY for a catalog delta bundle "
                                      "(with --manifest)")
    ap.add_argument("--manifest", help="catalog delta manifest — {label: [vars]}, or "
                                       "[{label, tag, vars}] for named boxes (with "
                                       "--outdir: fetch only these pieces)")
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

    if args.outdir:
        # The remote only cuts and returns arrays; files are written on the
        # caller's side, where the provenance record is made. So a directory
        # reduce is always a catalog delta.
        if not args.manifest:
            return _fail("--outdir needs --manifest (the remote no longer writes "
                         "output files itself; update the caller's checkout)")
        return _run_folder_delta(text, args.manifest, args.outdir)
    return _run_single(text, args.out)


if __name__ == "__main__":
    sys.exit(main())
