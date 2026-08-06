"""Write a materialized result to disk, preserving the source's format.

save() used to always emit .npz. Now the output format follows the sink path's
extension when it is a known one (.npz / .h5 / .hdf5 / .hdf / .gio); with no
recognized extension (or a folder target, for a timeseries), it defaults to the
SOURCE's original format so a round-trip keeps the file type. Writers exist for
HDF5, npz, and GenericIO; formats without a writer yet (FITS, yt, raw) fall back
to npz with a printed note — add a writer here to extend.

GenericIO is a *constrained* writer: pygio takes equal-length 1-D columns of a
few dtypes plus the box geometry (`phys_scale`/`phys_origin`, which inspect
already captures into `attributes`). When a result cannot satisfy that — a grid,
a dtype pygio rejects, a header with no box size — we do NOT invent the missing
metadata: format preservation degrades to npz with a note saying why. An
explicit `.gio` path is a request, not a default, so it raises instead.

A folder (timeseries) source writes one file per timestep into the output
directory, named `timestep#N.<ext>` (GenericIO files carry no extension), so the
result is itself a valid timeseries folder that source()/timesteps() can read
back.
"""

import os

import numpy as np

# Recognized output extensions -> writer format key.
_EXT_FORMAT = {".npz": "npz", ".h5": "hdf5", ".hdf5": "hdf5", ".hdf": "hdf5",
               ".gio": "genericio"}
# Source filetype -> writer format key (formats we can round-trip). Anything not
# here falls back to npz.
_FILETYPE_FORMAT = {"HDF5": "hdf5", "npz": "npz", "GenericIO": "genericio"}
# Writer format key -> default file extension. GenericIO snapshots are named by
# convention, not extension (`m000p-499.haloproperties`), so preserving that
# format leaves the path the spec gave us alone.
_FORMAT_EXT = {"hdf5": ".hdf5", "npz": ".npz", "genericio": ""}

# dtypes pygio.write_genericio accepts (per its own docstring).
_GIO_DTYPES = {np.dtype(t) for t in
               (np.float32, np.float64, np.int32, np.int64, np.uint16)}


def _write_npz(out, data, attrs):
    np.savez(out, **data)


def _write_hdf5(out, data, attrs):
    import h5py
    with h5py.File(out, "w") as f:
        for name, arr in data.items():
            f.create_dataset(name, data=np.asarray(arr))   # "/" in name -> nested groups


# Run in a child process (see _write_genericio). argv: cols.npz, out, meta-json.
_GIO_CHILD = """
import json, sys
import numpy as np, pygio
cols_path, out, meta = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
with np.load(cols_path) as z:
    cols = {name: z[name] for name in z.files}
pygio.write_genericio(out, cols, meta["scale"], meta["origin"])
"""


def _write_genericio(out, data, attrs):
    """One GenericIO file from equal-length 1-D columns. A partitioned source
    (`file#0…#7`, one per MPI rank) collapses to a single unpartitioned file —
    the ranks were a property of the write, not of the data, and pygio reads the
    result back the same way. Callers must clear `_genericio_blocker` first.

    The write runs in a short-lived CHILD process. pygio only exposes
    `write_genericio` from its MPI-enabled extension, and the reader imports the
    module with GENERICIO_NO_MPI set — which permanently drops the writer for
    that interpreter, so any process that has read a GenericIO file cannot write
    one. The child gets a clean env, and MPI_Init stays out of the session
    process (and out of any srun step it may be running in)."""
    import json
    import subprocess
    import sys
    import tempfile
    cols = {name: np.ascontiguousarray(arr) for name, arr in data.items()}
    meta = {"scale": _phys3(attrs.get("phys_scale")),
            "origin": _phys3(attrs.get("phys_origin")) or [0.0, 0.0, 0.0]}
    env = {k: v for k, v in os.environ.items() if k != "GENERICIO_NO_MPI"}
    with tempfile.TemporaryDirectory(prefix="vislang_gio_") as tmp:
        cols_path = os.path.join(tmp, "cols.npz")
        np.savez(cols_path, **cols)
        proc = subprocess.run(
            [sys.executable, "-c", _GIO_CHILD, cols_path, os.path.abspath(out),
             json.dumps(meta)],
            env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().split("\n")[-1]
        raise RuntimeError(f"pygio write failed: {tail}")


_WRITERS = {"npz": _write_npz, "hdf5": _write_hdf5, "genericio": _write_genericio}


def _phys3(value):
    """`value` as three floats (pygio's box geometry), or None if it isn't."""
    try:
        arr = np.asarray(value, dtype=float).reshape(-1)
    except (TypeError, ValueError):
        return None
    return [float(v) for v in arr] if arr.size == 3 else None


def _pygio_write_reason():
    """Why the installed pygio cannot write GenericIO at all, or None.

    `write_genericio` lives only in the MPI-enabled extension, so we look for
    that MODULE rather than the attribute — the attribute is missing whenever
    this process imported pygio in no-MPI mode, which says nothing about what a
    child process can do. GENERICIO_NO_MPI is pinned first so probing here never
    drags MPI_Init into this process (the reader sets it the same way)."""
    os.environ.setdefault("GENERICIO_NO_MPI", "true")
    try:
        import importlib.util
        if importlib.util.find_spec("pygio") is None:
            return "pygio is not installed"
        if importlib.util.find_spec("pygio.pygio_impl") is None:
            return "the installed pygio was built without MPI support (read-only)"
    except Exception as e:
        return f"pygio is unusable ({type(e).__name__}: {e})"
    return None


def _genericio_blocker(data, attrs):
    """Why this result cannot be written as GenericIO, or None if it can."""
    if not data:
        return "no arrays to write"
    unwritable = _pygio_write_reason()
    if unwritable:
        return unwritable
    if _phys3((attrs or {}).get("phys_scale")) is None:
        return "no phys_scale (box size) in the source header"
    lengths = set()
    for name, arr in data.items():
        arr = np.asarray(arr)
        if arr.ndim != 1:
            return f"{name} is {arr.ndim}-D (GenericIO stores 1-D columns)"
        if arr.dtype not in _GIO_DTYPES:
            return f"{name} has dtype {arr.dtype}, which pygio cannot write"
        lengths.add(arr.shape[0])
    if len(lengths) > 1:
        return f"columns have unequal lengths {sorted(lengths)}"
    return None


def _format_for_source(source_filetype):
    """The writer format to preserve a source's type, or 'npz' if we have no
    writer for it (with the caller free to note the fallback)."""
    return _FILETYPE_FORMAT.get(source_filetype, "npz")


def _resolve(path, source_filetype):
    """(fmt, out_path, explicit): the output format + final path, and whether the
    format came from an extension the caller wrote (which wins) rather than from
    preserving the source's format."""
    ext = os.path.splitext(path)[1].lower()
    if ext in _EXT_FORMAT:
        return _EXT_FORMAT[ext], path, True
    fmt = _format_for_source(source_filetype)
    want = _FORMAT_EXT[fmt]
    out = path if path.lower().endswith(want) else path + want
    return fmt, out, False


def save_loaded(loaded, path):
    """Write one materialized DatasetInfo to `path`, preserving its format (or
    honoring an explicit .npz/.hdf5/.gio extension on the path). Returns the
    path."""
    filetype = getattr(loaded, "filetype", None)
    attrs = getattr(loaded, "attributes", None) or {}
    fmt, out, explicit = _resolve(path, filetype)
    note = ""
    if fmt == "npz" and not explicit and filetype not in (None, "npz"):
        note = f" (no writer for {filetype}; wrote npz)"
    elif fmt == "genericio":
        blocker = _genericio_blocker(loaded.data, attrs)
        if blocker is None:
            try:
                _write_genericio(out, loaded.data, attrs)
                print(f"[save] wrote {len(loaded.data)} array(s) as genericio -> {out}")
                return out
            except Exception as e:
                if explicit:
                    raise
                blocker = f"{type(e).__name__}: {e}"   # never lose the result
        elif explicit:
            raise ValueError(f"cannot write {out} as GenericIO: {blocker}")
        fmt, out, _ = _resolve(path, "npz")            # degrade, saying why
        note = f" (cannot write GenericIO: {blocker}; wrote npz)"
    _WRITERS[fmt](out, loaded.data, attrs)
    print(f"[save] wrote {len(loaded.data)} array(s) as {fmt} -> {out}{note}")
    return out


def save_timeseries(per_step, path, source_filetype):
    """Write a folder (timeseries) result: one file per timestep into directory
    `path`, named `timestep#N.<ext>` in the source's format (npz fallback; for
    GenericIO the extension is empty, as its snapshots carry none). The output
    folder is itself a valid timeseries readable by source()/timesteps().

    The GenericIO decision is made for the WHOLE folder before anything is
    written, so a series is never a mix of formats. A write that fails after that
    (a pygio error on one timestep) is raised rather than swallowed — unlike the
    single-file case, degrading halfway would leave a half-GenericIO folder."""
    os.makedirs(path, exist_ok=True)
    fmt = _format_for_source(source_filetype)
    note = ""
    if fmt == "npz" and source_filetype not in (None, "npz"):
        note = f" (no writer for {source_filetype}; wrote npz)"
    elif fmt == "genericio":
        # One format for the whole folder: if ANY timestep can't be written as
        # GenericIO, the series degrades to npz rather than becoming a mixed bag.
        blocker = next((b for b in (_genericio_blocker(
            l.data, getattr(l, "attributes", None)) for _, l in per_step) if b), None)
        if blocker:
            fmt = "npz"
            note = f" (cannot write GenericIO: {blocker}; wrote npz)"
    ext = _FORMAT_EXT[fmt]
    for label, loaded in per_step:
        _WRITERS[fmt](os.path.join(path, f"timestep#{label}{ext}"), loaded.data,
                      getattr(loaded, "attributes", None) or {})
    print(f"[save] wrote {len(per_step)} timestep(s) as {fmt} -> {path}/{note}")
    return path
