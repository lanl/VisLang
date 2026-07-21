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

from datasetInfo import DatasetInfo
from my_catalog import ExtentCatalog, make_source_id
from my_download import (establish_connection, transfer, transfer_dir,
                         _parse_remote, remote_stat, remote_header_hash, run_remote)
from ast_serialize import to_plan_json
from dsl_forms.nodes import (SourceNode, FieldsNode, RegionNode, SubsampleNode,
                             ThresholdNode, CompressNode, TimestepsNode)

_NARROWING = ("fields", "region", "subsample", "threshold")
META_BEGIN = "===VISLANG_META_BEGIN==="
META_END = "===VISLANG_META_END==="


class RemoteUnavailable(Exception):
    """Remote reduce can't run (no key auth / can't stat the file / ...).
    The planner catches this and falls back to the whole-file fetch."""


def _srun_requested():
    """Raw VISLANG_SRUN_JOBID: a numeric allocation id, the literal 'auto'
    (discover the held allocation via squeue at reduce time), or None (direct
    ssh — the reducer runs on whatever node ssh lands on). Presence switches on
    srun mode: each reduce runs as a step INTO a held allocation (instant, no
    queue), never `sbatch` (a fresh scheduler job queues independently)."""
    v = os.environ.get("VISLANG_SRUN_JOBID", "").strip()
    return v or None


def _discover_jobid(conn, steps):
    """Resolve VISLANG_SRUN_JOBID=auto to the user's held allocation on the
    remote via `squeue`. Filters to VISLANG_SRUN_NAME (job name, e.g. from
    `salloc -J vislang`) and/or VISLANG_SRUN_PARTITION if set; on several, picks
    the newest (highest id). Raises RemoteUnavailable if none — the user must
    `salloc` first (the planner then falls back to a whole-file fetch)."""
    part = os.environ.get("VISLANG_SRUN_PARTITION", "").strip()
    name = os.environ.get("VISLANG_SRUN_NAME", "").strip()
    rc, out, err = run_remote(conn, 'squeue -h -u $(whoami) -t RUNNING -o "%A|%P|%j"')
    if rc != 0:
        raise RemoteUnavailable("could not query Slurm for a held allocation: "
                                + (err.strip()[-200:] or f"rc={rc}"))
    cand = []
    for line in out.splitlines():
        row = line.strip().split("|")
        if (len(row) >= 3 and row[0].strip().isdigit()
                and (not part or row[1].strip() == part)
                and (not name or row[2].strip() == name)):
            cand.append(row[0].strip())
    if not cand:
        where = ", ".join(f"{k} {v}" for k, v in
                          (("name", name), ("partition", part)) if v)
        raise RemoteUnavailable(
            f"VISLANG_SRUN_JOBID=auto but no RUNNING allocation"
            + (f" matching {where}" if where else "")
            + f" for you on {conn.host}; run `salloc --no-shell ...` first")
    jid = max(cand, key=int)
    steps.append(f"srun: discovered held allocation jobid={jid}"
                 + (f" ({len(cand)} found, using newest)" if len(cand) > 1 else ""))
    return jid


def _remote_tmp():
    """Base dir on the remote for the staged plan + reduced .npz. srun places
    vislang_exec on a COMPUTE node whose local /tmp the login-node pull can't
    see, so srun mode needs a shared-FS dir; direct-ssh mode keeps /tmp (the
    reducer runs on the same node ssh landed on)."""
    if _srun_requested():
        return os.environ.get("VISLANG_REMOTE_TMP", "~/.vislang/reduce")
    return "/tmp"


def _executor_cmd(rout, plan_path=None, jobid=None, folder=False):
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
    if jobid is None:
        env = "VISLANG_NO_BINDING=1 " if os.environ.get("VISLANG_NO_BINDING") else ""
        return f"{env}{py} {repo}/vislang_exec.py --stdin {flag} {rout}"
    # srun mode: run as a step in the held allocation, plan from a staged file.
    srun_args = os.environ.get("VISLANG_SRUN_ARGS", "--overlap -n1")
    export = "ALL,VISLANG_NO_BINDING=1" if os.environ.get("VISLANG_NO_BINDING") else "ALL"
    return (f"srun --jobid={jobid} {srun_args} --export={export} "
            f"{py} {repo}/vislang_exec.py --plan {plan_path} {flag} {rout}")


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
def remote_reduce(src, middle):
    """Run the narrowing prefix of `middle` on the remote host of src.uri.
    Returns (loaded DatasetInfo, steps list). Raises RemoteUnavailable to make
    the planner fall back, or a real error for a broken spec."""
    steps = []
    prefix, compresses = _split_middle(middle)
    if not prefix:
        raise RemoteUnavailable("no narrowing forms — whole-file fetch is equivalent")

    norm = _normalize_remote(src.uri)
    _, host, remote_path = _parse_remote(norm)
    conn = establish_connection(norm)
    if conn.method != "ssh-key":
        raise RemoteUnavailable("remote reduce needs ssh key auth")

    st = remote_stat(conn, remote_path)
    if st is None:
        raise RemoteUnavailable(f"cannot stat {remote_path} on {host}")
    size, mtime = st
    sid = make_source_id(norm, size, mtime, remote_header_hash(conn, remote_path) or "")
    steps.append(f"remote source {host}:{remote_path} ({size / 1e6:.1f} MB, id={sid[:8]})")

    # --- catalog delta: fetch only what we don't already hold -----------------
    from vislang_paths import cache_root
    catalog = ExtentCatalog(cache_root())
    key = _narrow_key(prefix)
    project = _projection_of(prefix)
    schema = catalog.schema(sid)
    want = project if project is not None else (
        list(schema["variables"]) if schema else None)

    have, missing = {}, want
    if want is not None:
        have, missing = catalog.delta(sid, want, key)
        if have:
            steps.append(f"catalog: cached {sorted(have)} for this narrowing")

    fetched = {}
    if want is None or missing:
        meta, fetched = _run_remote_prefix(conn, remote_path, src, prefix, missing, steps)
        catalog.store_schema(sid, meta["schema"])
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
    steps.append(f"assembled {len(data)} var(s), {total / 1e6:.1f} MB "
                 f"({len(have)} cached, {len(fetched)} fetched)")
    return info, steps


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
    if jobid is None:
        cmd = _executor_cmd(rout)                       # plan piped via stdin
        rc, out, err = run_remote(conn, cmd, stdin_bytes=plan.encode())
        rplan = None
    else:
        # srun runs vislang_exec on a compute node; stage the plan to shared FS
        # (its /tmp and the login node's differ) and read it via --plan.
        rplan = f"{base}/vislang_plan_{tag}.json"
        steps.append(f"srun --jobid={jobid}: step into held allocation "
                     f"(plan staged at {rplan})")
        stc, _, ste = run_remote(conn, f"mkdir -p {base} && cat > {rplan}",
                                 stdin_bytes=plan.encode())
        if stc != 0:
            raise RuntimeError(f"staging plan to {rplan} failed: {ste.strip()[-300:]}")
        cmd = _executor_cmd(rout, plan_path=rplan, jobid=jobid)
        rc, out, err = run_remote(conn, cmd)

    meta = _parse_meta(out)
    if meta is None or not meta.get("ok", False):
        detail = (meta or {}).get("error") or err.strip()[-500:] or f"rc={rc}"
        raise RuntimeError(f"remote reduce failed: {detail}")

    from vislang_paths import cache_root
    local = os.path.join(cache_root(), f"pull_{tag}.npz")
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
    """Reduce an ENTIRE remote folder timeseries in one remote process next to
    the data, then pull the saved directory once. This is O(1) in the number of
    timesteps: one ssh session, one (srun) step, one remote Python cold-start,
    one directory transfer — versus the per-timestep loop's ~7-9 handshakes +
    Python start + step + transfer PER file. The catalog is bypassed (this is a
    bulk save-to-disk export, not incremental in-memory extent assembly).

    Returns (local_out_dir, steps, report). Raises RemoteUnavailable so the
    planner can fall back to a whole-folder fetch, or RuntimeError for a real
    failure (a broken spec / a timestep that failed remotely — no silent
    partial: _plan_folder materializes every step before writing any)."""
    steps = []
    norm = _normalize_remote(src.uri)
    _, host, remote_dir = _parse_remote(norm)
    conn = establish_connection(norm)
    if conn.method != "ssh-key":
        raise RemoteUnavailable("remote reduce needs ssh key auth")

    terminal = _rebuild_folder_chain(remote_dir, src.positions, middle, ts_nodes)
    plan = to_plan_json(terminal)

    tag = os.urandom(4).hex()
    base = _remote_tmp()
    routdir = f"{base}/vislang_reduce_{tag}"
    requested = _srun_requested()
    jobid = _discover_jobid(conn, steps) if requested == "auto" else requested
    steps.append(f"remote folder {host}:{remote_dir}")
    steps.append("ship plan.json (folder AST moved to the remote): " + plan)

    if jobid is None:
        cmd = _executor_cmd(routdir, folder=True)          # plan piped via stdin
        rc, out, err = run_remote(conn, cmd, stdin_bytes=plan.encode())
        rplan = None
    else:
        # srun runs vislang_exec on a compute node; stage the plan to shared FS
        # (its /tmp and the login node's differ) and read it via --plan.
        rplan = f"{base}/vislang_plan_{tag}.json"
        steps.append(f"srun --jobid={jobid}: one step for the whole folder "
                     f"(plan staged at {rplan})")
        stc, _, ste = run_remote(conn, f"mkdir -p {base} && cat > {rplan}",
                                 stdin_bytes=plan.encode())
        if stc != 0:
            raise RuntimeError(f"staging plan to {rplan} failed: {ste.strip()[-300:]}")
        cmd = _executor_cmd(routdir, plan_path=rplan, jobid=jobid, folder=True)
        rc, out, err = run_remote(conn, cmd)

    meta = _parse_meta(out)
    if meta is None or not meta.get("ok", False):
        detail = (meta or {}).get("error") or err.strip()[-500:] or f"rc={rc}"
        raise RuntimeError(f"remote folder reduce failed: {detail}")

    remote_out = meta.get("outdir", routdir)
    pulled = transfer_dir(conn, remote_out, out_local_dir)
    cleanup = f"rm -rf {routdir}" + (f" {rplan}" if rplan else "")
    run_remote(conn, cleanup)                               # best-effort cleanup
    if pulled is None:
        raise RuntimeError("transfer of the reduced folder failed")

    labels = meta.get("timesteps", [])
    steps.append(f"pulled {len(labels)} timestep file(s) -> {out_local_dir}")
    report = {"n": len(labels), "host": host, "jobid": jobid,
              "timesteps": labels, "remote_steps": meta.get("steps", [])}
    return out_local_dir, steps, report
