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


class TestFakeCaptureControls(unittest.TestCase):
    """The doc §12.8 dev-panel control API, on the backend
    `camera/main.py`'s and `mjpeg.py`'s own tests drive — `WindowsCapture`/
    `V4L2Capture` get the real (bigger) knob set exercised below."""

    def test_lists_one_auto_capable_and_one_manual_only_control(self):
        cap = capture.FakeCapture()
        cap.open()
        specs = {s.name: s for s in cap.list_controls()}
        self.assertEqual(set(specs), {"white_balance", "brightness"})
        self.assertTrue(specs["white_balance"].auto_capable)
        self.assertFalse(specs["brightness"].auto_capable)

    def test_initial_states_match_the_open_fixture(self):
        cap = capture.FakeCapture()
        cap.open()
        states = cap.get_control_states()
        self.assertEqual(states["white_balance"],
                         capture.ControlState("white_balance", True, 4600))
        self.assertEqual(states["brightness"],
                         capture.ControlState("brightness", None, 0))

    def test_setting_a_value_on_an_auto_capable_control_switches_it_manual(self):
        cap = capture.FakeCapture()
        cap.open()
        state = cap.set_control("white_balance", value=5000)
        self.assertEqual(state, capture.ControlState("white_balance", False, 5000))

    def test_value_is_clamped_to_the_spec_range(self):
        cap = capture.FakeCapture()
        cap.open()
        state = cap.set_control("brightness", value=999)
        self.assertEqual(state.value, 64)

    def test_auto_true_on_a_manual_only_control_raises(self):
        cap = capture.FakeCapture()
        cap.open()
        with self.assertRaises(capture.CameraError):
            cap.set_control("brightness", auto=True)

    def test_unknown_control_raises(self):
        cap = capture.FakeCapture()
        cap.open()
        with self.assertRaises(capture.CameraError):
            cap.set_control("not_a_control", value=1)


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
                            "focus_absolute": 10,
                            "locked": True})
        exposure, wb, focus, locked = cap._lock_controls()
        self.assertEqual((exposure, wb, focus), (300, 4200, 10))
        self.assertTrue(locked)

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
        exposure, wb, focus, locked = cap._lock_controls()

        self.assertEqual((exposure, wb, focus), (111, 4600, 5))
        # This path DOES leave the device in manual mode, so the values it
        # reports are reproducible and the flag says so.
        self.assertTrue(locked)
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


class TestV4L2CaptureControls(unittest.TestCase):
    """The doc §12.8 dev-panel control API, parsed from real
    `v4l2-ctl --list-ctrls` output (faked here) — the one backend where the
    reported ranges come from the actual device, not a guess."""

    LIST_CTRLS = (
        "                     brightness 0x00980900 (int)    : min=-64 max=64 step=1 default=0 value=0\n"
        "                       contrast 0x00980901 (int)    : min=0 max=64 step=1 default=32 value=32\n"
        "                     saturation 0x00980902 (int)    : min=0 max=128 step=1 default=64 value=64\n"
        "                            hue 0x00980903 (int)    : min=-40 max=40 step=1 default=0 value=0\n"
        "                  exposure_auto 0x009a0901 (menu)   : min=0 max=3 default=3 value=3\n"
        "              exposure_absolute 0x009a0902 (int)    : min=3 max=2047 step=1 default=250 value=250 flags=inactive\n"
        "white_balance_temperature_auto 0x0098090c (bool)   : default=1 value=1\n"
        "      white_balance_temperature 0x0098091a (int)    : min=2800 max=6500 step=10 default=4600 value=4600 flags=inactive\n"
        "                      sharpness 0x0098091b (int)    : min=0 max=6 step=1 default=3 value=3\n"
        "         backlight_compensation 0x0098091c (int)    : min=0 max=1 step=1 default=0 value=0\n"
        "                           gain 0x00980913 (int)    : min=0 max=100 step=1 default=0 value=0\n"
        "                     focus_auto 0x009a090c (bool)   : default=1 value=1\n"
        "                 focus_absolute 0x009a0900 (int)    : min=0 max=255 step=5 default=0 value=0 flags=inactive\n"
    )

    def fake_run(self, cmd, **kwargs):
        if any("--list-ctrls" in a for a in cmd):
            return CompletedProcess(0, stdout=self.LIST_CTRLS)
        return CompletedProcess(0, stdout="")

    @patch("hotpot.camera.capture.subprocess.run")
    def test_lists_ten_controls_with_real_device_ranges(self, run):
        run.side_effect = self.fake_run
        cap = capture.V4L2Capture("/dev/video0", 1920, 1080, 30)
        specs = {s.name: s for s in cap.list_controls()}
        self.assertEqual(set(specs), {
            "white_balance", "exposure", "focus", "brightness", "contrast",
            "saturation", "gain", "sharpness", "hue",
            "backlight_compensation"})
        self.assertEqual((specs["white_balance"].min, specs["white_balance"].max),
                         (2800, 6500))
        self.assertTrue(specs["white_balance"].auto_capable)
        self.assertFalse(specs["brightness"].auto_capable)

    @patch("hotpot.camera.capture.subprocess.run")
    def test_get_control_states_reads_the_real_device_values(self, run):
        run.side_effect = self.fake_run
        cap = capture.V4L2Capture("/dev/video0", 1920, 1080, 30)
        states = cap.get_control_states()
        self.assertEqual(states["exposure"],
                         capture.ControlState("exposure", True, 250))
        self.assertEqual(states["white_balance"],
                         capture.ControlState("white_balance", True, 4600))
        self.assertIsNone(states["brightness"].auto)
        self.assertEqual(states["brightness"].value, 0)

    @patch("hotpot.camera.capture.subprocess.run")
    def test_set_control_manual_issues_the_matching_v4l2_ctl_calls(self, run):
        run.side_effect = self.fake_run
        cap = capture.V4L2Capture("/dev/video0", 1920, 1080, 30)
        cap.set_control("exposure", auto=False, value=500)
        set_calls = [" ".join(c.args[0][1:]) for c in run.call_args_list
                    if any("--set-ctrl" in a for a in c.args[0])]
        self.assertTrue(any("exposure_auto=1" in c for c in set_calls))
        self.assertTrue(any("exposure_absolute=500" in c for c in set_calls))

    @patch("hotpot.camera.capture.subprocess.run")
    def test_set_control_auto_true_uses_the_aperture_priority_value(self, run):
        run.side_effect = self.fake_run
        cap = capture.V4L2Capture("/dev/video0", 1920, 1080, 30)
        cap.set_control("exposure", auto=True)
        set_calls = [" ".join(c.args[0][1:]) for c in run.call_args_list
                    if any("--set-ctrl" in a for a in c.args[0])]
        self.assertTrue(any("exposure_auto=3" in c for c in set_calls))

    @patch("hotpot.camera.capture.subprocess.run")
    def test_manual_only_control_has_no_auto_mode(self, run):
        run.side_effect = self.fake_run
        cap = capture.V4L2Capture("/dev/video0", 1920, 1080, 30)
        with self.assertRaises(capture.CameraError):
            cap.set_control("brightness", auto=True)

    @patch("hotpot.camera.capture.subprocess.run")
    def test_unknown_control_raises(self, run):
        run.side_effect = self.fake_run
        cap = capture.V4L2Capture("/dev/video0", 1920, 1080, 30)
        with self.assertRaises(capture.CameraError):
            cap.set_control("nope", value=1)


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
                            "focus_absolute": 10,
                            "locked": True})
        info = cap.open()
        self.assertEqual(
            (info.exposure_absolute, info.white_balance_temperature,
             info.focus_absolute),
            (-6, 4200, 10))
        self.assertIn((cv2.CAP_PROP_EXPOSURE, -6), fake.set_calls)
        self.assertTrue(info.controls_locked)

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

    @patch("hotpot.camera.capture.time.sleep")
    def test_no_prior_settings_converges_then_locks(self, sleep):
        # Superseded 2026-08-12: this used to leave every auto running
        # forever, which was a deliberate stop-gap (unconverged locking had
        # looked worse than the OS's own camera app, and the DirectShow
        # manual-exposure trigger was unverified). Both blockers were
        # cleared by testing directly on the rig: EXPOSURE's readback moves
        # the picture and forces manual mode by itself with no separate
        # trigger, and a 5s watch after converge-then-lock held at
        # 40.3-40.4 where the auto had been visibly ramping moments before.
        # So this now mirrors V4L2Capture's own converge-then-lock, not the
        # incomplete "touch nothing" version of it.
        import cv2
        fake = FakeCv2Cap(props={
            cv2.CAP_PROP_EXPOSURE: -6, cv2.CAP_PROP_WB_TEMPERATURE: 4600,
            cv2.CAP_PROP_FOCUS: 12})
        cap = capture.WindowsCapture(
            0, 640, 480, 30, video_capture_factory=lambda: fake)
        info = cap.open()
        sleep.assert_called_once()
        touched_props = {prop for prop, _value in fake.set_calls}
        self.assertIn(cv2.CAP_PROP_AUTO_WB, touched_props)
        self.assertIn(cv2.CAP_PROP_AUTOFOCUS, touched_props)
        self.assertIn(cv2.CAP_PROP_EXPOSURE, touched_props)
        self.assertIn(cv2.CAP_PROP_WB_TEMPERATURE, touched_props)
        self.assertEqual(
            (info.exposure_absolute, info.white_balance_temperature,
             info.focus_absolute), (-6, 4600, 12))

    @patch("hotpot.camera.capture.time.sleep")
    def test_a_fresh_lock_reports_controls_locked_true(self, sleep):
        # This IS a real lock now, taken this call and used immediately —
        # not the stale-value-from-a-previous-run pattern that caused the
        # yellow-cast bug. controls_locked=True here is honest.
        import cv2
        fake = FakeCv2Cap(props={
            cv2.CAP_PROP_EXPOSURE: -6, cv2.CAP_PROP_WB_TEMPERATURE: 4600})
        cap = capture.WindowsCapture(
            0, 640, 480, 30, video_capture_factory=lambda: fake)
        info = cap.open()
        self.assertTrue(info.controls_locked)

    @patch("hotpot.camera.capture.time.sleep")
    def test_an_unlocked_prior_is_ignored_and_converges_fresh(self, sleep):
        # The exact shape of the poisoned file from the 2026-08-12 bug:
        # real numbers, no `"locked": true`. Must not be applied verbatim —
        # that is the bug — and must still end up locked, from a fresh
        # convergence, not left on auto forever either.
        import cv2
        fake = FakeCv2Cap(props={
            cv2.CAP_PROP_EXPOSURE: -6, cv2.CAP_PROP_WB_TEMPERATURE: 6500})
        cap = capture.WindowsCapture(
            0, 640, 480, 30, video_capture_factory=lambda: fake,
            prior_settings={"exposure_absolute": None,
                            "white_balance_temperature": 6500,
                            "focus_absolute": None})
        info = cap.open()
        sleep.assert_called_once()
        self.assertTrue(info.controls_locked)


class TestWindowsCaptureControls(unittest.TestCase):
    """The doc §12.8 dev-panel control API. `open()` always locks
    exposure/WB/focus to manual on this fixture (no prior settings, so the
    fresh converge-then-lock path runs) — that starting state is what
    `test_after_open_the_three_tracked_controls_read_as_manual` pins."""

    @patch("hotpot.camera.capture.time.sleep")
    def open_cap(self, sleep):
        import cv2
        fake = FakeCv2Cap(props={
            cv2.CAP_PROP_EXPOSURE: -6, cv2.CAP_PROP_WB_TEMPERATURE: 6500,
            cv2.CAP_PROP_FOCUS: 10, cv2.CAP_PROP_BRIGHTNESS: 0})
        cap = capture.WindowsCapture(
            0, 640, 480, 30, video_capture_factory=lambda: fake)
        cap.open()
        return cap, fake

    def test_list_controls_before_open_raises(self):
        cap = capture.WindowsCapture(0, 640, 480, 30)
        with self.assertRaises(capture.CameraError):
            cap.list_controls()

    def test_lists_ten_controls_including_the_three_tracked_ones(self):
        cap, _fake = self.open_cap()
        names = {c.name for c in cap.list_controls()}
        self.assertEqual(names, {
            "white_balance", "exposure", "focus", "brightness", "contrast",
            "saturation", "gain", "sharpness", "hue",
            "backlight_compensation"})

    def test_after_open_the_three_tracked_controls_read_as_manual(self):
        cap, _fake = self.open_cap()
        states = cap.get_control_states()
        self.assertEqual(states["white_balance"].auto, False)
        self.assertEqual(states["exposure"].auto, False)
        self.assertEqual(states["focus"].auto, False)
        # No auto concept for this one — always None, never False.
        self.assertIsNone(states["brightness"].auto)

    def test_set_control_pins_a_manual_value_and_reads_it_back(self):
        import cv2
        cap, fake = self.open_cap()
        state = cap.set_control("white_balance", auto=False, value=5100)
        self.assertEqual(state, capture.ControlState("white_balance", False, 5100))
        self.assertIn((cv2.CAP_PROP_WB_TEMPERATURE, 5100), fake.set_calls)

    def test_setting_a_value_alone_implies_manual(self):
        cap, _fake = self.open_cap()
        cap.set_control("white_balance", auto=True)   # back to auto first
        state = cap.set_control("white_balance", value=4000)
        self.assertFalse(state.auto)

    def test_auto_true_sets_the_best_effort_directshow_trigger(self):
        # 0.75 is unverified against this machine's actual driver — see
        # CLAUDE.md for the real run this got. This test only pins the
        # value this code sends, not that it works.
        import cv2
        cap, fake = self.open_cap()
        cap.set_control("exposure", auto=True)
        self.assertIn((cv2.CAP_PROP_AUTO_EXPOSURE, 0.75), fake.set_calls)

    def test_focus_auto_reenable_uses_the_autofocus_prop(self):
        import cv2
        cap, fake = self.open_cap()
        cap.set_control("focus", auto=True)
        self.assertIn((cv2.CAP_PROP_AUTOFOCUS, 1), fake.set_calls)

    def test_manual_only_control_has_no_auto_mode(self):
        cap, _fake = self.open_cap()
        with self.assertRaises(capture.CameraError):
            cap.set_control("brightness", auto=True)

    def test_value_is_clamped_to_the_hardcoded_range(self):
        import cv2
        cap, fake = self.open_cap()
        cap.set_control("brightness", value=99999)
        self.assertIn((cv2.CAP_PROP_BRIGHTNESS, 64), fake.set_calls)

    def test_unknown_control_raises(self):
        cap, _fake = self.open_cap()
        with self.assertRaises(capture.CameraError):
            cap.set_control("nope", value=1)


class TestUnlockedPriorsAreNotApplied(unittest.TestCase):
    """The yellow-cast regression, both backends (found 2026-08-12).

    The failure was a loop, not a single bad write: run 1 left auto-WB on
    and read back 6500 K, main.py recorded it, and run 2 read that file and
    turned auto-WB OFF to pin 6500 K under projector light. Every run after
    inherited it, so the cast looked like a camera fault rather than a file.

    What breaks the loop is provenance, so that is what these test: values
    carrying no `"locked": true` must never reach the device.
    """

    @patch("hotpot.camera.capture.time.sleep")
    def test_windows_ignores_priors_recorded_without_the_locked_flag(
            self, sleep):
        # Exactly the poisoned file: real numbers, no provenance. The stale
        # 6500 K must never reach the device — but unlike the earlier fix
        # (which just left the autos on forever), this now converges a
        # FRESH value and locks to that instead, which is the difference
        # between the old regression test and this one.
        import cv2
        fake = FakeCv2Cap(props={
            cv2.CAP_PROP_EXPOSURE: -7, cv2.CAP_PROP_WB_TEMPERATURE: 4200})
        cap = capture.WindowsCapture(
            0, 640, 480, 30, video_capture_factory=lambda: fake,
            prior_settings={"exposure_absolute": None,
                            "white_balance_temperature": 6500,
                            "focus_absolute": None})
        info = cap.open()
        sleep.assert_called_once()
        wb_sets = [v for prop, v in fake.set_calls
                  if prop == cv2.CAP_PROP_WB_TEMPERATURE]
        # AUTO_WB and WB_TEMPERATURE ARE touched (that is the lock this
        # class now performs) — the poisoned 6500 specifically must not be
        # the value they are touched WITH.
        self.assertNotIn(6500, wb_sets)
        self.assertEqual(info.white_balance_temperature, 4200)
        self.assertTrue(info.controls_locked)

    @patch("hotpot.camera.capture.time.sleep")
    @patch("hotpot.camera.capture.subprocess.run")
    def test_v4l2_reconverges_rather_than_applying_unlocked_priors(
            self, run, sleep):
        run.return_value = CompletedProcess(returncode=0, stdout="")
        cap = capture.V4L2Capture(
            "/dev/video0", 1920, 1080, 30,
            prior_settings={"exposure_absolute": 300,
                            "white_balance_temperature": 6500,
                            "focus_absolute": 10})
        cap._lock_controls()
        # The converge-then-lock path ran instead of the apply-verbatim one.
        sleep.assert_called_once()
        set_calls = [" ".join(c.args[0][1:]) for c in run.call_args_list
                     if any("--set-ctrl" in a for a in c.args[0])]
        self.assertTrue(any("white_balance_temperature_auto=1" in c
                            for c in set_calls),
                        "autos were never re-enabled, so the unlocked prior "
                        "was trusted after all")
        self.assertFalse(any("white_balance_temperature=6500" in c
                             for c in set_calls),
                         "the unlocked 6500 K prior reached the device")


if __name__ == "__main__":
    unittest.main()
