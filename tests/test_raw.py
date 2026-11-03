"""Headerless raw volumes, `<name>_<nx>x<ny>x<nz>_<dtype>.raw`, x fastest.

The layout is the Open SciVis Datasets' (array[z][y][x] in C, little-endian),
so a non-cubic file is the test that matters: read with the wrong axis order,
its values come back scrambled. A sparse file larger than memory checks the
reader maps the file rather than loading it.

    /opt/miniconda3/envs/vislang/bin/python tests/test_raw.py
"""
import os
import shutil
import sys

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_geometry as tg
from test_geometry import check, raises, read_grid, run, TMP
from fakes_remote import EXECS, install_fake_remote, install_login_inspect
from vislang.dsl import reset_sinks
from vislang.dsl.forms import source, region, fields, save, subsample
from vislang.formats.adapters import RawAdapter, get_adapter
from vislang.formats.inspect import inspect_file

SHAPE = (12, 10, 6)


def write_raw(name, arr):
    """Write `arr` (indexed [x, y, z]) x-fastest, as the convention says."""
    p = os.path.join(TMP, f"{name}_{arr.shape[0]}x{arr.shape[1]}x{arr.shape[2]}_{arr.dtype.name}.raw")
    np.asarray(arr).ravel(order="F").astype(arr.dtype.newbyteorder("<")).tofile(p)
    return p


def test():
    print("== the raw reader ==")
    n = int(np.prod(SHAPE))
    arr = np.arange(n, dtype=np.float64).reshape(SHAPE, order="F") * 1.5
    p = write_raw("pressure", arr)
    check("a convention-named file of the declared size goes to the raw reader",
          isinstance(get_adapter(p), RawAdapter))
    info = inspect_file(p)
    check("inspect: the name's variable, shape and item size",
          info.variables == ["pressure"] and info.dimensions["grid"] == SHAPE
          and info.itemsizes == {"pressure": 8}, (info.variables, info.dimensions))
    raw = np.fromfile(p, dtype="<f8")
    check("x is fastest: the file's second value is [1, 0, 0]", raw[1] == arr[1, 0, 0])

    out = os.path.join(TMP, "whole.npz")
    run(save(source(p), out))
    with np.load(out) as z:
        check("a non-cubic volume reads back unscrambled", np.array_equal(z["pressure"], arr))
    out = os.path.join(TMP, "cut.vti")
    run(save(subsample(region(source(p), x=(2, 11), y=(1, 9), z=(0, 5)), 2), out))
    a, _, mesh = read_grid(out)
    check("a box + stride: exactly those cells", np.array_equal(a["pressure"], arr[2:11:2, 1:9:2, 0:5:2]))
    check("placed at its source cells", np.allclose(mesh.origin, (2, 1, 0)))

    short = os.path.join(TMP, "short_12x10x6_float64.raw")
    raw[:-1].tofile(short)
    raises("a file shorter than its name declares is refused, saying why",
           lambda: inspect_file(short), "but the file is")
    odd = os.path.join(TMP, "odd_12x10x6_complex128.raw")
    np.zeros(n, np.complex128).tofile(odd)
    raises("a type that is not an integer or float is refused",
           lambda: inspect_file(odd), "not an integer or float")

    print("== a volume larger than memory: mapped, not loaded ==")
    big_shape = (8192, 8192, 1024)                  # 512 GiB of float64, sparse on disk
    big = os.path.join(TMP, f"big_{big_shape[0]}x{big_shape[1]}x{big_shape[2]}_float64.raw")
    try:
        with open(big, "wb") as f:
            f.truncate(int(np.prod(big_shape)) * 8)
    except OSError as e:
        print(f"  skip  sparse file not supported here: {e}")
    else:
        out = os.path.join(TMP, "big_cut.npz")
        run(save(region(source(big), x=(100, 164), y=(4000, 4032), z=(500, 516)), out))
        with np.load(out) as z:
            check("a box of a 512 GiB volume, read without loading it",
                  z["big"].shape == (64, 32, 16) and not z["big"].any())
        os.remove(big)

    print("== the simulated remote ==")
    os.environ["VISLANG_CACHE"] = os.path.join(TMP, "rcache")
    os.environ["VISLANG_REMOTE"] = "force"
    os.environ["VISLANG_REMOTE_TMP"] = os.path.join(TMP, "rtmp")
    os.makedirs(os.environ["VISLANG_REMOTE_TMP"], exist_ok=True)
    install_fake_remote()
    install_login_inspect()
    try:
        n0 = len(EXECS)
        out = os.path.join(TMP, "remote.npz")
        run(save(subsample(region(source(f"u@fakehost:{p}"), x=(0, 12), y=(3, 10)), 3), out))
        check("remote: the reducer ran on the far side", len(EXECS) > n0)
        with np.load(out) as z:
            check("remote: exactly those cells", np.array_equal(z["pressure"], arr[0:12:3, 3:10:3, ::3]))
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
