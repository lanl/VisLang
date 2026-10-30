"""Tests for the named-view MCP tools: new_view, focus, close_view, list_views."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Server import helpers (same pattern as test_auto_screenshot.py)
# ---------------------------------------------------------------------------

import siva.server as srv  # noqa: E402
from siva.renderer import RenderMode  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeRenderer:
    """Minimal renderer stub for testing views."""

    def __init__(self, name="fake"):
        self._actors = {}
        self._render_calls = 0
        self.name = name
        self.mode = RenderMode.OFFSCREEN  # stub for Renderer.mode

    def clear(self):
        self._actors.clear()

    def render(self):
        self._render_calls += 1

    def dispatch(self, fn):
        return fn()

    def screenshot(self, path):
        return path

    def get_camera_state(self):
        return {"position": [0, 0, 0], "focal_point": [0, 0, 0], "up": [0, 0, 1]}


def _reset_views():
    """Reset view state to a clean slate with one main view backed by a fake renderer."""
    main_renderer = _FakeRenderer("main")
    srv._init_for_test(main_renderer)


# ---------------------------------------------------------------------------
# Closed views
# ---------------------------------------------------------------------------

class TestDestroyedRenderer(unittest.TestCase):
    """A build still in flight when its view is closed can't reopen it.

    close_view() destroys the view's renderer; a build that was already
    computing then renders into it, which used to rebuild the render window
    (a new OS window, in INTERACTIVE mode) for a view that no longer exists.
    """

    def test_no_reinitialization_after_destroy(self):
        from siva.renderer import Renderer
        # INTERACTIVE defers VTK creation, so this never builds a window.
        r = Renderer(mode=RenderMode.INTERACTIVE, view_name="gone")
        r.destroy()
        with self.assertRaises(RuntimeError):
            r.clear()
        self.assertFalse(r._initialized)


# ---------------------------------------------------------------------------
# ViewContext unit tests
# ---------------------------------------------------------------------------

class TestViewContext(unittest.TestCase):
    """Test the ViewContext class directly."""

    def test_init_sets_name(self):
        r = _FakeRenderer()
        ctx = srv.ViewContext("alpha", r)
        self.assertEqual(ctx.name, "alpha")

    def test_init_empty_vtk_objects(self):
        r = _FakeRenderer()
        ctx = srv.ViewContext("beta", r)
        self.assertEqual(ctx.vtk_objects, {})

    def test_init_zero_version(self):
        r = _FakeRenderer()
        ctx = srv.ViewContext("delta", r)
        self.assertEqual(ctx.version, 0)

    def test_history_dir_uses_view_name(self):
        from pathlib import Path
        r = _FakeRenderer()
        ctx = srv.ViewContext("myview", r)
        self.assertIn("myview", str(ctx.history_dir))

    def test_pipeline_file_main_view(self):
        r = _FakeRenderer()
        ctx = srv.ViewContext("main", r)
        self.assertEqual(ctx.pipeline_file, "view-main.py")

    def test_pipeline_file_uses_view_name(self):
        r = _FakeRenderer()
        ctx = srv.ViewContext("closeup", r)
        self.assertEqual(ctx.pipeline_file, "view-closeup.py")

    def test_pipeline_file_arbitrary_name(self):
        r = _FakeRenderer()
        ctx = srv.ViewContext("temperature-detail", r)
        self.assertEqual(ctx.pipeline_file, "view-temperature-detail.py")


# ---------------------------------------------------------------------------
# new_view() tests
# ---------------------------------------------------------------------------

class TestNewView(unittest.TestCase):

    def setUp(self):
        _reset_views()

    def test_creates_new_view(self):
        result = srv.new_view("secondary")
        self.assertIn("secondary", srv._views)

    def test_returns_error_when_no_pipeline_file(self):
        result = srv.new_view("detail")
        self.assertIsInstance(result, list)
        self.assertIn("detail", result[0])

    def test_new_view_becomes_current(self):
        srv.new_view("secondary")
        self.assertEqual(srv._current_view, "secondary")

    def test_duplicate_name_returns_error(self):
        result = srv.new_view("main")
        self.assertIsInstance(result, list)
        self.assertIn("already exists", result[0])

    def test_current_view_unchanged_on_duplicate(self):
        srv.new_view("secondary")
        srv.new_view("secondary")  # duplicate
        self.assertEqual(srv._current_view, "secondary")

    def test_new_view_missing_file_leaves_empty_pipeline(self):
        srv.new_view("fresh")
        ctx = srv._views["fresh"]
        self.assertEqual(ctx.vtk_objects, {})
        self.assertEqual(ctx.current_code, "")
        self.assertEqual(ctx.version, 0)

    def test_new_view_has_independent_renderer(self):
        srv.new_view("secondary")
        ctx_main = srv._views["main"]
        ctx_sec = srv._views["secondary"]
        self.assertIsNot(ctx_main.renderer, ctx_sec.renderer)

    def test_two_new_views_created(self):
        srv.new_view("view_a")
        srv.new_view("view_b")
        self.assertIn("view_a", srv._views)
        self.assertIn("view_b", srv._views)
        self.assertEqual(len(srv._views), 3)  # main + view_a + view_b


# ---------------------------------------------------------------------------
# focus() tests
# ---------------------------------------------------------------------------

class TestFocus(unittest.TestCase):

    def setUp(self):
        _reset_views()
        srv.new_view("secondary")
        srv._current_view = "main"  # reset back to main

    def test_focus_existing_view(self):
        result = srv.focus("secondary")
        self.assertEqual(srv._current_view, "secondary")

    def test_focus_returns_message_with_view_name(self):
        with patch.object(srv, "_auto_screenshot", return_value=None):
            result = srv.focus("secondary")
        self.assertIsInstance(result, str)
        self.assertIn("secondary", result)

    def test_focus_returns_screenshot_when_available(self):
        fake_img = MagicMock()
        with patch.object(srv, "_auto_screenshot", return_value=fake_img):
            result = srv.focus("secondary")
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)
        self.assertIs(result[1], fake_img)

    def test_focus_unknown_view_returns_error(self):
        result = srv.focus("nonexistent")
        self.assertIsInstance(result, str)
        self.assertIn("not found", result)

    def test_focus_unknown_view_does_not_change_current(self):
        srv.focus("nonexistent")
        self.assertEqual(srv._current_view, "main")

    def test_focus_lists_available_views_in_error(self):
        result = srv.focus("nonexistent")
        self.assertIn("main", result)
        self.assertIn("secondary", result)

    def test_focus_main_from_secondary(self):
        srv._current_view = "secondary"
        with patch.object(srv, "_auto_screenshot", return_value=None):
            srv.focus("main")
        self.assertEqual(srv._current_view, "main")


# ---------------------------------------------------------------------------
# close_view() tests
# ---------------------------------------------------------------------------

class TestCloseView(unittest.TestCase):

    def setUp(self):
        _reset_views()
        srv.new_view("secondary")
        srv._current_view = "main"

    def test_close_secondary_view(self):
        result = srv.close_view("secondary")
        self.assertNotIn("secondary", srv._views)

    def test_close_returns_success_message(self):
        result = srv.close_view("secondary")
        self.assertIsInstance(result, str)
        self.assertIn("secondary", result)

    def test_cannot_close_last_view(self):
        # Close secondary first
        srv.close_view("secondary")
        # Now only main remains
        result = srv.close_view("main")
        self.assertIn("only remaining", result)
        self.assertIn("main", srv._views)

    def test_close_nonexistent_view_returns_error(self):
        result = srv.close_view("nonexistent")
        self.assertIsInstance(result, str)
        self.assertIn("not found", result)

    def test_close_current_view_switches_focus(self):
        srv._current_view = "secondary"
        srv.close_view("secondary")
        # Should have switched to "main"
        self.assertEqual(srv._current_view, "main")

    def test_close_non_current_view_preserves_current(self):
        srv._current_view = "main"
        srv.close_view("secondary")
        self.assertEqual(srv._current_view, "main")

    def test_close_reduces_view_count(self):
        srv.new_view("third")
        self.assertEqual(len(srv._views), 3)
        srv.close_view("third")
        self.assertEqual(len(srv._views), 2)


# ---------------------------------------------------------------------------
# list_views() tests
# ---------------------------------------------------------------------------

class TestListViews(unittest.TestCase):

    def setUp(self):
        _reset_views()

    def test_list_with_single_view(self):
        result = srv.list_views()
        self.assertIsInstance(result, str)
        self.assertIn("main", result)

    def test_list_marks_current_view(self):
        result = srv.list_views()
        # Current view should be marked with *
        self.assertIn("*", result)

    def test_list_with_multiple_views(self):
        srv.new_view("second")
        srv.new_view("third")
        result = srv.list_views()
        self.assertIn("main", result)
        self.assertIn("second", result)
        self.assertIn("third", result)

    def test_list_shows_view_count(self):
        srv.new_view("second")
        result = srv.list_views()
        self.assertIn("2", result)

    def test_list_shows_current_name(self):
        srv.new_view("secondary")
        result = srv.list_views()
        # Both views listed, secondary is current
        self.assertIn("secondary", result)

    def test_list_shows_no_pipeline_for_empty_view(self):
        result = srv.list_views()
        self.assertIn("no pipeline", result)

    def test_list_shows_pipeline_info_when_active(self):
        # Set up a fake pipeline on the main view
        fake_alg = MagicMock()
        srv._views["main"].vtk_objects = {"data": fake_alg}
        srv._views["main"].version = 2
        result = srv.list_views()
        self.assertIn("v2", result)
        self.assertIn("1 nodes", result)

    def test_list_empty_views_returns_message(self):
        saved = srv._views
        srv._views = {}
        result = srv.list_views()
        self.assertIn("No views", result)
        srv._views = saved

    def test_list_no_window_closed_flag_when_renderer_has_no_method(self):
        """_FakeRenderer has no is_window_closed — list_views must not crash."""
        result = srv.list_views()
        # No "[window closed]" flag should appear (fake renderer has no method)
        self.assertNotIn("window closed", result)

    def test_list_window_closed_flag_shown_when_renderer_reports_closed(self):
        """list_views appends [window closed] when is_window_closed() returns True."""
        # Attach a stub is_window_closed that returns True to the main renderer
        srv._views["main"].renderer.is_window_closed = lambda: True
        result = srv.list_views()
        self.assertIn("window closed", result)
        self.assertIn("main", result)

    def test_list_no_window_closed_flag_when_renderer_reports_open(self):
        """list_views does not append [window closed] when is_window_closed() returns False."""
        srv._views["main"].renderer.is_window_closed = lambda: False
        result = srv.list_views()
        self.assertNotIn("window closed", result)

    def test_list_window_closed_only_for_affected_view(self):
        """Only the closed view gets the flag, not others."""
        srv.new_view("secondary")
        # Mark only secondary as closed
        srv._views["secondary"].renderer.is_window_closed = lambda: True
        srv._views["main"].renderer.is_window_closed = lambda: False
        result = srv.list_views()
        lines = result.splitlines()
        # Find view-specific lines (they start with "  viewname")
        secondary_line = next(l for l in lines if l.strip().startswith("secondary"))
        main_line = next(l for l in lines if l.strip().startswith("main"))
        self.assertIn("window closed", secondary_line)
        self.assertNotIn("window closed", main_line)


# ---------------------------------------------------------------------------
# view_url() / list_views() view-index integration tests
#
# These fake the trame-mode signals (renderer.url/.proxy_url, srv._view_index)
# rather than starting a real trame server or ViewIndexServer -- the wiring
# in siva/server.py is what's under test, not siva/view_index.py itself
# (see tests/test_view_index.py for that).
# ---------------------------------------------------------------------------

class _FakeViewIndex:
    def __init__(self, url="http://localhost:9000/", proxy_url=None):
        self.url = url
        self.proxy_url = proxy_url


class TestViewUrlDesktopMode(unittest.TestCase):
    """No renderer reports a .url -- desktop/offscreen mode."""

    def setUp(self):
        _reset_views()
        srv._view_index = None

    def test_reports_no_browser_view(self):
        result = srv.view_url()
        self.assertIn("No browser view", result)

    def test_named_lookup_also_reports_no_browser_view(self):
        result = srv.view_url(name="main")
        self.assertIn("No browser view", result)


class TestViewUrlTrameMode(unittest.TestCase):
    """Renderers report .url/.proxy_url and a view index is registered."""

    def setUp(self):
        _reset_views()
        srv._views["main"].renderer.url = "http://localhost:1234/"
        srv._views["main"].renderer.proxy_url = None
        srv._view_index = _FakeViewIndex(url="http://localhost:9000/")
        self.addCleanup(setattr, srv, "_view_index", None)

    def test_no_args_returns_index_url(self):
        result = srv.view_url()
        self.assertIn("http://localhost:9000/", result)

    def test_no_args_without_index_reports_unavailable(self):
        srv._view_index = None
        result = srv.view_url()
        self.assertIn("No view index", result)

    def test_named_view_returns_its_own_url(self):
        result = srv.view_url(name="main")
        self.assertIn("http://localhost:1234/", result)
        self.assertNotIn("http://localhost:9000/", result)

    def test_named_view_reports_proxy_url_when_set(self):
        srv._views["main"].renderer.proxy_url = "https://host.example/proxy/1234/"
        result = srv.view_url(name="main")
        self.assertIn("https://host.example/proxy/1234/", result)

    def test_unknown_named_view_returns_error(self):
        result = srv.view_url(name="nope")
        self.assertIn("not found", result)
        self.assertIn("main", result)

    def test_index_reports_proxy_url_when_set(self):
        srv._view_index = _FakeViewIndex(
            url="http://localhost:9000/",
            proxy_url="https://host.example/proxy/9000/",
        )
        result = srv.view_url()
        self.assertIn("https://host.example/proxy/9000/", result)

    def test_named_view_proxied_url_leads(self):
        # Agents tend to relay the first URL they see; behind code-server the
        # localhost URL is useless to a remote browser, so proxied must lead.
        srv._views["main"].renderer.proxy_url = "https://host.example/proxy/1234/"
        result = srv.view_url(name="main")
        self.assertLess(
            result.index("https://host.example/proxy/1234/"),
            result.index("http://localhost:1234/"),
        )

    def test_index_proxied_url_leads(self):
        srv._view_index = _FakeViewIndex(
            url="http://localhost:9000/",
            proxy_url="https://host.example/proxy/9000/",
        )
        result = srv.view_url()
        self.assertLess(
            result.index("https://host.example/proxy/9000/"),
            result.index("http://localhost:9000/"),
        )

    def test_list_views_proxied_urls_lead(self):
        srv._views["main"].renderer.proxy_url = "https://host.example/proxy/1234/"
        srv._view_index = _FakeViewIndex(
            url="http://localhost:9000/",
            proxy_url="https://host.example/proxy/9000/",
        )
        result = srv.list_views()
        self.assertLess(
            result.index("https://host.example/proxy/9000/"),
            result.index("http://localhost:9000/"),
        )
        self.assertLess(
            result.index("https://host.example/proxy/1234/"),
            result.index("http://localhost:1234/"),
        )

    def test_list_views_includes_index_url(self):
        result = srv.list_views()
        self.assertIn("http://localhost:9000/", result)

    def test_list_views_omits_index_line_when_no_index(self):
        srv._view_index = None
        result = srv.list_views()
        self.assertNotIn("Index page", result)


# ---------------------------------------------------------------------------
# _current_ctx() isolation tests
# ---------------------------------------------------------------------------

class TestCurrentCtxIsolation(unittest.TestCase):
    """Verify that each view has truly independent state."""

    def setUp(self):
        _reset_views()
        srv.new_view("secondary")

    def test_views_have_separate_vtk_objects(self):
        srv._current_view = "main"
        srv._current_ctx().vtk_objects["nodeA"] = "val_main"

        srv._current_view = "secondary"
        self.assertNotIn("nodeA", srv._current_ctx().vtk_objects)

    def test_views_have_separate_current_code(self):
        srv._current_view = "main"
        srv._views["main"].current_code = "main code"

        srv._current_view = "secondary"
        self.assertEqual(srv._current_ctx().current_code, "")

    def test_views_have_separate_version(self):
        srv._views["main"].version = 5
        srv._views["secondary"].version = 0

        srv._current_view = "main"
        self.assertEqual(srv._current_ctx().version, 5)

        srv._current_view = "secondary"
        self.assertEqual(srv._current_ctx().version, 0)

    def test_views_have_separate_vtk_objects(self):
        """Verify that vtk_objects are scoped per-view (not shared)."""
        srv._current_view = "main"
        srv._current_ctx().vtk_objects["node1"] = "obj1"

        srv._current_view = "secondary"
        self.assertNotIn("node1", srv._current_ctx().vtk_objects)


# ---------------------------------------------------------------------------
# Per-view pipeline file tests — wait_for_pipeline uses view's pipeline_file
# ---------------------------------------------------------------------------

class TestSetPipelinePerViewFile(unittest.TestCase):
    """Verify wait_for_pipeline reads from the current view's pipeline_file by default."""

    def setUp(self):
        _reset_views()

    def tearDown(self):
        # Clean up any per-view pipeline files written during tests
        import os
        for fname in ["view-main.py", "view-alpha.py"]:
            if os.path.exists(fname):
                os.unlink(fname)

    def test_wait_for_pipeline_reads_view_main_file(self):
        """wait_for_pipeline() with no args should read view-main.py for the main view."""
        from pathlib import Path
        Path("view-main.py").write_text(
            'data = source("vtkSphereSource")\nshow(data, "s")'
        )
        ctx = srv._views["main"]
        # Call with empty string (the default)
        result = srv.wait_for_pipeline()
        # Should not say file not found; it read view-main.py
        self.assertNotIn("File not found: view-main.py", result if isinstance(result, str) else result[0])

    def test_wait_for_pipeline_default_uses_current_view_pipeline_file(self):
        """The default pipeline file path should match the current view's pipeline_file."""
        ctx = srv._views["main"]
        self.assertEqual(ctx.pipeline_file, "view-main.py")

    def test_per_view_pipeline_file_differs_per_view(self):
        """Different views should have different pipeline file names."""
        srv.new_view("alpha")
        ctx_main = srv._views["main"]
        ctx_alpha = srv._views["alpha"]
        self.assertNotEqual(ctx_main.pipeline_file, ctx_alpha.pipeline_file)
        self.assertEqual(ctx_main.pipeline_file, "view-main.py")
        self.assertEqual(ctx_alpha.pipeline_file, "view-alpha.py")

    def test_wait_for_pipeline_missing_view_file_gives_helpful_error(self):
        """wait_for_pipeline() with no args when the view file doesn't exist should give a clear error."""
        import os
        if os.path.exists("view-main.py"):
            os.unlink("view-main.py")
        result = srv.wait_for_pipeline()
        msg = result if isinstance(result, str) else result[0]
        self.assertIn("view-main.py", msg)
        self.assertIn("File not found", msg)


if __name__ == "__main__":
    unittest.main()
