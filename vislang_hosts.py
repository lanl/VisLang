"""Per-host remote settings: which python runs the reducer, and where the repo is.

These were two global env vars, `VISLANG_REMOTE_PYTHON` and `VISLANG_REMOTE_REPO`
— one value for every host a session might touch. Worse, `VISLANG_REMOTE_REPO`
defaulted to `os.path.dirname(__file__)`: the LOCAL repo's own directory, a path
that exists on this machine and almost certainly nowhere else. That default only
ever appeared to work because the LANL bench scripts set it explicitly; on any
other host it produced `python /Users/<you>/…/vislang_exec.py: No such file or
directory` from a remote shell, which reads like a VisLang bug rather than a
missing setting.

Resolution order, per setting:

    1. the environment  (VISLANG_REMOTE_PYTHON / VISLANG_REMOTE_REPO / …)
       — still wins, so existing setups, the bench scripts and the tests behave
         exactly as before
    2. `.vislang/hosts.json`, keyed by host
    3. None — and callers must say what is missing rather than guess

A host may be named by its ssh alias or its real hostname; both find the same
entry, so `ssh://gpu-server/…` and `ssh://gpu-2024-a100a.eecs.wsu.edu/…` share
one line of configuration.

    {
      "gpu-server": {
        "python": "/home/.../miniconda3/envs/vislang/bin/python",
        "repo":   "/home/.../projects/VisLang",
        "tmp":    "/tmp"
      }
    }
"""

import json
import os

from vislang_paths import home

_CACHE = {}          # host -> settings dict (or {}); one file read per process


def hosts_file():
    """Where per-host settings live. `$VISLANG_HOSTS` overrides; else
    `<home>/hosts.json`, alongside the other caches."""
    return os.environ.get("VISLANG_HOSTS") or os.path.join(home(), "hosts.json")


def _load():
    try:
        with open(hosts_file(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}                       # absent or malformed -> no settings, not an error
    return data if isinstance(data, dict) else {}


def _resolved(name):
    """`name`'s real hostname per ssh config, or `name` itself."""
    try:
        from my_download import _ssh_config
        return _ssh_config(name).get("hostname") or name
    except Exception:
        return name


def host_settings(host):
    """The `.vislang/hosts.json` entry for `host`, or {}.

    Matching resolves BOTH sides through ssh config rather than only the query.
    A file keyed by the alias `gpu-server` must also answer a spec that spells the
    source `ssh://gpu-2024-a100a.eecs.wsu.edu/…`, and alias→hostname resolution
    alone only finds the entry when the query happens to use the same spelling
    the file does."""
    if host in _CACHE:
        return _CACHE[host]
    data = _load()
    found = data.get(host)
    if not isinstance(found, dict):
        found = {}
        target = _resolved(host)
        for key, entry in data.items():
            if isinstance(entry, dict) and _resolved(key) == target:
                found = entry
                break
    _CACHE[host] = found
    return found


def clear_cache():
    """Forget loaded settings — call after writing hosts.json in-process."""
    _CACHE.clear()


def remote_python(host=None):
    """Interpreter that runs vislang_exec on `host`, or None if unconfigured.

    Falls back to the bare name `python` ONLY when nothing is configured
    anywhere, preserving the historical behavior for hosts where the login shell
    already puts the right interpreter on PATH."""
    return (os.environ.get("VISLANG_REMOTE_PYTHON")
            or (host_settings(host).get("python") if host else None)
            or "python")


def remote_repo(host=None):
    """VisLang checkout on `host`, or None when it is not configured.

    None rather than a guess: the old local-path default turned a missing setting
    into a confusing remote error, and there is no value here we can infer that
    is more likely right than wrong."""
    return (os.environ.get("VISLANG_REMOTE_REPO")
            or (host_settings(host).get("repo") if host else None))


def remote_tmp(host=None, default="/tmp"):
    """Staging dir for the shipped plan and the reduced result on `host`."""
    return (os.environ.get("VISLANG_REMOTE_TMP")
            or (host_settings(host).get("tmp") if host else None)
            or default)


def describe(host):
    """One-line summary of what is configured for `host` (for reports)."""
    repo, py = remote_repo(host), remote_python(host)
    return f"python={py or '(unset)'} repo={repo or '(unset)'}"


def preflight(conn):
    """Can this host actually run the reducer? {'ok', 'repo', 'python', 'problems'}.

    A session only buys the right to run things; it does not make a checkout or a
    numpy appear. Both are configuration, and one ssh round trip answers both —
    while the user is already looking at `connect`'s output, rather than as a
    remote traceback in the middle of a materialize."""
    import shlex
    from my_download import run_remote
    repo, py = remote_repo(conn.host), remote_python(conn.host)
    if not repo:
        return {"ok": False, "repo": repo, "python": py, "problems": [
            f"no VisLang checkout configured for {conn.host} — add `repo` for it "
            f"in {hosts_file()}"]}
    cmd = (f"test -f {shlex.quote(repo)}/vislang_exec.py && echo REPO_OK; "
           f"{shlex.quote(py)} -c 'import numpy, h5py' 2>&1 && echo DEPS_OK")
    _, out, _ = run_remote(conn, cmd)
    problems = []
    if "REPO_OK" not in out:
        problems.append(f"{repo}/vislang_exec.py not found on {conn.host}")
    if "DEPS_OK" not in out:
        why = next((ln for ln in out.splitlines() if "Error" in ln), "")
        problems.append(f"{py} on {conn.host} cannot import numpy + h5py"
                        + (f" — {why.strip()}" if why else ""))
    return {"ok": not problems, "repo": repo, "python": py, "problems": problems}
