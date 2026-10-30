"""The server process exits when its MCP client disconnects, in every mode.

Regression tests for:

- INTERACTIVE and HEADLESS_INTERACTIVE leaking a process per MCP session:
  the main thread sat in the renderer event loop forever after stdin closed.
- OFFSCREEN on macOS aborting on its first build: builds rendered on worker
  threads, and vtkCocoaRenderWindow.Render() creates an NSWindow even
  offscreen, which AppKit aborts the process for off the main thread.
- A segfault at exit when a build was still computing: interpreter
  finalization freed VTK objects out from under the build worker.

Each test launches the real server as a subprocess, drives it over MCP stdio,
closes stdin, and checks the process exits promptly with code 0. INTERACTIVE
mode isn't covered here (it opens real windows); HEADLESS_INTERACTIVE
exercises the same main-thread event loop and MCP-thread topology without
them.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from siva.renderer import RenderMode
from siva import renderer as renderer_mod

try:
    import trame  # noqa: F401
    _HAVE_TRAME = True
except ImportError:
    _HAVE_TRAME = False


def _venv_python():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(here, ".venv", "bin", "python")


_PIPELINE = (
    'from siva.spec_api import *\n'
    'data = source("vtkSphereSource")\n'
    'show(data, "sphere")\n'
)

# Takes a few seconds to build.
_MEDIUM_PIPELINE = (
    'from siva.spec_api import *\n'
    'data = source("vtkSphereSource", ThetaResolution=1000, PhiResolution=1000)\n'
    'd = filter("vtkDecimatePro", data, TargetReduction=0.9)\n'
    'show(d, "medium")\n'
)

# Takes tens of seconds to build: still computing when a test disconnects.
_SLOW_PIPELINE = (
    'from siva.spec_api import *\n'
    'data = source("vtkSphereSource", ThetaResolution=3000, PhiResolution=3000)\n'
    'd = filter("vtkDecimatePro", data, TargetReduction=0.9)\n'
    'show(d, "slow")\n'
)


class _Server:
    """A siva.server subprocess driven over MCP stdio."""

    def __init__(self, workdir, flags):
        self.proc = subprocess.Popen(
            [_venv_python(), "-m", "siva.server", *flags],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=workdir,
        )
        # Drain stderr so the server never blocks writing logs to a full pipe
        # (see test_headless_interactive._drain_stderr).
        self._stderr = []
        self._stderr_thread = threading.Thread(
            target=lambda: [self._stderr.append(l)
                            for l in iter(self.proc.stderr.readline, "")],
            daemon=True,
        )
        self._stderr_thread.start()
        self._next_id = 0
        self._send({
            "jsonrpc": "2.0", "id": "init", "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.1"},
            },
        })
        self.recv("init")
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def stderr(self):
        if self.proc.poll() is not None:
            self._stderr_thread.join(timeout=5)  # read to EOF
        else:
            time.sleep(0.1)  # let the drain thread catch up
        return "".join(self._stderr)

    def _send(self, msg):
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def recv(self, want_id, timeout=60):
        result = [None]

        def _read():
            for line in iter(self.proc.stdout.readline, ""):
                msg = json.loads(line)
                if msg.get("id") == want_id:
                    result[0] = msg
                    return

        t = threading.Thread(target=_read, daemon=True)
        t.start()
        t.join(timeout)
        if result[0] is None:
            raise AssertionError(
                f"No response to {want_id!r} (exit code {self.proc.poll()}). "
                f"stderr:\n{self.stderr()}")
        return result[0]

    def call(self, tool, arguments=None, wait=True):
        """Call *tool*; return its response, or its request id if not *wait*."""
        self._next_id += 1
        self._send({
            "jsonrpc": "2.0", "id": self._next_id, "method": "tools/call",
            "params": {"name": tool, "arguments": arguments or {}},
        })
        if not wait:
            return self._next_id
        resp = self.recv(self._next_id)
        if "error" in resp or resp["result"].get("isError"):
            raise AssertionError(f"{tool} failed: {resp}")
        return resp

    def close_stdin(self):
        self.proc.stdin.close()

    def wait(self, timeout):
        """Return the exit code, or None if still running after *timeout*."""
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def kill(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()


class _ServerExitMixin:
    """Tests run against each mode; subclasses set FLAGS."""

    FLAGS: list = []

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        for name, code in (("main", _PIPELINE), ("second", _PIPELINE),
                           ("medium", _MEDIUM_PIPELINE)):
            self._write_view(name, code)
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        self.server = _Server(self._tmpdir, self.FLAGS)
        self.addCleanup(self.server.kill)

    def _write_view(self, name, code):
        with open(os.path.join(self._tmpdir, f"view-{name}.py"), "w") as f:
            f.write(code)

    def _assert_exits_cleanly(self, timeout=5):
        rc = self.server.wait(timeout)
        self.assertIsNotNone(
            rc, f"server still running {timeout}s after stdin closed "
                f"(leaked process). stderr:\n{self.server.stderr()}")
        self.assertEqual(rc, 0, f"stderr:\n{self.server.stderr()}")

    def test_exits_when_idle(self):
        self.server.close_stdin()
        self._assert_exits_cleanly()

    def test_exits_after_building_and_closing_views(self):
        # Also the macOS offscreen crash regression: rendering a build used
        # to abort the process from a worker thread.
        self.server.call("wait_for_pipeline", {"file": "view-main.py"})
        self.server.call("new_view", {"name": "second"})
        self.server.call("close_view", {"name": "second"})
        self.server.call("list_views")
        self.server.close_stdin()
        self._assert_exits_cleanly()

    def test_exits_when_disconnected_during_hot_reload_build(self):
        # A build triggered by saving the pipeline file, with no tool call
        # waiting on it, is still computing when the process exits.
        self.server.call("wait_for_pipeline", {"file": "view-main.py"})
        self._write_view("main", _SLOW_PIPELINE)
        deadline = time.monotonic() + 10
        while "Build in flight" not in json.dumps(self.server.call("pipeline_status")):
            self.assertLess(time.monotonic(), deadline, "build never started")
            time.sleep(0.2)
        self.server.close_stdin()
        self._assert_exits_cleanly()

    def test_exits_after_call_in_flight_at_disconnect(self):
        # FastMCP only notices EOF once the running (sync) tool call returns.
        # (Whether that call's answer still goes out is up to the MCP SDK,
        # which cancels in-flight requests at EOF; so it isn't checked.)
        self.server.call("new_view", {"name": "medium"}, wait=False)
        self.server.close_stdin()
        self._assert_exits_cleanly(timeout=60)


class TestOffscreenExit(_ServerExitMixin, unittest.TestCase):
    FLAGS = ["--offscreen"]


class TestHeadlessInteractiveExit(_ServerExitMixin, unittest.TestCase):
    FLAGS = ["--headless-interactive"]


@unittest.skipUnless(_HAVE_TRAME, "trame extra not installed")
class TestTrameExit(_ServerExitMixin, unittest.TestCase):
    FLAGS = ["--trame"]


class TestEventLoopStop(unittest.TestCase):
    """Once the main thread is claimed, dispatch() runs work there; the loop
    returns on stop_event_loop().

    Uses a fresh stop event and restores the module's loop state afterward,
    since stop_event_loop() is one-shot per process.
    """

    def setUp(self):
        self._saved = (renderer_mod._stop_requested, renderer_mod._loop_claimed,
                       renderer_mod._main_thread_id)
        renderer_mod._stop_requested = threading.Event()

    def tearDown(self):
        (renderer_mod._stop_requested, renderer_mod._loop_claimed,
         renderer_mod._main_thread_id) = self._saved

    def test_offscreen_dispatch_runs_on_loop_thread_until_stopped(self):
        # The loop runs on this (the main) thread, as in the server: it pumps
        # any live interactor, which Cocoa only allows on the main thread,
        # and earlier tests may have left one registered.
        r = renderer_mod.Renderer(mode=RenderMode.OFFSCREEN)
        self.addCleanup(r.destroy)
        ran_on = []

        def client():
            try:
                ran_on.append(r.dispatch(threading.get_ident))
            finally:
                renderer_mod.stop_event_loop()

        renderer_mod.claim_main_thread()  # as server.main() does up front
        t = threading.Thread(target=client, daemon=True)
        t.start()
        renderer_mod.run_event_loop()  # returns once client() stops it
        t.join(timeout=5)
        self.assertEqual(ran_on, [threading.get_ident()])


if __name__ == "__main__":
    unittest.main()
