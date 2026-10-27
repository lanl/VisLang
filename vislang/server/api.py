"""The machine interface for front ends: JSON in, JSON out.

The MCP tools and `sieve` print prose for people and models. A graphical
client (the ParaView plugin) needs the same operations as data: what a source
is, what a request would cost, why it was held, which files it wrote. Every
operation here is a thin layer over the code those tools already use — the
same `run_spec`, the same gates, the same records — so a request made from a
GUI is checked, priced, cached and recorded exactly as a spec run by hand.

    inspect(uri)        a descriptor of the source (metadata only, never copies)
    run(request)        compose or take a spec, write it, run or price it
    session(uri)        is there a usable ssh session (no prompt)
    connect(uri)        open one (the password dialog appears on the user's screen)
    paths()             where Sieve keeps its state
    doctor()            what a client needs to know works

`sieve api <op> --request F --result F [--events F]` is the process boundary:
the result is written atomically, and the exit code is 0 whenever a result was
written — the outcome (ok / held / error) is in the JSON.
"""

import dataclasses
import json
import math
import os
import re
import sys

API = 1

# What a first look at a source should cost the viewer: the overview's
# suggested stride keeps one field under this many bytes, or the point cloud
# under this many points.
_OVERVIEW_BYTES = 32 * 1024 ** 2
_OVERVIEW_POINTS = 2_000_000


# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------
def inspect(uri, positions=None):
    """A descriptor of `uri` — a file, or a folder of `…#N` timesteps — from
    metadata only. Never copies data: a schema only a copy could read comes
    back as status "schema_unavailable" with the size the copy would move."""
    from vislang.formats.adapters import NeedsAdapterError
    from vislang.formats.inspect import (inspect_source, is_remote, remote_is_dir,
                                         remote_timestep_files_stat, timestep_files,
                                         SchemaUnavailable)
    from vislang.remote.download import clear_remote_caches
    clear_remote_caches()
    out = {"api": API, "uri": uri}
    pos = tuple(positions) if positions else None
    if is_remote(uri):
        held = _session_hold(uri)
        if held:
            return {**out, **held}
    series = None
    try:
        if is_remote(uri):
            if remote_is_dir(uri):
                files = remote_timestep_files_stat(uri)
                if files is None:
                    return {**out, "status": "needs_session",
                            "message": "cannot list the remote folder without a "
                                       "live session"}
                series = [(lab, u, size) for lab, u, size, _ in files]
        elif os.path.isdir(uri):
            series = [(lab, p, os.path.getsize(p)) for lab, p in timestep_files(uri)]
        target = series[0][1] if series else uri
        info = inspect_source(target, positions=pos, allow_fetch=False)
    except SchemaUnavailable as e:
        return {**out, "status": "schema_unavailable", "message": str(e),
                "source_bytes": e.size}
    except NeedsAdapterError as e:
        return {**out, "status": "needs_adapter",
                "message": f"no reader recognizes {uri}; write an adapter "
                           f"(inspect it in Claude Code or with `sieve inspect`)"}
    except Exception as e:
        return {**out, "status": "error", "message": f"{type(e).__name__}: {e}"}
    desc = {**out, "status": "ok", **describe(info)}
    if series:
        desc["timesteps"] = [lab for lab, _, _ in series]
        desc["timestep_bytes"] = series[0][2]
        desc["source_bytes"] = sum(size for _, _, size in series)
    else:
        desc["timesteps"] = None
        desc["source_bytes"] = _file_size(uri, info)
    return desc


def describe(info):
    """What a client needs to know about one inspected file (a DatasetInfo)."""
    from vislang.formats.dataset_info import geometry_descriptor, in_cells
    dims = info.dimensions or {}
    points = bool(getattr(info, "positions", None)) or "particles" in dims
    grid = list(dims["grid"]) if not points and dims.get("grid") else None
    geometry = getattr(info, "geometry", None)
    gdesc = geometry_descriptor(geometry)
    if points:
        coords, bounds, ext = "world", None, ".vtp"
    else:
        physical = bool(geometry) and not in_cells(geometry)
        coords = "world" if physical else "cells"
        ext = ".vts" if (geometry or {}).get("kind") == "structured" else ".vti"
        bounds = _bounds(grid, geometry)
    itemsizes = {k: int(v) for k, v in (info.itemsizes or {}).items()}
    d = {"filetype": info.filetype,
         "modality": "particles" if points else "grid",
         "grid": grid,
         "particles": int(dims["particles"]) if points and "particles" in dims else None,
         "positions": list(info.positions) if getattr(info, "positions", None) else None,
         "variables": list(info.variables),
         "itemsizes": itemsizes,
         "geometry": gdesc,
         "coords": coords,
         "bounds": bounds,
         "out_ext": ext,
         "region_text": info._region_coords_text() if hasattr(info, "_region_coords_text")
                        else None}
    d["suggested_stride"] = _suggest_stride(d)
    return d


def _bounds(grid, geometry):
    """Per-axis [first, last] sample position in the coordinates region() takes
    on this grid, or None when only the points could tell (curvilinear)."""
    if not grid:
        return None
    if not geometry:
        return [[0, n - 1] for n in grid]
    if geometry.get("kind") != "uniform":
        return None
    out = []
    for a, n in enumerate(grid):
        o, s = geometry["origin"][a], geometry["spacing"][a]
        first, last = o, o + (n - 1) * s
        out.append([min(first, last), max(first, last)])
    return out


def _suggest_stride(d):
    """The smallest stride that keeps a first look cheap: one field under
    _OVERVIEW_BYTES on a grid, under _OVERVIEW_POINTS points for particles."""
    if d["modality"] == "particles":
        n = d.get("particles") or 0
        return max(1, math.ceil(n / _OVERVIEW_POINTS)) if n else 1
    grid = d.get("grid")
    if not grid:
        return 1
    item = max(d["itemsizes"].values()) if d["itemsizes"] else 4
    s = 1
    while s < max(grid):
        cells = 1
        for n in grid:
            cells *= -(-n // s)
        if cells * item <= _OVERVIEW_BYTES:
            break
        s += 1
    return s


def _file_size(uri, info):
    from vislang.formats.inspect import is_remote
    if is_remote(uri):
        return None
    try:
        return os.path.getsize(uri)
    except OSError:
        return None


def _session_hold(uri):
    from vislang.remote.reduce import session_check
    hold = session_check(uri)
    if hold is None:
        return None
    return {"status": "needs_session", "message": hold.reason,
            "connect_cmd": hold.connect_cmd, "reachable": hold.reachable}


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------
_STATUS = {"OK": "ok", "NEEDS SESSION": "held", "NEEDS ALLOCATION": "held",
           "NEEDS CONFIRM": "held", "FAILED": "error", "BUILD FAILED": "error",
           "ERROR": "error"}


def run(request):
    """Run (or price) one spec for a client.

    request:
        mode        "execute" (default) or "estimate" (check + price only)
        workdir     absolute folder for this request: spec.py and outputs land here
        spec_text   the spec to run, OR
        descriptor + params   a spec to compose (dsl/compose.compose_spec);
                    params.output defaults to <workdir>/out<descriptor.out_ext>
        approval    {"budget", "without_allocation", "fetch"} (approval.py)
        client      {"name", "session", "step"} — recorded on the timing record
        budget_bytes, budget_seconds, net_bps   per-request overrides
    Returns {"api", "status": "ok" | "held" | "error", "holds", "spec_text",
    "spec_path", "estimate", "output", "files", "run_id", "wire_bytes",
    "probe_bytes", "reuse", "route", "total_s", "steps", "error"}."""
    from vislang.dsl.compose import compose_spec
    from vislang.server.cli_core import run_spec
    workdir = request["workdir"]
    os.makedirs(workdir, exist_ok=True)
    text = request.get("spec_text")
    if text is None:
        params = dict(request.get("params") or {})
        desc = request["descriptor"]
        params.setdefault("output", os.path.join(workdir, "out" + desc["out_ext"]))
        try:
            text = compose_spec(desc, params)
        except (KeyError, ValueError, TypeError) as e:
            return {"api": API, "status": "error", "holds": {},
                    "error": f"{type(e).__name__}: {e}", "spec_text": None}
    spec_path = os.path.join(workdir, "spec.py")
    with open(spec_path, "w") as f:
        f.write(text)
    approval = request.get("approval") or {}
    overrides = {"VISLANG_BUDGET_BYTES": request.get("budget_bytes"),
                 "VISLANG_BUDGET_SECONDS": request.get("budget_seconds"),
                 "VISLANG_NET_BPS": request.get("net_bps")}
    with _env(overrides):
        res = run_spec(spec_path, confirm=bool(approval.get("budget")),
                       force_dry=request.get("mode") == "estimate",
                       without_allocation=bool(approval.get("without_allocation")),
                       allow_fetch=bool(approval.get("fetch")),
                       client=request.get("client"))
    return _summarize(res, text, spec_path)


def _summarize(res, text, spec_path):
    pipes = res.get("pipelines") or []
    results = [p["result"] for p in pipes if p.get("result")]
    holds = {}
    for r in results:
        est = _asdict(r.get("estimate"))
        if r.get("needs_session"):
            holds["session"] = {"connect_cmd": r.get("connect_cmd"),
                                "reason": r.get("session_reason")}
        elif r.get("needs_allocation"):
            holds["allocation"] = {"salloc_cmd": r.get("salloc_cmd")}
        elif r.get("needs_fetch_approval"):
            holds["fetch"] = {"estimate": est}
        elif r.get("needs_confirm"):
            holds["budget"] = {"estimate": est}
    first = results[0] if results else {}
    rec = res.get("timing") or {}
    counters = rec.get("counters") or {}
    pipe_rec = (rec.get("pipelines") or [{}])[0]
    errors = [p["error"] for p in pipes if p.get("error")] + \
        ([res["error"]] if res.get("error") else [])
    # A spec may save to a relative path; it resolved against this process's
    # working folder, and a client needs it absolute.
    output = os.path.abspath(first["output"]) if first.get("output") else None
    return {"api": API,
            "status": _STATUS.get(res["status"], "error"),
            "report_status": res["status"],
            "holds": holds,
            "spec_text": text,
            "spec_path": spec_path,
            "estimate": _asdict(first.get("estimate")),
            "output": output,
            "files": series_files(output, first.get("timesteps"), first.get("tags")),
            "run_id": res.get("run_id"),
            "wire_bytes": counters.get("wire_bytes"),
            "probe_bytes": counters.get("probe_bytes"),
            "reuse": {k: pipe_rec.get(k) for k in
                      ("reused_pairs", "total_pairs", "cached_vars", "want_vars",
                       "cached_bytes") if pipe_rec.get(k) is not None},
            "route": pipe_rec.get("route"),
            "total_s": rec.get("total_s"),
            "steps": [str(s) for r in results for s in r.get("steps", [])],
            "error": errors[0] if errors else None,
            "report": res.get("report")}


_STEP_FILE = re.compile(r"^timestep#(\d+)\.[^.]+$")


def series_files(output, timesteps=None, tags=None):
    """The data files a run wrote: [{"label", "tag", "path"}]. A single file is
    one entry (label None); a series folder lists its `timestep#N.<ext>` files;
    named boxes add a tag per sub-folder (or per file, on one file)."""
    if not output:
        return []
    if os.path.isfile(output):
        return [{"label": None, "tag": None, "path": output}]
    if not os.path.isdir(output):
        return []
    out = []
    if isinstance(tags, (list, tuple)):
        # Named boxes on one file: <dir>/<tag>.<ext>
        for name in sorted(os.listdir(output)):
            stem, ext = os.path.splitext(name)
            if stem in tags and ext != ".pvd" and not name.startswith("."):
                out.append({"label": None, "tag": stem,
                            "path": os.path.join(output, name)})
        return out
    # A series: <dir>/timestep#N.<ext>, or one series per tag <dir>/<tag>/…
    dirs = [(t, os.path.join(output, t)) for t in tags] if tags else [(None, output)]
    for tag, d in dirs:
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            m = _STEP_FILE.match(name)
            if m:
                out.append({"label": int(m.group(1)), "tag": tag,
                            "path": os.path.join(d, name)})
    out.sort(key=lambda f: (f["tag"] or "", -1 if f["label"] is None else f["label"]))
    return out


def _asdict(est):
    if est is None:
        return None
    try:
        return dataclasses.asdict(est)
    except TypeError:
        return None


class _env:
    """Set the non-None overrides in os.environ for a block, then restore."""
    def __init__(self, kv):
        self.kv = {k: str(v) for k, v in kv.items() if v is not None}
        self.old = {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.old[k] = os.environ.get(k)
            os.environ[k] = v

    def __exit__(self, *exc):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ---------------------------------------------------------------------------
# sessions, paths, doctor
# ---------------------------------------------------------------------------
def session(uri):
    """Is there a usable ssh session for `uri`'s host? Never prompts."""
    from vislang.formats.inspect import is_remote
    if not is_remote(uri):
        return {"api": API, "status": "ok", "remote": False}
    held = _session_hold(uri)
    return {"api": API, "remote": True, **(held or {"status": "ok"})}


def connect(uri, timeout=120):
    """Open a session (a password dialog appears on the user's screen; the
    secret goes straight to ssh). Blocks until it is open or refused."""
    from vislang.server.cli_core import do_connect
    text = do_connect(uri, timeout=timeout)
    bad = text.startswith(("UNREACHABLE", "NOT CONNECTED", "ERROR"))
    return {"api": API, "status": "error" if bad else "ok", "message": text}


def paths():
    from vislang.runtime.paths import cache_root, home
    from vislang.runtime.timing import timings_file
    return {"api": API, "status": "ok", "home": home(), "cache": cache_root(),
            "timings": timings_file(),
            "paraview_sessions": os.path.join(home(), "paraview")}


def doctor():
    """What a client should check before relying on Sieve: the interpreter,
    the libraries the readers need, the ssh tools, and the settings in force."""
    import shutil
    checks = {"python": sys.version.split()[0], "executable": sys.executable}
    for mod in ("numpy", "h5py", "pyvista", "vtk"):
        try:
            __import__(mod)
            checks[mod] = "ok"
        except Exception as e:
            checks[mod] = f"missing: {type(e).__name__}"
    for tool in ("ssh", "rsync"):
        checks[tool] = shutil.which(tool) or "missing"
    settings = {k: v for k, v in os.environ.items() if k.startswith("VISLANG_")}
    ok = all(v == "ok" for k, v in checks.items() if k in ("numpy", "h5py", "pyvista"))
    return {"api": API, "status": "ok" if ok else "error", "checks": checks,
            "settings": settings, **paths_quiet()}


def paths_quiet():
    try:
        p = paths()
        return {k: p[k] for k in ("home", "cache", "timings", "paraview_sessions")}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# the process boundary
# ---------------------------------------------------------------------------
OPS = ("inspect", "run", "session", "connect", "paths", "doctor")


def dispatch(op, request):
    if op == "inspect":
        return inspect(request["uri"], request.get("positions"))
    if op == "run":
        return run(request)
    if op == "session":
        return session(request["uri"])
    if op == "connect":
        return connect(request["uri"], timeout=int(request.get("timeout", 120)))
    if op == "paths":
        return paths()
    if op == "doctor":
        return doctor()
    raise ValueError(f"unknown op {op!r}")


def main(op, request_path=None, result_path=None, events_path=None):
    """`sieve api`: read the request, run the op, write the result atomically.
    Library chatter goes to stderr, so stdout carries only the result when no
    result file is named. Returns the exit code."""
    if events_path:
        os.environ["VISLANG_EVENTS_FILE"] = events_path
    try:
        if request_path in (None, "-"):
            raw = sys.stdin.read() if request_path == "-" else "{}"
        else:
            with open(request_path) as f:
                raw = f.read()
        request = json.loads(raw or "{}")
    except (OSError, ValueError) as e:
        print(f"sieve api: cannot read the request: {e}", file=sys.stderr)
        return 2
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        result = dispatch(op, request)
    except Exception as e:
        result = {"api": API, "status": "error", "error": f"{type(e).__name__}: {e}"}
    finally:
        sys.stdout = real_stdout
    text = json.dumps(result, default=str)
    if result_path:
        tmp = f"{result_path}.{os.getpid()}.tmp"
        with open(tmp, "w") as f:
            f.write(text)
        os.replace(tmp, result_path)
    else:
        print(text)
    return 0
