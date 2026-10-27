"""Checks run INSIDE ParaView's pvpython (driven by tests/test_paraview_plugin.py).

    pvpython tests/paraview/pv_plugin_checks.py <plugin.py> <data dir> <session root>

Loads the Sieve plugin and drives it as a script would: an overview and a box
for each kind of data, the time slider over a folder, a request held over
budget then approved, a spec typed into Sieve Spec, and the background path —
nothing waited for inside the pipeline update, the result picked up by calling
GetNeedsUpdate() the way ParaView's live-source timer does. Prints "ok  …" per
check and exits non-zero on the first failure.
"""

import glob
import json
import os
import sys
import time

PLUGIN, DATA, SESSIONS = sys.argv[1:4]
os.environ["SIEVE_PV_SYNC"] = "1"
os.environ["SIEVE_PV_SESSION_ROOT"] = SESSIONS

from paraview import servermanager                       # noqa: E402
from paraview.simple import *                            # noqa: E402,F401,F403
from vtkmodules.numpy_interface import dataset_adapter as dsa  # noqa: E402

LoadPlugin(PLUGIN, ns=globals())
N = 0


def check(name, cond, detail=""):
    global N
    if not cond:
        print(f"FAIL {name}: {detail}")
        sys.exit(1)
    N += 1
    print(f"  ok  {name}")


def status(p):
    p.UpdatePropertyInformation()
    return p.GetPropertyValue("Status")


def spec(p):
    p.UpdatePropertyInformation()
    return p.GetPropertyValue("Spec")


def spec_path(p):
    lines = [json.loads(ln) for f in glob.glob(os.path.join(SESSIONS, "*",
                                                            "session.jsonl"))
             for ln in open(f)]
    return [ln for ln in lines if ln.get("spec_path")][-1]["spec_path"]


def bounds(p, t=None):
    p.UpdatePipeline(t) if t is not None else p.UpdatePipeline()
    return [round(v, 6) for v in p.GetDataInformation().GetBounds()]


def fetch(p):
    return dsa.WrapDataObject(servermanager.Fetch(p))


def steps():
    return sorted(glob.glob(os.path.join(SESSIONS, "*", "[0-9][0-9][0-9]-*")))


def path(name):
    return os.path.join(DATA, name)


def overview(**kw):
    """A Sieve Source as a user brings it up: Apply (metadata only), then
    Fetch Overview."""
    src = SieveSource(**kw)
    src.UpdatePipeline()
    src.SMProxy.InvokeCommand("FetchOverview")
    src.UpdatePipeline()
    return src


VIEW = CreateView("RenderView")
VIEW.ViewSize = [200, 150]


def drawn(p):
    """Points the view actually draws for p, after the timer's next tick (the
    pass that states the new data's extent)."""
    if p.GetClientSideObject().GetNeedsUpdate():
        p.SMProxy.MarkModified(p.SMProxy)
        p.UpdatePipeline()
    rep = Show(p, VIEW)
    Render(VIEW)
    n = rep.GetRepresentedDataInformation().GetNumberOfPoints()
    Hide(p, VIEW)
    return n


print("== the first Apply reads metadata only ==")
m = SieveSource(Source=path("plain.h5"))
m.UpdatePipeline()
check("Apply on a new source fetches nothing, and says what an overview would cost",
      m.GetDataInformation().GetNumberOfPoints() == 0
      and not glob.glob(os.path.join(SESSIONS, "*", "*-source-overview"))
      and "Nothing fetched yet" in status(m) and "would be ~0.1 MB" in status(m), status(m))
m.Stride = 4                         # the user changes a setting and Applies
m.UpdatePipeline()
check("the next Apply that changes a setting fetches",
      bounds(m) == [0.0, 28.0, 0.0, 28.0, 0.0, 28.0], status(m))
Delete(m)

print("== a uniform grid with geometry: world coordinates ==")
s = overview(Source=path("cube.vti"), Stride=2)
check("overview spans the source (every 2nd sample)", bounds(s) == [100.0, 131.0, -4.0, 88.0, 2.0, 32.0],
      bounds(s))
d = SieveDetail(Input=s)
d.BoxCorner, d.BoxSize = [110.2, 10.0, 0.0], [9.6, 20.0, 99.0]
check("the detail is the box, at full resolution",
      bounds(d) == [110.5, 119.5, 10.0, 30.0, 2.0, 33.0], bounds(d))
check("its spec names world coordinates",
      "coords='world'" in spec(d) and "subsample" not in spec(d), spec(d))
d2 = SieveDetail(Input=s)
check("an untouched box asks for nothing", "Drag the box" in status(d2) or
      bounds(d2) == [1.0, -1.0, 1.0, -1.0, 1.0, -1.0], status(d2))
Delete(d2)

print("== a grid with no geometry: cells, placed in the source's numbering ==")
h = overview(Source=path("plain.h5"), Stride=4)
check("overview over the whole grid (spacing = stride)",
      bounds(h) == [0.0, 28.0, 0.0, 28.0, 0.0, 28.0], bounds(h))
hd = SieveDetail(Input=h)
hd.BoxCorner, hd.BoxSize = [10.0, 4.0, 0.0], [9.0, 7.0, 40.0]
check("the detail lands inside the overview, in the same cells",
      bounds(hd) == [10.0, 19.0, 4.0, 11.0, 0.0, 31.0], bounds(hd))
check("its spec names cells, whole", "x=(10, 20), y=(4, 12), coords='cells'" in spec(hd),
      spec(hd))
rho = fetch(hd).PointData["rho"]
check("the values are the source's cells 10–19",
      float(rho[0]) == float(10 * 32 * 32 + 4 * 32), float(rho[0]))
hdd = SieveDetail(Input=hd)
hdd.BoxCorner, hdd.BoxSize = [12.0, 5.0, 3.0], [2.0, 2.0, 2.0]
check("a detail of a detail keeps the numbering",
      bounds(hdd) == [12.0, 14.0, 5.0, 7.0, 3.0, 5.0], bounds(hdd))
check("the view draws the overview and the details (their extents are stated)",
      all(drawn(x) > 0 for x in (h, hd, hdd)), [drawn(x) for x in (h, hd, hdd)])

print("== points ==")
p = overview(Source=path("pts.npz"))
check("points arrive as points", p.GetDataInformation().GetDataSetTypeAsString()
      .endswith("PolyData") or bounds(p)[1] > 9, bounds(p))
pd = SieveDetail(Input=p)
pd.BoxCorner, pd.BoxSize = [2.0, 2.0, 2.0], [3.0, 3.0, 3.0]
b = bounds(pd)
check("the box keeps only the points inside it",
      b[0] >= 2.0 and b[1] <= 5.0 and b[2] >= 2.0 and b[5] <= 5.0
      and pd.GetDataInformation().GetNumberOfPoints() > 0, b)

print("== a curvilinear grid ==")
c = overview(Source=path("fire.vts"))
cb = bounds(c)
cd = SieveDetail(Input=c)
cd.BoxCorner, cd.BoxSize = [-6.0, cb[2], cb[4]], [6.0, cb[3] - cb[2], 4.0]
check("a curvilinear detail is a structured grid around the box",
      cd.GetDataInformation().GetDataSetTypeAsString().endswith("StructuredGrid")
      or bounds(cd)[0] >= -6.0 - 2.0, (bounds(cd), spec(cd)))

print("== time ==")
t = overview(Source=path("series"), Stride=2)
t.UpdatePipelineInformation()
check("the folder's timesteps reach the time slider",
      list(t.TimestepValues) == [3.0, 4.0, 5.0], list(t.TimestepValues))
bounds(t, 4.0)
v4 = float(fetch(t).PointData["rho"][0])
bounds(t, 5.0)
v5 = float(fetch(t).PointData["rho"][0])
check("each time shows its own timestep", (v4, v5) == (4000.0, 5000.0), (v4, v5))
n = len(steps())
bounds(t, 4.0)
check("a revisited timestep comes back without a new request",
      len(steps()) == n and float(fetch(t).PointData["rho"][0]) == 4000.0
      and "from this session" in status(t), (len(steps()), n, status(t)))
td = SieveDetail(Input=t)
td.BoxCorner, td.BoxSize = [0.0, 0.0, 0.0], [7.0, 7.0, 7.0]
bounds(td, 5.0)
check("a detail follows the time", float(fetch(td).PointData["rho"][0]) == 5000.0)

print("== held over budget, then approved ==")
g = overview(Source=path("plain.h5"), Stride=1, BudgetGB=1e-9)
g.UpdatePipeline()
check("held: nothing shown, the panel says why",
      g.GetDataInformation().GetNumberOfPoints() == 0 and "Over the" in status(g),
      status(g))
g.SMProxy.InvokeCommand("FetchAnyway")
g.UpdatePipeline()
check("Fetch Anyway runs it", g.GetDataInformation().GetNumberOfPoints() == 32 ** 3,
      status(g))

print("== Sieve Spec ==")
out = os.path.join(SESSIONS, "typed", "boxes.vti")
sp = SieveSpec(SpecText=f"src = source({path('plain.h5')!r})\n"
                        f"save(region(src, center={{'a': (4, 4, 4), 'b': (20, 20, 20)}}, "
                        f"size=4), {out!r})\n")
sp.UpdatePipeline()
check("a typed spec runs and its first file is shown",
      sp.GetDataInformation().GetNumberOfPoints() == 64
      and os.path.exists(os.path.join(SESSIONS, "typed", "boxes", "b.vti")), status(sp))

print("== promote to spec ==")
hd.SMProxy.InvokeCommand("PromoteToSpec")
promoted = [p for p in GetSources().values() if p.GetXMLName() == "SieveSpec"
            and p is not sp]
check("Promote To Spec makes a Sieve Spec holding the detail's spec",
      len(promoted) == 1 and "x=(10, 20), y=(4, 12), coords='cells'" in promoted[0].SpecText
      and "save(view, 'out.vti')" in promoted[0].SpecText, promoted[0].SpecText
      if promoted else None)
pr = promoted[0]
pr.UpdatePipeline()
check("the promoted spec reproduces the detail, in its own folder",
      [round(v, 6) for v in pr.GetDataInformation().GetBounds()] == bounds(hd)
      and "spec" in os.path.basename(os.path.dirname(spec_path(pr))), status(pr))

print("== in the background, as the GUI runs it ==")
os.environ["SIEVE_PV_SYNC"] = "0"
a = SieveSource(Source=path("cube.vti"), Stride=2)
a.UpdatePipeline()
algo = a.GetClientSideObject()
t0 = time.time()
while time.time() - t0 < 60 and algo._desc is None:
    if algo.GetNeedsUpdate():
        a.SMProxy.MarkModified(a.SMProxy)
        a.UpdatePipeline()
    time.sleep(0.1)
check("in the background too, the first Apply stops at the metadata",
      a.GetDataInformation().GetNumberOfPoints() == 0 and "Nothing fetched" in status(a),
      status(a))
a.Stride = 4                         # Apply with a changed setting
a.UpdatePipeline()
t0 = time.time()
while time.time() - t0 < 60:
    if algo.GetNeedsUpdate():
        # What pqLiveSourceItem does when GetNeedsUpdate says yes: mark the
        # proxy modified, then the view updates the pipeline.
        a.SMProxy.MarkModified(a.SMProxy)
        a.UpdatePipeline()
        if a.GetDataInformation().GetNumberOfPoints() > 0:
            break
    time.sleep(0.1)
check("the result arrives through GetNeedsUpdate, without blocking the update",
      a.GetDataInformation().GetNumberOfPoints() == 16 * 12 * 8, status(a))
os.environ["SIEVE_PV_SYNC"] = "1"

print("== prefetching a series ==")
pf = overview(Source=path("series"), Stride=4)
bounds(pf, 3.0)
pf.SMProxy.InvokeCommand("Prefetch")
pf.UpdatePipeline(3.0)
n = len(steps())
vals = []
for t in (3.0, 4.0, 5.0):
    bounds(pf, t)
    vals.append(float(fetch(pf).PointData["rho"][0]))
check("after a prefetch every timestep shows without a new request",
      len(steps()) == n and vals == [3000.0, 4000.0, 5000.0], (len(steps()), n, vals))
pd2 = SieveDetail(Input=pf)
pd2.BoxCorner, pd2.BoxSize = [4.0, 4.0, 4.0], [4.0, 4.0, 4.0]
bounds(pd2, 3.0)
pd2.SMProxy.InvokeCommand("Prefetch")
pd2.UpdatePipeline(3.0)
n = len(steps())
vals = [float((bounds(pd2, t), fetch(pd2).PointData["rho"][0])[1]) for t in (4.0, 5.0)]
check("a detail prefetches its box over the series (the region across time)",
      len(steps()) == n and vals == [4000.0, 5000.0], (len(steps()), n, vals))

FAKE = os.environ.get("SIEVE_FAKE")
if FAKE:
    print("== a remote source (simulated host) ==")
    import SieveParaView as plugin
    plugin._runner().sieve_cmd = FAKE
    r = overview(Source=f"u@fakehost:{path('plain.h5')}", Stride=4)
    check("a remote overview, read next to the data",
          bounds(r) == [0.0, 28.0, 0.0, 28.0, 0.0, 28.0], status(r))
    rd = SieveDetail(Input=r)
    rd.BoxCorner, rd.BoxSize = [10.0, 4.0, 0.0], [9.0, 7.0, 40.0]
    check("a remote detail lands where the local one did",
          bounds(rd) == [10.0, 19.0, 4.0, 11.0, 0.0, 31.0], status(rd))
    plugin._runner().base_env = {"FAKE_NO_ALLOCATION": "1", "VISLANG_SRUN_JOBID": "auto"}
    ra = SieveDetail(Input=r)
    ra.BoxCorner, ra.BoxSize = [20.0, 20.0, 20.0], [5.0, 5.0, 5.0]
    ra.UpdatePipeline()
    check("no allocation: held, and the panel shows the salloc to ask for",
          ra.GetDataInformation().GetNumberOfPoints() == 0
          and "salloc" in status(ra) and "shared HPC time" in status(ra), status(ra))
    plugin._runner().base_env = {"FAKE_UNREACHABLE": "1"}
    un = SieveSource(Source=f"u@fakehost:{path('cube.vti')}")
    un.UpdatePipeline()
    n = len(glob.glob(os.path.join(SESSIONS, "*", "*-inspect")))
    for _ in range(3):              # what ParaView's timer and re-renders do
        un.SMProxy.MarkModified(un.SMProxy)
        un.UpdatePipeline()
    again = len(glob.glob(os.path.join(SESSIONS, "*", "*-inspect")))
    check("an unreachable host: says so, asks for no password, and does not retry",
          "not reachable" in status(un) and "No password" in status(un) and again == n,
          (status(un), n, again))
    plugin._runner().base_env = {}
    lines = [json.loads(ln) for f in glob.glob(os.path.join(SESSIONS, "*",
                                                            "session.jsonl"))
             for ln in open(f)]
    check("the trace says the remote cuts ran next to the data",
          any(ln.get("route") == "remote_reduce" for ln in lines))

print("== the session trace ==")
lines = [json.loads(ln) for f in glob.glob(os.path.join(SESSIONS, "*", "session.jsonl"))
         for ln in open(f)]
actions = {ln.get("action") for ln in lines}
check("every request is in the trace", {"inspect", "overview", "detail", "spec"} <= actions
      and any(ln.get("memo_hit") for ln in lines)
      and any("budget" in (ln.get("holds") or []) for ln in lines), actions)
check("each finished request names its spec and run",
      all(ln.get("spec_path") and ln.get("run_id") for ln in lines
          if ln.get("status") == "ok" and not ln.get("memo_hit")
          and ln.get("action") != "inspect"))

print(f"\nall {N} checks passed")
