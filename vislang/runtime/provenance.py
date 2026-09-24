"""The derivation record that travels with an output.

`timing.py` records what a run *cost* and `trace.py` narrates what it *decided*;
both land beside the repo, keyed by run. Neither helps someone holding only the
artifact. This module writes the third thing — what an output *is* — into the
artifact itself, or into a companion file where the container has nowhere to put
it.

One versioned JSON object per output, assembled here and emitted by the sinks:

    {"sieve_provenance": 1, "summary", "created", "record_id",
     "producer":  {tool, version, commit, commit_dirty, python, libraries},
     "run":       {run_id, spec_path, spec_sha, rerun_of},
     "source":    {uri, site, filetype, read_from, identity, schema},
     "transform": {summary, spec, plan, lowered, site, seed},
     "output":    {path, format, requested, degraded_from, embedding},
     "variables": {var: {source_variable, shape, source_shape, origin, ...}},
     "geometry", "timesteps", "derived_from"}

The three properties timing.py establishes are inherited verbatim, and the third
is load-bearing beyond tidiness:

  * **Never break a run.** Every entry point swallows its own errors. A record
    that cannot be built must not destroy a save that already succeeded.
  * **Off means gone.** `VISLANG_PROVENANCE=0` makes every call a no-op.
  * **Detached calls are silent.** With no run open, `record()` returns None.
    That is what keeps the remote reducer quiet — it runs `plan_pipeline` on the
    cluster to write a transient npz, which must not acquire a record describing
    a temp path — and what lets the existing tests drive the planner directly
    without growing provenance side effects.

Assembly is split across the run because the facts arrive at different times:
the planner knows the source and the lowered narrowing, the sink knows the
format actually written. The sink pulls the finished record rather than having
it threaded down, so the recorded output path is the one after extension
resolution and any format degradation.
"""

import hashlib
import json
import os
import platform
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime

SCHEMA_KEY = "sieve_provenance"
SCHEMA_VERSION = 1

# The identity window: hash this many bytes at each end of the file. Head alone
# misses formats that keep an index or footer at the tail, and a second read of
# the same size is free next to the stat we already pay for.
_WINDOW = 65536

# Attribute / key name for the full record, and the flat companions written
# beside it so h5dump -A, ncdump -h and ParaView's Information panel show
# something legible without a JSON parser.
REC_ATTR = "sieve_provenance"
FLAT_ATTRS = ("sieve_version", "sieve_source", "sieve_source_id")

_run = None          # the open run record, or None (detached: record nothing)
_pipe = None         # the open pipeline record within _run
_producer = None     # resolved once per process


def enabled():
    return os.environ.get("VISLANG_PROVENANCE", "1") != "0"


def redacting():
    return os.environ.get("VISLANG_PROVENANCE_REDACT", "0") != "0"


# ---------------------------------------------------------------------------
# Producer: which Sieve, on what, with which libraries
# ---------------------------------------------------------------------------
def _version():
    """The installed version, falling back to the in-package constant. The two
    are hardcoded duplicates (vislang/__init__.py and pyproject.toml) with
    nothing keeping them in sync, so prefer the one that reflects what is
    actually installed."""
    try:
        import importlib.metadata as md
        return md.version("vislang")
    except Exception:
        try:
            from vislang import __version__
            return __version__
        except Exception:
            return "unknown"


def _git(args):
    try:
        import subprocess
        from vislang.runtime.paths import REPO_ROOT
        out = subprocess.run(["git", "-C", REPO_ROOT] + args,
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:
        return None


def _commit_state():
    """(short commit, dirty) for the code that ran.

    `dirty` deliberately ignores spec.py and anything else outside the package:
    the spec is edited in place on every run and lives in the repo, so a
    repo-wide check would report dirty always and mean nothing. What we want to
    know is whether the *executing code* diverged from the commit. The spec's own
    state is captured exactly, by `run.spec_sha` and the embedded text."""
    commit = _git(["rev-parse", "--short", "HEAD"])
    if commit is None:
        return None, None
    scope = ["vislang", "mcp_server.py", "cli.py", "vislang_exec.py"]
    changed = _git(["status", "--porcelain", "--"] + scope)
    return commit, bool(changed)


def _libraries(*names):
    """Versions of the libraries that actually touched the data. Not a full
    environment capture — a hundred unrelated package versions in every artifact
    is noise."""
    out = {}
    try:
        import importlib.metadata as md
        for n in ("numpy",) + tuple(n for n in names if n):
            if n in out:
                continue
            try:
                out[n] = md.version(n)
            except Exception:
                pass
    except Exception:
        pass
    return out


def producer():
    global _producer
    if _producer is None:
        commit, dirty = _commit_state()
        _producer = {"tool": "sieve", "version": _version(),
                     "commit": commit, "commit_dirty": dirty,
                     "python": platform.python_version(),
                     "platform": platform.platform(terse=True)}
        if not redacting():
            try:
                _producer["host"] = platform.node()
                _producer["user"] = os.environ.get("USER") or ""
            except Exception:
                pass
    return dict(_producer)


# ---------------------------------------------------------------------------
# Source identity — cheap by default, full content hash on request
# ---------------------------------------------------------------------------
def _window_md5(path, size):
    """(head_md5, tail_md5) over the first and last `_WINDOW` bytes. The tail is
    None when the file is smaller than one window (head already covers it)."""
    try:
        with open(path, "rb") as f:
            head = hashlib.md5(f.read(_WINDOW)).hexdigest()
            if size <= _WINDOW:
                return head, None
            f.seek(-_WINDOW, os.SEEK_END)
            return head, hashlib.md5(f.read(_WINDOW)).hexdigest()
    except OSError:
        return None, None


def content_sha256(path, chunk=1 << 22):
    """Full-content hash. Reads the whole file — callers must gate on cost."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def local_identity(path, uri=None, full_hash=False):
    """Identity for a LOCAL source: size, whole-second mtime, and md5 over the
    head and tail windows.

    Whole-second mtime and the `uri|size|mtime|hash` id string both mirror the
    remote catalog exactly (`remote/download.py` truncates the same way), so a
    file read in place and the same file fetched from a cluster resolve to the
    same source_id."""
    try:
        from vislang.remote.catalog import make_source_id
        st = os.stat(path)
        size, mtime = int(st.st_size), int(st.st_mtime)
        head, tail = _window_md5(path, size)
        ident = {
            "method": "size+mtime+head64k+tail64k-md5",
            "id_uri": uri or path,
            "size": size, "mtime": mtime, "mtime_precision_s": 1,
            "window_bytes": _WINDOW,
            "head_md5": head, "tail_md5": tail,
            "content_sha256": None,
            "observed": datetime.now().isoformat(timespec="seconds"),
            "caveat": ("a rewrite in the same second, at the same size, with an "
                       "unchanged head and tail is not detected"),
        }
        if full_hash:
            ident["content_sha256"] = content_sha256(path)
            ident["method"] += "+sha256"
        ident["source_id"] = make_source_id(ident["id_uri"], size, mtime,
                                            head or "")
        return ident
    except Exception:
        return None


def compare_identity(recorded, current):
    """Fields that differ between a recorded identity and a fresh one, as
    [(field, old, new)]. Empty means unchanged as far as the method can tell."""
    if not recorded or not current:
        return []
    out = []
    for field in ("size", "mtime", "head_md5", "tail_md5", "content_sha256"):
        old, new = recorded.get(field), current.get(field)
        if old is not None and new is not None and old != new:
            out.append((field, old, new))
    return out


# ---------------------------------------------------------------------------
# Run / pipeline scope
# ---------------------------------------------------------------------------
@contextmanager
def run(spec_path, spec_code=None, **fields):
    """Open the run scope. Re-entrant calls yield the open record rather than
    nesting, matching timing.run."""
    global _run, _pipe
    if not enabled() or _run is not None:
        yield _run if _run is not None else {}
        return
    _run = {
        "run_id": uuid.uuid4().hex[:12],
        "spec_path": spec_path,
        "spec_sha": (hashlib.sha256(spec_code.encode()).hexdigest()[:12]
                     if spec_code else None),
        "spec": spec_code,
        "started": datetime.now().isoformat(timespec="seconds"),
        "rerun_of": None,
    }
    _run.update(fields)
    _pipe = None
    try:
        yield _run
    finally:
        _run, _pipe = None, None


@contextmanager
def pipeline(terminal):
    """Open the per-sink scope and stash the serialised plan once.

    The plan is taken from `terminal` rather than from the SourceNode the
    planner is holding: on the whole-file and whole-folder fallbacks the planner
    is re-entered with the *local pulled copy* as its source, so only the
    terminal still carries the URI the spec actually named."""
    global _pipe
    if not enabled() or _run is None:
        yield {}
        return
    rec = {"plan": None, "plan_error": None, "source": {}, "lowered": None,
           "seed": None, "site": None}
    try:
        from vislang.dsl.ast_serialize import to_plan
        rec["plan"] = to_plan(terminal)
        chain = rec["plan"].get("chain") or []
        if chain and chain[0].get("kind") == "source":
            rec["source"]["uri"] = chain[0].get("uri")
    except Exception as e:
        rec["plan_error"] = f"{type(e).__name__}: {e}"
    prev, _pipe = _pipe, rec
    try:
        yield rec
    finally:
        _pipe = prev


def note_source(read_from=None, info=None, identity=None, site=None):
    """Report what the planner learned about the source. Never overwrites the
    authored URI recorded when the pipeline scope opened."""
    if _pipe is None:
        return
    try:
        src = _pipe["source"]
        if read_from:
            src["read_from"] = read_from
        if site:
            src["site"] = site
            _pipe["site"] = site
        if identity:
            src["identity"] = identity
        if info is not None:
            src["filetype"] = getattr(info, "filetype", None)
            src["schema"] = {
                "variables": list(getattr(info, "variables", []) or [])[:200],
                "dimensions": _jsonable(getattr(info, "dimensions", {})),
            }
            if src.get("uri") is None:
                src["uri"] = getattr(info, "filepath", None)
    except Exception:
        pass


def note_lowered(narrowing):
    """Report the fused Narrowing — what the interpreter actually did, as
    opposed to what the spec asked for."""
    if _pipe is None:
        return
    try:
        _pipe["lowered"] = _lowered(narrowing)
    except Exception:
        pass


def note_seed(seed):
    if _pipe is not None:
        _pipe["seed"] = seed


def note_timesteps(steps):
    """steps: [(label, path, identity|None)] for a timeseries run."""
    if _pipe is not None:
        _pipe["timesteps"] = [{"label": l, "path": p, "identity": i}
                              for l, p, i in steps]


# ---------------------------------------------------------------------------
# Serialising the lowered narrowing
# ---------------------------------------------------------------------------
def _pred(p):
    return {"var": getattr(p, "var", None), "op": getattr(p, "op", None),
            "value": getattr(p, "value", None)}


def _post_op(op):
    """Post-ops as DATA, not as class names.

    The planner's trace echo reduces these to `type(op).__name__`, which throws
    away every predicate's variable, operator and value — enough for a one-line
    trace, useless as a record of what filtered the data."""
    kind = type(op).__name__
    out = {"op": kind}
    bbox = getattr(op, "bbox", None)
    if bbox is not None:
        out["bbox"] = {"lo": list(getattr(bbox, "lo", ()) or ()),
                       "hi": list(getattr(bbox, "hi", ()) or ())}
    preds = getattr(op, "predicates", None)
    if preds:
        out["predicates"] = [_pred(p) for p in preds]
    if hasattr(op, "factor"):
        out["factor"] = getattr(op, "factor")
    return out


def _lowered(n):
    if n is None:
        return None
    ranges = getattr(n, "grid_ranges", None)
    return {
        "project": list(n.project) if getattr(n, "project", None) else None,
        "grid_ranges": ([[r.start, r.stop, r.step] for r in ranges]
                        if ranges else None),
        "post_ops": [_post_op(op) for op in (getattr(n, "post_ops", ()) or ())],
    }


def _jsonable(v):
    """Coerce to something json.dumps accepts, without raising."""
    try:
        json.dumps(v)
        return v
    except (TypeError, ValueError):
        if isinstance(v, dict):
            return {str(k): _jsonable(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [_jsonable(x) for x in v]
        return str(v)


# ---------------------------------------------------------------------------
# Assembling the record
# ---------------------------------------------------------------------------
def _variables(loaded, lowered):
    out = {}
    try:
        locations = getattr(loaded, "variable_locations", None) or {}
        positions = tuple(getattr(loaded, "positions", None) or ())
        attrs = getattr(loaded, "attributes", None) or {}
        comp = (getattr(loaded, "compression_info", None) or {}).get("variables", {})
        for name, arr in (loaded.data or {}).items():
            entry = {"dtype": str(getattr(arr, "dtype", "")),
                     "shape": list(getattr(arr, "shape", ()) or ())}
            loc = locations.get(name)
            entry["source_variable"] = (loc.get("source") if isinstance(loc, dict)
                                        else (loc if loc is not None else name))
            src_shape = attrs.get(f"{name}_shape")
            if src_shape is not None:
                entry["source_shape"] = list(src_shape)
            if name in positions:
                entry["role"] = f"position:{'xyz'[positions.index(name)]}"
            entry["origin"] = (attrs.get("remote_reduced") and "remote_reduce") \
                or ("cache" if attrs.get("from_cache") else "read")
            if name in comp:
                entry["compression"] = _jsonable(comp[name])
            out[name] = entry
    except Exception:
        pass
    return out


def _summary(loaded, plan, source_uri):
    try:
        n_vars = len(loaded.data or {})
        first = next(iter((loaded.data or {}).values()), None)
        shape = getattr(first, "shape", ()) or ()
        size = f"{shape[0]:,} rows" if len(shape) == 1 else "×".join(str(s) for s in shape)
        base = os.path.basename((source_uri or "").rstrip("/")) or "source"
        return f"{size}, {n_vars} variable(s) from {base}"
    except Exception:
        return None


def record(loaded, out_path, fmt, *, requested=None, degraded_from=None,
           embedding=None, label=None):
    """The finished record for one written artifact, or None when detached.

    Called by the sinks after the format is resolved, so `output.path` is what
    was actually written rather than what was asked for."""
    if not enabled() or _run is None or _pipe is None:
        return None
    try:
        from vislang.dsl.ast_serialize import describe_plan
        plan = _pipe.get("plan")
        source = dict(_pipe.get("source") or {})
        uri = source.get("uri")
        rec = {
            SCHEMA_KEY: SCHEMA_VERSION,
            "summary": _summary(loaded, plan, uri),
            "created": datetime.now().isoformat(timespec="seconds"),
            "record_id": uuid.uuid4().hex[:12],
            "producer": _redact_producer(producer(), loaded),
            "run": {k: _run.get(k) for k in
                    ("run_id", "spec_path", "spec_sha", "started", "rerun_of")},
            "source": source,
            "transform": {
                "summary": describe_plan(plan) if plan else None,
                "spec": _run.get("spec"),
                "plan": plan,
                "plan_error": _pipe.get("plan_error"),
                "lowered": _pipe.get("lowered"),
                "site": _pipe.get("site"),
                "seed": _pipe.get("seed"),
            },
            "output": {"path": os.path.abspath(out_path), "format": fmt,
                       "requested": requested, "degraded_from": degraded_from,
                       "embedding": embedding, "label": label},
            "variables": _variables(loaded, _pipe.get("lowered")),
            "geometry": _jsonable(getattr(loaded, "geometry", None)),
            "timesteps": _pipe.get("timesteps"),
            "derived_from": _inherited(loaded),
        }
        if redacting():
            rec = _redact(rec)
        return rec
    except Exception:
        return None                       # provenance must never break a save


def _redact_producer(p, loaded):
    """Fold in the libraries that read and wrote this artifact."""
    try:
        reader = {"HDF5": "h5py", "GenericIO": "pygio", "VTK": "vtk",
                  "FITS": "astropy", "yt": "yt"}.get(
                      getattr(loaded, "filetype", None))
        p["libraries"] = _libraries(reader)
    except Exception:
        pass
    return p


def _redact(rec):
    for key in ("host", "user"):
        rec.get("producer", {}).pop(key, None)
    for path in (("run", "spec_path"), ("output", "path"),
                 ("source", "uri"), ("source", "read_from")):
        d = rec.get(path[0]) or {}
        if d.get(path[1]):
            d[path[1]] = os.path.basename(str(d[path[1]]).rstrip("/"))
    return rec


def _inherited(loaded):
    """The ancestry chain: this artifact's own step is recorded at the top
    level, and anything the SOURCE carried is appended beneath, so re-narrowing
    an output preserves the trail back to the original file."""
    try:
        prior = (getattr(loaded, "attributes", None) or {}).get(REC_ATTR)
        if not prior:
            return []
        # h5py hands back a str for its own variable-length attributes but bytes
        # for the fixed-length NC_CHAR ones netCDF writes, so both spellings of
        # "a record was here" have to decode.
        if isinstance(prior, bytes):
            prior = prior.decode("utf-8", "replace")
        parent = json.loads(prior) if isinstance(prior, str) else prior
        chain = [{k: parent.get(k) for k in
                  ("record_id", "created", "producer", "source", "transform",
                   "output")}]
        chain.extend(parent.get("derived_from") or [])
        return chain[:8]                  # depth cap: a record, not an archive
    except Exception:
        return []


def to_json(rec, indent=2):
    """Pretty-printed for a companion file; compact when embedded as a string."""
    return json.dumps(rec, indent=indent, default=str)


# ---------------------------------------------------------------------------
# Companion files
# ---------------------------------------------------------------------------
def sidecar_path(out_path):
    """`.<basename>.sieve-prov.json` beside the output; a directory gets
    `<dir>/.sieve-provenance.json`.

    The leading dot is functional. Timeseries discovery matches `#(\\d+)`
    anywhere in a filename, so `timestep#0.sieve-prov.json` inside an output
    folder would be enumerated as a second timestep 0 and then fail adapter
    dispatch. Dotfiles are skipped by that scan."""
    if os.path.isdir(out_path):
        return os.path.join(out_path, ".sieve-provenance.json")
    d, base = os.path.split(os.path.abspath(out_path))
    return os.path.join(d, f".{base}.sieve-prov.json")


def write_sidecar(out_path, rec):
    """Write the companion file, returning its path (or None). Records the
    target's own size and head hash so a reader can tell when a companion has
    drifted from the file beside it."""
    if not rec:
        return None
    try:
        rec = dict(rec)
        rec.setdefault("output", {})["embedding"] = "sidecar"
        try:
            if os.path.isfile(out_path):
                size = os.path.getsize(out_path)
                head, _ = _window_md5(out_path, size)
                rec["target"] = {"path": os.path.basename(out_path),
                                 "size": size, "head_md5": head}
            elif os.path.isdir(out_path):
                rec["target"] = {"path": os.path.basename(out_path.rstrip("/")),
                                 "kind": "folder"}
        except OSError:
            pass
        path = sidecar_path(out_path)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write(to_json(rec) + "\n")
        os.replace(tmp, path)             # never leave a half-written file
        return path
    except Exception:
        return None


def read_sidecar(out_path):
    try:
        with open(sidecar_path(out_path)) as f:
            return json.load(f)
    except Exception:
        return None


def record_for(path):
    """Resolve a record for `path` from wherever it lives: a folder record, an
    embedded one, or a companion file. Callers never branch on mechanism."""
    if os.path.isdir(path):
        return read_sidecar(path)
    try:
        from vislang.output.save import read_embedded
        rec = read_embedded(path)
        if rec:
            return rec
    except Exception:
        pass
    return read_sidecar(path)
