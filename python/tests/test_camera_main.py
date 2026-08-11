"""Tests for camera/main.py's CameraProcess — M3 build item 2 (doc section 21).

Run from the repo root:

    python -m unittest discover -s python/tests -v

Driven entirely against `capture.FakeCapture` and a fake JPEG encoder, the
same "no hardware, no subprocess" discipline `capture.py`'s own tests use —
see that module's docstring. The control link is a real `wire.Server`
fixture, following `test_stub.py`'s reasoning: readiness ordering is
exactly the property a mock would get right by construction.
"""

import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch  # noqa: E402

from hotpot.camera import capture  # noqa: E402
from hotpot.camera import main as camera_main  # noqa: E402
from hotpot.camera.main import CameraProcess  # noqa: E402
from hotpot.common import atomicio, framebus, log as hlog, wire  # noqa: E402

DEADLINE = 5.0


def wait_for(pred, timeout=DEADLINE, tick=0.01):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(tick)
    return False


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def unique_shm_name() -> str:
    return "hotpot_test_cam_" + uuid.uuid4().hex[:16]


def fake_encode(frame_bytes, width, height, target_width):
    """No cv2: a deterministic, cheap stand-in that a test can recognise."""
    return b"JPEG:" + frame_bytes[:8]


class CameraProcessCase(unittest.TestCase):
    """A real core-side wire.Server, a FakeCapture, a throwaway shm ring
    and settings file — everything CameraProcess touches, isolated."""

    def setUp(self):
        hlog.reset()
        self.addCleanup(hlog.reset)

        self.sink_msgs = []
        self.sink_connected = []
        self._lock = threading.Lock()
        self.server = wire.Server(
            "127.0.0.1", 0,
            on_message=self._on_message, on_connect=self._on_connect,
            name="core")
        self.core_port = self.server.start()
        self.addCleanup(self.server.stop)

        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.settings_path = Path(self.tmpdir.name) / "camera_settings.json"

        self.cfg = {
            "camera": {"device": "/dev/video0", "capture": [8, 4], "fps": 30,
                      "mjpeg_fps": 1000, "mjpeg_width": 8},
            "of": {"field_level": 0.75},
        }
        self.cap = capture.FakeCapture(width=8, height=4)
        self.proc = CameraProcess(
            self.cfg, self.cap,
            settings_path=self.settings_path, encode_jpeg=fake_encode,
            core_host="127.0.0.1", core_port=self.core_port,
            bind_host="127.0.0.1", shm_name=unique_shm_name(),
            mjpeg_port=free_port())
        self.addCleanup(self.proc.stop)

    def _on_message(self, conn, msg):
        with self._lock:
            self.sink_msgs.append((conn.who, msg))

    def _on_connect(self, conn):
        with self._lock:
            self.sink_connected.append(conn.who)

    def count(self, t):
        with self._lock:
            return sum(1 for _, m in self.sink_msgs if m.get("t") == t)


class TestStart(CameraProcessCase):

    def test_writes_camera_settings_with_field_level_mirrored_in(self):
        self.proc.start()
        written = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(written["capture"], [8, 4])
        self.assertEqual(written["field_level"], 0.75)
        self.assertIn("exposure_absolute", written)

    def test_opens_a_readable_frame_ring(self):
        self.proc.start()
        reader = framebus.FrameReader(self.proc._shm_name)
        self.addCleanup(reader.close)
        self.assertEqual((reader.width, reader.height), (8, 4))

    def test_connects_to_core_and_heartbeats(self):
        self.proc.start()
        self.assertTrue(wait_for(lambda: "camera" in self.sink_connected))
        self.assertTrue(wait_for(lambda: self.count("hb") >= 1))

    def test_start_leaves_the_ring_writer_and_mjpeg_server_up(self):
        # log.setup() is main()'s job; call it so start()'s log.ready()
        # call (doc section 10.2's readiness line) has somewhere to print.
        hlog.setup("camera")
        self.proc.start()
        self.assertIsNotNone(self.proc.info)
        self.assertIsNotNone(self.proc._writer)
        self.assertIsNotNone(self.proc._mjpeg)


class TestRunForever(CameraProcessCase):

    def test_writes_frames_into_the_shm_ring_and_publishes_mjpeg(self):
        self.proc.start()
        t = threading.Thread(target=self.proc.run_forever, daemon=True)
        t.start()
        self.addCleanup(lambda: (self.proc.stop(), t.join(2.0)))

        reader = framebus.FrameReader(self.proc._shm_name)
        self.addCleanup(reader.close)
        self.assertTrue(wait_for(lambda: reader.read() is not None))

        got = wait_for(lambda: self.proc._frame_holder.snapshot())
        self.assertTrue(got)
        jpeg, frame_id, _ts = got
        self.assertTrue(jpeg.startswith(b"JPEG:"))

        self.proc.stop()
        t.join(2.0)
        self.assertFalse(t.is_alive())

    def test_info_snapshot_reports_progress(self):
        self.proc.start()
        t = threading.Thread(target=self.proc.run_forever, daemon=True)
        t.start()
        self.addCleanup(lambda: (self.proc.stop(), t.join(2.0)))

        self.assertTrue(wait_for(lambda: self.proc.stats.frames_written > 0))
        info = self.proc._info_snapshot()
        self.assertEqual(info["width"], 8)
        self.assertEqual(info["shm_name"], self.proc._shm_name)
        self.assertGreaterEqual(info["frame_id"], 0)
        self.assertEqual(info["shm_slot"], info["frame_id"] % framebus.DEFAULT_SLOT_COUNT)

        self.proc.stop()
        t.join(2.0)


class DeadCapture:
    """Implements the Capture protocol; read() always fails. Used to prove
    the crash-out-and-let-run.py-restart policy (module docstring)."""

    def open(self):
        return capture.CaptureInfo(width=8, height=4, fps=30.0, fourcc="MJPG",
                                   exposure_absolute=1, white_balance_temperature=1,
                                   focus_absolute=1)

    def read(self):
        return None

    def close(self):
        pass


class TestDeviceDeathCrashesTheLoop(CameraProcessCase):

    def test_too_many_consecutive_none_reads_raises(self):
        self.cap = DeadCapture()
        self.proc = CameraProcess(
            self.cfg, self.cap,
            settings_path=self.settings_path, encode_jpeg=fake_encode,
            core_host="127.0.0.1", core_port=self.core_port,
            bind_host="127.0.0.1", shm_name=unique_shm_name(),
            mjpeg_port=free_port())
        self.addCleanup(self.proc.stop)
        self.proc.start()
        with self.assertRaises(capture.CameraError):
            self.proc.run_forever()


class TestBuildCapture(unittest.TestCase):
    """`_build_capture`'s platform branch — not a doc build item, see
    capture.py's WindowsCapture docstring. `sys.platform` is patched
    rather than actually running on both OSes, the same reasoning
    `test_run.py` gives for patching `os.name` over `sys.platform` checks
    it can't otherwise exercise on one machine."""

    def test_windows_picks_windows_capture(self):
        with patch.object(camera_main.sys, "platform", "win32"):
            cap = camera_main._build_capture(
                {"capture": [64, 48], "fps": 15}, None)
        self.assertIsInstance(cap, capture.WindowsCapture)
        self.assertEqual(cap.device, 0)

    def test_windows_device_index_is_configurable(self):
        with patch.object(camera_main.sys, "platform", "win32"):
            cap = camera_main._build_capture(
                {"capture": [64, 48], "fps": 15, "windows_device_index": 1},
                None)
        self.assertEqual(cap.device, 1)

    def test_linux_picks_v4l2_capture(self):
        with patch.object(camera_main.sys, "platform", "linux"):
            cap = camera_main._build_capture(
                {"device": "/dev/video2", "capture": [64, 48], "fps": 15},
                None)
        self.assertIsInstance(cap, capture.V4L2Capture)
        self.assertEqual(cap.device, "/dev/video2")


if __name__ == "__main__":
    unittest.main()
