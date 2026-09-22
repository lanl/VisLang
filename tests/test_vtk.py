"""VTK output: geometry, the model/extension match, and the round trip.

Plain-python asserts (no pytest on the cluster). Run from the repo root:
    python tests/test_vtk.py

The round trip IS the writer's oracle — we write with the writer under test and
read back with pyvista, the same way test_save.py validates the GenericIO writer
by reading it back with GenericIOAdapter. The case that matters most is
`roi_alignment`: a cropped, strided block must report the world position it came
from, or two timesteps' ROIs will not line up in ParaView.

Self-skips when pyvista is not installed (it is an optional extra).
"""

import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vislang.formats.dataset_info import (DatasetInfo, uniform_geometry,
                                          geometry_from_edges, narrowed_geometry)
from vislang.interpreter.narrowing import AxisRange
from vislang.output import save as save_mod

TMP = tempfile.mkdtemp(prefix="vislang_vtk_test_")
PASS = []
SKIP = []


def check(name, cond, detail=""):
    assert cond, f"{name}: {detail}"
    PASS.append(name)
    print(f"  ok  {name}")


def skip(name, why):
    SKIP.append(name)
    print(f"  -- {name} skipped: {why}")


def have_pyvista():
    try:
        import pyvista  # noqa: F401
        return None
    except Exception as e:
        return f"pyvista not installed ({type(e).__name__})"


def grid_info(shape=(3, 4, 5), geometry=None, filetype="HDF5"):
    """A materialized grid result."""
    data = {"rho": np.arange(int(np.prod(shape)), dtype=np.float32).reshape(shape)}
    info = DatasetInfo("src", filetype, list(data))
    info.dimensions = {"grid": tuple(shape)}
    info.data = data
    info.loaded = True
    info.geometry = geometry
    return info


def point_info(n=6, filetype="GenericIO"):
    """A materialized point-cloud result, with positions identified."""
    data = {"x": np.linspace(0.0, 5.0, n, dtype=np.float32),
            "y": np.linspace(5.0, 0.0, n, dtype=np.float32),
            "z": np.zeros(n, dtype=np.float32),
            "rho": np.arange(n, dtype=np.float32)}
    info = DatasetInfo("src", filetype, list(data))
    info.dimensions = {"particles": n}
    info.data = data
    info.loaded = True
    info.positions = ("x", "y", "z")
    return info


# ---------------------------------------------------------------------------
# Geometry algebra — pure, no pyvista needed
# ---------------------------------------------------------------------------
def test_geometry():
    check("uniform_geometry_builds",
          uniform_geometry((1, 2, 3), (0.5, 0.5, 0.5))
          == {"kind": "uniform", "origin": (1.0, 2.0, 3.0), "spacing": (0.5, 0.5, 0.5)})
    check("uniform_geometry_rejects_short",
          uniform_geometry((1, 2), (1, 1, 1)) is None)
    check("uniform_geometry_rejects_nonfinite",
          uniform_geometry((1, 2, float("nan")), (1, 1, 1)) is None)

    # yt states a domain box + cell counts; spacing is (hi-lo)/n.
    check("geometry_from_edges",
          geometry_from_edges([0, 0, 0], [10, 10, 10], [10, 5, 2])
          == {"kind": "uniform", "origin": (0.0, 0.0, 0.0), "spacing": (1.0, 2.0, 5.0)})
    check("geometry_from_edges_rejects_zero_dims",
          geometry_from_edges([0, 0, 0], [1, 1, 1], [0, 1, 1]) is None)

    # The crop/stride algebra: origin shifts by start*spacing, spacing scales by step.
    g = uniform_geometry((0.0, 0.0, 0.0), (2.0, 2.0, 2.0))
    narrowed = narrowed_geometry(g, [AxisRange(100, 220, 2), AxisRange(40, 160, 1),
                                     AxisRange(None, None, 1)])
    check("narrowed_origin_shifts", narrowed["origin"] == (200.0, 80.0, 0.0), narrowed)
    check("narrowed_spacing_scales", narrowed["spacing"] == (4.0, 2.0, 2.0), narrowed)

    check("narrowed_geometry_none_stays_none",
          narrowed_geometry(None, [AxisRange(1, 2, 1)]) is None)
    check("narrowed_geometry_no_ranges_unchanged",
          narrowed_geometry(g, None) == g)
    check("narrowed_geometry_rank_mismatch_unchanged",
          narrowed_geometry(g, [AxisRange(1, 2, 1)]) == g)


# ---------------------------------------------------------------------------
# Which results each extension can hold
# ---------------------------------------------------------------------------
def test_model_and_blockers():
    check("resolve_vti", save_mod._resolve("out.vti", "HDF5") == ("vtk", "out.vti", True))
    check("resolve_vtp", save_mod._resolve("out.vtp", "HDF5") == ("vtk", "out.vtp", True))
    check("resolve_vtkhdf",
          save_mod._resolve("out.vtkhdf", "GenericIO") == ("vtk", "out.vtkhdf", True))
    check("resolve_vtk_source_preserved",
          save_mod._resolve("out", "VTK") == ("vtk", "out.vti", False))

    check("model_grid_is_uniform", save_mod._vtk_model(grid_info())[0] == "uniform")
    check("model_columns_are_points", save_mod._vtk_model(point_info())[0] == "points")

    no_pos = point_info()
    no_pos.positions = None
    check("model_points_need_coords",
          "coordinate" in save_mod._vtk_model(no_pos)[1])

    ragged = point_info()
    ragged.data["rho"] = np.zeros(3, dtype=np.float32)
    check("model_rejects_unequal_columns",
          "unequal" in save_mod._vtk_model(ragged)[1])

    mixed = grid_info()
    mixed.data["flat"] = np.zeros(4, dtype=np.float32)
    check("model_rejects_mixed_rank", "mix" in save_mod._vtk_model(mixed)[1])

    # A grid cannot be a .vtp and a cloud cannot be a .vti: that is a resampling.
    check("blocker_grid_into_vtp",
          "resampling" in save_mod._vtk_blocker(grid_info(), ".vtp"))
    check("blocker_points_into_vti",
          "resampling" in save_mod._vtk_blocker(point_info(), ".vti"))
    check("no_blocker_grid_into_vti",
          save_mod._vtk_blocker(grid_info(), ".vti") is None)
    check("no_blocker_points_into_vtp",
          save_mod._vtk_blocker(point_info(), ".vtp") is None)
    check("vtkhdf_takes_either",
          save_mod._vtk_blocker(grid_info(), ".vtkhdf") is None
          and save_mod._vtk_blocker(point_info(), ".vtkhdf") is None)

    # Extensions we recognize as VTK but cannot write must say so, not fall
    # through to source preservation and produce "out.vtu.hdf5".
    for bad, why in ((".vtu", "connectivity"), (".vtr", "coordinate"),
                     (".vts", "coordinate")):
        try:
            save_mod._resolve("out" + bad, "HDF5")
            check(f"reject{bad}", False, "no error raised")
        except ValueError as e:
            check(f"reject{bad}", why in str(e) and ".vti" in str(e), str(e))


# ---------------------------------------------------------------------------
# Round trip: write with the writer under test, read back with pyvista
# ---------------------------------------------------------------------------
def test_roundtrip():
    reason = have_pyvista()
    if reason:
        skip("vtk_roundtrip", reason)
        return
    import pyvista as pv

    shape = (3, 4, 5)
    info = grid_info(shape, uniform_geometry((1.0, 2.0, 3.0), (0.5, 0.5, 0.5)))
    out = save_mod.save_loaded(info, os.path.join(TMP, "grid.vti"))
    check("vti_written", out.endswith(".vti") and os.path.exists(out))

    back = pv.read(out)
    check("vti_dimensions", tuple(back.dimensions) == shape, back.dimensions)
    check("vti_origin", tuple(back.origin) == (1.0, 2.0, 3.0), back.origin)
    check("vti_spacing", tuple(back.spacing) == (0.5, 0.5, 0.5), back.spacing)
    # Fortran ravel on write must invert exactly: VTK numbers points x-fastest.
    got = np.asarray(back.point_data["rho"]).reshape(shape, order="F")
    check("vti_values_roundtrip", np.array_equal(got, info.data["rho"]))

    # No geometry -> index space, which is a real ImageData, not an invented origin.
    plain = save_mod.save_loaded(grid_info(shape), os.path.join(TMP, "plain.vti"))
    b2 = pv.read(plain)
    check("vti_no_geometry_is_index_space",
          tuple(b2.origin) == (0.0, 0.0, 0.0) and tuple(b2.spacing) == (1.0, 1.0, 1.0))

    pts = point_info(6)
    outp = save_mod.save_loaded(pts, os.path.join(TMP, "cloud.vtp"))
    bp = pv.read(outp)
    check("vtp_point_count", bp.n_points == 6, bp.n_points)
    check("vtp_coords_roundtrip",
          np.allclose(np.asarray(bp.points)[:, 0], pts.data["x"]))
    check("vtp_scalar_roundtrip",
          np.allclose(np.asarray(bp.point_data["rho"]), pts.data["rho"]))

    outh = save_mod.save_loaded(grid_info(shape), os.path.join(TMP, "grid.vtkhdf"))
    bh = pv.read(outh)
    check("vtkhdf_roundtrip", tuple(bh.dimensions) == shape, bh.dimensions)


# ---------------------------------------------------------------------------
# The case this whole feature exists for: a cropped ROI lands where it was cut
# ---------------------------------------------------------------------------
def test_roi_alignment():
    reason = have_pyvista()
    if reason:
        skip("roi_alignment", reason)
        return
    import pyvista as pv
    from vislang.interpreter.load import materialize
    from vislang.interpreter.narrowing import Narrowing

    # A source grid with a real world position, narrowed the way a spec would:
    # region(x=(2,8)) + subsample(x=2).
    src = grid_info((10, 10, 10), uniform_geometry((100.0, 200.0, 300.0),
                                                   (2.0, 2.0, 2.0)))
    src.loaded = False
    src.data = {}
    full = np.arange(1000, dtype=np.float32).reshape(10, 10, 10)

    class _Stub:
        """Stands in for an adapter so this test stays about geometry, not I/O."""
        name = "HDF5"

        def read_array(self, filepath, location, selection):
            return full[selection.indexer(3, full.shape[0])]

    import vislang.interpreter.load as load_mod
    real = load_mod.get_adapter_for_info
    load_mod.get_adapter_for_info = lambda info: _Stub()
    try:
        narrowing = Narrowing(grid_ranges=[AxisRange(2, 8, 2), AxisRange(None, None, 1),
                                           AxisRange(None, None, 1)],
                              project=("rho",), dimensions={"grid": (10, 10, 10)})
        loaded = materialize(src, narrowing)
    finally:
        load_mod.get_adapter_for_info = real

    check("materialize_shifts_origin",
          loaded.geometry["origin"] == (104.0, 200.0, 300.0), loaded.geometry)
    check("materialize_scales_spacing",
          loaded.geometry["spacing"] == (4.0, 2.0, 2.0), loaded.geometry)

    out = save_mod.save_loaded(loaded, os.path.join(TMP, "roi.vti"))
    back = pv.read(out)
    check("roi_vti_origin_is_the_cut", tuple(back.origin) == (104.0, 200.0, 300.0),
          back.origin)
    check("roi_vti_spacing_is_the_stride", tuple(back.spacing) == (4.0, 2.0, 2.0),
          back.spacing)
    # The last cell of the ROI must sit at the same world point as in the source.
    nx = back.dimensions[0]
    check("roi_last_cell_matches_source",
          back.origin[0] + (nx - 1) * back.spacing[0] == 100.0 + 6 * 2.0,
          f"{back.origin[0]} + {nx - 1}*{back.spacing[0]}")


# ---------------------------------------------------------------------------
# Degrade vs raise, and the timeseries extension rule
# ---------------------------------------------------------------------------
def test_degrade_and_series():
    reason = have_pyvista()
    if reason:
        skip("vtk_degrade_and_series", reason)
        return

    # Explicit extension that cannot hold the result: a request, so it raises.
    try:
        save_mod.save_loaded(point_info(), os.path.join(TMP, "cloud.vti"))
        check("explicit_vti_raises_on_points", False, "no error raised")
    except ValueError as e:
        check("explicit_vti_raises_on_points", "resampling" in str(e), str(e))

    # A VTK-typed source whose result VTK cannot hold degrades with a reason.
    mixed = grid_info(filetype="VTK")
    mixed.data["flat"] = np.zeros(4, dtype=np.float32)
    out = save_mod.save_loaded(mixed, os.path.join(TMP, "mixed_degrade"))
    check("degrade_to_npz", out.endswith(".npz") and os.path.exists(out))

    # A series converts when the path carries an extension, and the folder is
    # itself a readable timeseries.
    series = [("0", grid_info((3, 4, 5))), ("1", grid_info((3, 4, 5)))]
    folder = save_mod.save_timeseries(series, os.path.join(TMP, "roi_series.vti"), "HDF5")
    check("series_folder_drops_extension", folder.endswith("roi_series"), folder)
    names = sorted(os.listdir(folder))
    check("series_files_named_by_convention",
          names == ["timestep#0.vti", "timestep#1.vti"], names)

    # No extension -> preserve the source format, unchanged behavior.
    plain = save_mod.save_timeseries(series, os.path.join(TMP, "plain_series"), "HDF5")
    check("series_without_extension_preserves",
          sorted(os.listdir(plain)) == ["timestep#0.hdf5", "timestep#1.hdf5"],
          os.listdir(plain))

    # An explicit series extension that cannot hold the result raises, rather
    # than writing half a folder.
    pts_series = [("0", point_info()), ("1", point_info())]
    try:
        save_mod.save_timeseries(pts_series, os.path.join(TMP, "bad.vti"), "GenericIO")
        check("series_explicit_mismatch_raises", False, "no error raised")
    except ValueError as e:
        check("series_explicit_mismatch_raises", "resampling" in str(e), str(e))

    # Preserving a VTK series picks the extension from what the result IS, so a
    # point-cloud series round-trips as .vtp instead of hitting the .vti default
    # and degrading to npz.
    vtk_pts = [("0", point_info(filetype="VTK")), ("1", point_info(filetype="VTK"))]
    pf = save_mod.save_timeseries(vtk_pts, os.path.join(TMP, "vtk_pts_series"), "VTK")
    check("series_preserve_points_uses_vtp",
          sorted(os.listdir(pf)) == ["timestep#0.vtp", "timestep#1.vtp"],
          os.listdir(pf))
    vtk_grid = [("0", grid_info(filetype="VTK")), ("1", grid_info(filetype="VTK"))]
    gf = save_mod.save_timeseries(vtk_grid, os.path.join(TMP, "vtk_grid_series"), "VTK")
    check("series_preserve_grid_uses_vti",
          sorted(os.listdir(gf)) == ["timestep#0.vti", "timestep#1.vti"],
          os.listdir(gf))


# ---------------------------------------------------------------------------
# Reading VTK: dispatch, schema, and a full spec over a .vti source
# ---------------------------------------------------------------------------
def test_reader():
    reason = have_pyvista()
    if reason:
        skip("vtk_reader", reason)
        return
    import pyvista as pv
    from vislang.formats.adapters import get_adapter, VTKAdapter, HDF5Adapter
    from vislang.formats.inspect import inspect_file

    src = np.arange(60, dtype=np.float32).reshape(3, 4, 5)
    g = pv.ImageData(dimensions=(3, 4, 5), origin=(1.0, 2.0, 3.0),
                     spacing=(0.5, 0.5, 0.5))
    g.point_data["rho"] = src.ravel(order="F")
    vti = os.path.join(TMP, "read.vti")
    g.save(vti)

    check("dispatch_vti_to_vtk", isinstance(get_adapter(vti), VTKAdapter))
    info = inspect_file(vti)
    check("read_filetype", info.filetype == "VTK", info.filetype)
    check("read_dimensions", info.dimensions == {"grid": (3, 4, 5)}, info.dimensions)
    check("read_geometry",
          info.geometry == {"kind": "uniform", "origin": (1.0, 2.0, 3.0),
                            "spacing": (0.5, 0.5, 0.5)}, info.geometry)

    # .vtkhdf is HDF5 underneath — it must not be claimed by HDF5Adapter, and a
    # plain HDF5 file must not be claimed by VTKAdapter.
    import vtk as _vtk
    hp = os.path.join(TMP, "read.vtkhdf")
    w = _vtk.vtkHDFWriter()
    w.SetFileName(hp)
    w.SetInputData(g)
    w.Write()
    check("dispatch_vtkhdf_to_vtk", isinstance(get_adapter(hp), VTKAdapter))

    import h5py
    plain = os.path.join(TMP, "plain.h5")
    with h5py.File(plain, "w") as f:
        f.create_dataset("rho", data=np.zeros((4, 4, 4), np.float32))
    check("dispatch_plain_hdf5_unaffected",
          isinstance(get_adapter(plain), HDF5Adapter))

    # A point set exposes its coordinates as variables so positions are found.
    pts = pv.PolyData(np.column_stack([np.arange(5.0), np.arange(5.0), np.zeros(5)]))
    pts.point_data["rho"] = np.arange(5, dtype=np.float32)
    vtp = os.path.join(TMP, "read.vtp")
    pts.save(vtp)
    pinfo = inspect_file(vtp)
    check("read_points_dimensions", pinfo.dimensions == {"particles": 5})
    check("read_points_positions", pinfo.positions == ("x", "y", "z"), pinfo.positions)

    # An unstructured mesh is refused with a message naming the scope limit.
    from vislang.formats.adapters import UnsupportedFormatError
    ug = g.cast_to_unstructured_grid()
    vtu = os.path.join(TMP, "mesh.vtu")
    ug.save(vtu)
    try:
        inspect_file(vtu)
        check("unstructured_refused", False, "no error raised")
    except UnsupportedFormatError as e:
        check("unstructured_refused", "connectivity" in str(e), str(e))

    # Full spec over a VTK source: read .vti, crop, write .vti back.
    from vislang.dsl import reset_sinks
    from vislang.dsl.forms import source, region, save as save_form
    from vislang.interpreter.planner import plan_pipeline
    reset_sinks()
    out = os.path.join(TMP, "spec_roi.vti")
    res = plan_pipeline(save_form(region(source(vti), x=(1, 3)), out), dry_run=False)
    back = pv.read(res["output"])
    check("spec_roi_dimensions", tuple(back.dimensions) == (2, 4, 5), back.dimensions)
    check("spec_roi_origin_is_the_cut", tuple(back.origin) == (1.5, 2.0, 3.0),
          back.origin)
    got = np.asarray(back.point_data["rho"]).reshape(back.dimensions, order="F")
    check("spec_roi_values", np.array_equal(got, src[1:3]))


if __name__ == "__main__":
    print("VTK output")
    test_geometry()
    test_model_and_blockers()
    test_roundtrip()
    test_roi_alignment()
    test_degrade_and_series()
    test_reader()
    print(f"\n{len(PASS)} passed, {len(SKIP)} skipped  (artifacts in {TMP})")
