"""MCP server for SIVA - declarative VTK visualization via conversation."""

import argparse
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional
import numpy as np
from mcp.server.fastmcp import FastMCP, Image

from .renderer import (
    Renderer, RenderMode, claim_main_thread, run_event_loop, stop_event_loop)
from . import queries

# Module-level logger (no file handler until main() runs — avoids side effects on import)
logger = logging.getLogger("siva")


def _parse_args():
    parser = argparse.ArgumentParser(description="SIVA MCP server")
    parser.add_argument(
        "--offscreen",
        action="store_true",
        help="Use off-screen rendering (no interactive window)",
    )
    parser.add_argument(
        "--headless-interactive",
        action="store_true",
        help="Offscreen rendering with interactive-mode threading (for testing)",
    )
    parser.add_argument(
        "--trame",
        action="store_true",
        help="Serve each view as an interactive browser view via trame "
        "(server-side VTK rendering streamed over websocket). Everything "
        "is served on one port: views at /?ui=<name>, the view index at "
        "/views. Requires the 'trame' extra: uv sync --extra trame "
        "(or pip install 'siva[trame]').",
    )
    parser.add_argument(
        "--trame-port",
        type=int,
        default=0,
        metavar="N",
        help="The single port everything in --trame mode is served on "
        "(default 0 = auto-pick a free port). Pin it when only a fixed "
        "port can be reached (e.g. forwarded from a container); one "
        "forwarded port covers every view and the index page.",
    )
    parser.add_argument(
        "--trame-host",
        default="127.0.0.1",
        metavar="ADDR",
        help="Interface the trame port binds to (default 127.0.0.1, "
        "loopback only). Use 0.0.0.0 when publishing the port from a "
        "container (docker run -p): published ports arrive on the "
        "container's external interface, which a loopback-bound server "
        "won't accept. Reported URLs still say localhost -- correct from "
        "the host once the port is forwarded.",
    )
    parser.add_argument(
        "--workdir",
        default=None,
        metavar="DIR",
        help="Change to this directory at startup before initializing. "
        "Relative paths resolve from the launch directory. The server's "
        "scratch output (.siva/, view-*.py, screenshots) is written here.",
    )
    args, remaining = parser.parse_known_args()
    # Put unconsumed args back so FastMCP can use them
    sys.argv = [sys.argv[0]] + remaining
    return args


def _apply_workdir(workdir):
    """Change into ``workdir`` if given, erroring clearly if it's missing.

    Must run before anything resolves a path relative to cwd (logging, the
    hot-reload watcher, view-*.py and screenshot output).
    """
    if workdir is None:
        return
    if not os.path.isdir(workdir):
        sys.exit(f"--workdir: directory does not exist: {workdir}")
    os.chdir(workdir)


# _args and _renderer are None until main() initialises them.
# All tool functions access these as module globals and are only called
# after main() has run (either via the MCP server or tests that set them
# directly), so lazy initialisation is safe.
_args = None

# ---------------------------------------------------------------------------
# Tool name lists — the single source of truth for both the MCP instructions
# string and scripts/gen_docs.py (which imports these).
# ---------------------------------------------------------------------------

QUERY_TOOLS = [
    "describe_data",
    "query_stats",
    "get_histogram",
    "get_spatial_extent",
    "sample_points",
    "profile",
    "get_ground_z",
    "suggest_isosurface",
    "get_camera",
]

MUTATION_TOOLS = [
    "load",
    "wait_for_pipeline",
    "set_suggested_camera",
    "set_camera",
    "set_window_size",
]

META_TOOLS = [
    "screenshot",
    "camera_orbit",
    "list_versions",
    "restore_version",
    "get_dsl_overview",
    "list_data_files",
    "get_dsl_reference",
    "new_view",
    "focus",
    "close_view",
    "list_views",
    "view_url",
    "pipeline_status",
]

_ALL_TOOLS = QUERY_TOOLS + MUTATION_TOOLS + META_TOOLS

# Initialize
mcp = FastMCP(
    "SIVA",
    instructions=f"""SIVA: Declarative VTK scientific visualization via conversation.

PIPELINE FILE FORMAT:
Every pipeline file (view-<name>.py) must begin with the line
`from siva.spec_api import *` as its first statement — an optional module
docstring may precede it, but nothing else — it makes the SIVA DSL forms
(source, filter, show, threshold, contour, slice, ...) available. Leave
a blank line after it, then write the pipeline. load() and new_view() already
write this header for you; when you author or edit a file yourself, keep it as
the first statement (after any module docstring) or the build fails with a
SyntaxError. For example:

    from siva.spec_api import *

    data = source("vtkXMLImageDataReader", FileName="volume.vti")
    show(data, color_by="temperature")

WORKFLOW:
1. Call get_dsl_overview() to see the complete DSL toolkit — workflow patterns,
   all available forms with descriptions, VTK classes, and colormaps
2. Call list_data_files() to see what's available, then load("file.vts") to load it
3. load() auto-detects the reader, writes view-main.py with a source() call,
   and returns describe_data() output immediately. In --trame mode, the
   first call to load(), new_view(), or wait_for_pipeline() to return
   successfully also appends a live-view URL to its output (covers both a
   fresh session and resuming one where the pipeline file already existed,
   so load() was skipped) — relay that URL to the human immediately in your
   reply, without waiting to be asked. It's only surfaced once per server
   process; the human keeps that one browser tab open afterward (it updates
   automatically as views are added).
4. Add show() calls to view-main.py — saving the file triggers a build
   automatically; call wait_for_pipeline() when you want to block on the result
5. State-changing tools (wait_for_pipeline, set_camera, etc.)
   automatically return a screenshot — no separate screenshot() call needed
6. The first wait_for_pipeline() call automatically sets an overview camera — no
   action needed. Call set_suggested_camera() only to reset or switch style
   ("overview", "top_down", "side"). The human's camera adjustments are
   preserved across subsequent wait_for_pipeline() calls. The human user may
   adjust the camera at any time in the live window — don't reset or
   overwrite the camera in response to an unexpected view angle.
7. When the human asks you to look at, react to, or comment on the current
   view ("what do you think?", "look at that", "see the X?"), call
   screenshot() first unless a state-changing tool already returned an image
   this turn. The human may have moved the camera, resized the window, or
   otherwise changed what they're seeing since your last image.
8. Edit the pipeline file to add layers incrementally
9. Batch read-only tool calls (describe_data, get_histogram, suggest_isosurface,
   get_dsl_reference, etc.) in a single turn to save round trips

HOT RELOAD:
Edit anywhere — only the changed node and its descendants rebuild; node
hashes survive across edits. The server watches each `view-<name>.py` and
rebuilds on save (debounced); you don't need to call anything to kick a
build. Call `wait_for_pipeline()` to block until the current file's build is
done and get a screenshot. Each node is content-hashed by
`(kind, params, parent hashes)`, so ancestors and untouched siblings are
reused from cache. Visual-only edits (colormap, opacity, scalar_range,
camera) are ~free, as are pure whitespace/comment edits; adding a filter
at the tail reuses all prefix nodes; mid-pipeline edits rebuild only
downstream nodes; changing the data file is a full rebuild. `pipeline_status()` is a
non-blocking peek — prefer it during tight edit loops where you don't need
a screenshot every step.

ARTIFACTS:
The .siva/ folder in the session directory contains full-resolution PNG
screenshots and pipeline history. Use these when writing reports:
  .siva/latest_<view>.png   — most recent full-res PNG for each view
  .siva/history/            — versioned pipeline.py and screenshot.png per version
  view-<name>.py               — the current pipeline source for each view

Do NOT try to build a complex multi-layer pipeline in one shot. It will
likely fail due to wrong value ranges, bad seed positions, or field name
typos, and debugging is harder.

MULTIPLE VIEWS:
To show different aspects of the data side by side (e.g. temperature vs
oxygen, overview vs closeup), write view-<name>.py then call
new_view("name") to create the view and execute the pipeline in one step.
Each view gets its own window, pipeline, and camera. The human user
interacts with all view windows directly — focus("name") is only for
switching which view MCP tools (wait_for_pipeline, set_camera, etc.) target.
The human does not need to call focus() to look at or interact with a view.

SERVER STATE:
All views and loaded data exist only in the running server process. If the
MCP server is restarted, all state is lost. To recreate views after a
restart: call load() for the data, then wait_for_pipeline() and new_view() for
each view — the pipeline files (view-main.py, view-<name>.py) are still on
disk and just need to be re-executed.

CRITICAL RULES:
- Always query field ranges with describe_data(node=, field=) BEFORE choosing isosurface
  values, threshold ranges, or scalar_range for coloring
- Use get_ground_z() to find valid z-coordinates for seed placement in
  structured grids (terrain-following or curvilinear)
- Call get_dsl_overview() to see working pipeline patterns you can copy
- Before using any DSL form in a pipeline file, call get_dsl_reference(form="form-name")
  to confirm its exact parameters. The overview lists forms but not their
  signatures — don't guess arguments from the name. Batch multiple
  get_dsl_reference() calls in one turn.

VOLUME RENDERING:
- Use representation="Volume" in show() for volumetric rendering
- Use gradient_opacity=True for edge-enhanced volume rendering
- Threshold data first to focus on regions of interest

TROUBLESHOOTING:
- Empty output (0 points): check field ranges with describe_data(node=, field=), use suggest_isosurface()
- Wrong colors: check scalar_range, or just use color_by="fieldname" for auto defaults
- To color by one component of a vector: use component=0/1/2 or "x"/"y"/"z" in show()
- Volume looks empty: opacity too low, use an opacity_function preset like "fire" or set opacity_function control points manually
- Volume too opaque: lower opacity parameter or adjust opacity_function control points
- Streamlines empty: seeds outside data, use get_ground_z() to find valid Z coordinates
- Slow pipeline: reduce volume_resolution, threshold before volume render
- Camera too far/close: use set_suggested_camera("overview") or set_camera(position=[x,y,z])

Call list_data_files() to see available datasets.

DSL forms (source, filter, show, threshold, contour, etc.) are used in pipeline .py files
run by wait_for_pipeline(). Use get_dsl_reference(form="form-name") for detailed DSL docs.

Available tools: {", ".join(_ALL_TOOLS)}""",
)


# Wrap mcp.tool() so every tool is automatically logged (name, args, ok/fail/return).
_original_mcp_tool = mcp.tool


def _summarize(value, limit=200):
    """Produce a short repr of a return value for logging."""
    if isinstance(value, Image):
        return "<Image>"
    r = repr(value)
    if len(r) > limit:
        return r[:limit] + f"... ({len(r)} chars)"
    return r


def _logging_tool_decorator(*args, **kwargs):
    original_decorator = _original_mcp_tool(*args, **kwargs)
    def wrapper(fn):
        import functools
        @functools.wraps(fn)
        def logged(*a, **kw):
            logger.info("tool call: %s(%s)", fn.__name__,
                        ", ".join(
                            [repr(v) for v in a] +
                            [f"{k}={v!r}" for k, v in kw.items()]))
            try:
                result = fn(*a, **kw)
                logger.info("tool done: %s -> %s", fn.__name__, _summarize(result))
                return result
            except Exception:
                logger.exception("tool failed: %s", fn.__name__)
                raise
        return original_decorator(logged)
    return wrapper

mcp.tool = _logging_tool_decorator




# ---------------------------------------------------------------------------
# Per-view state
# ---------------------------------------------------------------------------

class ViewContext:
    """Bundles all per-view state: pipeline objects, version history, and renderer."""

    def __init__(self, name: str, renderer):
        self.name = name
        self.renderer = renderer
        self.vtk_objects: dict = {}
        self.current_code: str = ""
        self.version: int = 0
        self.versions: list = []
        # Hash of the content currently reflected by the renderer. Set after
        # a successful build's apply phase. None until the first build.
        self.applied_hash: Optional[str] = None
        from siva.build_cache import BuildCache
        self.cache: BuildCache = BuildCache()
        from siva.hot_reload import BuildCoordinator, PipelineWatcher
        self.coordinator: BuildCoordinator = BuildCoordinator(self, renderer)
        self.watcher: PipelineWatcher = PipelineWatcher(
            self.coordinator,
            self.pipeline_file,
        )
        # Hot reload is started explicitly via start_hot_reload() rather than
        # from __init__ so that test contexts can opt out.

    @property
    def history_dir(self) -> Path:
        """Per-view history directory under .siva/history/<view_name>/."""
        return Path(f".siva/history/{self.name}")

    @property
    def pipeline_file(self) -> str:
        """Per-view pipeline file name, e.g. 'view-main.py', 'view-closeup.py'."""
        return f"view-{self.name}.py"

    def start_hot_reload(self):
        """Start the file watcher. Called after the view is fully registered."""
        self.watcher.start()

    def save_version(self, code: str, screenshot_path: Optional[str]) -> int:
        """Save a version snapshot. Returns the version number.

        Versioning is per-view state; this method lives here rather than at
        module scope so it's clear that version counter and history_dir are
        owned by the view.
        """
        self.version += 1
        ver_dir = self.history_dir / f"v{self.version:04d}"
        ver_dir.mkdir(parents=True, exist_ok=True)
        (ver_dir / "pipeline.py").write_text(code)
        if screenshot_path:
            import shutil
            png_path = (
                screenshot_path[:-4] + ".png"
                if screenshot_path.endswith(".jpg")
                else screenshot_path
            )
            if os.path.exists(png_path):
                shutil.copy2(png_path, ver_dir / "screenshot.png")
        return self.version

    def shutdown(self):
        """Stop the hot-reload watcher and build coordinator."""
        try:
            self.watcher.stop()
        except Exception:
            pass
        try:
            self.coordinator.shutdown()
        except Exception:
            pass


# Global view registry — populated by main() or _init_for_test().
_views: dict = {}       # name -> ViewContext
_current_view: str = "main"

# Rendering backend selection, set by main(). Determines which renderer class
# _make_renderer() constructs for the main view and each new_view().
_render_mode: RenderMode = RenderMode.INTERACTIVE
_trame_port: int = 0
_trame_host: str = "127.0.0.1"

# The index-page URLs (--trame mode only): a small object with .url and
# .proxy_url pointing at /views on the shared trame app's single port. None
# in other modes and before main() has started the first view.
_view_index = None

# Whether the live-view URL banner (see _first_time_url_banner()) has been
# surfaced to the agent yet this process. Reset to False by main().
_url_reported = False


def _primary_url(url, proxy_url):
    """Return ``(primary, fallback)`` for reporting a live-view URL.

    With ``VSCODE_PROXY_URI`` set (code-server / Coder), the human's browser
    is remote: the proxied URL is the one that works for them, so it leads
    every report — agents tend to relay the first URL they see, and the bare
    localhost URL would be useless from a remote browser. The localhost URL
    becomes the fallback, usable only from the machine running SIVA. Without
    a proxy, the localhost URL is primary and there is no fallback.
    """
    if proxy_url:
        return proxy_url, url
    return url, None


def _first_time_url_banner() -> str:
    """Return a one-time "open this URL" banner to append to a tool result.

    Empty string in non-trame modes, before the view index exists, or after
    the first call that got a non-empty banner. Wired into load() and
    new_view() -- the earliest points a human has something worth opening a
    browser for -- so the agent sees the URL without the human having to
    ask, and is told (via the banner text itself) to relay it immediately.
    """
    global _url_reported
    if _render_mode != RenderMode.TRAME or _url_reported or _view_index is None:
        return ""
    _url_reported = True
    primary, fallback = _primary_url(_view_index.url, _view_index.proxy_url)
    lines = [
        "",
        "",
        f"Live view (share this URL with the human now): {primary}",
    ]
    if fallback:
        lines.append(
            f"(Only when browsing from this machine itself, use {fallback} instead.)"
        )
    lines.append(
        "It lists every view with a link and thumbnail, and updates "
        "automatically as views are added — the human only needs to open "
        "it once."
    )
    return "\n".join(lines)


def _make_renderer(view_name: str):
    """Construct a renderer for *view_name* per the selected backend.

    In TRAME mode returns a TrameRenderer (registered as a template on the
    one shared trame server -- every view shares that server's single
    port, addressed by /?ui=<name>); otherwise a desktop/offscreen Renderer
    in the active RenderMode.
    """
    if _render_mode == RenderMode.TRAME:
        from .trame_backend import TrameRenderer
        return TrameRenderer(view_name=view_name)
    return Renderer(mode=_render_mode, view_name=view_name)


# The single injectable seam for constructing view renderers. Both the main
# view (in main()) and every new_view() build their renderer by calling this
# hook, so a test can install a factory that returns fakes for *every* view --
# there is no other construction site that bypasses it. Defaults to the real
# _make_renderer; _init_for_test() overrides it (see there for why this matters
# on macOS). Signature: (view_name: str) -> renderer.
_renderer_factory = _make_renderer


def _view_index_snapshot():
    """Build a fresh list of ``ViewInfo`` from the live view registry.

    Called from the shared trame app's aiohttp handlers (on the shared
    event-loop thread), potentially concurrently with MCP tool threads
    mutating ``_views``. ``dict(_views)`` takes an atomic shallow copy
    (safe under the GIL); each ``ViewContext`` and its renderer are
    read-only from here.
    """
    from .view_index import ViewInfo
    current = _current_view
    views = []
    for name, ctx in dict(_views).items():
        renderer = ctx.renderer
        views.append(ViewInfo(
            name=name,
            url=getattr(renderer, "url", None),
            proxy_url=getattr(renderer, "proxy_url", None),
            current=(name == current),
            thumbnail_path=f".siva/latest_{name}.png",
        ))
    return views


def _current_ctx() -> "ViewContext":
    """Return the ViewContext for the currently active view."""
    if _views:
        return _views[_current_view]
    raise RuntimeError(
        "No view context initialised. "
        "Call _init_for_test() in tests or start the server via main()."
    )


def _init_for_test(renderer=None, factory=None) -> "ViewContext":
    """Initialise a minimal view context for use in tests.

    Creates a 'main' ViewContext backed by *renderer* (or a lightweight
    no-op stub when None is passed) and registers it as the active view.
    Returns the ViewContext so tests can inspect or mutate state via
    ``ctx.vtk_objects``, ``ctx.renderer``, etc.

    Also installs *factory* (signature ``(view_name) -> renderer``) as
    the module-level ``_renderer_factory`` so that any subsequent ``new_view()``
    builds its renderer through the fake path too, never constructing a real
    ``siva.renderer.Renderer``. This matters on macOS: VTK's Cocoa backend
    creates an NSWindow even in OFFSCREEN mode and requires that (and every
    render) to run on the process's real main thread, so a real Renderer built
    for a new view and rendered on a hot-reload thread segfaults. Tests must
    therefore supply fakes for *every* view, not just "main". When *factory* is
    None a default is installed that returns fresh no-op stubs.

    Usage::

        ctx = srv._init_for_test()
        ctx.renderer = my_fake_renderer   # swap renderer after the fact
        ctx.vtk_objects = {"data": reader}
        # ... call srv.tool_function() ...

        # Multi-view: supply a factory so new_view() gets fakes too.
        srv._init_for_test(_FakeRenderer("main"),
                           factory=lambda name: _FakeRenderer(name))
    """
    global _views, _current_view, _renderer_factory

    class _NoOpRenderer:
        """Minimal renderer stub — does nothing, never touches a display."""
        mode = RenderMode.OFFSCREEN  # satisfies new_view()'s cur_renderer.mode access
        camera_positioned = False

        def render(self):
            pass

        def dispatch(self, fn):
            return fn()

        def screenshot(self, path):
            return path

        def clear(self):
            pass

        def get_camera_state(self):
            return {"position": [0, 0, 1], "focal_point": [0, 0, 0], "up": [0, 1, 0]}

        def set_camera(self, **kwargs):
            pass

        def destroy(self):
            pass

    _renderer_factory = factory if factory is not None else (
        lambda view_name: _NoOpRenderer())
    ctx = ViewContext("main", renderer if renderer is not None else _NoOpRenderer())
    _views = {"main": ctx}
    _current_view = "main"
    return ctx


def _get_data(node_name=""):
    """Get VTK data output for a named node, or root source."""
    vtk_objects = _current_ctx().vtk_objects
    if node_name and node_name in vtk_objects:
        obj = vtk_objects[node_name]
        obj.Update()
        return obj.GetOutput()
    if node_name and node_name not in vtk_objects:
        return None  # caller handles this
    # Return the first source's output if no name given
    for name, obj in vtk_objects.items():
        obj.Update()
        return obj.GetOutput()
    return None


def _available_nodes_hint():
    vtk_objects = _current_ctx().vtk_objects
    if vtk_objects:
        return f"Available nodes: {sorted(vtk_objects.keys())}"
    return "No pipeline is active. Call wait_for_pipeline() first to load data."


def _get_data_or_error(node: str = ""):
    """Look up VTK data for *node*, returning (data, None) or (None, error_str).

    Centralises the repeated lookup pattern used by query tools::

        data, err = _get_data_or_error(node)
        if err:
            return err
        # ... use data ...
    """
    data = _get_data(node)
    if data is None:
        if node:
            return None, f"Node '{node}' not found. {_available_nodes_hint()}"
        return None, _available_nodes_hint()
    return data, None


def _load_file_directly(file_path: str):
    """Load a VTK file directly, returning (data, error_message).

    Delegates to filters.load_file which detects the reader from the extension.
    Returns (vtk_data_object, None) on success, (None, error_str) on failure.
    """
    from .filters import load_file
    return load_file(file_path)


def _auto_screenshot():
    """Capture and return an Image of the current scene.

    Uses dispatch to ensure correctness in both offscreen and
    interactive modes.
    """
    renderer = _current_ctx().renderer
    view_name = _current_ctx().name
    screenshot_path = f".siva/latest_{view_name}.png"
    def _take():
        renderer.render()
        return renderer.screenshot(screenshot_path)
    path = renderer.dispatch(_take)
    return Image(path=path)


def _with_screenshot(text_result) -> list[str | Image]:
    """Combine a text result with an auto-screenshot image.

    Always returns a list: [text, image].
    """
    img = _auto_screenshot()
    return [text_result, img]


@mcp.tool()
def load(filename: str) -> str:
    """Load a VTK data file and make it available for visualization.

    Auto-detects the appropriate reader from the file extension.
    Writes view-main.py (or the active view's pipeline file) with a source()
    call for the loaded file — ready for you to add show() calls and run
    wait_for_pipeline(). Returns a describe_data() overview of the loaded dataset.

    If the pipeline file already exists, load() will not overwrite it. Delete
    or rename it first, then call load() again.

    Supported extensions: .vts, .vti, .vtp, .vtu, .vtr, .vtk, .nrrd, .nhdr
    For .raw binary files, use raw_source() in a pipeline instead.

    Args:
        filename: Path to the VTK file to load (relative to the session directory).
    """
    if not os.path.exists(filename):
        return f"File not found: {filename}\n\nUse list_data_files() to see available files."

    from .filters import EXT_TO_READER, create_vtk_filter

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    reader_class = EXT_TO_READER.get(ext)
    if reader_class is None:
        supported = sorted(EXT_TO_READER.keys())
        return (
            f"Cannot load '{filename}': unsupported extension '.{ext}'. "
            f"Supported extensions: {supported}"
        )

    try:
        reader, _ = create_vtk_filter(reader_class, FileName=filename)
        reader.Update()
        data = reader.GetOutput()
    except Exception as e:
        logger.exception("load() failed for %s", filename)
        return f"Error loading '{filename}': {e}"

    if data is None or data.GetNumberOfPoints() == 0:
        return f"File '{filename}' loaded but contains no points. The file may be empty or corrupt."

    ctx = _current_ctx()
    ctx.vtk_objects = {"data": reader}

    if os.path.exists(ctx.pipeline_file):
        return (
            f"'{ctx.pipeline_file}' already exists. To load a new file, delete or rename it first.\n\n"
            + describe_data(node="data")
            + _first_time_url_banner()
        )

    pipeline_code = (
        "from siva.spec_api import *\n\n"
        f'data = source("{reader_class}", FileName="{filename}")\n'
    )
    with open(ctx.pipeline_file, "w") as f:
        f.write(pipeline_code)

    return describe_data(node="data") + _first_time_url_banner()


@mcp.tool(structured_output=False)
def wait_for_pipeline(verbose: bool = False) -> list[str | Image]:
    """Wait for the current view's pipeline file to finish building, and return
    the build report plus a screenshot.

    The server watches `view-<name>.py` and starts a rebuild automatically
    every time the file is saved (debounced ~100ms). You do not need to call
    `wait_for_pipeline()` to "kick" a build — saving the file is enough. Call
    `wait_for_pipeline()` when you want to **block until the build of the current
    file content is done** and see the result. If a matching build has already
    finished, this returns immediately (no rebuild).

    The pipeline file is plain Python. It must begin with the line
    `from siva.spec_api import *` as its first statement (an optional module
    docstring may precede it) — that header makes the SIVA DSL forms
    available; without it the build fails with a SyntaxError.
    Available forms include:
      source(), filter(), threshold(), contour(), stream_tracer(),
      tube(), glyph(), show(), camera(), background(), and more.
    Call get_dsl_reference(form="form-name") for detailed docs on any form.
    Call get_dsl_overview() for the full list of available DSL forms.

    Builds are incremental, keyed on a content hash of each DSL node:
      - Visual-only edits (colormap, opacity, scalar_range, camera) — ~free,
        all cache hits, ~1ms.
      - Mid-pipeline edits (a threshold range, a contour value) — only that
        node and its downstream rebuild; ~10-50ms typical.
      - Changing the data file or source() arguments — full rebuild.

    Args:
        verbose: If True, return the full per-node listing (arrays, point/cell
            counts, camera state).  If False (default), return a short terse
            summary: version, node count, what changed since the previous build,
            cache stats, and timing.  Errors and warnings always appear in full
            regardless of this flag.

    Example workflow::

        # 1. Write a pipeline file
        # view-main.py:
        #   from siva.spec_api import *
        #
        #   data = source("vtkXMLStructuredGridReader", FileName="mydata.vts")
        #   region = threshold(input=data, ThresholdBy="temperature",
        #                      ThresholdRange=[500, 2000])
        #   show(region, "fire", color_by="temperature",
        #        scalar_range=(500, 2000), lut="fire",
        #        scalar_bar="Temperature (K)")
        #   # background defaults to "dark"; call background("white") etc. to change

        # 2. Save the file (watcher triggers a build automatically)
        # 3. Call wait_for_pipeline() to block on the result and get the screenshot
        wait_for_pipeline()

        # For the full node listing after a first build:
        wait_for_pipeline(verbose=True)

    Notes:
        - Every successful build saves a versioned snapshot to .siva/history/.
          Use list_versions() / restore_version() to navigate history.
        - Use pipeline_status() for a non-blocking peek (no screenshot, no wait)
          while iterating on the file.
        - Empty output warnings usually mean wrong field ranges — use
          describe_data(node=, field=) to check.
        - Each node status dict follows the unified schema defined in
          siva/diagnostics.py: every dict has "status" (ok/error/skipped/warning),
          "class", and for non-ok statuses also "kind" and "message".
    """
    ctx = _current_ctx()
    file = ctx.pipeline_file
    if not os.path.exists(file):
        return [f"File not found: {file}\n\nWrite your pipeline code to this file first (begin it with `from siva.spec_api import *`), then call wait_for_pipeline()."]

    record = ctx.coordinator.wait_for_current(timeout=120)
    if record is None:
        return [f"File not found: {file}\n\nWrite your pipeline code to this file first (begin it with `from siva.spec_api import *`), then call wait_for_pipeline()."]

    if record.status == "running":
        return ["Pipeline build timed out after 120s. Check pipeline_status() for details."]

    result_text = record.format(verbose=verbose) + _first_time_url_banner()

    screenshot_path = record.screenshot_path
    if record.status == "ok" and screenshot_path and os.path.exists(screenshot_path):
        return [result_text, Image(path=screenshot_path)]
    return [result_text]


@mcp.tool()
def pipeline_status(verbose: bool = False) -> str:
    """Non-blocking peek at the current view's latest build status.

    Returns the same human-readable report that `wait_for_pipeline()` produces, but
    without waiting for any in-flight build and without attaching a screenshot.
    If a build is currently running or pending, that's noted on a leading line
    above the latest finished build's report.

    Use this when iterating on a pipeline file: save the file, then call
    `pipeline_status()` to confirm the new hash built cleanly without paying
    the screenshot+roundtrip cost; call `wait_for_pipeline()` when you want the
    screenshot back.

    Args:
        verbose: If True, return the full per-node listing (same as
            wait_for_pipeline(verbose=True)). If False (default), return the terse
            summary.
    """
    ctx = _current_ctx()
    coordinator = ctx.coordinator

    with coordinator._cv:
        inflight = coordinator._inflight
        pending = coordinator._pending
        latest = coordinator._latest

    lines: list[str] = []
    if inflight is not None and inflight.status == "running":
        elapsed = time.monotonic() - inflight.started_at
        lines.append(
            f"Build in flight: {elapsed:.1f}s elapsed, hash {inflight.source_hash[:8]}."
        )
    if pending is not None and pending.status == "running":
        lines.append(f"Build pending: hash {pending.source_hash[:8]}.")

    if latest is None:
        lines.append("No build has completed yet.")
    else:
        lines.append(latest.format(verbose=verbose))

    return "\n".join(lines)


@mcp.tool()
def screenshot() -> Image:
    """Render the current scene and return the image.

    Call this after wait_for_pipeline to see the current visualization.
    """
    ctx = _current_ctx()
    renderer = ctx.renderer
    screenshot_path = f".siva/latest_{ctx.name}.png"
    def _take():
        renderer.render()
        return renderer.screenshot(screenshot_path)
    path = renderer.dispatch(_take)
    return Image(path=path)


@mcp.tool()
def camera_orbit(n_frames: int = 8, elevation: float = 30.0) -> list:
    """Orbit the camera around the scene and return a series of screenshots.

    Captures views evenly spaced around the focal point at the given elevation
    angle, giving a turntable-style tour of the 3D scene.  Useful for
    understanding spatial structure that is hard to read from a single angle.

    The original camera state is restored after all frames are captured.

    Args:
        n_frames: Number of views to capture (default 8, clamped to 1–16).
        elevation: Camera elevation angle in degrees above the focal plane
                   (default 30.0, clamped to -89–89).

    Returns:
        A flat list alternating text descriptions and Image objects:
        [description_0, Image_0, description_1, Image_1, ...]
    """
    import math

    # Clamp parameters to sensible ranges
    n_frames = max(1, min(16, int(n_frames)))
    elevation = max(-89.0, min(89.0, float(elevation)))

    ctx = _current_ctx()
    renderer = ctx.renderer

    def _orbit():
        # Save original camera state so we can restore it
        original = renderer.get_camera_state()

        focal = original["focal_point"]
        cam_pos = original["position"]

        # Compute camera distance from focal point
        dx = cam_pos[0] - focal[0]
        dy = cam_pos[1] - focal[1]
        dz = cam_pos[2] - focal[2]
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)
        if distance < 1e-9:
            # Camera is at the focal point — fall back to a default distance
            distance = 1.0

        elev_rad = math.radians(elevation)
        cos_e = math.cos(elev_rad)
        sin_e = math.sin(elev_rad)

        results = []
        for i in range(n_frames):
            azimuth = 2.0 * math.pi * i / n_frames
            px = focal[0] + distance * math.cos(azimuth) * cos_e
            py = focal[1] + distance * math.sin(azimuth) * cos_e
            pz = focal[2] + distance * sin_e

            renderer.set_camera(
                position=[px, py, pz],
                focal_point=focal,
                up=[0.0, 0.0, 1.0],
            )
            renderer.render()

            frame_png = f".siva/orbit_{ctx.name}_frame{i:02d}.png"
            frame_path = renderer.screenshot(frame_png)

            az_deg = round(math.degrees(azimuth), 1)
            desc = (
                f"Frame {i + 1}/{n_frames} — "
                f"azimuth {az_deg}°, elevation {elevation}°"
            )
            results.append(desc)
            results.append(Image(path=frame_path))

        # Restore original camera
        renderer.set_camera(
            position=original["position"],
            focal_point=original["focal_point"],
            up=original["up"],
        )
        renderer.render()

        return results

    return renderer.dispatch(_orbit)


@mcp.tool()
def describe_data(node: str = "", file_path: str = "", field: str = "") -> str:
    """Get an overview of a dataset or a single field's statistics.

    Without field= : returns full overview — dimensions, bounds, all fields with
    percentiles (p1, p25, p50, p75, p99), distribution shape, and coordinate info.
    Note: load() already returns this — no need to call describe_data() on the root
    data after load(). Use describe_data() on derived nodes (after threshold, contour,
    etc.) to understand what the filter produced.

    With field= : returns rich statistics for that one field only (percentiles,
    distribution shape). Use this after filtering or transforming data to understand
    a specific field before choosing thresholds, isosurface values, or color ranges.

    Can be called in three ways:
    - describe_data() -- uses the active pipeline's first node
    - describe_data(node="nodename") -- uses a named node in the active pipeline
    - describe_data(file_path="myfile.vts") -- reads the file directly, no pipeline needed

    When file_path is given it takes precedence over node and the active pipeline.
    Supported file extensions: .vts, .vti, .vtp, .vtu, .vtr

    Examples:
        describe_data()                          -- full overview of root data
        describe_data(node="fire_threshold")     -- full overview of a filtered node
        describe_data(node="fire", field="theta") -- just theta stats on the fire node
    """
    source_label = node or "data"

    if file_path:
        data, error = _load_file_directly(file_path)
        if error:
            return error
        source_label = file_path
    else:
        data, err = _get_data_or_error(node)
        if err:
            return err

    # Single-field mode
    if field:
        field_stats = queries.get_rich_field_stats(data, field=field)
        if not field_stats:
            pd = data.GetPointData()
            cd = data.GetCellData()
            available = (
                [pd.GetArrayName(i) for i in range(pd.GetNumberOfArrays())] +
                [cd.GetArrayName(i) for i in range(cd.GetNumberOfArrays())]
            )
            return f"Field '{field}' not found. Available: {available}"
        return f"=== Field: {field} (node: {source_label}) ===\n" + queries.format_rich_field_stats(field_stats, data=data)

    lines = ["=== Dataset Overview ==="]
    lines.append(f"  Points: {data.GetNumberOfPoints():,}")
    lines.append(f"  Cells: {data.GetNumberOfCells():,}")
    lines.append(f"  Type: {data.GetClassName()}")

    dims = None
    if hasattr(data, "GetDimensions"):
        dims = [0, 0, 0]
        data.GetDimensions(dims)
        lines.append(f"  Dimensions: {dims[0]} x {dims[1]} x {dims[2]}")

    bounds = data.GetBounds()
    lines.append(
        f"  Bounds: X=[{bounds[0]:.1f}, {bounds[1]:.1f}] (range {bounds[1]-bounds[0]:.1f}), "
        f"Y=[{bounds[2]:.1f}, {bounds[3]:.1f}] (range {bounds[3]-bounds[2]:.1f}), "
        f"Z=[{bounds[4]:.1f}, {bounds[5]:.1f}] (range {bounds[5]-bounds[4]:.1f})"
    )

    # Spacing info for structured data
    if dims is not None:
        spacing_parts = []
        for axis, label, d in [(0, "X", dims[0]), (1, "Y", dims[1]), (2, "Z", dims[2])]:
            extent = bounds[2 * axis + 1] - bounds[2 * axis]
            if d > 1:
                avg_spacing = extent / (d - 1)
                spacing_parts.append(f"{label}~{avg_spacing:.2g}")
        if spacing_parts:
            lines.append(f"  Avg spacing: {', '.join(spacing_parts)}")

    # Structured grid extent and terrain-following detection
    if data.GetClassName() in ("vtkStructuredGrid", "vtkImageData", "vtkRectilinearGrid"):
        i0, i1, j0, j1, k0, k1 = data.GetExtent()
        lines.append(f"  Extent: i=[{i0}, {i1}], j=[{j0}, {j1}], k=[{k0}, {k1}]"
                     + (" (VOI indices for extract_grid)" if any(x != 0 for x in [i0, j0, k0]) else ""))
        if any(x != 0 for x in [i0, j0, k0]):
            lines.append("  WARNING: Extent does not start at (0,0,0). extract_grid VOI uses")
            lines.append(f"  absolute indices — use i=[{i0}..{i1}], j=[{j0}..{j1}], k=[{k0}..{k1}].")

    if data.GetClassName() == "vtkStructuredGrid":
        if dims is None:
            dims = [0, 0, 0]
            data.GetDimensions(dims)
        nx, ny, nz = dims
        if nz > 1:
            ground_zs = [data.GetPoint(iy * nx + ix)[2]
                         for iy in range(0, ny, max(1, ny // 20))
                         for ix in range(0, nx, max(1, nx // 20))]
            gz_std = np.std(ground_zs)
            if gz_std > 1.0:
                lines.append("")
                lines.append("=== Grid Structure ===")
                lines.append(f"  Terrain-following grid detected (ground z std={gz_std:.1f}).")
                lines.append(f"  Ground z ranges from {min(ground_zs):.1f} to {max(ground_zs):.1f}.")
                lines.append(f"  Use extract_grid(VOI=[{i0},{i1},{j0},{j1},{k0},{k0}]) for the ground surface.")
                lines.append(f"  Do NOT use extract_region with z=bounds_min for ground extraction.")

    # Rich field statistics
    lines.append("")
    lines.append("=== Fields (with percentiles and distribution shape) ===")
    field_stats = queries.get_rich_field_stats(data)
    lines.append(queries.format_rich_field_stats(field_stats, data=data))

    # Volume rendering readiness
    data_type = data.GetClassName()
    if data_type in ("vtkImageData", "vtkUniformGrid"):
        lines.append("")
        lines.append("=== Volume Rendering ===")
        lines.append("  Data is vtkImageData - volume rendering works directly (no resampling).")
        lines.append('  Quick: show(node, "vol", representation="Volume", opacity_function="ct_bone")')
    elif data_type in ("vtkStructuredGrid", "vtkRectilinearGrid"):
        lines.append("")
        lines.append("=== Volume Rendering ===")
        lines.append("  Data will be auto-resampled for volume rendering (set volume_resolution).")
        lines.append("  Tip: threshold first to reduce data, then volume render the subset.")

    lines.append("")
    lines.append("=== Quick Start ===")
    lines.append("  Use suggest_isosurface(node, field) for contour values")
    lines.append("  For sparse fields (feature is small fraction of domain): call get_histogram()")
    lines.append("    first — if >60% of mass is in first/last bins, threshold() to the feature")
    lines.append("    region before calling suggest_isosurface.")

    return "\n".join(lines)


@mcp.tool()
def query_stats(node: str, field: str, condition: str) -> str:
    """Compute statistics for a field filtered by a condition on another field.

    Answers questions like:
      - "mean updraft velocity where theta > 400"
      - "min/max oxygen where fuel_density > 0.1"
      - "volume (count) where temperature >= 500"

    The *condition* string must be in the form "<field> <op> <value>" where
    op is one of: >, <, >=, <=, ==, !=

    Examples:
        query_stats("", "w", "theta > 400")
        query_stats("thresh1", "O2", "fuel_density >= 0.1")
        query_stats("", "temperature", "temperature != 0")

    Returns count of matching points plus mean, min, max, std, and percentiles
    (p1, p25, p50, p75, p99) of the target field within the matching region.

    Args:
        node: Pipeline node to query (empty string for root source).
        field: Scalar field to compute statistics on.
        condition: Condition string like "theta > 400" (field op value).
    """
    import re

    data, err = _get_data_or_error(node)
    if err:
        return err

    # Parse the condition string: "<field> <op> <value>"
    # Operators ordered longest-first so ">=" is matched before ">"
    pattern = r"^\s*(.+?)\s*(>=|<=|!=|==|>|<)\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*$"
    m = re.match(pattern, condition)
    if not m:
        return (
            f"Could not parse condition '{condition}'. "
            "Expected format: '<field> <op> <value>' where op is >, <, >=, <=, ==, or !=. "
            "Example: 'theta > 400'"
        )

    cond_field = m.group(1).strip()
    cond_op = m.group(2)
    try:
        cond_value = float(m.group(3))
    except ValueError:
        return f"Could not parse numeric value from condition '{condition}'."

    return queries.query_stats(data, field, cond_field, cond_op, cond_value)


@mcp.tool()
def get_histogram(node: str, field: str, bins: int = 20) -> str:
    """Get a text histogram of a field's value distribution.

    Useful for understanding data distribution before choosing visualization parameters.
    """
    data, err = _get_data_or_error(node)
    if err:
        return err
    return queries.get_histogram(data, field, bins)


@mcp.tool()
def get_spatial_extent(node: str, field: str, min_value: float, max_value: float) -> str:
    """Find the bounding box where a field is within a given range.

    Useful for positioning seed points for streamlines, focusing cameras,
    or understanding where features are located in 3D space.
    """
    data, err = _get_data_or_error(node)
    if err:
        return err
    return queries.get_spatial_extent(data, field, min_value, max_value)


@mcp.tool()
def sample_points(
    node: str,
    points: list[list[float]],
    fields: list[str] = None,
) -> str:
    """Sample field values at multiple (x, y, z) locations in one call.

    Probes all requested points efficiently using a single spatial index,
    avoiding the round-trip cost of calling sample_point N times.

    Returns a structured text report: one block per input point showing
    the nearest grid point, field values (scalar or vector), and whether
    the query coordinate was outside the dataset bounds.

    Args:
        node: Pipeline node to sample from (empty string for root source).
        points: List of [x, y, z] coordinates to probe.
        fields: Optional list of field names to return. If omitted, all
                point-data fields are returned.

    Example:
        sample_points("", [[0,0,0],[1,1,1]], fields=["temperature","density"])
    """
    data, err = _get_data_or_error(node)
    if err:
        return err

    if not points:
        return "No points provided."

    for i, pt in enumerate(points):
        if len(pt) != 3:
            return f"Point {i} must be [x, y, z] (3 values), got {len(pt)}."

    results = queries.sample_points(data, [tuple(p) for p in points], fields or None)
    return queries.format_sample_points(results)


@mcp.tool()
def profile(
    node: str,
    point1: list[float],
    point2: list[float],
    fields: list[str],
    resolution: int = 100,
) -> str:
    """Extract a 1-D profile of field values along a line between two points.

    Samples the dataset at evenly spaced points along the line from point1
    to point2 using a probe filter. Returns a table of values with distance
    along the line, plus summary statistics (min, max, mean, trend) for each field.

    Great for extracting profiles like "temperature vs. height through the plume
    center" or "density along a horizontal transect."

    Args:
        node: Name of the pipeline node to sample from (empty string for root source).
        point1: Start point [x, y, z].
        point2: End point [x, y, z].
        fields: List of field names to extract (e.g. ["temperature", "density"]).
        resolution: Number of sample points along the line (default 100).
    """
    data, err = _get_data_or_error(node)
    if err:
        return err

    if len(point1) != 3 or len(point2) != 3:
        return "point1 and point2 must each be [x, y, z] (3 values)."

    probe_output = queries.sample_line(data, tuple(point1), tuple(point2), resolution)
    return queries.get_line_probe_data(probe_output, fields)


@mcp.tool()
def get_ground_z(node: str, x: float, y: float, layers: bool = True) -> str:
    """Return the Z coordinate at (x, y) for the lowest layer of a structured grid.

    Useful for any 3D structured grid where the Z coordinate of the bottom
    layer varies with position — for example terrain-following grids or
    curvilinear meshes. Use this before placing seed points for streamlines
    to ensure they are inside the grid.

    The response always leads with "Ground z = X.X" so the value is easy to
    extract. When layers=True (the default) the z-values at the first 10
    vertical layers are also included. Pass layers=False when you only need
    the ground z value.

    Returns an error message if the data is not a structured grid.
    """
    data, err = _get_data_or_error(node)
    if err:
        return err
    return queries.get_ground_z(data, x, y, layers=layers)


@mcp.tool()
def suggest_isosurface(node: str, field: str, num_values: int = 3) -> str:
    """Suggest good isosurface values for a field.

    Analyzes the field histogram to find transition points that produce
    meaningful isosurfaces. Returns values you can use in Isosurfaces=[].

    Note:
        For sparse fields where the feature of interest is a small fraction of the
        domain (e.g. a fire plume, a hot-spot, a jet), suggestions on the full dataset
        will be dominated by background gradients and give poor results.  Use
        get_histogram() first: if >60% of histogram mass is in the first or last few
        bins, threshold() to the feature region, then call suggest_isosurface on that
        thresholded node instead.
    """
    data, err = _get_data_or_error(node)
    if err:
        return err
    return queries.suggest_isosurface(data, field, num_values)


@mcp.tool(structured_output=False)
def set_suggested_camera(style: str = "overview") -> list[str | Image]:
    """Apply an automatic camera position based on visible actors and return a screenshot.

    The first wait_for_pipeline() call already applies an "overview" camera automatically,
    so you only need this tool if you want to reset the view or try a different style.

    Styles:
      "overview"  (default) — elevated oblique view of the whole scene
      "top_down"  — bird's eye view looking straight down
      "side"      — side view from the south

    Returns a screenshot showing the new camera angle.
    """
    renderer = _current_ctx().renderer
    def _impl():
        result = renderer.suggest_camera(style)
        if result is None:
            return "No actors in the scene. Call wait_for_pipeline first."
        renderer.set_camera(**result)
        renderer.render()
        pos = [round(x, 1) for x in result["position"]]
        fp = [round(x, 1) for x in result["focal_point"]]
        return f"Camera set to {style} view.\n  position={pos}\n  focal_point={fp}"
    return _with_screenshot(renderer.dispatch(_impl))


@mcp.tool(structured_output=False)
def get_camera() -> str:
    """Get the current camera position, focal point, and up vector.

    Returns the current camera state so you can save it, tweak it, or
    restore it later with set_camera().
    """
    renderer = _current_ctx().renderer
    cam = renderer.dispatch(renderer.get_camera_state)
    if cam is None:
        return "No scene initialized. Call wait_for_pipeline first."
    pos = [round(x, 1) for x in cam["position"]]
    fp = [round(x, 1) for x in cam["focal_point"]]
    up = cam["up"]
    return (
        f"Current camera:\n"
        f"  position={pos}\n"
        f"  focal_point={fp}\n"
        f"  up={up}\n\n"
        f"To reuse: set_camera(position={tuple(pos)}, focal_point={tuple(fp)}, up={up})"
    )


@mcp.tool(structured_output=False)
def set_camera(
    position: list[float] = None,
    focal_point: list[float] = None,
    up: list[float] = None,
    zoom: float = 0,
) -> list[str | Image]:
    """Set the camera position without rebuilding the pipeline.

    Much faster than modifying camera() in wait_for_pipeline. Pass coordinates
    as numeric lists, e.g. position=[100, -500, 400].

    Args:
        position: Camera position as [x, y, z].
        focal_point: Camera focal point as [x, y, z].
        up: Camera up vector as [x, y, z] (default [0, 0, 1]).
        zoom: Zoom factor (> 0 to apply, e.g. 1.5 to zoom in).
    """
    renderer = _current_ctx().renderer
    def _impl():
        kwargs = {}
        if position is not None:
            kwargs["position"] = tuple(float(x) for x in position)
        if focal_point is not None:
            kwargs["focal_point"] = tuple(float(x) for x in focal_point)
        if up is not None:
            kwargs["up"] = tuple(float(x) for x in up)
        if zoom > 0:
            kwargs["zoom"] = zoom
        if not kwargs:
            return "Specify at least position or focal_point."
        renderer.set_camera(**kwargs)
        renderer.render()
        cam = renderer.get_camera_state()
        return (
            f"Camera updated.\n"
            f"  position={[round(x,1) for x in cam['position']]}\n"
            f"  focal_point={[round(x,1) for x in cam['focal_point']]}"
        )
    return _with_screenshot(renderer.dispatch(_impl))


@mcp.tool(structured_output=False)
def set_window_size(width: int, height: int) -> list[str | Image]:
    """Set the render window size for higher/lower resolution screenshots.

    Default is 1920x1080. Use 3840x2160 for 4K publication quality.
    """
    renderer = _current_ctx().renderer
    def _impl():
        renderer.set_size(width, height)
        renderer.render()
        return f"Window size set to {width}x{height}."
    return _with_screenshot(renderer.dispatch(_impl))


def _pipeline_preview_line(code: str) -> str:
    """First meaningful line of a pipeline, for a version-list preview.

    The mandatory ``from siva.spec_api import *`` header is identical
    boilerplate across every version, so showing it would make all previews
    look the same — skip it (and blank lines). Human-written annotations are
    the opposite: a leading comment shows as-is, and a leading module
    docstring (now allowed before the header, see :mod:`siva.sandbox`) shows
    as its summary line, since both distinguish versions better than the
    first code line. Falls back to a line-scan when the file doesn't parse,
    and to ``(empty)`` when there is nothing else.
    """
    import ast

    from .sandbox import SPEC_IMPORT_HEADER, _is_module_docstring, _is_spec_import

    lines = code.split("\n")
    try:
        body = ast.parse(code).body
    except SyntaxError:
        body = None
    if body is not None:
        if body and _is_module_docstring(body[0]):
            for text_line in body[0].value.value.splitlines():
                if text_line.strip():
                    return text_line.strip()
        skip = set()
        for node in body:
            if _is_spec_import(node):
                end = getattr(node, "end_lineno", None) or node.lineno
                skip.update(range(node.lineno, end + 1))
        for lineno, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or lineno in skip:
                continue
            return stripped
        return "(empty)"

    # Unparseable (mid-edit) file: fall back to skipping blanks and the
    # literal header line.
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped == SPEC_IMPORT_HEADER:
            continue
        return stripped
    return "(empty)"


@mcp.tool()
def list_versions() -> str:
    """List all saved pipeline versions with timestamps.

    Each wait_for_pipeline call creates a new version. Use restore_version(n)
    to go back to a previous version.
    """
    ctx = _current_ctx()
    versions = sorted(ctx.history_dir.glob("v*/pipeline.py"))
    if not versions:
        return "No versions saved yet. Call wait_for_pipeline() to create the first version."
    lines = [f"Pipeline versions for view '{ctx.name}' ({len(versions)} total):"]
    for v in versions:
        ver_num = int(v.parent.name[1:])
        code = v.read_text()
        preview = _pipeline_preview_line(code)
        first_line = preview[:80]
        has_screenshot = (v.parent / "screenshot.png").exists()
        lines.append(f"  v{ver_num}: {first_line}{'...' if len(preview) > 80 else ''}"
                     f" {'[screenshot]' if has_screenshot else ''}")
    lines.append(f"\nCurrent: v{ctx.version}")
    lines.append("Use restore_version(n) to restore a previous version.")
    return "\n".join(lines)


@mcp.tool(structured_output=False)
def restore_version(version: int) -> list[str | Image]:
    """Restore a previous pipeline version by number.

    Writes the historical pipeline back to `view-<name>.py` and waits for the
    rebuild. Because this overwrites the file, the watcher will also see the
    change; the coordinator dedupes by content hash so there is no double build.
    """
    ctx = _current_ctx()
    ver_dir = ctx.history_dir / f"v{version:04d}"
    spec_file = ver_dir / "pipeline.py"
    if not spec_file.exists():
        # List available versions
        versions = sorted(ctx.history_dir.glob("v*/pipeline.py"))
        if versions:
            nums = [int(v.parent.name[1:]) for v in versions]
            return f"Version {version} not found. Available: {nums}"
        return f"Version {version} not found. No versions saved yet."
    pipeline_path = Path(ctx.pipeline_file)
    pipeline_path.write_text(spec_file.read_text())
    return wait_for_pipeline()


@mcp.tool()
def get_dsl_overview() -> str:
    """Get a complete overview of the SIVA DSL: workflow patterns, all forms, VTK classes, and colormaps.

    Returns everything you need before writing your first pipeline:

    - **Architecture overview** and typical workflow
    - **4 key patterns** (surface coloring, isosurface, volume rendering, streamlines)
    - **Full DSL form index** organized by category with one-line descriptions
    - **VTK Sources/Readers and Filters** usable with source() and filter()
    - **Colormap presets** for the lut= parameter of show()

    This is your single entry point for DSL discovery. Call this first, then use
    get_dsl_reference(form="form-name") for detailed parameter docs on any specific form.
    """
    import re
    import vtk
    from .filters import WHITELISTED_CLASSES
    from .colormaps import PRESETS, OPACITY_PRESETS

    sources = sorted(k for k in WHITELISTED_CLASSES if "Source" in k or "Reader" in k)
    filters = sorted(k for k in WHITELISTED_CLASSES if k not in set(sources))
    colormap_names = ", ".join(f'"{n}"' for n in sorted(PRESETS))
    opacity_preset_names = ", ".join(f'"{n}"' for n in sorted(OPACITY_PRESETS))

    def _vtk_first_para(cls_name):
        cls = getattr(vtk, cls_name, None)
        if cls is None:
            return ""
        doc = (cls.__doc__ or "").strip()
        para = []
        for line in doc.splitlines():
            line = line.strip()
            if not line:
                if para:
                    break
                continue
            para.append(line)
        s = " ".join(para)
        return re.sub(r"^" + re.escape(cls_name) + r"\s*-\s*", "", s)

    filter_lines = [f"  {c} — {_vtk_first_para(c)}" for c in filters]

    lines = [
        "=== SIVA DSL Overview ===",
        "",
        "TWO-LAYER ARCHITECTURE:",
        "  MCP tools  — interactive operations called by you/an AI: load data, query statistics,",
        "               execute pipelines, adjust the scene, take screenshots.",
        "  DSL forms  — declarative pipeline language used in pipeline .py files: source(),",
        "               filter(), threshold(), contour(), show(), camera(), background().",
        "",
        "The bridge is wait_for_pipeline(): it executes a DSL pipeline file and renders the result.",
        "",
        "TYPICAL WORKFLOW:",
        "  1. list_data_files()          — see what's available",
        "  2. load(\"mydata.vts\")         — load the dataset; already returns full describe_data() output",
        "  3. describe_data(node=, field=) — only needed for derived nodes (after threshold, contour, etc.)",
        "  4. Write a pipeline file (see patterns below), then call wait_for_pipeline()",
        "  5. The first wait_for_pipeline() auto-applies an overview camera. Call",
        "     set_suggested_camera() only to reset or switch style. Camera is preserved",
        "     across all subsequent wait_for_pipeline() calls.",
        "  6. Iterate: edit the file, call wait_for_pipeline() again",
        "",
        "PIPELINE FILE STRUCTURE:",
        "  # Mandatory header — first statement (an optional docstring may precede",
        "  # it); makes the SIVA DSL forms available",
        "  from siva.spec_api import *",
        "",
        "  # Load data",
        "  data = source(\"vtkXMLStructuredGridReader\", FileName=\"mydata.vts\")",
        "  # Filter chain",
        "  region = threshold(input=data, ThresholdBy=\"field\", ThresholdRange=[lo, hi])",
        "  # Display",
        "  show(region, \"name\", color_by=\"field\", scalar_range=(lo, hi))",
        "  # Background defaults to dark; call background(\"white\"|\"light\"|\"black\") to change.",
        "  # (Camera is set via set_camera() MCP tool, not in the pipeline file.)",
        "",
        "--- THE PIPELINE FILE IS REAL CODE, NOT JUST A LIST OF CALLS ---",
        "It runs in the Monty sandbox (https://github.com/pydantic/monty), which is a",
        "SUBSET of real Python — most of the language works, with some gaps (see the",
        "gotchas below). So you CAN compute the pipeline — loops, comprehensions,",
        "helper functions, variables, arithmetic, f-strings, def/lambda all work, and",
        "`math` is available (no import needed). Default to plain, literal calls: a",
        "handful of distinct layers is clearest written out flat. Reach for computation",
        "only when it removes real duplication or fragility:",
        "  - You're writing the 3rd near-identical show()/contour() call — loop it.",
        "  - A literal (an ambient temperature, a domain bound) recurs, or one value is",
        "    derived from another (opacity from height) — name it as a constant/helper",
        "    so the pieces stay consistent when you change it.",
        "  - You'll want to sweep a count or spacing — parameterize it once.",
        "If it doesn't reduce repetition or couple related numbers, keep it literal.",
        "",
        "Gotchas worth avoiding up front:",
        "  - Format strings with f-strings; `%` and str.format() are NOT supported.",
        "  - Use plain functions, not classes (class and match are unsupported).",
        "  - Load data with source(), never open(); most stdlib beyond math is absent.",
        "",
        "  Example — computation earns its place here: a recurring ambient temp is",
        "  named once, and per-shell opacity is DERIVED (hotter = more opaque) across",
        "  a generated family of isosurfaces — tedious and error-prone written flat:",
        "",
        "    from siva.spec_api import *",
        "",
        "    data = source(\"vtkXMLStructuredGridReader\", FileName=\"fire.vts\")",
        "",
        "    AMBIENT = 300.0                          # domain knowledge, named once",
        "    def opacity_for(frac):                   # 0.0 outer .. 1.0 hot core",
        "        return 0.08 + (0.9 - 0.08) * frac ** 1.5",
        "",
        "    shells = [AMBIENT + d for d in (20, 60, 120, 300)]   # comprehension",
        "    for i, temp in enumerate(shells):",
        "        frac = i / (len(shells) - 1)",
        "        iso = contour(input=data, ContourBy=\"theta\", Isosurfaces=[temp])",
        "        show(iso, f\"shell_{i}\", color_by=\"theta\", scalar_range=(320, 600),",
        "             lut=\"fire\", opacity=round(opacity_for(frac), 3),",
        "             scalar_bar=(\"theta (K)\" if i == 0 else False))   # one legend",
        "",
        "  Change one number (the shell list) and the whole scene re-derives itself.",
        "",
        "--- KEY PATTERNS ---",
        "",
        "1a. SURFACE COLORING — flat/regular grid (vtkImageData, vtkRectilinearGrid):",
        "from siva.spec_api import *",
        "",
        "data = source(\"vtkXMLImageDataReader\", FileName=\"mydata.vti\")",
        "surface = extract_region(input=data, bounds=[xmin, xmax, ymin, ymax, zmin, zmin])",
        "show(surface, \"ground\", color_by=\"fieldname\", scalar_range=(lo, hi), lut=\"cool_to_warm\")",
        "",
        "1b. SURFACE COLORING — terrain-following structured grid (vtkStructuredGrid):",
        "#   Use grid index k=0, NOT spatial z bounds (ground z varies across the domain)",
        "#   Check dimensions with describe_data() first",
        "ground = extract_grid(input=data, VOI=[0, ni_max, 0, nj_max, 0, 0])",
        "show(ground, \"ground\", color_by=\"fieldname\", scalar_range=(lo, hi), lut=\"cool_to_warm\")",
        "",
        "2. ISOSURFACE (one or more nested values):",
        "from siva.spec_api import *",
        "",
        "data = source(\"vtkXMLStructuredGridReader\", FileName=\"mydata.vts\")",
        "# Isosurfaces accepts a list of one or more values; suggest_isosurface() finds meaningful ones",
        "iso = contour(input=data, ContourBy=\"fieldname\",",
        "              Isosurfaces=[v_low, v_mid, v_high])",
        "show(iso, \"iso\", color_by=\"fieldname\", scalar_range=(v_low, v_high),",
        "     lut=\"cool_to_warm\", opacity=0.35)",
        "",
        "3. THRESHOLD + VOLUME RENDERING:",
        "from siva.spec_api import *",
        "",
        "data = source(\"vtkXMLStructuredGridReader\", FileName=\"mydata.vts\")",
        "region = threshold(input=data, ThresholdBy=\"fieldname\", ThresholdRange=[lo, hi])",
        "show(region, \"vol\", representation=\"Volume\", color_by=\"fieldname\",",
        "    scalar_range=(lo, hi), lut=\"cool_to_warm\",",
        "    opacity_function=[(lo, 0.0), (mid, 0.05), (hi, 0.5)],",
        "    gradient_opacity=True, volume_resolution=200)",
        "",
        "4. STREAMLINES:",
        "from siva.spec_api import *",
        "",
        "data = source(\"vtkXMLStructuredGridReader\", FileName=\"mydata.vts\")",
        "velocity = make_vector(input=data, components=(\"u\", \"v\", \"w\"), result=\"velocity\")",
        "# Use source(\"vtkLineSource\") or source(\"vtkPlaneSource\") for seed points",
        "# On terrain-following / curvilinear grids, call the get_ground_z MCP tool to get ground z at (x,y) before choosing seed z",
        "seeds = source(\"vtkLineSource\", Point1=[x0, y0, z0], Point2=[x1, y0, z0], Resolution=30)",
        "streams = stream_tracer(input=velocity, SeedSource=seeds, Vectors=\"velocity\",",
        "    IntegrationDirection=\"Both\", MaximumNumberOfSteps=2000, MaximumPropagation=500)",
        "show(streams, \"flow\", color_by=\"velocity\", opacity=0.8)",
        "",
        "--- TIPS ---",
        "- Use describe_data(node=, field=) to find field ranges before choosing scalar_range or threshold values",
        "- Use suggest_isosurface() to find meaningful contour values",
        "- The first wait_for_pipeline() auto-applies an overview camera. Call set_suggested_camera()",
        "  only to reset or try a different style (\"overview\", \"top_down\", \"side\")",
        "- Start simple and add layers incrementally — debug one layer at a time",
        "- COORDINATE SYSTEMS: slice(), extract_region(), and clip_box() use physical (world)",
        "  coordinates. extract_grid() uses absolute structured-grid indices from the file's",
        "  extent (which may NOT start at 0). describe_data() shows the valid index extent.",
        "  get_spatial_extent() returns BOTH physical bounds and grid indices for a feature.",
        "  Mixing physical coords and grid indices silently produces wrong selections.",
        "- VTK boolean properties: use the direct setter (e.g. Splitting=False), not VTK's",
        "  C++ macro form (SplittingOff=True). Only the underlying SetX(bool) is exposed;",
        "  XOn()/XOff() aren't.",
        "- Algebra vs. mesh: calculator() does math on existing field values (arithmetic,",
        "  vector ops, conditional masking). For quantities that depend on the *mesh* itself",
        "  — surface normals, cell shape, connectivity, neighborhood derivatives — use a",
        "  filter and scan the filter list first. Tell: if you're reconstructing a geometric",
        "  quantity from point coordinates, you're probably rebuilding what a filter already",
        "  computes.",
        "",
        "--- DSL FORMS (used in pipeline .py files, executed by wait_for_pipeline()) ---",
        "",
        "=== Data Sources ===",
        "  source(class_name, **props)       — load a file or create geometry using any whitelisted VTK class",
        "  raw_source(filename, dimensions, scalar_type, ...)  — load raw binary volume data",
        "  filter(class_name, input=, **props) — apply any whitelisted VTK filter directly",
        "",
        "=== Data Prep ===",
        "  threshold(input=, ThresholdBy=, ThresholdRange=[min,max])  — keep cells in a value range",
        "  extract_region(input=, bounds=[xmin,xmax,ymin,ymax,zmin,zmax])  — crop by spatial bounds (or voi= for grid indices)",
        "  extract_grid(input=, VOI=[i0,i1,j0,j1,k0,k1])  — extract a sub-grid by absolute index extent (NOT physical coords; check describe_data() for valid range)",
        "  calculator(input=, Function=, ResultArrayName=, AddScalarArrayName=[], AddVectorArrayName=[])  — derived scalar or vector field; vector algebra is first-class (dot/cross/mag/norm, vector arithmetic). See get_dsl_reference(form=\"calculator\") for the syntax.",
        "  cell_to_point(input=)   — promote cell arrays to point arrays (required before contouring)",
        "  point_to_cell(input=)   — demote point arrays to cell arrays",
        "  resample_to_image(input=, dimensions=(nx,ny,nz))  — resample to a regular grid",
        "  probe(input=, source=node)  — sample one dataset at the points of another",
        "  elevation(input=, low_point=, high_point=)  — add an Elevation scalar field by Z height",
        "",
        "=== Derived Fields ===",
        "  make_vector(input=, components=('cx','cy','cz'), result='velocity')  — assemble vector from scalar components",
        "  compute_magnitude(input=, components=('u','v','w'), result='speed')  — compute vector magnitude as a scalar",
        "  curl_vector(vector_field=node, output_field='vorticity')  — compute 3-component curl (vorticity vector)",
        "  curl_magnitude(vector_field=node, output_field='vorticity_magnitude')  — compute scalar curl magnitude",
        "  gradient(input=, GradientField=, ResultArrayName=)  — compute 3-component gradient vector",
        "  compute_gradient_magnitude(input=, field=, result=)  — scalar magnitude of gradient (edge detection)",
        "  extract_component(input=, field=, component=0, result_name=)  — isolate one component of a vector",
        "",
        "=== Geometry ===",
        "  contour(input=, ContourBy=, Isosurfaces=[])  — extract isosurfaces",
        "  slice(input=, origin=(x,y,z), normal=(nx,ny,nz))  — planar cross-section",
        "  clip(input=, origin=, normal=, inside_out=False)  — half-space clip by plane",
        "  clip_box(input=, bounds=(xmin,xmax,ymin,ymax,zmin,zmax))  — rectangular crop",
        "  clip_sphere(input=, center=, radius=, inside_out=True)  — spherical crop",
        "  surface(input=)  — extract outer boundary as a polygonal mesh",
        "  smooth(input=, iterations=20)  — Laplacian smoothing of a surface mesh",
        "  warp_vector(input=, ScaleFactor=)  — displace points along a vector field",
        "  warp_scalar(input=, ScaleFactor=)  — displace points along surface normal by a scalar",
        "  outline(input=)  — bounding-box wireframe",
        "",
        "=== Flow / Particles ===",
        "  stream_tracer(input=, SeedSource=, Vectors=, ...)  — trace streamlines through a vector field",
        "  tube(input=, Radius=, NumberOfSides=)  — wrap streamlines as 3D tubes; lines (default) usually look better — only use if the human asks",
        "  glyph(input=, GlyphSource=, OrientationArray=, ScaleArray=, ScaleFactor=)  — place oriented glyphs",
        "  mask_points(input=, OnRatio=, RandomMode=)  — subsample point cloud for glyphs/seeds",
        "  line_probe(input=, point1=, point2=, resolution=)  — sample values along a line",
        "",
        "=== Display ===",
        "  show(node, name, color_by=, scalar_range=, lut=, opacity=, component=0/1/2)  — add node to scene",
        "  show(..., representation='Volume', opacity_function=[(val,opacity),...],",
        f"       volume_resolution=256, gradient_opacity=True, shade=True)  — volume rendering",
        f"  Volume opacity presets: \"ramp_up\", \"gaussian\", \"step\", {opacity_preset_names}",
        "  camera(position=, focal_point=, up=, zoom=)  — embed camera in pipeline (for reproducible",
        "    exports only; camera is otherwise managed via set_suggested_camera()/set_camera())",
        "  background('dark'|'light'|'black'|'white') | background(r, g, b)  — set background color",
        "  title(text, position=, font_size=, color=)  — add a text overlay",
        "  annotate(x, y, z, text, color=, font_size=)  — 3-D billboard label at a world-space position",
        "  axes(color=, font_size=, labels=)  — add labeled X/Y/Z axes with tick marks (physical coords)",
        "",
        "=== Sources/Readers (for use with source()) ===",
        ", ".join(sources),
        "",
        "=== Filters (for use with filter()) ===",
        *filter_lines,
        "",
        "=== Colormaps (for lut= parameter of show()) ===",
        colormap_names,
        "",
        'Use get_dsl_reference(form="form-name") for full parameter docs on any form above.',
    ]

    return "\n".join(lines)


@mcp.tool()
def list_data_files() -> str:
    """List available data files in the current directory.

    Finds files with supported extensions: .vts, .vti, .vtp, .vtu, .vtr,
    .vtk, .nrrd, .nhdr, .raw

    Searches the current directory and all subdirectories.
    Call this first to see what datasets are available to visualize.
    """
    import glob
    patterns = ["*.vts", "*.vti", "*.vtk", "*.vtp", "*.vtu", "*.vtr",
                "*.raw", "*.nrrd", "*.nhdr"]
    files = []
    for pat in patterns:
        files.extend(glob.glob(pat))
        files.extend(glob.glob(f"**/{pat}", recursive=True))

    # Deduplicate (top-level files match both patterns) while preserving order
    seen = set()
    unique = []
    for f in files:
        if f not in seen:
            seen.add(f)
            unique.append(f)
    files = unique

    if not files:
        return "No VTK data files found in current directory or subdirectories."

    lines = ["Available data files:"]
    for f in sorted(files):
        size = os.path.getsize(f)
        if size > 1e9:
            size_str = f"{size/1e9:.1f} GB"
        elif size > 1e6:
            size_str = f"{size/1e6:.1f} MB"
        else:
            size_str = f"{size/1e3:.1f} KB"
        lines.append(f"  {f} ({size_str})")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Named-view management tools
# ---------------------------------------------------------------------------

@mcp.tool(structured_output=False)
def new_view(name: str, camera: str = "") -> list[str | Image]:
    """Create a new independent render context (view), execute its pipeline, and return a screenshot.

    Each view has its own pipeline, camera, and version history.
    Write view-<name>.py first (it must begin with `from siva.spec_api import *`
    as its first statement — an optional module docstring may precede it —
    that header makes the DSL forms available), then call this to create the
    view and render it in one step.
    After this call all tools operate on the new view.

    Args:
        name: Unique name for the new view (e.g. "temperature", "detail").
              Cannot be an existing view name. The pipeline file must already
              exist at view-<name>.py.
        camera: Optional camera style to apply after rendering. One of
                "overview", "top_down", or "side". Defaults to "overview"
                if not specified.
    """
    if name in _views:
        return [f"View '{name}' already exists. Use focus('{name}') to switch to it."]
    cur_renderer = _current_ctx().renderer
    # All view renderers are built through the injectable _renderer_factory
    # seam (see _make_renderer / _init_for_test) -- new_view has no private
    # construction path, so tests can supply fakes for every view.
    if cur_renderer.mode == RenderMode.TRAME:
        # TrameRenderer schedules itself onto the shared trame event loop
        # (see trame_backend.py) rather than needing thread marshaling here;
        # it joins the shared trame server, reachable at /?ui=<name> on the
        # same port as every other view.
        renderer = _renderer_factory(name)
    else:
        # Desktop renderer init must happen on the main thread (macOS Cocoa
        # requires NSWindow creation on the main thread; VTK's Initialize()
        # does this), so marshal construction onto the owner thread.
        renderer = cur_renderer.dispatch(lambda: _renderer_factory(name))
    ctx = _register_view(name, renderer)

    file = ctx.pipeline_file
    if not os.path.exists(file):
        return [f"View '{name}' created but pipeline file not found: {file}\n\nWrite your pipeline code to {file} first (begin it with `from siva.spec_api import *`), then call new_view() again."]

    record = ctx.coordinator.wait_for_current(timeout=120)
    if record is None:
        return [f"View '{name}' created but pipeline file not found: {file}"]
    if record.status == "running":
        return [f"View '{name}' created but build timed out after 120s. Check pipeline_status() for details."]

    if camera:
        set_suggested_camera(camera)

    result_text = record.format(verbose=False) + _first_time_url_banner()
    screenshot_path = record.screenshot_path
    if record.status == "ok" and screenshot_path and os.path.exists(screenshot_path):
        return [result_text, Image(path=screenshot_path)]
    return [result_text]


@mcp.tool(structured_output=False)
def focus(name: str) -> str:
    """Switch which view all tools target (make a named view current).

    After calling this, all tools (wait_for_pipeline, set_camera, screenshot, etc.)
    will operate on the named view. Returns a screenshot of the focused view.

    Args:
        name: Name of the view to switch to.
    """
    global _current_view
    if name not in _views:
        available = sorted(_views.keys())
        return f"View '{name}' not found. Available views: {available}"
    _current_view = name
    img = _auto_screenshot()
    msg = f"Switched to view '{name}'."
    if img is not None:
        return [msg, img]
    return msg


@mcp.tool()
def close_view(name: str) -> str:
    """Close and remove a named view.

    Cannot close the last remaining view. Clears all VTK resources for that view.
    If the closed view was current, focus switches to the first remaining view.

    Args:
        name: Name of the view to close.
    """
    global _views, _current_view
    if name not in _views:
        available = sorted(_views.keys())
        return f"View '{name}' not found. Available views: {available}"
    if len(_views) <= 1:
        return f"Cannot close view '{name}': it is the only remaining view."
    # Clean up the renderer — destroy on main thread (macOS requires it)
    ctx = _views.pop(name)
    ctx.shutdown()
    try:
        ctx.renderer.dispatch(ctx.renderer.destroy)
    except Exception:
        pass
    # If we closed the current view, switch to the first remaining
    if _current_view == name:
        _current_view = next(iter(_views))
    return f"Closed view '{name}'. Current view is now '{_current_view}'."


@mcp.tool()
def list_views() -> str:
    """List all named views and which one is currently active.

    Returns view names, pipeline status, version numbers, and whether
    each view's OS window has been closed by the user (interactive mode
    only).  A "window closed" flag means the view still exists in the
    registry but the OS window is gone — the agent can offer to reopen
    it (via focus()) or remove it (via close_view()).

    In --trame mode each view also lists the browser URL where it is
    served. Every view shares one server on a single port; the URLs
    differ only by their ?ui=<name> query parameter, so forwarding that
    one port exposes every view. When running behind code-server / Coder
    the proxied URL is shown first — it is the one to open from the
    human's browser; the localhost URL works only from the machine
    running SIVA. A stable index page listing all views together (with
    thumbnails and links) is served at /views on the same port -- see
    view_url().
    """
    if not _views:
        return "No views initialized (call main() first)."
    lines = [f"Views ({len(_views)} total, current: '{_current_view}'):"]
    if _view_index is not None:
        primary, fallback = _primary_url(_view_index.url, _view_index.proxy_url)
        lines.append(f"  Index page (all views): {primary}")
        if fallback:
            lines.append(f"      from this machine only: {fallback}")
    for vname, ctx in sorted(_views.items()):
        marker = " *" if vname == _current_view else ""
        has_pipeline = bool(ctx.vtk_objects)
        pipeline_info = f"v{ctx.version}, {len(ctx.vtk_objects)} nodes" if has_pipeline else "no pipeline"
        closed_flag = ""
        if hasattr(ctx.renderer, "is_window_closed") and ctx.renderer.is_window_closed():
            closed_flag = " [window closed]"
        lines.append(f"  {vname}{marker}: {pipeline_info}{closed_flag}")
        url = getattr(ctx.renderer, "url", None)
        if url:
            primary, fallback = _primary_url(
                url, getattr(ctx.renderer, "proxy_url", None))
            lines.append(f"      URL: {primary}")
            if fallback:
                lines.append(f"      from this machine only: {fallback}")
    return "\n".join(lines)


@mcp.tool()
def view_url(name: str = "") -> str:
    """Return a browser URL: the view index page, or one specific view.

    In --trame mode every view is served as an interactive browser view
    (server-side VTK rendering streamed over a websocket) by one shared
    server on a single port: each view lives at /?ui=<name>, and the
    index page at /views. Open the index page once -- it lists every
    live view with a link, a "focused" indicator, and a thumbnail, and
    it stays current as views come and go via new_view()/close_view().
    In non-trame modes there is no browser view and this reports that.

    Args:
        name: Optional view name. If omitted (the default), returns the
              index page URL -- the recommended entry point. If given,
              returns the URL for that specific view directly.
    """
    live = any(getattr(ctx.renderer, "url", None) for ctx in _views.values())
    if not live:
        return (
            "No browser view for this session. URLs are only available in "
            "--trame mode; this server is running in a desktop/offscreen mode."
        )
    if name:
        if name not in _views:
            available = sorted(_views.keys())
            return f"View '{name}' not found. Available views: {available}"
        ctx = _views[name]
        url = getattr(ctx.renderer, "url", None)
        primary, fallback = _primary_url(
            url, getattr(ctx.renderer, "proxy_url", None))
        lines = [f"View '{ctx.name}' is served at:", f"  {primary}"]
        if fallback:
            lines.append("Only when browsing from this machine itself, use:")
            lines.append(f"  {fallback}")
        return "\n".join(lines)
    if _view_index is None:
        return "No view index available for this session."
    primary, fallback = _primary_url(_view_index.url, _view_index.proxy_url)
    lines = ["All live views are listed at the index page:", f"  {primary}"]
    if fallback:
        lines.append("Only when browsing from this machine itself, use:")
        lines.append(f"  {fallback}")
    return "\n".join(lines)


@mcp.tool()
def get_dsl_reference(form: str) -> str:
    """Get detailed documentation for a DSL pipeline form.

    Returns the full docstring, signature, a concrete usage example, and
    links to related forms.  This is the primary reference for understanding
    what parameters any DSL form accepts and how to use it.

    DSL forms are plain Python functions available inside pipeline .py files
    executed by wait_for_pipeline().  Every pipeline file must begin with the
    line `from siva.spec_api import *` as its first statement (an optional
    module docstring may precede it) — that header makes the DSL forms
    available. The per-form examples below assume that header and an
    already-loaded `data` node.

    Call get_dsl_overview() first to see all available form names with descriptions.
    Common forms to look up:
    - "show" — add a node to the scene with all display options
    - "source" — load data or create a geometric shape
    - "filter" — apply any whitelisted VTK filter directly
    - "threshold" — keep cells in a field value range
    - "contour" — extract isosurfaces
    - "stream_tracer" — trace streamlines through a vector field
    - "glyph" — place oriented/scaled glyphs at grid points
    - "volume" — (use show() with representation="Volume")

    Args:
        form: DSL form name string, e.g. "show", "threshold", "contour",
              "stream_tracer", "glyph", "extract_component", etc.
              Case-insensitive.
    """
    import inspect
    from .dsl import PipelineBuilder

    # Hand-written examples per form (short, illustrative)
    _EXAMPLES = {
        "source": '''\
# Every pipeline file starts with this header (makes the DSL forms available):
from siva.spec_api import *

# Load a structured grid (fire/CFD simulation):
data = source("vtkXMLStructuredGridReader", FileName="output.30000.vts")

# Load image/volume data (CT scan, uniform grid):
ct = source("vtkXMLImageDataReader", FileName="bonsai.vti")

# Geometry sources (no FileName needed):
arrow = source("vtkArrowSource", TipResolution=8, ShaftResolution=8)
pts   = source("vtkPointSource", NumberOfPoints=200, Radius=50)
''',
        "raw_source": '''\
# Load a 16-bit CT scan raw binary (256x256x128 voxels):
ct = raw_source("scan.raw", dimensions=(256, 256, 128),
                scalar_type="unsigned_short")
show(ct, "vol", representation="Volume",
     opacity_function="ct_bone", lut="grayscale")

# Float32 simulation with 256-byte header:
sim = raw_source("sim_output.raw", dimensions=(512, 512, 256),
                 scalar_type="float", header_size=256)
show(sim, "sim", representation="Volume",
     color_by="density", scalar_range=(0.0, 1.0))
''',
        "filter": '''\
# Use any whitelisted VTK class not covered by a convenience form:
slim = filter("vtkPassArrays", input=data,
              PointDataArrays=["temperature", "pressure"])
''',
        "threshold": '''\
# Keep only cells where temperature is between 500 and 2000 K:
fire = threshold(input=data, ThresholdBy="temperature",
                 ThresholdRange=[500, 2000])
show(fire, "fire", color_by="temperature",
     scalar_range=(500, 2000), lut="fire",
     scalar_bar="Temperature (K)")

# Volume-render the thresholded region:
show(fire, "fire_vol", representation="Volume",
     color_by="temperature", scalar_range=(500, 2000),
     lut="fire", opacity_function=[(500,0),(800,0.02),(2000,0.4)],
     gradient_opacity=True)
''',
        "contour": '''\
# Single isosurface at 800 K:
iso = contour(input=data, ContourBy="temperature", Isosurfaces=[800.0])
show(iso, "flame_front", color_by="temperature",
     scalar_range=(300, 1200), lut="fire")

# Multiple isosurfaces (pressure shells):
shells = contour(input=data, ContourBy="pressure",
                 Isosurfaces=[0.25, 0.5, 0.75])
show(shells, "pressure_shells", color_by="pressure",
     scalar_range=(0, 1), opacity=0.5)

# NOTE: coloring a contour by its own ContourBy field produces a uniform-color
# surface (all points share the same isovalue). Use multiple Isosurfaces to
# show variation, or set color_by to a *different* field for richer coloring.
''',
        "slice": '''\
# Horizontal cross-section at mid-altitude:
xsec = slice(input=data, origin=(500, 400, 50), normal=(0, 0, 1))
show(xsec, "horiz_cut", color_by="temperature",
     scalar_range=(300, 1200), opacity=0.8)

# Vertical YZ cross-section at a specific x position (physical coords):
# Use this — NOT extract_grid — when selecting by physical location.
# Get x from describe_data() bounds or get_spatial_extent() output.
vert = slice(input=data, origin=(80, 0, 100), normal=(1, 0, 0))
show(vert, "crosswind_cut", color_by="w", lut="cool_to_warm",
     scalar_range=(-5, 15))
''',
        "clip": '''\
# Keep everything to the right of x=500:
right = clip(input=data, origin=(500, 0, 0), normal=(1, 0, 0))
show(right, "right_half", color_by="temperature",
     scalar_range=(300, 1200))

# Keep everything to the left (flip the normal):
left = clip(input=data, origin=(500, 0, 0), normal=(-1, 0, 0))
show(left, "left_half", color_by="pressure", scalar_range=(0, 1))
''',
        "clip_box": '''\
# Crop to a 200x200x100 sub-region around the fire:
crop = clip_box(input=data,
                bounds=[400, 600, 300, 500, 0, 100])
show(crop, "zoom", color_by="temperature",
     scalar_range=(300, 1200), lut="fire")
''',
        "clip_sphere": '''\
# Keep only data within 200 units of a point of interest:
local = clip_sphere(input=data, center=(500, 400, 50), radius=200)
show(local, "plume", color_by="temperature",
     lut="fire", scalar_range=(300, 1200))
''',
        "extract_region": '''\
# Crop by physical bounds (auto-converts to grid indices):
region = extract_region(input=data,
                        bounds=[400, 600, 300, 500, 0, 100])
show(region, "crop", color_by="temperature")

# By grid indices directly:
region = extract_region(input=data,
                        voi=[50, 150, 50, 150, 0, 20])

# With subsampling:
sub = extract_region(input=data,
                     bounds=[400, 600, 300, 500, 0, 100],
                     SampleRate=[2, 2, 1])
show(sub, "sparse", color_by="pressure")
''',
        "extract_grid": '''\
# Extract the ground surface (k=kmin) using extent indices.
# IMPORTANT: VOI uses absolute structured-extent indices, NOT physical coords.
# Call describe_data() first to see the valid extent: e.g. i=[251,850], j=[0,499], k=[0,60]
# Use get_spatial_extent() to convert a physical feature region to grid indices.
terrain = extract_grid(input=data, VOI=[251, 850, 0, 499, 0, 0])
show(terrain, "ground", color_by="fuel_density")
''',
        "surface": '''\
# Extract outer boundary of a volume and render it semi-transparently:
surf = surface(input=data)
show(surf, "skin", color_by="temperature",
     scalar_range=(300, 1200), opacity=0.3)

# Smooth the surface before displaying:
smooth_surf = smooth(input=surf, iterations=50)
show(smooth_surf, "clean_skin", color=(0.8, 0.8, 0.8), opacity=0.5)
''',
        "smooth": '''\
surf = surface(input=iso)
polished = smooth(input=surf, iterations=50)
show(polished, "surface",
     color=(0.9, 0.7, 0.3), specular=0.5, specular_power=30)
''',
        "make_vector": '''\
# Assemble velocity from U, V, W scalar components:
vel = make_vector(input=data, components=("u", "v", "w"), result="velocity")

# Now use it for streamlines:
seed_line = source("vtkLineSource",
                   Point1=[450, 400, 10], Point2=[550, 400, 10],
                   Resolution=30)
streams = stream_tracer(input=vel, SeedSource=seed_line, Vectors="velocity",
                        IntegrationDirection="Both",
                        MaximumNumberOfSteps=2000)
''',
        "compute_magnitude": '''\
# Compute wind speed scalar from U, V, W components:
speed = compute_magnitude(input=data, components=("u", "v", "w"),
                          result="speed")
show(data, "wind_speed", color_by="speed",
     scalar_range=(0, 30), lut="wind",
     scalar_bar="Speed (m/s)")
''',
        "curl_vector": '''\
vel = make_vector(input=data, components=("u","v","w"), result="velocity")

# Full 3-component curl vector (vorticity):
vort = curl_vector(vector_field=vel)
show(vort, "vort_z", color_by="vorticity", component="z",
     lut="cool_to_warm", scalar_range=(-0.3, 0.3))
''',
        "curl_magnitude": '''\
vel = make_vector(input=data, components=("u","v","w"), result="velocity")

# Scalar curl magnitude (total spinning intensity):
mag = curl_magnitude(vector_field=vel)
show(mag, "spinning", color_by="vorticity_magnitude", scalar_range=(0, 0.5))
''',
        "gradient": '''\
# Compute gradient of pressure field:
grad = gradient(input=data, GradientField="pressure",
                ResultArrayName="pressure_grad")
# grad now has a 3-component "pressure_grad" array
# Use compute_gradient_magnitude for edge detection:
gm = compute_gradient_magnitude(input=data, field="pressure",
                                 result="pressure_edges")
show(data, "boundaries", color_by="pressure_edges",
     scalar_range=(0, 50))
''',
        "compute_gradient_magnitude": '''\
# Find temperature boundaries (flame front):
gm = compute_gradient_magnitude(input=data, field="temperature",
                                 result="temp_edges")
show(data, "flame_front_edges", color_by="temp_edges",
     scalar_range=(0, 100), lut="fire",
     scalar_bar="Gradient magnitude")
''',
        "extract_component": '''\
# Isolate the vertical (Z) component of velocity:
vel = make_vector(input=data, components=("u","v","w"), result="velocity")
w = extract_component(input=vel, field="velocity",
                       component=2, result_name="w_component")
show(data, "updraft", color_by="w_component",
     scalar_range=(-5, 20), lut="cool_to_warm",
     scalar_bar="W velocity (m/s)")

# Alternative: use component= in show() without extract_component:
show(vel, "w_via_show", color_by="velocity", component="z",
     scalar_range=(-5, 20), lut="cool_to_warm")
''',
        "stream_tracer": '''\
# Build velocity vector and trace streamlines:
vel = make_vector(input=data, components=("u","v","w"), result="velocity")

# Line of seed points (use get_ground_z to find valid Z):
seed_line = source("vtkLineSource",
                   Point1=[450, 400, 10], Point2=[550, 400, 10],
                   Resolution=30)
streams = stream_tracer(
    input=vel, SeedSource=seed_line, Vectors="velocity",
    IntegrationDirection="Both",
    MaximumNumberOfSteps=2000,
    MaximumPropagation=500)
show(streams, "flow", color_by="velocity",
     scalar_range=(0, 30), lut="wind", opacity=0.9,
     scalar_bar="Speed (m/s)")

# Plane of seeds for broad coverage:
seeds_plane = source("vtkPlaneSource",
                     Origin=[400, 380, 10], Point1=[600, 380, 10], Point2=[400, 420, 10],
                     XResolution=10, YResolution=5)
streams2 = stream_tracer(input=vel, SeedSource=seeds_plane,
                          Vectors="velocity",
                          IntegrationDirection="Forward",
                          MaximumNumberOfSteps=3000)
''',
        "tube": '''\
# Only use tube() if the human explicitly asks — lines usually look better.
tubes = tube(input=streams, Radius=2.5, NumberOfSides=8)
show(tubes, "flow_tubes", color_by="velocity",
     scalar_range=(0, 30), opacity=0.85, lut="wind")
''',
        "line_seeds": '''\
# A line source is the default choice for streamline seeds — simple and effective.
# Use get_spatial_extent() / get_ground_z() to find good coordinates first.
seeds = source("vtkLineSource", Point1=[x0, y, z], Point2=[x1, y, z], Resolution=40)
streams = stream_tracer(input=vel, SeedSource=seeds, Vectors="velocity",
                        IntegrationDirection="Both", MaximumNumberOfSteps=2000)
show(streams, "flow", color_by="velocity", scalar_range=(0, 30), lut="wind")
''',
        "plane_seeds": '''\
# Use a plane source only when you need broad 2D coverage — a line is usually better.
seeds = source("vtkPlaneSource",
               Origin=(x0, y0, z0), Point1=(x1, y0, z0), Point2=(x0, y1, z0),
               XResolution=10, YResolution=10)
streams = stream_tracer(input=vel, SeedSource=seeds, Vectors="velocity",
                        IntegrationDirection="Both", MaximumNumberOfSteps=2000)
show(streams, "flow", color_by="velocity", scalar_range=(0, 30), lut="wind")
''',
        "glyph": '''\
# Subsample first, then place oriented arrows:
sparse = mask_points(input=data, OnRatio=20, RandomMode=True)
vel = make_vector(input=sparse, components=("u","v","w"), result="velocity")
speed = compute_magnitude(input=vel, components=("u","v","w"), result="speed")
arrow = source("vtkArrowSource", TipResolution=8, ShaftResolution=8)
arrows = glyph(input=speed, GlyphSource=arrow,
               OrientationArray="velocity",
               ScaleArray="speed", ScaleFactor=5.0)
show(arrows, "wind_arrows", color_by="speed",
     scalar_range=(0, 30), lut="wind")
''',
        "mask_points": '''\
# Keep every 20th point, randomly selected (for glyph/seed subsampling):
sparse = mask_points(input=data, OnRatio=20, RandomMode=True)

# Uniform subsampling (every 10th):
uniform = mask_points(input=data, OnRatio=10, RandomMode=False)
''',
        "line_probe": '''\
# Sample temperature along a vertical profile through the plume:
prof = line_probe(input=data,
                  point1=[500, 400, 0],
                  point2=[500, 400, 200],
                  resolution=200)
# Then use the profile() MCP tool to read the values:
# profile("prof", [500,400,0], [500,400,200], fields=["temperature","w"])
''',
        "cell_to_point": '''\
# Promote cell arrays to point arrays before contouring:
pts = cell_to_point(input=data)
iso = contour(input=pts, ContourBy="pressure", Isosurfaces=[0.5])
show(iso, "shell", color_by="pressure", scalar_range=(0, 1))
''',
        "point_to_cell": '''\
# Demote point arrays to cell arrays for cell-based thresholding:
cells = point_to_cell(input=data)
region = threshold(input=cells, ThresholdBy="temperature",
                   ThresholdRange=[500, 2000])
''',
        "probe": '''\
# Sample a volume dataset at a set of points:
pts = source("vtkPointSource", NumberOfPoints=200, Radius=50)
sampled = probe(input=pts, source=data)
show(sampled, "samples", color_by="temperature")

# Sample along a line (line_probe() is the cleaner wrapper):
line = source("vtkLineSource",
              Point1=[0, 0, 0], Point2=[200, 200, 100],
              Resolution=200)
profile_pts = probe(input=line, source=data)
''',
        "resample_to_image": '''\
# Resample to a regular grid for volume rendering (coarse):
img = resample_to_image(input=data, dimensions=[64, 64, 32])
show(img, "vol", representation="Volume",
     color_by="temperature", scalar_range=(300, 1200),
     opacity_function=[(300,0),(600,0.02),(1200,0.4)])

# Higher resolution (more detail, more memory):
img_hi = resample_to_image(input=region, dimensions=[256, 256, 128])
show(img_hi, "vol_hi", representation="Volume",
     color_by="pressure", lut="cool_to_warm")
''',
        "elevation": '''\
# Color a surface mesh by Z height:
surf = surface(input=data)
elev = elevation(input=surf,
                 low_point=(0, 0, 0),
                 high_point=(0, 0, 200))
show(elev, "terrain", color_by="Elevation",
     scalar_range=(0, 200), lut="terrain")
''',
        "outline": '''\
# Add a bounding-box wireframe as a reference frame:
box = outline(input=data)
show(box, "bbox", color=(1, 1, 1), opacity=0.3)

# Combine with other actors:
show(iso, "flame", color_by="temperature", lut="fire")
show(outline(input=data), "frame", color=(0.5, 0.5, 0.5), opacity=0.2)
''',
        "warp_vector": '''\
# Exaggerate structural deformation by 10x:
warped = warp_vector(input=data, ScaleFactor=10.0)
show(warped, "deformed", color_by="displacement_mag")

# Subtle deformation (scale < 1):
subtle = warp_vector(input=data, ScaleFactor=0.5)
show(subtle, "slight_deform", color_by="von_mises_stress")
''',
        "warp_scalar": '''\
# Create terrain relief from elevation data:
surf = surface(input=data)
elev = elevation(input=surf, low_point=(0,0,0), high_point=(0,0,200))
relief = warp_scalar(input=elev, ScaleFactor=5.0)
show(relief, "terrain_3d", color_by="Elevation", lut="terrain")
''',
        "show": '''\
# Surface coloring by field:
show(data, "temperature",
     color_by="temperature", scalar_range=(300, 1200),
     lut="heat", scalar_bar="Temperature (K)")

# Volume rendering with opacity transfer function:
show(region, "vol",
     representation="Volume",
     color_by="temperature", scalar_range=(300, 1200),
     lut="fire",
     opacity_function=[(300,0),(600,0.02),(800,0.1),(1200,0.5)],
     gradient_opacity=True, volume_resolution=200)

# Solid color with specular highlight:
show(iso, "surface",
     color=(0.9, 0.6, 0.2), opacity=0.8,
     specular=0.5, specular_power=30)

# Color by a single component of a vector field:
show(vel, "updraft", color_by="velocity", component="z",
     scalar_range=(-5, 20), lut="cool_to_warm",
     scalar_bar="W velocity (m/s)")
''',
        "camera": '''\
# Embed camera in pipeline for reproducible exports only.
# For interactive sessions use set_suggested_camera() or set_camera() instead.
camera(position=(500, -800, 300),
       focal_point=(500, 500, 50),
       up=(0, 0, 1))

# Just zoom in without moving:
camera(zoom=1.5)

# Top-down view:
camera(position=(500, 500, 1000),
       focal_point=(500, 500, 0),
       up=(0, 1, 0))
''',
        "background": '''\
# Named presets (default is "dark"):
background("dark")     # dark blue/charcoal (default, great for colorful data)
background("light")    # soft gray (good for solid surfaces)
background("black")    # pure black (maximum contrast)
background("white")    # white (papers/publications)

# Or an explicit RGB triple:
background(0.05, 0.05, 0.1)
''',
        "title": '''\
# Add a descriptive title overlay:
title("Wildfire Simulation — t = 30 s",
      position="top", font_size=20, color=(1, 1, 1))

# Bottom label (e.g. parameters):
title("Threshold: T > 500 K | Resolution: 256³",
      position="bottom", font_size=14, color=(0.8, 0.8, 0.8))
''',
        "annotate": '''\
# Label the world-space origin and an axis point:
annotate(0, 0, 0, "origin")
annotate(1, 0, 0, "x-axis", color="red")

# Label a fire-front feature with a hex color and larger font:
annotate(0, 0, 50, "fire front", color="#ff8800", font_size=16)

# Mark a sphere center with a tuple color:
data = source("vtkSphereSource", Radius=1.0, Center=(2, 3, 0))
show(data)
annotate(2, 3, 0, "sphere center", color=(0.2, 1.0, 0.4))
''',
    }

    # Normalize the form name
    form_lower = form.strip().lower()

    # Collect all public methods from PipelineBuilder
    builder_methods = {
        name: func
        for name, func in inspect.getmembers(PipelineBuilder, predicate=inspect.isfunction)
        if not name.startswith("_")
    }

    # Find matching method (exact or case-insensitive)
    matched_name = None
    matched_func = None
    if form in builder_methods:
        matched_name = form
        matched_func = builder_methods[form]
    else:
        for name, func in builder_methods.items():
            if name.lower() == form_lower:
                matched_name = name
                matched_func = func
                break

    if matched_name is None:
        available = sorted(builder_methods.keys())
        return (
            f"Unknown DSL form '{form}'.\n\n"
            f"Available forms: {', '.join(available)}\n\n"
            "Use get_dsl_overview() for a grouped overview with descriptions."
        )

    # Build the signature string (skip 'self')
    try:
        sig = inspect.signature(matched_func)
        params = []
        for pname, param in sig.parameters.items():
            if pname == "self":
                continue
            annotation = ""
            if param.annotation is not inspect.Parameter.empty:
                ann = param.annotation
                if hasattr(ann, "__name__"):
                    annotation = f": {ann.__name__}"
                else:
                    annotation = f": {str(ann).replace('typing.', '')}"
            default = ""
            if param.default is not inspect.Parameter.empty:
                default = f" = {param.default!r}"
            params.append(f"{pname}{annotation}{default}")
        sig_str = "(" + ", ".join(params) + ")"
    except (ValueError, TypeError):
        sig_str = "(...)"

    # Get docstring
    doc = inspect.getdoc(matched_func) or ""

    # Related forms lookup
    _RELATED = {
        "source": ["raw_source", "filter"],
        "raw_source": ["source", "show"],
        "filter": ["source", "show"],
        "threshold": ["contour", "extract_region", "show"],
        "contour": ["threshold", "show"],
        "slice": ["clip", "show"],
        "clip": ["clip_box", "clip_sphere", "slice"],
        "clip_box": ["clip", "clip_sphere"],
        "clip_sphere": ["clip", "clip_box"],
        "extract_region": ["extract_grid", "clip_box", "threshold"],
        "extract_grid": ["extract_region"],
        "surface": ["smooth", "show"],
        "smooth": ["surface", "show"],
        "make_vector": ["curl_vector", "curl_magnitude", "compute_magnitude"],
        "compute_magnitude": ["make_vector"],
        "curl_vector": ["make_vector", "gradient", "curl_magnitude"],
        "curl_magnitude": ["make_vector", "gradient", "curl_vector"],
        "gradient": ["compute_gradient_magnitude", "curl_vector", "curl_magnitude"],
        "compute_gradient_magnitude": ["gradient", "show"],
        "extract_component": ["make_vector", "show"],
        "calculator": ["make_vector", "gradient"],
        "stream_tracer": ["line_seeds", "plane_seeds", "tube", "make_vector"],
        "tube": ["stream_tracer", "show"],
        "glyph": ["mask_points", "make_vector"],
        "mask_points": ["glyph", "stream_tracer"],
        "line_probe": ["probe", "slice"],
        "cell_to_point": ["point_to_cell", "show"],
        "point_to_cell": ["cell_to_point"],
        "probe": ["line_probe", "resample_to_image"],
        "resample_to_image": ["probe", "show"],
        "elevation": ["show", "surface"],
        "outline": ["show", "clip_box"],
        "warp_vector": ["warp_scalar", "show"],
        "warp_scalar": ["warp_vector", "elevation"],
        "show": ["camera", "background"],
        "camera": ["show", "background"],
        "background": ["camera", "show"],
        "title": ["annotate", "show", "background"],
        "annotate": ["title", "axes", "show"],
        "axes": ["annotate", "show", "background"],
    }
    related = _RELATED.get(matched_name, [])

    # Cross-reference notes for easily confused forms
    _CROSS_REFS = {
        "extract_region": (
            "Note: extract_region crops by spatial bounds or grid index (VOI) and is "
            "specific to structured grids. For planar cross-sections use slice(); for "
            "half-space or box crops on any dataset use clip() or clip_box()."
        ),
        "slice": (
            "Note: slice() cuts along any arbitrary plane. For axis-aligned sub-regions "
            "of structured grids (by physical bounds or grid indices), use extract_region() instead."
        ),
        "compute_magnitude": (
            "Note: compute_magnitude() computes the scalar magnitude (length) of a vector "
            "from its components, producing a single scalar field. "
            "curl_vector() / curl_magnitude() compute the curl of a velocity field — "
            "a measure of rotation, not speed. "
            "Use compute_magnitude() for speed/intensity; use curl_vector() or "
            "curl_magnitude() for rotational analysis."
        ),
        "curl_vector": (
            "Note: curl_vector() returns the full 3-component vorticity vector. "
            "Use curl_magnitude() if you only need the scalar rotation intensity. "
            "Both require a 3-component vector input (use make_vector() first if needed)."
        ),
        "curl_magnitude": (
            "Note: curl_magnitude() returns a scalar field equal to |curl(v)|. "
            "Use curl_vector() if you need the full 3-component vorticity vector. "
            "Both require a 3-component vector input (use make_vector() first if needed)."
        ),
    }

    cross_ref = _CROSS_REFS.get(matched_name)

    lines = [
        f"=== DSL Form: {matched_name}{sig_str} ===",
        "",
    ]

    if doc:
        lines += [doc, ""]

    example = _EXAMPLES.get(matched_name)
    if example:
        lines += [
            "--- Example ---",
            "```python",
            example.rstrip(),
            "```",
            "",
        ]

    if related:
        lines += [
            f"--- Related forms: {', '.join(related)} ---",
            'Use get_dsl_reference(form="form-name") to look up any of these.',
            "",
        ]

    if cross_ref:
        lines += [
            "--- See also ---",
            cross_ref,
            "",
        ]

    lines += [
        "DSL forms are used in pipeline .py files executed by wait_for_pipeline().",
        "Every pipeline file must begin with `from siva.spec_api import *` as its",
        "first statement (an optional module docstring may precede it); it makes",
        "the DSL forms available. The snippet above assumes it.",
        "Use get_dsl_overview() to see all available forms with descriptions.",
    ]

    return "\n".join(lines)


def _register_view(name, renderer):
    """Make *renderer* the view *name*, focus it, and start its hot reload."""
    global _current_view
    ctx = ViewContext(name, renderer)
    ctx.history_dir.mkdir(parents=True, exist_ok=True)
    _views[name] = ctx
    _current_view = name
    ctx.start_hot_reload()
    return ctx


def _start_main_view():
    """Create the "main" view; in TRAME mode, also publish the view index."""
    global _view_index
    ctx = _register_view("main", _renderer_factory("main"))
    if _render_mode == RenderMode.TRAME:
        from types import SimpleNamespace
        from .trame_backend import shared_app
        logger.info("SIVA trame server ready; main view at %s",
                    getattr(ctx.renderer, "url", None))
        app = shared_app()
        _view_index = SimpleNamespace(
            url=app.index_url, proxy_url=app.index_proxy_url)
        logger.info("SIVA view index ready at %s", _view_index.url)


def _exit_without_finalizing(code):
    """Flush logs, then end the process without finalizing Python.

    A build worker may still be inside a VTK algorithm, which runs without
    the GIL; interpreter finalization would free VTK objects out from under
    it and segfault. Nothing else the server holds needs finalizers to run:
    MCP flushes stdout after every message, and the OS closes windows and
    sockets.
    """
    logging.shutdown()
    os._exit(code)


def main():
    global _args, _render_mode, _trame_port, _trame_host

    # Parse CLI args (only runs when main() is called, not on import)
    _args = _parse_args()

    # Change working directory before anything resolves a path relative to
    # cwd (logging below, the hot-reload watcher, view-*.py, screenshots).
    _apply_workdir(_args.workdir)

    if _args.trame:
        # Fail fast with an actionable message if the extra isn't installed,
        # rather than crashing mid-render later.
        try:
            import trame  # noqa: F401
            from trame.widgets.vtk import VtkRemoteView  # noqa: F401
        except ImportError as e:
            sys.exit(
                "--trame requires the 'trame' extra, which is not installed. "
                "Install it with:\n    uv sync --extra trame"
                "\n(or: pip install 'siva[trame]')\n"
                f"(import error: {e})"
            )
        _render_mode = RenderMode.TRAME
        _trame_port = _args.trame_port
        _trame_host = _args.trame_host
    elif _args.headless_interactive:
        _render_mode = RenderMode.HEADLESS_INTERACTIVE
    elif _args.offscreen:
        _render_mode = RenderMode.OFFSCREEN
    else:
        _render_mode = RenderMode.INTERACTIVE

    # Set up logging FIRST so crashes during init are captured
    #
    # FastMCP's constructor (executed at import time when `mcp = FastMCP(...)`
    # runs above) already called logging.basicConfig(level="INFO"), which
    # attaches a RichHandler (or a plain StreamHandler if `rich` isn't
    # installed) to the root logger, writing to stderr. That handler has no
    # explicit level of its own (Handler default is NOTSET), so it processes
    # whatever the root logger lets through. If we simply raised the root
    # level to DEBUG below, every DEBUG record from any logger without its
    # own explicit level -- siva's own modules, the mcp framework (which logs
    # every request/response at DEBUG), and noisy third parties like
    # watchdog's fsevents backend on macOS -- would flood straight through
    # that handler to stderr. A client that doesn't continuously drain
    # stderr has only a ~64KB OS pipe buffer; once it fills, the server's own
    # logging write(2) blocks, and since logging happens on the build-worker
    # and event-loop threads, the whole server hangs (this is what hung
    # test_headless_interactive before its harness was taught to drain
    # stderr). So: cap the handler(s) the framework already put on root at
    # INFO -- preserving warnings/errors on stderr while keeping DEBUG
    # detail off the pipe -- before raising root's level for the file
    # handler below.
    for _h in logging.root.handlers:
        _h.setLevel(logging.INFO)

    _log_dir = Path(".siva")
    _log_dir.mkdir(parents=True, exist_ok=True)
    _fh = logging.FileHandler(_log_dir / "server.log")
    _fh.setLevel(logging.DEBUG)
    _fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    # Attach to root logger so both siva and mcp framework logs are captured.
    # The MCP Server already logs every request/response at debug level.
    logging.root.addHandler(_fh)
    logging.root.setLevel(logging.DEBUG)
    logger.setLevel(logging.DEBUG)

    # watchdog's fsevents backend (used for the hot-reload watcher on macOS)
    # logs every filesystem event at DEBUG, which is rarely useful even in
    # the file log and would dominate it. Quiet it specifically rather than
    # lowering the root level, so other DEBUG detail is unaffected.
    logging.getLogger("fsevents").setLevel(logging.INFO)
    logging.getLogger("watchdog").setLevel(logging.INFO)

    try:
        logger.info("Starting SIVA server (mode=%s)", _render_mode.value)

        # VTK lives on the real main thread, which runs an event loop, while
        # MCP is served from a background thread. Every mode needs this on
        # macOS, where VTK's Cocoa backend creates an NSWindow on every
        # Render() -- even offscreen -- and AppKit allows that only on the
        # main thread; it's used unconditionally rather than branching on
        # platform. TRAME's loop is the shared trame/asyncio loop (see
        # siva/trame_backend.py's module docstring), every other mode's is
        # the renderer's work-queue loop (see claim_main_thread()).
        if _render_mode == RenderMode.TRAME:
            from .trame_backend import (
                configure_shared_app, run_shared_loop, stop_shared_loop)

            # One shared trame app serves everything (views, index page,
            # thumbnails) on a single port -- _trame_port pins it, 0
            # auto-picks. Must be configured before the first TrameRenderer
            # exists; the app starts with the first view. The snapshot
            # callable is invoked on aiohttp's request handling; see
            # _view_index_snapshot for why the shallow dict copy makes that
            # safe.
            configure_shared_app(
                port=_trame_port,
                host=_trame_host,
                index_snapshot_fn=_view_index_snapshot,
                session_label=os.getcwd(),
            )
            run_loop, stop_loop = run_shared_loop, stop_shared_loop
            # A TrameRenderer schedules its construction onto the shared
            # loop, so the main view can only be created once the main
            # thread is running it: create it from the MCP thread.
            mcp_thread_setup = _start_main_view
        else:
            # Claim the main thread before the main view exists so its
            # first builds already dispatch here.
            claim_main_thread()
            _start_main_view()
            run_loop, stop_loop = run_event_loop, stop_event_loop
            mcp_thread_setup = None

        mcp_failed = False

        def _mcp_thread():
            nonlocal mcp_failed
            try:
                if mcp_thread_setup is not None:
                    mcp_thread_setup()
                # Returns once the client closes stdin. FastMCP runs sync
                # tools on its own event loop, so a client that disconnects
                # mid-call is noticed when that call returns (tool waits are
                # bounded, e.g. 120s for a build).
                mcp.run()
            except BaseException:  # e.g. SystemExit from a logging handler
                mcp_failed = True
                logger.critical("Server crashed", exc_info=True)
            finally:
                logger.info("MCP session ended; exiting")
                stop_loop()

        threading.Thread(target=_mcp_thread, name="mcp", daemon=True).start()
        try:
            run_loop()
            exit_code = 1 if mcp_failed else 0
        except KeyboardInterrupt:
            exit_code = 130
        except BaseException:
            logger.critical("Server crashed", exc_info=True)
            exit_code = 1
        _exit_without_finalizing(exit_code)
    except Exception:
        logger.critical("Server crashed", exc_info=True)
        raise


if __name__ == "__main__":
    main()
