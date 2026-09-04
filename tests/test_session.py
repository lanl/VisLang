"""Sessions: naming the ControlMaster, opening it, and holding a run without one.

Run from the repo root: python tests/test_session.py
No ssh and no dialog — subprocess.run is monkeypatched to capture argv/env, so
these tests pin command construction and the hold plumbing rather than talking to
a host. The end-to-end check against a real host is a manual step.
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["VISLANG_TRACE"] = "0"
os.environ["VISLANG_TIMING"] = "0"

import my_download
from my_download import (Connection, master_path, master_alive, close_master,
                         open_master, host_reachable, connect_command)

PASS = []
CALLS = []
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check(name, cond, detail=""):
    assert cond, f"{name}: {detail}"
    PASS.append(name)
    print(f"  ok  {name}")


class Result:
    def __init__(self, rc=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = rc, stdout, stderr


def with_fake(script, fn):
    """Run `fn` with subprocess.run replaced by `script(cmd, kwargs)`."""
    CALLS.clear()
    real = subprocess.run

    def _run(cmd, **kw):
        CALLS.append((list(cmd), kw))
        return script(list(cmd), kw)
    subprocess.run = _run
    try:
        return fn()
    finally:
        subprocess.run = real


def ssh_config(**over):
    cfg = {"hostname": "h.example.edu", "user": "u", "port": "22"}
    cfg.update(over)
    return cfg


def main():
    print("== master_path: one socket per RESOLVED endpoint ==")
    # The bug this replaced %C to prevent: `connect` and the auth probe naming
    # different sockets, so a live session reads as no session at all.
    my_download._SSHCFG_CACHE.clear()
    my_download._SSHCFG_CACHE["alias"] = ssh_config()
    my_download._SSHCFG_CACHE["u@h.example.edu"] = ssh_config()
    my_download._SSHCFG_CACHE["other"] = ssh_config(hostname="other.example.edu")

    check("alias and real hostname share one socket",
          master_path("alias") == master_path("u@h.example.edu"),
          f"{master_path('alias')} vs {master_path('u@h.example.edu')}")
    check("different hosts get different sockets",
          master_path("alias") != master_path("other"))
    check("deterministic across calls", master_path("alias") == master_path("alias"))
    check("lives under ~/.ssh",
          master_path("alias").startswith(os.path.expanduser("~/.ssh/vislang-cm-")))
    check("under the 104-byte sockaddr limit", len(master_path("alias")) < 104,
          str(len(master_path("alias"))))
    my_download._SSHCFG_CACHE["altport"] = ssh_config(port="2222")
    my_download._SSHCFG_CACHE["altuser"] = ssh_config(user="someone")
    check("port is part of the identity",
          master_path("alias") != master_path("altport"))
    check("user is part of the identity",
          master_path("alias") != master_path("altuser"))

    print("== the ambient ssh opts point at that same socket ==")
    opts = " ".join(my_download._ssh_opts("alias"))
    check("mux ControlPath is the computed one", master_path("alias") in opts)
    check("ambient commands stay BatchMode", "BatchMode=yes" in opts)
    check("ControlMaster=auto so a live master is reused", "ControlMaster=auto" in opts)

    print("== open_master: prompts OUT OF BAND, never on a pipe ==")

    def opened(cmd, kw):
        return Result(0, "Master running (pid=1)" if "-O" in cmd else "")
    ok, detail = with_fake(opened, lambda: open_master("alias", persist="4h"))
    check("reports success", ok is True, detail)
    argv, kwargs = CALLS[0]
    check("backgrounds after auth", "-f" in argv and "-N" in argv)
    check("opens a master explicitly", "ControlMaster=yes" in argv)
    check("at the computed ControlPath", f"ControlPath={master_path('alias')}" in argv)
    check("honors the persist argument", "ControlPersist=4h" in argv)
    check("BatchMode=no — the one place prompting is allowed",
          "BatchMode=no" in argv)
    check("asks for at most one password prompt",
          "NumberOfPasswordPrompts=1" in argv)
    env = kwargs.get("env") or {}
    check("hands ssh the askpass helper",
          env.get("SSH_ASKPASS", "").endswith("vislang-askpass"), env.get("SSH_ASKPASS"))
    check("forces the helper even when a tty exists",
          env.get("SSH_ASKPASS_REQUIRE") == "force")
    check("dialog title names the host", "alias" in env.get("VISLANG_ASKPASS_TITLE", ""))
    check("a per-attempt cancel-state path is passed",
          env.get("VISLANG_ASKPASS_STATE"))
    check("the cancel-state file is cleaned up",
          not os.path.exists(env.get("VISLANG_ASKPASS_STATE", "/nonexistent")))
    check("stdin is /dev/null, never the caller's",
          kwargs.get("stdin") == subprocess.DEVNULL)
    # `-f` daemonizes the master, which inherits these descriptors. On a PIPE it
    # would hold the write end open for the session's whole life (hours) and
    # subprocess.run would block reading it long after the session was up.
    check("stdout is a real file, not a pipe",
          kwargs.get("stdout") not in (subprocess.PIPE, None)
          and hasattr(kwargs.get("stdout"), "read"))
    check("stderr is a real file, not a pipe",
          kwargs.get("stderr") not in (subprocess.PIPE, None)
          and hasattr(kwargs.get("stderr"), "read"))
    check("a timeout is always set", kwargs.get("timeout"))

    print("== open_master: failures are reported, not raised ==")
    ok, detail = with_fake(lambda c, k: Result(255, "", ""),
                           lambda: open_master("alias"))
    check("failed ssh -> (False, reason)", ok is False and detail)

    def timeout(cmd, kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 1))
    ok, detail = with_fake(timeout, lambda: open_master("alias", timeout=7))
    check("an unanswered dialog times out cleanly", ok is False)
    check("the timeout says what was being waited on", "dialog" in detail, detail)

    ok, detail = with_fake(opened, lambda: open_master("alias", persist="1h"))
    real_helper = my_download.askpass_helper()
    check("the shipped askpass helper is executable",
          real_helper and os.access(real_helper, os.X_OK), str(real_helper))

    print("== master_alive / close_master ==")
    check("Master running on stdout",
          with_fake(lambda c, k: Result(0, "Master running (pid=9)"),
                    lambda: master_alive("alias")) is True)
    check("Master running on stderr",
          with_fake(lambda c, k: Result(0, "", "Master running (pid=9)"),
                    lambda: master_alive("alias")) is True)
    check("no socket -> False",
          with_fake(lambda c, k: Result(255, "", "No such file or directory"),
                    lambda: master_alive("alias")) is False)
    check("check costs no auth (-O check, not a login)",
          "-O" in CALLS[0][0] and "check" in CALLS[0][0])
    with_fake(lambda c, k: Result(0), lambda: close_master("alias"))
    check("close uses -O exit at the same path",
          "exit" in CALLS[0][0] and f"ControlPath={master_path('alias')}" in CALLS[0][0])

    print("== host_reachable: a network answer, not a credential one ==")
    real_conn = my_download.socket.create_connection

    class Sock:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    try:
        my_download.socket.create_connection = lambda addr, timeout=None: Sock()
        check("open port -> reachable", host_reachable("alias") is True)

        def refuse(addr, timeout=None):
            raise OSError("timed out")
        my_download.socket.create_connection = refuse
        check("filtered port -> unreachable", host_reachable("alias") is False)
    finally:
        my_download.socket.create_connection = real_conn

    print("== session_check: the single verdict everything shares ==")
    import remote_reduce
    real_establish = remote_reduce.establish_connection
    real_reach = remote_reduce.host_reachable
    try:
        remote_reduce.establish_connection = lambda s: Connection(
            user="u", host="h", target="u@h", batch_ok=True)
        check("a live session is no hold",
              remote_reduce.session_check("ssh://h/data") is None)

        remote_reduce.establish_connection = lambda s: Connection(
            user="u", host="h", target="u@h", batch_ok=False)
        remote_reduce.host_reachable = lambda t, timeout=8.0: True
        hold = remote_reduce.session_check("ssh://h/data")
        check("no session -> SessionHold", isinstance(hold, remote_reduce.SessionHold))
        check("reachable host is a credential problem", hold.reachable is True)
        check("hold carries the connect command",
              hold.connect_cmd == connect_command("u@h"), hold.connect_cmd)
        check("SessionHold is NOT a RemoteUnavailable (no fetch fallback)",
              not isinstance(hold, remote_reduce.RemoteUnavailable))

        remote_reduce.host_reachable = lambda t, timeout=8.0: False
        hold = remote_reduce.session_check("ssh://h/data")
        check("unreachable host is flagged as such", hold.reachable is False)
        check("and says so in the reason", "VPN" in hold.reason or "network" in hold.reason)

        print("== the handshake and the hold ==")
        import cli_core
        text = cli_core._session_handshake("ssh://h/data")
        check("unreachable handshake does not ask for a password",
              "NEEDS_SESSION" in text and "password" not in text.lower(), text)
        check("unreachable handshake names the network",
              "network" in text and "VPN" in text)

        remote_reduce.host_reachable = lambda t, timeout=8.0: True
        text = cli_core._session_handshake("ssh://h/data")
        check("reachable handshake tells the model to call connect",
              "connect(" in text and "NEEDS_SESSION" in text, text)
        check("handshake promises the secret stays out of the transcript",
              "never reaches VisLang" in text)
        check("handshake states nothing was read", "Nothing was read" in text)
    finally:
        remote_reduce.establish_connection = real_establish
        remote_reduce.host_reachable = real_reach

    print("== a held run reports NEEDS SESSION and materializes nothing ==")
    import cli_core
    import planner
    real_plan = planner.plan_pipeline
    try:
        def held_plan(node, dry_run=False, confirm=False):
            return {"kind": "save", "uri": "ssh://h/data", "steps": ["HELD"],
                    "output": None, "materialized": False, "needs_session": True,
                    "connect_cmd": "sieve connect h", "session_reason": "no session"}
        cli_core.plan_pipeline = held_plan
        ok, hold, _ = cli_core._run_one(object(), dry_run=False)
        check("_run_one classifies the session hold", hold == "session")

        spec = os.path.join(REPO, ".vislang", "test_session_spec.py")
        os.makedirs(os.path.dirname(spec), exist_ok=True)
        with open(spec, "w") as f:
            f.write('save(source("ssh://h/data"), "out.npz")\n')
        report = cli_core.do_execute(spec)
        os.remove(spec)
        check("status is NEEDS SESSION", report.startswith("Status: NEEDS SESSION"),
              report[:120])
        check("report tells the model to call connect", "connect(host)" in report)
        check("report says nothing was read", "before anything was read" in report)

        import cli
        check("the CLI exits non-zero on a session hold",
              any(report.startswith(s) for s in cli._BAD_STATUSES))
        check("the CLI exits non-zero on a NEEDS_SESSION handshake",
              "NEEDS_SESSION".startswith(cli._BAD_PREFIXES)
              or "NEEDS_SESSION\nx".startswith(cli._BAD_PREFIXES))
    finally:
        cli_core.plan_pipeline = real_plan

    print("== connect accepts whatever spelling the caller has ==")
    from cli_core import _target_of
    check("bare host", _target_of("gpu-server") == "gpu-server")
    check("ssh:// URI with a path", _target_of("ssh://gpu-server/scratch/run") == "gpu-server")
    check("ssh:// URI with a user",
          _target_of("ssh://me@gpu-server/scratch/run") == "me@gpu-server")
    check("scp-style source", _target_of("me@gpu-server:/scratch/run") == "me@gpu-server")

    print("== the askpass helper never keeps the answer ==")
    with open(os.path.join(REPO, "bin", "vislang-askpass")) as f:
        helper = f.read()
    check("renders the server's own prompt ($1), not a fixed label",
          "$1" in helper or "${1" in helper)
    check("the answer goes to stdout only — no file, no logger",
          "text returned" in helper
          and not any(bad in helper for bad in ("tee ", ">> \"$", "logger ")))
    check("syntax is valid POSIX sh",
          subprocess.run(["sh", "-n", os.path.join(REPO, "bin", "vislang-askpass")],
                         capture_output=True).returncode == 0)

    # A server offering keyboard-interactive AND password prompts once per
    # method, so a dismissed dialog used to be answered with a second one.
    # With the state file present the helper must decline instantly — no dialog,
    # which is also why this check can run unattended.
    import tempfile as _tf
    state = os.path.join(_tf.gettempdir(), "vislang-askpass-test-state")
    open(state, "w").close()
    try:
        r = subprocess.run([os.path.join(REPO, "bin", "vislang-askpass"), "Password:"],
                           capture_output=True, text=True, timeout=10,
                           env={**os.environ, "VISLANG_ASKPASS_STATE": state})
        check("a cancelled attempt declines later prompts without re-asking",
              r.returncode != 0 and not r.stdout.strip())
    finally:
        os.remove(state)

    print(f"\nALL {len(PASS)} CHECKS PASSED")


if __name__ == "__main__":
    main()
