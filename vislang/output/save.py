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
               ".vti": "vtk", ".vtp": "vtk", ".vtk": "vtk", ".vtkhdf": "vtk",
               ".nc": "netcdf4", ".nc4": "netcdf4"}
# Source filetype -> writer format key (formats we can round-trip). Anything not
# here falls back to npz.
_FILETYPE_FORMAT = {"HDF5": "hdf5", "npz": "npz", "GenericIO": "genericio",
                    "VTK": "vtk"}
# Writer format key -> default file extension. GenericIO snapshots are named by
# convention, not extension (`m000p-499.haloproperties`), so preserving that
# format leaves the path the spec gave us alone.
_FORMAT_EXT = {"hdf5": ".hdf5", "npz": ".npz", "genericio": "", "vtk": ".vti",
               "netcdf4": ".nc"}

# dtypes pygio.write_genericio accepts (per its own docstring).
_GIO_DTYPES = {np.dtype(t) for t in
               (np.float32, np.float64, np.int32, np.int64, np.uint16)}


# Every writer takes (out, loaded) where `loaded` is the materialized
# DatasetInfo. It used to be (out, data, attrs), which is enough for the formats
# that are bags of arrays but not for one that places its data in space: a VTK
# dataset needs `geometry` and `positions` too, and those live on the same
# object. DatasetInfo is already the currency every other consumer takes
# (output/render.py reads exactly this), so there is no new type here.
#
# A writer may also carry the provenance record into the file. The record is
# built before the write (it needs only the resolved path and format) and hung
# on `loaded.provenance`, because a VTK dataset has to have its field data set
# BEFORE it is serialized — there is no reopening an XML file to add it. A
# writer that embeds calls `_embedded(loaded)`; a record left unclaimed falls
# back to a companion file.

PROV_KEY = "sieve_provenance"


def _prov(loaded):
    """The record to embed, or None when provenance is off or detached."""
    return getattr(loaded, "provenance", None)


def _embedded(loaded):
    """Record that this artifact carries its record internally."""
    loaded.provenance_embedded = True


def _prov_blob(loaded, ascii=False):
    """(record as YAML text, {"history": line}) for embedding, or (None, {}).

    `ascii=True` for containers that refuse anything else (VTK string arrays)."""
    rec = _prov(loaded)
    if rec is None:
        return None, {}
    from vislang.runtime import provenance
    # CF-style: newest first, prepending anything the source carried, so a
    # chain of processing steps stays visible to ordinary tooling.
    flat = {"history": _history(loaded, rec)}
    return provenance.to_yaml(rec, ascii=ascii), flat


def _history(loaded, rec):
    logical = rec.get("logical") or {}
    sieve = logical.get("sieve") or {}
    what = (rec.get("result_summary") or "").split("\n")[0]
    line = (f"{(rec.get('realization') or {}).get('at')}: sieve "
            f"{sieve.get('version')} ({sieve.get('commit')}): {what}")
    prior = (getattr(loaded, "attributes", None) or {}).get("history")
    if isinstance(prior, bytes):
        prior = prior.decode("utf-8", "replace")
    return f"{line}\n{prior}" if prior else line


def _write_npz(out, loaded):
    blob, _ = _prov_blob(loaded)
    extra = {}
    if blob is not None:
        # A reserved key, skipped by the npz reader so it never surfaces as a
        # variable. np.savez needs arrays, hence the 0-d wrapper.
        extra[PROV_KEY] = np.array(blob)
        _embedded(loaded)
    np.savez(out, **loaded.data, **extra)


def _write_hdf5(out, loaded):
    import h5py
    blob, flat = _prov_blob(loaded)
    with h5py.File(out, "w") as f:
        for name, arr in loaded.data.items():
            f.create_dataset(name, data=np.asarray(arr))   # "/" -> groups
        if blob is not None:
            _attach_attrs(f.attrs, blob, flat, out, loaded)


# HDF5 rejects an attribute much past 64 KiB (the object header message cap),
# and netCDF-4 is HDF5 underneath. A record that large goes to a companion file
# and leaves a pointer behind — never a dataset, which would surface as a
# phantom variable on read-back.
_ATTR_MAX = 60_000


def _attach_attrs(attrs, blob, flat, out, loaded, setter=None):
    """Put the record (or, if it is too big, a pointer to its companion file)
    into an attribute set. Only a record that actually fits counts as embedded,
    so an oversized one still gets its companion written by _finish_provenance."""
    from vislang.runtime import provenance
    put = setter or attrs.__setitem__
    if len(blob.encode("utf-8")) > _ATTR_MAX:
        put(PROV_KEY, provenance.stub_text(out))
    else:
        put(PROV_KEY, blob)
        _embedded(loaded)
    for k, v in flat.items():
        put(k, v)


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
        # Distinguish "the header has no box" from "nothing ever read the
        # header". A remote result carries the source's attributes forward from
        # the cached schema, so an entry frozen before inspect captured them
        # arrives here indistinguishable from a genuinely box-less file — and
        # blaming the source sends the reader to look at the wrong thing.
        if attrs.get("remote_reduced") and "dtypes" not in attrs:
            return ("the remote schema reached here without header attributes, "
                    "so the box size is unknown (it may well be in the file); "
                    "re-run with the source reachable to re-inspect it")
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

    # Field data is arrays not tied to points or cells — where a VTK dataset
    # carries its metadata. Must be set before serialization; there is no
    # reopening an XML file to add it.
    # VTK's string arrays reject non-ASCII outright, hence the ASCII rendering.
    #
    # Legacy `.vtk` gets no record in-file: its reader's information pass scans
    # the file's words for keywords (DIMENSIONS, SPACING, ORIGIN), field data
    # included, so a spec comment like `# dimensions 302 302 302` would be read
    # as the grid's shape. Free text cannot ride safely there; the companion
    # file takes it.
    blob, _ = (None, {}) if ext == ".vtk" else _prov_blob(loaded, ascii=True)
    if blob is not None:
        mesh.field_data[PROV_KEY] = np.array([blob])
        if ext != ".vtkhdf":
            _embedded(loaded)    # .vtkhdf claims it only once the attach lands

    if ext == ".vtkhdf":
        # pyvista's save() rejects .vtkhdf for ImageData (it is PolyData-only
        # there), so go through the VTK writer directly for both models.
        import vtk
        writer = vtk.vtkHDFWriter()
        writer.SetFileName(out)
        writer.SetInputData(mesh)
        if not writer.Write():
            raise RuntimeError(f"vtkHDFWriter failed to write {out}")
        if blob is not None:
            _vtkhdf_attach(out, blob, loaded)
        return
    mesh.save(out)


def _vtkhdf_attach(out, blob, loaded):
    """Write the record into a .vtkhdf after VTK has closed it.

    vtkHDFWriter emits no FieldData group at all for ImageData (PolyData is
    fine), so anything set on the mesh is silently dropped for grids. A .vtkhdf
    is HDF5 underneath, so we reopen and attach directly. Both a FieldData
    dataset — picked up by readers that look there — and a plain attribute,
    which nothing can drop. Failure leaves the record unclaimed, so the
    companion file takes over rather than losing it."""
    try:
        import h5py
        with h5py.File(out, "r+") as f:
            grp = f["VTKHDF"]
            fd = grp.require_group("FieldData")
            if PROV_KEY in fd:
                del fd[PROV_KEY]
            fd.create_dataset(PROV_KEY, data=np.array([blob],
                                                      dtype=h5py.string_dtype()))
            _attach_attrs(grp.attrs, blob, {}, out, loaded)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# netCDF-4
# ---------------------------------------------------------------------------
# A netCDF-4 file IS an HDF5 file — the library adds a convention on top (named
# dimensions, _NCProperties, the `history` attribute), it does not change the
# container, and h5py still reads the result. `.nc` is therefore an explicit
# conversion target only: it is deliberately absent from _FILETYPE_FORMAT,
# because with no netCDF reader there is no source whose format it could
# preserve. `.hdf5` keeps going through h5py.

def _netcdf_blocker(loaded):
    """Why this result cannot be written as netCDF-4, or None if it can. Checks
    about the RESULT first, about this MACHINE last — the same order
    _genericio_blocker uses, and for the same reason."""
    model, reason = _vtk_model(loaded)      # same grid/points inference
    if reason:
        return reason
    try:
        import netCDF4  # noqa: F401
    except Exception:
        return "netCDF4 is not installed (pip install 'vislang[netcdf]')"
    return None


def _nc_name(name, taken):
    """netCDF has no path separator — `/` opens a group — so a variable called
    `native_fields/density` cannot be created at the root under that name. Flatten
    it, and keep the original in a per-variable attribute so nothing is lost."""
    base = name.replace("/", "_").lstrip("_") or "var"
    out, n = base, 1
    while out in taken:
        n += 1
        out = f"{base}_{n}"
    return out


def _write_netcdf4(out, loaded):
    """Write as netCDF-4: named dimensions, coordinate variables where geometry
    is known, and the provenance record as conventional attributes."""
    from netCDF4 import Dataset
    blob, flat = _prov_blob(loaded)
    model, _ = _vtk_model(loaded)
    geom = getattr(loaded, "geometry", None) or {}

    with Dataset(out, "w", format="NETCDF4") as ds:
        if model == "uniform":
            shape = np.asarray(next(iter(loaded.data.values()))).shape
            # Axis order is Sieve's own (x is axis 0), NOT CF's slowest-first.
            # The file has to agree with the region(x=...) that produced it;
            # silently transposing would make the record describe a different
            # array than the one stored. Stated rather than left implicit.
            axes = ("x", "y", "z")[:len(shape)]
            for ax, n in zip(axes, shape):
                ds.createDimension(ax, int(n))
            ds.sieve_axis_order = ",".join(axes) + " (index space, matching the " \
                                                   "DSL's region/subsample axes)"
            if geom.get("kind") == "uniform":
                # Coordinate variables only where the source actually stated a
                # geometry; index space is a real answer and an origin is never
                # synthesized.
                for i, ax in enumerate(axes):
                    cv = ds.createVariable(ax, "f8", (ax,))
                    cv[:] = geom["origin"][i] + np.arange(shape[i]) * geom["spacing"][i]
                    cv.axis = ax.upper()
            dims = axes
        else:
            n = len(next(iter(loaded.data.values())))
            ds.createDimension("particles", int(n))
            dims = ("particles",)

        taken = set(ds.variables)
        for name, arr in loaded.data.items():
            arr = np.asarray(arr)
            vname = _nc_name(name, taken)
            taken.add(vname)
            var = ds.createVariable(vname, arr.dtype, dims)
            var[...] = arr
            if vname != name:
                var.sieve_source_variable = name    # data, not provenance

        if blob is not None:
            _attach_attrs(None, blob, flat, out, loaded, setter=ds.setncattr)


# Writer format key -> the writer. Every entry takes (out, loaded).
_WRITERS = {"npz": _write_npz, "hdf5": _write_hdf5, "genericio": _write_genericio,
            "vtk": _write_vtk, "netcdf4": _write_netcdf4}


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


def _begin_provenance(loaded, out, fmt, step=None, degraded_from=None):
    """Build the record and hang it on `loaded` so the writer can embed it.

    Returns None whenever no run is open — which is what keeps the remote
    reducer (and any direct library call) from emitting records about transient
    files."""
    try:
        from vislang.runtime import provenance
        rec = provenance.record(loaded, out, fmt, step=step,
                                degraded_from=degraded_from)
        loaded.provenance = rec
        loaded.provenance_embedded = False
        return rec
    except Exception:
        return None                   # provenance must never break a save


def _finish_provenance(loaded, out):
    """Write a companion file if the writer did not embed the record; return a
    fragment for the save message."""
    rec = _prov(loaded)
    if not rec:
        return ""
    try:
        from vislang.runtime import provenance
        if getattr(loaded, "provenance_embedded", False):
            return "; provenance in-file"
        side = provenance.write_sidecar(out, rec)
        # The companion file is dot-prefixed and therefore invisible in `ls`, so
        # the save message has to name it or nobody learns it exists.
        return f"; provenance -> {os.path.basename(side)}" if side else ""
    except Exception:
        return ""
    finally:
        loaded.provenance = None
        loaded.provenance_embedded = False


def read_embedded(path):
    """The record text embedded in `path`, or None.

    Sniffs by extension then by content, so a caller never has to know which
    container it is holding. A pointer to a companion file comes back as-is;
    provenance.record_text follows it."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".npz":
            with np.load(path, allow_pickle=False) as z:
                if PROV_KEY in z.files:
                    return str(z[PROV_KEY])
            return None
        if ext in (".vti", ".vtp", ".vtk", ".vtr", ".vts"):
            import pyvista as pv
            fd = pv.read(path).field_data
            if PROV_KEY in fd:
                return str(np.asarray(fd[PROV_KEY])[0])
            return None
        # HDF5-family: plain HDF5, netCDF-4 and .vtkhdf are all HDF5 underneath,
        # so one reader covers all three — the record is a root attribute, or a
        # VTKHDF group attribute.
        import h5py
        with h5py.File(path, "r") as f:
            for holder in (f, f.get("VTKHDF")):
                if holder is None:
                    continue
                raw = holder.attrs.get(PROV_KEY)
                if raw is None:
                    continue
                # h5py hands back a str for its own variable-length attributes,
                # bytes for the fixed-length NC_CHAR ones netCDF writes for
                # ASCII text, and a one-element array for the NC_STRING it
                # writes for anything else.
                if isinstance(raw, np.ndarray):
                    raw = raw.reshape(-1)[0] if raw.size else None
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", "replace")
                return None if raw is None else str(raw)
    except Exception:
        return None
    return None


def save_loaded(loaded, path):
    """Write one materialized DatasetInfo to `path`, preserving its format (or
    honoring an explicit .npz/.hdf5/.gio/.vti/.vtp/.vtk/.vtkhdf extension on the
    path). Returns the path."""
    filetype = getattr(loaded, "filetype", None)
    fmt, out, explicit = _resolve(path, filetype)
    note = ""
    degraded = None            # the format we wanted, when we had to fall back
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
            degraded = "VTK"
            fmt, out, _ = _resolve(path, "npz")
            note = f" (cannot write VTK: {blocker}; wrote npz)"
    elif fmt == "genericio":
        blocker = _genericio_blocker(loaded)
        if blocker is None:
            try:
                # GenericIO returns here, before the shared tail below — which is
                # why provenance is handled at BOTH exits. A hook placed only
                # after the writer dispatch would miss the one format that has no
                # in-file slot at all.
                _begin_provenance(loaded, out, "genericio")
                _write_genericio(out, loaded)
                extra = _finish_provenance(loaded, out)
                print(f"[save] wrote {len(loaded.data)} array(s) as genericio "
                      f"-> {out}{extra}")
                return out
            except Exception as e:
                if explicit:
                    raise
                blocker = f"{type(e).__name__}: {e}"   # never lose the result
        elif explicit:
            raise ValueError(f"cannot write {out} as GenericIO: {blocker}")
        degraded = "GenericIO"
        fmt, out, _ = _resolve(path, "npz")            # degrade, saying why
        note = f" (cannot write GenericIO: {blocker}; wrote npz)"
    _begin_provenance(loaded, out, fmt, degraded_from=degraded)
    _WRITERS[fmt](out, loaded)
    note += _finish_provenance(loaded, out)
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
    degraded = None
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
            degraded = label_fmt
            fmt, ext = "npz", _FORMAT_EXT["npz"]
            note = f" (cannot write {label_fmt}: {blocker}; wrote npz)"

    for label, loaded in per_step:
        step_out = os.path.join(path, f"timestep#{label}{ext}")
        # Each step embeds its own record where the format allows, so a single
        # file lifted out of the folder still carries one. Where it cannot
        # (GenericIO), the folder record below lists the step: a companion file
        # per step would be enumerated by timeseries discovery.
        _begin_provenance(loaded, step_out, fmt, step=label, degraded_from=degraded)
        _WRITERS[fmt](step_out, loaded)
        loaded.provenance = None
    # Plus ONE folder-level record, naming every step in and out.
    from vislang.runtime import provenance
    side = provenance.write_sidecar(
        path, provenance.record_series(per_step, path, fmt, degraded_from=degraded))
    if side:
        note += f"; provenance -> {os.path.basename(side)}"
    print(f"[save] wrote {len(per_step)} timestep(s) as {fmt} -> {path}/{note}")
    return path
