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
from vtkmodules.vtkCommonCore import (vtkDataArraySelection, vtkDoubleArray,  # noqa: E402
                                      vtkLongLongArray, vtkStringArray)
from vtkmodules.vtkCommonDataModel import (vtkDataObject, vtkImageData,  # noqa: E402
                                           vtkPolyData, vtkStructuredGrid, vtkTable)
from vtkmodules.vtkCommonExecutionModel import (  # noqa: E402
    vtkExecutive, vtkStreamingDemandDrivenPipeline as SDDP)
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


def _objects():
    """Every live Sieve object in this process (held weakly: a deleted one
    drops out)."""
    if "objects" not in _STATE:
        import weakref
        _STATE["objects"] = weakref.WeakSet()
    return _STATE["objects"]


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
    # ParaView lays the panel out by the line numbers of the property methods;
    # PANEL (method names, top to bottom) overrides that for this class alone.
    for i, name in enumerate(getattr(cls, "PANEL", ())):
        setattr(cls, name, _at_line(cls.__dict__[name], i + 1))
    return obj


def _at_line(method, line):
    """A copy of a property method that ParaView places as if at `line`."""
    import types
    fn = method
    while hasattr(fn, "_pv_original_func"):
        fn = fn._pv_original_func
    copy = types.FunctionType(fn.__code__.replace(co_firstlineno=line),
                              fn.__globals__, fn.__name__, fn.__defaults__,
                              fn.__closure__)
    copy.__dict__.update(fn.__dict__)          # the property's XML rides here
    copy.__qualname__, copy.__doc__ = fn.__qualname__, fn.__doc__
    return copy


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

    def __init__(self, nin, nout=1):
        # The port is declared as any dataset: RequestDataObject makes the
        # concrete kind (grid, curvilinear grid, points) the data turns out to
        # be — a narrower declaration would have VTK swap it back.
        VTKPythonAlgorithmBase.__init__(self, nInputPorts=nin, nOutputPorts=nout,
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
        self._fields.AddObserver("ModifiedEvent", lambda *a: self._settings_changed())
        self._last_refresh = 0.0
        self._desc = None
        self._base = None            # this view's request, without its timestep
        self._proxy = None           # this object's server-manager proxy (GUI side)
        self._declared = None        # the grid extent ParaView was last told
        self._kick = False           # a button asked for a pipeline pass
        self._cancelled = None       # the request Cancel stopped: not asked again
        self._shown_text = None      # the (status, spec) the panel last read
        _objects().add(self)

    def _key(self, params):
        return core.request_key(self.KIND, (self._desc or {}).get("uri"), params)

    # -- the live-source hook: ParaView's GUI calls this on a timer ------------
    def GetNeedsUpdate(self, *args):
        """ParaView 6.1's GUI times only sources, never a filter (a Sieve
        Detail): each tick advances every Sieve object, and one that changed
        but is not this one is marked modified here — saying yes for it has
        ParaView update the pipelines and render the views it shows in."""
        _runner().tick()
        changed = False
        for obj in list(_objects()):
            if obj is self:
                changed = self._step() or changed
            elif obj._step():
                obj.Modified()
                changed = True
        return changed

    def _step(self):
        """One tick of this object: hand over a finished request, refresh the
        running status and the panel. True when its output must update."""
        changed = self._poll()
        import time
        if not changed and self._job is not None and self._job.state == "running" \
                and time.time() - self._last_refresh > 2.0:
            self._last_refresh = time.time()
            self._status = core.status_text(self._job, showing=self._shown_label)
            changed = True                     # refresh the status text
        if self._shown_extent() != self._declared or self._kick:
            self._kick = False
            changed = True                     # declare the new data's extent
        if changed:
            self.Modified()
        self._refresh_panel()
        return bool(changed)

    def _refresh_panel(self):
        """The panel reads Status and Spec only when ParaView refreshes property
        information — not after a live-source update. Ask for it when either
        changed (in the builtin session the proxy lives in this process)."""
        text = (self._status, self._spec, self._panel_extra())
        if text == self._shown_text:
            return
        proxy = self._own_proxy()
        if proxy is None:
            return
        self._shown_text = text
        proxy.UpdatePropertyInformation()

    def _panel_extra(self):
        """Other read-only panel text a subclass shows (refreshed alike)."""
        return None

    def _own_proxy(self):
        """This object's server-manager proxy (the builtin session keeps it in
        this process), or None."""
        if self._proxy is None:
            try:
                from paraview import servermanager
                pxm = servermanager.ProxyManager()
                for group in ("sources", "filters"):
                    for (_, _), p in pxm.GetProxiesInGroup(group).items():
                        if p.GetClientSideObject() is self:
                            self._proxy = p
                            return p
            except Exception:                                   # noqa: BLE001
                return None
        return self._proxy

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
        labelled = [f for f in r.get("files") or [] if f.get("label") is not None]
        if r.get("status") == "ok" and labelled:
            # One request fetched the selected timesteps: remember each as if
            # it had been asked for alone, so the slider shows it at once.
            _memo().put(job.key, r)
            for f in labelled:
                _memo().put(self._key({**job.base, "timestep": f["label"]}),
                            {**r, "files": [f]})
            want = getattr(self, "_want", None)
            pick = next((f for f in labelled if f["label"] == want), labelled[0])
            self._show(self._key({**job.base, "timestep": pick["label"]}),
                       {**r, "files": [pick]}, pick["label"])
            self._status = (core.status_text(job)
                            + f"\nFetched {len(labelled)} timestep"
                            + ("s" if len(labelled) != 1 else "")
                            + ": the time slider shows each at once.")
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

    def _series_desc(self):
        """The descriptor as this object shows it (a Source narrows its
        timesteps to the chosen range)."""
        return self._desc or {}

    def _shown_extent(self):
        get = getattr(self._shown, "GetExtent", None)
        return tuple(get()) if get else None

    def _declare_extent(self, outInfo):
        """A grid's extent must be stated in RequestInformation: ParaView's
        surface and volume representations draw only what it says (an
        undeclared or inherited extent draws nothing). The data arrives
        later, so this states the data now on screen; when that changes,
        GetNeedsUpdate asks for another pass."""
        info = outInfo.GetInformationObject(0)
        ext = self._shown_extent()
        if ext is None:
            info.Remove(SDDP.WHOLE_EXTENT())
        else:
            info.Set(SDDP.WHOLE_EXTENT(), *ext)
        self._declared = ext

    def RequestInformation(self, request, inInfo, outInfo):
        self._declare_extent(outInfo)
        return 1

    def _serve(self, key, request, action, outInfo, label=None):
        """Show `key`'s data: on screen already, remembered, in flight, or asked
        for now. Never fetches the same thing twice."""
        out = outInfo.GetInformationObject(0)
        if key == self._shown_key and self._shown is not None:
            return self._emit(out)
        if key == self._cancelled:
            return self._emit(out)             # the user stopped this one
        hit = _memo().get(key)
        if hit is not None:
            self._show(key, hit, label)
            self._status = core.status_text(None, result=hit) + "\n(from this session)"
            self._spec = hit.get("spec_text") or self._spec
            _session().trace(core.trace_record(self.NAME, action, None, key=key,
                                               memo_hit=True))
            return self._emit(out)
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

    def _serve_series(self, label, base, desc, action, outInfo):
        """Show timestep `label` of a series. The data comes by the selection:
        one request fetches every selected timestep (the Timesteps field, or
        all), each is remembered on its own, and moving the time slider then
        shows them without asking again."""
        out = outInfo.GetInformationObject(0)
        key_t = self._key({**base, "timestep": label})
        self._want = label
        if key_t == self._shown_key and self._shown is not None:
            return self._emit(out)
        hit = _memo().get(key_t)
        if hit is not None:
            self._show(key_t, hit, label)
            self._spec = hit.get("spec_text") or self._spec
            self._status = (core.status_text(None, result=hit)
                            + f"\nTimestep #{label}, from this session.")
            return self._emit(out)
        params = {**base, **_selection(desc)}
        self._serve(self._key(params), {"descriptor": desc, "params": params}, action,
                    outInfo, label=label)
        return 1

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
            self._settings_changed()

    def _settings_changed(self):
        """A setting the request is built from changed (pushed by Apply)."""
        self._cancelled = None
        self.Modified()

    @smproperty.stringvector(name="ValueFilter", default_values="")
    def SetValueFilter(self, v):
        v = (v or "").strip()
        if v != self._filter:
            self._filter = v
            self._settings_changed()

    @smproperty.doublevector(name="BudgetGB", default_values=10.0)
    def SetBudgetGB(self, v):
        if float(v) != self._budget_gb:
            self._budget_gb = float(v)
            self._settings_changed()

    @smproperty.doublevector(name="BudgetMinutes", default_values=58.0)
    def SetBudgetMinutes(self, v):
        if float(v) != self._budget_min:
            self._budget_min = float(v)
            self._settings_changed()

    @smproperty.xml('<Property name="FetchAnyway" label="Fetch Anyway" '
                    'command="FetchAnyway" panel_widget="command_button">'
                    '<Documentation>Approve the request that was held (over budget, '
                    'or a whole-file copy), and run it.</Documentation></Property>')
    def FetchAnyway(self):
        holds = ((self._job.result or {}).get("holds") or {}) if self._job else {}
        self._approve = {"budget": True, "fetch": "fetch" in holds}
        self._job_key = None                  # ask again, approved
        self._cancelled = None
        self.Modified()

    @smproperty.xml('<Property name="Cancel" command="Cancel" '
                    'panel_widget="command_button"/>')
    def Cancel(self):
        # Stopping the local process ends its ssh; the remote reducer notices
        # and stops too (executor._exit_with_caller).
        self._cancelled = self._job_key
        _runner().cancel(id(self))
        self._status = ("Cancelled. Change a setting and Apply to ask for "
                        "something else" + (", or press Fetch Overview to ask again."
                                            if self.KIND == "source" else "."))
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
@smhint.xml('<View type="SpreadSheetView" port="1"/>')
@smhint.xml('<View type="None" port="2"/>')
@smhint.xml('<View type="None" port="3"/>')
@smproperty.xml('<OutputPort name="Data" index="0"/>')
@smproperty.xml('<OutputPort name="Summary" index="1"/>')
@smproperty.xml('<OutputPort name="Fields" index="2"/>')
@smproperty.xml('<OutputPort name="Timesteps" index="3"/>')
class SieveSource(_Sieve):
    """A coarse overview of a source — a file or a folder of timesteps, local
    or remote (ssh://host/path or user@host:/path)."""

    NAME = "source"
    KIND = "source"
    PANEL = ("SetSource", "SetPositions", "GetFieldSelection", "SetStride",
             "SetValueFilter", "SetBudgetGB", "SetBudgetMinutes", "SetTimesteps",
             "GetStatus", "GetSpec", "FetchOverview", "ShowMetadata",
             "FetchAnyway", "Cancel", "Connect", "PromoteToSpec")
    # Outputs: the data, then what the source is, as three tables. ParaView
    # opens a SpreadSheet on the Summary once (on the first Apply); its
    # "Showing" list switches to Fields or Timesteps.
    TABLES = (1, 2, 3)

    def __init__(self):
        _Sieve.__init__(self, 0, nout=4)
        self._uri = ""
        self._positions = ""
        self._desc_job = None
        self._desc_failed = None     # the (uri, positions) a descriptor was refused for
        # The first Apply only reads the metadata; data is fetched once the
        # user has seen it — an Apply that changes a setting, or Fetch Overview.
        self._armed = False
        self._filling = False        # the plugin itself is filling the Fields list
        self._ts_text = ""           # the Timesteps field: "" = every timestep
        self._ts_problem = None      # why that text selects nothing usable

    def _out_ext(self):
        return (self._desc or {}).get("out_ext", ".vti")

    @smproperty.stringvector(name="Source", default_values="")
    def SetSource(self, v):
        v = (v or "").strip()
        if v != self._uri:
            self._uri, self._desc = v, None
            self._armed = False
            self._shown = self._shown_key = None
            self._filling = True
            self._fields.RemoveAllArrays()
            self._filling = False
            self.Modified()

    @smproperty.xml('<StringVectorProperty name="Positions" command="SetPositions" '
                    'number_of_elements="1" default_values="" '
                    'panel_visibility="advanced"><Documentation>Points only: the '
                    'names of the variables holding x, y, z, as "px,py,pz". Sieve '
                    'finds them itself when they are called x, y, z (or similar); '
                    'set this only when it does not.</Documentation>'
                    '</StringVectorProperty>')
    def SetPositions(self, v):
        v = (v or "").strip()
        if v != self._positions:
            self._positions, self._desc = v, None
            self._armed = False
            self.Modified()

    def _settings_changed(self):
        if self._desc is not None and not self._filling:
            self._armed = True           # an Apply after the metadata: fetch
        _Sieve._settings_changed(self)

    @smproperty.xml('<Property name="FetchOverview" label="Fetch Overview" '
                    'command="FetchOverview" panel_widget="command_button">'
                    '<Documentation>Fetch the overview with the settings as they '
                    'are (Apply fetches too, once a setting is changed).'
                    '</Documentation></Property>')
    def FetchOverview(self):
        if self._desc is None:
            self._status = ("Press Apply first: the source's metadata is read "
                            "before anything is fetched.")
        else:
            self._armed = True
            self._kick = True
            self._cancelled = None
        self.Modified()

    @smproperty.xml('<Property name="Connect" command="Connect" '
                    'panel_widget="command_button"><Documentation>Open a session '
                    'to the remote host: a password dialog appears on your screen; '
                    'the secret goes straight to ssh.</Documentation></Property>')
    def Connect(self):
        if not self._uri:
            self._status = ("Set Source to a remote path (ssh://host/path) and press "
                            "Apply first: ParaView passes Source on only at Apply.")
            self.Modified()
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
        self._desc = self._desc_failed = None    # ask again on the next Apply
        self.Modified()

    @smproperty.xml('<StringVectorProperty name="Timesteps" command="SetTimesteps" '
                    'number_of_elements="1" default_values=""><Documentation>Which '
                    '#N of a series to fetch and show: "1-9" (a range), '
                    '"1,4,9" (exactly these), or both, "1-9,20". Empty: all.'
                    '</Documentation></StringVectorProperty>')
    def SetTimesteps(self, v):
        v = (v or "").strip()
        if v != self._ts_text:
            self._ts_text = v
            self._settings_changed()


    @smproperty.xml('<Property name="ShowMetadata" label="Show Metadata" '
                    'command="ShowMetadata" panel_widget="command_button">'
                    '<Documentation>Open the Summary, Fields and Timesteps tables '
                    'beside the view.</Documentation></Property>')
    def ShowMetadata(self):
        try:
            from paraview import simple
            proxy = self._own_proxy()
            view = simple.CreateView("SpreadSheetView")
            simple.AssignViewToLayout(view)
            simple.Show(simple.OutputPort(proxy, 1), view)
            simple.Render(view)
        except Exception as e:                                  # noqa: BLE001
            self._status += f"\nCould not open the tables: {type(e).__name__}: {e}"
            self.Modified()

    def _series_desc(self):
        """The descriptor with its timesteps narrowed to the Timesteps field;
        the series' full list rides along for the request to choose its form."""
        desc = self._desc or {}
        ts = desc.get("timesteps")
        if not ts:
            return desc
        keep, self._ts_problem = core.pick_timesteps(self._ts_text, ts)
        return {**desc, "timesteps": keep or ts, "series_timesteps": ts}

    @smproperty.doublevector(name="TimestepValues", information_only="1",
                             si_class="vtkSITimeStepsProperty")
    def GetTimestepValues(self):
        ts = self._series_desc().get("timesteps")
        return [float(t) for t in ts] if ts else None

    # -- metadata -----------------------------------------------------------------
    def _need_descriptor(self):
        """Ask for the source's descriptor once per source; True while waiting."""
        if not self._uri or self._desc is not None:
            return False
        if self._desc_failed == (self._uri, self._positions):
            return False                 # refused: wait for Connect or a new Source
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
        self._status = ("Checking the session to the remote host…"
                        if core.is_remote(self._uri) else "Reading the source's metadata…")
        if _sync():
            _runner().wait(job)
            self._take_descriptor(job)
            return self._desc is None and False
        return True

    def _take_descriptor(self, job):
        job._handled = True
        r = job.result or {}
        if r.get("status") != "ok":
            self._desc_failed = (job.request.get("uri"), self._positions)
        if r.get("status") == "ok":
            self._desc = r
            have = {self._fields.GetArrayName(i)
                    for i in range(self._fields.GetNumberOfArrays())}
            self._filling = True
            for v in r.get("variables") or []:
                if v not in have and v not in (r.get("positions") or []):
                    self._fields.AddArray(v)
            self._filling = False
            ts = r.get("timesteps")
            self._status = (f"{r['filetype']} {r['modality']}"
                            + (f" {tuple(r['grid'])}" if r.get("grid") else "")
                            + (f", {len(ts)} timesteps (#{ts[0]} … #{ts[-1]})" if ts else "")
                            + f". Region coordinates: {r.get('region_text') or r['coords']}."
                            + "\n" + _ready_text(r))
        elif r.get("status") == "needs_session" and r.get("reachable") is False:
            self._status = ((r.get("message") or "The remote host is not reachable.")
                            + ".\nNo password was asked for: it could not reach ssh. "
                            "Check the network/VPN, then press Connect.")
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
    def FillOutputPortInformation(self, port, info):
        # Port 0 is any dataset (made concrete in RequestDataObject); the
        # others are tables, and must say so or VTK refuses them.
        info.Set(vtkDataObject.DATA_TYPE_NAME(),
                 "vtkTable" if port in self.TABLES else "vtkDataSet")
        return 1

    def RequestDataObject(self, request, inInfo, outInfo):
        # The output's kind (grid, curvilinear, points) is the source's: read
        # its metadata first (in the background, unless running synchronously).
        self._need_descriptor()
        for port in self.TABLES:
            info = outInfo.GetInformationObject(port)
            if not isinstance(info.Get(vtkDataObject.DATA_OBJECT()), vtkTable):
                info.Set(vtkDataObject.DATA_OBJECT(), vtkTable())
        return _Sieve.RequestDataObject(self, request, inInfo, outInfo)

    def RequestInformation(self, request, inInfo, outInfo):
        self._need_descriptor()
        self._declare_extent(outInfo)
        info = outInfo.GetInformationObject(0)
        info.Remove(SDDP.TIME_STEPS())
        info.Remove(SDDP.TIME_RANGE())
        ts = self._series_desc().get("timesteps")
        if ts:
            for t in ts:
                info.Append(SDDP.TIME_STEPS(), float(t))
            info.Append(SDDP.TIME_RANGE(), float(ts[0]))
            info.Append(SDDP.TIME_RANGE(), float(ts[-1]))
        return 1

    def RequestData(self, request, inInfo, outInfo):
        self._overview(request, outInfo)
        self._fill_tables(outInfo)
        return 1

    def _overview(self, request, outInfo):
        if not self._uri or self._need_descriptor() or self._desc is None:
            return 1
        if not self._armed:
            return 1                     # metadata only, until the user asks
        desc = self._series_desc()
        if self._ts_problem:
            self._status = f"Timesteps: {self._ts_problem}. Nothing fetched."
            return 1
        # The time asked for, by whichever output asked (the view's time
        # reaches the tables' SpreadSheet too).
        port = request.Get(vtkExecutive.FROM_OUTPUT_PORT()) \
            if request.Has(vtkExecutive.FROM_OUTPUT_PORT()) else 0
        info = outInfo.GetInformationObject(port if 0 <= port < 4 else 0)
        if not info.Has(SDDP.UPDATE_TIME_STEP()):
            info = outInfo.GetInformationObject(0)
        label = None
        if desc.get("timesteps"):
            t = info.Get(SDDP.UPDATE_TIME_STEP()) \
                if info.Has(SDDP.UPDATE_TIME_STEP()) else None
            label = _snap(desc["timesteps"], t)
        self._base = self._params(desc, None)
        if label is not None:
            self._serve_series(label, self._base, desc, "overview", outInfo)
        else:
            self._serve(self._key(self._base), {"descriptor": desc,
                                                "params": self._base},
                        "overview", outInfo)
        if label is not None:
            vtkDataObject.GetData(outInfo).GetInformation().Set(
                vtkDataObject.DATA_TIME_STEP(), float(label))
        return 1

    def _finished(self, job):
        _Sieve._finished(self, job)
        r = job.result or {}
        if job.action != "overview" or r.get("status") != "ok":
            return
        params = (job.request or {}).get("params") or {}
        notes = []
        if self._stride <= 0 and int(params.get("stride") or 1) > 1:
            notes.append(f"Stride {params['stride']} was chosen automatically (Stride "
                         f"0); set Stride to 1 for full resolution.")
        if notes:
            self._status += "\n" + "\n".join(notes)

    # -- the tables -------------------------------------------------------------
    def _fill_tables(self, outInfo):
        desc = self._series_desc() if self._desc else None
        stride = self._stride
        chosen = [self._fields.GetArrayName(i)
                  for i in range(self._fields.GetNumberOfArrays())
                  if self._fields.GetArraySetting(i)]
        ranges = _ranges(self._shown)
        fetched = set()
        if desc and self._base is not None:
            for lab in desc.get("series_timesteps") or desc.get("timesteps") or []:
                if _memo().get(self._key({**self._base, "timestep": lab})) is not None:
                    fetched.add(lab)
        whole = bool(desc) and not desc.get("timesteps") and self._shown is not None
        _table(vtkTable.GetData(outInfo, 1), ("Item", "Value"),
               core.summary_table(self._desc, self._ts_text, stride))
        _table(vtkTable.GetData(outInfo, 2),
               ("Field", "Bytes per value", "Full MB", "At stride MB", "In view",
                "Min", "Max"),
               core.fields_table(desc, chosen, stride, ranges))
        _table(vtkTable.GetData(outInfo, 3),
               ("Index", "Label", "File", "Size MB", "Selected", "Fetched"),
               core.timesteps_table(desc, self._ts_text, whole or fetched))

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
        _tag(data, {**self._series_desc(), "positions_given": self._positions or None})


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
    PANEL = ("SetBoxPosition", "SetBoxLength", "SetUseReferenceBounds", "_region_group", "GetFieldSelection",
             "SetStride", "SetValueFilter", "SetBudgetGB", "SetBudgetMinutes",
             "GetStatus", "GetSpec", "FetchAnyway", "Cancel", "PromoteToSpec")

    def __init__(self):
        _Sieve.__init__(self, 1)
        self._stride = 1
        self._pos = [0.0, 0.0, 0.0]
        self._len = [1.0, 1.0, 1.0]
        self._use_ref = False
        self._ref = None             # the input's bounds (the widget's reference)

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

    # Without this switch in its group, ParaView's box widget reads Position
    # and Scale against the input's bounds (box = Position + Scale * bounds);
    # stated and off, they are the box's corner and size, as the panel says.
    @smproperty.xml('''<IntVectorProperty name="UseReferenceBounds"
        label="Relative To Input" command="SetUseReferenceBounds"
        number_of_elements="1" default_values="0"><BooleanDomain name="bool"/>
        <Documentation>Off: Box Corner and Box Size are the box itself. On: they
        move and scale the input's bounds.</Documentation></IntVectorProperty>''')
    def SetUseReferenceBounds(self, v):
        self._use_ref = bool(v)
        self.Modified()

    # The widget group must come after the properties it names (see PANEL).
    @smproperty.xml('''<PropertyGroup label="Region" panel_widget="InteractiveBox">
        <Property function="Position" name="BoxPosition"/>
        <Property function="Scale" name="BoxLength"/>
        <Property function="UseReferenceBounds" name="UseReferenceBounds"/>
        <Property function="Input" name="Input"/>
        </PropertyGroup>''')
    def _region_group(self):
        pass

    def _box(self):
        if self._use_ref and self._ref is not None:
            # vtkPVBox: each point of the reference box, scaled, then moved.
            a = [self._pos[i] + self._len[i] * self._ref[2 * i] for i in range(3)]
            b = [self._pos[i] + self._len[i] * self._ref[2 * i + 1] for i in range(3)]
        else:
            a = list(self._pos)
            b = [p + l for p, l in zip(self._pos, self._len)]
        return {"lo": [min(x, y) for x, y in zip(a, b)],
                "hi": [max(x, y) for x, y in zip(a, b)]}

    def RequestUpdateExtent(self, request, inInfo, outInfo):
        # This output is fetched, not computed from the input: ask the input
        # for all of itself, never for this output's (different) extent.
        info = inInfo[0].GetInformationObject(0)
        if info.Has(SDDP.WHOLE_EXTENT()):
            info.Set(SDDP.UPDATE_EXTENT(), *info.Get(SDDP.WHOLE_EXTENT()))
        return 1

    def RequestData(self, request, inInfo, outInfo):
        source = vtkDataObject.GetData(inInfo[0])
        desc = _descriptor_of(source)
        if desc is None:
            self._status = ("Put this under a Sieve Source (or another Sieve "
                            "Detail): it needs to know which data the box is on.")
            return 1
        self._desc = desc
        bounds = source.GetBounds() if hasattr(source, "GetBounds") else None
        self._ref = bounds
        box = self._box()
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
            self._serve_series(label, self._base, desc, "detail", outInfo)
        else:
            self._serve(self._key(params), {"descriptor": desc, "params": params},
                        "detail", outInfo)
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


_pick_timesteps = core.pick_timesteps          # kept for the checks


def _selection(desc):
    """The timesteps() part of a request for the selected timesteps: nothing
    for the whole series, a range for a run of it, else the labels."""
    ts = desc.get("timesteps") or []
    full = desc.get("series_timesteps") or ts
    if not ts or ts == full:
        return {}
    at = full.index(ts[0]) if ts[0] in full else -1
    if full[at:at + len(ts)] == ts:
        return {"timesteps": [ts[0], ts[-1]]}
    return {"timestep_labels": list(ts)}


def _ranges(data):
    """{array: (min, max)} of the data on screen (first component)."""
    out = {}
    if data is None:
        return out
    for attrs in (data.GetPointData(), data.GetCellData()):
        for k in range(attrs.GetNumberOfArrays()):
            a = attrs.GetArray(k)
            if a is not None and a.GetName() and a.GetNumberOfTuples():
                out[a.GetName()] = tuple(a.GetRange(0))
    return out


def _table(table, names, rows):
    """Fill a vtkTable: text columns as strings, numbers as numbers."""
    table.Initialize()
    cols = list(zip(*rows)) if rows else [()] * len(names)
    for name, values in zip(names, cols):
        if values and all(isinstance(v, int) and not isinstance(v, bool) for v in values):
            arr = vtkLongLongArray()
        elif values and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                            for v in values):
            arr = vtkDoubleArray()
        else:
            arr = vtkStringArray()
        arr.SetName(name)
        for v in values:
            arr.InsertNextValue(v if not isinstance(arr, vtkStringArray) else str(v))
        table.AddColumn(arr)


def _ready_text(desc):
    """What an overview at the automatic stride would bring, before any of it
    is fetched."""
    s = max(1, int(desc.get("suggested_stride") or 1))
    per = sum((desc.get("itemsizes") or {}).get(v, 0)
              for v in desc.get("variables") or []
              if v not in (desc.get("positions") or []))
    if desc.get("grid"):
        n = 1
        for d in desc["grid"]:
            n *= -(-int(d) // s)
    elif desc.get("particles"):
        n = -(-int(desc["particles"]) // s)
    else:
        n = 0
    size = (f" An overview of every field at stride {s} (the automatic choice) "
            f"would be ~{core._mb(n * per / 1024 ** 2)}"
            + (" per timestep" if desc.get("timesteps") else "") + "."
            if n and per else "")
    return (f"Nothing fetched yet.{size}\n"
            "Choose Fields, Stride or a Value Filter and press Apply, or press "
            "Fetch Overview to take it as it is.")


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
    PANEL = ("SetSpecText", "SetBudgetGB", "SetBudgetMinutes", "GetStatus", "GetSpec",
             "FetchAnyway", "Cancel")

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
