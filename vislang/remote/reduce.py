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
Lustre-mounted $HOME/$PROJECT). The interpreter and repo path on the remote come
from `vislang_hosts` — per-host settings in `.vislang/hosts.json`, with
VISLANG_REMOTE_PYTHON / VISLANG_REMOTE_REPO still overriding.

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
interpreter, default "python"), VISLANG_REMOTE_REPO (remote repo dir — no
default; unset means unconfigured, and the reduce says so rather than shipping a
local path a remote shell cannot resolve), VISLANG_CACHE (catalog
root), VISLANG_NO_BINDING (force generic HDF5 names on both sides),
VISLANG_SRUN_JOBID (held allocation id, or "auto" to discover it → srun-step
mode), VISLANG_SRUN_NAME / VISLANG_SRUN_PARTITION (filter auto-discovery to this
job name / partition), VISLANG_SRUN_ARGS (extra srun flags, default
"--overlap -n1"), VISLANG_REMOTE_TMP (shared-FS staging dir for srun mode,
default "~/.vislang/reduce").
"""

import dataclasses
import json
import os

import numpy as np

import vislang.runtime.timing as timing        # per-phase seconds/bytes -> timings.jsonl
import vislang.runtime.provenance as provenance  # the derivation record -> the artifact
from vislang.formats.dataset_info import (DatasetInfo, GEOMETRY_KEY, decode_geometry,
                                          encode_geometry, geometry_from_arrays)
from vislang.remote.catalog import ExtentCatalog, make_source_id
from vislang.remote.download import (establish_connection, transfer, transfer_dir,
                         _parse_remote, remote_stat, remote_header_hash, run_remote,
                         measure_bandwidth, host_reachable, connect_command)
from vislang.dsl.ast_serialize import to_plan_json
from vislang.dsl.nodes import (SourceNode, FieldsNode, RegionNode, SubsampleNode,
                             ThresholdNode)

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


class SessionHold(Exception):
    """The remote needs an interactive login and no session is live, so nothing
    can run next to the data. Carries the reason, the `connect` command to
    propose, and the steps so far, so the planner reports NEEDS SESSION without
    reading anything.

    Deliberately NOT a RemoteUnavailable: that means "fall back to the whole-file
    fetch", and the fetch path is equally unauthenticated. Falling back here
    would trade a clear, fixable hold for a second failure further along, after
    the cost gate has already approved moving the whole file."""

    def __init__(self, reason, connect_cmd, steps, reachable=True, host=None,
                 target=None):
        super().__init__("no session — remote work held pending connect")
        self.reason = reason
        self.connect_cmd = connect_cmd
        self.steps = steps
        self.reachable = reachable      # False -> a network problem, not a credential one
        self.host = host
        self.target = target


def _session_hold(conn, steps):
    """The SessionHold for a host we cannot drive non-interactively.

    A BatchMode probe fails identically whether the host is unreachable or
    merely unauthenticated, and the two need opposite fixes — so ask the network
    which one it is rather than answering an unplugged VPN with a password
    dialog that cannot possibly help."""
    reachable = host_reachable(conn.target)
    if not reachable:
        reason = (f"{conn.host} is not reachable at all — this is a network or "
                  f"VPN problem, not a credential one")
    else:
        reason = (f"{conn.host} answers but needs an interactive login, and no "
                  f"VisLang session is open for it")
    return SessionHold(reason, connect_command(conn.target), steps,
                       reachable=reachable, host=conn.host, target=conn.target)


def session_check(uri):
    """SessionHold for `uri`'s host when it cannot be driven non-interactively,
    else None.

    The single place that answers "is there a session?", so the planner's gate,
    the reduce's own re-check and `inspect`'s handshake all agree — and all
    resolve the connection through this module's `establish_connection`, the seam
    the tests already substitute."""
    try:
        conn = establish_connection(_normalize_remote(uri))
    except Exception:
        return None            # not a session problem; the caller reports its own
    return None if conn.batch_ok else _session_hold(conn, [])


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


def _remote_tmp(host=None):
    """Base dir on the remote for the staged plan + reduced .npz. srun places
    vislang_exec on a COMPUTE node whose local /tmp the login-node pull can't
    see, so srun mode needs a shared-FS dir; direct-ssh mode keeps /tmp (the
    reducer runs on the same node ssh landed on)."""
    from vislang.remote.hosts import remote_tmp
    if _srun_requested():
        return remote_tmp(host, default="~/.vislang/reduce")
    return remote_tmp(host, default="/tmp")


def _executor_cmd(rout, plan_path=None, jobid=None, folder=False,
                  manifest_path=None, host=None):
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
    from vislang.remote.hosts import remote_python, remote_repo
    py = remote_python(host)
    repo = remote_repo(host)
    if not repo:
        raise RemoteUnavailable(
            f"no VisLang checkout configured for {host or 'this host'} — set "
            f"`repo` for it in .vislang/hosts.json (or VISLANG_REMOTE_REPO). "
            f"`sieve connect <host>` reports what is missing.")
    flag = "--outdir" if folder else "--out"
    man = f" --manifest {manifest_path}" if manifest_path else ""   # catalog delta
    # The run's sampling seed travels too, so a random subsample drawn next to
    # the data is the one the local record names — not one the remote process
    # picked for itself.
    from vislang.interpreter.narrowing import sampling_seed
    fwd = {"VISLANG_SAMPLE_SEED": str(sampling_seed())}
    if os.environ.get("VISLANG_NO_BINDING"):
        fwd["VISLANG_NO_BINDING"] = "1"
    if jobid is None:
        env = "".join(f"{k}={v} " for k, v in fwd.items())
        return f"{env}{py} {repo}/vislang_exec.py --stdin {flag} {rout}{man}"
    # srun mode: run as a step in the held allocation, plan from a staged file.
    srun_args = os.environ.get("VISLANG_SRUN_ARGS", "--overlap -n1")
    export = ",".join(["ALL"] + [f"{k}={v}" for k, v in fwd.items()])
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
def _narrow_key(middle, label=None):
    """A JSON-safe, order-faithful description of the narrowing (minus fields —
    projection is the catalog's variable axis, not part of the key). Written
    order is part of the key because threshold/subsample do not commute.

    A per-timestep region keys on THIS timestep's centre and size (`label`), so
    two timesteps — or two tracks — never share an extent. The `region_at` form
    is not a plain crop, so the catalog reuses it on an exact match only."""
    forms = []
    for n in middle:
        if n.kind == "region" and n.per_step:
            from vislang.interpreter.track import box_at
            at = box_at(n, label)
            if at is None:
                raise ValueError(f"region: no centre for timestep #{label}")
            forms.append(["region_at", list(at[0]), list(at[1])])
        elif n.kind == "region":
            forms.append(["region", [[a, lo, hi] for a, lo, hi in n.ranges]])
        elif n.kind == "subsample":
            forms.append(["subsample", n.uniform,
                          [[a, f] for a, f in (n.per_axis or ())], n.method])
        elif n.kind == "threshold":
            forms.append(["threshold", n.var, n.op, n.value])
    return {"forms": forms}


def delta_key(label, tag, var):
    """The key of one fetched array in a delta bundle: `<label>/<var>` for a
    folder (the original form), `<label>/<tag>/<var>` for a named box, with `-`
    for the label of a box on a single file. Tags cannot hold `/`; a variable
    name may, which is why it comes last and the split is bounded."""
    if tag is None:
        return f"{label}/{var}"
    return f"{'-' if label is None else label}/{tag}/{var}"


def split_delta_key(key, tagged):
    """(label, tag, var) from a delta_key."""
    if not tagged:
        label, var = key.split("/", 1)
        return int(label), None, var
    label, tag, var = key.split("/", 2)
    return (None if label == "-" else int(label)), tag, var


def _stale_remote_hint(detail):
    """A remote checkout older than named boxes rejects their plan or manifest;
    say how to fix that rather than leaving a validator message to decode."""
    text = str(detail)
    if "unknown ['tags']" in text or "has no attribute 'items'" in text:
        return (f"{text} — the remote checkout predates named boxes "
                f"(region(center={{tag: …}})); update it: git pull in "
                f"VISLANG_REMOTE_REPO")
    return text


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
            node = dataclasses.replace(n, upstream=node)
        elif n.kind == "subsample":
            node = SubsampleNode(upstream=node, uniform=n.uniform, per_axis=n.per_axis,
                                 method=n.method)
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


# Where a cut sits in space comes back with its data. For a grid it is requested,
# cached and reused under GEOMETRY_KEY exactly like a variable — so a re-run
# reuses it, and a cache filled before it existed fetches just that piece. Point
# data carries its positions in its own columns and needs none.
def _wants_geometry(schema):
    dims = (schema or {}).get("dimensions") or {}
    return "grid" in dims and not (schema or {}).get("positions")


def _with_geometry(variables, schema):
    """The catalog request: `variables` plus the geometry piece for a grid —
    or for a source whose modality is not known yet (no schema): the reducer
    answers it only for a grid, so asking costs a point set nothing."""
    if variables is None or (schema is not None and not _wants_geometry(schema)):
        return variables
    return list(variables) + [GEOMETRY_KEY]


def _data_vars(names):
    """`names` without the geometry piece (None stays None)."""
    return None if names is None else [v for v in names if v != GEOMETRY_KEY]


def _geometry_in(*mappings):
    """The geometry held under GEOMETRY_KEY by the first mapping that has it."""
    for m in mappings:
        if GEOMETRY_KEY in m:
            return decode_geometry(m[GEOMETRY_KEY])
    return None


# ---------------------------------------------------------------------------
def _catalog_schema(schema):
    """The persistable part of a schema. `itemsizes` is live inspect metadata and
    is deliberately NOT stored in the catalog (see DatasetInfo.itemsizes)."""
    return {k: v for k, v in (schema or {}).items() if k != "itemsizes"}


def _keep_attributes(schema, known):
    """`schema` (a reducer's report) with `known`'s attributes filled in when it
    has none. A remote checkout older than this build reports its schema without
    `attributes`; storing that over the login-node schema would erase header
    facts (GenericIO's phys_scale/dtypes) that a format-preserving save needs."""
    if not schema or schema.get("attributes") or not (known or {}).get("attributes"):
        return schema
    return {**schema, "attributes": known["attributes"]}


def _prime_schema(conn, remote_path, catalog, sid, steps):
    """Fetch the schema from the LOGIN NODE (metadata only, no allocation, no
    bulk read) and store it, so the very first run of a file can size its own
    transfer. Without this the cost gate is fed by the catalog, which is only
    written AFTER a reduce — i.e. the first run, the one you cannot predict,
    would ship ungated. Returns the schema dict (with live `itemsizes`) or None;
    any failure is non-fatal — the caller proceeds as before."""
    try:
        from vislang.formats.inspect import _run_remote_inspect
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
    from vislang.interpreter.narrowing import RowMask, RowSample
    return any(isinstance(op, (RowMask, RowSample))
               for op in (getattr(narrowing, "post_ops", ()) or ()))


def _info_from_schema(uri, schema):
    """A DatasetInfo standing in for the remote source, built from the schema the
    remote itself reported. Not a guess: `_prime_schema` obtained it by running
    the inspect next to the data (same binding mode the reducer will use) and the
    catalog stored it under a size+mtime+header identity, so if the file changed
    the identity changed and the schema was re-read."""
    from vislang.formats.inspect import _dims_from_json
    info = DatasetInfo(uri, (schema or {}).get("filetype", "remote"),
                       list((schema or {}).get("variables") or []),
                       dimensions=_dims_from_json((schema or {}).get("dimensions") or {}),
                       attributes=dict((schema or {}).get("attributes") or {}),
                       itemsizes=dict((schema or {}).get("itemsizes") or {}))
    info.positions = (tuple(schema["positions"])
                      if (schema or {}).get("positions") else None)
    return info


def _check_before_shipping(src, prefix, schema, steps, label=None):
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
    from vislang.interpreter.planner import _lower
    info = _info_from_schema(src.uri, schema)
    narrowing, _pending = _lower(info, prefix, [], label=label)   # steps discarded; see below
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
    from vislang.interpreter.estimate import estimate_plan_cost, format_plan_estimate
    from vislang.formats.inspect import _dims_from_json
    from vislang.interpreter.narrowing import Narrowing, AxisRange

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
        from vislang.remote.catalog import _grid_ranges_of
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


def _note_remote_provenance(schema, prefix, have, fetched, missing, fetched_bytes=None):
    """Tell the record what the remote read was and how each column arrived:
    from the local extent catalog, or read next to the data. The read lists
    every column the remote touched — a threshold reads its variable even when
    the output drops it — and the forms it ran there, in written order.
    `fetched_bytes` overrides the byte count (named boxes fetch a column once
    per box)."""
    if not provenance.active():
        return
    provenance.note_source(columns=(schema or {}).get("variables"),
                           filetype=(schema or {}).get("filetype"))
    groups = {}
    if have:
        groups["cache"] = {"columns": list(have)}
    if fetched:
        read_cols = list(missing or fetched)
        for n in prefix:
            if n.kind == "threshold" and n.var not in read_cols:
                read_cols.append(n.var)
        groups["remote"] = {
            "columns": list(fetched),
            "read": {"columns": read_cols,
                     "then": [n.kind for n in prefix if n.kind != "fields"]},
            "fetched_bytes": int(fetched_bytes if fetched_bytes is not None
                                 else sum(a.nbytes for a in fetched.values()))}
    provenance.note_columns_from(groups)


def _probe(src, steps):
    """Connect to the source's host and identify the file: (conn, host,
    remote_path, size, sid). Raises SessionHold / RemoteUnavailable."""
    norm = _normalize_remote(src.uri)
    _, host, remote_path = _parse_remote(norm)
    with timing.phase("probe_source"):
        conn = establish_connection(norm)
        if not conn.batch_ok:
            raise _session_hold(conn, steps)

        st = remote_stat(conn, remote_path)
        if st is None:
            raise RemoteUnavailable(f"cannot stat {remote_path} on {host}")
        size, mtime = st
        header = remote_header_hash(conn, remote_path) or ""
        sid = make_source_id(norm, size, mtime, header)
    # The same three facts the catalog keys on, recorded as the input's
    # fingerprint so an output can say exactly which bytes it came from.
    provenance.note_source(site="remote",
                           fingerprint=provenance.fingerprint(size, mtime, header))
    # source_bytes is the denominator of the reduction factor: how much a
    # whole-file fetch would have moved, against what actually crossed.
    timing.note(host=host, source_bytes=size, source_id=sid[:8])
    steps.append(f"remote source {host}:{remote_path} ({size / 1e6:.1f} MB, id={sid[:8]})")
    return conn, host, remote_path, size, sid


def _allocation_gate(conn, host, steps, confirm):
    """In srun auto mode, HOLD (and propose salloc) when no allocation is held —
    never create one here. A FIXED VISLANG_SRUN_JOBID is trusted as-is."""
    if _srun_requested() == "auto" and not confirm:
        with timing.phase("allocation_probe"):
            alloc = allocation_status(conn)
        if alloc.get("present") is False:      # definitively absent (not unknown)
            steps.append(f"allocation: {alloc['reason']}")
            raise AllocationHold(alloc["reason"], recommended_salloc(host), steps)


def _remote_info(src, schema, variables, data, extra_attrs):
    """The loaded DatasetInfo for the local suffix, from a remote schema and
    assembled arrays. The source's own attributes ride along: a
    format-preserving save needs them (GenericIO's phys_scale/phys_origin live
    there), and dropping them would make a remote round-trip silently lossier
    than a local one."""
    info = DatasetInfo(src.uri, schema.get("filetype", "remote"), variables,
                       dimensions=schema.get("dimensions") or {},
                       attributes={**(schema.get("attributes") or {}),
                                   "remote_reduced": True, **extra_attrs})
    info.positions = tuple(schema["positions"]) if schema.get("positions") else None
    info.data = {v: data[v] for v in data}
    info.loaded = True
    return info


def remote_reduce_tags(src, middle, confirm=False):
    """Named boxes on ONE remote file (region(center={tag: (x, y[, z])})).

    remote_reduce, per box: each tag is its own catalog extent (keyed by its own
    centre and size, not its name — renaming a tag still reuses it), the cost
    gate prices every box still missing, and ONE remote job cuts all of them
    (the executor reads the file once where the format allows), returning one
    bundle. Returns ([(tag, loaded)], steps, CostEstimate|None)."""
    from vislang.interpreter.estimate import (estimate_boxes_cost, estimate_plan_cost,
                                              format_plan_estimate)
    from vislang.interpreter.track import tagged_region, tag_names, for_tag
    from vislang.runtime.paths import cache_root
    steps = []
    prefix, _compresses = _split_middle(middle)
    tagged = tagged_region(prefix)
    tags = tag_names(tagged)
    prefixes = {t: for_tag(prefix, t) for t in tags}
    narrow_prefix = [n for n in prefix if n.kind != "fields"]
    conn, host, remote_path, size, sid = _probe(src, steps)
    timing.note(tags=len(tags), boxes=len(tags))

    catalog = ExtentCatalog(cache_root())
    keys = {t: _narrow_key(prefixes[t]) for t in tags}
    project = _projection_of(prefix)
    schema = catalog.schema(sid)
    timing.note(schema_cached=schema is not None)
    if schema is None:
        with timing.phase("login_node_inspect"):
            schema = _prime_schema(conn, remote_path, catalog, sid, steps)
    want = project if project is not None else (
        list(schema["variables"]) if schema else None)
    have, missing = {}, {}
    with timing.phase("catalog_delta"):
        for t in tags:
            have[t], missing[t] = ({}, None) if want is None else \
                catalog.delta(sid, _with_geometry(want, schema), keys[t])
    need = [t for t in tags if missing[t] is None or missing[t]]
    held = [t for t in tags if _data_vars(have[t])]
    if held:
        steps.append(f"catalog: cached extents for box(es) {', '.join(held)}")

    # Static check of every box BEFORE anything ships (see remote_reduce).
    with timing.phase("static_check"):
        if schema is not None:
            from vislang.interpreter.planner import _check_boxes
            _check_boxes(_info_from_schema(src.uri, schema), prefixes, {None: tags})
        checked = {t: _check_before_shipping(src, prefixes[t], schema,
                                             steps if i == 0 else [])
                   for i, t in enumerate(tags)}
    timing.note(pre_validated=all(v is not None for v in checked.values()))

    estimate = None
    if need:
        with timing.phase("cost_estimate"):
            try:
                net_bw = measure_bandwidth(conn)
            except Exception:
                net_bw = None
            if schema is not None and all(checked[t] is not None for t in need):
                estimate = estimate_boxes_cost(
                    info=_info_from_schema(src.uri, schema), site="remote",
                    boxes=[(checked[t], 1, _data_vars(missing[t])
                            or list(schema["variables"])) for t in need],
                    net_bw_bps=net_bw)
                if any(_row_cutting(checked[t]) for t in need):
                    estimate.notes.append(
                        "value-dependent row cut — the count survives only after "
                        "the read, so this prices the pre-cut selection: an UPPER "
                        "bound.")
            else:
                estimate = estimate_plan_cost(info=DatasetInfo(src.uri, "remote", []),
                                              narrowing=None, site="remote",
                                              net_bw_bps=net_bw)
                estimate.notes.append("schema unavailable (login-node inspect failed) "
                                      "— reduced size not estimated; not gated.")
            steps.append(format_plan_estimate(estimate))
        if estimate.over_budget and not confirm:
            raise BudgetHold(estimate, steps)
        _allocation_gate(conn, host, steps, confirm)
    else:
        timing.note(route="catalog_full_hit")

    fetched = {t: {} for t in tags}
    total = 0
    if need:
        manifest = [{"label": None, "tag": t, "vars": missing[t]} for t in need]
        steps.append(f"remote file {host}:{remote_path}: one job cuts "
                     f"{len(need)} named box(es) ({', '.join(need)})")
        meta, bundle, _ = _run_delta_job(
            conn, _rebuild_delta_source(remote_path, src.positions, narrow_prefix),
            manifest, steps, what=f"{len(need)} named box(es)", n_timesteps=1)
        schema = _keep_attributes(meta.get("schema"), schema)
        catalog.store_schema(sid, _catalog_schema(schema))
        for k, arr in bundle.items():
            _, t, var = split_delta_key(k, True)
            fetched[t][var] = arr
            total += arr.nbytes
            catalog.store(sid, var, keys[t], arr)
        n_geom = sum(1 for k in bundle if k.endswith("/" + GEOMETRY_KEY))
        steps.append(f"pulled {len(bundle) - n_geom} (box,var) array(s)"
                     + (f" + {n_geom} grid position piece(s)" if n_geom else "")
                     + f", {total / 1e6:.1f} MB over the wire")
    else:
        steps.append("catalog: full hit — nothing crossed the wire")
    if schema is None:
        raise RemoteUnavailable("no schema for cached-only assembly")   # defensive

    from vislang.interpreter.planner import _boxes_at
    info_s = _info_from_schema(src.uri, schema)
    cut = []
    for t in tags:
        geometry = _geometry_in(fetched[t], have[t])
        data = {v: a for v, a in {**have[t], **fetched[t]}.items() if v != GEOMETRY_KEY}
        order = want if want is not None else list(data)
        variables = [v for v in order if v in data]
        loaded = _remote_info(src, schema, variables, data, {"source_id": sid})
        loaded.geometry = geometry
        loaded.selection_info = {"variables_loaded": variables,
                                 "dimension_selection": keys[t], "site": "remote"}
        notes = []
        _, boxes = _boxes_at(info_s, prefixes[t], None, notes, tag=t)
        steps += notes
        provenance.note_region_box(None, boxes, tag=t)
        cut.append((t, loaded))
    timing.note(result_bytes=int(sum(a.nbytes for _, l in cut for a in l.data.values())),
                fetched_vars=len({v for f in fetched.values() for v in f
                                  if v != GEOMETRY_KEY}))
    _note_remote_provenance(
        schema, prefix,
        {v: a for t in tags for v, a in have[t].items() if v != GEOMETRY_KEY},
        {v: a for t in tags for v, a in fetched[t].items() if v != GEOMETRY_KEY},
        sorted({v for t in need for v in (_data_vars(missing[t]) or [])}),
        fetched_bytes=total)
    steps.append(f"assembled {len(cut)} named box(es) "
                 f"({len(held)} with cached extents, {len(need)} fetched)")
    return cut, steps, estimate


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
    conn, host, remote_path, size, sid = _probe(src, steps)

    # --- catalog delta: fetch only what we don't already hold -----------------
    from vislang.runtime.paths import cache_root
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

    have, missing = {}, _with_geometry(want, schema)
    if want is not None:
        with timing.phase("catalog_delta"):
            have, missing = catalog.delta(sid, _with_geometry(want, schema), key)
        # want / have / missing counted in VARIABLES here (the single-file axis);
        # the folder path counts (timestep, variable) pairs.
        timing.note(want_vars=len(want), cached_vars=len(_data_vars(have)),
                    missing_vars=len(_data_vars(missing) or []),
                    cached_bytes=int(sum(a.nbytes for a in have.values())) if have else 0)
        if _data_vars(have):
            steps.append(f"catalog: cached {sorted(_data_vars(have))} for this narrowing")

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
            estimate = _reduce_estimate(src, key, schema, _data_vars(missing), conn,
                                        steps, narrowing=checked)
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
    if want is None or missing:
        _allocation_gate(conn, host, steps, confirm)

    fetched = {}
    if want is None or missing:
        # Only the geometry missing (a cache filled before it was kept): the
        # reducer still needs one column to cut, and it comes back too.
        fetch = None if missing is None else (_data_vars(missing) or want[:1])
        meta, pulled = _run_remote_prefix(conn, remote_path, src, prefix, fetch, steps)
        schema = _keep_attributes(meta["schema"], schema)
        catalog.store_schema(sid, _catalog_schema(schema))
        # The reducer's .npz holds the cut's geometry under reserved keys.
        fetched = {k: a for k, a in pulled.items() if not k.startswith("sieve_")}
        for var, arr in fetched.items():
            catalog.store(sid, var, key, arr)
        if _wants_geometry(schema):
            fetched_geom = encode_geometry(geometry_from_arrays(pulled))
            catalog.store(sid, GEOMETRY_KEY, key, fetched_geom)
            have = {**have, GEOMETRY_KEY: fetched_geom}
    else:
        steps.append("catalog: full hit — nothing crossed the wire")

    if schema is None:
        raise RemoteUnavailable("no schema for cached-only assembly")   # defensive

    # --- assemble the loaded DatasetInfo for the local suffix -----------------
    geometry = _geometry_in(have)
    data = {v: a for v, a in have.items() if v != GEOMETRY_KEY}
    data.update(fetched)
    order = want if want is not None else list(data)
    variables = [v for v in order if v in data]
    # The source's own attributes ride along: a format-preserving save needs them
    # (GenericIO's phys_scale/phys_origin live there), and dropping them would
    # make a remote round-trip silently lossier than a local one.
    info = DatasetInfo(src.uri, schema.get("filetype", "remote"), variables,
                       dimensions=schema.get("dimensions") or {},
                       attributes={**(schema.get("attributes") or {}),
                                   "remote_reduced": True, "source_id": sid})
    info.positions = tuple(schema["positions"]) if schema.get("positions") else None
    info.data = {v: data[v] for v in data}
    info.loaded = True
    info.geometry = geometry
    info.selection_info = {"variables_loaded": variables,
                           "dimension_selection": key,
                           "site": "remote"}
    total = sum(a.nbytes for a in data.values())
    timing.note(result_bytes=int(total), fetched_vars=len(fetched))
    _note_remote_provenance(schema, prefix,
                            {v: a for v, a in have.items() if v != GEOMETRY_KEY},
                            fetched, _data_vars(missing))
    steps.append(f"assembled {len(data)} var(s), {total / 1e6:.1f} MB "
                 f"({len(_data_vars(have))} cached, {len(fetched)} fetched)")
    return info, steps, estimate


def _run_remote_prefix(conn, remote_path, src, prefix, missing, steps):
    """Serialize the (missing-variables) prefix, run vislang_exec on the remote,
    pull the reduced npz, and return (meta, {var: array})."""
    terminal = _rebuild_prefix(remote_path, src.positions, prefix, missing)
    plan = to_plan_json(terminal)

    tag = os.urandom(4).hex()
    base = _remote_tmp(conn.host)
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
        cmd = _executor_cmd(rout, host=conn.host)                       # plan piped via stdin
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
        cmd = _executor_cmd(rout, plan_path=rplan, jobid=jobid, host=conn.host)
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

    from vislang.runtime.paths import cache_root
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
    n_data = sum(1 for k in arrays if not k.startswith("sieve_"))
    steps.append(f"pulled {n_data} var(s)"
                 + (" + the grid's positions" if n_data < len(arrays) else "")
                 + f", {total / 1e6:.1f} MB over the wire")
    return meta, arrays


def _parse_meta(stdout_text):
    """The meta JSON vislang_exec prints between sentinel lines."""
    try:
        chunk = stdout_text.split(META_BEGIN, 1)[1].split(META_END, 1)[0]
        return json.loads(chunk.strip())
    except (IndexError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# Whole-folder (timeseries) reduce: ONE remote job, ONE bundle pulled back
# ---------------------------------------------------------------------------
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

    Named boxes (region(center={tag: [(N, …), …]})) make the unit (timestep, tag):
    each box is its own extent, keyed by its own centre and size; the manifest
    lists the missing (timestep, tag, variables) entries; each tag is written as
    its own series under the sink path.

    With no fields() projection the request is every variable of the series —
    read from timestep 0's schema, which stands for the series (sharing one
    schema is what makes a folder a series). Either way the remote only cuts and
    returns arrays; the files are written here, by the same save() as every
    other route, so each carries its provenance record. A grid's geometry (its
    origin + spacing, or every point's position) is fetched and cached with it.

    Returns (local_out_dir, steps, report). Raises RemoteUnavailable (the
    planner falls back to a whole-folder fetch) or RuntimeError for a real
    failure."""
    from vislang.remote.catalog import make_source_id
    from vislang.runtime.paths import cache_root
    from vislang.interpreter.compress import compress as _compress
    from vislang.interpreter.track import (check_coverage, tagged_region, tag_plan,
                                           tag_names, for_tag)

    steps = []
    prefix, compresses = _split_middle(middle)          # narrowing (incl fields) + compress
    project = _projection_of(prefix)                    # requested vars, or None
    narrow_prefix = [n for n in prefix if n.kind != "fields"]   # region/subsample/threshold
    moving = [n for n in prefix if n.kind == "region" and n.per_step]
    tagged = tagged_region(prefix)

    norm = _normalize_remote(src.uri)
    _, host, remote_dir = _parse_remote(norm)
    with timing.phase("probe_source"):
        conn = establish_connection(norm)
        if not conn.batch_ok:
            raise _session_hold(conn, steps)
    timing.note(host=host)

    from vislang.formats.inspect import remote_timestep_files_stat
    with timing.phase("list_timesteps"):
        files = remote_timestep_files_stat(src.uri)     # [(label, uri, size, mtime)]
    if files is None:
        raise RemoteUnavailable(f"cannot list remote folder {src.uri!r}")
    available = [f[0] for f in files]
    if ts_nodes:
        lo, hi = max(n.start for n in ts_nodes), min(n.stop for n in ts_nodes)
        files = [f for f in files if lo <= f[0] <= hi]
    if not files:
        raise ValueError(f"no timesteps match in {src.uri}")
    if tagged is not None:
        plan, skipped = tag_plan(tagged, [f[0] for f in files], available)
        files = [f for f in files if f[0] in plan]
        if moving:                  # an unnamed moving box too: every file read
            check_coverage(prefix, list(plan), available)
        tags = [t for t in tag_names(tagged) if t not in skipped]
        prefixes = {t: for_tag(prefix, t) for t in tags}
        steps += [f"tag {t!r}: no box in the selected timesteps — nothing written "
                  f"for it" for t in skipped]
    else:
        if moving:
            check_coverage(prefix, [f[0] for f in files], available)
        plan = {f[0]: [None] for f in files}
        tags, prefixes = [None], {None: prefix}
    # The unit of work: one per timestep, or one per (timestep, named box).
    units = [(label, t) for label in plan for t in plan[label]]
    # Ordered, fields-independent; one per timestep (and box) when the region moves.
    keys = {(label, t): _narrow_key(prefixes[t], label) for label, t in units}
    unit = "(timestep,variable)" if tagged is None else "(box,variable)"
    timing.note(source_bytes=int(sum(f[2] for f in files)),
                selected_timesteps=len(files))
    if tagged is not None:
        timing.note(tags=len(tags), boxes=len(units))

    catalog = ExtentCatalog(cache_root())
    per = {label: {"uri": uri, "sid": make_source_id(uri, size, mtime)}
           for label, uri, size, mtime in files}       # label -> {uri, sid}

    # --- timestep 0's schema stands for the series ----------------------------
    # Needed before the delta: it is the variable list when there is no
    # fields(), and it says whether this is a grid (whose geometry is fetched
    # too). Free once the catalog holds it; otherwise one login-node inspect,
    # which the run would have spent anyway. An outdated entry still answers
    # "is it a grid"; only a missing variable list makes the inspect necessary.
    label0 = files[0][0]
    schema0 = catalog.schema(per[label0]["sid"])
    known = schema0 or catalog.stale_schema(per[label0]["sid"])
    if schema0 is None and (project is None or known is None):
        _, _, ts0_path = _parse_remote(_normalize_remote(per[label0]["uri"]))
        with timing.phase("login_node_inspect"):
            schema0 = _prime_schema(conn, ts0_path, catalog, per[label0]["sid"], steps)
        known = schema0 or known
    if project is None:
        if not (schema0 or {}).get("variables"):
            raise RemoteUnavailable(
                f"no fields() given and the variables of {src.uri} could not be "
                f"read (login-node inspect of timestep #{label0} failed)")
        project = list(schema0["variables"])
        steps.append(f"no fields() — every variable of the series: {project}")
    request = _with_geometry(project, known)

    # --- catalog delta per timestep (local, no network) ----------------------
    have, missing = {}, {}                              # unit -> cached {var: arr} / [vars]
    with timing.phase("catalog_delta", n=len(units)):
        for label in plan:
            for t in plan[label]:
                h, m = catalog.delta(per[label]["sid"], request, keys[(label, t)])
                have[(label, t)] = h
                if m:
                    missing[(label, t)] = m
    if tagged is None:
        # The folder form {label: [missing vars]}, which every executor reads.
        manifest = {str(label): m for (label, _), m in missing.items()}
    else:
        manifest = [{"label": label, "tag": t, "vars": m}
                    for (label, t), m in missing.items()]
    fetch_labels = sorted({label for label, _ in missing})

    if provenance.active():
        provenance.note_source(site="remote")
        provenance.note_timesteps([
            {"label": label, "uri": uri, "fingerprint": provenance.fingerprint(size, mtime)}
            for label, uri, size, mtime in files])

    n = len(files)
    # Counted in data variables; the geometry piece rides along uncounted.
    union_missing = sorted({v for vs in missing.values() for v in _data_vars(vs)})
    total_pairs = len(units) * len(project)             # (unit, variable) extents needed
    fetched_pairs = sum(len(_data_vars(vs)) for vs in missing.values())
    reused_pairs = total_pairs - fetched_pairs
    geom_only = sum(1 for vs in missing.values() if not _data_vars(vs))
    steps.append(f"catalog: reused {reused_pairs}/{total_pairs} {unit} "
                 f"extent(s)"
                 + (f"; fetching {union_missing} ({fetched_pairs} pair(s)) for "
                    f"{len(fetch_labels)} timestep(s)" if fetched_pairs
                    else " — full hit, nothing crosses the wire" if not missing
                    else "")
                 + (f"; fetching the grid position(s) only for {geom_only} "
                    f"{'box' if tagged is not None else 'timestep'}(s)"
                    if geom_only else ""))

    # --- static check BEFORE the plan ships -----------------------------------
    # Same reasoning as the single-file path: a malformed request should not cost
    # a scheduler step. Timestep 0's schema stands for the series — sharing one
    # schema is what makes a folder a series. Free once the catalog holds it; one
    # login-node inspect on the very first sighting, which the run would have
    # spent anyway (the reduce stores the schema when it returns).
    if schema0 is None and missing:
        _, _, ts0_path = _parse_remote(_normalize_remote(per[label0]["uri"]))
        with timing.phase("login_node_inspect"):
            schema0 = _prime_schema(conn, ts0_path, catalog, per[label0]["sid"], steps)
    with timing.phase("static_check"):
        firsts = {}
        for label, t in units:
            firsts.setdefault(t, label)
        if schema0 is not None and tagged is not None:
            # Only each tag's first box: schema0 is timestep 0's, and the rest
            # are cut on the remote against their own files.
            from vislang.interpreter.planner import _check_boxes, _first_boxes
            _check_boxes(_info_from_schema(src.uri, schema0), prefixes,
                         _first_boxes(firsts))
        for i, t in enumerate(tags):                    # one box per tag stands for it
            _check_before_shipping(src, prefixes[t], schema0, steps if i == 0 else [],
                                   label=firsts[t])
    timing.note(pre_validated=schema0 is not None)

    # --- fetch only the missing (timestep, variable) pairs, in ONE remote job -
    fetched = {}                                        # (label, tag, var) -> array
    schema = None
    jobid = None
    if missing:
        steps.append(f"remote folder {host}:{remote_dir} (catalog delta)")
        meta, bundle, jobid = _run_delta_job(
            conn, _rebuild_delta_source(remote_dir, src.positions, narrow_prefix),
            manifest, steps, what=f"delta {union_missing}", n_timesteps=len(fetch_labels))
        schema = _keep_attributes(meta.get("schema"), schema0)
        total = 0
        for k, arr in bundle.items():                   # keys from delta_key
            label, t, var = split_delta_key(k, tagged is not None)
            fetched[(label, t, var)] = arr
            total += arr.nbytes
            catalog.store(per[label]["sid"], var, keys[(label, t)], arr)  # cache fresh
        for label in fetch_labels:
            catalog.store_schema(per[label]["sid"], schema)
        n_geom = sum(1 for (_, _, v) in fetched if v == GEOMETRY_KEY)
        steps.append(f"pulled delta: {len(fetched) - n_geom} "
                     f"{unit.replace('variable', 'var')} array(s)"
                     + (f" + {n_geom} grid position piece(s)" if n_geom else "")
                     + f", {total / 1e6:.2f} MB over the wire")

    # --- assemble each timestep from cached + fetched, then save -------------
    if schema is None:                                  # fully cached: schema from catalog
        for label in per:
            schema = catalog.schema(per[label]["sid"])
            if schema:
                break
    if schema is None:
        # Nothing current. There is no connection open here to re-inspect with,
        # so fall back to an outdated entry rather than failing a run whose data
        # is already on local disk — and say that it is outdated, because the
        # fields it lacks (GenericIO's phys_scale) would otherwise look like
        # facts about the source instead of gaps in the cache.
        for label in per:
            schema = catalog.stale_schema(per[label]["sid"])
            if schema:
                steps.append("NOTE: cached schema predates this build "
                             "(no attributes); a format-preserving save may "
                             "degrade. Re-run with the source reachable to refresh.")
                break
    if schema is None:
        raise RemoteUnavailable("no cached schema for a fully-cached folder")

    per_tag = {t: [] for t in tags}
    for label, t in units:
        data = {}
        for var in project:
            if var in have[(label, t)]:
                data[var] = have[(label, t)][var]
            else:
                data[var] = fetched[(label, t, var)]
        geom = have[(label, t)].get(GEOMETRY_KEY)
        if geom is None:
            geom = fetched.get((label, t, GEOMETRY_KEY))
        loaded = DatasetInfo(f"{src.uri}#{label}", schema.get("filetype", "remote"),
                             list(project), dimensions=schema.get("dimensions") or {},
                             attributes={**(schema.get("attributes") or {}),
                                         "remote_reduced": True})
        loaded.positions = tuple(schema["positions"]) if schema.get("positions") else None
        loaded.data = data
        loaded.loaded = True
        loaded.geometry = None if geom is None else decode_geometry(geom)
        for c in compresses:                            # compress is a LOCAL suffix (lossy; keep extents raw)
            loaded = _compress(loaded, list(c.variables), c.error_bound, c.mode)
        per_tag[t].append((label, loaded))

    if moving or tagged is not None:
        # The box each timestep got, from the same routine the remote used to
        # cut it (one schema stands for the series, as in the static check).
        from vislang.interpreter.planner import _boxes_at
        info_s = _info_from_schema(src.uri, schema)
        for label, t in units:
            clip_notes = []
            _, boxes = _boxes_at(info_s, prefixes[t], label, clip_notes, tag=t)
            steps += clip_notes
            provenance.note_region_box(label, boxes, tag=t)
    if provenance.active():
        provenance.note_source(columns=schema.get("variables"),
                               filetype=schema.get("filetype"))
        groups = {}
        if reused_pairs:
            groups["cache"] = {"columns": list(project),
                               "extents_reused": f"{reused_pairs} of {total_pairs}"}
        if union_missing:
            groups["remote"] = {
                "columns": union_missing,
                "read": {"then": [n.kind for n in narrow_prefix]},
                "fetched_bytes": int(sum(a.nbytes for (_, _, v), a in fetched.items()
                                         if v != GEOMETRY_KEY))}
        provenance.note_columns_from(groups)

    from vislang.output.save import save_timeseries, save_tagged_series
    report = {"n": n, "host": host, "jobid": jobid, "cached": n - len(fetch_labels),
              "fetched_timesteps": len(fetch_labels), "fetched_vars": union_missing,
              "total_pairs": total_pairs, "reused_pairs": reused_pairs,
              "fetched_pairs": fetched_pairs,
              "fetched_geometry": sum(1 for (_, _, v) in fetched if v == GEOMETRY_KEY),
              "timesteps": [lab for lab, *_ in files]}
    if tagged is None:
        per_step = per_tag[None]
        with timing.phase("sink_save_timeseries", n=len(per_step)):
            out = save_timeseries(per_step, out_local_dir, schema.get("filetype"))
        steps.append(f"-> save timeseries -> {out}")
    else:
        with timing.phase("sink_save_timeseries", n=len(units)):
            out = save_tagged_series([(t, per_tag[t]) for t in tags], out_local_dir,
                                     schema.get("filetype"))
        steps.append(f"-> save one series per tag -> {out}/<tag>/")
        report["tags"] = {t: [lab for lab, _ in per_tag[t]] for t in tags}
    return out, steps, report


def _run_delta_job(conn, terminal, manifest, steps, what, n_timesteps):
    """Ship the narrowing (`terminal`) plus a delta manifest, run ONE remote job
    (vislang_exec --outdir --manifest) over it, pull the bundle it writes, and
    return (meta, {delta_key: array}, jobid). Shared by the catalog folder route
    and named boxes on one file; the manifest's form says which."""
    import shutil
    from vislang.runtime.paths import cache_root
    plan = to_plan_json(terminal)
    nonce = os.urandom(4).hex()
    base = _remote_tmp(conn.host)
    routdir = f"{base}/vislang_reduce_{nonce}"
    rmanifest = f"{base}/vislang_manifest_{nonce}.json"
    requested = _srun_requested()
    jobid = _discover_jobid(conn, steps) if requested == "auto" else requested

    # The manifest is a file on BOTH paths (--manifest takes a path).
    man_bytes = len(json.dumps(manifest))
    with timing.phase("ship_plan", bytes=len(plan) + man_bytes):
        stc, _, ste = run_remote(conn, f"mkdir -p {base} && cat > {rmanifest}",
                                 stdin_bytes=json.dumps(manifest).encode())
        if stc != 0:
            raise RuntimeError(f"staging manifest failed: {ste.strip()[-300:]}")
        steps.append(f"ship plan.json + manifest ({what}) to the remote")
        rplan = None
        if jobid is not None:
            rplan = f"{base}/vislang_plan_{nonce}.json"
            steps.append(f"srun --jobid={jobid}: one step for the whole delta")
            stc, _, ste = run_remote(conn, f"cat > {rplan}",
                                     stdin_bytes=plan.encode())
            if stc != 0:
                raise RuntimeError(f"staging plan failed: {ste.strip()[-300:]}")

    # ONE remote job for the whole delta, however many timesteps (and boxes) it
    # spans — `remote_jobs` in the counters stays 1 as N grows.
    with timing.phase("remote_exec", srun=bool(jobid), jobid=jobid,
                      n_timesteps=n_timesteps):
        if jobid is None:
            cmd = _executor_cmd(routdir, folder=True, manifest_path=rmanifest,
                                host=conn.host)
            rc, out, err = run_remote(conn, cmd, stdin_bytes=plan.encode())
        else:
            cmd = _executor_cmd(routdir, plan_path=rplan, jobid=jobid,
                                folder=True, manifest_path=rmanifest,
                                host=conn.host)
            rc, out, err = run_remote(conn, cmd)

    meta = _parse_meta(out)
    if meta is None or not meta.get("ok", False):
        detail = (meta or {}).get("error") or err.strip()[-_ERR_TAIL:] or f"rc={rc}"
        raise RuntimeError(f"remote delta failed: {_stale_remote_hint(detail)}")

    pull_dir = os.path.join(cache_root(), f"folderpull_{nonce}")
    remote_out = meta.get("outdir", routdir)
    with timing.phase("pull"):
        pulled = transfer_dir(conn, remote_out, pull_dir)
    cleanup = f"rm -rf {routdir} {rmanifest}" + (f" {rplan}" if jobid else "")
    run_remote(conn, cleanup)                           # best-effort
    if pulled is None:
        raise RuntimeError("transfer of the delta bundle failed")

    with np.load(os.path.join(pull_dir, "bundle.npz")) as z:
        bundle = {k: z[k] for k in z.files}
    shutil.rmtree(pull_dir, ignore_errors=True)
    return meta, bundle, jobid


def _rebuild_delta_source(remote_dir, positions, narrow_prefix):
    """source(remote-local DIR) -> region/subsample/threshold (NO fields, NO
    timesteps): the shared narrowing the delta manifest re-roots onto each listed
    timestep file (per-file projection comes from the manifest)."""
    node = SourceNode(uri=remote_dir,
                      positions=tuple(positions) if positions else None)
    for nn in narrow_prefix:
        if nn.kind == "region":
            node = dataclasses.replace(nn, upstream=node)
        elif nn.kind == "subsample":
            node = SubsampleNode(upstream=node, uniform=nn.uniform, per_axis=nn.per_axis,
                                 method=nn.method)
        elif nn.kind == "threshold":
            node = ThresholdNode(upstream=node, var=nn.var, op=nn.op, value=nn.value)
    return node
