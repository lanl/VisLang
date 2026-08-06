"""The interpreter/planner — it turns an AST chain into physical ops.

A spec builds AST nodes (dsl_forms); after exec, run_pipeline hands each
registered sink here. plan_pipeline walks the chain source->sink, inspects the
source for its schema, static-checks the request (raising BEFORE any bulk read),
lowers the forms onto the physical layer, and executes.

Form order is the promise; how we lower it is ours to choose — within what is
provably result-preserving. Every middle form is classified on two independent
axes:

  Pushdown (efficiency): STRUCTURAL cuts are knowable from metadata and fold
  into the one read (projection via `fields`; grid index crop via `region`;
  grid stride / leading particle row-stride via `subsample`). COMPUTED cuts
  need the data and run after the read (particle `region` bbox, `threshold`,
  demoted subsamples).

  Reordering (correctness): every form here is an ABSOLUTE cut (a fixed rule —
  ANDed cuts commute freely) EXCEPT `subsample`, the lone RELATIVE sampler
  ("every Nth of what's left"): its result depends on the element set at the
  moment it runs, so no cut may slide across it. Concretely: a particle
  subsample is pushed into the read ONLY if no computed cut precedes it in the
  spec; otherwise it is demoted to a post-read op at its written position.
  Grid subsample composes with grid crops into one [a:b:k] per axis, and grid
  `threshold` NaN-masks in place (shape-preserving), so on grids everything
  commutes and full pushdown is always safe.

The lowered plan is one Narrowing: the pushdown layer (project + grid_ranges /
particle_index) plus `post_ops`, the computed cuts in written order, applied by
materialize after the read.
"""

import os
import re

import vislang_timing as timing        # per-phase seconds/bytes -> timings.jsonl
from dsl_forms.nodes import SourceNode, upstream_of
from my_inspect import inspect_file
from my_load import materialize
from my_compress import compress as _compress
from my_render import render as _render
from adapters import adapter_capabilities
from narrowing import (Narrowing, AxisRange, Predicate, BBox,
                       RowMask, RowSample, VoxelMask,
                       narrowing_from_dimensions, validate_narrowing)

_REMOTE = re.compile(r"^[a-z][a-z0-9+.-]*://|^[^/\s]+@[^/\s]+:")   # scheme:// or user@host:
_AXIS_INDEX = {"x": 0, "y": 1, "z": 2}


# --- human-in-the-loop tracing ---------------------------------------------
# Every plan narrates itself through result["steps"] (what format_result shows).
# VISLANG_TRACE=0 quiets the extra lines; the core plan lines always show.
def _tracing():
    return os.environ.get("VISLANG_TRACE", "1") != "0"


def _trace(steps, msg):
    if _tracing():
        steps.append(msg)


def _loaded_bytes(loaded):
    """Bytes actually materialized in memory, for the timing record. This is the
    ACTUAL against which the cost estimate's predicted `output_mb` is scored."""
    try:
        return int(sum(a.nbytes for a in (loaded.data or {}).values()))
    except Exception:
        return None


def _describe_ast(chain):
    """The chain in WRITTEN order, before the planner lowers it — so the human
    sees the spec exactly as authored next to how it was optimized."""
    parts = []
    for n in chain:
        k = n.kind
        if k == "source":
            parts.append(f"source({os.path.basename(n.uri.rstrip('/')) or n.uri})")
        elif k == "fields":
            parts.append(f"fields[{','.join(n.keep)}]")
        elif k == "region":
            parts.append("region{" + ",".join(f"{a}:({lo},{hi})"
                                               for a, lo, hi in n.ranges) + "}")
        elif k == "subsample":
            parts.append(f"subsample({n.uniform})" if n.uniform is not None
                         else f"subsample({dict(n.per_axis)})")
        elif k == "threshold":
            parts.append(f"threshold({n.var}{n.op}{n.value})")
        elif k == "timesteps":
            parts.append(f"timesteps({n.start},{n.stop})")
        elif k == "compress":
            parts.append(f"compress({list(n.variables)},eb={n.error_bound})")
        else:
            parts.append(k)                    # save / render
    return "spec (as written): " + " → ".join(parts)


def _describe_schema(info, where):
    """What inspect returned — the schema the static checks run against."""
    pos = (f", positions={tuple(info.positions)}"
           if getattr(info, "positions", None) else "")
    return (f"inspect {where} → {info.filetype}: vars={list(info.variables)}, "
            f"dims={info.dimensions or {}}{pos}")


def _normalize_remote(uri):
    """ssh://[user@]host/path -> scp-style [user@]host:/path that _parse_remote reads."""
    if uri.startswith("ssh://"):
        rest = uri[len("ssh://"):]
        if "/" not in rest:
            raise ValueError(f"ssh URL needs a path: {uri!r}")
        hostpart, path = rest.split("/", 1)
        return f"{hostpart}:/{path}"
    return uri


def _remote_fetch_gate(uri, sink, terminal, steps, confirm):
    """Estimate + gate a whole-file remote FETCH before pulling. The whole file
    crosses the wire, so wire bytes == file size (known from one stat). Returns a
    HELD result dict when over budget and unconfirmed, else None (proceed). Any
    probe failure is non-fatal — the fetch proceeds and its own path reports."""
    try:
        from my_download import (establish_connection, remote_stat,
                                  measure_bandwidth, _parse_remote)
        from datasetInfo import DatasetInfo
        from my_estimate import estimate_plan_cost
        norm = _normalize_remote(uri)
        _, _, remote_path = _parse_remote(norm)
        conn = establish_connection(norm)
        st = remote_stat(conn, remote_path)
        if st is None:
            return None
        size_mb = st[0] / (1024 ** 2)
        net_bw = measure_bandwidth(conn)
        stub = DatasetInfo(uri, "remote", [])
        est = estimate_plan_cost(info=stub, narrowing=None, site="remote",
                                 read_mb_override=size_mb, net_bw_bps=net_bw,
                                 sink_kind=(sink.kind if sink is not None else None))
        result = {"kind": (sink.kind if sink is not None else terminal.kind),
                  "uri": uri, "steps": steps, "output": None, "materialized": False}
        if _gate(result, est, confirm, steps):
            return result
    except Exception as e:
        steps.append(f"(fetch estimate skipped: {type(e).__name__}: {e})")
    return None


def _remote_parts(connection, remote_path):
    """Sibling `<base>#N` partition paths for a multi-part snapshot, or [].

    GenericIO writes one file per MPI rank next to a small header, and the reader
    follows the header to the parts — so the path a spec names is NOT all of the
    data. One `ls` expands the glob remotely; a format with no such siblings
    simply yields nothing and the caller behaves as before.

    NB this is the same `…#N` spelling VisLang uses for TIMESTEPS. The collision
    is safe here because we only reach this function for a path that already
    resolved to a FILE (remote_is_dir was false): a timeseries is a DIRECTORY of
    `#N` files, never a file with `#N` siblings."""
    import shlex
    from my_download import _ssh_query
    out = _ssh_query(connection.target,
                     f"ls -1d {shlex.quote(remote_path)}#* 2>/dev/null")
    return [ln for ln in (out or "").split("\n") if ln.strip()]


def _fetch_remote(uri):
    """Establish a connection and transfer a remote source to a local cache,
    returning the local path.

    Multi-part formats need their siblings too. Fetching only the named path
    leaves a local HEADER whose parts are missing, and the read then fails (or
    worse, reads nothing) — so pull `<base>#N` into the same directory, where the
    reader expects to find them."""
    from my_download import establish_connection, transfer, _parse_remote
    from vislang_paths import downloads_dir
    norm = _normalize_remote(uri)
    _, _, remote_path = _parse_remote(norm)
    cache_dir = downloads_dir()
    os.makedirs(cache_dir, exist_ok=True)
    local = os.path.join(cache_dir, os.path.basename(remote_path.rstrip("/")) or "download")
    conn = establish_connection(norm)
    out = transfer(conn, remote_path, local)
    if out is None:
        raise RuntimeError(f"remote transfer of {uri} was cancelled")
    parts = _remote_parts(conn, remote_path)
    for i, part in enumerate(parts, 1):
        print(f"[fetch] partition {i}/{len(parts)}: {os.path.basename(part)}")
        if transfer(conn, part, os.path.join(cache_dir,
                                             os.path.basename(part))) is None:
            raise RuntimeError(f"remote transfer of partition {part} was cancelled")
    return out


# ---------------------------------------------------------------------------
def _chain(terminal):
    """Ordered [source, ..., terminal] by following `upstream`. Validates head."""
    nodes = []
    n = terminal
    while n is not None:
        nodes.append(n)
        n = upstream_of(n)
    nodes.reverse()
    if not isinstance(nodes[0], SourceNode):
        raise ValueError("pipeline does not begin at source(); every chain must "
                         "start from source(path)")
    return nodes


def _modality(info):
    """'particles' if the dataset has coordinate vars / a particle count, else
    'grid'. Decides how region/subsample/threshold lower."""
    if getattr(info, "positions", None):
        return "particles"
    if "particles" in (info.dimensions or {}):
        return "particles"
    return "grid"


def _step_from_factor(factor):
    """A subsample factor -> integer stride. int stride stays; float fraction ->
    nearest integer stride (legacy grid semantics: keep ~fraction per axis)."""
    if isinstance(factor, int):
        return factor
    return max(1, int(round(1 / factor)))


def _grid_ranges(region_nodes, subsample_nodes, grid):
    """Fuse grid region crops + subsample strides into one [a:b:k] per axis.
    Both are structural index-space directives on the ORIGINAL grid, so they
    compose order-free by convention (the stride anchors at the crop start).
    `grid` is the grid shape (info.dimensions['grid']); the extent catalog reuses
    this same routine to key cached grid extents, so a cached slice is identical
    to a fresh remote reduce (my_catalog._fuse_forms)."""
    if not (region_nodes or subsample_nodes):
        return None
    if not grid:
        raise ValueError("region/subsample needs grid dimensions, but none were "
                         "detected for this dataset (the HDF5 binding may have "
                         "fallen back to a generic listing)")
    ndim = len(grid)

    def axis_idx(axis):
        if axis not in _AXIS_INDEX or _AXIS_INDEX[axis] >= ndim:
            raise ValueError(f"unknown axis {axis!r}; this grid has {ndim} axes (x, y, z)")
        return _AXIS_INDEX[axis]

    ranges = [AxisRange(None, None, 1) for _ in range(ndim)]
    for rn in region_nodes:                       # crop [lo:hi] per named axis
        for axis, lo, hi in rn.ranges:
            i = axis_idx(axis)
            ranges[i] = AxisRange(lo, hi, ranges[i].step)
    for sn in subsample_nodes:                    # compose stride onto each axis
        if sn.uniform is not None:
            step = _step_from_factor(sn.uniform)
            ranges = [AxisRange(r.start, r.stop, step) for r in ranges]
        else:
            for axis, f in sn.per_axis:
                i = axis_idx(axis)
                ranges[i] = AxisRange(ranges[i].start, ranges[i].stop, _step_from_factor(f))
    return ranges

def _bbox_of(region_node):
    """A particle region: a world-coordinate box on the position variables."""
    lo, hi = [None, None, None], [None, None, None]
    for axis, a, b in region_node.ranges:
        if axis not in _AXIS_INDEX:
            raise ValueError(f"region axis {axis!r} not one of x, y, z")
        i = _AXIS_INDEX[axis]
        lo[i], hi[i] = a, b
    return BBox(lo=tuple(lo), hi=tuple(hi))


def plan_pipeline(terminal, dry_run=False, confirm=False):
    """Plan (and unless dry_run, execute) one pipeline ending at `terminal`.

    `terminal` is normally a sink (render/save). For the no-sink dry run it may
    be any dangling leaf node, in which case there is no action to run and the
    call only reports the inferred plan.

    A remote source (ssh:// or user@host:) dispatches by SITE: if the chain has
    a narrowing prefix and the remote is reachable, the prefix executes next to
    the data (via vislang_exec) and only the reduced result crosses the wire
    (see remote_reduce.py); otherwise we fall back to the whole-file fetch and
    plan locally as before. Render/save always run locally.

    Returns a result dict: {kind, uri, steps, output, materialized}.
    Raises on a bad spec (with a clear message) — the caller reports per-sink.
    """
    chain = _chain(terminal)
    src = chain[0]
    sink = terminal if terminal.is_sink else None
    middle = chain[1:-1] if sink is not None else chain[1:]

    # timesteps() is a TIME-axis selector for a folder (timeseries), not a
    # per-file narrowing — split it out of the middle before dispatch.
    ts_nodes = [n for n in middle if n.kind == "timesteps"]
    middle = [n for n in middle if n.kind != "timesteps"]

    # One measured record per sink (vislang_timing): the forms as written, the
    # site chosen, and every phase timed inside it. No-op when timing is off.
    with timing.pipeline(kind=(sink.kind if sink is not None else terminal.kind),
                         uri=src.uri, forms=[n.kind for n in middle],
                         dry_run=bool(dry_run or sink is None),
                         ts_range=([max(n.start for n in ts_nodes),
                                    min(n.stop for n in ts_nodes)]
                                   if ts_nodes else None)):
        # A FOLDER source is a timeseries: map the single-file chain over its
        # timestep files (named `…#N`). This holds for a remote folder as much as a
        # local one — we detect it locally with an os.path check, or remotely with a
        # metadata-only `stat` over ssh (planner._plan_remote_folder next to the
        # data, mirroring _plan_folder).
        remote_src = bool(_REMOTE.match(src.uri))
        if remote_src:
            from my_inspect import remote_is_dir
            # The remote folder probe is skipped on a dry run to keep the dry run
            # zero-network (as _plan_remote does): there timesteps() is the folder
            # signal — it applies only to a folder — else we preview the single-file
            # plan. The actual run probes and dispatches for real.
            is_folder = (bool(ts_nodes) if (dry_run or sink is None)
                         else remote_is_dir(src.uri))
        else:
            is_folder = os.path.isdir(src.uri)
        timing.note(site="remote" if remote_src else "local", folder=is_folder)

        if is_folder:
            plan_folder = _plan_remote_folder if remote_src else _plan_folder
            result = plan_folder(src, middle, ts_nodes, sink, terminal, dry_run, confirm)
        elif ts_nodes:
            raise ValueError("timesteps() applies only to a folder (timeseries) "
                             f"source; {src.uri!r} is a single file.")
        elif remote_src:
            result = _plan_remote(src, middle, sink, terminal, dry_run, confirm)
        else:
            result = _plan_local(src, middle, sink, terminal, dry_run, src.uri, [],
                                 confirm)
        timing.note(materialized=bool(result.get("materialized")),
                    held=("allocation" if result.get("needs_allocation")
                          else "budget" if result.get("needs_confirm") else None))

    if _tracing() and isinstance(result, dict) and "steps" in result:
        result["steps"].insert(0, _describe_ast(chain))    # spec as authored, on top
    return result


def _plan_remote(src, middle, sink, terminal, dry_run, confirm=False):
    """Site dispatch for a remote source. v1 cost gate: any narrowing present +
    a reachable remote -> reduce remotely (a threshold can't run locally
    without the whole file anyway; a geometric cut ships fewer bytes by
    construction). No narrowing -> the whole file must cross the wire either
    way, so fetch it. VISLANG_REMOTE=off forces the fetch; =force errors
    instead of falling back (surfacing what broke)."""
    steps = []
    mode = os.environ.get("VISLANG_REMOTE", "auto")
    has_narrowing = any(n.kind in ("fields", "region", "subsample", "threshold")
                        for n in middle)

    # No sink: there is no action to plan, so preview the shape of the chain and
    # touch nothing at all — a chain that ends mid-narrowing is an authoring
    # sketch, not a request, and probing for it would spend round trips on
    # something that cannot run.
    if sink is None:
        steps.append(f"remote source {src.uri}")
        steps.append("plan: reduce on the remote (narrowing prefix next to the "
                     "data), pull the result, run the sink locally"
                     if has_narrowing and mode != "off" else
                     "plan: fetch the whole file, then plan locally")
        steps.append("(no sink — nothing probed, nothing read)")
        for n in middle:
            steps.append(f"  … {n.kind}")
        steps.append(f"(no sink — chain ends at {terminal.kind}; nothing to materialize)")
        return {"kind": terminal.kind, "uri": src.uri, "steps": steps,
                "output": None, "materialized": False}

    # A sink IS present but this is a dry run — the `estimate` intent. Resolve the
    # schema on the login node, static-check the request against it, and price the
    # transfer. Metadata only: nothing is shipped and no bulk data is read.
    if dry_run:
        return _estimate_remote(src, middle, sink, steps, mode, has_narrowing)

    if mode != "off" and has_narrowing:
        from remote_reduce import (remote_reduce, RemoteUnavailable, BudgetHold,
                                   AllocationHold)
        try:
            with timing.phase("remote_reduce"):
                loaded, rsteps, estimate = remote_reduce(src, middle, confirm)
            timing.note(route="remote_reduce")
            timing.note_estimate(estimate)
            steps += rsteps
            pending_compress = [n for n in middle if n.kind == "compress"]
            for c in pending_compress:
                steps.append(f"compress {list(c.variables)} "
                             f"(error_bound={c.error_bound}, local)")
            result = {"kind": sink.kind, "uri": src.uri, "steps": steps,
                      "output": None, "materialized": True, "estimate": estimate}
            steps.append(f"-> {sink.kind} (local)")
            return _finish(loaded, pending_compress, sink, result)
        except AllocationHold as h:
            timing.note(route="held_allocation")
            steps += h.steps
            steps.append("HELD: no Slurm allocation — nothing shipped. Ask the "
                         "user to approve creating one (Claude runs it on approval):")
            steps.append(f"  {h.salloc_cmd}")
            steps.append("Once it is RUNNING, re-run the spec; or re-run with "
                         "confirm=True to proceed without one (whole-file fetch).")
            return {"kind": sink.kind, "uri": src.uri, "steps": steps,
                    "output": None, "materialized": False, "needs_allocation": True,
                    "salloc_cmd": h.salloc_cmd}
        except BudgetHold as h:
            timing.note(route="held_budget")
            timing.note_estimate(h.estimate)
            steps += h.steps
            return {"kind": sink.kind, "uri": src.uri, "steps": steps,
                    "output": None, "materialized": False, "needs_confirm": True,
                    "estimate": h.estimate}
        except RemoteUnavailable as e:
            timing.note(remote_unavailable=str(e))
            if mode == "force":
                raise RuntimeError(f"VISLANG_REMOTE=force but remote reduce is "
                                   f"unavailable: {e}")
            steps.append(f"remote reduce unavailable ({e}) — falling back to "
                         f"whole-file fetch")
    elif not has_narrowing:
        steps.append("no narrowing forms — the whole file crosses the wire "
                     "either way (cost gate: fetch)")

    # Whole-file FETCH path: the entire file crosses the wire. Estimate + gate on
    # the measured transfer cost before pulling.
    timing.note(route="whole_file_fetch")
    held = _remote_fetch_gate(src.uri, sink, terminal, steps, confirm)
    if held is not None:
        timing.note(route="held_budget")
        return held
    with timing.phase("fetch_whole_file"):
        local = _fetch_remote(src.uri)           # establish_connection + transfer
    steps.append(f"fetch {src.uri} -> {local}")
    return _plan_local(src, middle, sink, terminal, dry_run, local, steps, confirm)


def _probe_bandwidth(uri, steps):
    """Measured link speed for a remote estimate's time band, or None. Failure is
    never fatal: a byte estimate without a time band still prices the request."""
    try:
        from my_download import establish_connection, measure_bandwidth
        return measure_bandwidth(establish_connection(_normalize_remote(uri)))
    except Exception as e:
        steps.append(f"(bandwidth probe skipped: {type(e).__name__}: {e})")
        return None


def _estimate_remote(src, middle, sink, steps, mode, has_narrowing, n_timesteps=1,
                     schema_uri=None):
    """Price a remote request WITHOUT running it — the `estimate` path.

    This is a dry run with a sink present, which is a different intent from a
    chain that has no sink: the author has asked what the request would cost and
    whether it is well-posed, and is willing to spend a few metadata round trips
    to find out. So we do the three things that need no bulk data:

      1. read the schema next to the data (login node, no allocation),
      2. lower the forms against it — `_lower` runs validate_narrowing, so a bad
         field / out-of-bounds region / unknown axis raises HERE, locally, before
         anything is shipped,
      3. price the reduced payload, with a measured time band when the link can
         be probed.

    `allow_fetch=False` is the load-bearing argument: pricing a request must
    never be able to pay for it, so a schema that could only be read by
    downloading the file raises SchemaUnavailable instead.

    `schema_uri` names the file whose schema stands for the request (one timestep
    of a folder); `n_timesteps` scales the estimate over the series.
    """
    from my_inspect import inspect_source, SchemaUnavailable
    from my_estimate import estimate_plan_cost, format_plan_estimate

    steps.append(f"remote source {src.uri}")
    steps.append("plan: reduce on the remote (narrowing prefix next to the data), "
                 "pull the result, run the sink locally"
                 if has_narrowing and mode != "off" else
                 "plan: fetch the whole file, then plan locally")

    uri = schema_uri or src.uri
    try:
        with timing.phase("inspect", uri=uri):
            info = inspect_source(uri, positions=src.positions, allow_fetch=False)
    except SchemaUnavailable as e:
        # `estimate` promises a check and a price. If neither can be produced, the
        # command failed — reporting OK would be a green light for a request
        # nothing verified. `execute` still works: it may fetch.
        raise RuntimeError(
            f"cannot estimate without moving data: {e} Run `execute` to let the "
            f"whole-file fallback proceed, or fix the remote reader "
            f"(VISLANG_REMOTE_PYTHON / VISLANG_REMOTE_REPO).") from e
    steps.append(_describe_schema(info, uri))

    with timing.phase("lower"):
        narrowing, _pending = _lower(info, middle, steps)   # raises on a bad request

    estimate = estimate_plan_cost(info=info, narrowing=narrowing, site="remote",
                                  n_timesteps=n_timesteps, sink_kind=sink.kind,
                                  wire_vars=narrowing.project,
                                  net_bw_bps=_probe_bandwidth(src.uri, steps))
    # The estimate prices the WHOLE request. Extents already held locally are not
    # deducted, so this is an upper bound on what would cross — the useful
    # direction for a budget question.
    estimate.notes.append("prices the whole request; extents already cached "
                          "locally are not deducted.")
    timing.note_estimate(estimate)
    steps.append(f"-> {sink.kind} (would run locally)")
    steps.append(format_plan_estimate(estimate))
    return {"kind": sink.kind, "uri": src.uri, "steps": steps, "output": None,
            "materialized": False, "estimate": estimate, "narrowing": narrowing}


def _gate(result, estimate, confirm, steps):
    """Attach a CostEstimate to the result and decide whether to HOLD.

    Always records the estimate (so a run report shows the cost). When the plan
    is over budget and the caller has not passed confirm=True, flags
    needs_confirm and returns True — the caller must return WITHOUT materializing.
    """
    from my_estimate import format_plan_estimate
    result["estimate"] = estimate
    timing.note_estimate(estimate)         # the predicted half of est-vs-actual
    steps.append(format_plan_estimate(estimate))
    if estimate.over_budget and not confirm:
        result["needs_confirm"] = True
        steps.append("HELD: over budget — nothing materialized. Re-run with "
                     "confirm=True to commit, or narrow the spec.")
        return True
    return False


def _finish(loaded, pending_compress, sink, result):
    """The local suffix shared by both sites: compress, then the sink."""
    for c in pending_compress:
        with timing.phase("compress", vars=list(c.variables),
                          error_bound=c.error_bound):
            loaded = _compress(loaded, list(c.variables), c.error_bound, c.mode)
    with timing.phase(f"sink_{sink.kind}", bytes=_loaded_bytes(loaded)):
        if sink.kind == "render":
            _render(loaded, cmap=sink.cmap, opacity=sink.opacity)   # prints its URL
            result["output"] = "rendered (URL in output above)"
        else:  # save
            result["output"] = _save(loaded, sink.path)
    return result


def _lower(info, middle, steps):
    """Classify + lower the middle forms against ONE file's schema into a single
    fused Narrowing (+ any trailing compress nodes). No read happens here, so the
    folder/timeseries loop can call it once per timestep. Appends human-readable
    steps. `middle` must not contain timesteps() nodes — those are a time-axis
    selector consumed before the loop, not a per-file narrowing."""
    modality = _modality(info)
    caps = adapter_capabilities(info)
    slab = "pushdown" if caps["strided_read"] else "in-memory slice"

    # --- classify + lower each middle form, in written order -----------------
    keep = None                    # which fields to project (None = keep all)
    grid_region_nodes = []         # grid crops, saved up to fuse into the read later
    grid_subsample_nodes = []      # grid strides, same idea
    post_ops = []                  # filters that must run AFTER reading, in order
    pushdown_subsample = None      # a particle subsample safe to push into the read
    seen_computed = False          # "have we hit an order-sensitive filter yet?"
    pending_compress = []          # compress form queued for the very end

    for node in middle:
        if pending_compress and node.kind != "compress":
            raise NotImplementedError("narrowing after compress is not supported; "
                                      "put fields/region/subsample/threshold before compress")
        if node.kind == "fields":
            base = keep if keep is not None else list(info.variables)
            invalid = set(node.keep) - set(base)
            if invalid:
                raise ValueError(f"fields: {sorted(invalid)} not available here; "
                                 f"have {sorted(base)}")
            keep = list(node.keep)
            steps.append(f"project fields={keep}")
        elif node.kind == "region":
            if modality == "grid":
                grid_region_nodes.append(node)   # structural index crop
                steps.append(f"region {dict((a, (lo, hi)) for a, lo, hi in node.ranges)} "
                             f"({slab})")
            else:                                # computed world-coord bbox
                post_ops.append(RowMask(bbox=_bbox_of(node)))
                seen_computed = True
                steps.append(f"region {dict((a, (lo, hi)) for a, lo, hi in node.ranges)} "
                             f"(bbox mask, post-read)")
        elif node.kind == "subsample":
            if modality == "grid":
                grid_subsample_nodes.append(node)  # structural stride
                what = (f"uniform={node.uniform}" if node.uniform is not None
                        else str(dict(node.per_axis)))
                steps.append(f"subsample {what} ({slab})")
            else:
                if node.uniform is None:
                    raise ValueError("per-axis subsample doesn't apply to point data; "
                                     "use a single factor, e.g. subsample(d, 0.1)")
                if not seen_computed and pushdown_subsample is None:
                    pushdown_subsample = node      # safe: no computed cut precedes it
                    steps.append(f"subsample uniform={node.uniform} (pushdown)")
                else:
                    # A computed cut (or an earlier sampler) precedes it: pushing
                    # it into the read would silently reorder. Run it post-read,
                    # at its written position.
                    post_ops.append(RowSample(factor=node.uniform))
                    steps.append(f"subsample uniform={node.uniform} "
                                 f"(post-read: follows a computed cut)")
        elif node.kind == "threshold":
            pred = Predicate(node.var, node.op, node.value)
            if modality == "grid":
                post_ops.append(VoxelMask(predicates=(pred,)))  # NaN-fill, commutes
                steps.append(f"threshold {node.var} {node.op} {node.value} "
                             f"(voxel NaN-mask)")
            else:
                post_ops.append(RowMask(predicates=(pred,)))
                seen_computed = True
                steps.append(f"threshold {node.var} {node.op} {node.value} (row mask)")
        elif node.kind == "compress":
            pending_compress.append(node)
            steps.append(f"compress {list(node.variables)} (error_bound={node.error_bound})")
        else:
            raise ValueError(f"unknown form {node.kind!r} in pipeline")

    # --- fuse the structural layer into one Narrowing ------------------------
    if modality == "grid":
        ranges = _grid_ranges(grid_region_nodes, grid_subsample_nodes,
                              (info.dimensions or {}).get("grid"))
        echo = {}
        if ranges:
            echo["grid_ranges"] = [(r.start, r.stop, r.step) for r in ranges]
        narrowing = Narrowing(grid_ranges=ranges, dimensions=echo,
                              positions=getattr(info, "positions", None))
    else:
        total = (info.dimensions or {}).get("particles", 0)
        dims = None
        if pushdown_subsample is not None:
            f = pushdown_subsample.uniform
            dims = {"particles": slice(None, None, f) if isinstance(f, int) else f}
        narrowing = narrowing_from_dimensions(dims, total,
                                              getattr(info, "positions", None))
        echo = dict(narrowing.dimensions or {})

    narrowing.post_ops = tuple(post_ops)
    narrowing.project = tuple(keep) if keep is not None else None
    if keep is not None:
        echo["project"] = keep
    if post_ops:
        echo["post_ops"] = [type(op).__name__ for op in post_ops]
    narrowing.dimensions = echo

    # Static check against the FULL schema (a threshold may read a var outside
    # the projection), before any bulk read.
    validate_narrowing(info, narrowing)

    fuse_bits = []
    if narrowing.grid_ranges:
        fuse_bits.append(f"grid_ranges={echo['grid_ranges']}")
    if narrowing.particle_index is not None:
        fuse_bits.append(f"particles={echo.get('particles')}")
    if narrowing.project is not None:
        fuse_bits.append(f"project={list(narrowing.project)}")
    if post_ops:
        fuse_bits.append(f"post_ops={echo['post_ops']}")
    if fuse_bits:
        steps.append("fuse -> " + ", ".join(fuse_bits))

    return narrowing, pending_compress


def _plan_local(src, middle, sink, terminal, dry_run, source_uri, steps, confirm=False):
    """Plan (and unless dry_run, execute) a SINGLE-file chain: inspect, lower the
    middle forms into one fused Narrowing, then materialize + compress + sink."""
    # Inspect (metadata only) — static checks in _lower run before any bulk read.
    # These two phases are the "cost of a rejected request": everything up to and
    # including validate_narrowing happens on metadata, before any bulk read.
    with timing.phase("inspect", uri=source_uri):
        info = inspect_file(source_uri, positions=src.positions)
    # The on-disk size of what we were pointed at, so a local run reports the same
    # "how much did we avoid touching" denominator the remote paths do.
    try:
        timing.note(source_bytes=os.path.getsize(source_uri))
    except OSError:
        pass
    steps.append(_describe_schema(info, source_uri))
    with timing.phase("lower"):
        narrowing, pending_compress = _lower(info, middle, steps)

    # The sink (the action). A dangling leaf has none — it can only dry-run.
    if sink is not None:
        sink_step = (f"render(cmap={sink.cmap})" if sink.kind == "render"
                     else f"save -> {sink.path}")
        steps.append(f"-> {sink_step}")
    else:
        steps.append(f"(no sink — chain ends at {terminal.kind}; nothing to materialize)")

    result = {"kind": (sink.kind if sink is not None else terminal.kind),
              "uri": src.uri, "steps": steps, "output": None, "materialized": False,
              "narrowing": narrowing}   # the lowered plan, for introspection (explain.py)

    # Cost estimate at the seam: the plan is known, nothing bulky has been read.
    # Local cost is disk->memory, so we report bytes and gate on size only.
    from my_estimate import estimate_plan_cost, format_plan_estimate
    sink_kind = sink.kind if sink is not None else None
    estimate = estimate_plan_cost(info=info, narrowing=narrowing, site='local',
                                  n_timesteps=1, sink_kind=sink_kind)
    timing.note_estimate(estimate)
    if dry_run or sink is None:
        result["estimate"] = estimate                # show the cost, never gate
        steps.append(format_plan_estimate(estimate))
        return result
    if _gate(result, estimate, confirm, steps):
        return result                                # over budget, unconfirmed: HOLD

    # Execute: materialize under the fused narrowing, compress, then the sink.
    with timing.phase("materialize") as _m:
        loaded = materialize(info, narrowing)
        _m["bytes"] = _loaded_bytes(loaded)
    result["materialized"] = True
    for c in pending_compress:
        with timing.phase("compress", vars=list(c.variables),
                          error_bound=c.error_bound):
            loaded = _compress(loaded, list(c.variables), c.error_bound, c.mode)

    with timing.phase(f"sink_{sink.kind}"):
        if sink.kind == "render":
            _render(loaded, cmap=sink.cmap, opacity=sink.opacity)   # prints its URL
            result["output"] = "rendered (URL in output above)"
        else:  # save
            result["output"] = _save(loaded, sink.path)
    return result


def _plan_folder(src, middle, ts_nodes, sink, terminal, dry_run, confirm=False):
    """A FOLDER source is a TIMESERIES: map the single-file chain over the
    timestep files (named `…#N`) and combine. timesteps() nodes (already split
    out of `middle`) pick the inclusive #N range; the rest of the chain lowers
    per file exactly as for one file. render over a series isn't supported — pick
    one timestep, or save the range (one file per timestep, original format)."""
    from my_inspect import timestep_files
    from my_estimate import estimate_plan_cost, format_plan_estimate
    files = timestep_files(src.uri)                     # [(label, path)]; raises if none
    rng = None
    if ts_nodes:
        lo = max(n.start for n in ts_nodes)
        hi = min(n.stop for n in ts_nodes)
        rng = (lo, hi)
        files = [(lab, p) for lab, p in files if lo <= lab <= hi]

    steps = [f"folder timeseries {src.uri}: {len(files)} timestep(s)"
             + (f" in #{rng[0]}..{rng[1]}" if rng else "")]
    if not files:
        raise ValueError(f"no timesteps match {rng} in {src.uri}")

    if sink is not None and sink.kind == "render":
        raise NotImplementedError(
            "render over a timeseries folder isn't supported — select one timestep "
            "with timesteps(node, N, N), or use save() to write the range.")

    # Plan + estimate off timestep 0 (metadata only); folder cost scales × N steps.
    with timing.phase("inspect", uri=files[0][1], n_timesteps=len(files)):
        info0 = inspect_file(files[0][1], positions=src.positions)
    with timing.phase("lower"):
        narrowing0, _ = _lower(info0, middle, [])
    estimate = estimate_plan_cost(info=info0, narrowing=narrowing0, site='local',
                                  n_timesteps=len(files),
                                  sink_kind=(sink.kind if sink is not None else None))
    timing.note_estimate(estimate)

    if dry_run or sink is None:
        steps.append("per timestep: "
                     + (" -> ".join(n.kind for n in middle) if middle else "(no narrowing)"))
        _trace(steps, _describe_schema(info0, files[0][1]))
        _lower(info0, middle, steps)
        steps.append("(no sink — nothing materialized)" if sink is None
                     else f"-> save timeseries -> {sink.path}")
        steps.append(format_plan_estimate(estimate))
        return {"kind": (sink.kind if sink is not None else terminal.kind),
                "uri": src.uri, "steps": steps, "output": None, "materialized": False,
                "estimate": estimate}

    # Gate on the whole-series cost BEFORE materializing any timestep.
    held = {"kind": "save", "uri": src.uri, "steps": steps, "output": None,
            "materialized": False}
    if _gate(held, estimate, confirm, steps):
        return held

    per_step = []
    with timing.phase("materialize_timeseries", n=len(files)) as _m:
        total = 0
        for i, (label, path) in enumerate(files):
            try:                                      # name the failing timestep
                info = inspect_file(path, positions=src.positions)
                if i == 0:                            # echo the schema once
                    _trace(steps, _describe_schema(info, path))
                narrowing, pending_compress = _lower(info, middle, [])   # per-file steps discarded
                loaded = materialize(info, narrowing)
                for c in pending_compress:
                    loaded = _compress(loaded, list(c.variables), c.error_bound, c.mode)
            except Exception as e:
                raise type(e)(f"timestep #{label} ({path}): {e}") from e
            total += _loaded_bytes(loaded) or 0
            per_step.append((label, loaded))
        _m["bytes"] = total
    steps.append(f"materialized {len(per_step)} timestep(s), same chain per file")

    from my_save import save_timeseries
    with timing.phase("sink_save_timeseries", n=len(per_step)):
        out = save_timeseries(per_step, sink.path, per_step[0][1].filetype)
    steps.append(f"-> save timeseries -> {out}")
    return {"kind": "save", "uri": src.uri, "steps": steps, "output": out,
            "materialized": True, "timesteps": [lab for lab, _ in per_step]}


def _fetch_remote_folder(uri):
    """Whole-folder fallback: pull the entire remote directory to a local cache
    dir (one rsync) and return the local path, so the local _plan_folder can map
    the chain over it exactly as for a local folder. Sibling of _fetch_remote."""
    from my_download import establish_connection, transfer_dir, _parse_remote
    from vislang_paths import downloads_dir
    norm = _normalize_remote(uri)
    _, _, remote_path = _parse_remote(norm)
    local = os.path.join(downloads_dir(),
                         os.path.basename(remote_path.rstrip("/")) or "download")
    out = transfer_dir(establish_connection(norm), remote_path, local)
    if out is None:
        raise RuntimeError(f"remote folder fetch of {uri} failed")
    return out


def _plan_remote_folder(src, middle, ts_nodes, sink, terminal, dry_run, confirm=False):
    """A REMOTE folder is a TIMESERIES. The whole timeseries runs in ONE remote
    job next to the data (remote_folder_reduce): the remote's own plan_pipeline
    dispatches the folder to _plan_folder, writes one file per timestep, and we
    pull that directory back once — O(1) in the timestep count, versus the old
    per-file loop's ~7-9 handshakes + Python start + srun step + transfer PER
    file. VISLANG_REMOTE=off / no narrowing / a RemoteUnavailable under `auto`
    fall back to a whole-folder fetch + the local _plan_folder. render over a
    series isn't supported — pick one timestep, or save the range."""
    if sink is not None and sink.kind == "render":
        raise NotImplementedError(
            "render over a timeseries folder isn't supported — select one timestep "
            "with timesteps(node, N, N), or use save() to write the range.")

    mode = os.environ.get("VISLANG_REMOTE", "auto")
    has_narrowing = any(n.kind in ("fields", "region", "subsample", "threshold",
                                   "compress") for n in middle)

    # No sink: nothing to plan, so touch nothing (see _plan_remote).
    if sink is None:
        steps = [f"remote folder timeseries {src.uri}"]
        if ts_nodes:
            lo, hi = max(n.start for n in ts_nodes), min(n.stop for n in ts_nodes)
            steps.append(f"select timesteps #{lo}..{hi}")
        steps.append("plan: run the whole folder in one remote job next to the "
                     "data (the remote lists + narrows every timestep), then pull "
                     "the saved directory once")
        steps.append("(no sink — nothing probed, nothing read)")
        for n in middle:
            steps.append(f"  … {n.kind}")
        steps.append(f"(no sink — chain ends at {terminal.kind}; nothing to materialize)")
        return {"kind": terminal.kind, "uri": src.uri, "steps": steps,
                "output": None, "materialized": False}

    # `estimate` over a series: list the timesteps (one metadata call), then check
    # and price the per-timestep request against timestep 0's schema, scaled by the
    # number of steps selected. Nothing is shipped.
    if dry_run:
        from my_inspect import remote_timestep_files_stat
        steps = [f"remote folder timeseries {src.uri}"]
        with timing.phase("list_timesteps"):
            files = remote_timestep_files_stat(src.uri)
        if files is None:
            steps.append("(could not list the remote folder — needs ssh key auth; "
                         "nothing checked, nothing priced)")
            return {"kind": sink.kind, "uri": src.uri, "steps": steps,
                    "output": None, "materialized": False}
        if ts_nodes:
            lo, hi = max(n.start for n in ts_nodes), min(n.stop for n in ts_nodes)
            files = [f for f in files if lo <= f[0] <= hi]
            steps.append(f"select timesteps #{lo}..{hi}")
        if not files:
            raise ValueError(f"no timesteps match in {src.uri}")
        steps.append(f"{len(files)} timestep(s), "
                     f"{sum(f[2] for f in files) / (1024 ** 2):.0f} MiB of source")
        timing.note(selected_timesteps=len(files),
                    source_bytes=int(sum(f[2] for f in files)))
        return _estimate_remote(src, middle, sink, steps, mode, has_narrowing,
                                n_timesteps=len(files), schema_uri=files[0][1])

    # Execute: one remote job for the whole folder (cost gate mirrors _plan_remote).
    steps = []
    if mode != "off" and has_narrowing:
        from remote_reduce import remote_folder_reduce, RemoteUnavailable
        steps.append("(note: the folder reduce runs as one batched remote job — "
                     "the per-run budget gate is applied on the single-file and "
                     "whole-folder-fetch paths, not to this batch)")
        try:
            with timing.phase("remote_folder_reduce"):
                out, rsteps, report = remote_folder_reduce(src, middle, ts_nodes,
                                                           sink.path)
            # The catalog's reuse at (timestep, variable) granularity — the number
            # the iterative-workflow claim rests on.
            timing.note(route="remote_folder_reduce", n_timesteps=report.get("n"),
                        jobid=report.get("jobid"),
                        reused_pairs=report.get("reused_pairs"),
                        total_pairs=report.get("total_pairs"),
                        fetched_pairs=report.get("fetched_pairs"),
                        cached_timesteps=report.get("cached"),
                        fetched_timesteps=report.get("fetched_timesteps"))
            steps += rsteps
            # The remote ran its OWN plan_pipeline (same tracing code, on darwin) —
            # surface what it actually did when it reports it (non-catalog batch),
            # prefixed so local vs remote is visible at a glance.
            if report.get("remote_steps"):
                steps.append(f"remote:  --- what ran on {report['host']} ---")
                for s in report["remote_steps"]:
                    steps.append(f"remote:  {s}")
            n = report["n"]
            cached, fetched = report.get("cached", 0), report.get("fetched_timesteps", n)
            job = f" (srun jobid={report['jobid']})" if report.get("jobid") else ""
            if report.get("total_pairs") is not None:
                # catalog-aware path: report reuse at the (timestep, variable) level
                reused, tot = report["reused_pairs"], report["total_pairs"]
                deltamsg = (f"fetched {report['fetched_vars']} — "
                            f"{report['fetched_pairs']} (timestep,variable) pair(s) "
                            f"crossed the wire"
                            if report["fetched_pairs"] else
                            "nothing crossed the wire (full catalog hit)")
                steps.append(f"remote compute on {report['host']}{job}: reused "
                             f"{reused}/{tot} extent(s) from the catalog; {deltamsg}")
            else:
                steps.append(f"remote compute: {n}/{n} timestep(s) reduced next to "
                             f"the data on {report['host']}{job}, dir pulled once")
            steps.append(f"local:   -> save timeseries -> {out}")
            return {"kind": "save", "uri": src.uri, "steps": steps, "output": out,
                    "materialized": True, "timesteps": report.get("timesteps", []),
                    "sites": {"remote": fetched, "cached": cached, "fetch": 0}}
        except RemoteUnavailable as e:
            timing.note(remote_unavailable=str(e))
            if mode == "force":
                raise RuntimeError(f"VISLANG_REMOTE=force but remote folder reduce "
                                   f"is unavailable: {e}")
            steps.append(f"remote folder reduce unavailable ({e}) — falling back "
                         f"to whole-folder fetch")
    elif not has_narrowing:
        steps.append("no narrowing forms — the whole folder crosses the wire "
                     "either way (cost gate: fetch)")

    # Fallback: fetch the whole folder, then plan it locally as a timeseries
    # (the local _plan_folder applies the budget gate on the materialize/save).
    timing.note(route="whole_folder_fetch")
    with timing.phase("fetch_whole_folder"):
        local = _fetch_remote_folder(src.uri)
    steps.append(f"fetch folder {src.uri} -> {local}")
    local_src = SourceNode(uri=local, positions=src.positions)
    res = _plan_folder(local_src, middle, ts_nodes, sink, terminal, dry_run=False,
                       confirm=confirm)
    res["uri"] = src.uri                                 # report the original remote uri
    res["steps"] = steps + res["steps"]
    res["sites"] = {"remote": 0, "fetch": len(res.get("timesteps", []))}
    return res


def _save(loaded, path):
    """Write the loaded arrays, preserving the source's format where possible
    (HDF5, npz, GenericIO today; npz otherwise). The output format follows the
    path's extension when it is a known one (.npz/.h5/.hdf5/.gio), else the
    source's original format."""
    from my_save import save_loaded
    return save_loaded(loaded, path)


def format_result(result):
    head = f"[{result['kind']}] {result['uri']}"
    # Indent every line of each step (some steps — e.g. the cost estimate — are
    # multi-line blocks, so a single leading indent would leave their tails flush).
    body = "\n".join("\n".join(f"    {ln}" for ln in str(s).split("\n"))
                     for s in result["steps"])
    tail = f"\n  output: {result['output']}" if result["output"] else ""
    if result.get("needs_allocation"):
        mode = "  (HELD — no Slurm allocation, nothing materialized)"
    elif result.get("needs_confirm"):
        mode = "  (HELD — over budget, nothing materialized)"
    elif not result["materialized"]:
        mode = "  (dry run — nothing materialized)"
    else:
        mode = ""
    return f"{head}{mode}\n{body}{tail}"
