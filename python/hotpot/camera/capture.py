"""The capture backend: what actually gets pixels and locked settings out
of the webcam (doc section 6.6, M3 build item 2).

`main.py` never touches V4L2 or OpenCV directly — everything it needs is
this module's four-method `Capture` surface (`open`/`read`/`close`, plus the
`CaptureInfo` `open()` returns). That split exists for the same reason
`classifier`/`voice` are built on a `backend_stub.py`/`backend_ei.py` pair
(doc section 19.4, "backend abstraction — mandatory"): this dev machine is
Windows, the deploy target is Linux with a V4L2 device (doc section 1.4),
and nothing in `camera/main.py`, the shm write path, or the MJPEG server
should require real hardware to test. `FakeCapture` is the one this
process's own tests drive; `V4L2Capture` is unverified against a real
webcam until it runs on the rig, the same honest caveat M3.1's framebus.py
carries for the shared-memory ring it is now feeding. `WindowsCapture`,
added later, is the real backend for *this* dev machine — a genuine USB
webcam over DirectShow, not a synthetic `FakeCapture` frame — so the ring/
mjpeg/dev-panel path can be exercised with real frames before the rig is
available. It is not a doc build item and does not replace `V4L2Capture`
as the deploy backend.

Locking exposure/WB/focus — and why it happens here, not by shelling a
one-off script
---------------------------------------------------------------------------
Doc section 6.6 is explicit that changing exposure between the training set
and inference is a classifier accuracy bug that looks like a model problem,
so the values in force at capture time have to be both locked and recorded.
Two cases:

- `state/camera_settings.json` already holds values (a prior sweep, doc
  section 6.6's "swept on the rig ... then frozen") — apply them exactly.
  Reproducing the light the dataset was captured under is the entire point.
- No prior file (first run on this rig) — there is no target to apply yet.
  Auto-exposure/WB/focus are left on just long enough to converge, then
  locked at whatever they converged to, and *that* becomes the recorded
  baseline. A human sweeping the rig later overwrites it deliberately; nothing
  here invents a number that was never measured.

`v4l2-ctl` is the tool for both enumeration and control, not OpenCV's own
V4L2 property mapping — doc section 6.6 already names `v4l2-ctl
--list-formats-ext` for enumeration, and its control names (`exposure_auto`,
`white_balance_temperature_auto`, `focus_auto`, and the matching `_absolute`/
`_temperature` values) are the actual UVC control vocabulary a webcam driver
exposes. OpenCV's `CAP_PROP_*` mapping onto those same controls is
inconsistent across drivers; asking the one tool that speaks V4L2 controls
directly, the same tool used to enumerate formats, is the cheaper and more
reliable choice, not a second one to keep in sync with the first.
"""

from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass
from typing import Dict, Optional, Protocol

log = logging.getLogger("hotpot.camera.capture")

CHANNELS = 3  # BGR, matching framebus.CHANNELS (doc section 6.1)

# How long auto-exposure/WB get to converge before being locked, when there
# is no prior state/camera_settings.json to apply instead. Not measured
# against real hardware yet — a starting point, not a claim.
AUTO_SETTLE_S = 1.5


class CameraError(RuntimeError):
    """Raised when the capture backend cannot do what doc section 6.6 asks
    of it — opening the device, negotiating MJPG, or locking a control.
    Deliberately not swallowed anywhere in this module: a camera process
    that silently ran with auto-exposure still on would produce a dataset
    with a coupled-but-unrecorded illuminant (doc section 6.6's whole
    argument), which is worse than a camera process that refuses to start.
    """


@dataclass(frozen=True)
class CaptureInfo:
    """What `open()` actually negotiated — not necessarily what was asked
    for. Most USB webcams round resolution/fps to whatever modes they
    support; doc section 6.6 wants the chosen mode logged, and `main.py`
    needs the real width/height to size the frame ring."""

    width: int
    height: int
    fps: float
    fourcc: str
    exposure_absolute: Optional[int]
    white_balance_temperature: Optional[int]
    focus_absolute: Optional[int]

    # Did this backend actually put the device into MANUAL mode at the three
    # values above, or are they merely what the controls read while their
    # autos were still running?
    #
    # **This distinction is the whole reason the field exists, and getting it
    # wrong produced a real bug (2026-08-12: a yellow cast on every frame the
    # dashboard showed).** `main.py` records these three numbers to
    # `state/camera_settings.json` after every open, and the next run feeds
    # that file back in as `prior_settings`. Without a provenance flag the
    # file cannot say which of two very different things it holds:
    #
    #   - a locked state, which re-applying REPRODUCES (doc section 6.6's
    #     entire point — the dataset's light must be recoverable), or
    #   - a passing readout of a moving auto, which re-applying does not
    #     reproduce but CREATES: it pins the ISP to a number that was never a
    #     considered choice, and every later run inherits it.
    #
    # The second case is self-fulfilling. One observation gets frozen into the
    # file and every subsequent run locks to it, so the fault looks permanent
    # and looks like the camera rather than like the file.
    #
    # False here means "these are observations, do not apply them" and is the
    # safe default for a file written before this field existed.
    controls_locked: bool = False


class Capture(Protocol):
    """What `camera/main.py` needs from a capture backend. Both
    `V4L2Capture` and `FakeCapture` satisfy this with no shared base class —
    a Protocol, not an ABC, because nothing here needs runtime `isinstance`
    checks and a structural type keeps `FakeCapture` free of methods it
    would otherwise have to override just to satisfy inheritance."""

    def open(self) -> CaptureInfo:
        """Open the device, negotiate the format, lock exposure/WB/focus.
        Raises CameraError on any failure. Idempotent is not required —
        `main.py` calls it exactly once."""
        ...

    def read(self) -> Optional[bytes]:
        """One frame as raw BGR bytes, `width*height*3` long — exactly what
        `framebus.FrameWriter.write()` expects. `None` means a transient
        read failure (a dropped USB frame), not that the camera is gone;
        the caller decides how many `None`s in a row means the latter."""
        ...

    def close(self) -> None:
        ...


# ---------------------------------------------------------------------------
# FakeCapture — what this module's own tests, and camera/main.py's tests,
# drive. No device, no subprocess, deterministic frames.
# ---------------------------------------------------------------------------

class FakeCapture:
    """Synthetic frames, no hardware. `frames` is an optional queue of
    exact byte strings to hand back in order (for tests asserting on
    specific pixel content); once it is exhausted, `read()` generates a
    flat-grey frame of the right size forever, so a test that only cares
    about "does the pipeline run" doesn't have to pre-supply frames."""

    def __init__(self, *, width: int = 640, height: int = 480,
                 fps: float = 30.0, frames: Optional[list] = None) -> None:
        self.width = width
        self.height = height
        self.fps = fps
        self._frames = list(frames) if frames else None
        self._opened = False
        self.closed = False
        self.reads = 0

    def open(self) -> CaptureInfo:
        self._opened = True
        return CaptureInfo(
            width=self.width, height=self.height, fps=self.fps,
            fourcc="MJPG", exposure_absolute=100,
            white_balance_temperature=4600, focus_absolute=0,
            # No device, so nothing was locked. Values are fixtures.
            controls_locked=False)

    def read(self) -> Optional[bytes]:
        if not self._opened:
            raise CameraError("read() before open()")
        self.reads += 1
        if self._frames:
            return self._frames.pop(0)
        return bytes([128]) * (self.width * self.height * CHANNELS)

    def close(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# V4L2Capture — the real backend. Linux only; unverified against a real
# webcam until M3's acceptance test runs on the rig (doc section 21).
# ---------------------------------------------------------------------------

class V4L2Capture:
    """V4L2 open, MJPG preference, exposure/WB/focus lock (doc section 6.6).

    `cv2` is imported inside `open()`, not at module scope — the same
    reason `core/scale.py` imports `serial` inside `_open_serial()`: this
    module must stay importable, and `FakeCapture` usable, on a machine
    with neither OpenCV nor a webcam.
    """

    def __init__(self, device: str, width: int, height: int, fps: float, *,
                 prior_settings: Optional[Dict[str, object]] = None,
                 v4l2ctl_bin: str = "v4l2-ctl") -> None:
        self.device = device
        self.width = width
        self.height = height
        self.fps = fps
        self._prior = prior_settings or {}
        self._v4l2ctl = v4l2ctl_bin
        self._cap = None

    def open(self) -> CaptureInfo:
        import cv2  # local import — see class docstring

        self._log_formats()

        cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
        if not cap.isOpened():
            raise CameraError(f"could not open {self.device} (V4L2)")
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        self._cap = cap

        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = cap.get(cv2.CAP_PROP_FPS) or self.fps
        fourcc_int = int(cap.get(cv2.CAP_PROP_FOURCC))
        fourcc = "".join(chr((fourcc_int >> (8 * i)) & 0xFF) for i in range(4))
        if (actual_w, actual_h) != (self.width, self.height):
            log.warning("camera: asked for %dx%d, device gave %dx%d",
                        self.width, self.height, actual_w, actual_h)
        log.info("camera: opened %s at %dx%d@%.1ffps, fourcc=%s",
                 self.device, actual_w, actual_h, actual_fps, fourcc)

        exposure, wb, focus, locked = self._lock_controls()

        return CaptureInfo(
            width=actual_w, height=actual_h, fps=actual_fps, fourcc=fourcc,
            exposure_absolute=exposure, white_balance_temperature=wb,
            focus_absolute=focus, controls_locked=locked)

    def read(self) -> Optional[bytes]:
        if self._cap is None:
            raise CameraError("read() before open()")
        ok, frame = self._cap.read()
        if not ok or frame is None:
            return None
        return frame.tobytes()

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    # -- controls, via v4l2-ctl (see module docstring) ----------------------

    def _log_formats(self) -> None:
        """Doc section 6.6: 'enumerate at startup ... and log the chosen
        mode.' Best-effort: a missing `v4l2-ctl` binary stops enumeration
        from happening, not the whole camera process, since this call is
        diagnostic — locking the controls below is the one that must not
        fail silently."""
        out = self._run_v4l2ctl(["--list-formats-ext", "-d", self.device],
                                required=False)
        if out is not None:
            log.info("camera: %s formats:\n%s", self.device, out.strip())

    def _lock_controls(self):
        prior_exp = self._prior.get("exposure_absolute")
        prior_wb = self._prior.get("white_balance_temperature")
        prior_focus = self._prior.get("focus_absolute")
        # `locked` is the provenance flag, not a fourth setting — see
        # CaptureInfo.controls_locked. Values recorded while the autos were
        # running are observations of a moving target; re-applying them
        # would pin the ISP to a number nobody chose. Absent (a file written
        # before the flag existed) reads as False, so the converge-and-lock
        # path below runs instead, which is the recoverable direction.
        prior_locked = bool(self._prior.get("locked"))
        if (prior_locked and prior_exp is not None and prior_wb is not None
                and prior_focus is not None):
            log.info("camera: applying prior locked settings from "
                     "state/camera_settings.json: exposure=%s wb=%s focus=%s",
                     prior_exp, prior_wb, prior_focus)
            self._set_ctrl(exposure_auto=1, exposure_absolute=int(prior_exp),
                           white_balance_temperature_auto=0,
                           white_balance_temperature=int(prior_wb),
                           focus_auto=0, focus_absolute=int(prior_focus))
            return int(prior_exp), int(prior_wb), int(prior_focus), True
        if prior_exp is not None or prior_wb is not None or prior_focus is not None:
            log.warning("camera: state/camera_settings.json holds control "
                        "values but not `\"locked\": true` — they were "
                        "observed while the autos ran, not frozen, so they "
                        "are being ignored rather than applied. Sweep and "
                        "freeze deliberately (doc section 6.6) to make them "
                        "authoritative.")

        log.info("camera: no prior camera_settings.json — letting "
                 "auto-exposure/WB/focus converge for %.1fs before locking",
                 AUTO_SETTLE_S)
        self._set_ctrl(exposure_auto=3, white_balance_temperature_auto=1,
                       focus_auto=1)
        time.sleep(AUTO_SETTLE_S)
        exposure = self._get_ctrl("exposure_absolute")
        wb = self._get_ctrl("white_balance_temperature")
        focus = self._get_ctrl("focus_absolute")
        self._set_ctrl(exposure_auto=1, white_balance_temperature_auto=0,
                       focus_auto=0)
        if exposure is not None:
            self._set_ctrl(exposure_absolute=exposure)
        if wb is not None:
            self._set_ctrl(white_balance_temperature=wb)
        if focus is not None:
            self._set_ctrl(focus_absolute=focus)
        log.info("camera: locked at exposure=%s wb=%s focus=%s "
                 "(converged, not swept — sweep and freeze deliberately "
                 "per doc section 6.6 before this is a real dataset)",
                 exposure, wb, focus)
        # True: the autos are genuinely off and the device is pinned at these
        # numbers, so re-applying them next run reproduces this state rather
        # than inventing one. "Converged, not swept" is a caveat about whether
        # a HUMAN chose them, which is a separate question from whether they
        # are locked, and doc section 6.6 answers it with the sweep.
        return exposure, wb, focus, True

    def _set_ctrl(self, **ctrls: int) -> None:
        pairs = ",".join(f"{name}={value}" for name, value in ctrls.items())
        self._run_v4l2ctl(["-d", self.device, f"--set-ctrl={pairs}"],
                          required=True)

    def _get_ctrl(self, name: str) -> Optional[int]:
        out = self._run_v4l2ctl(["-d", self.device, f"--get-ctrl={name}"],
                                required=False)
        if out is None:
            return None
        # v4l2-ctl prints "name: 123".
        try:
            return int(out.strip().rsplit(":", 1)[-1].strip())
        except ValueError:
            log.warning("camera: could not parse --get-ctrl=%s output %r",
                       name, out)
            return None

    def _run_v4l2ctl(self, args, *, required: bool) -> Optional[str]:
        cmd = [self._v4l2ctl, *args]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    timeout=5.0, check=False)
        except (FileNotFoundError, OSError) as e:
            msg = f"camera: {self._v4l2ctl} unavailable ({e})"
            if required:
                raise CameraError(
                    f"{msg} — cannot lock exposure/WB/focus without it "
                    "(doc section 6.6)") from e
            log.warning(msg)
            return None
        if result.returncode != 0:
            msg = (f"camera: {' '.join(cmd)} failed "
                   f"(exit {result.returncode}): {result.stderr.strip()}")
            if required:
                raise CameraError(msg)
            log.warning(msg)
            return None
        return result.stdout


# ---------------------------------------------------------------------------
# WindowsCapture — a real backend for this dev machine. Not a doc build
# item: the ODYSSEY rig is Linux and V4L2Capture is what ships; this exists
# because the dev machine is Windows and a real USB webcam is the only way
# to see genuine frames (not FakeCapture's synthetic ones) flow through the
# ring/mjpeg/dev-panel path before the rig is available. See CLAUDE.md.
# ---------------------------------------------------------------------------

class WindowsCapture:
    """OpenCV's DirectShow backend, addressed by device *index* (0, 1, ...)
    rather than V4L2Capture's `/dev/videoN` path — Windows has no such path.

    **Exposure/WB/focus locking here is best-effort, not the guarantee
    V4L2Capture gives.** That class's own docstring already warns that
    OpenCV's `CAP_PROP_*` mapping onto UVC controls is inconsistent across
    drivers, which is exactly why V4L2Capture shells out to `v4l2-ctl`
    instead of trusting it — and `v4l2-ctl` does not exist on Windows, so
    there is no reliable alternative here. Every value this class reports is
    read back from the device after the matching `set()` call, never the
    number that was asked for: a driver that silently ignores `.set()` (a
    real, common DirectShow behaviour) must show up as an unreadable
    control, not a fabricated lock. Do not point M4's dataset capture at
    this backend and trust the recorded exposure the way §6.6 trusts
    V4L2Capture's — verify manually first.
    """

    def __init__(self, device: int, width: int, height: int, fps: float, *,
                 prior_settings: Optional[Dict[str, object]] = None,
                 video_capture_factory=None) -> None:
        self.device = device
        self.width = width
        self.height = height
        self.fps = fps
        self._prior = prior_settings or {}
        # Test seam: a fake object exposing isOpened/set/get/read/release,
        # so the readback/lock logic is testable with no real webcam — the
        # same role `subprocess.run` faking plays for V4L2Capture's tests.
        self._video_capture_factory = video_capture_factory
        self._cap = None

    def open(self) -> CaptureInfo:
        import cv2  # local import — see V4L2Capture's docstring for why

        factory = self._video_capture_factory
        if factory is None:
            factory = lambda: cv2.VideoCapture(self.device, cv2.CAP_DSHOW)
        cap = factory()
        if not cap.isOpened():
            raise CameraError(
                f"could not open camera index {self.device} (DirectShow)")
        # **FOURCC must be set LAST, after resolution AND fps, and it is
        # worth 6.5x the frame rate.** Measured on this dev machine's webcam
        # 2026-08-12, every combination tried:
        #
        #     MJPG, res, fps          -> YUY2, 4.6 fps   (what this was)
        #     MJPG, res, MJPG, fps    -> YUY2, 4.6 fps
        #     MJPG, res, fps, MJPG    -> MJPG, 30.3 fps  (this)
        #
        # Every `set` that changes the media type re-negotiates it, and
        # DirectShow answers with its default format for the new mode —
        # which for this camera is uncompressed YUY2. Setting fps does that
        # just as much as setting resolution does, which is the part that
        # is easy to miss: the fps call looks unrelated to pixel format and
        # silently undoes it. Raw YUY2 at 1920x1080 is ~6 MB a frame, and
        # USB bandwidth turns that into 4.6 fps.
        #
        # So the last `set` is the one that decides, and it has to be the
        # format. Doc section 6.6 asks for MJPG; this is what delivers it.
        mjpg = cv2.VideoWriter_fourcc(*"MJPG")
        cap.set(cv2.CAP_PROP_FOURCC, mjpg)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        cap.set(cv2.CAP_PROP_FOURCC, mjpg)
        self._cap = cap

        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = cap.get(cv2.CAP_PROP_FPS) or self.fps
        fourcc_int = int(cap.get(cv2.CAP_PROP_FOURCC))
        fourcc = ("".join(chr((fourcc_int >> (8 * i)) & 0xFF) for i in range(4))
                  if fourcc_int else "")
        if (actual_w, actual_h) != (self.width, self.height):
            log.warning("camera: asked for %dx%d, device gave %dx%d",
                        self.width, self.height, actual_w, actual_h)
        if fourcc != "MJPG":
            # Not fatal — the pipeline works on whatever it gets — but it
            # costs most of the frame rate, so it must not pass silently.
            log.warning("camera: wanted MJPG, negotiated %s — expect a much "
                        "lower frame rate at this resolution", fourcc or "?")
        log.info("camera: opened index %s at %dx%d@%.1ffps, fourcc=%s "
                 "(DirectShow, Windows dev backend)",
                 self.device, actual_w, actual_h, actual_fps, fourcc)

        exposure, wb, focus, locked = self._lock_controls(cap, cv2)
        return CaptureInfo(
            width=actual_w, height=actual_h, fps=actual_fps, fourcc=fourcc,
            exposure_absolute=exposure, white_balance_temperature=wb,
            focus_absolute=focus, controls_locked=locked)

    def read(self) -> Optional[bytes]:
        if self._cap is None:
            raise CameraError("read() before open()")
        ok, frame = self._cap.read()
        if not ok or frame is None:
            return None
        return frame.tobytes()

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def _lock_controls(self, cap, cv2):
        """Best-effort, and honest about it (see class docstring): only
        ever move a control away from auto when there is a real prior
        value (from a previous rig sweep) to set it *to*. With no prior
        calibration, every auto is left running and this only reads back
        whatever the driver already reports.

        **This used to force auto-WB/autofocus off unconditionally on a
        fresh run, with no convergence wait — found 2026-08-12 because
        the resulting stream looked visibly worse than the OS's own
        camera app.** `.set(CAP_PROP_AUTO_WB, 0)` locks onto whatever the
        driver happens to be sitting at the instant it's called; called
        right after `open()`, before the ISP has converged anything, that
        is close to a random value, not a calibration. V4L2Capture avoids
        this with `AUTO_SETTLE_S` — let autos run, then lock at whatever
        they converged to — but that wait is not mirrored here: it is
        gated on `CAP_PROP_AUTO_EXPOSURE`'s "manual mode" trigger value
        (0.25 on DirectShow, by widely-repeated report, not verified
        against this machine's driver), and flipping into manual mode is
        exactly the step being avoided until that number is confirmed.
        Leaving every auto alone is the smaller, verifiably-safe fix.

        **That fix was incomplete, and the second half is this gate — found
        2026-08-12, same day, from a yellow cast on the dashboard feed that
        the OS camera app did not show.** Leaving the autos alone stopped
        this run from freezing a bad value, but `main.py` still WROTE the
        read-back numbers to `state/camera_settings.json`, and the next run
        read that file back as `prior_settings` and locked to them — so the
        very readout taken while auto-WB was running became the thing that
        turned auto-WB off. One poisoned observation, then permanent. The
        recorded 6500 K was a daylight value pinned under projector light,
        which is exactly a yellow cast. Priors are now applied only when the
        file says they were locked deliberately (`controls_locked`).
        """
        prior_exp = self._prior.get("exposure_absolute")
        prior_wb = self._prior.get("white_balance_temperature")
        prior_focus = self._prior.get("focus_absolute")
        prior_locked = bool(self._prior.get("locked"))
        applied = False

        if prior_locked:
            if prior_exp is not None:
                cap.set(cv2.CAP_PROP_EXPOSURE, prior_exp)
                applied = True
            if prior_wb is not None:
                cap.set(cv2.CAP_PROP_AUTO_WB, 0)
                cap.set(cv2.CAP_PROP_WB_TEMPERATURE, prior_wb)
                applied = True
            if prior_focus is not None:
                cap.set(cv2.CAP_PROP_AUTOFOCUS, 0)
                cap.set(cv2.CAP_PROP_FOCUS, prior_focus)
                applied = True
        elif prior_exp is not None or prior_wb is not None or prior_focus is not None:
            log.warning("camera: ignoring exposure=%s wb=%s focus=%s from "
                        "state/camera_settings.json — recorded without "
                        "`\"locked\": true`, so they are observations of a "
                        "running auto, not a frozen setting. Autos left on.",
                        prior_exp, prior_wb, prior_focus)

        exposure = self._readback(cap, cv2.CAP_PROP_EXPOSURE)
        wb = self._readback(cap, cv2.CAP_PROP_WB_TEMPERATURE)
        focus = self._readback(cap, cv2.CAP_PROP_FOCUS)
        log.info("camera: Windows best-effort lock read back "
                 "exposure=%s wb=%s focus=%s locked=%s (None = unsupported "
                 "by this driver, not a failure)",
                 exposure, wb, focus, applied)
        return exposure, wb, focus, applied

    @staticmethod
    def _readback(cap, prop) -> Optional[int]:
        val = cap.get(prop)
        if val is None:
            return None
        try:
            val = int(val)
        except (TypeError, ValueError):
            return None
        # OpenCV/DirectShow's documented "unsupported" signal for .get() is
        # 0 or -1 depending on the property and driver — neither is a real
        # exposure/WB/focus value a webcam would report, so treat both as
        # "no reading" rather than a fabricated 0.
        return val if val not in (0, -1) else None
