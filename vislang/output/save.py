"""Write a materialized result to disk: convert on request, else preserve.

The output format follows the sink path's extension when it is a known one
(.npz / .h5 / .hdf5 / .hdf / .gio / .vti / .vtp / .vtk / .vtkhdf); with no
recognized extension it defaults to the SOURCE's original format so a round-trip
keeps the file type. So an extension IS the conversion request — `save(node,
"roi.vti")` over an HDF5 source converts, and this holds for a timeseries too,
where the extension names the per-timestep format inside the output folder.
Writers exist for HDF5, npz, GenericIO, and VTK; formats without a writer yet
(FITS, yt, raw) fall back to npz with a printed note — add a writer here to
extend.

Writers are Tier-0 only. A format read through a verified LLM-written adapter
can be converted OUT of — once `read_array` has passed its conformance gate the
data is plain numpy and is format-blind — but there is no way to generate a
writer, and that asymmetry is deliberate: reading has a deterministic oracle
(does the trusted library parse the real file), whereas trusting a generated
writer would need a round-trip oracle we do not have. See
`instructions/soundness.md`.

Conversion never RESAMPLES. Changing the container is a format change; turning a
point cloud into a grid, or a grid into points, is a computation with its own
parameters and error, so it is a blocker here rather than something a sink does
quietly.

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
               ".gio": "genericio",
               ".vti": "vtk", ".vtp": "vtk", ".vtk": "vtk", ".vtkhdf": "vtk"}
# Source filetype -> writer format key (formats we can round-trip). Anything not
# here falls back to npz.
_FILETYPE_FORMAT = {"HDF5": "hdf5", "npz": "npz", "GenericIO": "genericio",
                    "VTK": "vtk"}
# Writer format key -> default file extension. GenericIO snapshots are named by
# convention, not extension (`m000p-499.haloproperties`), so preserving that
# format leaves the path the spec gave us alone.
_FORMAT_EXT = {"hdf5": ".hdf5", "npz": ".npz", "genericio": "", "vtk": ".vti"}

# dtypes pygio.write_genericio accepts (per its own docstring).
_GIO_DTYPES = {np.dtype(t) for t in
               (np.float32, np.float64, np.int32, np.int64, np.uint16)}


# Every writer takes (out, loaded) where `loaded` is the materialized
# DatasetInfo. It used to be (out, data, attrs), which is enough for the formats
# that are bags of arrays but not for one that places its data in space: a VTK
# dataset needs `geometry` and `positions` too, and those live on the same
# object. DatasetInfo is already the currency every other consumer takes
# (output/render.py reads exactly this), so there is no new type here.

def _write_npz(out, loaded):
    np.savez(out, **loaded.data)


def _write_hdf5(out, loaded):
    import h5py
    with h5py.File(out, "w") as f:
        for name, arr in loaded.data.items():
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


def _write_genericio(out, loaded):
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
    attrs = getattr(loaded, "attributes", None) or {}
    cols = {name: np.ascontiguousarray(arr) for name, arr in loaded.data.items()}
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


# (_WRITERS is assembled below, once every writer above it is defined.)


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


def _genericio_blocker(loaded):
    """Why this result cannot be written as GenericIO, or None if it can.

    Order matters: the checks about THIS RESULT come first, and the check about
    this MACHINE comes last. A 3-D grid is not GenericIO-shaped no matter what
    pygio was built with, and saying so is the more useful diagnostic — reporting
    "pygio is read-only" for a result that could never be written either way
    sends the reader off to rebuild a library that was not the problem."""
    data = loaded.data
    attrs = getattr(loaded, "attributes", None) or {}
    if not data:
        return "no arrays to write"
    if _phys3(attrs.get("phys_scale")) is None:
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
    return _pygio_write_reason()          # environment last; None when writable


# ---------------------------------------------------------------------------
# VTK
# ---------------------------------------------------------------------------
# Which VTK dataset each extension can hold. `.vtk` (legacy) and `.vtkhdf` take
# either; `.vti` is a uniform grid and `.vtp` is a point set, and asking for the
# wrong one is a blocker rather than a silent conversion. Deliberately absent:
# `.vtr` / `.vts`, which need per-axis or explicit point coordinates that no
# source in the trust ladder currently provides — accepting them here would only
# let us fail later with a worse message.
_VTK_MODELS = {".vti": ("uniform",), ".vtp": ("points",),
               ".vtk": ("uniform", "points"), ".vtkhdf": ("uniform", "points")}


def _vtk_model(loaded):
    """(model, reason): 'uniform' or 'points' for this result, else None + why.

    Decided from the arrays themselves, the same way the rest of the pipeline
    infers modality — 3-D arrays are a grid, equal-length 1-D columns with
    coordinates are a point set."""
    data = loaded.data
    if not data:
        return None, "no arrays to write"
    ndims = {np.asarray(a).ndim for a in data.values()}
    if ndims == {3}:
        shapes = {np.asarray(a).shape for a in data.values()}
        if len(shapes) > 1:
            return None, f"3-D arrays have differing shapes {sorted(shapes)}"
        return "uniform", None
    if ndims == {1}:
        lengths = {np.asarray(a).shape[0] for a in data.values()}
        if len(lengths) > 1:
            return None, f"columns have unequal lengths {sorted(lengths)}"
        positions = getattr(loaded, "positions", None)
        if not positions:
            return None, "point output needs coordinate variables; none identified"
        missing = [p for p in positions if p not in data]
        if missing:
            return None, f"coordinate variable(s) {missing} not in the result"
        return "points", None
    return None, (f"arrays are {sorted(ndims)}-D; VTK output needs either 3-D "
                  f"grids or 1-D columns, not a mix")


def _vtk_default_ext(loaded):
    """The VTK extension that fits this result, for when none was requested
    (preserving a VTK source). A point set wants .vtp, a grid .vti; an
    undecidable result falls back to the table default and the blocker then
    reports why it cannot be written."""
    model, _ = _vtk_model(loaded)
    return {"points": ".vtp", "uniform": ".vti"}.get(model, _FORMAT_EXT["vtk"])


def _vtk_blocker(loaded, ext):
    """Why this result cannot be written to `ext`, or None if it can."""
    model, reason = _vtk_model(loaded)
    if reason:
        return reason
    allowed = _VTK_MODELS.get(ext, ())
    if model not in allowed:
        want = " or ".join(allowed)
        # Turning a point cloud into a grid (or back) is a RESAMPLING — a
        # computation with its own parameters and error. save() converts
        # containers; it does not quietly invent one modality from another.
        return (f"result is a {model} dataset and {ext} holds {want}; "
                f"converting between them is a resampling, not a format change")
    return None


def _write_vtk(out, loaded):
    """Write the result as VTK, choosing the dataset type from the extension.

    Grid arrays are raveled Fortran-order because VTK numbers points with x
    fastest, while the pipeline's axes are (x, y, z) = (0, 1, 2). Origin and
    spacing come from `loaded.geometry`, which materialize() has already shifted
    to account for any crop or stride — that is what puts a cropped block in the
    right place in world space. A result with no geometry is written in index
    space, which is a real vtkImageData and not an invented origin."""
    import pyvista as pv
    ext = os.path.splitext(out)[1].lower()
    model, _ = _vtk_model(loaded)
    geom = getattr(loaded, "geometry", None) or {}

    if model == "uniform":
        shape = np.asarray(next(iter(loaded.data.values()))).shape
        mesh = pv.ImageData(dimensions=shape,
                            origin=geom.get("origin", (0.0, 0.0, 0.0)),
                            spacing=geom.get("spacing", (1.0, 1.0, 1.0)))
        for name, arr in loaded.data.items():
            # NaNs from a grid threshold are written through: VTK understands
            # them and ParaView renders them as blanked.
            mesh.point_data[name] = np.ascontiguousarray(
                np.asarray(arr).ravel(order="F"))
    else:
        px, py, pz = loaded.positions
        pts = np.column_stack([np.asarray(loaded.data[p], dtype=np.float64)
                               for p in (px, py, pz)])
        mesh = pv.PolyData(pts)
        for name, arr in loaded.data.items():
            mesh.point_data[name] = np.ascontiguousarray(np.asarray(arr))

    if ext == ".vtkhdf":
        # pyvista's save() rejects .vtkhdf for ImageData (it is PolyData-only
        # there), so go through the VTK writer directly for both models.
        import vtk
        writer = vtk.vtkHDFWriter()
        writer.SetFileName(out)
        writer.SetInputData(mesh)
        if not writer.Write():
            raise RuntimeError(f"vtkHDFWriter failed to write {out}")
    else:
        mesh.save(out)


# Writer format key -> the writer. Every entry takes (out, loaded).
_WRITERS = {"npz": _write_npz, "hdf5": _write_hdf5, "genericio": _write_genericio,
            "vtk": _write_vtk}


# VTK extensions we deliberately do not write, and why. Without this they would
# fall through to source-format preservation and silently produce `out.vtu.hdf5`
# — the read path refuses them by name, so the write path should too.
_VTK_UNSUPPORTED = {
    ".vtu": "unstructured meshes need connectivity-aware region/subsample",
    ".vtr": "rectilinear output needs per-axis coordinate arrays, which "
            "DatasetInfo.geometry does not carry yet",
    ".vts": "curvilinear output needs explicit point coordinates, which "
            "DatasetInfo.geometry does not carry yet",
}


def _reject_unsupported_ext(path):
    """Raise on an extension we recognize as VTK but cannot write."""
    ext = os.path.splitext(path)[1].lower()
    if ext in _VTK_UNSUPPORTED:
        raise ValueError(f"cannot write {path}: {_VTK_UNSUPPORTED[ext]}. Use "
                         f".vti for a grid or .vtp for a point set.")


def _format_for_source(source_filetype):
    """The writer format to preserve a source's type, or 'npz' if we have no
    writer for it (with the caller free to note the fallback)."""
    return _FILETYPE_FORMAT.get(source_filetype, "npz")


def _resolve(path, source_filetype):
    """(fmt, out_path, explicit): the output format + final path, and whether the
    format came from an extension the caller wrote (which wins) rather than from
    preserving the source's format."""
    _reject_unsupported_ext(path)
    ext = os.path.splitext(path)[1].lower()
    if ext in _EXT_FORMAT:
        return _EXT_FORMAT[ext], path, True
    fmt = _format_for_source(source_filetype)
    want = _FORMAT_EXT[fmt]
    out = path if path.lower().endswith(want) else path + want
    return fmt, out, False


def save_loaded(loaded, path):
    """Write one materialized DatasetInfo to `path`, preserving its format (or
    honoring an explicit .npz/.hdf5/.gio/.vti/.vtp/.vtk/.vtkhdf extension on the
    path). Returns the path."""
    filetype = getattr(loaded, "filetype", None)
    fmt, out, explicit = _resolve(path, filetype)
    note = ""
    if fmt == "npz" and not explicit and filetype not in (None, "npz"):
        # An LLM-read source registers as "<Format> (LLM)", which no writer
        # claims — say so plainly rather than implying a writer might appear.
        how = " (LLM-read source)" if str(filetype).endswith(" (LLM)") else ""
        note = f" (no writer for {filetype}{how}; wrote npz)"
    elif fmt == "vtk":
        blocker = _vtk_blocker(loaded, os.path.splitext(out)[1].lower())
        if blocker is not None:
            if explicit:
                raise ValueError(f"cannot write {out} as VTK: {blocker}")
            fmt, out, _ = _resolve(path, "npz")
            note = f" (cannot write VTK: {blocker}; wrote npz)"
    elif fmt == "genericio":
        blocker = _genericio_blocker(loaded)
        if blocker is None:
            try:
                _write_genericio(out, loaded)
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
    _WRITERS[fmt](out, loaded)
    print(f"[save] wrote {len(loaded.data)} array(s) as {fmt} -> {out}{note}")
    return out


def save_timeseries(per_step, path, source_filetype):
    """Write a folder (timeseries) result: one file per timestep into a
    directory, named `timestep#N.<ext>`. The output folder is itself a valid
    timeseries readable by source()/timesteps().

    Format follows the SAME rule as the single-file case — an extension the
    caller wrote wins, else the source's format is preserved (npz fallback; for
    GenericIO the extension is empty, as its snapshots carry none). Since the
    sink of a series is a directory, an extension is how a conversion is
    requested: `save(node, "roi.vti")` over a series writes `roi/timestep#N.vti`.
    Without that there would be no way to ask for a converted series at all.

    The format decision is made for the WHOLE folder before anything is written,
    so a series is never a mix. A write that fails after that is raised rather
    than swallowed — unlike the single-file case, degrading halfway would leave
    a half-converted folder."""
    _reject_unsupported_ext(path)
    stem, want_ext = os.path.splitext(path)
    want_ext = want_ext.lower()
    explicit = want_ext in _EXT_FORMAT
    if explicit:
        fmt, path, ext = _EXT_FORMAT[want_ext], stem, want_ext
    else:
        fmt = _format_for_source(source_filetype)
        ext = _FORMAT_EXT[fmt]
    os.makedirs(path, exist_ok=True)

    note = ""
    if fmt == "npz" and not explicit and source_filetype not in (None, "npz"):
        note = f" (no writer for {source_filetype}; wrote npz)"
    elif fmt in ("genericio", "vtk"):
        if fmt == "genericio":
            label_fmt, checks = "GenericIO", (_genericio_blocker(l) for _, l in per_step)
        else:
            # Preserving a VTK series: name the files after what the result
            # actually is, so a point-cloud series round-trips as .vtp rather
            # than failing against the .vti default and degrading to npz.
            if not explicit:
                ext = _vtk_default_ext(per_step[0][1])
            label_fmt, checks = "VTK", (_vtk_blocker(l, ext) for _, l in per_step)
        # One format for the WHOLE folder: if any timestep cannot be written the
        # series degrades together, rather than becoming a mixed bag or leaving
        # a half-converted folder behind.
        blocker = next((b for b in checks if b), None)
        if blocker:
            if explicit:
                raise ValueError(f"cannot write {path}/ as {label_fmt}: {blocker}")
            fmt, ext = "npz", _FORMAT_EXT["npz"]
            note = f" (cannot write {label_fmt}: {blocker}; wrote npz)"

    for label, loaded in per_step:
        _WRITERS[fmt](os.path.join(path, f"timestep#{label}{ext}"), loaded)
    print(f"[save] wrote {len(per_step)} timestep(s) as {fmt} -> {path}/{note}")
    return path
