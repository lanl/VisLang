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

import vislang.runtime.timing as timing        # per-phase seconds/bytes -> timings.jsonl
import vislang.runtime.provenance as provenance  # the derivation record -> the artifact
from vislang.dsl.nodes import SourceNode, RegionNode, upstream_of, select_timesteps
from vislang.interpreter.approval import Approval
from vislang.formats.inspect import inspect_file
from vislang.interpreter.load import materialize, materialize_boxes
from vislang.interpreter.compress import compress as _compress
from vislang.output.render import render as _render
from vislang.formats.adapters import adapter_capabilities
from vislang.interpreter.narrowing import (Narrowing, AxisRange, Predicate, BBox,
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
        elif k == "region" and (n.per_step or n.tagged):
            from vislang.interpreter.track import describe
            parts.append(describe(n))
        elif k == "region":
            parts.append("region{" + ",".join(f"{a}:({lo},{hi})"
                                               for a, lo, hi in n.ranges)
                         + (f",{n.coords}" if n.coords else "") + "}")
        elif k == "subsample":
            parts.append(f"subsample({n.uniform})" if n.uniform is not None
                         else f"subsample({dict(n.per_axis)})")
        elif k == "threshold":
            parts.append(f"threshold({n.var}{n.op}{n.value})")
        elif k == "timesteps":
            parts.append(f"timesteps({list(n.labels)})" if n.labels is not None
                         else f"timesteps({n.start},{n.stop})")
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


def _held_session(hold, kind, uri, steps):
    """The HELD result for a remote with no usable session. One builder so the
    reduce route, the folder route and the fetch route all report identically."""
    steps += hold.steps
    steps.append(f"HELD: {hold.reason} — nothing was read.")
    steps.append(f"  {hold.connect_cmd}")
    return {"kind": kind, "uri": uri, "steps": steps, "output": None,
            "materialized": False, "needs_session": True,
            "connect_cmd": hold.connect_cmd, "session_reason": hold.reason}


def _session_gate(uri, kind, steps):
    """HELD result when `uri`'s host can't be driven non-interactively, else None.

    Checked once per run, BEFORE the route is chosen. Every route below needs the
    same non-interactive connection — the remote reduce to ship a plan, the
    whole-file fetch to run rsync in BatchMode — so a missing session is one
    clear, fixable hold rather than a different failure depending on whether the
    spec happened to contain a narrowing form.

    A probe that throws is not a hold: the individual route reports it with more
    context than this gate has."""
    from vislang.remote.reduce import session_check
    hold = session_check(uri)
    return None if hold is None else _held_session(hold, kind, uri, steps)


def _remote_fetch_gate(uri, sink, terminal, steps, approved, fallback=False):
    """Estimate + gate a whole-file remote FETCH before pulling. The whole file
    crosses the wire, so wire bytes == file size (known from one stat). Returns a
    HELD result dict when over budget and not `approved`, else None (proceed).
    `fallback` says the run reached this fetch because its reduce could not run
    — that hold asks for its own approval (allow_fetch). Any probe failure is
    non-fatal — the fetch proceeds and its own path reports."""
    try:
        from vislang.remote.download import (establish_connection, remote_stat,
                                  measure_bandwidth, _parse_remote)
        from vislang.formats.dataset_info import DatasetInfo
        from vislang.interpreter.estimate import estimate_plan_cost
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
        if _gate(result, est, approved, steps, fallback=fallback):
            return result
    except Exception as e:
        steps.append(f"(fetch estimate skipped: {type(e).__name__}: {e})")
    return None


def _held_allocation(hold, kind, uri, steps):
    """The HELD result for srun auto mode with no Slurm allocation. Proposes the
    salloc — never runs it — and names the other way out."""
    steps += hold.steps
    steps.append("HELD: no Slurm allocation — nothing shipped. Ask the user to "
                 "approve creating one (it runs only on their approval):")
    steps.append(f"  {hold.salloc_cmd}")
    steps.append("Once it is RUNNING, re-run the spec; or re-run with "
                 "without_allocation=True to fall back to fetching whole files "
                 "(priced and gated on its own).")
    return {"kind": kind, "uri": uri, "steps": steps, "output": None,
            "materialized": False, "needs_allocation": True,
            "salloc_cmd": hold.salloc_cmd}


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
    from vislang.remote.download import _ssh_query
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
    from vislang.remote.download import establish_connection, transfer, _parse_remote
    from vislang.runtime.paths import downloads_dir
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
def _label_span(labels):
    """A folder's timestep labels in a few words, for an error that none matched."""
    labels = sorted(labels)
    if not labels:
        return "no timesteps"
    return (f"{len(labels)} timesteps, #{labels[0]}…#{labels[-1]}" if len(labels) > 1
            else f"one timestep, #{labels[0]}")


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

def _region_text(node):
    return "{" + ", ".join(f"{a}: ({lo}, {hi})" for a, lo, hi in node.ranges) + "}" \
        if node.ranges else "(placed by centre)"


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
    (see reduce.py); otherwise we fall back to the whole-file fetch and
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

    # A region that moves per timestep: read any track file NOW, on the host and
    # before dispatch, so every route (local, remote batch, catalog delta) sees
    # the centres inline and the plan ships data rather than a local path.
    from vislang.interpreter.track import (resolve_tracks, per_step_regions,
                                           tagged_region, tag_names)
    middle = resolve_tracks(middle)
    moving = per_step_regions(middle)
    tagged = tagged_region(middle)              # raises on two
    if tagged is not None and sink is not None and sink.kind == "render":
        raise NotImplementedError(
            f"render shows one dataset, and this region names {len(tagged.tags)} "
            f"boxes ({', '.join(tag_names(tagged))}). save() writes each to its own "
            f"output (.vti/.vtp adds a ParaView index that opens them together); "
            f"to look at one box here, render a plain region for it.")

    # One measured record per sink (vislang_timing): the forms as written, the
    # site chosen, and every phase timed inside it. No-op when timing is off.
    # The provenance scope is opened from `terminal`, not from `src`: on the
    # whole-file and whole-folder fallbacks the planner is re-entered with the
    # LOCAL pulled copy as its source, so only the terminal still carries the
    # URI the spec actually named.
    with timing.pipeline(kind=(sink.kind if sink is not None else terminal.kind),
                         uri=src.uri, forms=[n.kind for n in middle],
                         dry_run=bool(dry_run or sink is None),
                         ts_range=([max(n.start for n in ts_nodes),
                                    min(n.stop for n in ts_nodes)]
                                   if ts_nodes else None)), \
            provenance.pipeline(terminal):
        provenance.note_region_track([
            {"path": os.path.abspath(n.track), "sha256": n.track_sha}
            for n in moving + ([tagged] if tagged is not None else []) if n.track])
        provenance.note_resolved_regions([n for n in middle if n.kind == "region"])
        # A FOLDER source is a timeseries: map the single-file chain over its
        # timestep files (named `…#N`). This holds for a remote folder as much as a
        # local one — we detect it locally with an os.path check, or remotely with a
        # metadata-only `stat` over ssh (planner._plan_remote_folder next to the
        # data, mirroring _plan_folder).
        remote_src = bool(_REMOTE.match(src.uri))
        if remote_src:
            from vislang.formats.inspect import remote_is_dir
            # The remote folder probe is skipped on a dry run to keep the dry run
            # zero-network (as _plan_remote does): there timesteps() is the folder
            # signal — it applies only to a folder — else we preview the single-file
            # plan. The actual run probes and dispatches for real.
            is_folder = (bool(ts_nodes or any(n.moves for n in moving)
                              or (tagged is not None and tagged.tag_series))
                         if (dry_run or sink is None) else remote_is_dir(src.uri))
        else:
            is_folder = os.path.isdir(src.uri)
        timing.note(site="remote" if remote_src else "local", folder=is_folder)
        if tagged is not None:
            _check_tag_shape(tagged, is_folder, src.uri)

        approval = Approval.of(confirm)
        if is_folder:
            if remote_src:
                result = _plan_remote_folder(src, middle, ts_nodes, sink, terminal,
                                             dry_run, approval)
            else:
                result = _plan_folder(src, middle, ts_nodes, sink, terminal, dry_run,
                                      approval.budget)
        elif ts_nodes:
            raise ValueError("timesteps() applies only to a folder (timeseries) "
                             f"source; {src.uri!r} is a single file.")
        elif any(n.moves for n in moving):
            raise ValueError("region(center=[(step, …), …]) gives a box per "
                             "timestep, so it needs a folder (timeseries) source; "
                             f"{src.uri!r} is a single file. Give one centre "
                             "here: center=(x, y, z).")
        elif remote_src:
            result = _plan_remote(src, middle, sink, terminal, dry_run, approval)
        else:
            result = _plan_local(src, middle, sink, terminal, dry_run, src.uri, [],
                                 approval.budget)
        timing.note(materialized=bool(result.get("materialized")),
                    held=("allocation" if result.get("needs_allocation")
                          else "budget" if result.get("needs_confirm") else None))

    if _tracing() and isinstance(result, dict) and "steps" in result:
        result["steps"].insert(0, _describe_ast(chain))    # spec as authored, on top
    return result


def _check_tag_shape(tagged, is_folder, uri):
    """Named boxes placed per timestep need a folder; on one file each box is
    one centre. (A box that stays put works on either.)"""
    if not is_folder and tagged.tag_series:
        raise ValueError(
            f"region(center={{tag: [(step, …), …]}}) places a box per timestep, "
            f"but {uri!r} is a single file; on one file give each tag one "
            f"centre: center={{tag: (x, y, z)}}")


def _plan_remote(src, middle, sink, terminal, dry_run, approval=None):
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

    held = _session_gate(src.uri, sink.kind, steps)
    if held is not None:
        timing.note(route="held_session")
        return held

    approval = Approval.of(approval)
    fell_back = False
    if mode != "off" and has_narrowing:
        from vislang.remote.reduce import (remote_reduce, remote_reduce_tags,
                                   RemoteUnavailable, BudgetHold,
                                   AllocationHold, SessionHold)
        from vislang.interpreter.track import tagged_region
        tagged = tagged_region(middle)
        try:
            with timing.phase("remote_reduce"):
                if tagged is None:
                    loaded, rsteps, estimate = remote_reduce(src, middle, approval)
                else:
                    cut, rsteps, estimate = remote_reduce_tags(src, middle, approval)
            # A full catalog hit ships nothing and prices nothing (no
            # estimate): keep the route remote_reduce recorded for it.
            timing.note(route="remote_reduce" if estimate is not None
                        else "catalog_full_hit")
            timing.note_estimate(estimate)
            steps += rsteps
            pending_compress = [n for n in middle if n.kind == "compress"]
            for c in pending_compress:
                steps.append(f"compress {list(c.variables)} "
                             f"(error_bound={c.error_bound}, local)")
            result = {"kind": sink.kind, "uri": src.uri, "steps": steps,
                      "output": None, "materialized": True, "estimate": estimate}
            steps.append(f"-> {sink.kind} (local)")
            if tagged is not None:
                return _finish_tags(cut, pending_compress, sink, result)
            return _finish(loaded, pending_compress, sink, result)
        except SessionHold as h:
            # Normally caught by _session_gate above; kept because remote_reduce
            # re-checks after its own probe, and because a session can expire
            # between the gate and the ship. Never falls through to the fetch —
            # that path needs the very session this host does not have.
            timing.note(route="held_session")
            return _held_session(h, sink.kind, src.uri, steps)
        except AllocationHold as h:
            timing.note(route="held_allocation")
            return _held_allocation(h, sink.kind, src.uri, steps)
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
            fell_back = True
    elif not has_narrowing:
        steps.append("no narrowing forms — the whole file crosses the wire "
                     "either way (cost gate: fetch)")

    # Whole-file FETCH path: the entire file crosses the wire. Estimate + gate on
    # the measured transfer cost before pulling. When the run FELL BACK here, the
    # approval given for the reduce does not carry over (approval.py).
    timing.note(route="whole_file_fetch")
    approved = approval.fetch if fell_back else approval.budget
    held = _remote_fetch_gate(src.uri, sink, terminal, steps, approved,
                              fallback=fell_back)
    if held is not None:
        timing.note(route="held_budget")
        return held
    with timing.phase("fetch_whole_file"):
        local = _fetch_remote(src.uri)           # establish_connection + transfer
    steps.append(f"fetch {src.uri} -> {local}")
    # The fetch was approved (or under budget); the local read of it is gated
    # on the same terms.
    return _plan_local(src, middle, sink, terminal, dry_run, local, steps,
                       approved or approval.budget)


def _probe_bandwidth(uri, steps):
    """Measured link speed for a remote estimate's time band, or None. Failure is
    never fatal: a byte estimate without a time band still prices the request."""
    try:
        from vislang.remote.download import establish_connection, measure_bandwidth
        return measure_bandwidth(establish_connection(_normalize_remote(uri)))
    except Exception as e:
        steps.append(f"(bandwidth probe skipped: {type(e).__name__}: {e})")
        return None


def _estimate_remote(src, middle, sink, steps, mode, has_narrowing, n_timesteps=1,
                     schema_uri=None, plan=None):
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
    of a folder); `n_timesteps` scales the estimate over the series. Named boxes
    are priced per tag; `plan` ({label: [tags]}) says which run where.
    """
    from vislang.formats.inspect import inspect_source, SchemaUnavailable
    from vislang.interpreter.estimate import (estimate_plan_cost, estimate_boxes_cost,
                                              format_plan_estimate)
    from vislang.interpreter.track import tagged_region, tag_names, for_tag

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

    tagged = tagged_region(middle)
    if tagged is None:
        with timing.phase("lower"):
            narrowing, _pending = _lower(info, middle, steps)   # raises on a bad request
        estimate = estimate_plan_cost(info=info, narrowing=narrowing, site="remote",
                                      n_timesteps=n_timesteps, sink_kind=sink.kind,
                                      wire_vars=narrowing.project,
                                      net_bw_bps=_probe_bandwidth(src.uri, steps))
    else:
        plan = plan or {None: tag_names(tagged)}
        counts, firsts = _tag_summary(plan)
        middles = {t: for_tag(middle, t) for t in counts}
        with timing.phase("lower"):
            _check_boxes(info, middles, _first_boxes(firsts))
            boxes = [(_lower(info, middles[t], steps if i == 0 else [],
                             label=firsts[t])[0], counts[t])
                     for i, t in enumerate(counts)]
        narrowing = boxes[0][0]
        steps += _tag_box_lines(info, tagged, plan)
        estimate = estimate_boxes_cost(info=info, boxes=boxes, site="remote",
                                       n_timesteps=n_timesteps, sink_kind=sink.kind,
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


def _gate(result, estimate, approved, steps, fallback=False):
    """Attach a CostEstimate to the result and decide whether to HOLD.

    Always records the estimate (so a run report shows the cost). When the plan
    is over budget and not `approved`, flags needs_confirm and returns True —
    the caller must return WITHOUT materializing. A `fallback` hold (a
    whole-file fetch the run fell back to) also flags needs_fetch_approval: it
    is approved with allow_fetch=True, never by the confirm given for the plan
    it replaced.
    """
    from vislang.interpreter.estimate import format_plan_estimate
    result["estimate"] = estimate
    timing.note_estimate(estimate)         # the predicted half of est-vs-actual
    steps.append(format_plan_estimate(estimate))
    if estimate.over_budget and not approved:
        result["needs_confirm"] = True
        if fallback:
            result["needs_fetch_approval"] = True
            steps.append("HELD: the reduce could not run, and the whole-file fetch "
                         "it would fall back to is over budget — nothing moved. "
                         "Re-run with allow_fetch=True to approve that fetch, or "
                         "fix what stopped the reduce.")
        else:
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


def _finish_tags(cut, pending_compress, sink, result):
    """The local suffix for named boxes on one file: compress each box, then
    save each to its own output under the sink path."""
    from vislang.output.save import save_tagged
    done = []
    for tag, loaded in cut:
        for c in pending_compress:
            with timing.phase("compress", vars=list(c.variables),
                              error_bound=c.error_bound):
                loaded = _compress(loaded, list(c.variables), c.error_bound, c.mode)
        done.append((tag, loaded))
    with timing.phase("sink_save", bytes=sum(_loaded_bytes(l) or 0 for _, l in done)):
        result["output"] = save_tagged(done, sink.path, done[0][1].filetype)
    result["tags"] = [t for t, _ in done]
    return result


def _boxes_at(info, middle, label, steps, tag=None):
    """Swap each per-timestep region in `middle` for timestep `label`'s concrete
    box, so the rest of lowering sees an ordinary fixed region. `label=None`
    (pricing or checking a series against one schema) uses each region's first
    centre — and is the one centre a named box on a single file has. `tag` names
    the box in a clip note. Returns (middle, boxes) with boxes [(ranges,
    clipped_axes)]."""
    from vislang.interpreter.coords import resolve_regions
    from vislang.interpreter.track import box_for_step
    # Every region in this file's own coordinates first: a grid's world box
    # becomes the cells it selects, so a moving box below is cut in cells.
    middle = resolve_regions(info, middle, steps, label=label)
    grid = (info.dimensions or {}).get("grid") if _modality(info) == "grid" else None
    out, boxes = [], []
    for n in middle:
        if isinstance(n, RegionNode) and n.per_step and grid is not None \
                and n.coords != "index":
            pass                            # placed where the file is read
        elif isinstance(n, RegionNode) and n.per_step:
            lab = label if label is not None else n.centers[0][0]
            n, clipped = box_for_step(n, lab, grid)
            boxes.append((n.ranges, clipped))
            if clipped:
                who = (f"region at #{lab}" if tag is None else
                       f"box {tag!r}" + (f" at #{lab}" if lab is not None else ""))
                steps.append(f"{who}: box clipped to the grid edge on "
                             f"{', '.join(clipped)}")
        out.append(n)
    return out, boxes


def _materialize_units(info, middles, label=None, notes=None, tags=None):
    """Cut several boxes from ONE file: each middle (one per box — a named box's
    own view of the chain) is lowered against `info`, one inspect for all, and
    the boxes are read together (load.materialize_boxes: one read where the
    format would read the whole file per box anyway). `tags` names each box in
    clip notes. Returns [(loaded, boxes)] in the order given, `boxes` being each
    one's resolved per-timestep box for provenance."""
    notes = [] if notes is None else notes
    lowered = []
    for i, mid in enumerate(middles):
        mid, boxes = _boxes_at(info, mid, label, notes,
                               tag=tags[i] if tags else None)
        narrowing, pending = _lower(info, mid, [])
        lowered.append((narrowing, pending, boxes))
    # A subsample pushed into the read samples the FILE, before any box is cut
    # (that is the order the spec wrote), so every box gets the same draw —
    # lowering drew one per box. The chains differ only in the region, so the
    # pushdowns agree in kind.
    first = lowered[0][0]
    if first.particle_index is not None:
        for narrowing, _, _ in lowered[1:]:
            if narrowing.particle_index is not None:
                narrowing.particle_index = first.particle_index
    if len(lowered) == 1:
        loadeds = [materialize(info, lowered[0][0])]
    else:
        loadeds = materialize_boxes(info, [n for n, _, _ in lowered])
    out = []
    for loaded, (_, pending, boxes) in zip(loadeds, lowered):
        for c in pending:
            loaded = _compress(loaded, list(c.variables), c.error_bound, c.mode)
        out.append((loaded, boxes))
    return out


def _check_boxes(info, middles, plan, own_info=None):
    """Resolve every box against a schema before any read, so a box that misses
    the grid fails now — not after the timesteps before it were read. `info`
    stands for the series; a box that misses its grid is re-checked against its
    own timestep's file when `own_info(label)` can inspect it, so a series whose
    grid changes is not refused for a box its own file holds. `middles` is
    {tag: middle}; `plan` is {label: [tags]}."""
    from vislang.interpreter.coords import resolve_regions
    from vislang.interpreter.track import box_for_step, per_step_regions

    def grid_of(i):
        return (i.dimensions or {}).get("grid") if _modality(i) == "grid" else None

    def check(i, n, lab):
        # The box in the file's own coordinates, then cut: a world box this
        # side cannot place is checked where the file is read instead.
        n = resolve_regions(i, [n], read_points=False, label=lab)[0]
        if grid_of(i) is not None and n.coords != "index":
            return
        box_for_step(n, lab, grid_of(i))
    for lab, tags in plan.items():
        for t in tags:
            for n in per_step_regions(middles[t]):
                try:
                    try:
                        check(info, n, lab)
                    except ValueError:
                        if own_info is None or lab is None:
                            raise
                        check(own_info(lab), n, lab)
                except ValueError as e:
                    if t is None:
                        raise
                    raise ValueError(f"box {t!r}: {e}") from None


def _first_boxes(firsts):
    """{label: [tags]} holding only each tag's first box: what one schema can
    vouch for when the other timesteps' files are not at hand (an estimate, a
    remote folder) — the rest are cut against their own files."""
    plan = {}
    for t, lab in firsts.items():
        plan.setdefault(lab, []).append(t)
    return plan


def _tag_summary(plan):
    """({tag: number of boxes}, {tag: its first timestep}) from {label: [tags]}."""
    counts, firsts = {}, {}
    for lab, tags in plan.items():
        for t in tags:
            counts[t] = counts.get(t, 0) + 1
            firsts.setdefault(t, lab)
    return counts, firsts


def _lower(info, middle, steps, label=None):
    """Classify + lower the middle forms against ONE file's schema into a single
    fused Narrowing (+ any trailing compress nodes). No read happens here, so the
    folder/timeseries loop can call it once per timestep. Appends human-readable
    steps. `middle` must not contain timesteps() nodes — those are a time-axis
    selector consumed before the loop, not a per-file narrowing. `label` is the
    timestep being lowered; it picks the box of a per-timestep region."""
    middle, _ = _boxes_at(info, middle, label, steps)
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
            if modality == "grid" and node.coords != "index":
                # A world box this side could not place (a curvilinear grid's
                # points are bulk data; an old schema did not say). The side
                # that reads the file resolves it; here it neither crops nor
                # fails, so the check and the price cover the whole grid.
                steps.append(f"region {_region_text(node)} — world coordinates, "
                             f"placed where the file is read; checked and priced "
                             f"as the whole grid (an upper bound)")
            elif modality == "grid":
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
    from vislang.interpreter.track import tagged_region
    if tagged_region(middle) is not None:
        return _plan_local_tagged(src, middle, sink, terminal, dry_run, source_uri,
                                  steps, confirm)
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
    # Local sources have no identity anywhere else in the system — the catalog's
    # is computed only on the remote reduce paths — so this is where a locally
    # read file gets its fingerprint (and, if it is itself a Sieve output, the
    # link to that output's record).
    if provenance.active():
        provenance.note_source(read_from=source_uri, info=info, site="local",
                               fingerprint=provenance.file_fingerprint(source_uri))
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
    from vislang.interpreter.estimate import estimate_plan_cost, format_plan_estimate
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


def _plan_local_tagged(src, middle, sink, terminal, dry_run, source_uri, steps,
                       confirm=False):
    """Named boxes on ONE file (region(center={tag: (x, y[, z])})): one inspect,
    each tag lowered as its own box, the boxes read together (one read where
    the format slices in memory), and each tag saved to its own output."""
    from vislang.interpreter.estimate import estimate_boxes_cost, format_plan_estimate
    from vislang.interpreter.load import shares_read
    from vislang.interpreter.track import tagged_region, tag_names, for_tag
    tagged = tagged_region(middle)
    tags = tag_names(tagged)
    middles = {t: for_tag(middle, t) for t in tags}
    plan = {None: tags}
    with timing.phase("inspect", uri=source_uri):
        info = inspect_file(source_uri, positions=src.positions)
    try:
        timing.note(source_bytes=os.path.getsize(source_uri))
    except OSError:
        pass
    if provenance.active():
        provenance.note_source(read_from=source_uri, info=info, site="local",
                               fingerprint=provenance.file_fingerprint(source_uri))
    steps.append(_describe_schema(info, source_uri))
    with timing.phase("lower"):
        steps.append(f"named boxes: {', '.join(tags)} — each lowered as its own box")
        _check_boxes(info, middles, plan)
        boxes = [(_lower(info, middles[t], steps if i == 0 else [])[0], 1)
                 for i, t in enumerate(tags)]
    steps += _tag_box_lines(info, tagged, plan)
    if sink is not None:
        steps.append(f"-> save one output per tag -> {_output_root(sink.path)}/<tag>")
    else:
        steps.append(f"(no sink — chain ends at {terminal.kind}; nothing to materialize)")

    result = {"kind": (sink.kind if sink is not None else terminal.kind),
              "uri": src.uri, "steps": steps, "output": None, "materialized": False,
              "narrowing": boxes[0][0]}
    estimate = estimate_boxes_cost(info=info, boxes=boxes, site='local',
                                   sink_kind=sink.kind if sink is not None else None,
                                   shared_read=len(tags) > 1 and shares_read(info))
    timing.note(tags=len(tags), boxes=len(tags))
    timing.note_estimate(estimate)
    if dry_run or sink is None:
        result["estimate"] = estimate
        steps.append(format_plan_estimate(estimate))
        return result
    if _gate(result, estimate, confirm, steps):
        return result

    with timing.phase("materialize") as _m:
        notes = []
        cut = _materialize_units(info, [middles[t] for t in tags], None, notes,
                                 tags=tags)
        _m["bytes"] = sum(_loaded_bytes(l) or 0 for l, _ in cut)
    steps += notes
    result["materialized"] = True
    for t, (_, boxes_t) in zip(tags, cut):
        provenance.note_region_box(None, boxes_t, tag=t)
    return _finish_tags([(t, l) for t, (l, _) in zip(tags, cut)], [], sink, result)


def _output_root(path):
    """The directory a many-file sink writes into: the path minus a known
    output extension (`roi.vti` -> `roi`), as save does."""
    from vislang.output.save import _EXT_FORMAT
    stem, ext = os.path.splitext(path)
    return stem if ext.lower() in _EXT_FORMAT else path


def _box_space(info, grid):
    """How a dry run's box listing is to be read."""
    from vislang.formats.dataset_info import in_cells
    if grid is None:
        return "world coordinates"
    geometry = getattr(info, "geometry", None)
    if geometry and not in_cells(geometry):
        return "cells the world-coordinate boxes select, clipped to the grid"
    return "index space, clipped to the grid"


def _ranges_text(ranges):
    return ", ".join(f"{a}:({_fmt_bound(lo)},{_fmt_bound(hi)})" for a, lo, hi in ranges)


def _box_lines(info, middle, labels, limit=12):
    """Dry-run text: the box each timestep would get, from one schema (a series
    shares its grid shape). Shows the first `limit` timesteps."""
    from vislang.interpreter.coords import resolve_regions
    from vislang.interpreter.track import box_for_step, per_step_regions
    grid = (info.dimensions or {}).get("grid") if _modality(info) == "grid" else None
    lines = []
    for n in per_step_regions(middle):
        n = resolve_regions(info, [n])[0]
        if grid is not None and n.coords != "index":
            lines.append("per-timestep region boxes: world coordinates, placed on "
                         "each file's points where it is read")
            continue
        lines.append("per-timestep region boxes ("
                     + _box_space(info, grid) + "):")
        for lab in labels[:limit]:
            box, clipped = box_for_step(n, lab, grid)
            lines.append(f"  #{lab}: {_ranges_text(box.ranges)}"
                         + (f"  [clipped {','.join(clipped)}]" if clipped else ""))
        if len(labels) > limit:
            lines.append(f"  … {len(labels) - limit} more")
    return lines


def _tag_box_lines(info, tagged, plan, limit=8):
    """Dry-run text: every named box — per timestep over a series (the first
    `limit` of each tag), or its one box on a file."""
    from vislang.interpreter.coords import resolve_regions
    from vislang.interpreter.track import box_for_step, tag_names, tag_view
    grid = (info.dimensions or {}).get("grid") if _modality(info) == "grid" else None
    tagged = resolve_regions(info, [tagged])[0]
    if grid is not None and tagged.coords != "index":
        return ["named boxes: world coordinates, placed on each file's points "
                "where it is read"]
    lines = ["named boxes (" + _box_space(info, grid) + "):"]
    for t in tag_names(tagged):
        labels = [lab for lab, tags in plan.items() if t in tags]
        if not labels:
            continue
        view = tag_view(tagged, t)
        if labels == [None]:
            box, clipped = box_for_step(view, None, grid)
            lines.append(f"  {t}: {_ranges_text(box.ranges)}"
                         + (f"  [clipped {','.join(clipped)}]" if clipped else ""))
            continue
        lines.append(f"  {t} ({len(labels)} timestep(s)):")
        for lab in labels[:limit]:
            box, clipped = box_for_step(view, lab, grid)
            lines.append(f"    #{lab}: {_ranges_text(box.ranges)}"
                         + (f"  [clipped {','.join(clipped)}]" if clipped else ""))
        if len(labels) > limit:
            lines.append(f"    … {len(labels) - limit} more")
    return lines


def _fmt_bound(v):
    return f"{v:g}" if isinstance(v, float) else str(v)


def _plan_folder(src, middle, ts_nodes, sink, terminal, dry_run, confirm=False):
    """A FOLDER source is a TIMESERIES: map the single-file chain over the
    timestep files (named `…#N`) and combine. timesteps() nodes (already split
    out of `middle`) pick the inclusive #N range; the rest of the chain lowers
    per file exactly as for one file. render over a series isn't supported — pick
    one timestep, or save the range (one file per timestep, original format).

    Named boxes (region(center={tag: …})) run per (timestep, tag): each file
    is inspected once and every tag whose box exists there is cut from it (one
    read where the format slices in memory); each tag is then saved as its own
    series under the sink path. Timesteps where no tag has a box are not read."""
    from vislang.formats.inspect import timestep_files
    from vislang.interpreter.estimate import (estimate_plan_cost, estimate_boxes_cost,
                                              format_plan_estimate)
    from vislang.interpreter.load import shares_read
    from vislang.interpreter.track import (check_coverage, per_step_regions,
                                           tagged_region, tag_plan, tag_names, for_tag)
    files = timestep_files(src.uri)                     # [(label, path)]; raises if none
    available = [lab for lab, _ in files]
    chosen = " and ".join(n.text() for n in ts_nodes) if ts_nodes else None
    if ts_nodes:
        files = select_timesteps(files, ts_nodes)

    steps = [f"folder timeseries {src.uri}: {len(files)} timestep(s)"
             + (f" in {chosen}" if chosen else "")]
    if not files:
        raise ValueError(f"no timesteps match {chosen} in {src.uri} (it has "
                         f"{_label_span(available)})")
    moving = per_step_regions(middle)
    tagged = tagged_region(middle)
    if tagged is not None:
        # Which tag's box runs at which timestep — from the listing alone,
        # before any inspect or read. A tag may skip timesteps.
        plan, skipped = tag_plan(tagged, [lab for lab, _ in files], available)
        files = [(lab, p) for lab, p in files if lab in plan]
        if moving:                  # an unnamed moving box too: every file read
            check_coverage(middle, list(plan), available)
        tags = [t for t in tag_names(tagged) if t not in skipped]
        middles = {t: for_tag(middle, t) for t in tags}
        counts, firsts = _tag_summary(plan)
        steps.append("named boxes: " + ", ".join(f"{t} ×{counts[t]}" for t in tags)
                     + f" — {sum(counts.values())} box(es) from {len(files)} "
                       f"timestep file(s)")
        steps += [f"tag {t!r}: no box in the selected timesteps — nothing written "
                  f"for it" for t in skipped]
    else:
        # A per-timestep region must cover exactly the selected files — checked
        # from the listing alone, before any inspect or read.
        check_coverage(middle, [lab for lab, _ in files], available)
        plan = {lab: [None] for lab, _ in files}
        tags, middles = [None], {None: middle}
        counts, firsts = {None: len(files)}, {None: files[0][0]}

    if sink is not None and sink.kind == "render":
        raise NotImplementedError(
            "render over a timeseries folder isn't supported — select one timestep "
            "with timesteps(node, N, N), or use save() to write the range.")

    # Plan + estimate off the first timestep (metadata only); a series shares
    # its schema, so cost scales with the steps (or boxes) selected.
    with timing.phase("inspect", uri=files[0][1], n_timesteps=len(files)):
        info0 = inspect_file(files[0][1], positions=src.positions)
    provenance.note_source(info=info0, site="local")
    with timing.phase("lower"):
        if moving or tagged is not None:                # every box, before any read
            paths = dict(files)
            _check_boxes(info0, middles, plan, own_info=lambda lab: inspect_file(
                paths[lab], positions=src.positions))
        lowered0 = {t: _lower(info0, middles[t], [], label=firsts[t])[0] for t in tags}
    sink_kind = sink.kind if sink is not None else None
    if tagged is None:
        estimate = estimate_plan_cost(info=info0, narrowing=lowered0[None], site='local',
                                      n_timesteps=len(files), sink_kind=sink_kind)
    else:
        estimate = estimate_boxes_cost(
            info=info0, boxes=[(lowered0[t], counts[t]) for t in tags], site='local',
            n_timesteps=len(files), sink_kind=sink_kind,
            shared_read=shares_read(info0) and any(len(v) > 1 for v in plan.values()))
        timing.note(tags=len(tags), boxes=sum(counts.values()))
    timing.note_estimate(estimate)

    if dry_run or sink is None:
        steps.append("per timestep: "
                     + (" -> ".join(n.kind for n in middle) if middle else "(no narrowing)"))
        _trace(steps, _describe_schema(info0, files[0][1]))
        _lower(info0, middles[tags[0]], steps, label=firsts[tags[0]])
        if tagged is not None:
            steps += _tag_box_lines(info0, tagged, plan)
        elif moving:
            steps += _box_lines(info0, middle, [lab for lab, _ in files])
        if sink is None:
            steps.append("(no sink — nothing materialized)")
        elif tagged is not None:
            steps.append(f"-> save one series per tag -> {_output_root(sink.path)}/<tag>/")
        else:
            steps.append(f"-> save timeseries -> {sink.path}")
        steps.append(format_plan_estimate(estimate))
        return {"kind": (sink.kind if sink is not None else terminal.kind),
                "uri": src.uri, "steps": steps, "output": None, "materialized": False,
                "estimate": estimate}

    # Gate on the whole-series cost BEFORE materializing any timestep.
    held = {"kind": "save", "uri": src.uri, "steps": steps, "output": None,
            "materialized": False}
    if _gate(held, estimate, confirm, steps):
        return held

    # Each step's fingerprint — 64 KiB read per file — so the folder record can
    # say exactly which inputs it was made from.
    if provenance.active():
        provenance.note_timesteps([
            {"label": lab, "uri": p, "fingerprint": provenance.file_fingerprint(p),
             "derived_from": provenance.parent_link(p)} for lab, p in files])
    per_tag = {t: [] for t in tags}
    with timing.phase("materialize_timeseries", n=len(files)) as _m:
        total = 0
        for i, (label, path) in enumerate(files):
            here = plan[label]
            try:                                      # name the failing timestep
                info = inspect_file(path, positions=src.positions)
                if i == 0:                            # echo the schema once
                    _trace(steps, _describe_schema(info, path))
                clip_notes = []
                cut = _materialize_units(info, [middles[t] for t in here], label,
                                         clip_notes, tags=here)
                steps += clip_notes
            except Exception as e:
                raise type(e)(f"timestep #{label} ({path}): {e}") from e
            for t, (loaded, boxes) in zip(here, cut):
                if boxes:
                    provenance.note_region_box(label, boxes, tag=t)
                total += _loaded_bytes(loaded) or 0
                per_tag[t].append((label, loaded))
        _m["bytes"] = total

    from vislang.output.save import save_timeseries, save_tagged_series
    if tagged is None:
        per_step = per_tag[None]
        steps.append(f"materialized {len(per_step)} timestep(s), "
                     + ("each with its own region box" if moving else "same chain per file"))
        with timing.phase("sink_save_timeseries", n=len(per_step)):
            out = save_timeseries(per_step, sink.path, per_step[0][1].filetype)
        steps.append(f"-> save timeseries -> {out}")
        return {"kind": "save", "uri": src.uri, "steps": steps, "output": out,
                "materialized": True, "timesteps": [lab for lab, _ in per_step]}

    n_boxes = sum(len(v) for v in per_tag.values())
    steps.append(f"materialized {n_boxes} named box(es) from {len(files)} timestep(s)")
    with timing.phase("sink_save_timeseries", n=n_boxes):
        out = save_tagged_series([(t, per_tag[t]) for t in tags], sink.path,
                                 per_tag[tags[0]][0][1].filetype)
    steps.append(f"-> save one series per tag -> {out}/<tag>/")
    return {"kind": "save", "uri": src.uri, "steps": steps, "output": out,
            "materialized": True, "timesteps": [lab for lab, _ in files],
            "tags": {t: [lab for lab, _ in per_tag[t]] for t in tags}}


def per_step_regions_in(middle):
    """True when a region's box depends on the timestep (one moving box, or
    named boxes placed per timestep)."""
    return any(isinstance(n, RegionNode) and (n.per_step or n.tag_series)
               for n in middle)


def _check_remote_coverage(middle, files, available, steps):
    """Coverage of a per-timestep region against a remote listing
    [(label, uri, size, mtime)] — the same rule _plan_folder applies locally.
    Returns the named-box plan {label: [tags]} when the region names its boxes,
    else None."""
    from vislang.interpreter.track import (check_coverage, tagged_region, tag_plan,
                                           per_step_regions)
    tagged = tagged_region(middle)
    if tagged is not None:
        plan, skipped = tag_plan(tagged, [f[0] for f in files], available)
        if per_step_regions(middle):
            check_coverage(middle, list(plan), available)
        counts, _ = _tag_summary(plan)
        steps.append(f"named boxes: {sum(counts.values())} box(es) over "
                     f"{len(plan)} timestep(s)"
                     + (f"; none in the selection for {', '.join(skipped)}"
                        if skipped else ""))
        return plan
    if not per_step_regions_in(middle):
        return None
    check_coverage(middle, [f[0] for f in files], available)
    steps.append(f"per-timestep region: a centre for each of the {len(files)} "
                 f"selected timestep(s)")
    return None


def _fetch_remote_series(src, middle, ts_nodes, sink, terminal, steps, approved,
                         fallback=False):
    """Fetch ONLY the selected timesteps of a remote folder — each `…#N` file the
    request reads (and that file's own `#k` partitions) — into a local cache
    folder, priced on their summed size and gated first. The local _plan_folder
    then maps the chain over them exactly as for a local folder.

    Returns (held result, None) when the fetch is over budget and not
    `approved`, else (None, local folder). `fallback` marks a fetch the run fell
    back to (its hold asks for allow_fetch, approval.py)."""
    from vislang.formats.dataset_info import DatasetInfo
    from vislang.formats.inspect import remote_timestep_files_stat
    from vislang.interpreter.estimate import estimate_plan_cost
    from vislang.remote.download import (establish_connection, transfer, _parse_remote,
                                         measure_bandwidth)
    from vislang.runtime.paths import downloads_dir
    with timing.phase("list_timesteps"):
        files = remote_timestep_files_stat(src.uri)
    if files is None:
        raise RuntimeError(f"cannot list remote folder {src.uri!r} (needs a live "
                           f"session)")
    available = [f[0] for f in files]
    if ts_nodes:
        files = select_timesteps(files, ts_nodes)
    if not files:
        raise ValueError(f"no timesteps match in {src.uri} (it has "
                         f"{_label_span(available)})")
    plan = _check_remote_coverage(middle, files, available, steps)
    if plan is not None:                                # named boxes: only their files
        files = [f for f in files if f[0] in plan]
    norm = _normalize_remote(src.uri)
    conn = establish_connection(norm)
    size_mb = sum(f[2] for f in files) / (1024 ** 2)
    steps.append(f"fetch {len(files)} timestep file(s), {size_mb:.0f} MiB "
                 f"(only the timesteps the request reads)")
    est = estimate_plan_cost(info=DatasetInfo(src.uri, "remote", []), narrowing=None,
                             site="remote", read_mb_override=size_mb,
                             net_bw_bps=measure_bandwidth(conn),
                             sink_kind=(sink.kind if sink is not None else None))
    result = {"kind": (sink.kind if sink is not None else terminal.kind),
              "uri": src.uri, "steps": steps, "output": None, "materialized": False}
    if _gate(result, est, approved, steps, fallback=fallback):
        return result, None
    _, _, remote_dir = _parse_remote(norm)
    local = os.path.join(downloads_dir(),
                         os.path.basename(remote_dir.rstrip("/")) or "download")
    with timing.phase("fetch_timesteps", n=len(files)):
        for _label, uri, _size, _mtime in files:
            _, _, rpath = _parse_remote(_normalize_remote(uri))
            for path in [rpath] + _remote_parts(conn, rpath):
                if transfer(conn, path, os.path.join(local, os.path.basename(path))) \
                        is None:
                    raise RuntimeError(f"remote transfer of {path} was cancelled")
    return None, local


def _plan_remote_folder(src, middle, ts_nodes, sink, terminal, dry_run, approval=None):
    """A REMOTE folder is a TIMESERIES. The whole timeseries runs in ONE remote
    job next to the data (remote_folder_reduce): the remote's own plan_pipeline
    dispatches the folder to _plan_folder, writes one file per timestep, and we
    pull that directory back once — O(1) in the timestep count, versus the old
    per-file loop's ~7-9 handshakes + Python start + srun step + transfer PER
    file. VISLANG_REMOTE=off / no narrowing / a RemoteUnavailable under `auto`
    fall back to fetching the selected timesteps + the local _plan_folder. Both
    routes are priced and gated before anything moves (approval.py). render over
    a series isn't supported — pick one timestep, or save the range."""
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
            steps.append("select timesteps " + " and ".join(n.text() for n in ts_nodes))
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
        from vislang.formats.inspect import remote_timestep_files_stat
        steps = [f"remote folder timeseries {src.uri}"]
        with timing.phase("list_timesteps"):
            files = remote_timestep_files_stat(src.uri)
        if files is None:
            steps.append("(could not list the remote folder — needs a live session; "
                         "nothing checked, nothing priced)")
            return {"kind": sink.kind, "uri": src.uri, "steps": steps,
                    "output": None, "materialized": False}
        available = [f[0] for f in files]
        if ts_nodes:
            files = select_timesteps(files, ts_nodes)
            steps.append("select timesteps " + " and ".join(n.text() for n in ts_nodes))
        if not files:
            raise ValueError(f"no timesteps match in {src.uri} (it has "
                             f"{_label_span(available)})")
        plan = _check_remote_coverage(middle, files, available, steps)
        if plan is not None:                            # named boxes: only their files
            files = [f for f in files if f[0] in plan]
        steps.append(f"{len(files)} timestep(s), "
                     f"{sum(f[2] for f in files) / (1024 ** 2):.0f} MiB of source")
        timing.note(selected_timesteps=len(files),
                    source_bytes=int(sum(f[2] for f in files)))
        return _estimate_remote(src, middle, sink, steps, mode, has_narrowing,
                                n_timesteps=len(files), schema_uri=files[0][1],
                                plan=plan)

    # Execute: one remote job for the whole folder (gates mirror _plan_remote).
    steps = []
    held = _session_gate(src.uri, sink.kind, steps)
    if held is not None:
        timing.note(route="held_session")
        return held
    approval = Approval.of(approval)
    fell_back = False
    if mode != "off" and has_narrowing:
        from vislang.remote.reduce import (remote_folder_reduce, RemoteUnavailable,
                                   SessionHold, BudgetHold, AllocationHold)
        try:
            with timing.phase("remote_folder_reduce"):
                out, rsteps, report = remote_folder_reduce(src, middle, ts_nodes,
                                                           sink.path, approval)
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
            n = report["n"]
            cached, fetched = report.get("cached", 0), report.get("fetched_timesteps", n)
            job = f" (srun jobid={report['jobid']})" if report.get("jobid") else ""
            # Reuse reported at the (timestep, variable) level.
            reused, tot = report["reused_pairs"], report["total_pairs"]
            n_geom = report.get("fetched_geometry") or 0
            deltamsg = (f"fetched {report['fetched_vars']} — "
                        f"{report['fetched_pairs']} (timestep,variable) pair(s) "
                        f"crossed the wire"
                        if report["fetched_pairs"] else
                        f"only the grid positions crossed the wire ({n_geom} piece(s))"
                        if n_geom else
                        "nothing crossed the wire (full catalog hit)")
            steps.append(f"remote compute on {report['host']}{job}: reused "
                         f"{reused}/{tot} extent(s) from the catalog; {deltamsg}")
            steps.append(f"local:   -> save one series per tag -> {out}/<tag>/"
                         if report.get("tags") else
                         f"local:   -> save timeseries -> {out}")
            res = {"kind": "save", "uri": src.uri, "steps": steps, "output": out,
                   "materialized": True, "timesteps": report.get("timesteps", []),
                   "sites": {"remote": fetched, "cached": cached, "fetch": 0}}
            if report.get("estimate") is not None:
                res["estimate"] = report["estimate"]
            if report.get("tags"):
                res["tags"] = report["tags"]
            return res
        except SessionHold as h:
            # As in _plan_remote: the fetch needs the same session, so there is
            # nothing to fall back TO.
            timing.note(route="held_session")
            return _held_session(h, sink.kind, src.uri, steps)
        except AllocationHold as h:
            timing.note(route="held_allocation")
            return _held_allocation(h, sink.kind, src.uri, steps)
        except BudgetHold as h:
            timing.note(route="held_budget")
            steps += h.steps
            steps.append("HELD: over budget — nothing shipped. Re-run with "
                         "confirm=True to commit, or narrow the spec.")
            return {"kind": sink.kind, "uri": src.uri, "steps": steps,
                    "output": None, "materialized": False, "needs_confirm": True,
                    "estimate": h.estimate}
        except RemoteUnavailable as e:
            timing.note(remote_unavailable=str(e))
            if mode == "force":
                raise RuntimeError(f"VISLANG_REMOTE=force but remote folder reduce "
                                   f"is unavailable: {e}")
            steps.append(f"remote folder reduce unavailable ({e}) — falling back "
                         f"to fetching the selected timesteps")
            fell_back = True
    elif not has_narrowing:
        steps.append("no narrowing forms — the selected timesteps cross the wire "
                     "whole either way (cost gate: fetch)")

    # Fallback: fetch the timesteps the request reads, priced and gated (the
    # approval for the reduce does not carry over to a fallback), then plan them
    # locally as a timeseries.
    timing.note(route="timestep_fetch")
    approved = approval.fetch if fell_back else approval.budget
    held, local = _fetch_remote_series(src, middle, ts_nodes, sink, terminal, steps,
                                       approved, fallback=fell_back)
    if held is not None:
        timing.note(route="held_budget")
        return held
    steps.append(f"fetched timesteps {src.uri} -> {local}")
    local_src = SourceNode(uri=local, positions=src.positions)
    res = _plan_folder(local_src, middle, ts_nodes, sink, terminal, dry_run=False,
                       confirm=approved or approval.budget)
    res["uri"] = src.uri                                 # report the original remote uri
    res["steps"] = steps + res["steps"]
    res["sites"] = {"remote": 0, "fetch": len(res.get("timesteps", []))}
    return res


def _save(loaded, path):
    """Write the loaded arrays, preserving the source's format where possible
    (HDF5, npz, GenericIO today; npz otherwise). The output format follows the
    path's extension when it is a known one (.npz/.h5/.hdf5/.gio), else the
    source's original format."""
    from vislang.output.save import save_loaded
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
    elif result.get("needs_fetch_approval"):
        mode = "  (HELD — fallback fetch over budget, nothing materialized)"
    elif result.get("needs_confirm"):
        mode = "  (HELD — over budget, nothing materialized)"
    elif not result["materialized"]:
        mode = "  (dry run — nothing materialized)"
    else:
        mode = ""
    return f"{head}{mode}\n{body}{tail}"
