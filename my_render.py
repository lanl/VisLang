"""Headless volume renderer + viewer server for VisLang.

3-D fields are rendered as k3d volumes: k3d ships the array to the browser and
ray-marches it in WebGL, so NOTHING renders server-side. This is the only thing
that works on the compute nodes, which have no usable OpenGL ("bad X server
connection") — the trame/vtk.js render_server shows a blank screen there.

The render is a self-contained k3d snapshot (data embedded) served by a
persistent background HTTP server, so render() returns immediately and the
browser (via an SSH tunnel to the node) shows the latest view. Open the printed
URL; re-rendering replaces the page in place.

Particle (non-3-D) data is rendered as a k3d point cloud + density volume by
render_points(); render() routes 3-D fields to render_volume() and everything
else to render_points(). Both share the one HTTP snapshot server below.
"""

import os
import threading
import http.server
import socketserver

import numpy as np

HOST = os.environ.get("VISLANG_RENDER_HOST", "127.0.0.1")
PORT = int(os.environ.get("VISLANG_RENDER_PORT", "9123"))

# Above this per-axis size, k3d ships a large array to the browser
# (256³ float32 ≈ 67 MB per field) — warn so the spec can stride it down.
_VOL_RENDER_WARN_RES = 256

# Per-axis bin count for the particle density histogram (a render-derived volume,
# not the source data) — internal, not a user knob.
_DENSITY_GRID = 128

_CMAP_NAMES = ['viridis', 'plasma', 'inferno', 'magma', 'hot', 'cividis',
               'Blues', 'Reds', 'Greens', 'Purples', 'YlOrBr', 'BuGn', 'RdPu']

# Sigmoid-shaped opacity: empty space transparent, dense core opaque.
_OPACITY_FN = np.array([0.0, 0.0, 0.2, 0.0, 0.5, 0.1, 0.8, 0.5, 1.0, 0.9],
                       dtype=np.float32)


# ---------------------------------------------------------------------------
# Colormaps (flat [t, r, g, b, ...] with 256 stops, as k3d wants)
# ---------------------------------------------------------------------------
def _k3d_colormap(name):
    """Convert a matplotlib colormap to k3d's flat [t,r,g,b,...] format."""
    import matplotlib
    cmap = matplotlib.colormaps[name]
    t = np.linspace(0, 1, 256, dtype=np.float32)
    rgba = cmap(t).astype(np.float32)
    out = np.empty(256 * 4, dtype=np.float32)
    out[0::4] = t
    out[1::4] = rgba[:, 0]
    out[2::4] = rgba[:, 1]
    out[3::4] = rgba[:, 2]
    return out


def green_colormap():
    """Glowing-green ramp: dark green (diffuse gas) -> bright green (dense
    core), warming toward white-green at the top so the densest core glows."""
    t = np.linspace(0, 1, 256, dtype=np.float32)
    r = (0.7 * np.clip((t - 0.55) / 0.45, 0, 1)).astype(np.float32)
    g = (0.10 + 0.90 * t).astype(np.float32)
    b = (0.5 * np.clip((t - 0.70) / 0.30, 0, 1)).astype(np.float32)
    out = np.empty(256 * 4, dtype=np.float32)
    out[0::4] = t
    out[1::4] = r
    out[2::4] = g
    out[3::4] = b
    return out


def _resolve_cmap(cmap):
    """A k3d colormap from: None (caller's default), the name 'green' (our
    custom ramp), a matplotlib name, or an already-built flat array."""
    if cmap is None:
        return None
    if isinstance(cmap, str):
        return green_colormap() if cmap == 'green' else _k3d_colormap(cmap)
    return np.asarray(cmap, dtype=np.float32)  # pre-built [t,r,g,b,...]


# ---------------------------------------------------------------------------
# Persistent viewer HTTP server (serves the latest k3d snapshot)
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_state = {"started": False, "html": b"<!doctype html><title>VisLang</title>"
                                    b"<body>No render yet.</body>"}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        with _lock:
            body = _state["html"]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # quiet


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def _ensure_server():
    """Boot the viewer server once, on a background daemon thread. Binds BEFORE
    marking started, so a failed bind doesn't wedge us into thinking it's up."""
    with _lock:
        if _state["started"]:
            return
    try:
        httpd = _Server((HOST, PORT), _Handler)
    except OSError as e:
        raise RuntimeError(
            f"Cannot bind the viewer server on {HOST}:{PORT}: {e}. "
            f"Is another viewer still holding the port?") from e
    with _lock:
        _state["started"] = True
    threading.Thread(target=httpd.serve_forever, daemon=True,
                     name="vislang-k3d-http").start()


def _serve(html_bytes):
    """Publish a new snapshot and make sure the server is running."""
    with _lock:
        _state["html"] = html_bytes
    _ensure_server()
    return f"http://{HOST}:{PORT}/"


def url():
    return f"http://{HOST}:{PORT}/"


# ---------------------------------------------------------------------------
# The render entry point used by the MCP render() verb
# ---------------------------------------------------------------------------
def render_volume(dataset_info, cmap=None, opacity=None):
    """Render every 3-D field in a loaded DatasetInfo as a k3d volume and serve
    it. Returns the viewer URL.

    cmap:    None -> cycle the default colormaps (one per field); 'green' ->
             the custom green ramp; any matplotlib name; or a pre-built k3d
             colormap array. Applied to all fields when given explicitly.
    opacity: override the transfer function (k3d flat [t,a,...]); default
             _OPACITY_FN.
    """
    if not dataset_info.loaded or not dataset_info.data:
        raise ValueError("Data not loaded — call load() before render().")

    data = dataset_info.data
    vol_vars = [k for k, v in data.items() if getattr(v, 'ndim', 0) == 3]
    if not vol_vars:
        raise ValueError(
            "No 3-D field found to render as a volume. "
            f"Loaded variables: {list(data.keys())}")

    import k3d

    total_mb = sum(data[v].astype(np.float32, copy=False).nbytes
                   for v in vol_vars) / 1024**2
    if any(max(data[v].shape) > _VOL_RENDER_WARN_RES for v in vol_vars):
        print(f"[render] WARNING: large grid(s) — ~{total_mb:.0f} MB will be "
              f"sent to the browser. Stride it down in the spec, e.g. "
              f"load(info, dimensions={{'grid': 128}}).")

    opacity_fn = _OPACITY_FN if opacity is None else np.asarray(opacity, np.float32)
    chosen = _resolve_cmap(cmap)

    plot = k3d.plot(height=900)
    for i, var in enumerate(vol_vars):
        arr = np.ascontiguousarray(data[var].astype(np.float32))
        # log-scale so high-dynamic-range fields aren't washed out
        arr = np.log10(np.abs(arr) + 1.0)
        arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
        color_map = chosen if chosen is not None \
            else _k3d_colormap(_CMAP_NAMES[i % len(_CMAP_NAMES)])
        plot += k3d.volume(
            arr,
            color_map=color_map,
            color_range=[float(arr.min()), float(arr.max())],
            opacity_function=opacity_fn,
            name=var.split('/')[-1],
        )

    html = plot.get_snapshot().encode("utf-8")
    return _serve(html)


# ---------------------------------------------------------------------------
# Particle / point data: subsampled point cloud + density volume
# ---------------------------------------------------------------------------
def render_points(dataset_info, cmap=None, opacity=None):
    """Render particle/point data as a k3d point cloud plus a density volume.

    Renders every loaded point — thin upstream with
    subset(info, dimensions={'particles': ...}) so the cloud stays responsive.
    Coordinate variables come from dataset_info.positions (resolved by inspect);
    override at inspect with inspect(path, positions=('x','y','z')).
    """
    if not dataset_info.loaded or not dataset_info.data:
        raise ValueError("Data not loaded — call load() before render().")

    data = dataset_info.data
    positions = dataset_info.positions
    if positions is None:
        raise ValueError(
            f"No coordinate variables resolved for this dataset (loaded: "
            f"{list(data.keys())}). Set them at inspect: "
            f"inspect(path, positions=('x','y','z')).")
    missing = [v for v in positions if v not in data]
    if missing:
        raise ValueError(
            f"Coordinate variables {missing} aren't loaded — keep them in the "
            f"subset (loaded: {list(data.keys())}).")
    x_var, y_var, z_var = positions

    import k3d

    pts = np.ascontiguousarray(
        np.column_stack([data[x_var], data[y_var], data[z_var]]).astype(np.float32))
    scalar_vars = [k for k in data if k not in (x_var, y_var, z_var)]

    opacity_fn = _OPACITY_FN if opacity is None else np.asarray(opacity, np.float32)
    bounds = [[float(pts[:, a].min()), float(pts[:, a].max())] for a in range(3)]
    extent = max(b[1] - b[0] for b in bounds) or 1.0

    plot = k3d.plot(height=900)

    # density volume from a 3-D histogram of the points (log-scaled). k3d indexes
    # volumes [z, y, x], so transpose the histogram (which is [x, y, z]).
    hist, _ = np.histogramdd(pts, bins=_DENSITY_GRID, range=bounds)
    dens = np.ascontiguousarray(
        np.transpose(np.log10(hist.astype(np.float32) + 1.0), (2, 1, 0)))
    plot += k3d.volume(
        dens,
        color_map=_resolve_cmap(cmap) if cmap is not None else _k3d_colormap('viridis'),
        color_range=[float(dens.min()), float(dens.max())],
        opacity_function=opacity_fn,
        bounds=[bounds[0][0], bounds[0][1], bounds[1][0], bounds[1][1],
                bounds[2][0], bounds[2][1]],
        name="density",
    )

    # point cloud (every loaded point — thin via subset upstream): ONE LAYER PER
    # scalar, since a k3d point object carries a single `attribute` and so can
    # only be colored by one variable. Every loaded field therefore gets its own
    # layer over the same positions, named "particles: <var>", and the viewer's
    # object panel switches between them. Only the first starts visible —
    # identical clouds stacked at identical coordinates would just hide each
    # other, and the panel is how you compare them.
    #
    # Each layer holds its own copy of `pts` (12 bytes/point), so N fields cost
    # N x the cloud. That multiplier is the reason to subsample hard upstream:
    # `mass` sorts first when present because it is the usual thing to look at.
    order = (["mass"] if "mass" in scalar_vars else []) + \
            [v for v in scalar_vars if v != "mass"]
    if order:
        for i, var in enumerate(order):
            attr = np.ascontiguousarray(np.asarray(data[var]).astype(np.float32))
            lo, hi = float(np.nanmin(attr)), float(np.nanmax(attr))
            if lo == hi:
                hi = lo + 1.0        # a constant field has no range to map onto
            layer = k3d.points(positions=pts, point_size=extent / 300.0,
                               shader="flat", attribute=attr,
                               color_map=_k3d_colormap('plasma'),
                               color_range=[lo, hi], name=f"particles: {var}")
            layer.visible = (i == 0)
            plot += layer
    else:
        plot += k3d.points(positions=pts, point_size=extent / 300.0, shader="flat",
                           color=0xffffff, name="particles")

    html = plot.get_snapshot().encode("utf-8")
    return _serve(html)


# ---------------------------------------------------------------------------
# The render() DSL verb — routes by data shape, serves to the browser
# ---------------------------------------------------------------------------
def render(dataset_info, cmap=None, opacity=None):
    """Serve the loaded dataset to the browser viewer (k3d/WebGL, headless).

    3-D fields render as volume(s); particle/point data renders as a point cloud
    + density volume. Both are served by the persistent HTTP snapshot server;
    open the printed URL (forward the port if remote). Re-rendering replaces the
    page in place.

    cmap:      'green', any matplotlib name, or None (default colormaps).
    opacity:   k3d flat opacity transfer function [t, a, ...]; default sigmoid.

    Renders everything the info describes. To show only part of a dataset, narrow
    and load it first: render(load(subset(info, variables=[...], dimensions={...}))).
    Particle coordinates come from info.positions (resolved by inspect); override
    with inspect(path, positions=('x','y','z')).
    """
    if not dataset_info.loaded or not dataset_info.data:
        raise ValueError("Data not loaded — call load() before render().")

    data = dataset_info.data
    if any(getattr(v, 'ndim', 0) == 3 for v in data.values()):
        view_url = render_volume(dataset_info, cmap=cmap, opacity=opacity)
        print(f"[render] served k3d volume -> {view_url}")
    else:
        view_url = render_points(dataset_info, cmap=cmap, opacity=opacity)
        print(f"[render] served k3d point cloud -> {view_url}")
    return dataset_info
