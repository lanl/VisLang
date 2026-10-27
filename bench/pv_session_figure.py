"""A figure of a ParaView session: the overview with each detail in place.

    pvpython bench/pv_session_figure.py <session dir> <out.png>
             [--field NAME] [--timestep N] [--size 1600x1200]

Reads the session's trace (session.jsonl) and draws, from the files the
session itself wrote: the first overview, translucent, and every detail box on
top of it, opaque and outlined — overview first, zoom, details on demand, in
one picture. With --timestep, the overview and details of that timestep.
Rendered offscreen; nothing is fetched.
"""

import argparse
import json
import os
import re

from paraview.simple import (ColorBy, GetActiveViewOrCreate, GetDisplayProperties,  # noqa
                             OpenDataFile, Outline, ResetCamera, SaveScreenshot, Show)


def _source_of(spec_path):
    """The source a request's spec read (its source(...) argument)."""
    try:
        with open(spec_path) as f:
            m = re.search(r"source\((['\"])(.+?)\1", f.read())
        return m.group(2) if m else None
    except OSError:
        return None


def session_files(session_dir, timestep=None, source=None):
    """The first overview (of `source`, if given) and every detail drawn on
    the same source."""
    overview, details, uri = None, [], source
    with open(os.path.join(session_dir, "session.jsonl")) as f:
        recs = [json.loads(ln) for ln in f if ln.strip()]
    for rec in recs:
        if rec.get("status") != "ok" or not rec.get("spec_path"):
            continue
        src = _source_of(rec["spec_path"])
        for path in rec.get("files") or []:
            if timestep is not None and "timestep#" in path \
                    and f"timestep#{timestep}." not in path:
                continue
            if rec.get("action") == "overview" and overview is None \
                    and (uri is None or src == uri):
                overview, uri = path, src
            elif rec.get("action") == "detail" and src == uri and uri is not None \
                    and path not in details:
                details.append(path)
    return overview, details


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("session")
    ap.add_argument("out")
    ap.add_argument("--field")
    ap.add_argument("--timestep", type=int)
    ap.add_argument("--source", help="draw this source's overview (default: the first)")
    ap.add_argument("--size", default="1600x1200")
    a = ap.parse_args()
    overview, details = session_files(a.session, a.timestep, a.source)
    if overview is None:
        raise SystemExit("the session has no overview to draw")
    view = GetActiveViewOrCreate("RenderView")
    view.OrientationAxesVisibility = 1
    view.DepthPeeling = 0          # translucency without peeling renders offscreen too
    base = OpenDataFile(overview)
    disp = Show(base, view)
    disp.SetRepresentationType("Surface")
    disp.Opacity = 0.25
    field = a.field or (base.PointData.keys() or [None])[0]
    if field:
        ColorBy(disp, ("POINTS", field))
    for path in details:
        src = OpenDataFile(path)
        d = Show(src, view)
        d.SetRepresentationType("Surface")
        if field and field in src.PointData.keys():
            ColorBy(d, ("POINTS", field))
        box = Show(Outline(Input=src), view)
        box.AmbientColor = box.DiffuseColor = [1.0, 0.55, 0.0]
        box.LineWidth = 2.0
    view.CameraPosition = [1, -1.6, 1.2]
    view.CameraViewUp = [0, 0, 1]
    ResetCamera(view)
    w, h = (int(v) for v in a.size.lower().split("x"))
    SaveScreenshot(a.out, view, ImageResolution=[w, h])
    print(f"wrote {a.out}: overview + {len(details)} detail(s)")


if __name__ == "__main__":
    main()
