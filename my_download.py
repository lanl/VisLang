import os
import re
import shlex
import hashlib
import subprocess
from dataclasses import dataclass


# A remote endpoint with its probed auth method. Built once by
# establish_connection() and reused by transfer()/run_remote() — so a session
# of transfers and remote commands authenticates once rather than per call.
@dataclass
class Connection:
    user: str        # may be None
    host: str
    target: str      # "user@host" or "host"
    method: str      # 'ssh-key' (BatchMode works) or 'password' (paramiko fallback)


# Probe a remote endpoint's auth ONCE. No bytes transferred. Splits the source,
# then tries a trivial BatchMode ssh command: if it succeeds, key auth works
# (rsync/scp will too); otherwise we fall back to a paramiko password login at
# transfer time.
def establish_connection(remote_source):
    user, host, _ = _parse_remote(remote_source)
    target = f"{user}@{host}" if user else host
    method = "ssh-key" if _ssh_query(target, "true") is not None else "password"
    return Connection(user=user, host=host, target=target, method=method)


# Move bytes for one file/dir over an established Connection.
#
# Transfer strategy (unchanged from the old monolithic download):
#     1. rsync over ssh (progress display, resumes partial transfers)
#     2. scp (if rsync is missing)
#     3. paramiko SFTP with password prompt (if SSH keys are not set up)
# rsync/scp run in BatchMode (key auth only) because a password prompt inside a
# Jupyter kernel would hang with no terminal to type into.
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
            return local_path

    # Warn + confirm before pulling a large file.
    # Best-effort: needs key auth for the remote stat; silently skipped otherwise.
    size_bytes = _remote_size(target, remote_path)
    if size_bytes and size_bytes > size_warn_mb * 1e6:
        resp = input(f"Remote file is {size_bytes / 1e6:.1f} MB "
                     f"(> {size_warn_mb} MB). Download? [y/N]: ").strip().lower()
        if resp not in ('y', 'yes'):
            print("Download cancelled.")
            return None

    print(f"Downloading from {connection.host}:")
    print(f"  remote: {remote_path}")
    print(f"  local:  {local_path}")

    # Key-based transfer (rsync preferred, scp fallback) when the probe found a key.
    if connection.method == "ssh-key":
        if _have_cmd('rsync'):
            ok, err = _run_transfer([
                'rsync', '-a', '--progress', '--partial',
                '-e', _ssh_transport(),
                remote_source, local_path
            ])
            if ok:
                return _report_success(local_path)
            if not _is_auth_error(err):
                raise RuntimeError(f"rsync failed:\n{err}")
        elif _have_cmd('scp'):
            ok, err = _run_transfer([
                'scp', '-r', *_ssh_opts(),
                remote_source, local_path
            ])
            if ok:
                return _report_success(local_path)
            if not _is_auth_error(err):
                raise RuntimeError(f"scp failed:\n{err}")
        print("⚠ SSH key authentication not available, trying password login...")

    # Password fallback (works inside Jupyter).
    if _download_paramiko(connection.user, connection.host, remote_path, local_path):
        return _report_success(local_path)

    u = connection.user
    raise RuntimeError(
        "Could not authenticate to the remote server.\n\n"
        "To enable passwordless transfers, set up SSH keys (one time):\n"
        f"  ssh-keygen -t ed25519            # press enter at every prompt\n"
        f"  ssh-copy-id {u + '@' if u else ''}{connection.host}   # type your password once\n\n"
        "After that, transfers will work without a password."
    )


# Pull a whole remote DIRECTORY's contents into local_dir in one transfer.
# Trailing slashes (`remote:dir/` -> `local/`) put the CONTENTS directly in
# local_dir rather than nesting a `dir/` under it — so a remote timeseries save
# lands as the user's save dir. Key auth only, no size prompt (non-interactive
# batch). Returns local_dir on success, None on failure. Used by the one-shot
# remote folder reduce (remote_folder_reduce).
def transfer_dir(connection, remote_dir, local_dir):
    if connection.method != "ssh-key":
        return None
    os.makedirs(local_dir, exist_ok=True)
    src = f"{connection.target}:{remote_dir.rstrip('/')}/"
    dst = local_dir.rstrip("/") + "/"
    if _have_cmd('rsync'):
        ok, err = _run_transfer(['rsync', '-a', '--partial',
                                 '-e', _ssh_transport(), src, dst])
        if ok:
            return _report_success(local_dir)
        if not _is_auth_error(err):
            raise RuntimeError(f"rsync (dir) failed:\n{err}")
    elif _have_cmd('scp'):
        # scp -r needs `dir/.` to copy contents into an existing dst.
        ok, err = _run_transfer(['scp', '-r', *_ssh_opts(),
                                 f"{connection.target}:{remote_dir.rstrip('/')}/.",
                                 dst])
        if ok:
            return _report_success(local_dir)
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


def _mux_opts():
    """OpenSSH multiplexing -o flags, or [] when disabled / uncreatable.
    ControlPath uses %C (a hash of local-host/host/port/user — NOT the path), so
    the folder probe and every `…#N` timestep to the same host share one master.
    ~ is pre-expanded here because rsync's `-e ssh` gets no shell to expand it."""
    if os.environ.get("VISLANG_SSH_MUX", "1") == "0":
        return []
    cm_dir = os.path.expanduser("~/.ssh")
    try:
        os.makedirs(cm_dir, exist_ok=True)      # best-effort; ~/.ssh usually exists
    except OSError:
        return []                               # can't stage a socket -> degrade
    persist = os.environ.get("VISLANG_SSH_MUX_PERSIST", _MUX_PERSIST_DEFAULT)
    path = os.path.join(cm_dir, "vislang-cm-%C")   # ~73 chars < the 104-byte limit
    return ['-o', 'ControlMaster=auto',
            '-o', f'ControlPath={path}',
            '-o', f'ControlPersist={persist}']


def _ssh_opts():
    """The full `ssh -o` list shared by ssh/scp: base flags + mux."""
    return ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', *_mux_opts()]


def _ssh_transport():
    """The `ssh …` string for rsync's -e — same opts, so rsync shares the
    master too. shlex-quoted since it is one argv token passed to rsync."""
    return "ssh " + " ".join(shlex.quote(o) for o in _ssh_opts())


# Run a one-off ssh command in BatchMode (key auth only, never prompts).
# Returns stdout on success, or None if it failed / auth unavailable.
def _ssh_query(target, command):
    result = subprocess.run(
        ['ssh', *_ssh_opts(), target, command],
        capture_output=True, text=True
    )
    return result.stdout if result.returncode == 0 else None


# Remote file size in bytes (GNU stat), or None if unavailable.
def _remote_size(target, remote_path):
    out = _ssh_query(target, f"stat -c %s {shlex.quote(remote_path)}")
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


# SFTP fallback: paramiko can prompt for a password inside Jupyter.
def _download_paramiko(user, host, remote_path, local_path):
    try:
        import paramiko
    except ImportError:
        print("⚠ paramiko not installed (pip install paramiko) — cannot do password login.")
        return False

    import getpass
    import stat as statmod

    user = user or getpass.getuser()
    password = getpass.getpass(f"Password for {user}@{host}: ")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(host, username=user, password=password, timeout=15)
        sftp = client.open_sftp()

        def fetch(rpath, lpath):
            if statmod.S_ISDIR(sftp.stat(rpath).st_mode):
                os.makedirs(lpath, exist_ok=True)
                for entry in sftp.listdir(rpath):
                    fetch(f"{rpath}/{entry}", os.path.join(lpath, entry))
            else:
                size = sftp.stat(rpath).st_size
                print(f"  {rpath} ({size / 1e6:.1f} MB)")
                sftp.get(rpath, lpath)

        fetch(remote_path, local_path)
        sftp.close()
        return True
    except paramiko.AuthenticationException:
        print("✗ Wrong password.")
        return False
    finally:
        client.close()


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
# All of these are key-auth only: with connection.method != 'ssh-key' they
# return the documented "unavailable" value instead of prompting — the caller
# (remote_reduce) treats that as RemoteUnavailable and falls back.
#
# All ssh/scp calls here go through _ssh_opts() (base + multiplexing), and rsync
# through _ssh_transport(), so every probe rides the shared master.
# ===========================================================================


def remote_stat(connection, remote_path):
    """(size_bytes, mtime_epoch) of a remote file in one round-trip, or None.
    Feeds the catalog's source identity (GNU stat on the HPC targets)."""
    if connection.method != 'ssh-key':
        return None
    out = _ssh_query(connection.target, f"stat -c '%s %Y' {shlex.quote(remote_path)}")
    try:
        size, mtime = out.split()
        return int(size), int(mtime)
    except (AttributeError, ValueError):
        return None


def remote_header_hash(connection, remote_path, nbytes=65536):
    """md5 of just the file's first nbytes — cheap identity for the catalog
    without hashing a multi-GB file. None if unavailable."""
    if connection.method != 'ssh-key':
        return None
    out = _ssh_query(connection.target,
                     f"head -c {int(nbytes)} {shlex.quote(remote_path)} | md5sum")
    return out.split()[0] if out else None


def remote_file_md5(connection, remote_path):
    """Full-file remote md5 (used to hash-verify the shipped image, which is
    small enough to afford it). None if unavailable."""
    if connection.method != 'ssh-key':
        return None
    return _remote_md5(connection.target, remote_path)


def measure_bandwidth(connection, mb=4):
    """Rough link speed in bytes/sec, measured once by streaming `mb` MB of
    zeros from the remote. Input for the finer cost gate; None if unavailable."""
    import time
    if connection.method != 'ssh-key':
        return None
    cmd = ['ssh', *_ssh_opts(), connection.target,
           f"dd if=/dev/zero bs=1M count={int(mb)} status=none"]
    t0 = time.monotonic()
    result = subprocess.run(cmd, capture_output=True)
    elapsed = time.monotonic() - t0
    if result.returncode != 0 or not result.stdout or elapsed <= 0:
        return None
    return len(result.stdout) / elapsed


def run_remote(connection, command, stdin_bytes=None, timeout=None):
    """Run one remote command in BatchMode; (rc, stdout, stderr) as text.
    `stdin_bytes` pipes a payload to the command — how plan.json reaches
    vislang_exec without landing a file first."""
    if connection.method != 'ssh-key':
        return 255, '', 'remote commands need ssh key auth'
    cmd = ['ssh', *_ssh_opts(), connection.target, command]
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
    if connection.method != 'ssh-key':
        return False
    parent = os.path.dirname(remote_path)
    if parent:
        rc, _, _ = run_remote(connection, f"mkdir -p {parent}")
        if rc != 0:
            return False
    tool = 'rsync' if _have_cmd('rsync') else 'scp'
    if tool == 'rsync':
        cmd = ['rsync', '-a', '--partial', '-e', _ssh_transport(),
               local_path, f"{connection.target}:{remote_path}"]
    else:
        cmd = ['scp', *_ssh_opts(),
               local_path, f"{connection.target}:{remote_path}"]
    return subprocess.run(cmd, capture_output=True).returncode == 0
