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
carries for the shared-memory ring it is now feeding.

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
            white_balance_temperature=4600, focus_absolute=0)

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

        exposure, wb, focus = self._lock_controls()

        return CaptureInfo(
            width=actual_w, height=actual_h, fps=actual_fps, fourcc=fourcc,
            exposure_absolute=exposure, white_balance_temperature=wb,
            focus_absolute=focus)

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
        if prior_exp is not None and prior_wb is not None and prior_focus is not None:
            log.info("camera: applying prior locked settings from "
                     "state/camera_settings.json: exposure=%s wb=%s focus=%s",
                     prior_exp, prior_wb, prior_focus)
            self._set_ctrl(exposure_auto=1, exposure_absolute=int(prior_exp),
                           white_balance_temperature_auto=0,
                           white_balance_temperature=int(prior_wb),
                           focus_auto=0, focus_absolute=int(prior_focus))
            return int(prior_exp), int(prior_wb), int(prior_focus)

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
        return exposure, wb, focus

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
