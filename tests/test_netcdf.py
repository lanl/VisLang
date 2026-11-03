"""netCDF-3 (classic) through the whole pipeline: the reader, region/subsample/
fields/threshold, saving, a series, and the simulated remote.

The fixtures are written with the netCDF4 library itself, in the layout of the
SciVis 2021 mantle files: three fields on (lat, r, lon), coordinate variables
for each axis, a field name with spaces. One file has uneven axes (placed in
cells), one has even increasing axes (placed in world coordinates).

    /opt/miniconda3/envs/vislang/bin/python tests/test_netcdf.py
"""
import os
import shutil
import sys

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import netCDF4

import test_geometry as tg
from test_geometry import check, raises, read_grid, run, TMP
from fakes_remote import EXECS, install_fake_remote, install_login_inspect
from vislang.dsl import reset_sinks
from vislang.dsl.forms import source, region, fields, save, subsample, threshold, timesteps
from vislang.formats.adapters import NetCDFAdapter, HDF5Adapter, get_adapter
from vislang.formats.inspect import inspect_file

SHAPE = (18, 20, 36)                         # lat, r, lon
NAMES = ("temperature", "vx", "thermal conductivity")


def values(t=0):
    n = int(np.prod(SHAPE))
    base = np.arange(n, dtype=np.float32).reshape(SHAPE)
    return {"temperature": base + 1000 * t, "vx": -base - t,
            "thermal conductivity": np.sin(base / 7 + t).astype(np.float32)}


def write_nc3(path, t=0, even=False, fmt="NETCDF3_64BIT_OFFSET"):
    """A mantle-like file. Uneven axes: lat falls 90 -> -90 with half steps at
    the poles, r has half steps at both shells. Even axes: all increasing."""
    if even:
        lat = np.linspace(-85.0, 85.0, SHAPE[0])
        r = np.linspace(3500.0, 6300.0, SHAPE[1])
    else:
        lat = np.r_[90.0, np.linspace(85.0, -85.0, SHAPE[0] - 2), -90.0]
        r = np.r_[3485.0, np.linspace(3500.0, 6350.0, SHAPE[1] - 2), 6371.0]
    lon = np.linspace(0.0, 350.0, SHAPE[2])
    with netCDF4.Dataset(path, "w", format=fmt) as ds:
        for d, n in zip(("lat", "r", "lon"), SHAPE):
            ds.createDimension(d, n)
        for d, a, u in (("lon", lon, "degrees_east"), ("r", r, "km"), ("lat", lat, "degrees_north")):
            cv = ds.createVariable(d, "f8", (d,))
            cv[:] = a
            cv.units = u
        for name, arr in values(t).items():
            ds.createVariable(name, "f4", ("lat", "r", "lon"))[...] = arr
        ds.title = "mantle-like test"
    return path


def test():
    print("== the netCDF-3 reader ==")
    uneven = write_nc3(os.path.join(TMP, "uneven.nc"))
    even = write_nc3(os.path.join(TMP, "even.nc"), even=True)
    cdf1 = write_nc3(os.path.join(TMP, "classic.nc"), fmt="NETCDF3_CLASSIC")
    nc4 = write_nc3(os.path.join(TMP, "four.nc"), fmt="NETCDF4")

    check("a 64-bit-offset file goes to the netCDF reader",
          isinstance(get_adapter(uneven), NetCDFAdapter))
    check("a CDF-1 classic file too", isinstance(get_adapter(cdf1), NetCDFAdapter))
    check("a netCDF-4 file stays with the HDF5 reader", isinstance(get_adapter(nc4), HDF5Adapter))

    info = inspect_file(uneven)
    check("inspect: the three fields, not the coordinate variables",
          sorted(info.variables) == sorted(NAMES), info.variables)
    check("inspect: one grid of the fields' shape", info.dimensions.get("grid") == SHAPE,
          info.dimensions)
    check("inspect: item sizes from the header", info.itemsizes == {n: 4 for n in NAMES},
          info.itemsizes)
    check("inspect: each axis described, uneven ones said so",
          "uneven spacing" in info.attributes["axis_lat"]
          and "uneven spacing" in info.attributes["axis_r"]
          and "every 10" in info.attributes["axis_lon"], info.attributes)
    check("inspect: global attributes and the variant kept",
          info.attributes.get("title") == "mantle-like test"
          and info.attributes.get("netcdf_format") == "NETCDF3_64BIT_OFFSET")
    check("inspect: uneven axes state no geometry (the grid is placed in cells)",
          info.geometry is None, info.geometry)
    g = inspect_file(even).geometry
    check("inspect: even increasing axes state origin + spacing in lat, r, lon order",
          g and g["kind"] == "uniform" and np.allclose(g["origin"], (-85.0, 3500.0, 0.0))
          and np.allclose(g["spacing"], (10.0, 2800.0 / 19, 10.0)), g)

    print("== cut, saved, compared with the stored arrays ==")
    v = values()
    out = os.path.join(TMP, "cut.vti")
    run(save(subsample(region(fields(source(uneven), ["temperature", "thermal conductivity"]),
                              x=(2, 9), y=(4, 15), z=(10, 30)), 2), out))
    arrays, _, mesh = read_grid(out)
    sl = (slice(2, 9, 2), slice(4, 15, 2), slice(10, 30, 2))   # half-open, as region in cells
    check("a box (cells) + stride: exactly the stored values, both fields",
          np.array_equal(arrays["temperature"], v["temperature"][sl])
          and np.array_equal(arrays["thermal conductivity"], v["thermal conductivity"][sl]),
          {k: a.shape for k, a in arrays.items()})
    check("the cut sits at its source cells", np.allclose(mesh.origin, (2, 4, 10))
          and np.allclose(mesh.spacing, (2, 2, 2)), (mesh.origin, mesh.spacing))
    check("only the asked fields are written", sorted(arrays) == ["temperature", "thermal conductivity"])

    out = os.path.join(TMP, "world.vti")
    run(save(region(fields(source(even), ["vx"]), x=(-60, 0), y=(None, None), z=(100, 200)), out))
    arrays, _, mesh = read_grid(out)
    check("even axes: a box in degrees selects the same cells as their indices",
          np.array_equal(arrays["vx"], v["vx"][3:9, :, 10:21]), arrays["vx"].shape)   # lat -55..-5, lon 100..200
    check("even axes: the cut is placed at its latitude and longitude",
          np.allclose(np.asarray(mesh.origin)[[0, 2]], (-55.0, 100.0)), mesh.origin)

    out = os.path.join(TMP, "hot.vti")
    run(save(threshold(fields(source(uneven), ["temperature"]), "thermal conductivity > 0.5"), out))
    arrays, _, _ = read_grid(out)
    keep = v["thermal conductivity"] > 0.5
    t = arrays["temperature"]
    check("a threshold on a field named with spaces masks the rest",
          np.array_equal(np.isnan(t), ~keep) and np.array_equal(t[keep], v["temperature"][keep]))

    run(save(region(source(uneven), x=(0, 3), y=(0, 3), z=(0, 3)), os.path.join(TMP, "kept")))
    check("saved with no extension: no netCDF-3 writer, so npz",
          os.path.exists(os.path.join(TMP, "kept.npz")), os.listdir(TMP))
    run(save(region(source(uneven), x=(0, 3), y=(0, 3), z=(0, 3)), os.path.join(TMP, "asked.nc")))
    with netCDF4.Dataset(os.path.join(TMP, "asked.nc")) as ds:
        check("saved as .nc on request: netCDF-4 holding the three fields, cut",
              all(np.array_equal(np.asarray(ds.variables[n][:]), values()[n][:3, :3, :3])
                  for n in NAMES) and ds.file_format == "NETCDF4")

    print("== a series of netCDF-3 files ==")
    d = os.path.join(TMP, "mantle")
    os.makedirs(d, exist_ok=True)
    for t in (1, 2, 3):
        write_nc3(os.path.join(d, f"mantle#{t}.nc"), t=t)
    out = os.path.join(TMP, "series.vti")
    run(save(region(fields(timesteps(source(d), 2, 3), ["vx"]), x=(5, 7), z=(0, 4)), out))
    for t in (2, 3):
        arrays, _, _ = read_grid(os.path.join(TMP, "series", f"timestep#{t}.vti"))
        check(f"series #{t}: that timestep's values",
              np.array_equal(arrays["vx"], values(t)["vx"][5:7, :, 0:4]))
    check("series: unselected timestep not written",
          not os.path.exists(os.path.join(TMP, "series", "timestep#1.vti")))

    print("== the simulated remote: reduced next to the data ==")
    os.environ["VISLANG_CACHE"] = os.path.join(TMP, "ncache")
    os.environ["VISLANG_REMOTE"] = "force"
    os.environ["VISLANG_REMOTE_TMP"] = os.path.join(TMP, "ntmp")
    os.makedirs(os.environ["VISLANG_REMOTE_TMP"], exist_ok=True)
    install_fake_remote()
    install_login_inspect()
    try:
        out = os.path.join(TMP, "remote.vti")
        n0 = len(EXECS)
        run(save(region(fields(timesteps(source(f"u@fakehost:{d}"), 1, 3),
                               ["temperature", "thermal conductivity"]),
                        x=(0, 5), y=(10, 19), z=(30, 35)), out))
        check("remote: the reducer ran on the far side", len(EXECS) > n0)
        for t in (1, 2, 3):
            arrays, _, _ = read_grid(os.path.join(TMP, "remote", f"timestep#{t}.vti"))
            vt = values(t)
            check(f"remote #{t}: both fields, exactly the stored box",
                  np.array_equal(arrays["temperature"], vt["temperature"][0:5, 10:19, 30:35])
                  and np.array_equal(arrays["thermal conductivity"],
                                     vt["thermal conductivity"][0:5, 10:19, 30:35]))
        n1 = len(EXECS)
        run(save(region(fields(timesteps(source(f"u@fakehost:{d}"), 1, 3), ["temperature"]),
                        x=(0, 5), y=(10, 19), z=(30, 35)), os.path.join(TMP, "again.vti")))
        check("remote: a field already fetched comes from the cache (no new job)",
              len(EXECS) == n1, len(EXECS) - n1)
    finally:
        for k in ("VISLANG_CACHE", "VISLANG_REMOTE", "VISLANG_REMOTE_TMP"):
            os.environ.pop(k, None)
    reset_sinks()


if __name__ == "__main__":
    test()
    shutil.rmtree(TMP, ignore_errors=True)
    import test_region_tags
    shutil.rmtree(test_region_tags.TMP, ignore_errors=True)
    print(f"\n{len(tg.PASS)} checks passed")
