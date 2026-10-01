"""The derivation record that travels with an output.

`timing.py` records what a run *cost* and `trace.py` narrates what it *decided*;
both land beside the repo, keyed by run. Neither helps someone holding only the
artifact. This module writes the third thing — what an output *is* — into the
artifact itself, or into a companion file where the container has nowhere to put
it.

One YAML document per output, `format: sieve-provenance/2`, laid out exactly as
the templates in `prov-ex/`:

    format, result_summary,
    logical:     spec, input, resolved, output, sieve, libraries, explanation
    realization: at, took_s, by, columns_from, compress, env, run

`logical` is what any correct run of this spec on this input must agree on;
`realization` is how this particular run went. Absent means none: a field with
no value is left out, never written as null.

Nothing here is written by a model. `result_summary` and `explanation` are
rendered from recorded fields by one fixed template per form, so a checker can
rebuild them and compare. A note about *intent* belongs in the spec's own `#`
comments, which `logical.spec` keeps verbatim.

Ancestry is a link, not a copy: when the source is itself a Sieve output, the
record names that output's `data_sha256` and the parent's own record holds the
next link.

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
the planner knows the source, the sink knows the format actually written. The
sink pulls the finished record rather than having it threaded down.
"""

import hashlib
import json
import math
import os
import platform
import re
import time
import uuid
from contextlib import contextmanager
from datetime import datetime

FORMAT = "sieve-provenance/2"

# Attribute / key / field-data name for the record, in every container.
REC_KEY = "sieve_provenance"

# Fingerprints: sha256 everywhere, cut to this many hex characters — plenty for
# catching accidental change. The input's head window is the same 64 KiB the
# remote catalog reads.
_HASH_LEN = 16
_WINDOW = 65536

_run = None          # the open run record, or None (detached: record nothing)
_pipe = None         # the open pipeline record within _run
_build = None        # which Sieve, resolved once per process


def enabled():
    return os.environ.get("VISLANG_PROVENANCE", "1") != "0"


def active():
    """True while a pipeline scope is recording. Lets a caller skip work (a
    fingerprint read per timestep) whose only consumer is the record."""
    return _pipe is not None


def redacting():
    return os.environ.get("VISLANG_PROVENANCE_REDACT", "0") != "0"


# ---------------------------------------------------------------------------
# Which Sieve
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
    text is captured exactly, in `logical.spec`."""
    commit = _git(["rev-parse", "--short", "HEAD"])
    if commit is None:
        return None, None
    scope = ["vislang", "mcp_server.py", "cli.py", "vislang_exec.py"]
    changed = _git(["status", "--porcelain", "--"] + scope)
    return commit, bool(changed)


def build():
    """{version, commit, uncommitted_changes?} — the last only when true."""
    global _build
    if _build is None:
        commit, dirty = _commit_state()
        _build = {"version": _version(), "commit": commit}
        if dirty:
            _build["uncommitted_changes"] = True
    return dict(_build)


def _libraries(names):
    """Versions of the libraries that actually touched the data. Not a full
    environment capture — a hundred unrelated package versions in every artifact
    is noise."""
    out = {}
    try:
        import importlib.metadata as md
        for n in names:
            if n and n not in out:
                try:
                    out[n] = md.version(n)
                except Exception:
                    pass
    except Exception:
        pass
    return out


_READER_LIB = {"HDF5": "h5py", "GenericIO": "pygio", "VTK": "vtk",
               "FITS": "astropy", "yt": "yt"}
_WRITER_LIBS = {"hdf5": ("h5py",), "genericio": ("pygio",),
                "vtk": ("vtk", "pyvista"), "netcdf4": ("netCDF4",), "npz": ()}


# ---------------------------------------------------------------------------
# Fingerprints
# ---------------------------------------------------------------------------
def short_sha256(blob):
    return hashlib.sha256(blob).hexdigest()[:_HASH_LEN]


def iso_time(epoch=None):
    """Local time with its UTC offset, whole seconds."""
    t = datetime.now() if epoch is None else datetime.fromtimestamp(int(epoch))
    return t.astimezone().isoformat(timespec="seconds")


def fingerprint(size, mtime, head_sha256=None):
    """The input fingerprint from facts already in hand (a remote stat)."""
    fp = {"size": int(size), "mtime": iso_time(mtime)}
    if head_sha256:
        fp["head64k_sha256"] = str(head_sha256)[:_HASH_LEN]
    return fp


def file_fingerprint(path):
    """size, mtime and a hash of the first 64 KiB of a LOCAL file. A cheap
    stand-in for content identity; size alone catches the commonest damage, a
    truncated copy. A rewrite at the same size and second that leaves the head
    unchanged is not detected."""
    try:
        st = os.stat(path)
        with open(path, "rb") as f:
            head = f.read(_WINDOW)
        return {"size": int(st.st_size), "mtime": iso_time(st.st_mtime),
                "head64k_sha256": short_sha256(head)}
    except OSError:
        return None


def data_sha256(data):
    """A hash of the column VALUES, not the file's bytes — the same whatever the
    output format or writer, so it identifies a result across a conversion and
    is what records link to each other by. Each column, in name order, hashes
    its name, dtype, shape and C-ordered bytes."""
    import numpy as np
    h = hashlib.sha256()
    for name in sorted(data):
        arr = np.ascontiguousarray(np.asarray(data[name]))
        h.update(f"{name}\0{arr.dtype.str}\0{list(arr.shape)}\0".encode())
        h.update(arr.data if arr.size else b"")
    return h.hexdigest()[:_HASH_LEN]


def _data_hash(loaded):
    """data_sha256, computed once per result however many records ask."""
    cache = _pipe.setdefault("hashes", {}) if _pipe is not None else {}
    key = id(loaded)
    if key not in cache:
        cache[key] = data_sha256(loaded.data or {})
    return cache[key]


def parent_link(path):
    """The `data_sha256` of the Sieve output at `path`, or None when it carries
    no record. This is the whole of ancestry: a link, not a copy."""
    try:
        rec = record_for(path)
        return (((rec or {}).get("logical") or {}).get("output") or {}) \
            .get("fingerprint", {}).get("data_sha256")
    except Exception:
        return None


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
    _run = {"run_id": uuid.uuid4().hex[:12], "spec_path": spec_path,
            "spec": spec_code, "t0": time.monotonic()}
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
    rec = {"plan": None, "source": {}, "columns_from": None, "timesteps": {},
           "cast": []}
    try:
        from vislang.dsl.ast_serialize import to_plan
        rec["plan"] = to_plan(terminal)
        chain = rec["plan"].get("chain") or []
        if chain and chain[0].get("kind") == "source":
            rec["source"]["uri"] = chain[0].get("uri")
    except Exception:
        pass
    prev, _pipe = _pipe, rec
    try:
        yield rec
    finally:
        _pipe = prev


def note_source(read_from=None, info=None, fingerprint=None, site=None,
                columns=None, filetype=None):
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
        if fingerprint:
            src["fingerprint"] = fingerprint
        if info is not None:
            src["filetype"] = getattr(info, "filetype", None)
            src["columns"] = list(getattr(info, "variables", []) or [])
            if src.get("uri") is None:
                src["uri"] = getattr(info, "filepath", None)
        if columns is not None:
            src["columns"] = list(columns)
        if filetype:
            src["filetype"] = filetype
        if site == "local" and read_from and os.path.isfile(read_from):
            link = parent_link(read_from)
            if link:
                src["derived_from"] = link
    except Exception:
        pass


def note_timesteps(steps):
    """steps: [{label, uri, fingerprint, derived_from?}] for a timeseries run."""
    if _pipe is None:
        return
    try:
        for s in steps:
            _pipe["timesteps"][int(s["label"])] = s
    except Exception:
        pass


def note_columns_from(groups):
    """How each output column arrived: {group: {columns, ...}} — `cache`,
    `remote`, `local`, `fetched_whole_file`. Realization, not logic."""
    if _pipe is not None:
        _pipe["columns_from"] = groups


def note_cast(var):
    """An integer grid variable was cast to float32 to hold a threshold's NaN."""
    if _pipe is not None and var not in _pipe["cast"]:
        _pipe["cast"].append(var)


# ---------------------------------------------------------------------------
# Small renderers shared by the summary and the explanation
# ---------------------------------------------------------------------------
def _num(v):
    return repr(v) if isinstance(v, float) else str(v)


def _names(xs):
    return ", ".join(str(x) for x in xs)


def _ordinal(n):
    n = int(n)
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _bytes(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return (f"{n:.0f} {unit}" if unit == "B" or n >= 10
                    else f"{n:.1f} {unit}")
        n /= 1000


def _step_from_factor(f):
    """The grid stride a factor means — planner._step_from_factor, repeated so
    a reader of the record needs no interpreter in scope."""
    return f if isinstance(f, int) else max(1, int(round(1 / f)))


def _lossy(comp):
    """{var: (kind, bound)} for the compressed variables whose values changed:
    kind is 'absolute', 'relative' or 'precision'. Zstd is lossless."""
    out = {}
    for var, m in (comp or {}).items():
        method = m.get("method")
        if method == "SPERR":
            out[var] = (m.get("mode"), m.get("error_bound"))
        elif method == "dtype_reduction":
            out[var] = ("precision", m.get("error_bound"))
    return out


def _within(kind, bound):
    if kind == "absolute":
        return f"within {_num(bound)} of the source"
    if kind == "relative":
        return f"within a relative error bound of {_num(bound)}"
    return f"rounded to float{bound}"


# ---------------------------------------------------------------------------
# explanation: one template per form
# ---------------------------------------------------------------------------
_FORMAT_DESC = {"hdf5": "one HDF5 file", "npz": "one npz file",
                "genericio": "one GenericIO file", "netcdf4": "one netCDF-4 file"}
_FAMILY = {"HDF5": "hdf5", "npz": "npz", "GenericIO": "genericio", "VTK": "vtk"}


def _vtk_kind(ext, grid):
    model = "ImageData" if grid else "PolyData"
    if ext == ".vtk":
        return f"legacy VTK {model}"
    if ext == ".vtkhdf":
        return f"VTKHDF {model}"
    return f"VTK {model}"


def _save_lines(fmt, ext, in_filetype, grid, positions, geometry, series,
                degraded):
    if fmt == "vtk":
        desc = _vtk_kind(ext, grid)
        if series:
            desc = f"one {desc} file"
    else:
        desc = _FORMAT_DESC.get(fmt, f"one {fmt} file")
    head = f"write {desc}" + (" per timestep" if series else "")
    converted = (in_filetype is not None
                 and _FAMILY.get(in_filetype) != fmt)
    more = []
    if degraded:
        head += f" (the {degraded} writer could not take this result)"
    elif converted:
        head += f", converted from {in_filetype}"
    if fmt == "vtk":
        if grid:
            more.append("every column as point data")
            if geometry and geometry.get("origin") is not None:
                more.append(f"origin {_flow_text(geometry.get('origin'))}, "
                            f"spacing {_flow_text(geometry.get('spacing'))}")
            else:
                more.append("in index space (origin 0, spacing 1)")
        elif positions:
            more.append(f"{_names(positions)} as point coordinates,")
            more.append("and every column as point data")
    elif fmt == "netcdf4":
        more.append("dimensions x, y, z in Sieve's index order" if grid
                    else 'one "particles" dimension')
    if more and not head.endswith(")"):
        head += ":"
    return head, more


def _flow_text(v):
    if isinstance(v, (list, tuple)):
        return "(" + ", ".join(_num(x) for x in v) + ")"
    return _num(v)


def explain(chain, *, input_columns, output_columns, in_filetype, fmt, ext,
            comp, modes, grid, positions=None, geometry=None, cast=(),
            series=False, degraded=None):
    """The Steps list and the Values verdict, from recorded fields only."""
    lossy = _lossy(comp)
    avail = list(input_columns or [])
    out_cols = set(output_columns or [])
    steps = []

    for step in chain[1:]:
        kind = step.get("kind")
        head, more = None, []
        if kind == "fields":
            keep = list(step.get("keep") or [])
            drop = [c for c in avail if c not in keep]
            head = f"keep {_names(keep)}" + (f"; drop {_names(drop)}" if drop else "")
            avail = keep
        elif kind == "region":
            ranges = step.get("ranges") or []
            if grid:
                head = "keep the block"
                more = [f"  {a} index {_num(lo)} to {_num(hi - 1)}"
                        if isinstance(lo, int) and isinstance(hi, int)
                        else f"  {a} index {_num(lo)} up to {_num(hi)}"
                        for a, lo, hi in ranges]
            else:
                head = "keep rows with (bounds inclusive)"
                more = [f"  {a} between {_num(lo)} and {_num(hi)}"
                        for a, lo, hi in ranges]
        elif kind == "threshold":
            test = f"{step.get('var')} {step.get('op')} {_num(step.get('value'))}"
            head = (f"keep cells where {test}; set the rest to NaN" if grid
                    else f"keep rows where {test}")
            if step.get("var") not in out_cols:
                more.append("(read for this test only; not in the output)")
        elif kind == "subsample":
            u, per = step.get("uniform"), step.get("per_axis") or []
            if grid:
                if u is not None:
                    k = _step_from_factor(u)
                    head = (f"keep every {_ordinal(k)} cell along each axis"
                            if k > 1 else "keep every cell")
                    if isinstance(u, float):
                        more.append(f"(fraction {_num(u)} -> stride {k})")
                else:
                    head = "keep " + ", ".join(
                        f"every {_ordinal(_step_from_factor(f))} cell along {a}"
                        for a, f in per)
            elif isinstance(u, float) and u < 1:
                head = f"keep a random {u * 100:g}% of the rows"
            elif isinstance(u, int) and u > 1:
                head = f"keep every {_ordinal(u)} row: rows 0, {u}, {2 * u}, …"
            else:
                head = "keep every row"
        elif kind == "timesteps":
            head = f"keep timesteps #{step.get('start')} to #{step.get('stop')}"
        elif kind == "compress":
            names = list(step.get("variables") or [])
            groups = {}
            for v in names:
                m = (comp or {}).get(v) or {}
                groups.setdefault((m.get("method"), m.get("mode")), []).append(v)
            lines = []
            for (method, mode), vs in groups.items():
                if method == "SPERR":
                    lines.append(f"{_names(vs)} with SPERR, {mode} error bound "
                                 f"{_num(step.get('error_bound'))}")
                elif method == "Zstd":
                    lines.append(f"{_names(vs)} with Zstd (lossless; not floating point)")
                elif method == "dtype_reduction":
                    lines.append(f"{_names(vs)} cast to float{step.get('error_bound')}")
                else:
                    lines.append(f"{_names(vs)} skipped (not numeric)")
            head, more = lines[0], lines[1:]
            if step.get("mode") == "auto" and modes:
                chosen = {modes[v] for v in names if v in modes}
                if len(chosen) == 1:
                    more.append(f'(mode "auto" chose {chosen.pop()} for each)')
                elif chosen:
                    more.append('(mode "auto" chose ' + "; ".join(
                        f"{m} for {_names(v for v in names if modes.get(v) == m)}"
                        for m in sorted(chosen)) + ")")
        elif kind == "save":
            head, more = _save_lines(fmt, ext, in_filetype, grid, positions,
                                     geometry, series, degraded)
        else:
            continue                                   # render: never recorded
        steps.append((kind, head, more))

    lines = ["Steps"]
    for i, (kind, head, more) in enumerate(steps, 1):
        prefix = f"  {i}. {kind:<11}"
        lines.append(prefix + head)
        lines.extend(" " * len(prefix) + m for m in more)
    lines += ["", "Values: " + _values_line(output_columns, lossy, cast)]
    return "\n".join(lines) + "\n"


def _values_line(columns, lossy, cast):
    columns = list(columns or [])
    changed = [c for c in columns if c in lossy or c in cast]
    if not changed:
        return "all exact."
    parts = []
    exact = [c for c in columns if c not in changed]
    if exact:
        parts.append(f"{_names(exact)} exact")
    by_bound = {}
    for c in columns:
        if c in lossy:
            by_bound.setdefault(lossy[c], []).append(c)
    for (kind, bound), cs in by_bound.items():
        parts.append(f"{_names(cs)} {_within(kind, bound)}")
    casted = [c for c in columns if c in cast and c not in lossy]
    if casted:
        parts.append(f"{_names(casted)} cast to float32 to hold NaN "
                     f"(kept values exact)")
    return "; ".join(parts) + "."


# ---------------------------------------------------------------------------
# result_summary
# ---------------------------------------------------------------------------
def _shape_text(data):
    first = next(iter(data.values()), None)
    shape = tuple(getattr(first, "shape", ()) or ())
    if len(shape) == 1:
        return f"{shape[0]:,} rows"
    return "×".join(str(s) for s in shape) + " grid"


def summarize(per_step, comp, cast, input_bytes, series=False):
    """What came out that the spec alone can't tell you: rows, columns by
    type, sizes in and out. Plain text for people; tools compare fingerprints."""
    last = per_step[-1].data or {}
    ncols = len(last)
    out_bytes = sum(int(getattr(a, "nbytes", 0)) for l in per_step
                    for a in (l.data or {}).values())
    if series:
        rows = {_shape_text(l.data or {}) for l in per_step}
        each = f", {rows.pop()} each" if len(rows) == 1 else ""
        line = f"{len(per_step)} timesteps{each} × {ncols} columns"
    else:
        line = f"{_shape_text(last)} × {ncols} columns"
    line += f", {_bytes(out_bytes)}"
    if input_bytes:
        line += f" (from a {_bytes(input_bytes)} input)"
    lossy = _lossy(comp)
    by_dtype = {}
    for name, arr in last.items():
        by_dtype.setdefault(str(getattr(arr, "dtype", "")), []).append(name)
    w = max((len(d) for d in by_dtype), default=0)
    lines = [line]
    for dtype, names in by_dtype.items():
        row = f"  {dtype:<{w}}  {_names(names)}"
        notes = []
        lossy_here = [n for n in names if n in lossy]
        if lossy_here:
            kinds = {lossy[n] for n in lossy_here}
            bound = (f"within {_num(next(iter(kinds))[1])}"
                     if len(kinds) == 1 and next(iter(kinds))[0] == "absolute"
                     else "see explanation")
            notes.append(f"{_names(lossy_here)} lossy, {bound}")
        cast_here = [n for n in names if n in cast and n not in lossy]
        if cast_here:
            notes.append(f"{_names(cast_here)} cast from an integer type")
        if notes:
            row += "   (" + "; ".join(notes) + ")"
        lines.append(row)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Assembling the record
# ---------------------------------------------------------------------------
def _out_format(fmt, out_path):
    if fmt == "vtk":
        return os.path.splitext(out_path)[1].lstrip(".").lower() or "vtk"
    return fmt


def _in_format(filetype):
    return str(filetype).lower() if filetype else None


def _is_grid(data):
    return any(getattr(a, "ndim", 1) >= 2 for a in (data or {}).values())


def _random_subsample(chain, grid):
    return (not grid) and any(
        s.get("kind") == "subsample" and isinstance(s.get("uniform"), float)
        and s.get("uniform") < 1 for s in chain)


def _output_entry(loaded, grid):
    data = loaded.data or {}
    first = next(iter(data.values()), None)
    shape = list(getattr(first, "shape", ()) or ())
    out = {}
    if grid:
        out["shape"] = shape
    else:
        out["rows"] = shape[0] if shape else 0
    return out


def record(loaded, out_path, fmt, *, step=None, degraded_from=None):
    """The finished record for one written file, or None when detached.

    Called by the sinks after the format is resolved and before the write, so a
    writer can embed it. `step` is the timestep label when the file is one step
    of a series."""
    if not enabled() or _run is None or _pipe is None:
        return None
    try:
        return _assemble([(step, loaded)], out_path, fmt, degraded_from,
                         series=False)
    except Exception:
        return None                       # provenance must never break a save


def record_series(per_step, out_dir, fmt, degraded_from=None):
    """The one record for a timeseries folder: every step's fingerprint in,
    every step's rows and data_sha256 out."""
    if not enabled() or _run is None or _pipe is None or not per_step:
        return None
    try:
        return _assemble(list(per_step), out_dir, fmt, degraded_from, series=True)
    except Exception:
        return None


def _assemble(per_step, out_path, fmt, degraded_from, series):
    plan = _pipe.get("plan") or {}
    chain = plan.get("chain") or []
    src = _pipe.get("source") or {}
    steps_in = _pipe.get("timesteps") or {}
    loadeds = [l for _, l in per_step]
    last = loadeds[-1]
    data = last.data or {}
    grid = _is_grid(data)
    comp = (getattr(last, "compression_info", None) or {}).get("variables") or {}
    cast = list(_pipe.get("cast") or [])
    out_fmt = _out_format(fmt, out_path)
    ext = os.path.splitext(out_path)[1].lower()

    # --- input -------------------------------------------------------------
    inp = {"uri": src.get("uri"), "format": _in_format(src.get("filetype")),
           "columns": src.get("columns")}
    label = per_step[0][0]
    input_bytes = None
    if series:
        ts = {}
        for lab, _ in per_step:
            s = steps_in.get(int(lab)) if lab is not None else None
            if s and s.get("fingerprint"):
                entry = dict(s["fingerprint"])
                if s.get("derived_from"):
                    entry["derived_from"] = s["derived_from"]
                ts[int(lab)] = entry
        inp["timesteps"] = ts
        sizes = [e.get("size") for e in ts.values() if e.get("size")]
        input_bytes = sum(sizes) if sizes else None
    elif label is not None and int(label) in steps_in:
        s = steps_in[int(label)]
        inp["uri"] = s.get("uri") or inp["uri"]
        inp["fingerprint"] = s.get("fingerprint")
        inp["derived_from"] = s.get("derived_from")
        input_bytes = (s.get("fingerprint") or {}).get("size")
    else:
        inp["fingerprint"] = src.get("fingerprint")
        inp["derived_from"] = src.get("derived_from")
        input_bytes = (src.get("fingerprint") or {}).get("size")

    # --- resolved: what the spec left open --------------------------------
    resolved = {}
    if _random_subsample(chain, grid):
        try:
            from vislang.interpreter.narrowing import sampling_seed
            resolved["subsample_random_seed"] = str(sampling_seed())
        except Exception:
            pass
    auto = [v for s in chain if s.get("kind") == "compress"
            and s.get("mode") == "auto" for v in (s.get("variables") or [])]
    modes = {v: comp[v].get("mode") for v in auto if v in comp
             and comp[v].get("method") == "SPERR"}
    if modes:
        resolved["compress_mode"] = modes

    # --- output ------------------------------------------------------------
    out = {"format": out_fmt}
    if series:
        out["timesteps"] = {int(lab): {**_output_entry(l, grid),
                                       "data_sha256": _data_hash(l)}
                            for lab, l in per_step}
        out["columns"] = {k: str(getattr(a, "dtype", "")) for k, a in data.items()}
    else:
        out.update(_output_entry(last, grid))
        out["columns"] = {k: str(getattr(a, "dtype", "")) for k, a in data.items()}
        geom = getattr(last, "geometry", None)
        if grid and isinstance(geom, dict):
            g = {k: geom.get(k) for k in ("origin", "spacing")
                 if geom.get(k) is not None}
            if g:
                out["geometry"] = g
        out["fingerprint"] = {"data_sha256": _data_hash(last)}

    libs = [_READER_LIB.get(src.get("filetype")), "numpy"]
    if comp:
        libs += ["h5py", "hdf5plugin"]
    libs += list(_WRITER_LIBS.get(fmt, ()))

    explanation = explain(
        chain, input_columns=src.get("columns"), output_columns=list(data),
        in_filetype=src.get("filetype"), fmt=fmt, ext=ext, comp=comp,
        modes=modes, grid=grid, positions=getattr(last, "positions", None),
        geometry=getattr(last, "geometry", None), cast=cast, series=series,
        degraded=degraded_from)

    logical = {"spec": _run.get("spec"), "input": inp, "resolved": resolved,
               "output": out, "sieve": build(), "libraries": _libraries(libs),
               "explanation": explanation}

    # --- realization: how this run went ------------------------------------
    real = {"at": iso_time(),
            "took_s": int(round(time.monotonic() - _run.get("t0", time.monotonic())))}
    if not redacting():
        try:
            real["by"] = f"{os.environ.get('USER') or ''}@{platform.node()}"
        except Exception:
            pass
    real["columns_from"] = _pipe.get("columns_from") or _local_columns(src, data)
    if comp:
        codecs = {v: str(m.get("method") or "").lower() for v, m in comp.items()}
        errs = {v: float(f"{m['max_absolute_error']:.4g}") for v, m in comp.items()
                if m.get("max_absolute_error") is not None}
        real["compress"] = {"codec": (next(iter(set(codecs.values())))
                                      if len(set(codecs.values())) == 1 else codecs),
                            "max_error": errs}
    real["env"] = {"python": platform.python_version(),
                   "platform": platform.platform(terse=True)}
    real["run"] = _run.get("run_id")

    rec = {"format": FORMAT,
           "result_summary": summarize(loadeds, comp, cast, input_bytes, series),
           "logical": logical, "realization": real}
    if redacting() and inp.get("uri"):
        inp["uri"] = os.path.basename(str(inp["uri"]).rstrip("/"))
    return rec


def _local_columns(src, data):
    """The default when no remote path reported its own grouping: everything
    was read here — from the source itself, or from a whole-file copy of a
    remote one."""
    group = "local"
    try:
        from vislang.formats.inspect import is_remote
        if is_remote(str(src.get("uri") or "")):
            group = "fetched_whole_file"
    except Exception:
        pass
    return {group: {"columns": list(data)}}


# ---------------------------------------------------------------------------
# YAML
# ---------------------------------------------------------------------------
# A small emitter rather than PyYAML's dump, because the layout is the point:
# fixed key order, `|` blocks for the spec and prose, one-line maps for
# fingerprints. Its output is checked by loading it back (see to_yaml), so a
# layout choice can never change a value.
_BLOCK_KEYS = {"logical", "input", "resolved", "output", "realization",
               "columns_from", "timesteps", "local", "remote", "cache",
               "fetched_whole_file"}
_QUOTED_KEYS = {"data_sha256", "head64k_sha256", "derived_from", "run",
                "commit", "subsample_random_seed"}
_WIDTH = 120
_PLAIN = re.compile(r"^[A-Za-z_][A-Za-z0-9_./+-]*$")
_RESERVED = {"yes", "no", "on", "off", "true", "false", "null"}
# The characters our own templates emit, and their ASCII spellings for
# containers (VTK string arrays) that refuse anything else.
_ASCII_FOLD = {"×": "x", "…": "...", "→": "->"}


def _plain(v):
    """Normalise to JSON-like python: numpy scalars, tuples, bytes."""
    try:
        import numpy as np
        if isinstance(v, np.generic):
            return v.item()
        if isinstance(v, np.ndarray):
            return [_plain(x) for x in v.tolist()]
    except Exception:
        pass
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()
                if x is not None and x != {} and x != []}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return v


def _quote(s, ascii):
    return json.dumps(s, ensure_ascii=ascii)


def _str(s, ascii, force_quote=False):
    if not force_quote and _PLAIN.match(s) and s.lower() not in _RESERVED:
        return s
    return _quote(s, ascii)


def _float(x):
    if math.isnan(x):
        return ".nan"
    if math.isinf(x):
        return ".inf" if x > 0 else "-.inf"
    r = repr(float(x))
    if "e" in r and "." not in r:                # YAML 1.1 floats need a dot
        m, e = r.split("e")
        r = f"{m}.0e{e}"
    return r


def _scalar(v, ascii, force_quote=False):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return _float(v)
    return _str(str(v), ascii, force_quote)


def _flow(v, ascii, key=None):
    if isinstance(v, dict):
        return "{" + ", ".join(f"{_scalar(k, ascii)}: {_flow(x, ascii, k)}"
                               for k, x in v.items()) + "}"
    if isinstance(v, list):
        return "[" + ", ".join(_flow(x, ascii) for x in v) + "]"
    return _scalar(v, ascii, key in _QUOTED_KEYS)


def _block(text, indent, ascii):
    """(header, lines) for a `|` block scalar, or None when the text needs a
    quoted string instead (non-ASCII in ASCII mode, control characters)."""
    if ascii and not text.isascii():
        return None
    if any((ord(c) < 32 and c not in "\n\t") or c in "\x7f﻿" for c in text):
        return None
    if text.endswith("\n"):
        body = text[:-1]
        chomp = "+" if body.endswith("\n") else ""
    else:
        body, chomp = text, "-"
    lines = body.split("\n")
    first = next((ln for ln in lines if ln.strip()), "")
    spacey = any(ln and not ln.strip() for ln in lines)
    ind = "2" if (first[:1] in (" ", "\t") or spacey) else ""
    pad = " " * indent
    return f"|{ind}{chomp}", [pad + ln if ln else "" for ln in lines]


def _emit(d, indent, out, ascii):
    pad = " " * indent
    for k, v in d.items():
        key = _scalar(k, ascii)
        if isinstance(v, str) and "\n" in v:
            blk = _block(v, indent + 2, ascii)
            if blk is None:
                out.append(f"{pad}{key}: {_quote(v, ascii)}")
            else:
                out.append(f"{pad}{key}: {blk[0]}")
                out.extend(blk[1])
        elif isinstance(v, dict):
            flow = None if k in _BLOCK_KEYS else _flow(v, ascii)
            if flow is not None and len(pad) + len(key) + 2 + len(flow) <= _WIDTH:
                out.append(f"{pad}{key}: {flow}")
            else:
                out.append(f"{pad}{key}:")
                _emit(v, indent + 2, out, ascii)
        elif isinstance(v, list):
            flow = _flow(v, ascii)
            if len(pad) + len(key) + 2 + len(flow) <= _WIDTH or not v:
                out.append(f"{pad}{key}: {flow}")
            else:
                out.append(f"{pad}{key}:")
                out.extend(f"{pad}  - {_flow(x, ascii)}" for x in v)
        else:
            out.append(f"{pad}{key}: {_scalar(v, ascii, k in _QUOTED_KEYS)}")


def _fold(v):
    if isinstance(v, str):
        for a, b in _ASCII_FOLD.items():
            v = v.replace(a, b)
        return v
    if isinstance(v, dict):
        return {k: _fold(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_fold(x) for x in v]
    return v


def _same(a, b):
    """Equality that lets NaN equal NaN, for the read-back check."""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, float) and isinstance(b, float) and a != a and b != b:
        return True
    return type(a) is type(b) and a == b


def to_yaml(rec, ascii=False):
    """The record as YAML text in the template's layout.

    `ascii=True` is for containers that refuse anything else: our own symbols
    are spelled in ASCII, and any other text that is not ASCII (the spec, say)
    becomes an escaped quoted string instead of a block.

    The text is loaded back and compared before it is returned. If a layout
    choice ever changed a value, the record falls back to JSON — which any
    YAML 1.2 reader also reads — rather than carrying a wrong one."""
    rec = _plain(rec)
    if ascii:
        rec = _fold(rec)
    lines = []
    _emit(rec, 0, lines, ascii)
    text = "\n".join(lines) + "\n"
    try:
        import yaml
        if not _same(yaml.safe_load(text), rec):
            return json.dumps(rec, indent=2, ensure_ascii=ascii) + "\n"
    except ImportError:
        pass
    except Exception:
        return json.dumps(rec, indent=2, ensure_ascii=ascii) + "\n"
    return text


def parse(text):
    """A record's text back into a dict, or None if it is not one."""
    if not text:
        return None
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    try:
        import yaml
        rec = yaml.safe_load(text)
    except ImportError:
        try:
            rec = json.loads(text)
        except Exception:
            return None
    except Exception:
        return None
    return rec if isinstance(rec, dict) else None


# ---------------------------------------------------------------------------
# Companion files
# ---------------------------------------------------------------------------
def sidecar_path(out_path):
    """`.<basename>.sieve-prov.yaml` beside the output; a directory gets
    `<dir>/.sieve-provenance.yaml`.

    The leading dot is functional. Timeseries discovery matches `#(\\d+)`
    anywhere in a filename, so `timestep#0.sieve-prov.yaml` inside an output
    folder would be enumerated as a second timestep 0 and then fail adapter
    dispatch. Dotfiles are skipped by that scan."""
    if os.path.isdir(out_path):
        return os.path.join(out_path, ".sieve-provenance.yaml")
    d, base = os.path.split(os.path.abspath(out_path))
    return os.path.join(d, f".{base}.sieve-prov.yaml")


def stub_text(out_path):
    """What goes in-file when the record is too big for the container's slot:
    a pointer to the companion file that holds it."""
    return to_yaml({"format": FORMAT,
                    "sidecar": os.path.basename(sidecar_path(out_path))})


def write_sidecar(out_path, rec):
    """Write the companion file, returning its path (or None). A file's record
    also carries the file's own size and head hash, so a reader can tell when a
    companion has drifted from the file beside it — something an embedded
    record cannot do, as a file cannot hold a hash of itself."""
    if not rec:
        return None
    try:
        if os.path.isfile(out_path):
            fp = file_fingerprint(out_path) or {}
            out = rec["logical"]["output"]
            old = out.get("fingerprint") or {}
            out["fingerprint"] = {"size": fp.get("size"),
                                  "head64k_sha256": fp.get("head64k_sha256"),
                                  **old}
        path = sidecar_path(out_path)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(to_yaml(rec))
        os.replace(tmp, path)             # never leave a half-written file
        return path
    except Exception:
        return None


def _read_sidecar_text(out_path):
    try:
        with open(sidecar_path(out_path), encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


def record_text(path):
    """The record for `path` as text, from wherever it lives: a folder record,
    an embedded one, or a companion file. Callers never branch on mechanism."""
    if os.path.isdir(path):
        return _read_sidecar_text(path)
    text = None
    try:
        from vislang.output.save import read_embedded
        text = read_embedded(path)
    except Exception:
        pass
    if text:
        rec = parse(text)
        if rec and "sidecar" in rec and "logical" not in rec:
            text = None                   # a pointer: the companion holds it
    return text or _read_sidecar_text(path)


def record_for(path):
    """The record for `path` as a dict, or None."""
    rec = parse(record_text(path))
    return rec if rec and rec.get("format") == FORMAT else None
