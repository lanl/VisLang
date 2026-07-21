"""remote_reduce end-to-end against a SIMULATED remote.

Run from the repo root: python tests/test_remote_reduce.py
The transport layer is monkeypatched so the "remote" is this machine: stat is
os.stat, the remote exec runs vislang_exec as a local subprocess, transfer is a
file copy. Everything else — plan serialization, the executor, the extent
catalog, delta planning, assembly, the planner's site dispatch — is the real
code path. What this cannot prove: ssh/rsync against a live host.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
# Deterministic HDF5 names on BOTH sides: force the generic listing here AND in
# the vislang_exec subprocess (which inherits this env), so local and remote
# never disagree on variable names via a stale frozen binding.
os.environ["VISLANG_NO_BINDING"] = "1"
sys.modules["schema_binding"] = None

import h5py

import remote_reduce
from my_download import Connection
from dsl_forms import reset_sinks
from dsl_forms.forms import source, fields, subsample, threshold, timesteps, save
from planner import plan_pipeline

# The reducer runs as a LOCAL subprocess here (fakes stand in for ssh), so use
# the interpreter running the test; override with VISLANG_TEST_PYTHON.
PY = os.environ.get("VISLANG_TEST_PYTHON", sys.executable)
EXEC = os.path.join(REPO, "vislang_exec.py")
TMP = tempfile.mkdtemp(prefix="vislang_rreduce_")

PASS = []
EXECS = []          # plan.json payloads the "remote" received
CMDS = []           # vislang_exec command strings the "remote" received


def check(name, cond, detail=""):
    assert cond, f"{name}: {detail}"
    PASS.append(name)
    print(f"  ok  {name}")


# ---------------------------------------------------------------------------
# The simulated remote: local machine behind the my_download function surface
# ---------------------------------------------------------------------------
def fake_establish(remote_source):
    return Connection(user="u", host="fakehost", target="u@fakehost",
                      method="ssh-key")


def fake_stat(conn, path):
    st = os.stat(path)
    return int(st.st_size), int(st.st_mtime)


def fake_header_hash(conn, path, nbytes=65536):
    import hashlib
    with open(path, "rb") as f:
        return hashlib.md5(f.read(nbytes)).hexdigest()


def fake_run_remote(conn, command, stdin_bytes=None, timeout=None):
    if command.startswith("rm -"):               # cleanup (rm -f / rm -rf ...)
        for tok in command.split()[2:]:
            shutil.rmtree(tok, ignore_errors=True)
            try:
                os.remove(tok)
            except OSError:
                pass
        return 0, "", ""
    if command.startswith("squeue"):             # auto-discovery of a held alloc
        # newest (654322) is unnamed; the named "vislang" alloc is older (654321)
        return 0, "654322|skylake-gold|other\n654321|skylake-gold|vislang\n", ""
    if "cat >" in command:                       # srun-mode: stage plan to shared FS
        rplan = command.split("cat >", 1)[1].strip().split()[0]
        os.makedirs(os.path.dirname(rplan) or ".", exist_ok=True)
        with open(rplan, "wb") as f:
            f.write(stdin_bytes)
        return 0, "", ""
    if "vislang_exec.py" in command:
        CMDS.append(command)
        # A folder reduce writes a DIRECTORY (--outdir); a single-file reduce an
        # .npz (--out). Detect --outdir first (it contains the substring --out).
        flag = "--outdir" if "--outdir" in command else "--out"
        rout = command.split(flag, 1)[1].strip().split()[0]
        if "--plan" in command:                  # srun-mode: reducer reads a file
            rplan = command.split("--plan", 1)[1].strip().split()[0]
            with open(rplan, "rb") as f:
                plan_bytes = f.read()
            run_args, stdin = [PY, EXEC, "--plan", rplan, flag, rout], None
        else:                                     # direct-ssh: plan piped via stdin
            plan_bytes = stdin_bytes
            run_args, stdin = [PY, EXEC, "--stdin", flag, rout], stdin_bytes
        EXECS.append(json.loads(plan_bytes.decode()))
        r = subprocess.run(run_args, input=stdin, capture_output=True, cwd=REPO)
        return (r.returncode, r.stdout.decode(errors="replace"),
                r.stderr.decode(errors="replace"))
    return 1, "", f"unexpected remote command: {command}"


def fake_transfer(conn, remote_path, local_path, size_warn_mb=500):
    os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
    shutil.copyfile(remote_path, local_path)
    return local_path


def fake_transfer_dir(conn, remote_dir, local_dir):
    os.makedirs(local_dir, exist_ok=True)
    for name in os.listdir(remote_dir):
        s = os.path.join(remote_dir, name)
        if os.path.isfile(s):
            shutil.copyfile(s, os.path.join(local_dir, name))
    return local_dir


def main():
    # particle file with several variables (the +1-field scenario needs them)
    ppath = os.path.join(TMP, "particles.h5")
    n = 1000
    dens = np.arange(n, dtype=np.float64)
    with h5py.File(ppath, "w") as f:
        f["x"] = np.linspace(0.0, 99.9, n)
        f["y"] = np.linspace(0.0, 99.9, n)[::-1].copy()
        f["z"] = np.tile(np.arange(10.0), n // 10)
        f["density"] = dens
        f["temp"] = dens % 7

    remote_reduce.establish_connection = fake_establish
    remote_reduce.remote_stat = fake_stat
    remote_reduce.remote_header_hash = fake_header_hash
    remote_reduce.run_remote = fake_run_remote
    remote_reduce.transfer = fake_transfer
    remote_reduce.transfer_dir = fake_transfer_dir

    # The planner now probes remote folder-ness before dispatch. Our fake remote
    # IS this machine, so back both probes with the local filesystem (the ssh
    # command construction is unit-tested separately in test_remote_helpers.py).
    import my_inspect

    def local_path(uri):
        return remote_reduce._parse_remote(remote_reduce._normalize_remote(uri))[2]

    def fake_remote_is_dir(uri):
        return os.path.isdir(local_path(uri))

    def fake_remote_timestep_files(uri):
        path, base = local_path(uri), uri.rstrip("/")
        out = [(int(m.group(1)), f"{base}/{name}")
               for name in os.listdir(path)
               if (m := re.search(r"#(\d+)", name))
               and os.path.isfile(os.path.join(path, name))]
        out.sort(key=lambda t: t[0])
        return out

    my_inspect.remote_is_dir = fake_remote_is_dir
    my_inspect.remote_timestep_files = fake_remote_timestep_files

    cache = os.path.join(TMP, "cache")
    os.environ["VISLANG_CACHE"] = cache
    os.environ["VISLANG_REMOTE"] = "auto"
    uri = f"u@fakehost:{ppath}"

    try:
        print("== first run: reduce on the 'remote', pull only the survivors ==")
        out1 = os.path.join(TMP, "o1.npz")
        reset_sinks()
        res = plan_pipeline(save(subsample(threshold(fields(source(uri),
                            ["density"]), "density >= 500"), 3), out1))
        check("materialized", res["materialized"] is True)
        check("one remote exec", len(EXECS) == 1)
        with np.load(out1) as z:
            got = z["density"]
        check("reduced result correct",
              np.array_equal(got, dens[dens >= 500][::3]))
        check("steps mention remote exec",
              any("remote exec" in s for s in res["steps"]), str(res["steps"]))

        print("== second identical run: served from the catalog, zero wire ==")
        out2 = os.path.join(TMP, "o2.npz")
        reset_sinks()
        res = plan_pipeline(save(subsample(threshold(fields(source(uri),
                            ["density"]), "density >= 500"), 3), out2))
        check("no new remote exec", len(EXECS) == 1)
        check("catalog full hit step",
              any("nothing crossed the wire" in s for s in res["steps"]),
              str(res["steps"]))
        with np.load(out2) as z:
            check("cached result identical", np.array_equal(z["density"], got))

        print("== +1 field: only the missing variable crosses the wire ==")
        out3 = os.path.join(TMP, "o3.npz")
        reset_sinks()
        res = plan_pipeline(save(subsample(threshold(fields(source(uri),
                            ["density", "temp"]), "density >= 500"), 3), out3))
        check("one more remote exec", len(EXECS) == 2)
        sent_fields = [s for s in EXECS[1]["chain"] if s["kind"] == "fields"]
        check("delta plan asks only for temp",
              sent_fields and sent_fields[0]["keep"] == ["temp"],
              json.dumps(EXECS[1]["chain"]))
        with np.load(out3) as z:
            check("assembled density from cache",
                  np.array_equal(z["density"], got))
            check("assembled temp fetched fresh",
                  np.array_equal(z["temp"], (dens % 7)[dens >= 500][::3]))
        check("steps report cache+fetch split",
              any("cached" in s and "fetched" in s for s in res["steps"]),
              str(res["steps"]))

        print("== written order still honored through the remote path ==")
        out4 = os.path.join(TMP, "o4.npz")
        reset_sinks()
        plan_pipeline(save(threshold(subsample(fields(source(uri),
                      ["density"]), 3), "density >= 500"), out4))
        with np.load(out4) as z:
            check("sample-then-threshold differs and is correct",
                  np.array_equal(z["density"],
                                 dens[::3][np.arange(n)[::3] >= 500]))

        print("== srun mode: reduce dispatched as a step into a held allocation ==")
        # Fresh cache so the reduce is a real remote exec, not a catalog hit.
        os.environ["VISLANG_CACHE"] = os.path.join(TMP, "cache_srun")
        os.environ["VISLANG_SRUN_JOBID"] = "123456"
        os.environ["VISLANG_REMOTE_TMP"] = os.path.join(TMP, "remote_tmp")
        try:
            n_execs, out5 = len(EXECS), os.path.join(TMP, "o5.npz")
            reset_sinks()
            res = plan_pipeline(save(subsample(threshold(fields(source(uri),
                                ["density"]), "density >= 500"), 3), out5))
            check("srun mode did a remote exec", len(EXECS) == n_execs + 1)
            check("command wraps srun --jobid",
                  CMDS and "srun --jobid=123456" in CMDS[-1], str(CMDS[-1:]))
            check("command reads plan from a staged file (--plan, not --stdin)",
                  "--plan" in CMDS[-1] and "--stdin" not in CMDS[-1], str(CMDS[-1:]))
            check("plan staged under VISLANG_REMOTE_TMP",
                  os.path.join(TMP, "remote_tmp") in CMDS[-1], str(CMDS[-1:]))
            check("steps note the srun step",
                  any("srun --jobid=123456" in s for s in res["steps"]),
                  str(res["steps"]))
            with np.load(out5) as z:
                check("srun-mode result identical to direct path",
                      np.array_equal(z["density"], got))

            print("== srun auto: newest held allocation discovered from squeue ==")
            os.environ["VISLANG_CACHE"] = os.path.join(TMP, "cache_auto")
            os.environ["VISLANG_SRUN_JOBID"] = "auto"
            n_execs, out6 = len(EXECS), os.path.join(TMP, "o6.npz")
            reset_sinks()
            res = plan_pipeline(save(subsample(threshold(fields(source(uri),
                                ["density"]), "density >= 500"), 3), out6))
            check("auto mode did a remote exec", len(EXECS) == n_execs + 1)
            check("newest jobid chosen when no name filter",
                  "srun --jobid=654322" in CMDS[-1], str(CMDS[-1:]))
            check("steps report the discovery",
                  any("discovered held allocation jobid=654322" in s
                      for s in res["steps"]), str(res["steps"]))
            with np.load(out6) as z:
                check("auto-mode result identical", np.array_equal(z["density"], got))

            print("== srun auto + name filter: picks the named alloc, not the newest ==")
            os.environ["VISLANG_CACHE"] = os.path.join(TMP, "cache_named")
            os.environ["VISLANG_SRUN_NAME"] = "vislang"
            n_execs, out7 = len(EXECS), os.path.join(TMP, "o7.npz")
            reset_sinks()
            plan_pipeline(save(subsample(threshold(fields(source(uri),
                          ["density"]), "density >= 500"), 3), out7))
            check("name filter overrides newest",
                  len(EXECS) == n_execs + 1 and "srun --jobid=654321" in CMDS[-1],
                  str(CMDS[-1:]))
            with np.load(out7) as z:
                check("named-alloc result identical", np.array_equal(z["density"], got))
        finally:
            os.environ.pop("VISLANG_SRUN_JOBID", None)
            os.environ.pop("VISLANG_SRUN_NAME", None)
            os.environ.pop("VISLANG_REMOTE_TMP", None)

        print("== remote FOLDER is a timeseries: chain maps over the #N files ==")
        os.environ["VISLANG_CACHE"] = os.path.join(TMP, "cache_folder")
        os.environ["VISLANG_REMOTE"] = "auto"
        series = os.path.join(TMP, "series")
        os.makedirs(series)
        labels = [1, 2, 3]
        dens_by_t = {}
        for t in labels:                            # density varies per timestep
            dens_t = np.arange(n, dtype=np.float64) + t
            dens_by_t[t] = dens_t
            with h5py.File(os.path.join(series, f"particles#{t}.hdf5"), "w") as f:
                f["x"] = np.linspace(0.0, 99.9, n)
                f["y"] = np.linspace(0.0, 99.9, n)
                f["z"] = np.tile(np.arange(10.0), n // 10)
                f["density"] = dens_t
        folder_uri = f"u@fakehost:{series}"

        def expect(t):                              # threshold >=500, then stride 3
            d = dens_by_t[t]
            return d[d >= 500][::3]

        out_all = os.path.join(TMP, "series_out")
        n_execs_before = len(EXECS)
        reset_sinks()
        res = plan_pipeline(save(subsample(threshold(fields(source(folder_uri),
                            ["density"]), "density >= 500"), 3), out_all))
        check("folder reduced next to the data on the remote",
              any("remote folder" in s for s in res["steps"]),
              str(res["steps"]))
        check("one remote exec for the WHOLE folder (batched, not per-file)",
              len(EXECS) == n_execs_before + 1, f"{len(EXECS) - n_execs_before}")
        check("observability: remote compute reported for all timesteps",
              any("remote compute:" in s and f"{len(labels)}/{len(labels)}" in s
                  for s in res["steps"]), str(res["steps"]))
        check("sites: all remote, none fetched",
              res.get("sites") == {"remote": len(labels), "fetch": 0},
              str(res.get("sites")))
        check("materialized folder result", res["materialized"] is True)
        check("one output file per timestep",
              sorted(os.listdir(out_all)) ==
              [f"timestep#{t}.hdf5" for t in labels], str(os.listdir(out_all)))
        for t in labels:
            with h5py.File(os.path.join(out_all, f"timestep#{t}.hdf5"), "r") as f:
                check(f"timestep {t} reduced correctly",
                      np.array_equal(f["density"][:], expect(t)))

        print("== timesteps(...) selects an inclusive #N range of the folder ==")
        out_rng = os.path.join(TMP, "series_out_rng")
        reset_sinks()
        plan_pipeline(save(subsample(threshold(fields(timesteps(source(folder_uri),
                      2, 3), ["density"]), "density >= 500"), 3), out_rng))
        check("range selects only #2..#3",
              sorted(os.listdir(out_rng)) == ["timestep#2.hdf5", "timestep#3.hdf5"],
              str(os.listdir(out_rng)))
        with h5py.File(os.path.join(out_rng, "timestep#2.hdf5"), "r") as f:
            check("ranged timestep 2 correct", np.array_equal(f["density"][:], expect(2)))
    finally:
        os.environ.pop("VISLANG_CACHE", None)
        os.environ.pop("VISLANG_REMOTE", None)

    shutil.rmtree(TMP)
    print(f"\nALL {len(PASS)} CHECKS PASSED")


if __name__ == "__main__":
    main()
