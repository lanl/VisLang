"""A simulated remote for tests: the transport is faked, everything else is
the real code — the plan shipped as JSON, the executor (run as a subprocess on
this machine), the catalog and the planner.

    install_fake_remote()     ssh/rsync/stat/listing answered from local paths;
                              "u@fakehost:/abs/path" names /abs/path here
    install_login_inspect()   also answer the login-node `--inspect`
    EXECS, MANIFESTS, FETCHES what was shipped, and what was copied whole

Shared by test_region_tags, test_geometry, test_region_coords and test_gates.
"""

import json
import os
import re
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.environ.get("VISLANG_TEST_PYTHON", sys.executable)
EXEC = os.path.join(REPO, "vislang_exec.py")

# Generic HDF5 listing on both sides; this repo is the "remote" checkout.
os.environ.setdefault("VISLANG_NO_BINDING", "1")
os.environ.setdefault("VISLANG_REMOTE_REPO", REPO)

EXECS, MANIFESTS, FETCHES = [], [], []
ORIGINALS = {}           # what install_fake_remote replaced, for a test to restore


def install_fake_remote():
    from vislang.remote import reduce, download
    from vislang.remote.download import Connection
    from vislang.formats import inspect
    from vislang.interpreter import planner

    def fake_establish(remote_source):
        return Connection(user="u", host="fakehost", target="u@fakehost", batch_ok=True)

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
        if "cat >" in command:
            rpath = command.split("cat >", 1)[1].strip().split()[0]
            os.makedirs(os.path.dirname(rpath) or ".", exist_ok=True)
            with open(rpath, "wb") as f:
                f.write(stdin_bytes)
            if "manifest" in rpath:
                MANIFESTS.append(json.loads(stdin_bytes.decode()))
            return 0, "", ""
        if "vislang_exec.py" in command:
            flag = "--outdir" if "--outdir" in command else "--out"
            rout = command.split(flag, 1)[1].strip().split()[0]
            extra = []
            if "--manifest" in command:
                extra = ["--manifest", command.split("--manifest", 1)[1].strip().split()[0]]
            EXECS.append(json.loads(stdin_bytes.decode()))
            r = subprocess.run([PY, EXEC, "--stdin", flag, rout, *extra],
                               input=stdin_bytes, capture_output=True, cwd=REPO)
            return r.returncode, r.stdout.decode(errors="replace"), r.stderr.decode(errors="replace")
        return 1, "", f"unexpected remote command: {command}"

    # Bytes are counted as the real transfer counts them (download._measured),
    # so a run's wire_bytes means the same thing faked or real.
    from vislang.runtime import timing

    def counted(local, kind):
        n = timing.dir_bytes(local)
        timing.count("transfers")
        if n:
            timing.count("wire_bytes", n)
            timing.count(f"wire_bytes_{kind}", n)

    def fake_transfer(conn, remote_path, local_path, size_warn_mb=500):
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        shutil.copyfile(remote_path, local_path)
        FETCHES.append(remote_path)
        counted(local_path, "file")
        return local_path

    def fake_transfer_dir(conn, remote_dir, local_dir):
        shutil.copytree(remote_dir, local_dir, dirs_exist_ok=True)   # rsync -a recurses
        FETCHES.append(remote_dir)
        counted(local_dir, "dir")
        return local_dir

    for mod in (reduce, download):
        mod.establish_connection = fake_establish
        mod.remote_stat = fake_stat
        mod.transfer = fake_transfer
        mod.transfer_dir = fake_transfer_dir
        mod.measure_bandwidth = lambda conn, *a, **k: None
    reduce.remote_header_hash = fake_header_hash
    reduce.run_remote = fake_run_remote
    reduce.session_check = lambda uri: None
    planner._remote_parts = lambda conn, path: []

    def local_path(uri):
        return reduce._parse_remote(reduce._normalize_remote(uri))[2]

    def fake_files_stat(uri):
        path, base = local_path(uri), uri.rstrip("/")
        out = []
        for name in os.listdir(path):
            m = re.search(r"#(\d+)", name)
            p = os.path.join(path, name)
            if m and os.path.isfile(p):
                st = os.stat(p)
                out.append((int(m.group(1)), f"{base}/{name}", int(st.st_size),
                            int(st.st_mtime)))
        return sorted(out)

    ORIGINALS.setdefault("inspect._inspect_remote", inspect._inspect_remote)
    inspect.remote_is_dir = lambda uri: os.path.isdir(local_path(uri))
    inspect.remote_timestep_files_stat = fake_files_stat
    # The login-node inspect behind `estimate`: metadata of the "remote" file.
    inspect._inspect_remote = (lambda uri, positions=None, allow_fetch=True:
                               inspect.inspect_file(local_path(uri), positions=positions))


def install_login_inspect():
    """Answer the login-node `vislang_exec.py --inspect PATH` (metadata only) by
    running the real executor here. Returns a function that undoes it."""
    import shlex
    from vislang.remote import download
    real_run_remote = download.run_remote

    def run_remote_with_inspect(conn, command, stdin_bytes=None, timeout=None):
        if "--inspect" not in command:
            return real_run_remote(conn, command, stdin_bytes, timeout)
        r = subprocess.run([PY, EXEC, "--inspect", shlex.split(command)[-1]],
                           capture_output=True, text=True, cwd=REPO,
                           env={**os.environ, "VISLANG_NO_BINDING": "1"})
        return r.returncode, r.stdout, r.stderr
    download.run_remote = run_remote_with_inspect

    def undo():
        download.run_remote = real_run_remote
    return undo
