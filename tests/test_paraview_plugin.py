"""The ParaView plugin, inside ParaView (plugins/paraview/SieveParaView.py).

Plain-python asserts (no pytest on the cluster). Run from the repo root:
    python tests/test_paraview_plugin.py

Writes the fixtures (a uniform .vti, an HDF5 grid with no geometry, points, a
curvilinear .vts, a folder of timesteps), then runs
tests/paraview/pv_plugin_checks.py in ParaView's pvpython. Skipped when no
ParaView is installed (set PVPYTHON to point at one).
"""

import os
import subprocess
import sys
import tempfile

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import h5py
import pyvista as pv

from test_geometry import write_vts

PVPYTHON = os.environ.get("PVPYTHON") or next(
    (p for p in ("/Applications/ParaView-6.1.0-RC2.app/Contents/bin/pvpython",)
     if os.path.exists(p)), None)


def fixtures(d):
    img = pv.ImageData(dimensions=(64, 48, 32), origin=(100.0, -4.0, 2.0),
                       spacing=(0.5, 2.0, 1.0))
    img.point_data["rho"] = np.arange(64 * 48 * 32, dtype=np.float32)
    img.save(os.path.join(d, "cube.vti"))
    with h5py.File(os.path.join(d, "plain.h5"), "w") as f:
        f["rho"] = np.arange(32 ** 3, dtype=np.float32).reshape(32, 32, 32)
    write_vts(os.path.join(d, "fire.vts"))
    rng = np.random.default_rng(0)
    np.savez(os.path.join(d, "pts.npz"), x=rng.uniform(0, 10, 4000),
             y=rng.uniform(0, 10, 4000), z=rng.uniform(0, 10, 4000),
             m=np.arange(4000.0))
    s = os.path.join(d, "series")
    os.makedirs(s)
    for t in (3, 4, 5):
        with h5py.File(os.path.join(s, f"snap#{t}.h5"), "w") as f:
            f["rho"] = np.full((16, 16, 16), 1000.0 * t, dtype=np.float32)


def main():
    if PVPYTHON is None:
        print("SKIP: no ParaView (set PVPYTHON)")
        return
    tmp = tempfile.mkdtemp(prefix="vislang_pvplugin_test_")
    data = os.path.join(tmp, "data")
    os.makedirs(data)
    fixtures(data)
    rtmp = os.path.join(tmp, "remote_tmp")
    os.makedirs(rtmp)
    env = {**os.environ, "VISLANG_PYTHON": sys.executable,
           "VISLANG_CACHE": os.path.join(tmp, "cache"),
           "VISLANG_TIMINGS_FILE": os.path.join(tmp, "timings.jsonl"),
           "VISLANG_REMOTE_TMP": rtmp, "VISLANG_REMOTE": "auto",
           "SIEVE_FAKE": os.path.join(REPO, "tests", "paraview", "fake_sieve")}
    r = subprocess.run([PVPYTHON, os.path.join(REPO, "tests", "paraview",
                                               "pv_plugin_checks.py"),
                        os.path.join(REPO, "plugins", "paraview", "SieveParaView.py"),
                        data, os.path.join(tmp, "sessions")],
                       capture_output=True, text=True, env=env, timeout=900)
    print("\n".join(ln for ln in r.stdout.splitlines()
                    if ln.startswith(("  ok", "FAIL", "==", "all "))))
    if r.returncode != 0:
        print(r.stdout[-3000:])
        print(r.stderr[-3000:])
        sys.exit(1)

    # The paper's figure script draws a recorded session (overview + boxes).
    sessions = sorted(os.path.join(tmp, "sessions", d)
                      for d in os.listdir(os.path.join(tmp, "sessions"))
                      if os.path.exists(os.path.join(tmp, "sessions", d, "session.jsonl")))
    png = os.path.join(tmp, "figure.png")
    f = subprocess.run([PVPYTHON, os.path.join(REPO, "bench", "pv_session_figure.py"),
                        sessions[0], png, "--size", "640x480"],
                       capture_output=True, text=True, env=env, timeout=300)
    ok = f.returncode == 0 and os.path.exists(png) and os.path.getsize(png) > 10_000
    print(("  ok  " if ok else "FAIL ") + "the session figure renders the overview "
          "and its details")
    if not ok:
        print(f.stdout[-2000:], f.stderr[-2000:])
        sys.exit(1)


if __name__ == "__main__":
    main()
