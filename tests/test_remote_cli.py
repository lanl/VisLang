"""The `sieve` terminal CLI driven over the REMOTE path, without a live host.

Run from the repo root: python tests/test_remote_cli.py

This is the CLI twin of test_remote_reduce.py. That test pins the deep remote
reduce semantics (catalog deltas, srun placement, per-timestep assembly) by
calling plan_pipeline directly. This one goes one layer up: it drives the same
simulated remote through `cli.main([...])` — the real argparse dispatch,
cli_core's do_inspect/do_estimate/do_execute, exit codes, and spec-file loading
— to prove the terminal front-end reaches the remote engine and reports it the
same way the MCP server does. Both front-ends call cli_core, so this also
covers the MCP tools' remote behavior by construction.

The "remote" is this machine: the my_download / my_inspect surfaces are
monkeypatched so stat is os.stat, remote inspect is a local inspect, and the
reducer runs as a local subprocess. What this cannot prove: ssh/rsync against a
live host (that seam is unit-tested in test_remote_helpers.py).
"""

import contextlib
import io
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
# Force the generic HDF5 listing on both sides (as test_remote_reduce does) so a
# stale frozen binding can't rename variables mid-test; silence run records so
# the test writes no .vislang/ artifacts into the repo.
os.environ["VISLANG_NO_BINDING"] = "1"
os.environ["VISLANG_TRACE"] = "0"
os.environ["VISLANG_TIMING"] = "0"
sys.modules["schema_binding"] = None
# The "remote" in these fixtures IS this machine, so name this repo as the
# remote checkout. `remote_repo` has no default any more: an unset value means
# "unconfigured", so that a real host says so instead of shipping a local path
# its shell cannot resolve.
os.environ["VISLANG_REMOTE_REPO"] = REPO

import h5py

import cli
import my_inspect
import remote_reduce
from my_download import Connection
from my_inspect import inspect_file

PY = os.environ.get("VISLANG_TEST_PYTHON", sys.executable)
EXEC = os.path.join(REPO, "vislang_exec.py")
TMP = tempfile.mkdtemp(prefix="vislang_rcli_")

PASS = []
EXECS = []          # plan.json payloads the "remote" received (real remote work)
CMDS = []
MANIFESTS = []


def check(name, cond, detail=""):
    assert cond, f"{name}: {detail}"
    PASS.append(name)
    print(f"  ok  {name}")


def run_cli(argv):
    """Invoke the CLI exactly as `sieve <argv>` would; return (exit_code, output)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        code = cli.main(argv)
    return code, buf.getvalue()


# --- the simulated remote (this machine behind the my_download surface) ------

def local_path(uri):
    """Resolve any remote uri form (ssh://host/p or host:/p) to its local path."""
    return remote_reduce._parse_remote(remote_reduce._normalize_remote(uri))[2]


def fake_establish(remote_source):
    return Connection(user="u", host="fakehost", target="u@fakehost",
                      batch_ok=True)


def fake_stat(conn, path):
    st = os.stat(path)
    return int(st.st_size), int(st.st_mtime)


def fake_header_hash(conn, path, nbytes=65536):
    import hashlib
    with open(path, "rb") as f:
        return hashlib.md5(f.read(nbytes)).hexdigest()


def fake_run_remote(conn, command, stdin_bytes=None, timeout=None):
    if command.startswith("rm -"):
        for tok in command.split()[2:]:
            shutil.rmtree(tok, ignore_errors=True)
            try:
                os.remove(tok)
            except OSError:
                pass
        return 0, "", ""
    if "cat >" in command:                       # stage plan / manifest to shared FS
        rpath = command.split("cat >", 1)[1].strip().split()[0]
        os.makedirs(os.path.dirname(rpath) or ".", exist_ok=True)
        with open(rpath, "wb") as f:
            f.write(stdin_bytes)
        if "manifest" in rpath:
            MANIFESTS.append(json.loads(stdin_bytes.decode()))
        return 0, "", ""
    if "vislang_exec.py" in command:
        CMDS.append(command)
        flag = "--outdir" if "--outdir" in command else "--out"
        rout = command.split(flag, 1)[1].strip().split()[0]
        extra = []
        if "--manifest" in command:
            extra = ["--manifest", command.split("--manifest", 1)[1].strip().split()[0]]
        if "--plan" in command:
            rplan = command.split("--plan", 1)[1].strip().split()[0]
            with open(rplan, "rb") as f:
                plan_bytes = f.read()
            run_args, stdin = [PY, EXEC, "--plan", rplan, flag, rout, *extra], None
        else:
            plan_bytes = stdin_bytes
            run_args, stdin = [PY, EXEC, "--stdin", flag, rout, *extra], stdin_bytes
        EXECS.append(json.loads(plan_bytes.decode()))
        r = subprocess.run(run_args, input=stdin, capture_output=True, cwd=REPO)
        return (r.returncode, r.stdout.decode(errors="replace"),
                r.stderr.decode(errors="replace"))
    return 1, "", f"unexpected remote command: {command}"


def fake_transfer(conn, remote_path, local_path_, size_warn_mb=500):
    os.makedirs(os.path.dirname(local_path_) or ".", exist_ok=True)
    shutil.copyfile(remote_path, local_path_)
    return local_path_


def fake_transfer_dir(conn, remote_dir, local_dir):
    os.makedirs(local_dir, exist_ok=True)
    for name in os.listdir(remote_dir):
        s = os.path.join(remote_dir, name)
        if os.path.isfile(s):
            shutil.copyfile(s, os.path.join(local_dir, name))
    return local_dir


def fake_inspect_remote(uri, positions=None, allow_fetch=True):
    """Remote schema read, backed by a local inspect of the same path.
    `allow_fetch` is accepted (and irrelevant here — this fake never fetches)
    because the estimate path passes allow_fetch=False to forbid the whole-file
    fallback."""
    return inspect_file(local_path(uri), positions=positions)


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


def fake_remote_timestep_files_stat(uri):
    path, base = local_path(uri), uri.rstrip("/")
    out = []
    for name in os.listdir(path):
        m = re.search(r"#(\d+)", name)
        p = os.path.join(path, name)
        if m and os.path.isfile(p):
            st = os.stat(p)
            out.append((int(m.group(1)), f"{base}/{name}",
                        int(st.st_size), int(st.st_mtime)))
    out.sort(key=lambda t: t[0])
    return out


def install_fakes():
    remote_reduce.establish_connection = fake_establish
    remote_reduce.remote_stat = fake_stat
    remote_reduce.remote_header_hash = fake_header_hash
    remote_reduce.run_remote = fake_run_remote
    remote_reduce.transfer = fake_transfer
    remote_reduce.transfer_dir = fake_transfer_dir
    my_inspect.remote_is_dir = fake_remote_is_dir
    my_inspect.remote_timestep_files = fake_remote_timestep_files
    my_inspect.remote_timestep_files_stat = fake_remote_timestep_files_stat
    my_inspect._inspect_remote = fake_inspect_remote


# --- fixtures ----------------------------------------------------------------

N = 1000
DENS = np.arange(N, dtype=np.float64)
TEMP = DENS % 7


def write_particles(path, dshift=0.0):
    with h5py.File(path, "w") as f:
        f["x"] = np.linspace(0.0, 99.9, N)
        f["y"] = np.linspace(0.0, 99.9, N)
        f["z"] = np.tile(np.arange(10.0), N // 10)
        f["density"] = DENS + dshift
        f["temperature"] = TEMP + dshift


def write_grid(path, n=32):
    """A small 3-D grid, so the index-space checks (region bounds, axis names)
    have something to be out of bounds OF. On particle data `region` is a
    world-coordinate bbox mask, where no bound can be exceeded."""
    with h5py.File(path, "w") as f:
        f["temperature"] = np.arange(n ** 3, dtype=np.float32).reshape(n, n, n)
        f["density"] = np.ones((n, n, n), dtype=np.float32)


def main():
    install_fakes()
    pfile = os.path.join(TMP, "particles.h5")
    write_particles(pfile)
    gfile = os.path.join(TMP, "grid.h5")
    write_grid(gfile)

    series = os.path.join(TMP, "series")
    os.makedirs(series)
    labels = [1, 2, 3]
    for t in labels:
        write_particles(os.path.join(series, f"run#{t}.hdf5"), dshift=float(t))

    file_uri = f"ssh://fakehost{pfile}"          # ssh://host/abs/path form (as the user types)
    grid_uri = f"ssh://fakehost{gfile}"
    folder_uri = f"ssh://fakehost{series}"

    os.environ["VISLANG_CACHE"] = os.path.join(TMP, "cache")
    os.environ["VISLANG_REMOTE"] = "auto"

    try:
        print("== sieve inspect <remote file>: schema over the wire, exit 0 ==")
        code, out = run_cli(["inspect", file_uri])
        check("inspect file exit 0", code == 0, f"code={code}\n{out}")
        check("inspect file shows the variables",
              "density" in out and "temperature" in out, out)
        check("inspect file reports HDF5 type", "HDF5" in out, out)

        print("== sieve inspect <remote folder>: recognized as a timeseries ==")
        code, out = run_cli(["inspect", folder_uri])
        check("inspect folder exit 0", code == 0, f"code={code}\n{out}")
        check("inspect folder says timeseries", "timeseries" in out.lower(), out)
        check("inspect folder lists the #N range", "1..3" in out, out)
        check("inspect folder shows the shared schema", "density" in out, out)

        print("== sieve estimate <remote spec>: static-check + cost, MATERIALIZE NOTHING ==")
        out_npz = os.path.join(TMP, "reduced.npz")
        spec = os.path.join(TMP, "spec_remote.py")
        with open(spec, "w") as f:
            f.write("save(subsample(threshold(fields(source(%r), ['density']), "
                    "'density >= 500'), 3), %r)\n" % (file_uri, out_npz))
        n_execs = len(EXECS)
        code, out = run_cli(["estimate", spec])
        check("estimate exit 0", code == 0, f"code={code}\n{out}")
        check("estimate reports it is a dry run",
              "dry run" in out.lower() or "estimate" in out.lower(), out)
        check("estimate ran NO remote reduce", len(EXECS) == n_execs, str(EXECS))
        check("estimate wrote NOTHING", not os.path.exists(out_npz), out_npz)
        # The point of `estimate`: it must actually resolve the schema and price
        # the request, not merely decline to run it. A dry run that reports
        # nothing is indistinguishable from a broken one.
        check("estimate resolved the remote schema",
              "density" in out and "vars=" in out, out)
        check("estimate lowered the forms", "project fields=" in out, out)
        check("estimate reported a cost", "Cost estimate (remote)" in out, out)

        print("== sieve estimate <remote spec, bad field>: caught pre-wire, exit 1 ==")
        # The regression this guards: `estimate` used to return Status: OK for a
        # request naming a field that does not exist, because the remote dry run
        # returned before reading any schema. It must now be rejected locally,
        # from the schema, WITHOUT shipping anything.
        est_bad = os.path.join(TMP, "spec_est_bad.py")
        with open(est_bad, "w") as f:
            f.write("save(fields(source(%r), ['no_such_field']), %r)\n"
                    % (file_uri, os.path.join(TMP, "never.npz")))
        n_execs = len(EXECS)
        code, out = run_cli(["estimate", est_bad])
        check("estimate bad-field exit 1", code == 1, f"code={code}\n{out}")
        check("estimate bad-field names the offender", "no_such_field" in out, out)
        check("estimate bad-field shipped nothing", len(EXECS) == n_execs, str(EXECS))

        print("== sieve estimate <remote grid spec, out-of-bounds region>: exit 1 ==")
        # A GRID source: here region is an index-space crop, so 99999 on a 32³ grid
        # is genuinely out of bounds and validate_narrowing must say so pre-wire.
        est_oob = os.path.join(TMP, "spec_est_oob.py")
        with open(est_oob, "w") as f:
            f.write("save(region(fields(source(%r), ['density']), x=(0, 99999)), %r)\n"
                    % (grid_uri, os.path.join(TMP, "never2.npz")))
        n_execs = len(EXECS)
        code, out = run_cli(["estimate", est_oob])
        check("estimate out-of-bounds exit 1", code == 1, f"code={code}\n{out}")
        check("estimate out-of-bounds names the extent", "0..32" in out, out)
        check("estimate out-of-bounds shipped nothing", len(EXECS) == n_execs, str(EXECS))

        print("== sieve estimate <remote FOLDER spec>: schema + cost over N steps ==")
        # The paper's specs are folders, so the timeseries estimate path matters
        # most: one metadata listing, timestep 0's schema, priced × N.
        est_folder = os.path.join(TMP, "spec_est_folder.py")
        with open(est_folder, "w") as f:
            f.write("save(subsample(fields(timesteps(source(%r), 1, 3), "
                    "['density']), 2), %r)\n"
                    % (folder_uri, os.path.join(TMP, "never3")))
        n_execs = len(EXECS)
        code, out = run_cli(["estimate", est_folder])
        check("estimate folder exit 0", code == 0, f"code={code}\n{out}")
        check("estimate folder counted the timesteps", "3 timestep(s)" in out, out)
        check("estimate folder priced the series",
              "Cost estimate (remote)" in out and "timesteps: 3" in out, out)
        check("estimate folder shipped nothing", len(EXECS) == n_execs, str(EXECS))

        print("== sieve estimate <remote FOLDER spec, bad field>: exit 1 ==")
        est_fbad = os.path.join(TMP, "spec_est_folder_bad.py")
        with open(est_fbad, "w") as f:
            f.write("save(fields(timesteps(source(%r), 1, 3), ['nope']), %r)\n"
                    % (folder_uri, os.path.join(TMP, "never4")))
        n_execs = len(EXECS)
        code, out = run_cli(["estimate", est_fbad])
        check("estimate folder bad-field exit 1", code == 1, f"code={code}\n{out}")
        check("estimate folder bad-field shipped nothing",
              len(EXECS) == n_execs, str(EXECS))

        print("== sieve execute <remote spec>: reduce on the 'remote', pull survivors ==")
        code, out = run_cli(["execute", spec])
        check("execute exit 0", code == 0, f"code={code}\n{out}")
        check("execute did one remote reduce", len(EXECS) == n_execs + 1, str(len(EXECS)))
        check("execute wrote the output", os.path.exists(out_npz), out_npz)
        with np.load(out_npz) as z:
            check("execute result is the reduced survivors",
                  np.array_equal(z["density"], DENS[DENS >= 500][::3]), repr(z["density"][:5]))
        check("execute report status OK", out.startswith("Status: OK"), out[:80])

        print("== sieve execute <missing spec>: clean error, exit 1 ==")
        code, out = run_cli(["execute", os.path.join(TMP, "nope.py")])
        check("missing spec exit 1", code == 1, f"code={code}")
        check("missing spec error message", "not found" in out.lower(), out)

        print("== sieve execute <remote spec, bad field>: failure surfaced, exit 1 ==")
        # A misspelled field over the remote path is rejected by the reducer next
        # to the data (remote specs are validated there, not pre-wire); the CLI
        # must still surface that as a non-zero exit and materialize nothing.
        bad_out = os.path.join(TMP, "x.npz")
        bad = os.path.join(TMP, "spec_bad.py")
        with open(bad, "w") as f:
            f.write("save(fields(source(%r), ['densty']), %r)\n"
                    % (file_uri, bad_out))
        code, out = run_cli(["execute", bad])
        check("bad-field exit 1", code == 1, f"code={code}\n{out}")
        check("bad-field report signals failure",
              "FAILED" in out or "densty" in out, out)
        check("bad-field wrote no output", not os.path.exists(bad_out), bad_out)
    finally:
        os.environ.pop("VISLANG_CACHE", None)
        os.environ.pop("VISLANG_REMOTE", None)

    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\nALL {len(PASS)} CHECKS PASSED")


if __name__ == "__main__":
    main()
