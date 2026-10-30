"""VTK renderer with interactive (default) or off-screen mode."""

import enum
import logging
import queue
import threading
import vtk

logger = logging.getLogger("siva.renderer")

vtk.vtkObject.GlobalWarningDisplayOff()


class RenderMode(enum.Enum):
    """How the renderer manages threading and display.

    OFFSCREEN: No window or interactor. Work runs inline on the calling
        thread unless an event loop has claimed the main thread (see
        claim_main_thread()), as the MCP server always does -- macOS needs
        even offscreen renders on the main thread. Used by headless agents.
    INTERACTIVE: Opens a window, runs an event loop on the main thread,
        dispatches work via a shared queue. Production mode for humans.
    HEADLESS_INTERACTIVE: Offscreen rendering but with interactive-mode
        threading (event loop + work queue). For testing the threading
        path without a display.
    TRAME: Offscreen rendering served to a browser via a trame
        VtkRemoteView. A dedicated backend thread owns the trame/asyncio
        loop and the VTK objects (see siva/trame_backend.py). Like
        INTERACTIVE, VTK creation is deferred off the constructing thread.
    """
    OFFSCREEN = "offscreen"
    INTERACTIVE = "interactive"
    HEADLESS_INTERACTIVE = "headless_interactive"
    TRAME = "trame"


# Shared work queue: all Renderer instances post here, and the single
# event-loop thread drains it. INTERACTIVE and HEADLESS_INTERACTIVE always
# use it; OFFSCREEN uses it only once claim_main_thread() has been called
# (as server.main() does), and otherwise runs work inline.
_shared_work_queue = queue.Queue()
_main_thread_id = threading.get_ident()  # updated by claim_main_thread()

# True once some thread has claimed the event loop via claim_main_thread().
_loop_claimed = False

# Set by stop_event_loop(); makes run_event_loop() return. One-shot per
# process: never cleared, so a stop requested before the loop has even
# started still takes effect.
_stop_requested = threading.Event()


def claim_main_thread():
    """Make the calling thread the one that owns VTK and runs the event loop.

    From then on every Renderer's dispatch() -- OFFSCREEN included -- hands
    work to this thread via the shared work queue. On macOS every
    vtkRenderWindow.Render() creates an NSWindow, which AppKit only allows
    on the real main thread, even with offscreen rendering; so a server
    whose builds run on worker threads must route offscreen rendering here
    too. Call it before any work can be dispatched (run_event_loop() calls
    it as well).
    """
    global _main_thread_id, _loop_claimed
    _main_thread_id = threading.get_ident()
    _loop_claimed = True


def stop_event_loop():
    """Make run_event_loop() return (threadsafe).

    server.main() calls it once the MCP client disconnects; without it the
    main thread would stay in run_event_loop() forever and the process
    would never exit.
    """
    _stop_requested.set()

# Registry of live Renderer instances. Renderers register themselves on
# initialization and deregister on destroy(). The event loop uses this to
# find any initialized interactor for pumping OS events (desktop mode only).
_live_renderers = []


def _register_renderer(renderer):
    if renderer not in _live_renderers:
        _live_renderers.append(renderer)


def _deregister_renderer(renderer):
    if renderer in _live_renderers:
        _live_renderers.remove(renderer)


def _get_any_interactor():
    """Return any available initialized vtkRenderWindowInteractor.

    The event loop calls this each iteration to find an interactor for
    pumping OS events.  On macOS/Cocoa, any single interactor drains the
    global event queue, so it handles events for all windows.
    """
    for renderer in _live_renderers:
        interactor = renderer._interactor
        if renderer._initialized and interactor:
            return interactor
    return None


def run_event_loop():
    """Process VTK events and queued work in a loop (blocks). Call from main thread.

    Serves every Renderer: drains the shared work queue, and pumps OS events
    through any initialized interactor from the live-renderer registry. On
    macOS/Cocoa, ProcessEvents() drains the global NSApplication event
    queue, so any single interactor handles events for all windows.

    Returns once stop_event_loop() is called. Work still queued then is
    abandoned: the server exits right after.
    """
    claim_main_thread()
    while not _stop_requested.is_set():
        # Drain shared work queue (serves all Renderer instances)
        while not _shared_work_queue.empty():
            try:
                fn, result_queue = _shared_work_queue.get_nowait()
                try:
                    result = fn()
                    result_queue.put((True, result))
                except Exception as e:
                    result_queue.put((False, e))
            except queue.Empty:
                break
        # Pump OS events via any available interactor
        interactor = _get_any_interactor()
        if interactor:
            interactor.ProcessEvents()
        # Sleep ~one frame (~60 fps), waking early to stop.
        _stop_requested.wait(0.016)


class Renderer:
    def __init__(self, width=640, height=800, mode=RenderMode.INTERACTIVE, view_name=None):
        self._mode = mode
        self.view_name = view_name
        self._interactor = None
        self._render_window = None
        self._renderer = None
        self._light_kit = None
        self._initialized = False
        self._destroyed = False  # set by destroy(); VTK is never rebuilt after

        self._actors = {}  # name -> vtkActor or vtkVolume (3D geometry only)
        self._overlay_actors = []  # list of vtkProp2D (title, etc.) added via AddViewProp
        self._overlays = {}  # name -> vtkProp2D (scalar bars, named 2D overlays)
        self._scalar_bars = []  # list of (bar_actor, title_actor) repositioned per render
        self._camera_positioned = False  # True once camera has been explicitly set
        # Initialize immediately unless a mode defers VTK creation to its own
        # thread: INTERACTIVE (main-thread Cocoa window) and TRAME (the trame
        # backend thread) both build VTK later, off the constructing thread.
        if mode not in (RenderMode.INTERACTIVE, RenderMode.TRAME):
            self._ensure_initialized(width, height)

    def _ensure_initialized(self, width=640, height=800):
        """Lazily create the VTK window and renderer on first use."""
        if self._initialized:
            return
        if self._destroyed:
            # E.g. a build still in flight when its view was closed: fail it
            # rather than open a new window for a view that no longer exists.
            raise RuntimeError(f"renderer for view {self.view_name!r} was destroyed")
        self._initialized = True

        self._render_window = vtk.vtkRenderWindow()
        self._render_window.SetSize(width, height)

        if self._mode == RenderMode.INTERACTIVE:
            window_name = f"SIVA — {self.view_name}" if self.view_name else "SIVA"
            self._render_window.SetWindowName(window_name)
        else:
            self._render_window.SetOffScreenRendering(True)

        self._renderer = vtk.vtkRenderer()
        self._renderer.SetBackground(0.15, 0.15, 0.2)
        self._render_window.AddRenderer(self._renderer)

        self._light_kit = vtk.vtkLightKit()
        self._light_kit.AddLightsToRenderer(self._renderer)

        self._renderer.AddObserver("StartEvent", self._reposition_scalar_bars)

        if self._mode == RenderMode.TRAME:
            # vtkRenderWindowInteractor's Initialize() creates a native
            # window on some platforms (e.g. Cocoa's NSWindow on macOS),
            # which must happen on the main thread. The trame backend
            # owns VTK from a dedicated non-main thread, so use the
            # platform-independent generic interactor instead — trame-vtk
            # only needs render_window.GetInteractor() to return something
            # with an interactor style; it never needs native windowing.
            self._interactor = vtk.vtkGenericRenderWindowInteractor()
            self._interactor.SetRenderWindow(self._render_window)
            self._interactor.SetInteractorStyle(
                vtk.vtkInteractorStyleTrackballCamera()
            )
            self._interactor.Initialize()
        elif self._mode != RenderMode.OFFSCREEN:
            self._interactor = vtk.vtkRenderWindowInteractor()
            self._interactor.SetRenderWindow(self._render_window)
            self._interactor.SetInteractorStyle(
                vtk.vtkInteractorStyleTrackballCamera()
            )
            self._interactor.Initialize()

        _register_renderer(self)

    @property
    def mode(self):
        """The RenderMode this renderer was constructed with (read-only)."""
        return self._mode

    @property
    def camera_positioned(self):
        """True once the camera has been explicitly positioned."""
        return self._camera_positioned

    @camera_positioned.setter
    def camera_positioned(self, value):
        self._camera_positioned = value

    def set_size(self, width, height):
        """Set the render window size in pixels."""
        self._ensure_initialized(width, height)
        self._render_window.SetSize(width, height)

    def get_size(self):
        """Return the render window size as (width, height) in pixels."""
        self._ensure_initialized()
        return self._render_window.GetSize()

    def get_visible_bounds(self):
        """Return the bounds of all visible props (xmin, xmax, ymin, ymax, zmin, zmax)."""
        self._ensure_initialized()
        return self._renderer.ComputeVisiblePropBounds()

    def get_active_camera(self):
        """Return the renderer's active vtkCamera."""
        self._ensure_initialized()
        return self._renderer.GetActiveCamera()

    def dispatch(self, fn):
        """Run fn on the thread that owns the renderer / VTK objects.

        If already on that thread (or in OFFSCREEN mode with no claimed event
        loop), run directly. Otherwise queue it via the shared work queue and
        block until complete."""
        if threading.get_ident() == _main_thread_id or (
                self._mode == RenderMode.OFFSCREEN and not _loop_claimed):
            return fn()
        logger.debug("Queuing work to main thread")
        result_queue = queue.Queue()
        _shared_work_queue.put((fn, result_queue))
        ok, result = result_queue.get()
        if ok:
            logger.debug("Main-thread work completed")
            return result
        else:
            logger.error("Main-thread work raised: %s", result)
            raise result

    def run_event_loop(self):
        """Run the shared event loop (see module-level run_event_loop())."""
        run_event_loop()

    def clear(self):
        self._ensure_initialized()
        for name, item in self._actors.items():
            if isinstance(item, vtk.vtkVolume):
                self._renderer.RemoveVolume(item)
            else:
                self._renderer.RemoveActor(item)
        self._actors.clear()
        for actor2d in self._overlay_actors:
            self._renderer.RemoveViewProp(actor2d)
        self._overlay_actors.clear()
        for actor2d in self._overlays.values():
            self._renderer.RemoveViewProp(actor2d)
        self._overlays.clear()
        self._scalar_bars.clear()

    def add_overlay_actor(self, actor2d):
        """Add a 2D or billboard overlay actor that will be removed on clear().

        Unlike add_actor(), overlay actors are not keyed by name — they are
        accumulated in a list and cleared as a group during pipeline rebuild.
        This is appropriate for pipeline-generated overlays such as title text
        and 3-D billboard annotations created via the DSL ``annotate()`` form.
        """
        self._ensure_initialized()
        self._overlay_actors.append(actor2d)
        self._renderer.AddViewProp(actor2d)

    def add_scalar_bar(self, name, bar_actor, title_actor):
        """Add a scalar bar + its right-aligned title text, anchored bottom-right.

        Multiple bars stack vertically in registration order. Positions and
        sizes are recomputed per render so the layout is resolution-independent
        and survives window resizes.
        """
        self._ensure_initialized()
        bar_key = f"{name}__bar"
        title_key = f"{name}__title"
        for key in (bar_key, title_key):
            if key in self._overlays:
                self._renderer.RemoveViewProp(self._overlays[key])
        self._overlays[bar_key] = bar_actor
        self._overlays[title_key] = title_actor
        self._renderer.AddViewProp(bar_actor)
        self._renderer.AddViewProp(title_actor)
        self._scalar_bars.append((bar_actor, title_actor))

    def _reposition_scalar_bars(self, *args):
        """Recompute scalar-bar positions in pixel units on each render."""
        if not self._scalar_bars or self._render_window is None:
            return
        w, h = self._render_window.GetSize()
        if w <= 0 or h <= 0:
            return
        BAR_W, BAR_H = 220, 18
        LABEL_BAND = 18  # space above bar for tick labels (font + pad)
        ROW_SPACING = BAR_H + LABEL_BAND + 4  # bar + labels + gap
        MARGIN_R, MARGIN_B = 24, 20
        TITLE_GAP = 12
        for i, (bar, title) in enumerate(self._scalar_bars):
            bar_right = w - MARGIN_R
            bar_left = bar_right - BAR_W
            bar_bottom = MARGIN_B + i * ROW_SPACING
            bar.GetPositionCoordinate().SetCoordinateSystemToNormalizedViewport()
            bar.SetPosition(bar_left / w, bar_bottom / h)
            bar.SetWidth(BAR_W / w)
            bar.SetHeight(BAR_H / h)
            title.GetPositionCoordinate().SetCoordinateSystemToDisplay()
            title.GetPositionCoordinate().SetValue(
                bar_left - TITLE_GAP, bar_bottom + BAR_H // 2
            )

    def destroy(self):
        """Tear down the render window and all VTK resources.

        After this call the Renderer is unusable. Used by close_view() to
        actually close the OS window rather than leaving it open and dead.
        """
        self._destroyed = True
        if not self._initialized:
            return
        _deregister_renderer(self)
        self.clear()
        if self._render_window:
            self._render_window.Finalize()
            self._render_window.RemoveRenderer(self._renderer)
            self._render_window = None
        # Pump events after Finalize so the OS processes the window close.
        # On macOS/Cocoa, ProcessEvents drains the global event queue.
        if self._interactor:
            self._interactor.ProcessEvents()
            self._interactor.SetRenderWindow(None)
            self._interactor = None
        self._renderer = None
        self._light_kit = None
        self._initialized = False

    def add_actor(self, name, actor):
        self._ensure_initialized()
        if name in self._actors:
            old = self._actors[name]
            if isinstance(old, vtk.vtkVolume):
                self._renderer.RemoveVolume(old)
            else:
                self._renderer.RemoveActor(old)
        self._actors[name] = actor
        self._renderer.AddActor(actor)

    def add_volume(self, name, volume):
        """Add a vtkVolume to the renderer, replacing any existing item with this name."""
        self._ensure_initialized()
        if name in self._actors:
            old = self._actors[name]
            if isinstance(old, vtk.vtkVolume):
                self._renderer.RemoveVolume(old)
            else:
                self._renderer.RemoveActor(old)
        self._actors[name] = volume
        self._renderer.AddVolume(volume)

    def set_camera(self, position=None, focal_point=None, up=None, zoom=None):
        self._ensure_initialized()
        cam = self._renderer.GetActiveCamera()
        if position is not None:
            cam.SetPosition(*position)
        if focal_point is not None:
            cam.SetFocalPoint(*focal_point)
        if up is not None:
            cam.SetViewUp(*up)
        if zoom is not None:
            cam.Zoom(zoom)
        self._renderer.ResetCameraClippingRange()
        self._camera_positioned = True

    def get_camera_state(self):
        self._ensure_initialized()
        cam = self._renderer.GetActiveCamera()
        return {
            "position": list(cam.GetPosition()),
            "focal_point": list(cam.GetFocalPoint()),
            "up": list(cam.GetViewUp()),
        }

    def set_background(self, r, g, b):
        self._ensure_initialized()
        self._renderer.SetBackground(r, g, b)

    def reset_camera(self):
        self._ensure_initialized()
        self._renderer.ResetCamera()

    def suggest_camera(self, style="overview"):
        """Compute a good camera position based on visible actors.

        Styles:
          overview  - elevated oblique view of the whole scene
          top_down  - bird's eye view looking down
          side      - side view from the south
        """
        if not self._actors:
            return None

        xmin = ymin = zmin = float("inf")
        xmax = ymax = zmax = float("-inf")
        for actor in self._actors.values():
            b = actor.GetBounds()
            if b is None or any(abs(v) > 1e10 for v in b):
                continue
            xmin = min(xmin, b[0])
            xmax = max(xmax, b[1])
            ymin = min(ymin, b[2])
            ymax = max(ymax, b[3])
            zmin = min(zmin, b[4])
            zmax = max(zmax, b[5])

        cx = (xmin + xmax) / 2
        cy = (ymin + ymax) / 2
        cz = (zmin + zmax) / 2
        extent = max(xmax - xmin, ymax - ymin, zmax - zmin)

        if style == "overview":
            return {
                "position": (cx, cy - extent * 1.7, cz + extent * 1.3),
                "focal_point": (cx, cy, cz),
                "up": (0, 0, 1),
            }
        elif style == "top_down":
            return {
                "position": (cx, cy, cz + extent * 2.0),
                "focal_point": (cx, cy, cz),
                "up": (0, 1, 0),
            }
        elif style == "side":
            return {
                "position": (cx, cy - extent * 2.0, cz),
                "focal_point": (cx, cy, cz),
                "up": (0, 0, 1),
            }
        return None

    def is_window_closed(self) -> bool:
        """Return True if the OS window was closed by the user.

        Only meaningful in INTERACTIVE mode — in OFFSCREEN and
        HEADLESS_INTERACTIVE modes there is no real OS window, so this
        always returns False.

        Detection relies on vtkRenderWindow.GetMapped(): after the OS
        window is created via Initialize()/Render() it returns 1; if the
        user closes the window (or the OS destroys it) VTK calls
        Finalize() internally and GetMapped() drops back to 0.

        Returns False when:
          - mode is not INTERACTIVE (offscreen / headless)
          - the renderer has never been initialized (window not yet shown)
          - the renderer was explicitly destroyed via destroy()
        """
        if self._mode != RenderMode.INTERACTIVE:
            return False
        if not self._initialized or self._render_window is None:
            return False
        return self._render_window.GetMapped() == 0

    def render(self):
        self._ensure_initialized()
        self._render_window.Render()

    def screenshot(self, path="screenshot.png"):
        """Render and save a screenshot. Writes both PNG (for archival) and JPEG
        (for returning to Claude). The path argument sets the PNG destination;
        the JPEG is written alongside it with a .jpg extension. Returns the JPEG path."""
        self._ensure_initialized()
        self.render()
        w2i = vtk.vtkWindowToImageFilter()
        w2i.SetInput(self._render_window)
        w2i.SetInputBufferTypeToRGB()
        w2i.ReadFrontBufferOff()
        w2i.Update()

        png_path = path if path.endswith(".png") else path + ".png"
        png_writer = vtk.vtkPNGWriter()
        png_writer.SetFileName(png_path)
        png_writer.SetInputConnection(w2i.GetOutputPort())
        png_writer.Write()

        jpg_path = png_path[:-4] + ".jpg"
        jpg_writer = vtk.vtkJPEGWriter()
        jpg_writer.SetFileName(jpg_path)
        jpg_writer.SetQuality(40)
        jpg_writer.SetInputConnection(w2i.GetOutputPort())
        jpg_writer.Write()

        return jpg_path
