"""Trame rendering backend for SIVA.

`TrameRenderer` implements the full public Renderer interface (see
``tests/test_renderer_interface.py``) by serving each view as an interactive
browser view through trame's ``VtkRemoteView`` (server-side VTK rendering
streamed to the browser over a websocket). It reuses the desktop
``Renderer``'s scene-management logic (actors, volumes, scalar bars, camera,
LightKit) and diverges only where trame requires it: window/interactor
creation flags, the event loop, ``dispatch``, ``render``, ``screenshot``, and
teardown.

Architecture
------------
Everything is served from **one port** by **one shared trame server**
(``_SharedTrameApp``): the trame client assets, the websocket, every view,
the view-index page (``/views``) and its thumbnails (``/thumb/<name>``).
This makes SIVA easy to reach from a container or SSH session — forward a
single (optionally pinned via ``--trame-port``) port and every view is
reachable through it.

Per-view addressing uses trame's native multi-layout mechanism rather than
per-view servers: a trame "layout" is just a named Vue template stored in
the server's shared reactive state, and the generic trame client picks
which template to render from the ``ui`` URL query parameter (default
``main``). Each ``TrameRenderer`` therefore registers one ``DivLayout``
(template) named after its view, containing one ``VtkRemoteView`` bound to
its own ``vtkRenderWindow``, and its browser URL is ``/?ui=<view>``. The
SIVA view named ``main`` maps to trame's default template name, so the bare
root URL shows it. Because all views share one reactive state namespace,
everything per-view (the widget ``ref``, the frame-push callable) is kept
on the renderer instance, never in shared/controller slots.

Every ``TrameRenderer`` (every view) shares **one** asyncio event loop
(``run_shared_loop()``, run by ``server.main()`` on the real process main
thread before any view is constructed). The one trame server is scheduled
onto that shared loop (``exec_mode="task"``) rather than getting its own OS
thread. All VTK access from other threads (the MCP thread, the hot-reload
build worker) is marshalled onto that loop's thread via ``dispatch``, which
uses ``asyncio.run_coroutine_threadsafe`` (with a
run-inline-if-already-on-owner check to avoid self-deadlock, mirroring the
desktop work-queue pattern in ``renderer.py``).

This one design is required on macOS and is simply used everywhere, rather
than branching on platform: VTK's Cocoa backend creates a native
``NSWindow`` during *any* ``vtkRenderWindow.Render()`` call -- even with
``SetOffScreenRendering(True)`` and no interactor at all -- and Cocoa
requires that happen on the process main thread. Critically, this includes
renders that **wslink triggers internally** in response to a browser client
connecting or interacting (initial frame push, resize, drag) -- those never
go through SIVA's own ``render()``/``dispatch()`` at all, so no amount of
wrapping SIVA's own call sites would be sufficient; the loop those renders
run on has to *be* the main thread. Since ``server.main()`` is always
called directly from the process entry point (no thread wrapper), whichever
thread calls ``run_shared_loop()`` there already *is* the real main thread
on every platform, so satisfying macOS costs nothing elsewhere -- there is
no Linux-specific per-view-thread path to maintain.

The trame server is embedded with the accommodations documented in the
design investigation:

- ``show_connection_info=False`` and ``open_browser=False`` keep **stdout
  clean** — SIVA speaks MCP JSON-RPC on stdio and any banner would corrupt
  it. All diagnostics go to ``.siva/server.log``.
- ``timeout=0`` disables wslink's default 300s idle auto-shutdown.
- ``host="127.0.0.1"`` binds loopback only by default; ``--trame-host
  0.0.0.0`` (via ``configure_shared_app()``) opts into binding all
  interfaces, needed inside a container whose port is published with
  ``docker run -p`` (published ports arrive on the container's external
  interface, not its loopback).
- ``thread=True`` tells wslink it is not on the main thread (no signal
  handlers) and to create its own event loop there.
- ``port=0`` (the default) auto-picks a free port; ``--trame-port`` pins it
  (via ``configure_shared_app()``). The real port is read back from
  ``server.port`` once the server is ready.

When ``VSCODE_PROXY_URI`` is set (code-server / Coder terminals), the
proxied URL is computed by substituting ``{{port}}`` and reported alongside
the localhost URL, via ``siva.view_index.resolve_url`` (shared with the
view-index page so the substitution logic lives in one place).

The shared server starts with the first view and keeps serving until the
process exits: destroying a view tears down its VTK objects and replaces
its template with a "view closed" notice (pushed live to any connected
browser), but never stops the server — other views stay reachable on the
same port throughout.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import urllib.parse

from .renderer import Renderer, RenderMode
from .view_index import INDEX_ROUTE, resolve_url

logger = logging.getLogger("siva.renderer")

# The shared loop every TrameRenderer schedules its work onto (see the
# module docstring). Created by run_shared_loop(), which server.main()
# calls before constructing any TrameRenderer.
_shared_loop = None
_shared_loop_thread_id = None
_shared_loop_ready = threading.Event()

# Configuration consumed by the shared trame app when it starts (i.e. when
# the first TrameRenderer is constructed). server.main() sets this via
# configure_shared_app() beforehand; the defaults suit tests that construct
# TrameRenderer directly (auto-picked port, no index page).
_app_config = {
    "port": 0,
    "host": "127.0.0.1",
    "index_snapshot_fn": None,
    "session_label": "SIVA",
}


def configure_shared_app(port=0, host="127.0.0.1", index_snapshot_fn=None,
                         session_label="SIVA"):
    """Configure the single shared trame app before any view exists.

    Must be called before the first ``TrameRenderer`` is constructed (the
    app starts lazily with the first view and reads this configuration
    exactly once). *port* pins the one TCP port everything is served on
    (0 = auto-pick). *index_snapshot_fn* / *session_label*, when given,
    mount the view-index page at ``/views`` (and thumbnails at
    ``/thumb/<name>``) on the same app — see ``siva.view_index``.
    """
    _app_config.update(
        port=port,
        host=host,
        index_snapshot_fn=index_snapshot_fn,
        session_label=session_label,
    )


def shared_app():
    """Return the process-wide ``_SharedTrameApp`` (None before any view)."""
    return _SharedTrameApp._instance


def run_shared_loop():
    """Run the one shared trame event loop forever (blocks the caller).

    Called by server.main() on the real process main thread. Every
    TrameRenderer constructed afterward schedules its trame work onto
    this loop rather than spinning up its own thread, so every render it
    ever triggers -- including ones wslink issues internally in response
    to browser interaction -- runs on this thread, satisfying Cocoa's
    main-thread requirement on macOS (see module docstring).
    """
    global _shared_loop, _shared_loop_thread_id
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _shared_loop = loop
    _shared_loop_thread_id = threading.get_ident()
    _shared_loop_ready.set()
    loop.run_forever()


def stop_shared_loop():
    """Stop the shared loop (threadsafe), letting ``run_shared_loop()`` return.

    Called by server.main()'s MCP thread after the MCP client disconnects;
    without this the main thread would block in
    ``run_shared_loop()`` forever and the process would never exit. The
    shared trame server dies with the loop (and the process) — it is never
    stopped individually.
    """
    loop = _shared_loop
    if loop is not None and not loop.is_closed():
        loop.call_soon_threadsafe(loop.stop)


class _SharedTrameApp:
    """The one trame server all views share, plus its template registry.

    Only ever touched from the shared loop's thread. Created lazily by the
    first ``TrameRenderer``; starts serving on ``_app_config['port']`` and
    keeps serving until the process exits.
    """

    _instance = None

    @classmethod
    def instance(cls):
        """Return the singleton, creating it on first use (loop thread only)."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        from trame.app import get_server

        self.server = get_server("siva", client_type="vue3")
        self.server.controller.on_server_bind.add(self._on_bind)
        self.server.controller.on_server_ready.add(self._on_ready)
        self._ready = asyncio.Event()
        self._started = False
        self.port = None
        self.url = None
        self.proxy_url = None
        self.index_url = None
        self.index_proxy_url = None
        # template_name -> view_name, for every live (not yet destroyed)
        # view. Guards template-name collisions after sanitization.
        self._templates = {}

    # -- template names ----------------------------------------------------

    def claim_template_name(self, view_name):
        """Reserve a template name for *view_name* and return it.

        Template names become state keys and URL query values, so they are
        sanitized to ``[A-Za-z0-9_-]``. Distinct view names that sanitize to
        the same template name get a numeric suffix. The SIVA view "main"
        maps to trame's default template name ("main"), so the bare root
        URL renders it.
        """
        base = re.sub(r"[^A-Za-z0-9_-]", "_", view_name or "view") or "view"
        name = base
        i = 2
        while name in self._templates:
            name = f"{base}-{i}"
            i += 1
        self._templates[name] = view_name
        return name

    def release_template_name(self, template_name):
        """Free *template_name* (view destroyed); the name may be reused."""
        self._templates.pop(template_name, None)

    # -- lifecycle -----------------------------------------------------------

    async def ensure_started(self):
        """Start the server (idempotent) and wait until it is listening."""
        if not self._started:
            self._started = True
            # "task" schedules the server onto the already-running shared
            # loop and returns immediately; readiness comes via
            # on_server_ready.
            self.server.start(
                exec_mode="task",
                thread=True,
                port=_app_config["port"],
                host=_app_config["host"],
                open_browser=False,
                show_connection_info=False,
                timeout=0,
            )
        await self._ready.wait()

    def _on_bind(self, wslink_server, **_kwargs):
        """Mount the view-index routes on the app before it starts serving.

        wslink hands us its aiohttp application here; adding plain HTTP
        routes to it is how everything (index page included) shares the one
        port. Skipped when no snapshot function is configured (tests that
        construct TrameRenderer directly).
        """
        snapshot_fn = _app_config["index_snapshot_fn"]
        if snapshot_fn is None:
            return
        from .view_index import add_index_routes

        add_index_routes(wslink_server.app, snapshot_fn,
                         _app_config["session_label"])

    def _on_ready(self, **_kwargs):
        """Record port/URLs once the server is listening."""
        self.port = self.server.port
        self.url, self.proxy_url = resolve_url(self.port)
        self.index_url = self.url + INDEX_ROUTE.lstrip("/")
        if self.proxy_url:
            self.index_proxy_url = self.proxy_url + INDEX_ROUTE.lstrip("/")
        logger.info("SIVA trame app serving on port %s (%s)",
                    self.port, self.url)
        if self.proxy_url:
            logger.info("SIVA trame app proxied at %s", self.proxy_url)
        self._ready.set()

    # -- URLs ----------------------------------------------------------------

    def view_urls(self, template_name):
        """Return ``(url, proxy_url)`` for the view behind *template_name*.

        Resolved freshly (rather than from the values cached at server
        startup) so a proxy template that appears in the environment after
        the long-lived shared app started is still honored for new views.
        """
        if self.port is None:
            return None, None
        base, proxy_base = resolve_url(self.port)
        suffix = f"?ui={urllib.parse.quote(template_name)}"
        return base + suffix, (proxy_base + suffix if proxy_base else None)


class TrameRenderer(Renderer):
    """A Renderer backed by a trame ``VtkRemoteView`` served over websocket.

    Constructing a ``TrameRenderer`` schedules its VTK objects onto the
    shared event loop and registers the view as a template on the shared
    trame server (starting the server if this is the first view -- see the
    module docstring and ``run_shared_loop()``). The constructor blocks
    until the server is ready (or raises if startup fails).
    """

    def __init__(self, width=1920, height=1080, view_name=None,
                 startup_timeout=30):
        # Set up all base-class attributes (mode=TRAME defers VTK creation —
        # see Renderer.__init__ — so nothing touches VTK on the constructing
        # thread; _async_build() builds it on the shared loop below).
        super().__init__(width=width, height=height, mode=RenderMode.TRAME,
                         view_name=view_name)

        self._siva_size = (width, height)

        self._app = None
        self._view = None
        self._view_update = None
        self._template_name = None
        self._loop = None
        self._owner_thread_id = None
        self._url = None
        self._proxy_url = None

        if not _shared_loop_ready.wait(timeout=startup_timeout):
            raise RuntimeError(
                "Trame shared event loop is not running -- server.main() "
                "must call trame_backend.run_shared_loop() before "
                "constructing a TrameRenderer"
            )
        self._owner_thread_id = _shared_loop_thread_id
        self._loop = _shared_loop
        fut = asyncio.run_coroutine_threadsafe(self._async_build(), _shared_loop)
        try:
            fut.result(timeout=startup_timeout)
        except Exception as e:
            raise RuntimeError(
                f"Trame view '{view_name}' failed to start: {e}"
            ) from e

    # -- shared loop ---------------------------------------------------------

    async def _async_build(self):
        """Build VTK + register this view on the shared trame server.

        Runs as a coroutine on ``_shared_loop`` -- i.e. on the real process
        main thread -- so this setup *and* every render the server triggers
        afterward (including ones wslink issues internally in response to
        browser interaction, which never go through SIVA's own
        render()/dispatch()) happen on the one thread Cocoa allows.
        """
        self._ensure_initialized(*self._siva_size)

        from trame.ui.html import DivLayout
        from trame.widgets.vtk import VtkRemoteView

        app = _SharedTrameApp.instance()
        self._app = app
        self._template_name = app.claim_template_name(self.view_name or "view")

        with DivLayout(app.server, template_name=self._template_name):
            self._view = VtkRemoteView(
                self._render_window,
                # All views share one trame server (one reactive state
                # namespace), so the ref must be unique per view -- it keys
                # this view's render window in shared state.
                ref=f"view_{self._template_name}",
                style="position:absolute;top:0;left:0;width:100%;height:100%;",
                # trame's stillRatio defaults to 1 (CSS-pixel resolution),
                # not scaled by devicePixelRatio, so the browser upscales the
                # image on Retina displays and it looks soft. 2 covers most
                # HiDPI displays. SIVA is a single-user local tool (no real
                # bandwidth/latency constraint), so interactiveRatio/Quality
                # match the still values too -- no resolution/JPEG-quality
                # drop while dragging, at the cost of slower frames during
                # interaction on very large scenes.
                still_ratio=2,
                interactive_ratio=2,
                interactive_quality=100,
            )
        # Kept on the instance, never on the shared controller -- a shared
        # controller slot would be clobbered by every subsequent view.
        self._view_update = self._view.update

        await app.ensure_started()
        self._url, self._proxy_url = app.view_urls(self._template_name)
        logger.info("Trame view '%s' serving at %s", self.view_name, self._url)
        if self._proxy_url:
            logger.info("Trame view '%s' proxied at %s",
                        self.view_name, self._proxy_url)

    # -- URLs --------------------------------------------------------------

    @property
    def url(self):
        """The localhost URL where this view is served (None until ready)."""
        return self._url

    @property
    def proxy_url(self):
        """Proxied URL (VSCODE_PROXY_URI substituted), or None."""
        return self._proxy_url

    @property
    def port(self):
        """The TCP port of the shared trame server (same for every view)."""
        return self._app.port if self._app is not None else None

    def view_url(self):
        """Return {"url": ..., "proxy_url": ...} for this view."""
        return {"url": self._url, "proxy_url": self._proxy_url}

    # -- dispatch / event loop --------------------------------------------

    def dispatch(self, fn):
        """Run *fn* on the thread that owns the trame loop and VTK objects.

        Runs inline if already on the owner thread (avoids self-deadlock,
        mirroring the desktop work-queue pattern). Otherwise schedules the
        call on the trame event loop and blocks for the result.
        """
        if threading.get_ident() == self._owner_thread_id:
            return fn()
        loop = self._loop
        if loop is None or loop.is_closed():
            raise RuntimeError(
                "Trame renderer loop is not available (server not ready or "
                "already destroyed)"
            )

        async def _runner():
            return fn()

        future = asyncio.run_coroutine_threadsafe(_runner(), loop)
        return future.result()

    def run_event_loop(self):
        """No-op: present for interface compatibility.

        The event loop is ``run_shared_loop()``, called once by
        ``server.main()`` for the whole process, not per-renderer.
        """
        return None

    # -- rendering ---------------------------------------------------------

    def render(self):
        """Render the VTK scene and push a fresh frame to connected clients."""
        self._ensure_initialized()
        self._render_window.Render()
        self._push_frame()

    def _push_frame(self):
        """Push the current frame to any connected browser clients.

        Safe when no client is connected (view.update() is then a no-op) and
        callable from any thread — marshalled onto the loop when off-thread.
        """
        view_update = self._view_update
        if view_update is None:
            return
        try:
            if threading.get_ident() == self._owner_thread_id:
                view_update()
            elif self._loop is not None and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(view_update)
        except Exception:  # pragma: no cover - defensive
            logger.debug("view_update failed", exc_info=True)

    def set_size(self, width, height):
        """Set the SIVA-intended render window size.

        Tracks the intended size so ``screenshot`` can render at a stable
        resolution even after a browser client has resized the shared window.
        """
        self._siva_size = (width, height)
        super().set_size(width, height)

    def screenshot(self, path="screenshot.png"):
        """Capture a screenshot at the SIVA-intended size, then restore.

        The remote view resizes the shared render window to match the browser
        canvas whenever a client interacts. We save the current size, render
        and capture at the intended SIVA size (via the parent PNG+JPEG
        dual-write path), then restore the previous size so the browser view
        is unaffected.
        """
        self._ensure_initialized()
        saved = tuple(self._render_window.GetSize())
        target = self._siva_size or saved
        if tuple(target) != saved:
            self._render_window.SetSize(*target)
        try:
            return super().screenshot(path)
        finally:
            if tuple(target) != saved:
                self._render_window.SetSize(*saved)

    def is_window_closed(self) -> bool:
        """Always False — a trame view has no closable OS window."""
        return False

    # -- teardown ----------------------------------------------------------

    def destroy(self):
        """Tear down VTK and retire this view's template on the shared loop.

        The shared trame server keeps running (other views stay reachable on
        the same port); this view's template is replaced with a "view
        closed" notice, which trame pushes live to any browser tab showing
        it.
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            return

        async def _teardown():
            try:
                Renderer.destroy(self)
            except Exception:  # pragma: no cover - defensive
                logger.debug("VTK teardown failed", exc_info=True)
            self._view_update = None
            try:
                self._retire_template()
            except Exception:  # pragma: no cover - defensive
                logger.debug("Template retirement failed", exc_info=True)

        if threading.get_ident() == self._owner_thread_id:
            # Already on the shared loop's own thread -- can't block it
            # waiting on itself; just schedule and let it run.
            asyncio.ensure_future(_teardown())
        else:
            try:
                fut = asyncio.run_coroutine_threadsafe(_teardown(), loop)
                fut.result(timeout=10)
            except Exception:  # pragma: no cover - defensive
                logger.debug("Trame view teardown failed", exc_info=True)

    def _retire_template(self):
        """Replace this view's template with a closed notice and free its name."""
        if self._app is None or self._template_name is None:
            return
        from trame.ui.html import DivLayout
        from trame.widgets import html

        with DivLayout(self._app.server, template_name=self._template_name):
            html.Div(
                f"SIVA view '{self.view_name}' has been closed.",
                style="font-family:sans-serif;color:#888;margin:2rem;",
            )
        self._app.release_template_name(self._template_name)
        self._template_name = None
