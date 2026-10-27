"""Sieve in ParaView: overview first, zoom and filter, details on demand.

Load this file in ParaView (Tools > Manage Plugins > Load New, or
`LoadPlugin(path, ns=globals())` in pvpython). It adds, under Sources/Filters
> Sieve:

  Sieve Source   a coarse overview of a file or a folder of `…#N` timesteps,
                 local or remote (ssh), at a stride chosen to stay cheap;
                 the time slider walks the timesteps.
  Sieve Detail   a filter under an overview (or another detail): drag the box
                 over a feature and Apply — that box comes at full resolution.
  Sieve Spec     a source that runs any spec you type (named boxes, tracks…).

Every Apply writes a real Sieve spec (shown in the panel, saved next to its
output) and runs it with `sieve api` in the background: statically checked,
priced, held over budget until you press "Fetch anyway", served from Sieve's
cache when it can be, and recorded with provenance. ParaView stays responsive;
the result appears when it arrives. All of the logic lives in Sieve; this file
only maps panel settings to requests (sieve_pv_core.py runs them).

Headless use (pvpython, tests): set SIEVE_PV_SYNC=1 and each request is waited
for inside the pipeline update.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sieve_pv_core as core                           # noqa: E402

from paraview.util.vtkAlgorithm import (VTKPythonAlgorithmBase, smdomain,  # noqa: E402
                                        smhint, smproperty, smproxy)
from vtkmodules.vtkCommonCore import vtkDataArraySelection, vtkStringArray  # noqa: E402
from vtkmodules.vtkCommonDataModel import (vtkDataObject, vtkImageData,  # noqa: E402
                                           vtkPolyData, vtkStructuredGrid)
from vtkmodules.vtkCommonExecutionModel import (  # noqa: E402
    vtkStreamingDemandDrivenPipeline as SDDP)
from vtkmodules.vtkIOXML import (vtkXMLImageDataReader, vtkXMLPolyDataReader,  # noqa: E402
                                 vtkXMLStructuredGridReader)

_TYPES = {".vti": vtkImageData, ".vts": vtkStructuredGrid, ".vtp": vtkPolyData}
_READERS = {".vti": vtkXMLImageDataReader, ".vts": vtkXMLStructuredGridReader,
            ".vtp": vtkXMLPolyDataReader}
SOURCE_KEY = "sieve_source"          # field data: the descriptor, for filters below

# One runner, one session, one memo per ParaView process.
_STATE = {}


def _runner():
    if "runner" not in _STATE:
        _STATE["runner"] = core.Runner()
        # Quitting ParaView stops a request still running, rather than leaving
        # a `sieve api` process (and its remote job) behind.
        import atexit
        atexit.register(_STATE["runner"].shutdown)
    return _STATE["runner"]


def _session():
    if "session" not in _STATE:
        info = {}
        try:
            from paraview import servermanager
            info["paraview"] = servermanager.vtkSMProxyManager.GetParaViewSourceVersion()
        except Exception:
            pass
        _STATE["session"] = core.Session(root=os.environ.get("SIEVE_PV_SESSION_ROOT"),
                                         info=info)
    return _STATE["session"]


def _memo():
    if "memo" not in _STATE:
        _STATE["memo"] = core.Memo()
    return _STATE["memo"]


def _sync():
    return os.environ.get("SIEVE_PV_SYNC") == "1"


def _load(path):
    """A finished result file as a VTK dataset (by its extension)."""
    ext = os.path.splitext(path)[1].lower()
    reader = _READERS[ext]()
    reader.SetFileName(path)
    reader.Update()
    out = reader.GetOutputDataObject(0).NewInstance()
    out.ShallowCopy(reader.GetOutputDataObject(0))
    return out


def _tag(data, descriptor):
    """Carry the source's descriptor in field data, for filters downstream."""
    arr = vtkStringArray()
    arr.SetName(SOURCE_KEY)
    arr.InsertNextValue(json.dumps(descriptor, default=str))
    data.GetFieldData().RemoveArray(SOURCE_KEY)
    data.GetFieldData().AddArray(arr)


def _descriptor_of(data):
    if data is None:
        return None
    arr = data.GetFieldData().GetAbstractArray(SOURCE_KEY)
    if arr is None or arr.GetNumberOfValues() == 0:
        return None
    try:
        return json.loads(arr.GetValue(0))
    except ValueError:
        return None


def _inherit(obj):
    """Make the properties a Sieve object inherits visible to ParaView.

    ParaView builds a proxy from the decorated methods in the class's OWN
    namespace, so properties defined once on the shared base would vanish
    from every subclass. Copy them in (a subclass's own override wins). Must
    sit directly under @smproxy. The decorators below it hand over a wrapper;
    the class is at the end of its _pv_original_func chain."""
    cls = obj
    while hasattr(cls, "_pv_original_func"):
        cls = cls._pv_original_func
    for base in cls.__mro__[1:]:
        for name, val in base.__dict__.items():
            if name not in cls.__dict__ and callable(val) \
                    and hasattr(val, "_pv_original_func"):
                setattr(cls, name, val)
    return obj


def _snap(timesteps, t):
    """The timestep label to show for view time `t`: the last at or before it."""
    if not timesteps:
        return None
    if t is None:
        return timesteps[0]
    best = timesteps[0]
    for lab in timesteps:
        if lab <= t + 1e-9:
            best = lab
    return best


# ---------------------------------------------------------------------------
class _Sieve(VTKPythonAlgorithmBase):
    """What every Sieve pipeline object shares: submitting requests, polling
    them from ParaView's live-source timer, showing results and status."""

    NAME = "sieve"
    KIND = "sieve"

    def __init__(self, nin):
        # The port is declared as any dataset: RequestDataObject makes the
        # concrete kind (grid, curvilinear grid, points) the data turns out to
        # be — a narrower declaration would have VTK swap it back.
        VTKPythonAlgorithmBase.__init__(self, nInputPorts=nin, nOutputPorts=1,
                                        outputType="vtkDataSet")
        self._job = None             # the request in flight (or last finished)
        self._job_key = None
        self._shown = None           # the dataset on screen
        self._shown_key = None
        self._shown_label = None
        self._status = core.status_text(None)
        self._spec = ""
        self._approve = {}           # approvals for the NEXT request only
        self._budget_gb = 10.0
        self._budget_min = 58.0
        self._stride = 0
        self._filter = ""
        self._fields = vtkDataArraySelection()
        self._fields.AddObserver("ModifiedEvent", lambda *a: self.Modified())
        self._last_refresh = 0.0
        self._desc = None
        self._base = None            # this view's request, without its timestep

    def _key(self, params):
        return core.request_key(self.KIND, (self._desc or {}).get("uri"), params)

    # -- the live-source hook: ParaView's GUI calls this on a timer ------------
    def GetNeedsUpdate(self, *args):
        runner = _runner()
        runner.tick()
        changed = self._poll()
        import time
        if not changed and self._job is not None and self._job.state == "running" \
                and time.time() - self._last_refresh > 2.0:
            self._last_refresh = time.time()
            self._status = core.status_text(self._job, showing=self._shown_label)
            changed = True                     # refresh the status text
        if changed:
            self.Modified()
        return bool(changed)

    def _poll(self):
        """Handle a finished job. Returns True when something changed."""
        job = self._job
        if job is None or job.state not in ("done",) or getattr(job, "_handled", False):
            return False
        job._handled = True
        self._finished(job)
        return True

    # -- submitting -------------------------------------------------------------
    def _submit(self, request, key, action):
        sess = _session()
        step, workdir = sess.next_dir(f"{self.NAME}-{action}")
        request = {**request, "workdir": workdir,
                   "client": {"name": "paraview", "session": sess.id, "step": step},
                   "budget_bytes": int(self._budget_gb * 1024 ** 3),
                   "budget_seconds": self._budget_min * 60.0}
        if self._approve:
            request["approval"] = dict(self._approve)
            self._approve = {}
        job = core.Job("run", request, workdir, label=action)
        job.key, job.action, job.base = key, action, self._base
        self._job, self._job_key = job, key
        _runner().submit(id(self), job)
        self._status = core.status_text(job, showing=self._shown_label)
        if _sync():
            _runner().wait(job)
            self._poll()

    def _finished(self, job):
        r = job.result or {}
        self._spec = r.get("spec_text") or self._spec
        if job.action == "prefetch":
            if r.get("status") == "ok":
                # One request fetched every timestep: remember each one as if
                # it had been asked for alone, so the slider shows it at once.
                for f in r.get("files") or []:
                    if f.get("label") is not None:
                        _memo().put(self._key({**job.base, "timestep": f["label"]}),
                                    {**r, "files": [f]})
                n = sum(1 for f in r.get("files") or [] if f.get("label") is not None)
                self._status = (core.status_text(job)
                                + f"\nPrefetched {n} timesteps: the slider shows each "
                                  f"at once.")
            else:
                self._status = core.status_text(job)
            self._shown_key = None             # show the current time from the memo
            _session().trace(core.trace_record(self.NAME, job.action, job, key=job.key))
            return
        if r.get("status") == "ok" and r.get("files"):
            _memo().put(job.key, r)
            self._show(job.key, r)
        self._status = core.status_text(job)
        _session().trace(core.trace_record(self.NAME, job.action, job, key=job.key))

    def _show(self, key, result, label=None):
        files = result["files"]
        pick = files[0]
        if label is not None:
            pick = next((f for f in files if f.get("label") == label), files[0])
        data = _load(pick["path"])
        self._decorate(data)
        self._shown, self._shown_key = data, key
        self._shown_label = (f"timestep {pick['label']}" if pick.get("label") is not None
                             else "the previous result")

    def _decorate(self, data):
        """Subclasses tag the data with what downstream filters need."""

    def _serve(self, key, request, action, outInfo, label=None):
        """Show `key`'s data: on screen already, remembered, in flight, or asked
        for now. Never fetches the same thing twice."""
        out = outInfo.GetInformationObject(0)
        if key == self._shown_key and self._shown is not None:
            return self._emit(out)
        hit = _memo().get(key)
        if hit is not None:
            self._show(key, hit, label)
            self._status = core.status_text(None, result=hit) + "\n(from this session)"
            self._spec = hit.get("spec_text") or self._spec
            _session().trace(core.trace_record(self.NAME, action, None, key=key,
                                               memo_hit=True))
            return self._emit(out)
        if self._job is not None and self._job.action == "prefetch" \
                and self._job.state in ("queued", "running"):
            return self._emit(out)             # the prefetch brings this one too
        in_flight = (self._job is not None and self._job_key == key
                     and self._job.state in ("queued", "running"))
        held = (self._job is not None and self._job_key == key
                and self._job.state == "done" and not self._approve
                and (self._job.result or {}).get("status") != "ok")
        if not in_flight and not held:
            self._submit(request, key, action)
            if key == self._shown_key and self._shown is not None:
                return self._emit(out)
        return self._emit(out)

    def _emit(self, out):
        data = vtkDataObject.GetData(out)
        if self._shown is None or data is None:
            return 1
        if data.IsA(self._shown.GetClassName()):
            data.ShallowCopy(self._shown)
        else:
            # The data turned out to be of another kind than the output was
            # made for: remake the output on the next pass.
            self.Modified()
        return 1

    # -- the output's type follows the data -------------------------------------
    def _out_ext(self):
        return ".vti"

    def RequestDataObject(self, request, inInfo, outInfo):
        cls = _TYPES.get(self._out_ext(), vtkImageData)
        info = outInfo.GetInformationObject(0)
        data = info.Get(vtkDataObject.DATA_OBJECT())
        if data is None or not data.IsA(cls.__name__):
            data = cls()
            info.Set(vtkDataObject.DATA_OBJECT(), data)
        return 1

    # -- properties every Sieve object has ----------------------------------------
    @smproperty.dataarrayselection(name="Fields")
    def GetFieldSelection(self):
        return self._fields

    @smproperty.intvector(name="Stride", default_values=0)
    def SetStride(self, v):
        if v != self._stride:
            self._stride = int(v)
            self.Modified()

    @smproperty.stringvector(name="ValueFilter", default_values="")
    def SetValueFilter(self, v):
        v = (v or "").strip()
        if v != self._filter:
            self._filter = v
            self.Modified()

    @smproperty.doublevector(name="BudgetGB", default_values=10.0)
    def SetBudgetGB(self, v):
        self._budget_gb = float(v)

    @smproperty.doublevector(name="BudgetMinutes", default_values=58.0)
    def SetBudgetMinutes(self, v):
        self._budget_min = float(v)

    @smproperty.xml('<Property name="FetchAnyway" label="Fetch Anyway" '
                    'command="FetchAnyway" panel_widget="command_button">'
                    '<Documentation>Approve the request that was held (over budget, '
                    'or a whole-file copy), and run it.</Documentation></Property>')
    def FetchAnyway(self):
        holds = ((self._job.result or {}).get("holds") or {}) if self._job else {}
        self._approve = {"budget": True, "fetch": "fetch" in holds}
        self._job_key = None                  # ask again, approved
        self.Modified()

    @smproperty.xml('<Property name="Cancel" command="Cancel" '
                    'panel_widget="command_button"/>')
    def Cancel(self):
        _runner().cancel(id(self))
        self._status = "Cancelled."
        self.Modified()

    @smproperty.xml('<Property name="PromoteToSpec" label="Promote To Spec" '
                    'command="PromoteToSpec" panel_widget="command_button">'
                    '<Documentation>Copy the spec this object last ran into a new '
                    'Sieve Spec, to edit it freely (several boxes, a track, more '
                    'forms).</Documentation></Property>')
    def PromoteToSpec(self):
        if not self._spec:
            self._status = "Nothing to promote yet: Apply first."
            self.Modified()
            return
        # The copy saves by file name alone: a Sieve Spec runs in its own step
        # folder, so it never writes over the output it was copied from.
        text = re.sub(r"(save\(.*?,\s*)(['\"])([^'\"]+)\2(\s*\))",
                      lambda m: f"{m.group(1)}{m.group(2)}{os.path.basename(m.group(3))}"
                                f"{m.group(2)}{m.group(4)}", self._spec, flags=re.S)
        try:
            from paraview import simple
            proxy = simple.SieveSpec(SpecText=text)
            simple.RenameProxy(proxy, "sources", f"Spec from {self.NAME}")
            self._status += "\nCopied the spec into a new Sieve Spec: edit it there."
        except Exception as e:
            self._status += f"\nCould not create a Sieve Spec: {type(e).__name__}: {e}"
        self.Modified()

    @smproperty.xml('<Property name="Prefetch" command="Prefetch" '
                    'panel_widget="command_button"><Documentation>Fetch every '
                    'timestep of a series in one request, so the time slider shows '
                    'each at once.</Documentation></Property>')
    def Prefetch(self):
        ts = (self._desc or {}).get("timesteps")
        if not ts or self._base is None:
            self._status = ("Prefetch needs a series (a folder of timesteps) shown "
                            "here first.")
            self.Modified()
            return
        params = {**self._base, "timesteps": [ts[0], ts[-1]]}
        self._submit({"descriptor": self._desc, "params": params},
                     self._key(params), "prefetch")
        self.Modified()

    @smproperty.xml('<StringVectorProperty name="Status" command="GetStatus" '
                    'information_only="1" panel_visibility="default">'
                    '<Hints><Widget type="multi_line"/></Hints></StringVectorProperty>')
    def GetStatus(self):
        return self._status

    @smproperty.xml('<StringVectorProperty name="Spec" command="GetSpec" '
                    'information_only="1" panel_visibility="default">'
                    '<Hints><Widget type="multi_line" syntax="python"/></Hints>'
                    '</StringVectorProperty>')
    def GetSpec(self):
        return self._spec


# ---------------------------------------------------------------------------
@smproxy.source(name="SieveSource", label="Sieve Source", support_reload=False)
@_inherit
@smhint.xml('<ShowInMenu category="Sieve"/>')
@smhint.xml('<LiveSource interval="500"/>')
@smhint.xml('<RepresentationType view="RenderView" type="Surface"/>')
class SieveSource(_Sieve):
    """A coarse overview of a source — a file or a folder of timesteps, local
    or remote (ssh://host/path or user@host:/path)."""

    NAME = "source"
    KIND = "source"

    def __init__(self):
        _Sieve.__init__(self, 0)
        self._uri = ""
        self._positions = ""
        self._desc_job = None

    def _out_ext(self):
        return (self._desc or {}).get("out_ext", ".vti")

    @smproperty.stringvector(name="Source", default_values="")
    def SetSource(self, v):
        v = (v or "").strip()
        if v != self._uri:
            self._uri, self._desc = v, None
            self._fields.RemoveAllArrays()
            self.Modified()

    @smproperty.stringvector(name="Positions", default_values="")
    def SetPositions(self, v):
        v = (v or "").strip()
        if v != self._positions:
            self._positions, self._desc = v, None
            self.Modified()

    @smproperty.xml('<Property name="Connect" command="Connect" '
                    'panel_widget="command_button"><Documentation>Open a session '
                    'to the remote host: a password dialog appears on your screen; '
                    'the secret goes straight to ssh.</Documentation></Property>')
    def Connect(self):
        if not self._uri:
            return
        sess = _session()
        _, workdir = sess.next_dir("connect")
        job = core.Job("connect", {"uri": self._uri}, workdir, label="connect")
        self._status = "Connecting… answer the password dialog."
        _runner().submit(("connect", id(self)), job)
        _runner().wait(job, timeout=180)
        r = job.result or {}
        self._status = ("Connected. Press Apply." if r.get("status") == "ok"
                        else "Could not connect:\n" + (r.get("message") or r.get("error")
                                                       or ""))
        self._desc = None
        self.Modified()

    @smproperty.doublevector(name="TimestepValues", information_only="1",
                             si_class="vtkSITimeStepsProperty")
    def GetTimestepValues(self):
        ts = (self._desc or {}).get("timesteps")
        return [float(t) for t in ts] if ts else None

    # -- metadata -----------------------------------------------------------------
    def _need_descriptor(self):
        """Ask for the source's descriptor once per source; True while waiting."""
        if not self._uri or self._desc is not None:
            return False
        job = self._desc_job
        if job is not None and job.request.get("uri") == self._uri \
                and job.state in ("queued", "running"):
            return True
        sess = _session()
        _, workdir = sess.next_dir("inspect")
        req = {"uri": self._uri}
        if self._positions:
            req["positions"] = [p.strip() for p in self._positions.split(",")]
        job = core.Job("inspect", req, workdir, label="inspect")
        self._desc_job = job
        _runner().submit(("inspect", id(self)), job)
        self._status = "Reading the source's metadata…"
        if _sync():
            _runner().wait(job)
            self._take_descriptor(job)
            return self._desc is None and False
        return True

    def _take_descriptor(self, job):
        job._handled = True
        r = job.result or {}
        if r.get("status") == "ok":
            self._desc = r
            have = {self._fields.GetArrayName(i)
                    for i in range(self._fields.GetNumberOfArrays())}
            for v in r.get("variables") or []:
                if v not in have and v not in (r.get("positions") or []):
                    self._fields.AddArray(v)
            ts = r.get("timesteps")
            self._status = (f"{r['filetype']} {r['modality']}"
                            + (f" {tuple(r['grid'])}" if r.get("grid") else "")
                            + (f", {len(ts)} timesteps" if ts else "")
                            + f". Region coordinates: {r.get('region_text') or r['coords']}."
                            + "\nFetching the overview…")
        elif r.get("status") == "needs_session":
            self._status = ("Not connected to the remote host. Press Connect "
                            "(a password dialog opens), then Apply.")
        else:
            self._status = ("Cannot read this source: "
                            + (r.get("message") or r.get("error") or r.get("status", "")))
        _session().trace({"source": self.NAME, "action": "inspect",
                          "status": r.get("status"), **job.timings()})

    def _poll(self):
        job = self._desc_job
        if job is not None and job.state == "done" and not getattr(job, "_handled", False):
            self._take_descriptor(job)
            return True
        return _Sieve._poll(self)

    # -- pipeline -------------------------------------------------------------------
    def RequestDataObject(self, request, inInfo, outInfo):
        # The output's kind (grid, curvilinear, points) is the source's: read
        # its metadata first (in the background, unless running synchronously).
        self._need_descriptor()
        return _Sieve.RequestDataObject(self, request, inInfo, outInfo)

    def RequestInformation(self, request, inInfo, outInfo):
        self._need_descriptor()
        info = outInfo.GetInformationObject(0)
        info.Remove(SDDP.TIME_STEPS())
        info.Remove(SDDP.TIME_RANGE())
        ts = (self._desc or {}).get("timesteps")
        if ts:
            for t in ts:
                info.Append(SDDP.TIME_STEPS(), float(t))
            info.Append(SDDP.TIME_RANGE(), float(ts[0]))
            info.Append(SDDP.TIME_RANGE(), float(ts[-1]))
        return 1

    def RequestData(self, request, inInfo, outInfo):
        if not self._uri or self._need_descriptor() or self._desc is None:
            return 1
        desc = self._desc
        info = outInfo.GetInformationObject(0)
        label = None
        if desc.get("timesteps"):
            t = info.Get(SDDP.UPDATE_TIME_STEP()) \
                if info.Has(SDDP.UPDATE_TIME_STEP()) else None
            label = _snap(desc["timesteps"], t)
        params = self._params(desc, label)
        self._base = {k: v for k, v in params.items() if k != "timestep"}
        key = self._key(params)
        self._serve(key, {"descriptor": desc, "params": params}, "overview", outInfo,
                    label=label)
        if label is not None:
            vtkDataObject.GetData(outInfo).GetInformation().Set(
                vtkDataObject.DATA_TIME_STEP(), float(label))
        return 1

    def _params(self, desc, label):
        n = self._fields.GetNumberOfArrays()
        chosen = [self._fields.GetArrayName(i) for i in range(n)
                  if self._fields.GetArraySetting(i)]
        p = {"stride": self._stride if self._stride > 0 else desc.get("suggested_stride", 1)}
        if n and len(chosen) < n:
            p["fields"] = chosen
        if self._filter:
            p["threshold"] = self._filter
        if self._positions:
            p["positions"] = [s.strip() for s in self._positions.split(",")]
        if label is not None:
            p["timestep"] = label
        return p

    def _decorate(self, data):
        _tag(data, {**(self._desc or {}), "positions_given": self._positions or None})


# ---------------------------------------------------------------------------
@smproxy.filter(name="SieveDetail", label="Sieve Detail", support_reload=False)
@_inherit
@smproperty.input(name="Input", port_index=0)
@smdomain.datatype(dataTypes=["vtkDataObject"], composite_data_supported=False)
@smhint.xml('<ShowInMenu category="Sieve"/>')
@smhint.xml('<LiveSource interval="500"/>')
@smhint.xml('<RepresentationType view="RenderView" type="Surface"/>')
class SieveDetail(_Sieve):
    """The box, at full resolution: drag it over a feature of the overview (or
    of another detail) and Apply. Region coordinates follow the source — the
    spec names them (coords=…), so it means the same thing anywhere."""

    NAME = "detail"
    KIND = "detail"

    def __init__(self):
        _Sieve.__init__(self, 1)
        self._stride = 1
        self._pos = [0.0, 0.0, 0.0]
        self._len = [1.0, 1.0, 1.0]

    def FillInputPortInformation(self, port, info):
        info.Set(self.INPUT_REQUIRED_DATA_TYPE(), "vtkDataObject")
        return 1

    def _out_ext(self):
        return (self._desc or {}).get("out_ext", ".vti")

    def RequestDataObject(self, request, inInfo, outInfo):
        # A detail is the same kind of data as what it is drawn on.
        source = vtkDataObject.GetData(inInfo[0])
        cls = next((c for c in _TYPES.values()
                    if source is not None and source.IsA(c.__name__)), vtkImageData)
        info = outInfo.GetInformationObject(0)
        data = info.Get(vtkDataObject.DATA_OBJECT())
        if data is None or not data.IsA(cls.__name__):
            info.Set(vtkDataObject.DATA_OBJECT(), cls())
        return 1

    @smproperty.intvector(name="Stride", default_values=1)
    def SetStride(self, v):
        _Sieve.SetStride(self, v)

    @smproperty.xml('''<DoubleVectorProperty name="BoxPosition" label="Box Corner"
        command="SetBoxPosition" number_of_elements="3" default_values="0 0 0">
        <BoundsDomain name="range" mode="normal" default_mode="min">
          <RequiredProperties><Property name="Input" function="Input"/></RequiredProperties>
        </BoundsDomain>
        <Documentation>The box's lower corner, in the coordinates the view's axes show.
        </Documentation></DoubleVectorProperty>''')
    def SetBoxPosition(self, x, y, z):
        self._pos = [x, y, z]
        self.Modified()

    @smproperty.xml('''<DoubleVectorProperty name="BoxLength" label="Box Size"
        command="SetBoxLength" number_of_elements="3" default_values="1 1 1">
        <BoundsDomain name="range" mode="component_magnitude" default_mode="max">
          <RequiredProperties><Property name="Input" function="Input"/></RequiredProperties>
        </BoundsDomain>
        <Documentation>The box's size along each axis.</Documentation>
        </DoubleVectorProperty>''')
    def SetBoxLength(self, x, y, z):
        self._len = [x, y, z]
        self.Modified()

    # The widget group must come after the properties it names (ParaView emits
    # properties in the order their methods are defined).
    @smproperty.xml('''<PropertyGroup label="Region" panel_widget="InteractiveBox">
        <Property function="Position" name="BoxPosition"/>
        <Property function="Scale" name="BoxLength"/>
        <Property function="Input" name="Input"/>
        </PropertyGroup>''')
    def _region_group(self):
        pass

    def _box(self):
        lo = [min(p, p + l) for p, l in zip(self._pos, self._len)]
        hi = [max(p, p + l) for p, l in zip(self._pos, self._len)]
        return {"lo": lo, "hi": hi}

    def RequestData(self, request, inInfo, outInfo):
        source = vtkDataObject.GetData(inInfo[0])
        desc = _descriptor_of(source)
        if desc is None:
            self._status = ("Put this under a Sieve Source (or another Sieve "
                            "Detail): it needs to know which data the box is on.")
            return 1
        self._desc = desc
        box = self._box()
        bounds = source.GetBounds() if hasattr(source, "GetBounds") else None
        if bounds is not None and _covers(box, bounds):
            self._status = ("Drag the box over a feature (or type its corner and "
                            "size), then Apply — that box comes at full resolution.")
            return 1
        label = None
        if desc.get("timesteps"):
            t = source.GetInformation().Get(vtkDataObject.DATA_TIME_STEP()) \
                if source.GetInformation().Has(vtkDataObject.DATA_TIME_STEP()) else None
            label = _snap(desc["timesteps"], t)
        params = {"box": box, "stride": max(1, self._stride)}
        n = self._fields.GetNumberOfArrays()
        if n == 0:
            for v in desc.get("variables") or []:
                if v not in (desc.get("positions") or []):
                    self._fields.AddArray(v)
            n = self._fields.GetNumberOfArrays()
        chosen = [self._fields.GetArrayName(i) for i in range(n)
                  if self._fields.GetArraySetting(i)]
        if n and len(chosen) < n:
            params["fields"] = chosen
        if self._filter:
            params["threshold"] = self._filter
        if desc.get("positions_given"):
            params["positions"] = [s.strip() for s in desc["positions_given"].split(",")]
        self._base = dict(params)
        if label is not None:
            params["timestep"] = label
        key = self._key(params)
        self._serve(key, {"descriptor": desc, "params": params}, "detail", outInfo,
                    label=label)
        if label is not None:
            vtkDataObject.GetData(outInfo).GetInformation().Set(
                vtkDataObject.DATA_TIME_STEP(), float(label))
        return 1

    def _decorate(self, data):
        _tag(data, self._desc or {})

    def _show(self, key, result, label=None):
        _Sieve._show(self, key, result, label)
        # The data must sit in the box asked for (a curvilinear block may reach
        # past it: its rows do not follow the box). Say so if not.
        if self._shown is None or self._shown.IsA("vtkStructuredGrid"):
            return
        b, box = self._shown.GetBounds(), self._box()
        step = list(self._shown.GetSpacing()) if self._shown.IsA("vtkImageData") \
            else [0.0, 0.0, 0.0]
        out = [ax for a, ax in enumerate("xyz")
               if b[2 * a] < box["lo"][a] - step[a] - 1e-9
               or b[2 * a + 1] > box["hi"][a] + step[a] + 1e-9]
        if out and b[0] <= b[1]:
            self._box_warning = (f"Note: the result reaches past the box on "
                                 f"{', '.join(out)}.")
        else:
            self._box_warning = ""

    def _finished(self, job):
        self._box_warning = ""
        _Sieve._finished(self, job)
        if getattr(self, "_box_warning", ""):
            self._status += "\n" + self._box_warning


def _covers(box, bounds, tol=1e-6):
    """True when the box spans the whole of `bounds` — the untouched default."""
    for a in range(3):
        lo, hi = bounds[2 * a], bounds[2 * a + 1]
        span = max(hi - lo, 1e-12)
        if box["lo"][a] > lo + tol * span or box["hi"][a] < hi - tol * span:
            return False
    return True


# ---------------------------------------------------------------------------
@smproxy.source(name="SieveSpec", label="Sieve Spec", support_reload=False)
@_inherit
@smhint.xml('<ShowInMenu category="Sieve"/>')
@smhint.xml('<LiveSource interval="500"/>')
@smhint.xml('<RepresentationType view="RenderView" type="Surface"/>')
class SieveSpec(_Sieve):
    """Runs a spec you write — any spec Sieve accepts (named boxes, a box that
    follows a track, several fields…). Its first saved file is shown."""

    NAME = "spec"
    KIND = "spec"

    def __init__(self):
        _Sieve.__init__(self, 0)
        self._text = ""
        self._ext = ".vti"

    def _out_ext(self):
        # The kind of data the spec saves, from its save() path.
        m = re.findall(r"save\(.*?,\s*['\"]([^'\"]+)['\"]\s*\)", self._text, re.S)
        ext = os.path.splitext(m[-1])[1].lower() if m else ""
        return ext if ext in _TYPES else self._ext

    @smproperty.xml('''<StringVectorProperty name="SpecText" label="Spec Text"
        command="SetSpecText" number_of_elements="1" default_values="">
        <Hints><Widget type="multi_line" syntax="python"/></Hints>
        <Documentation>A Sieve spec: source(...) and the forms, ending in save(...).
        </Documentation></StringVectorProperty>''')
    def SetSpecText(self, v):
        if (v or "") != self._text:
            self._text = v or ""
            self.Modified()

    def RequestData(self, request, inInfo, outInfo):
        if not self._text.strip():
            self._status = "Write a spec (it must end in save(...)) and press Apply."
            return 1
        key = core.request_key("spec", None, {"text": self._text})
        self._serve(key, {"spec_text": self._text}, "spec", outInfo)
        return 1

    def _show(self, key, result, label=None):
        ext = os.path.splitext(result["files"][0]["path"])[1].lower()
        if ext not in _READERS:
            self._status = (f"The spec wrote {ext} files; the view shows .vti, .vts "
                            f"or .vtp. Save to one of those to see it here.")
            return
        if ext != self._ext:
            self._ext = ext                    # the next update makes that type
        _Sieve._show(self, key, result, label)
