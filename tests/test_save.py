"""save() format preservation, with the GenericIO writer under test.

Plain-python asserts (no pytest on the cluster). Run from the repo root:
    python tests/test_save.py

Covers: the format/extension resolution table, the GenericIO round-trip through
a real spec (single file and timeseries folder), an explicit extension winning
over preservation, and the degrade-with-a-reason path for results GenericIO
cannot hold. The GenericIO cases self-skip where the installed pygio is
read-only (no MPI build) — writing is impossible there by construction.
"""

import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datasetInfo import DatasetInfo
from dsl_forms.forms import source, threshold, timesteps, save
from planner import plan_pipeline
from my_inspect import inspect_file
import my_save

TMP = tempfile.mkdtemp(prefix="vislang_save_test_")
PASS = []
SKIP = []


def check(name, cond, detail=""):
    assert cond, f"{name}: {detail}"
    PASS.append(name)
    print(f"  ok  {name}")


def skip(name, why):
    SKIP.append(name)
    print(f"  -- {name} skipped: {why}")


def loaded_info(filetype, data, attrs=None):
    """A materialized DatasetInfo standing in for a planner result."""
    info = DatasetInfo("src", filetype, list(data), attributes=attrs or {})
    info.data = data
    info.loaded = True
    return info


PHYS = {"phys_scale": [64.0, 64.0, 64.0], "phys_origin": [0.0, 0.0, 0.0]}


def particles(n=100, offset=0.0):
    """GenericIO-writable columns: 1-D, equal length, pygio dtypes."""
    return {"x": np.linspace(0.0, 63.0, n, dtype=np.float32) + np.float32(offset),
            "y": np.linspace(63.0, 0.0, n, dtype=np.float32),
            "z": np.zeros(n, dtype=np.float32),
            "id": np.arange(n, dtype=np.int64)}


def make_gio(path, n=100, offset=0.0):
    """Write a synthetic GenericIO snapshot with the writer under test."""
    my_save._write_genericio(path, particles(n, offset), PHYS)
    return path


def read_gio(path):
    from adapters import GenericIOAdapter
    return GenericIOAdapter._read(path)


def run_save(node_path_pairs):
    """plan+execute one save() sink; returns the reported output path."""
    from dsl_forms import reset_sinks
    reset_sinks()
    node, out = node_path_pairs
    res = plan_pipeline(save(node, out), dry_run=False)
    return res["output"]


# ---------------------------------------------------------------------------
# Resolution table (no I/O)
# ---------------------------------------------------------------------------
def test_resolve():
    check("resolve_genericio_preserved_no_extension",
          my_save._resolve("out", "GenericIO") == ("genericio", "out", False))
    check("resolve_explicit_gio_wins",
          my_save._resolve("out.gio", "HDF5") == ("genericio", "out.gio", True))
    check("resolve_explicit_hdf5_beats_genericio_source",
          my_save._resolve("out.hdf5", "GenericIO") == ("hdf5", "out.hdf5", True))
    check("resolve_unknown_source_falls_back_to_npz",
          my_save._resolve("out", "FITS") == ("npz", "out.npz", False))


# ---------------------------------------------------------------------------
# Blockers: what GenericIO cannot hold, and how save() reacts
# ---------------------------------------------------------------------------
def test_blockers():
    check("blocker_grid_is_not_columns",
          "3-D" in my_save._genericio_blocker({"rho": np.zeros((4, 4, 4), np.float32)}, PHYS))
    check("blocker_bad_dtype",
          "dtype uint8" in my_save._genericio_blocker({"v": np.zeros(4, np.uint8)}, PHYS))
    check("blocker_unequal_lengths",
          "unequal" in my_save._genericio_blocker(
              {"a": np.zeros(4, np.float32), "b": np.zeros(5, np.float32)}, PHYS))
    check("blocker_missing_phys_scale",
          "phys_scale" in my_save._genericio_blocker({"a": np.zeros(4, np.float32)}, {}))
    check("no_blocker_for_writable_columns",
          my_save._genericio_blocker(particles(8), PHYS) is None
          or my_save._pygio_write_reason() is not None)

    # A grid from a GenericIO-typed source degrades to npz, saying why.
    grid = loaded_info("GenericIO", {"rho": np.zeros((4, 4, 4), np.float32)}, PHYS)
    out = my_save.save_loaded(grid, os.path.join(TMP, "grid_degrade"))
    check("degrade_to_npz_writes_npz", out.endswith(".npz") and os.path.exists(out))
    with np.load(out) as z:
        check("degrade_to_npz_keeps_data", z["rho"].shape == (4, 4, 4))

    # An explicit .gio is a request, not a default: it raises instead.
    try:
        my_save.save_loaded(grid, os.path.join(TMP, "grid.gio"))
        check("explicit_gio_raises_on_unwritable", False, "no error raised")
    except ValueError as e:
        check("explicit_gio_raises_on_unwritable", "GenericIO" in str(e), str(e))


# ---------------------------------------------------------------------------
# The other writers still preserve their formats (same 3-arg writer signature)
# ---------------------------------------------------------------------------
def test_other_formats():
    import h5py
    grid = {"rho": np.arange(64, dtype=np.float32).reshape(4, 4, 4)}
    out = my_save.save_loaded(loaded_info("HDF5", grid), os.path.join(TMP, "h5_pres"))
    check("hdf5_source_preserved", out.endswith(".hdf5"))
    with h5py.File(out, "r") as f:
        check("hdf5_source_data_kept", np.array_equal(f["rho"][...], grid["rho"]))

    out = my_save.save_loaded(loaded_info("npz", grid), os.path.join(TMP, "npz_pres"))
    check("npz_source_preserved", out.endswith(".npz"))

    out = my_save.save_loaded(loaded_info("FITS", grid), os.path.join(TMP, "fits_fallback"))
    check("no_writer_falls_back_to_npz", out.endswith(".npz") and os.path.exists(out))


# ---------------------------------------------------------------------------
# Round-trip through a real spec
# ---------------------------------------------------------------------------
def test_roundtrip():
    reason = my_save._pygio_write_reason()
    if reason:
        skip("genericio_roundtrip", reason)
        return
    src_path = make_gio(os.path.join(TMP, "snap"))
    info = inspect_file(src_path)
    check("inspect_reports_genericio", info.filetype == "GenericIO", info.filetype)
    check("inspect_captures_phys_scale",
          my_save._phys3(info.attributes.get("phys_scale")) == [64.0, 64.0, 64.0],
          str(info.attributes))

    # save() with no extension preserves GenericIO — and keeps the row cut.
    out = run_save((threshold(source(src_path), "x > 32"), os.path.join(TMP, "cut")))
    check("save_preserves_genericio_path", out == os.path.join(TMP, "cut"), out)
    with open(out, "rb") as f:
        check("save_wrote_hacc_magic", b"HACC" in f.read(64))
    back = read_gio(out)
    expected = particles()["x"]
    expected = expected[expected > 32]
    check("roundtrip_row_cut_preserved",
          np.array_equal(np.asarray(back["x"]), expected),
          f"{len(back['x'])} vs {len(expected)}")
    check("roundtrip_columns_preserved", set(back) == {"x", "y", "z", "id"}, str(list(back)))
    back_info = inspect_file(out)
    check("roundtrip_readable_as_genericio", back_info.filetype == "GenericIO")
    check("roundtrip_phys_scale_preserved",
          my_save._phys3(back_info.attributes.get("phys_scale")) == [64.0, 64.0, 64.0],
          str(back_info.attributes))

    # An explicit .hdf5 on a GenericIO source still wins.
    out5 = run_save((source(src_path), os.path.join(TMP, "as_h5.hdf5")))
    import h5py
    with h5py.File(out5, "r") as f:
        check("explicit_hdf5_beats_preservation", set(f.keys()) == {"x", "y", "z", "id"})


def test_timeseries_roundtrip():
    reason = my_save._pygio_write_reason()
    if reason:
        skip("genericio_timeseries", reason)
        return
    folder = os.path.join(TMP, "series")
    os.makedirs(folder, exist_ok=True)
    for step in (0, 1, 2):
        make_gio(os.path.join(folder, f"snap#{step}"), n=50, offset=float(step))

    out = run_save((timesteps(source(folder), 0, 1), os.path.join(TMP, "series_out")))
    names = sorted(os.listdir(out))
    check("timeseries_writes_extensionless_steps", names == ["timestep#0", "timestep#1"],
          str(names))
    with open(os.path.join(out, "timestep#0"), "rb") as f:
        check("timeseries_steps_are_genericio", b"HACC" in f.read(64))

    # The output folder is itself a readable timeseries.
    from my_inspect import timestep_files
    check("timeseries_output_is_a_timeseries",
          [lab for lab, _ in timestep_files(out)] == [0, 1])
    back = read_gio(os.path.join(out, "timestep#1"))
    check("timeseries_step_values_kept",
          np.allclose(np.asarray(back["x"]), particles(50, 1.0)["x"]))


if __name__ == "__main__":
    print("save() format preservation")
    test_resolve()
    test_blockers()
    test_other_formats()
    test_roundtrip()
    test_timeseries_roundtrip()
    print(f"\n{len(PASS)} passed, {len(SKIP)} skipped  (artifacts in {TMP})")
