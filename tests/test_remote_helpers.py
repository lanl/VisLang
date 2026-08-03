"""Remote-probe helpers in my_download.py, tested without a live host.

Run from the repo root: python tests/test_remote_helpers.py
No ssh here — subprocess.run is monkeypatched to capture argv and return
canned outputs, so these tests pin command construction, parsing, the
and the key-auth-only gate.
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import my_download
from my_download import (Connection, remote_stat, remote_header_hash,
                         remote_file_md5, measure_bandwidth,
                         run_remote, push_file)

PASS = []
CALLS = []


def check(name, cond, detail=""):
    assert cond, f"{name}: {detail}"
    PASS.append(name)
    print(f"  ok  {name}")


class Result:
    def __init__(self, rc=0, stdout=b"", stderr=b""):
        self.returncode, self.stdout, self.stderr = rc, stdout, stderr


def fake_run(script):
    """subprocess.run stand-in: record argv, return the scripted result.
    Honors text=True the way the real subprocess.run does (str streams)."""
    def _run(cmd, **kw):
        CALLS.append((list(cmd), kw))
        r = script(cmd, kw)
        if kw.get("text") and isinstance(r.stdout, bytes):
            r.stdout = r.stdout.decode()
            r.stderr = r.stderr.decode() if isinstance(r.stderr, bytes) else r.stderr
        return r
    return _run


def with_fake(script, fn):
    CALLS.clear()
    real = subprocess.run
    subprocess.run = fake_run(script)
    try:
        return fn()
    finally:
        subprocess.run = real


KEY = Connection(user="u", host="h", target="u@h", method="ssh-key")


def main():
    print("== key-auth gate: password connections never spawn a process ==")
    pw = Connection(user="u", host="h", target="u@h", method="password")

    def boom(cmd, kw):
        raise AssertionError("subprocess was called")
    with_fake(boom, lambda: (
        check("stat gated", remote_stat(pw, "/f") is None),
        check("header gated", remote_header_hash(pw, "/f") is None),
        check("md5 gated", remote_file_md5(pw, "/f") is None),
        check("bandwidth gated", measure_bandwidth(pw) is None),
        check("run gated", run_remote(pw, "true")[0] == 255),
        check("push gated", push_file(pw, "/a", "/b") is False)))

    print("== remote_stat ==")
    out = with_fake(lambda c, k: Result(0, b"27543608 1750000000\n"),
                    lambda: remote_stat(KEY, "/data/f.raw"))
    check("stat parsed", out == (27543608, 1750000000), repr(out))
    argv = CALLS[0][0]
    check("stat uses BatchMode", "BatchMode=yes" in " ".join(argv))
    check("stat targets host", "u@h" in argv)
    check("stat quotes path", "'/data/f.raw'" in argv[-1] or "/data/f.raw" in argv[-1])
    check("stat unparsable -> None",
          with_fake(lambda c, k: Result(0, b"weird\n"),
                    lambda: remote_stat(KEY, "/f")) is None)
    check("stat failure -> None",
          with_fake(lambda c, k: Result(1, b""),
                    lambda: remote_stat(KEY, "/f")) is None)

    print("== remote_header_hash ==")
    md5 = "d41d8cd98f00b204e9800998ecf8427e"
    got = with_fake(lambda c, k: Result(0, f"{md5}  -\n".encode()),
                    lambda: remote_header_hash(KEY, "/data/f.raw", nbytes=1024))
    check("header hash parsed", got == md5)
    check("head -c present", "head -c 1024" in CALLS[0][0][-1])

    print("== run_remote ==")
    rc, so, se = with_fake(lambda c, k: Result(3, b"out", b"err"),
                           lambda: run_remote(KEY, "do thing", stdin_bytes=b"PLAN"))
    check("run rc/stdout/stderr", (rc, so, se) == (3, "out", "err"))
    check("run pipes stdin", CALLS[0][1].get("input") == b"PLAN")
    check("run passes command", CALLS[0][0][-1] == "do thing")

    print("== measure_bandwidth ==")
    bw = with_fake(lambda c, k: Result(0, b"\0" * (1 << 20)),
                   lambda: measure_bandwidth(KEY, mb=1))
    check("bandwidth positive", bw is not None and bw > 0)
    check("bandwidth dd command", "dd if=/dev/zero" in CALLS[0][0][-1])
    check("bandwidth failure -> None",
          with_fake(lambda c, k: Result(1, b""), lambda: measure_bandwidth(KEY)) is None)

    print("== remote_is_dir / remote_timestep_files (folder detection) ==")
    import my_inspect
    from my_download import clear_remote_caches, remote_probe, remote_stat as _rstat
    from my_download import remote_header_hash as _rhash

    def folder_script(cmd, kw):
        """Fake ssh: the establish probe (`true`), then the combined probe / ls.
        `remote_is_dir` goes through remote_probe, which asks for type, size,
        mtime and the header hash in ONE command."""
        last = cmd[-1]
        if last == "true":                       # establish_connection -> ssh-key
            return Result(0, b"")
        if last.startswith("stat -Lc '%F|%s|%Y'"):   # -L: a symlink TO a dir is a folder
            return Result(0, b"directory|4096|1750000000\nd41d8cd98f00b204e9800998ecf8427e  -\n")
        if last.startswith("ls -1p"):
            # a subdir and a dotfile mixed in; both must be dropped
            return Result(0, b"run#1.hdf5\nrun#2.hdf5\nrun#10.hdf5\n"
                             b"README\nsub/\n.hidden#3.hdf5\n")
        return Result(1, b"", b"unexpected: " + last.encode())

    clear_remote_caches()
    is_dir = with_fake(folder_script,
                       lambda: my_inspect.remote_is_dir("u@h:/data/series"))
    check("remote_is_dir True on a directory", is_dir is True)
    check("is_dir dereferences symlinks (-L)",
          any("stat -Lc" in c[0][-1] for c in CALLS), str(CALLS))
    check("is_dir asks for type+size+mtime+hash in ONE command",
          sum(1 for c in CALLS if "stat -Lc" in c[0][-1]) == 1
          and "md5sum" in CALLS[-1][0][-1], str(CALLS))

    clear_remote_caches()
    check("remote_is_dir False on a regular file",
          with_fake(lambda c, k: Result(0, b"") if c[-1] == "true"
                    else Result(0, b"regular file|27543608|1750000000\nabc  -\n"),
                    lambda: my_inspect.remote_is_dir("u@h:/data/f.raw")) is False)

    print("== probe cache: identity is free after the folder check ==")
    # The folder check is the first thing to touch a remote source. Warming the
    # probe there must make the catalog's later size/mtime/hash lookup cost NO
    # further round trips — that is the whole point of the combined probe.
    clear_remote_caches()
    with_fake(lambda c, k: Result(0, b"") if c[-1] == "true"
              else Result(0, b"regular file|27543608|1750000000\n"
                             b"d41d8cd98f00b204e9800998ecf8427e  -\n"),
              lambda: my_inspect.remote_is_dir("u@h:/data/f.raw"))
    n_after_probe = len(CALLS)

    def explode(cmd, kw):
        raise AssertionError(f"extra round trip: {cmd[-1]}")
    with_fake(explode, lambda: (
        check("remote_stat served from the probe cache",
              _rstat(KEY, "/data/f.raw") == (27543608, 1750000000)),
        check("remote_header_hash served from the probe cache",
              _rhash(KEY, "/data/f.raw") == "d41d8cd98f00b204e9800998ecf8427e")))
    check("warming the probe cost exactly one probe", n_after_probe == 2,
          f"calls={n_after_probe}")

    # A different header window is a different question — it must NOT be answered
    # from a cache holding the 64 KiB hash.
    check("a different nbytes falls through to its own command",
          with_fake(lambda c, k: Result(0, b"deadbeef  -\n"),
                    lambda: _rhash(KEY, "/data/f.raw", nbytes=1024)) == "deadbeef")
    clear_remote_caches()

    files = with_fake(folder_script,
                      lambda: my_inspect.remote_timestep_files("u@h:/data/series"))
    check("timesteps sorted by #N, non-#N and subdirs dropped",
          [lab for lab, _ in files] == [1, 2, 10], str(files))
    check("per-timestep uri rebuilt from the folder uri",
          files[0][1] == "u@h:/data/series/run#1.hdf5", str(files))
    check("trailing slash on the folder uri is handled",
          with_fake(folder_script,
                    lambda: my_inspect.remote_timestep_files("u@h:/data/series/"))[0][1]
          == "u@h:/data/series/run#1.hdf5")

    def no_ts_script(cmd, kw):
        if cmd[-1] == "true":
            return Result(0, b"")
        if cmd[-1].startswith("ls -1p"):
            return Result(0, b"notes.txt\ndata.bin\n")
        return Result(1, b"")
    try:
        with_fake(no_ts_script,
                  lambda: my_inspect.remote_timestep_files("u@h:/data/empty"))
        check("no #N files raises ValueError", False, "no error raised")
    except ValueError as e:
        check("no #N files raises ValueError", "timestep" in str(e))

    print("== push_file ==")
    real_have = my_download._have_cmd
    my_download._have_cmd = lambda name: name == "rsync"
    try:
        ok = with_fake(lambda c, k: Result(0, b""),
                       lambda: push_file(KEY, "/local/env.tar.gz",
                                         "~/.vislang/env.tar.gz"))
        check("push succeeds", ok is True)
        check("push mkdir first", "mkdir -p ~/.vislang" in CALLS[0][0][-1])
        check("push then rsync", CALLS[1][0][0] == "rsync"
              and CALLS[1][0][-1] == "u@h:~/.vislang/env.tar.gz")
        check("push mkdir failure -> False",
              with_fake(lambda c, k: Result(1, b""),
                        lambda: push_file(KEY, "/a", "/x/b")) is False)
    finally:
        my_download._have_cmd = real_have

    print(f"\nALL {len(PASS)} CHECKS PASSED")


if __name__ == "__main__":
    main()
