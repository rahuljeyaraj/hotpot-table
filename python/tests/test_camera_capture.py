"""Tests for camera/capture.py — M3 build item 2 (doc section 6.6).

Run from the repo root:

    python -m unittest discover -s python/tests -v

`FakeCapture` is exercised directly, no mocking needed — that is the point
of it. `V4L2Capture`'s device/format handling needs real V4L2 hardware and
is not tested here (doc section 21's acceptance test is what proves that,
on the rig); what *is* tested without hardware is the control-locking logic
in `_lock_controls`/`_get_ctrl`/`_run_v4l2ctl`, by faking `subprocess.run`
the same way `test_scale.py` fakes the serial port rather than opening a
real one.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hotpot.camera import capture  # noqa: E402


class CompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ---------------------------------------------------------------------------
# FakeCapture
# ---------------------------------------------------------------------------

class TestFakeCapture(unittest.TestCase):

    def test_open_returns_the_configured_size(self):
        cap = capture.FakeCapture(width=64, height=48)
        info = cap.open()
        self.assertEqual((info.width, info.height), (64, 48))
        self.assertEqual(info.fourcc, "MJPG")

    def test_read_before_open_raises(self):
        cap = capture.FakeCapture()
        with self.assertRaises(capture.CameraError):
            cap.read()

    def test_read_returns_supplied_frames_in_order_then_falls_back(self):
        frames = [b"one", b"two"]
        cap = capture.FakeCapture(width=2, height=1, frames=frames)
        cap.open()
        self.assertEqual(cap.read(), b"one")
        self.assertEqual(cap.read(), b"two")
        # Exhausted: falls back to a flat frame of the right byte length.
        self.assertEqual(len(cap.read()), 2 * 1 * capture.CHANNELS)

    def test_close_is_recorded(self):
        cap = capture.FakeCapture()
        cap.open()
        cap.close()
        self.assertTrue(cap.closed)


# ---------------------------------------------------------------------------
# V4L2Capture control locking, with subprocess.run faked
# ---------------------------------------------------------------------------

class TestRunV4l2Ctl(unittest.TestCase):

    def make(self, **kwargs):
        return capture.V4L2Capture("/dev/video0", 1920, 1080, 30, **kwargs)

    @patch("hotpot.camera.capture.subprocess.run")
    def test_required_call_raises_camera_error_when_binary_missing(self, run):
        run.side_effect = FileNotFoundError("no such file")
        cap = self.make()
        with self.assertRaises(capture.CameraError):
            cap._run_v4l2ctl(["--set-ctrl=exposure_auto=1"], required=True)

    @patch("hotpot.camera.capture.subprocess.run")
    def test_optional_call_returns_none_when_binary_missing(self, run):
        run.side_effect = FileNotFoundError("no such file")
        cap = self.make()
        self.assertIsNone(
            cap._run_v4l2ctl(["--list-formats-ext"], required=False))

    @patch("hotpot.camera.capture.subprocess.run")
    def test_nonzero_exit_raises_when_required(self, run):
        run.return_value = CompletedProcess(returncode=1, stderr="nope")
        cap = self.make()
        with self.assertRaises(capture.CameraError):
            cap._run_v4l2ctl(["--set-ctrl=focus_auto=0"], required=True)

    @patch("hotpot.camera.capture.subprocess.run")
    def test_stdout_is_returned_on_success(self, run):
        run.return_value = CompletedProcess(returncode=0, stdout="hello\n")
        cap = self.make()
        self.assertEqual(
            cap._run_v4l2ctl(["--list-formats-ext"], required=False), "hello\n")


class TestGetCtrl(unittest.TestCase):

    @patch("hotpot.camera.capture.subprocess.run")
    def test_parses_name_colon_value(self, run):
        run.return_value = CompletedProcess(returncode=0, stdout="exposure_absolute: 250\n")
        cap = capture.V4L2Capture("/dev/video0", 1920, 1080, 30)
        self.assertEqual(cap._get_ctrl("exposure_absolute"), 250)

    @patch("hotpot.camera.capture.subprocess.run")
    def test_unparsable_output_returns_none(self, run):
        run.return_value = CompletedProcess(returncode=0, stdout="garbage\n")
        cap = capture.V4L2Capture("/dev/video0", 1920, 1080, 30)
        self.assertIsNone(cap._get_ctrl("exposure_absolute"))


class TestLockControls(unittest.TestCase):

    @patch("hotpot.camera.capture.subprocess.run")
    def test_prior_settings_are_applied_verbatim_not_reread(self, run):
        run.return_value = CompletedProcess(returncode=0, stdout="")
        cap = capture.V4L2Capture(
            "/dev/video0", 1920, 1080, 30,
            prior_settings={"exposure_absolute": 300,
                            "white_balance_temperature": 4200,
                            "focus_absolute": 10})
        exposure, wb, focus = cap._lock_controls()
        self.assertEqual((exposure, wb, focus), (300, 4200, 10))

        set_calls = [c for c in run.call_args_list
                    if any("--set-ctrl" in a for a in c.args[0])]
        self.assertEqual(len(set_calls), 1)
        set_arg = next(a for a in set_calls[0].args[0] if a.startswith("--set-ctrl"))
        self.assertIn("exposure_absolute=300", set_arg)
        self.assertIn("white_balance_temperature=4200", set_arg)
        self.assertIn("focus_absolute=10", set_arg)
        # Never called --get-ctrl: the prior values are trusted outright.
        get_calls = [c for c in run.call_args_list
                    if any("--get-ctrl" in a for a in c.args[0])]
        self.assertEqual(get_calls, [])

    @patch("hotpot.camera.capture.time.sleep")
    @patch("hotpot.camera.capture.subprocess.run")
    def test_no_prior_settings_converges_then_locks_at_read_back_values(
            self, run, sleep):
        def fake_run(cmd, **kwargs):
            args = cmd[1:]
            joined = " ".join(args)
            if "--get-ctrl=exposure_absolute" in joined:
                return CompletedProcess(0, stdout="exposure_absolute: 111\n")
            if "--get-ctrl=white_balance_temperature" in joined:
                return CompletedProcess(0, stdout="white_balance_temperature: 4600\n")
            if "--get-ctrl=focus_absolute" in joined:
                return CompletedProcess(0, stdout="focus_absolute: 5\n")
            return CompletedProcess(0, stdout="")

        run.side_effect = fake_run
        cap = capture.V4L2Capture("/dev/video0", 1920, 1080, 30)
        exposure, wb, focus = cap._lock_controls()

        self.assertEqual((exposure, wb, focus), (111, 4600, 5))
        sleep.assert_called_once()
        set_calls = [" ".join(c.args[0][1:]) for c in run.call_args_list
                    if any("--set-ctrl" in a for a in c.args[0])]
        # First: turn autos on. Last two: lock autos off, then pin the
        # exact read-back numbers.
        self.assertIn("exposure_auto=3", set_calls[0])
        self.assertTrue(any("exposure_auto=1" in c for c in set_calls))
        self.assertTrue(any("exposure_absolute=111" in c for c in set_calls))
        self.assertTrue(any("white_balance_temperature=4600" in c for c in set_calls))
        self.assertTrue(any("focus_absolute=5" in c for c in set_calls))


# ---------------------------------------------------------------------------
# WindowsCapture, with the cv2.VideoCapture object faked (real cv2 constants
# are used — opencv-python-headless is a hard dependency of this repo — only
# the actual device handle is a fake, the same split subprocess-faking gives
# V4L2Capture above).
# ---------------------------------------------------------------------------

class FrameStub:
    """Something with a `.tobytes()`, standing in for the ndarray
    `cv2.VideoCapture.read()` actually returns — avoids pulling numpy into
    this test file for a value nothing here inspects past that one call."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def tobytes(self) -> bytes:
        return self._data


class FakeCv2Cap:
    def __init__(self, *, opened=True, props=None, frames=None):
        self._opened = opened
        self._props = dict(props or {})
        self.set_calls = []
        self._frames = list(frames) if frames is not None else None
        self.released = False

    def isOpened(self):
        return self._opened

    def set(self, prop, value):
        self.set_calls.append((prop, value))
        self._props[prop] = value
        return True

    def get(self, prop):
        return self._props.get(prop, 0)

    def read(self):
        if self._frames is not None:
            if not self._frames:
                return False, None
            return True, self._frames.pop(0)
        return True, FrameStub(b"\x80" * 12)

    def release(self):
        self.released = True


class TestWindowsCaptureOpen(unittest.TestCase):

    def test_not_opened_raises(self):
        cap = capture.WindowsCapture(
            0, 640, 480, 30,
            video_capture_factory=lambda: FakeCv2Cap(opened=False))
        with self.assertRaises(capture.CameraError):
            cap.open()

    def test_reports_the_negotiated_size(self):
        import cv2
        fake = FakeCv2Cap(props={
            cv2.CAP_PROP_FRAME_WIDTH: 320, cv2.CAP_PROP_FRAME_HEIGHT: 240,
            cv2.CAP_PROP_FPS: 30})
        cap = capture.WindowsCapture(
            0, 320, 240, 30, video_capture_factory=lambda: fake)
        info = cap.open()
        self.assertEqual((info.width, info.height), (320, 240))

    def test_read_returns_frame_bytes(self):
        cap = capture.WindowsCapture(
            0, 640, 480, 30,
            video_capture_factory=lambda: FakeCv2Cap(
                frames=[FrameStub(b"one"), FrameStub(b"two")]))
        cap.open()
        self.assertEqual(cap.read(), b"one")
        self.assertEqual(cap.read(), b"two")

    def test_a_failed_read_returns_none_not_an_exception(self):
        cap = capture.WindowsCapture(
            0, 640, 480, 30,
            video_capture_factory=lambda: FakeCv2Cap(frames=[]))
        cap.open()
        self.assertIsNone(cap.read())

    def test_read_before_open_raises(self):
        cap = capture.WindowsCapture(0, 640, 480, 30)
        with self.assertRaises(capture.CameraError):
            cap.read()

    def test_close_releases(self):
        fake = FakeCv2Cap()
        cap = capture.WindowsCapture(
            0, 640, 480, 30, video_capture_factory=lambda: fake)
        cap.open()
        cap.close()
        self.assertTrue(fake.released)


class TestWindowsCaptureLockControls(unittest.TestCase):

    def test_prior_settings_are_set_and_read_back(self):
        import cv2
        fake = FakeCv2Cap(props={
            cv2.CAP_PROP_EXPOSURE: -6, cv2.CAP_PROP_WB_TEMPERATURE: 4200,
            cv2.CAP_PROP_FOCUS: 10})
        cap = capture.WindowsCapture(
            0, 640, 480, 30, video_capture_factory=lambda: fake,
            prior_settings={"exposure_absolute": -6,
                            "white_balance_temperature": 4200,
                            "focus_absolute": 10})
        info = cap.open()
        self.assertEqual(
            (info.exposure_absolute, info.white_balance_temperature,
             info.focus_absolute),
            (-6, 4200, 10))
        self.assertIn((cv2.CAP_PROP_EXPOSURE, -6), fake.set_calls)

    def test_an_unsupported_property_reads_back_as_none_not_zero(self):
        # OpenCV/DirectShow's own "unsupported" signal for .get() is 0 or
        # -1 — the TRAP this guards is reporting that as a real reading
        # (a webcam whose exposure is genuinely 0 is not the same fact as
        # a webcam whose driver cannot answer at all).
        import cv2
        fake = FakeCv2Cap(props={
            cv2.CAP_PROP_EXPOSURE: 0, cv2.CAP_PROP_WB_TEMPERATURE: -1})
        cap = capture.WindowsCapture(
            0, 640, 480, 30, video_capture_factory=lambda: fake)
        info = cap.open()
        self.assertIsNone(info.exposure_absolute)
        self.assertIsNone(info.white_balance_temperature)

    def test_no_prior_settings_still_turns_autos_off(self):
        import cv2
        fake = FakeCv2Cap()
        cap = capture.WindowsCapture(
            0, 640, 480, 30, video_capture_factory=lambda: fake)
        cap.open()
        self.assertIn((cv2.CAP_PROP_AUTO_WB, 0), fake.set_calls)
        self.assertIn((cv2.CAP_PROP_AUTOFOCUS, 0), fake.set_calls)


if __name__ == "__main__":
    unittest.main()
