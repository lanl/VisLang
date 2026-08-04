"""Remote reduce — run a pipeline's narrowing prefix next to the data.

The planner calls remote_reduce(src, middle) for an ssh:// / user@host: source.
Instead of downloading the whole file and narrowing locally, we:

  1. probe the host (key auth + source identity via stat + header hash),
  2. ask the local extent catalog what we already hold for this exact narrowing
     (the "+1 field" case fetches only the missing variables),
  3. serialize the missing prefix to plan.json (data, never code) and pipe it to
     `vislang_exec.py` on the remote — which runs the SAME interpreter next to
     the data and writes a small reduced .npz,
  4. pull that back, register the new extents in the catalog, and assemble a
     loaded DatasetInfo for the local suffix (compress/sink).

Execution model (v1): the reducer is invoked as a plain
`python vislang_exec.py` on the remote — no container. That assumes the VisLang
code + its Python deps are reachable on the remote, which holds when the repo
lives on a filesystem shared with the compute nodes (the common HPC case: NFS/
Lustre-mounted $HOME/$PROJECT). VISLANG_REMOTE_PYTHON / VISLANG_REMOTE_REPO name
the interpreter and repo path on the remote.

Scheduler placement (opt-in): set VISLANG_SRUN_JOBID to a *held* allocation id
(or to `auto` to discover the held allocation via squeue at reduce time) and each
reduce runs as a step INTO it via `srun --jobid=<alloc>` — a compute node,
instant, no queue (never `sbatch`, which queues a fresh job). srun runs on a
different node than the login-node pull, so the staged plan + reduced .npz go to
a shared-FS dir (VISLANG_REMOTE_TMP) instead of node-local /tmp, and the plan is
read from a file (`--plan`) since srun's stdin forwarding is unreliable. See
REMOTE_COMPUTE_PLAN.md "Execution placement".

Anything that blocks the path raises RemoteUnavailable(reason); the planner
falls back to the whole-file fetch. v1 cost gate is deliberately simple: any
narrowing present + a reachable remote -> remote wins (a threshold can't run
locally without the whole file anyway; a pure geometric cut ships fewer bytes
by construction). `measure_bandwidth` is available for a finer gate later.

Env knobs: VISLANG_REMOTE=off|auto|force, VISLANG_REMOTE_PYTHON (remote
interpreter, default "python"), VISLANG_REMOTE_REPO (remote repo dir, default =
this repo's path — correct on a shared filesystem), VISLANG_CACHE (catalog
root), VISLANG_NO_BINDING (force generic HDF5 names on both sides),
VISLANG_SRUN_JOBID (held allocation id, or "auto" to discover it → srun-step
mode), VISLANG_SRUN_NAME / VISLANG_SRUN_PARTITION (filter auto-discovery to this
job name / partition), VISLANG_SRUN_ARGS (extra srun flags, default
"--overlap -n1"), VISLANG_REMOTE_TMP (shared-FS staging dir for srun mode,
default "~/.vislang/reduce").
"""

import json
import os

import numpy as np

import vislang_timing as timing        # per-phase seconds/bytes -> timings.jsonl
from datasetInfo import DatasetInfo
from my_catalog import ExtentCatalog, make_source_id
from my_download import (establish_connection, transfer, transfer_dir,
                         _parse_remote, remote_stat, remote_header_hash, run_remote,
                         measure_bandwidth)
from ast_serialize import to_plan_json
from dsl_forms.nodes import (SourceNode, FieldsNode, RegionNode, SubsampleNode,
                             ThresholdNode, CompressNode, TimestepsNode)

_NARROWING = ("fields", "region", "subsample", "threshold")

# How much remote stderr to keep when a remote step fails. Generous on purpose:
# the executor's own traceback lands at the END of the stream, so a tight tail
# clip keeps the symptom (a missing output file) and discards the CAUSE (whatever
# the srun step raised before it). 500 chars was enough to hide a real failure
# behind "FileNotFoundError: …/vislang_reduce_*.npz".
_ERR_TAIL = 4000
META_BEGIN = "===VISLANG_META_BEGIN==="
META_END = "===VISLANG_META_END==="


class RemoteUnavailable(Exception):
    """Remote reduce can't run (no key auth / can't stat the file / ...).
    The planner catches this and falls back to the whole-file fetch."""


class BudgetHold(Exception):
    """The estimated remote transfer is over budget and the run was not
    confirmed. Carries the CostEstimate and the steps gathered so far so the
    planner can report the HELD plan without shipping the read."""

    def __init__(self, estimate, steps):
        super().__init__("over budget — remote transfer held pending confirm")
        self.estimate = estimate
        self.steps = steps


class AllocationHold(Exception):
    """srun-step mode is requested but no held Slurm allocation was found, so the
    server-side lowering/reduce can't run. Carries a human note, the RECOMMENDED
    salloc command (never run here — the user approves and Claude runs it), and
    the steps so far, so the planner can report NEEDS ALLOCATION without shipping
    anything."""

    def __init__(self, reason, salloc_cmd, steps):
        super().__init__("no held allocation — reduce held pending salloc")
        self.reason = reason
        self.salloc_cmd = salloc_cmd
        self.steps = steps


_DEFAULT_WALLTIME = "00:30:00"        # 30 min — the default for a proposed salloc
_DEFAULT_PARTITION = "skylake-gold"   # recommended; never guess `general`


def recommended_salloc(host):
    """The salloc command we PROPOSE when no allocation exists. `--no-shell` holds
    the allocation for reuse; `-J` must match VISLANG_SRUN_NAME so auto-discovery
    finds it; the partition follows VISLANG_SRUN_PARTITION when set, else the
    recommended skylake-gold; walltime defaults to 30 min. Never run silently —
    an allocation spends shared HPC time and requires the user's approval."""
    name, part = _alloc_filters()
    name = name or "vislang"
    part = part or _DEFAULT_PARTITION
    return (f"ssh {host} 'salloc --no-shell -J {name} -N 1 -p {part} "
            f"-t {_DEFAULT_WALLTIME}'")


def _srun_requested():
    """Raw VISLANG_SRUN_JOBID: a numeric allocation id, the literal 'auto'
    (discover the held allocation via squeue at reduce time), or None (direct
    ssh — the reducer runs on whatever node ssh lands on). Presence switches on
    srun mode: each reduce runs as a step INTO a held allocation (instant, no
    queue), never `sbatch` (a fresh scheduler job queues independently)."""
    v = os.environ.get("VISLANG_SRUN_JOBID", "").strip()
    return v or None


def _alloc_filters():
    """(name, partition) auto-discovery filters from the env (empty = no filter)."""
    return (os.environ.get("VISLANG_SRUN_NAME", "").strip(),
            os.environ.get("VISLANG_SRUN_PARTITION", "").strip())


def _squeue_candidates(conn):
    """(rc, err, candidates) for the user's RUNNING Slurm allocations, filtered by
    VISLANG_SRUN_NAME / VISLANG_SRUN_PARTITION. candidates is a list of
    (jobid, partition, name) tuples. Shared by _discover_jobid (the run path) and
    allocation_status (the inspect-time probe), so both read the same Slurm state
    the same way."""
    name, part = _alloc_filters()
    rc, out, err = run_remote(conn, 'squeue -h -u $(whoami) -t RUNNING -o "%A|%P|%j"')
    cand = []
    if rc == 0:
        for line in out.splitlines():
            row = line.strip().split("|")
            if (len(row) >= 3 and row[0].strip().isdigit()
                    and (not part or row[1].strip() == part)
                    and (not name or row[2].strip() == name)):
                cand.append((row[0].strip(), row[1].strip(), row[2].strip()))
    return rc, err, cand


def _discover_jobid(conn, steps):
    """Resolve VISLANG_SRUN_JOBID=auto to the user's held allocation on the
    remote via `squeue`. Filters to VISLANG_SRUN_NAME (job name, e.g. from
    `salloc -J vislang`) and/or VISLANG_SRUN_PARTITION if set; on several, picks
    the newest (highest id). Raises RemoteUnavailable if none — the user must
    `salloc` first (the planner then falls back to a whole-file fetch)."""
    name, part = _alloc_filters()
    rc, err, cand = _squeue_candidates(conn)
    if rc != 0:
        raise RemoteUnavailable("could not query Slurm for a held allocation: "
                                + (err.strip()[-200:] or f"rc={rc}"))
    if not cand:
        where = ", ".join(f"{k} {v}" for k, v in
                          (("name", name), ("partition", part)) if v)
        raise RemoteUnavailable(
            f"VISLANG_SRUN_JOBID=auto but no RUNNING allocation"
            + (f" matching {where}" if where else "")
            + f" for you on {conn.host}; run `salloc --no-shell ...` first")
    jid = max(cand, key=lambda c: int(c[0]))[0]
    steps.append(f"srun: discovered held allocation jobid={jid}"
                 + (f" ({len(cand)} found, using newest)" if len(cand) > 1 else ""))
    return jid


def allocation_status(conn):
    """Non-raising detection of a held Slurm allocation, for surfacing at inspect
    time. Returns a dict {mode, present, jobid, candidates, host, reason}:

      mode     'direct' (VISLANG_SRUN_JOBID unset — no allocation needed),
               'auto' (discover the newest held one), or a fixed jobid string.
      present  True / False, or None when it can't be determined (Slurm query
               failed) or is not applicable (direct mode).
      jobid    the allocation that a reduce would step into, when present.

    Mirrors _discover_jobid's discovery, but reports instead of raising."""
    requested = _srun_requested()
    host = getattr(conn, "host", "the remote")
    if requested is None:
        return {"mode": "direct", "present": None, "jobid": None, "candidates": [],
                "host": host,
                "reason": "direct-ssh mode (VISLANG_SRUN_JOBID unset) — the reducer "
                          "runs on whatever node ssh lands on; no held allocation "
                          "needed."}
    rc, err, cand = _squeue_candidates(conn)
    if rc != 0:
        return {"mode": requested, "present": None, "jobid": None, "candidates": cand,
                "host": host,
                "reason": "could not query Slurm: " + (err.strip()[-200:] or f"rc={rc}")}
    if requested != "auto":                       # a fixed jobid: is it RUNNING?
        present = any(c[0] == requested for c in cand)
        return {"mode": requested, "present": present,
                "jobid": requested if present else None, "candidates": cand,
                "host": host,
                "reason": (f"requested allocation jobid={requested} is RUNNING"
                           if present else
                           f"requested jobid={requested} is not RUNNING for you "
                           f"on {host}")}
    if not cand:
        name, part = _alloc_filters()
        where = ", ".join(f"{k} {v}" for k, v in
                          (("name", name), ("partition", part)) if v)
        return {"mode": "auto", "present": False, "jobid": None, "candidates": [],
                "host": host,
                "reason": "no RUNNING allocation" + (f" matching {where}" if where else "")
                          + f" for you on {host}"}
    jid = max(cand, key=lambda c: int(c[0]))[0]
    extra = f", {len(cand)} found — using newest" if len(cand) > 1 else ""
    return {"mode": "auto", "present": True, "jobid": jid, "candidates": cand,
            "host": host,
            "reason": f"held allocation RUNNING (jobid={jid}{extra})"}


def _remote_tmp():
    """Base dir on the remote for the staged plan + reduced .npz. srun places
    vislang_exec on a COMPUTE node whose local /tmp the login-node pull can't
    see, so srun mode needs a shared-FS dir; direct-ssh mode keeps /tmp (the
    reducer runs on the same node ssh landed on)."""
    if _srun_requested():
        return os.environ.get("VISLANG_REMOTE_TMP", "~/.vislang/reduce")
    return "/tmp"


def _executor_cmd(rout, plan_path=None, jobid=None, folder=False, manifest_path=None):
    """The remote command that runs vislang_exec -> rout.

    Direct `python vislang_exec.py` (no container): the deps are assumed present
    on the remote (shared-filesystem repo + conda env). When `jobid` is given the
    command is wrapped in `srun --jobid=<alloc>` so it runs as a step inside a
    held allocation (a compute node, instant), reading the plan from a staged
    file (`plan_path`) — srun's stdin forwarding is unreliable. `folder=True`
    emits `--outdir` (a whole-timeseries reduce writes one file per timestep into
    a directory) instead of `--out` (a single-file reduce writes one .npz).

    Binding-mode consistency: the reducer defaults to cache-only binding, which
    agrees with a local `inspect` that froze the binding on a shared cache. If
    the LOCAL side is running generic (VISLANG_NO_BINDING), the remote must too,
    or the two disagree on variable names — so forward that flag. Under srun the
    `VAR=val cmd` shell prefix would be read as srun's executable, so the flag is
    forwarded via `--export` instead."""
    py = os.environ.get("VISLANG_REMOTE_PYTHON", "python")
    repo = os.environ.get("VISLANG_REMOTE_REPO",
                          os.path.dirname(os.path.abspath(__file__)))
    flag = "--outdir" if folder else "--out"
    man = f" --manifest {manifest_path}" if manifest_path else ""   # catalog delta
    if jobid is None:
        env = "VISLANG_NO_BINDING=1 " if os.environ.get("VISLANG_NO_BINDING") else ""
        return f"{env}{py} {repo}/vislang_exec.py --stdin {flag} {rout}{man}"
    # srun mode: run as a step in the held allocation, plan from a staged file.
    srun_args = os.environ.get("VISLANG_SRUN_ARGS", "--overlap -n1")
    export = "ALL,VISLANG_NO_BINDING=1" if os.environ.get("VISLANG_NO_BINDING") else "ALL"
    return (f"srun --jobid={jobid} {srun_args} --export={export} "
            f"{py} {repo}/vislang_exec.py --plan {plan_path} {flag} {rout}{man}")


def _normalize_remote(uri):
    if uri.startswith("ssh://"):
        rest = uri[len("ssh://"):]
        if "/" not in rest:
            raise ValueError(f"ssh URL needs a path: {uri!r}")
        hostpart, path = rest.split("/", 1)
        return f"{hostpart}:/{path}"
    return uri


# ---------------------------------------------------------------------------
# The narrowing key: identifies WHAT a cached extent is, order included
# ---------------------------------------------------------------------------
def _narrow_key(middle):
    """A JSON-safe, order-faithful description of the narrowing (minus fields —
    projection is the catalog's variable axis, not part of the key). Written
    order is part of the key because threshold/subsample do not commute."""
    forms = []
    for n in middle:
        if n.kind == "region":
            forms.append(["region", [[a, lo, hi] for a, lo, hi in n.ranges]])
        elif n.kind == "subsample":
            forms.append(["subsample", n.uniform,
                          [[a, f] for a, f in (n.per_axis or ())]])
        elif n.kind == "threshold":
            forms.append(["threshold", n.var, n.op, n.value])
    return {"forms": forms}


def _split_middle(middle):
    """(narrowing prefix, trailing compress nodes). Narrowing after a compress
    is rejected here exactly as the local planner rejects it."""
    prefix, compresses = [], []
    for n in middle:
        if n.kind == "compress":
            compresses.append(n)
        elif n.kind in _NARROWING:
            if compresses:
                raise ValueError("narrowing after compress is not supported; "
                                 "put fields/region/subsample/threshold before compress")
            prefix.append(n)
        else:
            raise ValueError(f"unknown form {n.kind!r} in pipeline")
    return prefix, compresses


def _rebuild_prefix(remote_path, positions, prefix, project):
    """The remote chain: source(remote-local path) -> narrowing forms, with the
    projection replaced by `project` (the catalog's missing-variables list).
    Non-fields forms keep their written order; fields floats to the front (it
    is an absolute cut — commutes with everything)."""
    node = SourceNode(uri=remote_path, positions=tuple(positions) if positions else None)
    if project is not None:
        node = FieldsNode(upstream=node, keep=tuple(project))
    for n in prefix:
        if n.kind == "fields":
            continue                       # replaced by `project` above
        if n.kind == "region":
            node = RegionNode(upstream=node, ranges=n.ranges)
        elif n.kind == "subsample":
            node = SubsampleNode(upstream=node, uniform=n.uniform, per_axis=n.per_axis)
        elif n.kind == "threshold":
            node = ThresholdNode(upstream=node, var=n.var, op=n.op, value=n.value)
    return node


def _projection_of(prefix):
    """The spec's final projection (last fields wins, matching the planner's
    sequential-narrowing semantics), or None = all variables."""
    keep = None
    for n in prefix:
        if n.kind == "fields":
            keep = list(n.keep)
    return keep


# ---------------------------------------------------------------------------
def _catalog_schema(schema):
    """The persistable part of a schema. `itemsizes` is live inspect metadata and
    is deliberately NOT stored in the catalog (see DatasetInfo.itemsizes)."""
    return {k: v for k, v in (schema or {}).items() if k != "itemsizes"}


def _prime_schema(conn, remote_path, catalog, sid, steps):
    """Fetch the schema from the LOGIN NODE (metadata only, no allocation, no
    bulk read) and store it, so the very first run of a file can size its own
    transfer. Without this the cost gate is fed by the catalog, which is only
    written AFTER a reduce — i.e. the first run, the one you cannot predict,
    would ship ungated. Returns the schema dict (with live `itemsizes`) or None;
    any failure is non-fatal — the caller proceeds as before."""
    try:
        from my_inspect import _run_remote_inspect
        meta = _run_remote_inspect(conn, remote_path)
        if not meta or not meta.get("ok") or meta.get("needs_adapter"):
            return None
        schema = meta.get("schema")
        if not schema:
            return None
        catalog.store_schema(sid, _catalog_schema(schema))
        steps.append(f"login-node inspect: schema cached "
                     f"({len(schema.get('variables') or [])} vars, "
                     f"dims={schema.get('dimensions')})")
        return schema
    except Exception as e:
        steps.append(f"(login-node inspect skipped: {type(e).__name__}: {e})")
        return None


def _row_cutting(narrowing):
    """True when a post-read op DROPS elements rather than masking them in place.
    On a grid a threshold NaN-fills (shape preserved, bytes knowable); on particles
    it removes rows, so the transferred count is only known after the read."""
    from narrowing import RowMask, RowSample
    return any(isinstance(op, (RowMask, RowSample))
               for op in (getattr(narrowing, "post_ops", ()) or ()))


def _info_from_schema(uri, schema):
    """A DatasetInfo standing in for the remote source, built from the schema the
    remote itself reported. Not a guess: `_prime_schema` obtained it by running
    the inspect next to the data (same binding mode the reducer will use) and the
    catalog stored it under a size+mtime+header identity, so if the file changed
    the identity changed and the schema was re-read."""
    from my_inspect import _dims_from_json
    info = DatasetInfo(uri, (schema or {}).get("filetype", "remote"),
                       list((schema or {}).get("variables") or []),
                       dimensions=_dims_from_json((schema or {}).get("dimensions") or {}),
                       itemsizes=dict((schema or {}).get("itemsizes") or {}))
    info.positions = (tuple(schema["positions"])
                      if (schema or {}).get("positions") else None)
    return info


def _check_before_shipping(src, prefix, schema, steps):
    """Static-check the request against the schema we already hold, BEFORE any
    plan is shipped or any scheduler step is spent.

    The schema is in hand by this point either way — the cost estimate is computed
    from it. Asking it the second obvious question ("is this request even
    well-posed?") costs nothing extra, and turns a malformed spec from a compute-
    node traceback into a local error. `_lower` is the SAME lowering the read path
    uses, so a request that passes here cannot be rejected later for a different
    reason.

    Returns the fused Narrowing (reused for the estimate, so the priced shape is
    the one that would actually be read), or None when there is no schema to check
    against. Raises on an invalid request — that is the point."""
    if not schema:
        return None
    from planner import _lower
    info = _info_from_schema(src.uri, schema)
    narrowing, _pending = _lower(info, prefix, [])      # steps discarded; see below
    steps.append(f"static check: request valid against the cached schema "
                 f"({len(info.variables)} vars, dims={info.dimensions or {}})")
    return narrowing


def _reduce_estimate(src, key, schema, missing, conn, steps, narrowing=None):
    """Estimate a single-file remote reduce's wire transfer and append a readable
    line to `steps`. Returns a CostEstimate; when the reduced size can't be known
    locally the estimate carries read_mb=None (over_budget False), so the caller
    does not gate.

    The byte math is `my_estimate.estimate_plan_cost` — the same estimator the
    local paths use. `narrowing` is the fused plan from the pre-ship static check
    (`_check_before_shipping`), i.e. the SAME lowering the read will perform, so
    the priced shape is the shape that gets read. Without one (no schema to check
    against) it falls back to the catalog's own form-fuse."""
    from my_estimate import estimate_plan_cost, format_plan_estimate
    from my_inspect import _dims_from_json
    from narrowing import Narrowing, AxisRange

    try:
        net_bw = measure_bandwidth(conn)
    except Exception:
        net_bw = None

    dims = _dims_from_json((schema or {}).get("dimensions") or {})
    grid = dims.get("grid")
    variables = list((schema or {}).get("variables") or [])
    wire_vars = list(missing) if missing else variables

    info, note = None, None
    if schema is None:
        narrowing = None
        note = ("schema unavailable (login-node inspect failed) — reduced size "
                "not estimated; not gated.")
    elif narrowing is not None:
        # Lowered by the static check. A grid `threshold` is a shape-preserving
        # NaN mask, so its bytes ARE knowable — the old path refused to price any
        # threshold because it asked the catalog's fuse, whose real question is
        # "can this be sliced out of a cached superset?" (where a value cut does
        # disqualify). Those are different questions; this one is answerable.
        info = _info_from_schema(src.uri, schema)
        if _row_cutting(narrowing):
            note = ("value-dependent row cut — the count survives only after the "
                    "read, so this prices the pre-cut selection: an UPPER bound.")
    elif isinstance(grid, tuple) and len(grid) == 3:
        from my_catalog import _grid_ranges_of
        ranges = _grid_ranges_of(key, grid)
        if ranges is None:
            note = ("value-dependent narrowing (threshold) — reduced size not "
                    "knowable before the read; not gated.")
        else:
            narrowing = Narrowing(
                grid_ranges=[AxisRange(a, b, s or 1) for a, b, s in ranges],
                project=tuple(wire_vars))
            info = _info_from_schema(src.uri, schema)
    else:
        note = "particle/unknown modality — reduced size not estimated; not gated."

    # No estimable shape -> a bare stub, so estimate_plan_cost reports read_mb
    # None (modality unknown) rather than pricing the whole grid.
    est = estimate_plan_cost(info=info or DatasetInfo(src.uri, "remote", []),
                             narrowing=narrowing, site="remote",
                             wire_vars=wire_vars, net_bw_bps=net_bw)
    if note:
        est.notes.append(note)
    steps.append(format_plan_estimate(est))
    return est


def remote_reduce(src, middle, confirm=False):
    """Run the narrowing prefix of `middle` on the remote host of src.uri.
    Returns (loaded DatasetInfo, steps, CostEstimate|None). Raises
    RemoteUnavailable to make the planner fall back, BudgetHold when the
    estimated wire transfer is over budget and `confirm` is False, or a real
    error for a broken spec."""
    steps = []
    prefix, compresses = _split_middle(middle)
    if not prefix:
        raise RemoteUnavailable("no narrowing forms — whole-file fetch is equivalent")

    norm = _normalize_remote(src.uri)
    _, host, remote_path = _parse_remote(norm)
    with timing.phase("probe_source"):
        conn = establish_connection(norm)
        if conn.method != "ssh-key":
            raise RemoteUnavailable("remote reduce needs ssh key auth")

        st = remote_stat(conn, remote_path)
        if st is None:
            raise RemoteUnavailable(f"cannot stat {remote_path} on {host}")
        size, mtime = st
        sid = make_source_id(norm, size, mtime,
                             remote_header_hash(conn, remote_path) or "")
    # source_bytes is the denominator of the reduction factor: how much a
    # whole-file fetch would have moved, against what actually crossed.
    timing.note(host=host, source_bytes=size, source_id=sid[:8])
    steps.append(f"remote source {host}:{remote_path} ({size / 1e6:.1f} MB, id={sid[:8]})")

    # --- catalog delta: fetch only what we don't already hold -----------------
    from vislang_paths import cache_root
    catalog = ExtentCatalog(cache_root())
    key = _narrow_key(prefix)
    project = _projection_of(prefix)
    schema = catalog.schema(sid)
    timing.note(schema_cached=schema is not None)
    if schema is None:
        # First sighting of this file: read its schema on the LOGIN NODE before
        # anything ships. Feeds both the cost gate (below) and `want` — without
        # it an unprojected spec can't be diffed against the catalog either.
        with timing.phase("login_node_inspect"):
            schema = _prime_schema(conn, remote_path, catalog, sid, steps)
    want = project if project is not None else (
        list(schema["variables"]) if schema else None)

    have, missing = {}, want
    if want is not None:
        with timing.phase("catalog_delta"):
            have, missing = catalog.delta(sid, want, key)
        # want / have / missing counted in VARIABLES here (the single-file axis);
        # the folder path counts (timestep, variable) pairs.
        timing.note(want_vars=len(want), cached_vars=len(have),
                    missing_vars=len(missing or []),
                    cached_bytes=int(sum(a.nbytes for a in have.values())) if have else 0)
        if have:
            steps.append(f"catalog: cached {sorted(have)} for this narrowing")

    # --- cost gate: estimate the wire transfer BEFORE shipping the read -------
    # Remote cost is bytes over ssh; we measure the link with a synthetic probe
    # and gate on size + measured time. On a full cache hit nothing crosses, so
    # there is nothing to gate. Runs BEFORE the allocation gate: it is login-node
    # only (schema + a bandwidth probe), so even a run that stops for a missing
    # allocation reports what it would have cost.
    # --- static check BEFORE the plan ships ----------------------------------
    # The schema is already in hand (catalog or login-node inspect), so validate
    # here rather than letting a compute node discover a typo. Raises on an
    # invalid request, which costs no bandwidth probe, no scheduler query, no
    # srun step and no shipped plan.
    with timing.phase("static_check"):
        checked = _check_before_shipping(src, prefix, schema, steps)
    timing.note(pre_validated=checked is not None)

    if want is None or missing:
        with timing.phase("cost_estimate"):
            estimate = _reduce_estimate(src, key, schema, missing, conn, steps,
                                        narrowing=checked)
        if estimate is not None and estimate.over_budget and not confirm:
            raise BudgetHold(estimate, steps)
    else:
        estimate = None
        timing.note(route="catalog_full_hit")

    # --- allocation gate: the server-side lowering/reduce srun-steps into a HELD
    # Slurm allocation. In AUTO discovery mode, if none exists we HOLD and PROPOSE
    # salloc (never run it here) rather than silently shipping / falling back to a
    # whole-file fetch. Only when there is compute to ship — a full cache hit needs
    # no allocation. A FIXED VISLANG_SRUN_JOBID is trusted as-is (the run path uses
    # it directly, so we don't second-guess it here). confirm=True proceeds anyway
    # (auto-mode then falls back to a whole-file fetch downstream).
    if (want is None or missing) and _srun_requested() == "auto" and not confirm:
        with timing.phase("allocation_probe"):
            alloc = allocation_status(conn)
        if alloc.get("present") is False:      # definitively absent (not unknown)
            steps.append(f"allocation: {alloc['reason']}")
            raise AllocationHold(alloc["reason"], recommended_salloc(host), steps)

    fetched = {}
    if want is None or missing:
        meta, fetched = _run_remote_prefix(conn, remote_path, src, prefix, missing, steps)
        catalog.store_schema(sid, _catalog_schema(meta["schema"]))
        schema = meta["schema"]
        for var, arr in fetched.items():
            catalog.store(sid, var, key, arr)
    else:
        steps.append("catalog: full hit — nothing crossed the wire")

    if schema is None:
        raise RemoteUnavailable("no schema for cached-only assembly")   # defensive

    # --- assemble the loaded DatasetInfo for the local suffix -----------------
    data = dict(have)
    data.update(fetched)
    order = want if want is not None else list(data)
    variables = [v for v in order if v in data]
    info = DatasetInfo(src.uri, schema.get("filetype", "remote"), variables,
                       dimensions=schema.get("dimensions") or {},
                       attributes={"remote_reduced": True, "source_id": sid})
    info.positions = tuple(schema["positions"]) if schema.get("positions") else None
    info.data = {v: data[v] for v in data}
    info.loaded = True
    info.selection_info = {"variables_loaded": variables,
                           "dimension_selection": key,
                           "site": "remote"}
    total = sum(a.nbytes for a in data.values())
    timing.note(result_bytes=int(total), fetched_vars=len(fetched))
    steps.append(f"assembled {len(data)} var(s), {total / 1e6:.1f} MB "
                 f"({len(have)} cached, {len(fetched)} fetched)")
    return info, steps, estimate


def _run_remote_prefix(conn, remote_path, src, prefix, missing, steps):
    """Serialize the (missing-variables) prefix, run vislang_exec on the remote,
    pull the reduced npz, and return (meta, {var: array})."""
    terminal = _rebuild_prefix(remote_path, src.positions, prefix, missing)
    plan = to_plan_json(terminal)

    tag = os.urandom(4).hex()
    base = _remote_tmp()
    rout = f"{base}/vislang_reduce_{tag}.npz"
    requested = _srun_requested()
    jobid = _discover_jobid(conn, steps) if requested == "auto" else requested
    steps.append("ship plan.json (the AST moved to the remote): " + plan)

    # [source (ssh://) ],
    # [region ] -> 
    # Fields

    # region fold into source (localpath) fold 
    # 
    #

    steps.append(f"remote exec: narrowing prefix -> {rout}"
                 + (f" (vars {missing})" if missing else " (all vars)"))
    # ship_plan / remote_exec / pull are the three phases the paper's remote-
    # execution comparison needs kept apart: the AST is bytes-negligible, the
    # reduce is compute next to the data, and only the pull touches the link.
    timing.note(plan_bytes=len(plan), srun=bool(jobid))
    if jobid is None:
        cmd = _executor_cmd(rout)                       # plan piped via stdin
        with timing.phase("remote_exec", srun=False):
            rc, out, err = run_remote(conn, cmd, stdin_bytes=plan.encode())
        rplan = None
    else:
        # srun runs vislang_exec on a compute node; stage the plan to shared FS
        # (its /tmp and the login node's differ) and read it via --plan.
        rplan = f"{base}/vislang_plan_{tag}.json"
        steps.append(f"srun --jobid={jobid}: step into held allocation "
                     f"(plan staged at {rplan})")
        with timing.phase("ship_plan", bytes=len(plan)):
            stc, _, ste = run_remote(conn, f"mkdir -p {base} && cat > {rplan}",
                                     stdin_bytes=plan.encode())
        if stc != 0:
            raise RuntimeError(f"staging plan to {rplan} failed: {ste.strip()[-300:]}")
        cmd = _executor_cmd(rout, plan_path=rplan, jobid=jobid)
        with timing.phase("remote_exec", srun=True, jobid=jobid):
            rc, out, err = run_remote(conn, cmd)

    meta = _parse_meta(out)
    if meta is None or not meta.get("ok", False):
        detail = (meta or {}).get("error") or err.strip()[-_ERR_TAIL:] or f"rc={rc}"
        raise RuntimeError(f"remote reduce failed: {detail}")

    # The remote ran its OWN plan_pipeline (same tracing code) — surface what it
    # actually did, not just that it ran (mirrors the folder path's local:/remote:
    # split).
    if meta.get("steps"):
        steps.append("remote:  --- what ran on the remote ---")
        for s in meta["steps"]:
            steps.append(f"remote:  {s}")

    from vislang_paths import cache_root
    local = os.path.join(cache_root(), f"pull_{tag}.npz")
    with timing.phase("pull"):
        pulled = transfer(conn, rout, local, size_warn_mb=10 ** 9)   # never prompt
    cleanup = f"rm -f {rout}" + (f" {rplan}" if rplan else "")
    run_remote(conn, cleanup)                                    # best-effort cleanup
    if pulled is None:
        raise RuntimeError("transfer of the reduced result failed")

    with np.load(pulled) as z:
        arrays = {k: z[k] for k in z.files}
    os.remove(pulled)                       # extents are the durable copy
    total = sum(a.nbytes for a in arrays.values())
    steps.append(f"pulled {len(arrays)} var(s), {total / 1e6:.1f} MB over the wire")
    return meta, arrays


def _parse_meta(stdout_text):
    """The meta JSON vislang_exec prints between sentinel lines."""
    try:
        chunk = stdout_text.split(META_BEGIN, 1)[1].split(META_END, 1)[0]
        return json.loads(chunk.strip())
    except (IndexError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# Whole-folder (timeseries) reduce: ONE remote job, ONE directory pulled back
# ---------------------------------------------------------------------------
def _rebuild_folder_chain(remote_dir, positions, middle, ts_nodes):
    """The remote chain for a folder reduce: source(remote-local DIR) ->
    timesteps(range) -> the middle forms in written order. On the remote the dir
    is a *local* folder, so plan_pipeline dispatches it to _plan_folder and maps
    the chain over each timestep. Unlike _rebuild_prefix (single-file, catalog
    projection) this keeps `fields` in place and carries `compress` too (it runs
    remotely, shrinking the transfer)."""
    node = SourceNode(uri=remote_dir,
                      positions=tuple(positions) if positions else None)
    if ts_nodes:                                   # inclusive #N range, as the planner folds it
        lo = max(n.start for n in ts_nodes)
        hi = min(n.stop for n in ts_nodes)
        node = TimestepsNode(upstream=node, start=lo, stop=hi)
    for n in middle:
        if n.kind == "fields":
            node = FieldsNode(upstream=node, keep=tuple(n.keep))
        elif n.kind == "region":
            node = RegionNode(upstream=node, ranges=n.ranges)
        elif n.kind == "subsample":
            node = SubsampleNode(upstream=node, uniform=n.uniform, per_axis=n.per_axis)
        elif n.kind == "threshold":
            node = ThresholdNode(upstream=node, var=n.var, op=n.op, value=n.value)
        elif n.kind == "compress":
            node = CompressNode(upstream=node, variables=tuple(n.variables),
                                error_bound=n.error_bound, mode=n.mode)
        else:
            raise ValueError(f"unknown form {n.kind!r} in folder chain")
    return node


def remote_folder_reduce(src, middle, ts_nodes, out_local_dir):
    """Reduce a remote folder timeseries as ONE remote job, CATALOG-AWARE: the
    local extent catalog is consulted per (timestep, variable), so only the delta
    crosses the wire. Re-running with one more field fetches just that field, for
    just the timesteps that lack it; a fully-cached timestep is never touched.

    Flow: one listing call gets each timestep's (size, mtime) -> per-file
    source_id -> catalog.delta -> a manifest {label: [missing vars]}. The missing
    work runs in ONE remote job (vislang_exec --manifest) that returns a single
    npz bundle of just those (timestep, var) arrays; we merge them with the
    cached extents locally, store the fresh ones, and write one file per timestep.

    Needs an explicit fields() projection to know each file's variables without a
    per-file inspect; without one it falls back to the non-catalog save-to-dir
    batch. Returns (local_out_dir, steps, report). Raises RemoteUnavailable (the
    planner falls back to a whole-folder fetch) or RuntimeError for a real
    failure."""
    from my_catalog import make_source_id
    from vislang_paths import cache_root
    from my_compress import compress as _compress

    steps = []
    prefix, compresses = _split_middle(middle)          # narrowing (incl fields) + compress
    project = _projection_of(prefix)                    # requested vars, or None
    narrow_prefix = [n for n in prefix if n.kind != "fields"]   # region/subsample/threshold
    narrow_key = _narrow_key(prefix)                    # ordered, fields-independent

    norm = _normalize_remote(src.uri)
    _, host, remote_dir = _parse_remote(norm)
    with timing.phase("probe_source"):
        conn = establish_connection(norm)
        if conn.method != "ssh-key":
            raise RemoteUnavailable("remote reduce needs ssh key auth")
    timing.note(host=host)

    # No projection -> we can't know each file's variables from metadata; run the
    # non-catalog whole-folder batch (still one remote job, one dir pull).
    if project is None:
        steps.append("no fields() projection — catalog needs an explicit variable "
                     "set; running the non-catalog whole-folder batch")
        return _folder_batch_savedir(conn, host, remote_dir, src, middle, ts_nodes,
                                     out_local_dir, steps)

    from my_inspect import remote_timestep_files_stat
    with timing.phase("list_timesteps"):
        files = remote_timestep_files_stat(src.uri)     # [(label, uri, size, mtime)]
    if files is None:
        raise RemoteUnavailable(f"cannot list remote folder {src.uri!r}")
    if ts_nodes:
        lo, hi = max(n.start for n in ts_nodes), min(n.stop for n in ts_nodes)
        files = [f for f in files if lo <= f[0] <= hi]
    if not files:
        raise ValueError(f"no timesteps match in {src.uri}")
    timing.note(source_bytes=int(sum(f[2] for f in files)),
                selected_timesteps=len(files))

    # --- catalog delta per timestep (local, no network) ----------------------
    catalog = ExtentCatalog(cache_root())
    per = {}                                            # label -> {uri, sid, have, missing}
    manifest = {}                                       # label(str) -> [missing vars]  (fetch set)
    with timing.phase("catalog_delta", n=len(files)):
        for label, uri, size, mtime in files:
            sid = make_source_id(uri, size, mtime)
            have, missing = catalog.delta(sid, project, narrow_key)
            per[label] = {"uri": uri, "sid": sid, "have": have, "missing": missing}
            if missing:
                manifest[str(label)] = missing

    n = len(files)
    union_missing = sorted({v for vs in manifest.values() for v in vs})
    total_pairs = n * len(project)                      # (timestep, variable) extents needed
    fetched_pairs = sum(len(vs) for vs in manifest.values())
    reused_pairs = total_pairs - fetched_pairs
    steps.append(f"catalog: reused {reused_pairs}/{total_pairs} (timestep,variable) "
                 f"extent(s)"
                 + (f"; fetching {union_missing} ({fetched_pairs} pair(s)) for "
                    f"{len(manifest)} timestep(s)" if manifest
                    else " — full hit, nothing crosses the wire"))

    # --- static check BEFORE the plan ships -----------------------------------
    # Same reasoning as the single-file path: a malformed request should not cost
    # a scheduler step. Timestep 0's schema stands for the series — sharing one
    # schema is what makes a folder a series. Free once the catalog holds it; one
    # login-node inspect on the very first sighting, which the run would have
    # spent anyway (the reduce stores the schema when it returns).
    label0 = files[0][0]
    schema0 = catalog.schema(per[label0]["sid"])
    if schema0 is None and manifest:
        _, _, ts0_path = _parse_remote(_normalize_remote(per[label0]["uri"]))
        with timing.phase("login_node_inspect"):
            schema0 = _prime_schema(conn, ts0_path, catalog, per[label0]["sid"], steps)
    with timing.phase("static_check"):
        _check_before_shipping(src, prefix, schema0, steps)
    timing.note(pre_validated=schema0 is not None)

    # --- fetch only the missing (timestep, variable) pairs, in ONE remote job -
    fetched = {}                                        # (label, var) -> array
    schema = None
    jobid = None
    if manifest:
        terminal = _rebuild_delta_source(remote_dir, src.positions, narrow_prefix)
        plan = to_plan_json(terminal)
        tag = os.urandom(4).hex()
        base = _remote_tmp()
        routdir = f"{base}/vislang_reduce_{tag}"
        rmanifest = f"{base}/vislang_manifest_{tag}.json"
        requested = _srun_requested()
        jobid = _discover_jobid(conn, steps) if requested == "auto" else requested
        steps.append(f"remote folder {host}:{remote_dir} (catalog delta)")

        # The manifest is a file on BOTH paths (--manifest takes a path).
        man_bytes = len(json.dumps(manifest))
        with timing.phase("ship_plan", bytes=len(plan) + man_bytes):
            stc, _, ste = run_remote(conn, f"mkdir -p {base} && cat > {rmanifest}",
                                     stdin_bytes=json.dumps(manifest).encode())
            if stc != 0:
                raise RuntimeError(f"staging manifest failed: {ste.strip()[-300:]}")
            steps.append(f"ship plan.json + manifest (delta {union_missing}) to the remote")
            rplan = None
            if jobid is not None:
                rplan = f"{base}/vislang_plan_{tag}.json"
                steps.append(f"srun --jobid={jobid}: one step for the whole delta")
                stc, _, ste = run_remote(conn, f"cat > {rplan}",
                                         stdin_bytes=plan.encode())
                if stc != 0:
                    raise RuntimeError(f"staging plan failed: {ste.strip()[-300:]}")

        # ONE remote job for the whole delta, however many timesteps it spans —
        # `remote_jobs` in the counters stays 1 as N grows.
        with timing.phase("remote_exec", srun=bool(jobid), jobid=jobid,
                          n_timesteps=len(manifest)):
            if jobid is None:
                cmd = _executor_cmd(routdir, folder=True, manifest_path=rmanifest)
                rc, out, err = run_remote(conn, cmd, stdin_bytes=plan.encode())
            else:
                cmd = _executor_cmd(routdir, plan_path=rplan, jobid=jobid,
                                    folder=True, manifest_path=rmanifest)
                rc, out, err = run_remote(conn, cmd)

        meta = _parse_meta(out)
        if meta is None or not meta.get("ok", False):
            detail = (meta or {}).get("error") or err.strip()[-_ERR_TAIL:] or f"rc={rc}"
            raise RuntimeError(f"remote folder delta failed: {detail}")
        schema = meta.get("schema")

        pull_dir = os.path.join(cache_root(), f"folderpull_{tag}")
        remote_out = meta.get("outdir", routdir)
        with timing.phase("pull"):
            pulled = transfer_dir(conn, remote_out, pull_dir)
        cleanup = f"rm -rf {routdir} {rmanifest}" + (f" {rplan}" if jobid else "")
        run_remote(conn, cleanup)                       # best-effort
        if pulled is None:
            raise RuntimeError("transfer of the delta bundle failed")

        with np.load(os.path.join(pull_dir, "bundle.npz")) as z:
            total = 0
            for k in z.files:                           # keys are "<label>/<var>"
                lab, var = k.split("/", 1)
                arr = z[k]
                fetched[(int(lab), var)] = arr
                total += arr.nbytes
                catalog.store(per[int(lab)]["sid"], var, narrow_key, arr)  # cache fresh
        import shutil
        shutil.rmtree(pull_dir, ignore_errors=True)
        for label in (int(k) for k in manifest):
            catalog.store_schema(per[label]["sid"], schema)
        steps.append(f"pulled delta: {len(fetched)} (timestep,var) array(s), "
                     f"{total / 1e6:.2f} MB over the wire")

    # --- assemble each timestep from cached + fetched, then save -------------
    if schema is None:                                  # fully cached: schema from catalog
        for label in per:
            schema = catalog.schema(per[label]["sid"])
            if schema:
                break
    if schema is None:
        raise RemoteUnavailable("no cached schema for a fully-cached folder")

    per_step = []
    for label, uri, *_ in files:
        data = {}
        for var in project:
            if var in per[label]["have"]:
                data[var] = per[label]["have"][var]
            else:
                data[var] = fetched[(label, var)]
        loaded = DatasetInfo(f"{src.uri}#{label}", schema.get("filetype", "remote"),
                             list(project), dimensions=schema.get("dimensions") or {},
                             attributes={"remote_reduced": True})
        loaded.positions = tuple(schema["positions"]) if schema.get("positions") else None
        loaded.data = data
        loaded.loaded = True
        for c in compresses:                            # compress is a LOCAL suffix (lossy; keep extents raw)
            loaded = _compress(loaded, list(c.variables), c.error_bound, c.mode)
        per_step.append((label, loaded))

    from my_save import save_timeseries
    with timing.phase("sink_save_timeseries", n=len(per_step)):
        out = save_timeseries(per_step, out_local_dir, schema.get("filetype"))
    steps.append(f"-> save timeseries -> {out}")
    report = {"n": n, "host": host, "jobid": jobid, "cached": n - len(manifest),
              "fetched_timesteps": len(manifest), "fetched_vars": union_missing,
              "total_pairs": total_pairs, "reused_pairs": reused_pairs,
              "fetched_pairs": fetched_pairs,
              "timesteps": [lab for lab, *_ in files]}
    return out, steps, report


def _rebuild_delta_source(remote_dir, positions, narrow_prefix):
    """source(remote-local DIR) -> region/subsample/threshold (NO fields, NO
    timesteps): the shared narrowing the delta manifest re-roots onto each listed
    timestep file (per-file projection comes from the manifest)."""
    node = SourceNode(uri=remote_dir,
                      positions=tuple(positions) if positions else None)
    for nn in narrow_prefix:
        if nn.kind == "region":
            node = RegionNode(upstream=node, ranges=nn.ranges)
        elif nn.kind == "subsample":
            node = SubsampleNode(upstream=node, uniform=nn.uniform, per_axis=nn.per_axis)
        elif nn.kind == "threshold":
            node = ThresholdNode(upstream=node, var=nn.var, op=nn.op, value=nn.value)
    return node


def _folder_batch_savedir(conn, host, remote_dir, src, middle, ts_nodes,
                          out_local_dir, steps):
    """Non-catalog whole-folder batch (the fallback when there's no fields()
    projection): the remote runs its own _plan_folder over every timestep and
    saves one file per step; we pull the directory once. One remote job, one dir
    transfer, but no per-variable delta reuse."""
    terminal = _rebuild_folder_chain(remote_dir, src.positions, middle, ts_nodes)
    plan = to_plan_json(terminal)

    tag = os.urandom(4).hex()
    base = _remote_tmp()
    routdir = f"{base}/vislang_reduce_{tag}"
    requested = _srun_requested()
    jobid = _discover_jobid(conn, steps) if requested == "auto" else requested
    steps.append(f"remote folder {host}:{remote_dir}")
    steps.append("ship plan.json (folder AST moved to the remote): " + plan)

    timing.note(route="folder_batch_savedir", plan_bytes=len(plan), srun=bool(jobid))
    if jobid is None:
        cmd = _executor_cmd(routdir, folder=True)
        with timing.phase("remote_exec", srun=False):
            rc, out, err = run_remote(conn, cmd, stdin_bytes=plan.encode())
        rplan = None
    else:
        rplan = f"{base}/vislang_plan_{tag}.json"
        steps.append(f"srun --jobid={jobid}: one step for the whole folder "
                     f"(plan staged at {rplan})")
        with timing.phase("ship_plan", bytes=len(plan)):
            stc, _, ste = run_remote(conn, f"mkdir -p {base} && cat > {rplan}",
                                     stdin_bytes=plan.encode())
        if stc != 0:
            raise RuntimeError(f"staging plan to {rplan} failed: {ste.strip()[-300:]}")
        cmd = _executor_cmd(routdir, plan_path=rplan, jobid=jobid, folder=True)
        with timing.phase("remote_exec", srun=True, jobid=jobid):
            rc, out, err = run_remote(conn, cmd)

    meta = _parse_meta(out)
    if meta is None or not meta.get("ok", False):
        detail = (meta or {}).get("error") or err.strip()[-_ERR_TAIL:] or f"rc={rc}"
        raise RuntimeError(f"remote folder reduce failed: {detail}")

    remote_out = meta.get("outdir", routdir)
    with timing.phase("pull"):
        pulled = transfer_dir(conn, remote_out, out_local_dir)
    cleanup = f"rm -rf {routdir}" + (f" {rplan}" if rplan else "")
    run_remote(conn, cleanup)
    if pulled is None:
        raise RuntimeError("transfer of the reduced folder failed")

    labels = meta.get("timesteps", [])
    steps.append(f"pulled {len(labels)} timestep file(s) -> {out_local_dir}")
    report = {"n": len(labels), "host": host, "jobid": jobid, "cached": 0,
              "fetched_timesteps": len(labels), "fetched_vars": None,
              "timesteps": labels, "remote_steps": meta.get("steps", [])}
    return out_local_dir, steps, report
