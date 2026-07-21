"""Inspect a data file and return its metadata as a DatasetInfo.

Two layers, mirroring the planner's site split (planner._plan_remote /
_plan_local):

  inspect_file(path)   — the LOCAL primitive. Dispatch lives in adapters.py: the
                         registry picks the adapter that recognizes the file.
                         Unknown formats raise (NeedsAdapterError, surfaced by the
                         MCP `inspect` tool). Always called with a path local to
                         the machine it runs on — the planner and the remote
                         reducer (vislang_exec) call it directly.

  inspect_source(uri)  — the SITE-AWARE entry for the authoring tools (inspect /
                         estimate_render_cost). A local path goes straight to
                         inspect_file; a remote URI (ssh:// or user@host:) is
                         inspected NEXT TO THE DATA — schema shipped back, no bulk
                         transfer — via `vislang_exec.py --inspect`, falling back
                         to a whole-file fetch when there is no ssh key auth.

inspect also resolves the spatial-coordinate variables (`info.positions`),
auto-detected from the variable names. Pass positions=('x','y','z') to override.
"""

import os
import re

from adapters import (get_adapter, detect_positions,  # noqa: F401
                      UnsupportedFormatError, NeedsAdapterError)

# scheme:// or user@host:  — matches planner._REMOTE.
_REMOTE = re.compile(r"^[a-z][a-z0-9+.-]*://|^[^/\s]+@[^/\s]+:")


def is_remote(uri):
    """True if uri names a remote source (ssh:// or user@host:)."""
    return bool(_REMOTE.match(uri))


def inspect_file(filepath, positions=None):
    """LOCAL primitive: read one on-disk file's metadata into a DatasetInfo.
    Stays transport-blind — remote resolution is inspect_source's job."""
    info = get_adapter(filepath).inspect(filepath)
    # Resolve which variables are spatial coordinates once, at the format
    # boundary, so render/etc. stay meaning-blind. None when there are no
    # explicit coordinate variables (e.g. a grid).
    info.positions = positions if positions is not None else detect_positions(info.variables)
    return info


def inspect_source(uri, positions=None):
    """Site-aware inspect for the authoring tools. Local path -> inspect_file;
    remote URI -> inspected next to the data (or fetched, if no key auth)."""
    if is_remote(uri):
        return _inspect_remote(uri, positions)
    return inspect_file(uri, positions=positions)


# ---------------------------------------------------------------------------
# Remote inspection (schema shipped back; no bulk data crosses the wire)
# ---------------------------------------------------------------------------
def _remote_conn(uri):
    """(connection, remote_path) for a remote uri, or (None, None) if the host
    can't be reached with ssh key auth (caller then falls back to a fetch)."""
    from my_download import establish_connection, _parse_remote
    from remote_reduce import _normalize_remote
    try:
        norm = _normalize_remote(uri)
        _, _, remote_path = _parse_remote(norm)
        conn = establish_connection(norm)
    except Exception:
        return None, None
    if conn.method != "ssh-key":
        return None, None
    return conn, remote_path


def _run_remote_inspect(conn, remote_path):
    """Ship `vislang_exec.py --inspect` to the remote (metadata only, generic
    HDF5 names) and return its parsed meta dict, or None on failure."""
    import shlex
    from my_download import run_remote
    from remote_reduce import _parse_meta
    py = os.environ.get("VISLANG_REMOTE_PYTHON", "python")
    repo = os.environ.get("VISLANG_REMOTE_REPO",
                          os.path.dirname(os.path.abspath(__file__)))
    # VISLANG_NO_BINDING=1: the remote returns the generic listing + the HDF5
    # schema tree; the binding decision is made LOCALLY, keyed by the structure-
    # only signature (filesystem-independent), so a locally-frozen binding serves
    # the remote file too.
    cmd = (f"VISLANG_NO_BINDING=1 {py} {repo}/vislang_exec.py --inspect "
           f"{shlex.quote(remote_path)}")
    _, out, _ = run_remote(conn, cmd)
    return _parse_meta(out)


def _inspect_remote(uri, positions=None):
    conn, remote_path = _remote_conn(uri)
    if conn is None:
        return _inspect_via_fetch(uri, positions)
    meta = _run_remote_inspect(conn, remote_path)
    if not meta or not meta.get("ok") or meta.get("needs_adapter"):
        # No schema (or an unrecognized format: the remote can't conform an
        # adapter without a model). Fetch the whole file so the LOCAL inspect can
        # bind/enrich or raise NeedsAdapterError against a real local copy.
        return _inspect_via_fetch(uri, positions)
    return _build_remote_info(uri, meta, positions)


def _inspect_via_fetch(uri, positions):
    """Fallback: pull the whole file (planner._fetch_remote) and inspect locally."""
    from planner import _fetch_remote
    return inspect_file(_fetch_remote(uri), positions=positions)


def _dims_from_json(dims):
    """JSON turns grid tuples into lists; restore tuples (region/subsample and
    the cost estimate expect a shape tuple for 'grid')."""
    return {k: (tuple(v) if isinstance(v, list) else v) for k, v in (dims or {}).items()}


def _build_remote_info(uri, meta, positions):
    """Assemble a DatasetInfo from a remote --inspect meta. For an HDF5 with a
    LOCALLY-cached binding (same schema signature) we enrich it here — zero data
    moved, since we already hold the schema tree; otherwise the generic listing
    is returned and the tree stashed for the binding offer."""
    from datasetInfo import DatasetInfo
    schema = meta.get("schema", {})
    info = DatasetInfo(uri, schema.get("filetype", "remote"),
                       list(schema.get("variables", [])),
                       dimensions=_dims_from_json(schema.get("dimensions", {})),
                       attributes=dict(schema.get("attributes", {}) or {}))

    tree = meta.get("schema_tree")
    if info.filetype == "HDF5" and tree:
        try:
            import schema_binding
            binding = schema_binding.load_cached_binding(schema_binding.schema_signature(tree))
            if binding is not None:
                info = schema_binding.build_info(uri, tree, binding)   # rich names
            else:
                info._remote_schema_tree = tree                        # for the offer
        except Exception:
            pass

    if positions is not None:
        info.positions = positions
    elif not getattr(info, "positions", None):
        info.positions = detect_positions(info.variables)
    return info


def remote_schema_tree(uri):
    """Ship extract_schema to the remote and return the HDF5 schema tree (dict),
    or None. Used by submit_binding for a remote source: bindings are keyed by a
    structure-only signature, so verifying+freezing against the shipped tree
    locally serves the remote file with no data movement."""
    conn, remote_path = _remote_conn(uri)
    if conn is None:
        return None
    meta = _run_remote_inspect(conn, remote_path)
    return meta.get("schema_tree") if meta else None


# ---------------------------------------------------------------------------
# Timestep folders — a folder is a TIMESERIES: one file per timestep, named
# `…#N` (N is the timestep number). No classification, no model: the `#N`
# convention is assumed. These helpers are the shared primitive for the planner
# loop and the authoring listing.
# ---------------------------------------------------------------------------
_TS_TOKEN = re.compile(r"#(\d+)")


def _parse_timesteps(names):
    """[(N, name)] for the names carrying a `#N` token, sorted by the integer N.
    Dotfiles and names without the token are skipped. The parsing shared by the
    local (timestep_files) and remote (remote_timestep_files) listers."""
    out = []
    for name in names:
        if name.startswith("."):
            continue
        m = _TS_TOKEN.search(name)
        if m:
            out.append((int(m.group(1)), name))
    out.sort(key=lambda t: t[0])
    return out


def _no_timesteps_error(where):
    return ValueError(
        f"{where!r} has no timestep files: a folder source is a timeseries "
        f"and its files must be named `…#N` (N = timestep), e.g. run#8.hdf5.")


def timestep_files(dirpath):
    """[(label, path)] for a LOCAL timestep folder, sorted by the integer `#N`
    label parsed from each filename. Files with no `#N` token (and non-files) are
    skipped. Raises ValueError if the folder has none."""
    names = [n for n in sorted(os.listdir(dirpath))
             if os.path.isfile(os.path.join(dirpath, n))]
    parsed = _parse_timesteps(names)
    if not parsed:
        raise _no_timesteps_error(dirpath)
    return [(label, os.path.join(dirpath, name)) for label, name in parsed]


def remote_is_dir(uri):
    """True if a remote uri points at a DIRECTORY (a timeseries folder), False if
    a regular file — or if the remote can't be reached with ssh key auth or the
    path can't be stat'd, in which case the caller treats it as a single file and
    the single-file path surfaces any real error. Metadata only (one `stat`)."""
    import shlex
    from my_download import run_remote
    conn, remote_path = _remote_conn(uri)
    if conn is None:
        return False
    rc, out, _ = run_remote(conn, f"stat -c %F {shlex.quote(remote_path)}")
    return rc == 0 and "directory" in out.lower()


def remote_timestep_files(uri):
    """[(label, remote_uri)] for a REMOTE timestep folder — the remote analog of
    timestep_files(). Lists the folder over ssh, parses each name's `#N` token,
    and rebuilds a full remote uri per timestep (the original uri + '/<name>').
    Returns None if the remote can't be reached with ssh key auth; raises
    ValueError if reachable but holding no `…#N` files. Metadata only — no file
    contents cross the wire."""
    import shlex
    from my_download import run_remote
    conn, remote_path = _remote_conn(uri)
    if conn is None:
        return None
    # -1p: one entry per line, a trailing '/' on directories, so we can skip
    # subdirectories the way timestep_files' os.path.isfile filter does.
    rc, out, _ = run_remote(conn, f"ls -1p {shlex.quote(remote_path)}")
    if rc != 0:
        return None
    names = [ln for ln in out.splitlines() if ln and not ln.endswith("/")]
    parsed = _parse_timesteps(names)
    if not parsed:
        raise _no_timesteps_error(uri)
    base = uri.rstrip("/")
    return [(label, f"{base}/{name}") for label, name in parsed]


def folder_listing(dirpath):
    """Plain, deterministic summary of a timestep folder for authoring: the
    timestep count/labels and the shared schema read from the first file. No
    judgment, no handshake — a folder is simply a timeseries."""
    try:
        ts = timestep_files(dirpath)
    except ValueError as e:
        return f"FOLDER {dirpath}\n  {e}"
    labels = [lab for lab, _ in ts]
    first = ts[0][1]
    contiguous = labels == list(range(labels[0], labels[-1] + 1))
    head = (
        f"FOLDER {dirpath} — timeseries of {len(ts)} timestep(s)\n"
        f"  timesteps: {labels[0]}..{labels[-1]}"
        + ("" if contiguous else f" (non-contiguous: {labels})") + "\n"
        f"  select a range with: timesteps(source({dirpath!r}), start, stop)  "
        f"# inclusive #N labels\n\n"
        f"  shared schema (from timestep {labels[0]}, {os.path.basename(first)}):"
    )
    try:
        body = "\n".join("    " + ln for ln in str(inspect_file(first)).splitlines())
    except NeedsAdapterError:
        body = "    (the first timestep needs an adapter — inspect it directly to write one)"
    except Exception as e:
        body = f"    (could not inspect the first timestep: {type(e).__name__}: {e})"
    return head + "\n" + body


def remote_folder_listing(uri):
    """Remote analog of folder_listing: the timestep count/labels + the shared
    schema read from the first timestep, both over ssh (metadata only — no bulk
    data crosses the wire). Backs the MCP `inspect` tool for a remote folder."""
    try:
        ts = remote_timestep_files(uri)
    except ValueError as e:
        return f"FOLDER {uri}\n  {e}"
    if ts is None:
        return (f"FOLDER {uri}\n  (cannot list the remote folder — remote commands "
                f"need ssh key auth; set up keys, or inspect a local copy)")
    labels = [lab for lab, _ in ts]
    first_uri = ts[0][1]
    contiguous = labels == list(range(labels[0], labels[-1] + 1))
    head = (
        f"FOLDER {uri} — remote timeseries of {len(ts)} timestep(s)\n"
        f"  timesteps: {labels[0]}..{labels[-1]}"
        + ("" if contiguous else f" (non-contiguous: {labels})") + "\n"
        f"  select a range with: timesteps(source({uri!r}), start, stop)  "
        f"# inclusive #N labels\n\n"
        f"  shared schema (from timestep {labels[0]}):"
    )
    try:
        body = "\n".join("    " + ln for ln in str(inspect_source(first_uri)).splitlines())
    except NeedsAdapterError:
        body = "    (the first timestep needs an adapter — inspect it directly to write one)"
    except Exception as e:
        body = f"    (could not inspect the first timestep: {type(e).__name__}: {e})"
    return head + "\n" + body
