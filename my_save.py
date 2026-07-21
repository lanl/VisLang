"""Write a materialized result to disk, preserving the source's format.

save() used to always emit .npz. Now the output format follows the sink path's
extension when it is a known one (.npz / .h5 / .hdf5 / .hdf); with no recognized
extension (or a folder target, for a timeseries), it defaults to the SOURCE's
original format so a round-trip keeps the file type. Writers exist for HDF5 (the
common case) and npz; formats without a writer yet (FITS, GenericIO, yt, raw)
fall back to npz with a printed note — add a writer here to extend.

A folder (timeseries) source writes one file per timestep into the output
directory, named `timestep#N.<ext>`, so the result is itself a valid timeseries
folder that source()/timesteps() can read back.
"""

import os

import numpy as np

# Recognized output extensions -> writer format key.
_EXT_FORMAT = {".npz": "npz", ".h5": "hdf5", ".hdf5": "hdf5", ".hdf": "hdf5"}
# Source filetype -> writer format key (formats we can round-trip). Anything not
# here falls back to npz.
_FILETYPE_FORMAT = {"HDF5": "hdf5", "npz": "npz"}
# Writer format key -> default file extension.
_FORMAT_EXT = {"hdf5": ".hdf5", "npz": ".npz"}


def _write_npz(out, data):
    np.savez(out, **data)


def _write_hdf5(out, data):
    import h5py
    with h5py.File(out, "w") as f:
        for name, arr in data.items():
            f.create_dataset(name, data=np.asarray(arr))   # "/" in name -> nested groups


_WRITERS = {"npz": _write_npz, "hdf5": _write_hdf5}


def _format_for_source(source_filetype):
    """The writer format to preserve a source's type, or 'npz' if we have no
    writer for it (with the caller free to note the fallback)."""
    return _FILETYPE_FORMAT.get(source_filetype, "npz")


def _resolve(path, source_filetype):
    """(fmt, out_path): the output format + final path. A recognized extension on
    `path` wins; otherwise preserve the source format and ensure that extension."""
    ext = os.path.splitext(path)[1].lower()
    if ext in _EXT_FORMAT:
        return _EXT_FORMAT[ext], path
    fmt = _format_for_source(source_filetype)
    want = _FORMAT_EXT[fmt]
    out = path if path.lower().endswith(want) else path + want
    return fmt, out


def save_loaded(loaded, path):
    """Write one materialized DatasetInfo to `path`, preserving its format (or
    honoring an explicit .npz/.hdf5 extension on the path). Returns the path."""
    fmt, out = _resolve(path, getattr(loaded, "filetype", None))
    note = ""
    if fmt == "npz" and _format_for_source(getattr(loaded, "filetype", None)) == "npz" \
            and getattr(loaded, "filetype", None) not in (None, "npz") \
            and not path.lower().endswith(".npz"):
        note = f" (no writer for {loaded.filetype}; wrote npz)"
    _WRITERS[fmt](out, loaded.data)
    print(f"[save] wrote {len(loaded.data)} array(s) as {fmt} -> {out}{note}")
    return out


def save_timeseries(per_step, path, source_filetype):
    """Write a folder (timeseries) result: one file per timestep into directory
    `path`, named `timestep#N.<ext>` in the source's format (npz fallback). The
    output folder is itself a valid timeseries readable by source()/timesteps()."""
    os.makedirs(path, exist_ok=True)
    fmt = _format_for_source(source_filetype)
    ext = _FORMAT_EXT[fmt]
    note = "" if (fmt != "npz" or source_filetype in (None, "npz")) \
        else f" (no writer for {source_filetype}; wrote npz)"
    for label, loaded in per_step:
        _WRITERS[fmt](os.path.join(path, f"timestep#{label}{ext}"), loaded.data)
    print(f"[save] wrote {len(per_step)} timestep(s) as {fmt} -> {path}/{note}")
    return path
