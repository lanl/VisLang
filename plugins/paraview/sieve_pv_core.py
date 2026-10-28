"""The ParaView plugin's engine — no ParaView in here, so it is testable in
plain Python.

The plugin is a thin client. Every request it makes is one `sieve api`
subprocess (vislang/server/api.py) that writes a spec, runs it through Sieve's
own gates, cache and records, and leaves a result file. Nothing here decides
what a request means; it only runs requests, remembers their results, and
keeps the session's trace.

    Runner    one request at a time, in the order asked; each pipeline object
              has at most one waiting request (a newer one replaces it). Jobs
              are polled — no threads run inside ParaView.
    Job       one `sieve api <op>` process: request, events and result files
              in its own step folder, output in log.txt.
    Session   <sieve home>/paraview/<id>/: session.json (what was in force),
              session.jsonl (one line per finished request: the trace the
              evaluation replays), NNN-<name>/ step folders.
    Memo      (request key) -> finished result, so a revisited view (a
              timestep, an earlier box) is shown again without a process.

The environment a request runs in is scrubbed of the host application's
Python settings (PYTHONHOME, PYTHONPATH, DYLD_*…), which would otherwise point
Sieve's interpreter at ParaView's own libraries.
"""

import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))

# Variables a host application sets for ITS Python that would break Sieve's.
_SCRUB_PREFIXES = ("PYTHON", "DYLD_", "__PYVENV_LAUNCHER__")
_SCRUB = ("LD_LIBRARY_PATH", "LD_PRELOAD", "VIRTUAL_ENV", "CONDA_PREFIX")


def find_sieve():
    """The `sieve` launcher: $SIEVE_CMD, else the repo this plugin lives in."""
    cmd = os.environ.get("SIEVE_CMD")
    if cmd:
        return cmd
    return os.path.normpath(os.path.join(HERE, "..", "..", "sieve"))


def clean_env(extra=None):
    """os.environ without the host's Python settings, plus `extra`. A GUI app
    started from the Dock has a minimal PATH; the system tools ssh/rsync live
    in /usr/bin, so that is put back."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(_SCRUB_PREFIXES) and k not in _SCRUB}
    path = env.get("PATH", "")
    for p in ("/usr/bin", "/bin", "/usr/sbin", "/sbin"):
        if p not in path.split(":"):
            path = f"{path}:{p}" if path else p
    env["PATH"] = path
    for k, v in (extra or {}).items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = str(v)
    return env


# scheme:// or user@host:  — the same test as vislang.formats.inspect.is_remote.
_REMOTE = re.compile(r"^[a-z][a-z0-9+.-]*://|^[^/\s]+@[^/\s]+:")


def is_remote(uri):
    return bool(_REMOTE.match(uri or ""))


def request_key(kind, uri, params, extra=None):
    """A stable key for a request's meaning: what was asked, not where it was
    written. Two requests with the same key produce the same data."""
    p = {k: v for k, v in (params or {}).items() if k != "output"}
    return json.dumps({"kind": kind, "uri": uri, "params": p, **(extra or {})},
                      sort_keys=True, default=str)


# ---------------------------------------------------------------------------
class Job:
    """One `sieve api <op>` process and its files."""

    def __init__(self, op, request, workdir, env=None, label=""):
        self.op, self.request, self.workdir = op, dict(request), workdir
        self.env, self.label = env or {}, label
        self.state = "queued"           # queued | running | done | cancelled | superseded
        self.result = None
        self.proc = None
        self.t_queued, self.t_started, self.t_done = time.time(), None, None
        self.request_path = os.path.join(workdir, "request.json")
        self.result_path = os.path.join(workdir, "result.json")
        self.events_path = os.path.join(workdir, "events.jsonl")
        self.log_path = os.path.join(workdir, "log.txt")

    def start(self, sieve_cmd, base_env):
        os.makedirs(self.workdir, exist_ok=True)
        with open(self.request_path, "w") as f:
            json.dump(self.request, f, indent=1, default=str)
        self.log = open(self.log_path, "w")
        self.proc = subprocess.Popen(
            [sieve_cmd, "api", self.op, "--request", self.request_path,
             "--result", self.result_path, "--events", self.events_path],
            cwd=self.workdir, stdout=self.log, stderr=subprocess.STDOUT,
            env=clean_env({**base_env, **self.env}), start_new_session=True)
        self.state, self.t_started = "running", time.time()

    def poll(self):
        """True once the process has ended (result read, or an error made)."""
        if self.state != "running":
            return self.state in ("done", "cancelled")
        if self.proc.poll() is None:
            return False
        self.log.close()
        self.t_done = time.time()
        try:
            with open(self.result_path) as f:
                self.result = json.load(f)
        except (OSError, ValueError):
            tail = ""
            try:
                with open(self.log_path) as f:
                    tail = f.read()[-800:]
            except OSError:
                pass
            self.result = {"status": "error",
                           "error": f"sieve exited {self.proc.returncode} without a "
                                    f"result. {tail}".strip()}
        self.state = "done"
        return True

    def cancel(self):
        if self.state == "running" and self.proc and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except OSError:
                pass
            self.proc.wait()
            self.log.close()
        self.state = "cancelled"
        self.t_done = time.time()

    def progress(self):
        """The latest progress line from the events file, or ''."""
        last = ""
        try:
            with open(self.events_path) as f:
                for ln in f:
                    try:
                        e = json.loads(ln)
                    except ValueError:
                        continue
                    if e.get("kind") == "phase" and e.get("state") == "start":
                        last = e["name"].split("/")[-1].replace("_", " ")
                    elif e.get("kind") == "estimate":
                        last = "priced"
        except OSError:
            pass
        return last

    def timings(self):
        return {"queue_s": _dt(self.t_queued, self.t_started),
                "process_s": _dt(self.t_started, self.t_done)}


def _dt(a, b):
    return round(b - a, 3) if a is not None and b is not None else None


# ---------------------------------------------------------------------------
class Runner:
    """Runs jobs one at a time, oldest request first. Each owner (a pipeline
    object) has at most one waiting job: a newer request replaces the waiting
    one, which is marked "superseded". A running job is never killed by a newer
    request — it finishes and warms the cache — only by cancel()."""

    def __init__(self, sieve_cmd=None, base_env=None):
        self.sieve_cmd = sieve_cmd or find_sieve()
        self.base_env = base_env or {}
        self.running = None              # (owner, job)
        self.waiting = []                # [(owner, job)], oldest first

    def submit(self, owner, job):
        for i, (o, j) in enumerate(self.waiting):
            if o == owner:
                j.state = "superseded"
                del self.waiting[i]
                break
        self.waiting.append((owner, job))
        self.tick()
        return job

    def tick(self):
        """Advance: reap the running job, start the next. Returns the job that
        finished this tick, if any."""
        finished = None
        if self.running is not None:
            owner, job = self.running
            if job.poll():
                finished = job
                self.running = None
        if self.running is None and self.waiting:
            owner, job = self.waiting.pop(0)
            try:
                job.start(self.sieve_cmd, self.base_env)
                self.running = (owner, job)
            except OSError as e:
                job.state, job.t_done = "done", time.time()
                job.result = {"status": "error",
                              "error": f"could not start {self.sieve_cmd}: {e}"}
        return finished

    def cancel(self, owner):
        """Cancel the owner's waiting and running jobs."""
        keep = []
        for o, j in self.waiting:
            if o == owner:
                j.state = "cancelled"
            else:
                keep.append((o, j))
        self.waiting = keep
        if self.running is not None and self.running[0] == owner:
            self.running[1].cancel()
            self.running = None
        self.tick()

    def wait(self, job, timeout=None, interval=0.05):
        """Block until `job` ends (synchronous mode: scripts and tests)."""
        t0 = time.time()
        while job.state in ("queued", "running"):
            self.tick()
            if job.state in ("queued", "running"):
                if timeout is not None and time.time() - t0 > timeout:
                    raise TimeoutError(f"sieve request timed out after {timeout} s")
                time.sleep(interval)
        return job

    def shutdown(self):
        for o, j in list(self.waiting):
            j.state = "cancelled"
        self.waiting = []
        if self.running is not None:
            self.running[1].cancel()
            self.running = None


# ---------------------------------------------------------------------------
class Session:
    """A ParaView session's folder and trace."""

    def __init__(self, root=None, home=None, info=None):
        sid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        base = root or os.path.join(home or _default_home(), "paraview")
        self.id = sid
        self.root = os.path.join(base, sid)
        os.makedirs(self.root, exist_ok=True)
        self.step = 0
        self.trace_path = os.path.join(self.root, "session.jsonl")
        meta = {"session": sid, "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "sieve_cmd": find_sieve(), "plugin_python": sys.version.split()[0],
                "settings": {k: v for k, v in os.environ.items()
                             if k.startswith("VISLANG_")},
                **(info or {})}
        with open(os.path.join(self.root, "session.json"), "w") as f:
            json.dump(meta, f, indent=1)

    def next_dir(self, name):
        self.step += 1
        safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in name)[:40]
        return self.step, os.path.join(self.root, f"{self.step:03d}-{safe}")

    def trace(self, record):
        """Append one finished request to the session trace. Never raises."""
        try:
            with open(self.trace_path, "a") as f:
                f.write(json.dumps({"t": round(time.time(), 3), "session": self.id,
                                    **record}, default=str) + "\n")
        except Exception:
            pass


def _default_home():
    """Sieve's home, by the rule vislang/runtime/paths.py uses: $VISLANG_HOME,
    else <repo>/.vislang (this plugin lives in the repo)."""
    return os.environ.get("VISLANG_HOME") or os.path.normpath(
        os.path.join(HERE, "..", "..", ".vislang"))


def trace_record(owner_name, action, job, key=None, memo_hit=False):
    """One session.jsonl line for a finished (or memo-served) request."""
    r = (job.result if job is not None else None) or {}
    est = r.get("estimate") or {}
    rec = {"source": owner_name, "action": action, "memo_hit": memo_hit,
           "status": r.get("status"), "holds": sorted((r.get("holds") or {}).keys()),
           "spec_path": r.get("spec_path"), "run_id": r.get("run_id"),
           "route": r.get("route"), "wire_bytes": r.get("wire_bytes"),
           "probe_bytes": r.get("probe_bytes"), "reuse": r.get("reuse"),
           "est_read_mb": est.get("read_mb"), "est_time_lo_s": est.get("time_lo_s"),
           "est_time_hi_s": est.get("time_hi_s"), "sieve_s": r.get("total_s"),
           "files": [f["path"] for f in r.get("files") or []],
           "error": r.get("error"), "key": key}
    if job is not None:
        rec.update(job.timings())
        rec["state"] = job.state
    return rec


# ---------------------------------------------------------------------------
class Memo:
    """Finished results by request key (this ParaView session only)."""

    def __init__(self):
        self._d = {}

    def get(self, key):
        return self._d.get(key)

    def put(self, key, result):
        if result and result.get("status") == "ok" and result.get("files"):
            self._d[key] = result

    def __len__(self):
        return len(self._d)


# ---------------------------------------------------------------------------
# Status text: what the panel shows. Pure, so it is tested without ParaView.
# ---------------------------------------------------------------------------
def _mb(v):
    if v is None:
        return "?"
    return f"{v:,.1f} MB" if v < 1024 else f"{v / 1024:,.2f} GB"


def _secs(lo, hi):
    if lo is None:
        return ""
    def f(s):
        return f"{s:.0f} s" if s < 90 else f"{s / 60:.1f} min"
    return f", ~{f(lo)}–{f(hi)}"


def status_text(job, result=None, showing=None):
    """The panel's status for a job (running, finished, held, failed)."""
    if job is None and result is None:
        return "Ready. Set a source and press Apply."
    if job is not None and job.state == "queued":
        return "Waiting for an earlier request to finish…"
    if job is not None and job.state == "running":
        step = job.progress()
        return ("Fetching" + (f" — {step}" if step else "") + "…"
                + (f"\n(showing {showing} until it arrives)" if showing else ""))
    if job is not None and job.state == "cancelled":
        return "Cancelled."
    r = result or (job.result if job is not None else None) or {}
    st = r.get("status")
    est = r.get("estimate") or {}
    if st == "ok":
        size = est.get("output_mb")
        if size is None:                 # a cache hit is not priced: weigh the files
            try:
                size = sum(os.path.getsize(f["path"]) for f in r.get("files") or []) \
                    / 1024 ** 2 or None
            except (OSError, KeyError, TypeError):
                size = None
        lines = [f"Fetched: result {_mb(size)}"
                 + (f" in {r['total_s']:.1f} s" if r.get("total_s") else "")]
        if r.get("route") == "catalog_full_hit":
            lines.append("Served from the cache: nothing crossed the network.")
        if r.get("wire_bytes"):
            lines.append(f"Over the wire: {_mb(r['wire_bytes'] / 1024 ** 2)}"
                         + (f" (predicted {_mb(est.get('read_mb'))}"
                            f"{_secs(est.get('time_lo_s'), est.get('time_hi_s'))})"
                            if est.get("site") == "remote" else ""))
        reuse = r.get("reuse") or {}
        if reuse.get("total_pairs"):
            lines.append(f"Cache: reused {reuse['reused_pairs']}/{reuse['total_pairs']} "
                         f"pieces")
        elif reuse.get("want_vars"):
            lines.append(f"Cache: reused {reuse.get('cached_vars', 0)}/"
                         f"{reuse['want_vars']} fields")
        return "\n".join(lines)
    holds = r.get("holds") or {}
    if "session" in holds:
        return ("Not connected to the remote host. Press Connect (a password "
                "dialog opens), then Apply.\n" + (holds["session"].get("reason") or ""))
    if "allocation" in holds:
        return ("No Slurm allocation is held on the cluster, so nothing was sent.\n"
                "Ask for one in a terminal (it uses shared HPC time):\n  "
                + holds["allocation"].get("salloc_cmd", "")
                + "\nthen press Apply again.")
    if "fetch" in holds:
        e = holds["fetch"].get("estimate") or {}
        return ("The reduce cannot run next to the data, and copying the whole "
                f"files instead is over budget ({_mb(e.get('read_mb'))}"
                f"{_secs(e.get('time_lo_s'), e.get('time_hi_s'))}).\n"
                "Press 'Fetch anyway' to approve that copy, or narrow the request.")
    if "budget" in holds:
        e = holds["budget"].get("estimate") or {}
        why = {"size": "size", "time": "time", "size+time": "size and time"}.get(
            e.get("budget_reason"), "budget")
        return (f"Over the {why} budget: {_mb(e.get('read_mb'))}"
                f"{_secs(e.get('time_lo_s'), e.get('time_hi_s'))}. Nothing was fetched.\n"
                "Press 'Fetch anyway' to approve it, or narrow the request "
                "(larger stride, smaller box, fewer fields).")
    if st == "error":
        return "Failed: " + (r.get("error") or "unknown error")
    return f"Status: {st}"


# ---------------------------------------------------------------------------
# What the source is, as text and tables: the panel's Series line and the
# Summary / Fields / Timesteps tables. Pure, so tested without ParaView.
# ---------------------------------------------------------------------------
def _series_module():
    """vislang/formats/series.py, loaded on its own (standard library only), so
    the panel describes a series exactly as `inspect` does."""
    if "_series" not in globals():
        import importlib.util
        path = os.path.join(HERE, "..", "..", "vislang", "formats", "series.py")
        mod = None
        try:
            spec = importlib.util.spec_from_file_location("sieve_series", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except (OSError, ImportError, AttributeError):
            mod = None
        globals()["_series"] = mod
    return globals()["_series"]


def describe_series(labels):
    mod = _series_module()
    if mod is not None:
        return mod.describe_series(labels)
    labels = sorted(labels)
    return f"{len(labels)} timesteps: #{labels[0]} … #{labels[-1]}" if labels else \
        "no timesteps"


def pick_timesteps(text, available):
    """The labels of `available` that a Timesteps field selects, and what is
    wrong with it (None when nothing). Parts, separated by commas:
        4          that timestep (it must exist)
        1-9        every timestep whose label is in [1, 9]
        1-9/4      every 4th timestep of those (the 1st, 5th, 9th, …)
        /4         every 4th timestep of the whole series
    Empty text selects everything."""
    text = (text or "").strip()
    available = sorted(available)
    if not text:
        return list(available), None
    keep, missing = set(), []
    for part in (p.strip() for p in text.split(",")):
        if not part:
            continue
        span, slash, every = part.partition("/")
        try:
            k = int(every) if slash else 1
        except ValueError:
            return [], f"cannot read {part!r} (write 1-9, 1-9/4 or 1,4,9)"
        if k < 1:
            return [], f"{part!r}: every n-th needs n of 1 or more"
        span = span.strip()
        if not span:
            got = list(available)
            dash = True
        else:
            lo, dash, hi = span.partition("-")
            try:
                lo = int(lo)
                hi = int(hi) if dash else lo
            except ValueError:
                return [], f"cannot read {part!r} (write 1-9, 1-9/4 or 1,4,9)"
            if lo > hi:
                return [], f"{part!r} runs backwards"
            got = [t for t in available if lo <= t <= hi]
            if not dash and not got:
                missing.append(lo)
        keep.update(got[::k])
    if missing:
        return [], ("not in this series: " + ", ".join(f"#{m}" for m in missing)
                    + f" (it has #{available[0]} … #{available[-1]})")
    if not keep:
        return [], (f"{text!r} matches no timestep (this series has "
                    f"#{available[0]} … #{available[-1]})")
    return sorted(keep), None


def series_text(desc, ts_text):
    """The panel's Series line: what the series has, and what the Timesteps
    field selects from it."""
    if not desc:
        return "Apply to read the series."
    ts = desc.get("timesteps")
    if not ts:
        return "One file (no timesteps)."
    have = desc.get("series_text") or describe_series(ts)
    picked, problem = pick_timesteps(ts_text, ts)
    if problem:
        return f"{have}\nTimesteps: {problem}"
    if len(picked) == len(ts):
        return f"{have}\nselected: all"
    return f"{have}\nselected: " + describe_series(picked)


def _n_elements(desc, stride):
    s = max(1, int(stride or 1))
    if desc.get("grid"):
        n = 1
        for d in desc["grid"]:
            n *= -(-int(d) // s)
        full = 1
        for d in desc["grid"]:
            full *= int(d)
        return full, n
    if desc.get("particles"):
        full = int(desc["particles"])
        return full, -(-full // s)
    return None, None


def _kind_text(desc):
    if desc.get("modality") == "particles":
        return "points"
    g = (desc.get("geometry") or {}).get("kind")
    if g == "structured":
        return "grid, curvilinear (every point's position is listed)"
    if desc.get("coords") == "world":
        return "grid, uniform (origin and spacing)"
    return "grid with no stated position (placed in its own cells)"


def summary_table(desc, ts_text, stride):
    """[(Item, Value)] — what the source is."""
    if not desc:
        return [("Status", "Apply to read the source's metadata.")]
    rows = [("Source", desc.get("uri", "")),
            ("Format", str(desc.get("filetype", ""))),
            ("Kind", _kind_text(desc))]
    if desc.get("grid"):
        rows.append(("Grid", " × ".join(str(int(d)) for d in desc["grid"])))
    if desc.get("particles"):
        rows.append(("Points", f"{int(desc['particles']):,}"))
    fields = [v for v in desc.get("variables") or [] if v not in (desc.get("positions")
                                                                   or [])]
    rows.append(("Fields", f"{len(fields)}: " + ", ".join(fields)))
    if desc.get("positions"):
        rows.append(("Positions", ", ".join(desc["positions"])))
    rows.append(("Region coordinates", str(desc.get("region_text") or desc.get("coords"))))
    ts = desc.get("timesteps")
    if ts:
        rows.append(("Timesteps", series_text(desc, ts_text).replace("\n", "; ")))
        if desc.get("timestep_bytes"):
            rows.append(("Size per timestep", _mb(desc["timestep_bytes"] / 1024 ** 2)))
    if desc.get("source_bytes"):
        rows.append(("Size on disk" + (" (all timesteps)" if ts else ""),
                     _mb(desc["source_bytes"] / 1024 ** 2)))
    s = int(stride) if stride and int(stride) > 0 else int(desc.get("suggested_stride") or 1)
    per = sum((desc.get("itemsizes") or {}).get(v, 0) for v in fields)
    _, n = _n_elements(desc, s)
    if n and per:
        rows.append((f"Overview at stride {s}",
                     _mb(n * per / 1024 ** 2) + (" per timestep" if ts else "")))
    return rows


def fields_table(desc, chosen, stride, shown_ranges):
    """Rows of (Field, Bytes per value, Full MB, At stride MB, In view, Min, Max).
    `shown_ranges`: {field: (min, max)} of the data on screen (empty before)."""
    if not desc:
        return []
    s = int(stride) if stride and int(stride) > 0 else int(desc.get("suggested_stride") or 1)
    full, n = _n_elements(desc, s)
    rows = []
    for v in desc.get("variables") or []:
        if v in (desc.get("positions") or []):
            continue
        b = int((desc.get("itemsizes") or {}).get(v, 0))
        rng = shown_ranges.get(v)
        rows.append((v, b,
                     round(full * b / 1024 ** 2, 2) if full and b else float("nan"),
                     round(n * b / 1024 ** 2, 2) if n and b else float("nan"),
                     "✓" if v in shown_ranges else ("chosen" if v in chosen else ""),
                     "" if rng is None else f"{rng[0]:.6g}",
                     "" if rng is None else f"{rng[1]:.6g}"))
    return rows


def timesteps_table(desc, ts_text, fetched):
    """Rows of (Index, Label, File, Size MB, Selected, Fetched)."""
    if not desc:
        return []
    files = desc.get("timestep_files")
    if not desc.get("timesteps"):
        name = os.path.basename(str(desc.get("uri", "")).rstrip("/"))
        size = desc.get("source_bytes")
        return [(0, "", name, round(size / 1024 ** 2, 2) if size else float("nan"),
                 "✓", "✓" if fetched else "")]
    if not files:
        files = [[lab, "", None] for lab in desc["timesteps"]]
    picked, _ = pick_timesteps(ts_text, [f[0] for f in files])
    picked = set(picked)
    return [(i, str(lab), name, round(size / 1024 ** 2, 2) if size else float("nan"),
             "✓" if lab in picked else "", "✓" if lab in fetched else "")
            for i, (lab, name, size) in enumerate(files)]
