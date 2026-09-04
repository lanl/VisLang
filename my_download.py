import os
import re
import shlex
import socket
import hashlib
import subprocess
from dataclasses import dataclass

import vislang_timing as timing        # counts ssh round trips + wire bytes (no-op when off)


# A remote endpoint and whether it can be driven NON-INTERACTIVELY. Built once by
# establish_connection() and reused by transfer()/run_remote() — so a session of
# transfers and remote commands authenticates once rather than per call.
@dataclass
class Connection:
    user: str        # may be None
    host: str
    target: str      # "user@host" or "host"
    batch_ok: bool   # a BatchMode command succeeds: nothing here will ever prompt


# Probe a remote endpoint's auth ONCE. No bytes transferred. Splits the source,
# then tries a trivial BatchMode ssh command: if it succeeds every ssh/rsync/scp
# below will too.
#
# What actually satisfies that probe is anything that authenticates without
# asking a human: an installed key, an agent identity, a Kerberos ticket
# (GSSAPI), or — the case `connect` exists for — a live ControlMaster session
# someone already authenticated. It is NOT specifically key auth, which is why
# this records `batch_ok` rather than an auth method.
_CONN_CACHE = {}        # target -> Connection      (auth probed once per run)
_PROBE_CACHE = {}       # (target, path) -> dict    (one stat+hash per path per run)
_HEADER_BYTES = 65536   # header window the catalog's identity hash reads


def clear_remote_caches():
    """Drop the per-run connection, probe and ssh-config caches.

    These are deliberately RUN-scoped, not process-scoped. The probe holds
    size and mtime, which is exactly what the extent catalog keys source identity
    on — caching that across runs would let a file change underneath us and turn
    a stale identity into a false cache hit, i.e. wrong data. The front ends call
    this at the start of every run, so within a run the facts are consistent and
    across runs they are re-read.

    `_SSHCFG_CACHE` is dropped too. It holds no data facts, but a session opened
    between two calls must be noticed: the connection verdict is only as fresh as
    the caches behind it."""
    _CONN_CACHE.clear()
    _PROBE_CACHE.clear()
    _SSHCFG_CACHE.clear()


def establish_connection(remote_source):
    """Probe a remote endpoint's auth once per run and reuse it.

    The probe is a full ssh session (`ssh … true`), which on a high-latency or
    brokered path costs as much as any real command. Several layers legitimately
    ask for a connection to the same host in one run — the folder check, the
    reduce, the transfer — and each re-probe was a wasted round trip."""
    user, host, _ = _parse_remote(remote_source)
    target = f"{user}@{host}" if user else host
    cached = _CONN_CACHE.get(target)
    if cached is not None:
        return cached
    conn = Connection(user=user, host=host, target=target,
                      batch_ok=_ssh_query(target, "true") is not None)
    _CONN_CACHE[target] = conn
    return conn


def remote_probe(connection, remote_path):
    """Type, size, mtime and header hash of one remote path in ONE round trip.

    Two independent layers want facts about the same path in a single run: the
    planner asks "is this a directory?" (a timeseries folder) and the catalog asks
    "what is this file's identity?" (size + mtime + a hash of its first bytes).
    Those were three separate ssh invocations. One command answers all of it, and
    the answer is cached for the rest of the run so the second asker pays nothing.

    Returns {'kind', 'size', 'mtime', 'header_md5', 'nbytes'} or None. `-L`
    throughout: a symlink is a way of naming data, not a kind of data, so every
    fact here is the target's."""
    if not connection.batch_ok:
        return None
    ck = (connection.target, remote_path)
    if ck in _PROBE_CACHE:
        return _PROBE_CACHE[ck]
    q = shlex.quote(remote_path)
    # One session, two facts: stat for type/size/mtime, then the header window for
    # the identity hash. `head` on a directory fails harmlessly (stderr dropped);
    # the hash is meaningless there and no caller uses it.
    out = _ssh_query(connection.target,
                     f"stat -Lc '%F|%s|%Y' {q}; head -c {_HEADER_BYTES} {q} "
                     f"2>/dev/null | md5sum")
    if not out:
        return None
    lines = [ln for ln in out.splitlines() if ln.strip()]
    try:
        kind, size, mtime = lines[0].split("|")
        info = {"kind": kind.strip(), "size": int(size), "mtime": int(float(mtime)),
                "header_md5": (lines[1].split()[0] if len(lines) > 1 else None),
                "nbytes": _HEADER_BYTES}
    except (IndexError, ValueError):
        return None
    _PROBE_CACHE[ck] = info
    return info


# Move bytes for one file/dir over an established Connection.
#
# Transfer strategy:
#     1. rsync over ssh (progress display, resumes partial transfers)
#     2. scp (if rsync is missing)
# Both run in BatchMode and so require `connection.batch_ok`. There is no
# in-process password fallback: prompting from here has no terminal to read
# (under MCP `getpass` would read the JSON-RPC stream), which is what `connect`
# and its out-of-band dialog exist to solve. A host with no session is refused
# early, by the caller, rather than half-transferred here.
def transfer(connection, remote_path, local_path, size_warn_mb=500):
    target = connection.target
    remote_source = f"{target}:{remote_path}"

    # Ensure destination directory exists
    dest_dir = os.path.dirname(local_path)
    if dest_dir:
        os.makedirs(dest_dir, exist_ok=True)

    # Skip if we already have an identical copy (MD5 match with remote).
    # Best-effort: needs key auth for the remote md5sum; silently skipped otherwise.
    if os.path.isfile(local_path):
        remote_md5 = _remote_md5(target, remote_path)
        if remote_md5 and remote_md5 == _local_md5(local_path):
            print(f"✓ {local_path} already matches remote (MD5) — skipping download.")
            timing.count("transfers_skipped_md5")   # reuse: zero bytes crossed
            return local_path

    # Warn before pulling a large file. NON-INTERACTIVE: the MCP/Jupyter flow has
    # no TTY, and confirmation now lives in the planner's budget gate (the single
    # place a run is held pending confirm). So we log the size and proceed rather
    # than block on input(). Best-effort: needs key auth for the remote stat.
    size_bytes = _remote_size(target, remote_path)
    if size_bytes and size_bytes > size_warn_mb * 1e6:
        print(f"⚠ Remote file is {size_bytes / 1e6:.1f} MB (> {size_warn_mb} MB) — "
              f"downloading (confirmed upstream by the budget gate).")

    print(f"Downloading from {connection.host}:")
    print(f"  remote: {remote_path}")
    print(f"  local:  {local_path}")

    # Key-based transfer (rsync preferred, scp fallback) when the probe found a key.
    #
    # `-L` (--copy-links) transfers what a symlink POINTS AT, not the link. `-a`
    # implies `-l`, which on a symlinked dataset copies the link's ~72 bytes and
    # exits 0 — a "successful" transfer of a locally-dangling link whose target
    # path (`/projects/...`) does not exist here, so the very next getsize raises
    # FileNotFoundError and the run records 0 wire bytes. Every metadata probe
    # already dereferences for the same reason (remote_stat's `stat -Lc`,
    # remote_timestep_files_stat's `find -L`): a link is a way of naming data,
    # not a kind of data. scp follows links already.
    with timing.phase("transfer", remote=remote_path) as _t:
        if connection.batch_ok:
            if _have_cmd('rsync'):
                ok, err = _run_transfer([
                    'rsync', '-aL', '--progress', '--partial',
                    '-e', _ssh_transport(target),
                    remote_source, local_path
                ])
                if ok:
                    _t["bytes"] = timing.dir_bytes(local_path)
                    return _measured(local_path, "file")
                if not _is_auth_error(err):
                    raise RuntimeError(f"rsync failed:\n{err}")
            elif _have_cmd('scp'):
                ok, err = _run_transfer([
                    'scp', '-r', *_ssh_opts(target),
                    remote_source, local_path
                ])
                if ok:
                    _t["bytes"] = timing.dir_bytes(local_path)
                    return _measured(local_path, "file")
                if not _is_auth_error(err):
                    raise RuntimeError(f"scp failed:\n{err}")

    raise RuntimeError(
        f"Could not authenticate to {connection.host}.\n\n"
        f"Open a session first — the password is typed into a dialog, never "
        f"handled by VisLang:\n"
        f"  sieve connect {connection.target}\n\n"
        f"(Or, for a host that accepts them, install a key once with "
        f"`ssh-copy-id {connection.target}` and no session is needed.)"
    )


# Pull a whole remote DIRECTORY's contents into local_dir in one transfer.
# Trailing slashes (`remote:dir/` -> `local/`) put the CONTENTS directly in
# local_dir rather than nesting a `dir/` under it — so a remote timeseries save
# lands as the user's save dir. Key auth only, no size prompt (non-interactive
# batch). Returns local_dir on success, None on failure. Used by the one-shot
# remote folder reduce (remote_folder_reduce).
def transfer_dir(connection, remote_dir, local_dir):
    if not connection.batch_ok:
        return None
    os.makedirs(local_dir, exist_ok=True)
    src = f"{connection.target}:{remote_dir.rstrip('/')}/"
    dst = local_dir.rstrip("/") + "/"
    with timing.phase("transfer_dir", remote=remote_dir) as _t:
        if _have_cmd('rsync'):
            # -L for the same reason as transfer(): a timeseries folder assembled
            # from `…#N` symlinks would otherwise arrive as N dangling links.
            ok, err = _run_transfer(['rsync', '-aL', '--partial',
                                     '-e', _ssh_transport(connection.target), src, dst])
            if ok:
                _t["bytes"] = timing.dir_bytes(local_dir)
                return _measured(local_dir, "dir")
            if not _is_auth_error(err):
                raise RuntimeError(f"rsync (dir) failed:\n{err}")
        elif _have_cmd('scp'):
            # scp -r needs `dir/.` to copy contents into an existing dst.
            ok, err = _run_transfer(['scp', '-r', *_ssh_opts(target),
                                     f"{connection.target}:{remote_dir.rstrip('/')}/.",
                                     dst])
            if ok:
                _t["bytes"] = timing.dir_bytes(local_dir)
                return _measured(local_dir, "dir")
            if not _is_auth_error(err):
                raise RuntimeError(f"scp (dir) failed:\n{err}")
        return None


# Back-compat shim: establish a connection, then transfer one file.
#     download("user@host:/remote/file.hdf5", "data/file.hdf5")
def download(remote_source, local_path, size_warn_mb=500):
    conn = establish_connection(remote_source)
    _, _, remote_path = _parse_remote(remote_source)
    return transfer(conn, remote_path, local_path, size_warn_mb)


# Split "user@host:/path" into (user, host, path). User is optional.
def _parse_remote(remote_source):
    m = re.match(r'^(?:([^@:]+)@)?([^:]+):(.+)$', remote_source)
    if not m:
        raise ValueError(
            f"Invalid remote source: {remote_source!r}\n"
            "Expected format: 'user@host:/path/to/file' or 'host:/path/to/file'"
        )
    return m.group(1), m.group(2), m.group(3)


def _have_cmd(name):
    return subprocess.run(['which', name], capture_output=True).returncode == 0


# --- SSH connection multiplexing --------------------------------------------
# Without a shared master, every ssh/rsync/scp subprocess pays a full TCP +
# (on GSSAPI/Kerberos hosts like LANL darwin) auth handshake — a 20-file remote
# folder run does ~144-184 of them, minutes of pure overhead. OpenSSH
# multiplexing fixes this: the FIRST connection opens a master socket at
# ControlPath; every later connection to the same host rides it as a cheap
# channel (no TCP, no re-auth — GSSAPI is delegated once on the master). Every
# ssh/rsync/scp site below funnels the SAME opts, so they all share one master.
_MUX_PERSIST_DEFAULT = "120"
_SESSION_PERSIST_DEFAULT = "8h"     # a session `connect` opened cost a typed secret
_SSHCFG_CACHE = {}                  # target -> {'hostname','user','port'}


def _ssh_config(target):
    """Resolved ssh config for `target`: {'hostname', 'user', 'port'}.

    `ssh -G` applies ~/.ssh/config — aliases, HostName, User, Port — and prints
    the result WITHOUT connecting: no network, no auth, no prompt."""
    if target in _SSHCFG_CACHE:
        return _SSHCFG_CACHE[target]
    cfg = {"hostname": target.rpartition("@")[2], "user": None, "port": "22"}
    try:
        r = subprocess.run(['ssh', '-G', target], capture_output=True,
                           text=True, timeout=10)
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                key, _, value = line.partition(" ")
                if key.lower() in ("hostname", "user", "port") and value.strip():
                    cfg[key.lower()] = value.strip()
    except (OSError, subprocess.SubprocessError):
        pass                                    # fall back to the literal target
    _SSHCFG_CACHE[target] = cfg
    return cfg


def master_path(target):
    """Path of the ControlMaster socket VisLang uses for `target`.

    Keyed by the RESOLVED `user@hostname:port` rather than the spelling, so an
    alias and its FQDN name one session.

    This replaced `%C`, which ssh expands itself from local-host/host/port/user.
    Python could not compute that name, so `connect` and the auth probe could
    look at different sockets — and a live master under a name the probe does not
    check is indistinguishable from having no session at all. `%C` also hashes
    none of the -o options, so nothing is lost by naming the socket ourselves.
    ~ is pre-expanded because rsync's `-e ssh` gets no shell to expand it."""
    cfg = _ssh_config(target)
    ident = f"{cfg.get('user') or ''}@{cfg['hostname']}:{cfg.get('port') or '22'}"
    digest = hashlib.sha1(ident.encode()).hexdigest()[:16]
    # ~/.ssh/vislang-cm-<16 hex> — comfortably under the 104-byte sockaddr limit.
    return os.path.join(os.path.expanduser("~/.ssh"), f"vislang-cm-{digest}")


def _mux_opts(target):
    """OpenSSH multiplexing -o flags, or [] when disabled / uncreatable.

    The FIRST connection opens a master; every later one to the same host rides
    it as a cheap channel (no TCP, no re-auth). `ControlMaster=auto` means a host
    that authenticates on its own still gets multiplexing for free, while a
    password host uses the master `connect` opened."""
    if os.environ.get("VISLANG_SSH_MUX", "1") == "0":
        return []
    try:
        os.makedirs(os.path.expanduser("~/.ssh"), exist_ok=True)
    except OSError:
        return []                               # can't stage a socket -> degrade
    persist = os.environ.get("VISLANG_SSH_MUX_PERSIST", _MUX_PERSIST_DEFAULT)
    return ['-o', 'ControlMaster=auto',
            '-o', f'ControlPath={master_path(target)}',
            '-o', f'ControlPersist={persist}']


def _ssh_opts(target):
    """The full `ssh -o` list shared by ssh/scp: base flags + mux.

    Do NOT add `GSSAPIAuthentication=no` here as a latency optimization: on a
    Kerberos-authenticated host (LANL Darwin, where the server offers
    publickey,gssapi-keyex,gssapi-with-mic,password,hostbased but the user holds
    no installed key) GSSAPI IS the login. Disabling it does not make the ~16 s
    session setup cheaper — it makes every connection fail in 0.27 s with
    'Permission denied', which is easy to mistake for a speedup if you time the
    command without checking its exit status."""
    return ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', *_mux_opts(target)]


def _ssh_transport(target):
    """The `ssh …` string for rsync's -e — same opts, so rsync shares the
    master too. shlex-quoted since it is one argv token passed to rsync."""
    return "ssh " + " ".join(shlex.quote(o) for o in _ssh_opts(target))


# --- Sessions: authenticating a host that wants a typed secret ----------------
# VisLang never handles a password. `open_master` hands OpenSSH an SSH_ASKPASS
# helper; ssh forks it, the helper shows a dialog, and the answer goes back to
# ssh over ITS OWN pipe. Nothing in this process, in the MCP transport, or in the
# model's context ever holds the secret — the only thing that outlives the prompt
# is an authenticated socket, and all `establish_connection` learns is that a
# BatchMode command now succeeds.


def askpass_helper():
    """The checked-in SSH_ASKPASS helper, or None when it can't be executed."""
    path = os.environ.get("VISLANG_ASKPASS") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "bin", "vislang-askpass")
    return path if os.access(path, os.X_OK) else None


def master_alive(target):
    """Is a VisLang session live for `target`? Instant, and costs no auth —
    `-O check` talks to the local socket, not the remote host."""
    try:
        r = subprocess.run(
            ['ssh', '-O', 'check', '-o', f'ControlPath={master_path(target)}', target],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and "Master running" in (r.stdout + r.stderr)


def host_reachable(target, timeout=8.0):
    """Can we open a TCP connection to the host at all?

    Separates "the VPN is down" from "we have no credential". Without this the
    two are the same failure — a BatchMode probe fails either way — and an
    unreachable host would be answered with a password dialog that cannot
    possibly help."""
    cfg = _ssh_config(target)
    try:
        port = int(cfg.get("port") or 22)
    except ValueError:
        port = 22
    try:
        with socket.create_connection((cfg["hostname"], port), timeout=timeout):
            return True
    except OSError:
        return False


def open_master(target, persist=None, timeout=120):
    """Open a ControlMaster for `target`, prompting for the secret out of band.
    Returns (ok, detail); `detail` is the reason when ok is False.

    stdout/stderr go to a temp FILE, not a pipe. `-f` backgrounds ssh after
    authenticating, and the daemonized master inherits whatever it was given: on
    a pipe it would hold the write end open for the master's whole lifetime
    (hours), so `subprocess.run` would block reading it long after the session
    was up. A file descriptor is handed over harmlessly."""
    import tempfile
    helper = askpass_helper()
    if helper is None:
        return False, ("no askpass helper — bin/vislang-askpass is missing or not "
                       "executable, and there is no terminal to prompt on")
    persist = persist or os.environ.get("VISLANG_SESSION_PERSIST",
                                        _SESSION_PERSIST_DEFAULT)
    env = dict(os.environ)
    env["SSH_ASKPASS"] = helper
    env["SSH_ASKPASS_REQUIRE"] = "force"    # use the helper even when a tty exists
    env["VISLANG_ASKPASS_TITLE"] = f"VisLang — {target}"
    # A server offering BOTH keyboard-interactive and password asks twice — once
    # per method — so a declined dialog would be followed by another. The helper
    # touches this path when the user cancels and declines silently thereafter.
    state = os.path.join(tempfile.gettempdir(),
                         f"vislang-askpass-{os.getpid()}-{id(target):x}")
    env["VISLANG_ASKPASS_STATE"] = state
    cmd = ['ssh', '-f', '-N',
           '-o', 'ControlMaster=yes',
           '-o', f'ControlPath={master_path(target)}',
           '-o', f'ControlPersist={persist}',
           '-o', 'BatchMode=no',               # the one place prompting is allowed
           '-o', 'NumberOfPasswordPrompts=1',
           '-o', 'ConnectTimeout=15',
           target]
    timing.count("session_opens")
    try:
        with tempfile.TemporaryFile(mode="w+") as log:
            proc = subprocess.run(cmd, env=env, stdin=subprocess.DEVNULL,
                                  stdout=log, stderr=log, timeout=timeout)
            log.seek(0)
            text = log.read()
    except subprocess.TimeoutExpired:
        return False, (f"no answer within {timeout}s — the password dialog was "
                       f"left unanswered, or no dialog could be shown")
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"{type(e).__name__}: {e}"
    finally:
        declined = os.path.exists(state)
        try:
            os.remove(state)
        except OSError:
            pass
    if proc.returncode == 0 and master_alive(target):
        return True, ""
    if declined:
        # Distinguish "said no" from "wrong password": the remote's message is
        # the same 'Permission denied' either way, and telling someone their
        # password was rejected when they dismissed the box is just wrong.
        return False, ("the password dialog was dismissed or timed out — no "
                       "password was sent. Run it again when you are ready.")
    # The login banner is usually the bulk of `text`; keep the tail, where the
    # actual failure lands.
    return False, (text.strip()[-800:] or f"ssh exited {proc.returncode}")


def connect_command(target):
    """The command that opens a session for `target` — one string, used verbatim
    in every hold, handshake and error so the user is never shown two spellings
    of the same instruction."""
    return f"sieve connect {target}"


def close_master(target):
    """Tear down the session for `target`. True if one was there and is gone."""
    try:
        r = subprocess.run(
            ['ssh', '-O', 'exit', '-o', f'ControlPath={master_path(target)}', target],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


# Run a one-off ssh command in BatchMode (key auth only, never prompts).
# Returns stdout on success, or None if it failed / auth unavailable.
def _ssh_query(target, command):
    timing.count("ssh_query")          # one metadata round trip (stat/md5/auth probe)
    result = subprocess.run(
        ['ssh', *_ssh_opts(target), target, command],
        capture_output=True, text=True
    )
    return result.stdout if result.returncode == 0 else None


# Remote file size in bytes (GNU stat), or None if unavailable.
def _remote_size(target, remote_path):
    out = _ssh_query(target, f"stat -Lc %s {shlex.quote(remote_path)}")   # -L: follow symlinks
    try:
        return int(out.strip()) if out else None
    except ValueError:
        return None


# Remote MD5 hex digest, or None if unavailable.
def _remote_md5(target, remote_path):
    out = _ssh_query(target, f"md5sum {shlex.quote(remote_path)}")
    return out.split()[0] if out else None


# Local MD5 hex digest, streamed in chunks so large files don't blow up memory.
def _local_md5(path):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


# Run a transfer command, streaming progress output. Returns (ok, stderr_text).
def _run_transfer(cmd):
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1
    )
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            print(f"  {line}")
    proc.wait()
    err = proc.stderr.read()
    return proc.returncode == 0, err


def _is_auth_error(stderr_text):
    markers = ['permission denied', 'host key verification failed',
               'authentication failed', 'publickey']
    text = stderr_text.lower()
    return any(m in text for m in markers)


# The paramiko SFTP fallback that used to live here is gone. It called
# getpass.getpass(), which needs a controlling terminal: under the MCP server
# there is none, so getpass falls back to sys.stdin — the JSON-RPC pipe — and
# would consume protocol bytes rather than a password. Where a terminal did
# exist it stole input from the client's own UI. Interactive authentication now
# happens once, out of band, in `open_master`; everything here stays BatchMode.


def _measured(local_path, kind):
    """Record a completed transfer's size + count it, then report it. Bytes are
    taken from what landed on local disk (a directory tree is walked), which is
    the only number both rsync and scp agree on. `wire_bytes` accumulates across
    a run — the headline "how much crossed the network" figure."""
    n = timing.dir_bytes(local_path)
    timing.count("transfers")
    if n:
        timing.count("wire_bytes", n)
        timing.count(f"wire_bytes_{kind}", n)
    return _report_success(local_path)


def _report_success(local_path):
    if os.path.isdir(local_path):
        total = sum(
            os.path.getsize(os.path.join(d, f))
            for d, _, files in os.walk(local_path) for f in files
        )
    else:
        total = os.path.getsize(local_path)
    print(f"\n✓ Downloaded {total / 1e6:.1f} MB to {local_path}")
    return local_path


# ===========================================================================
# Remote probes for the site-aware planner (REMOTE_COMPUTE_PLAN.md Phase 2).
# All of these need a NON-INTERACTIVE connection: with batch_ok False they
# return the documented "unavailable" value instead of prompting — the caller
# (remote_reduce) treats that as RemoteUnavailable and falls back.
#
# All ssh/scp calls here go through _ssh_opts(target) (base + multiplexing), and
# rsync through _ssh_transport(target), so every probe rides the shared master.
# ===========================================================================


def remote_stat(connection, remote_path):
    """(size_bytes, mtime_epoch) of a remote file in one round-trip, or None.
    Feeds the catalog's source identity (GNU stat on the HPC targets).

    `-L` dereferences: for a symlinked dataset the link's own size (~72 B) and
    creation time carry no information about the data, so keying cache identity on
    them would both misreport the source size to the cost gate and fail to notice
    the target changing underneath.

    Served from `remote_probe`'s cache when this run already probed the path, so
    the common case costs nothing; otherwise it runs its own single command."""
    if not connection.batch_ok:
        return None
    hit = _PROBE_CACHE.get((connection.target, remote_path))
    if hit is not None:
        return hit["size"], hit["mtime"]
    out = _ssh_query(connection.target, f"stat -Lc '%s %Y' {shlex.quote(remote_path)}")
    try:
        size, mtime = out.split()
        return int(size), int(mtime)
    except (AttributeError, ValueError):
        return None


def remote_header_hash(connection, remote_path, nbytes=_HEADER_BYTES):
    """md5 of just the file's first nbytes — cheap identity for the catalog
    without hashing a multi-GB file. None if unavailable.

    Served from `remote_probe`'s cache when this run already probed the path AND
    asked for the same window; a different `nbytes` is a different question, so it
    falls through to its own command rather than returning the wrong hash."""
    if not connection.batch_ok:
        return None
    hit = _PROBE_CACHE.get((connection.target, remote_path))
    if hit is not None and hit.get("nbytes") == int(nbytes) and hit.get("header_md5"):
        return hit["header_md5"]
    out = _ssh_query(connection.target,
                     f"head -c {int(nbytes)} {shlex.quote(remote_path)} | md5sum")
    return out.split()[0] if out else None


def remote_file_md5(connection, remote_path):
    """Full-file remote md5 (used to hash-verify the shipped image, which is
    small enough to afford it). None if unavailable."""
    if not connection.batch_ok:
        return None
    return _remote_md5(connection.target, remote_path)


def measure_bandwidth(connection, mb=4):
    """Rough link speed in bytes/sec, measured once by streaming `mb` MB of
    zeros from the remote. Input for the finer cost gate; None if unavailable."""
    import time
    if not connection.batch_ok:
        return None
    cmd = ['ssh', *_ssh_opts(connection.target), connection.target,
           f"dd if=/dev/zero bs=1M count={int(mb)} status=none"]
    timing.count("bandwidth_probes")
    t0 = time.monotonic()
    result = subprocess.run(cmd, capture_output=True)
    elapsed = time.monotonic() - t0
    if result.returncode != 0 or not result.stdout or elapsed <= 0:
        return None
    bps = len(result.stdout) / elapsed
    # The link speed the cost gate was fed — recorded so a predicted time band
    # can be read back against the bandwidth that produced it.
    timing.note(probe_bw_bps=round(bps, 1))
    return bps


def run_remote(connection, command, stdin_bytes=None, timeout=None):
    """Run one remote command in BatchMode; (rc, stdout, stderr) as text.
    `stdin_bytes` pipes a payload to the command — how plan.json reaches
    vislang_exec without landing a file first."""
    if not connection.batch_ok:
        return 255, '', 'remote commands need a live session (sieve connect <host>)'
    timing.count("ssh_exec")           # one remote command round trip
    if command.lstrip().startswith("srun"):
        timing.count("remote_jobs")    # a scheduler step — the O(N) vs O(1) metric
    cmd = ['ssh', *_ssh_opts(connection.target), connection.target, command]
    try:
        result = subprocess.run(cmd, input=stdin_bytes, capture_output=True,
                                timeout=timeout)
    except subprocess.TimeoutExpired:
        return 254, '', f'timed out after {timeout}s'
    return (result.returncode,
            result.stdout.decode(errors='replace'),
            result.stderr.decode(errors='replace'))


def push_file(connection, local_path, remote_path):
    """Push one local file to the remote (mirror of transfer()), creating the
    parent dir first. Paths under ~ are passed unquoted so the remote shell
    expands them (we control these paths; they carry no untrusted input)."""
    if not connection.batch_ok:
        return False
    parent = os.path.dirname(remote_path)
    if parent:
        rc, _, _ = run_remote(connection, f"mkdir -p {parent}")
        if rc != 0:
            return False
    tool = 'rsync' if _have_cmd('rsync') else 'scp'
    if tool == 'rsync':
        cmd = ['rsync', '-a', '--partial', '-e', _ssh_transport(connection.target),
               local_path, f"{connection.target}:{remote_path}"]
    else:
        cmd = ['scp', *_ssh_opts(connection.target),
               local_path, f"{connection.target}:{remote_path}"]
    return subprocess.run(cmd, capture_output=True).returncode == 0
