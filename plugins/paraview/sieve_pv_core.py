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
        lines = [f"Fetched: result {_mb(est.get('output_mb'))}"
                 + (f" in {r['total_s']:.1f} s" if r.get("total_s") else "")]
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
