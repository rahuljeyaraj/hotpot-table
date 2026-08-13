"""Camera process — the real body (doc section 21, M3 build item 2).

Was the M0 stub through M3.1; from here on this is what doc section 3 gives
`camera`: open the webcam, negotiate MJPG, lock exposure/WB/focus and
record them, publish every frame into the shared-memory ring
(`common/framebus.py`, M3.1), and serve `/stream.mjpg`, `/snapshot.jpg`,
`/info.json` over plain HTTP (`camera/mjpeg.py`). `camera/capture.py` holds
the V4L2 specifics and the fake this file's own tests run against — see
that module's docstring for why the split exists and why the real backend
is unverified until it runs against actual hardware.

Readiness is deliberately later than `common/stub.py`'s
------------------------------------------------------------------------
`stub.py` fires `HOTPOT-READY` the instant its control link starts, because
doc section 10.3 makes camera tier 1 and a stub waiting on core would
deadlock the tier order. That reasoning about *order* still holds, but what
"genuinely serving" (doc section 10.2) means for the real camera does not —
`run.py`'s own tier comment says tier 1 "creates the frame ring and serves
MJPEG", so readiness has to wait for those, not merely for a socket thread
to start. That is why this file builds its own control link out of
`wire.Client`/`health.Heartbeat` instead of calling `stub.start()`: that
helper bakes `log.ready()` into the moment the client starts, which is the
one thing this process cannot reuse as-is.

Frame-loop crash policy
------------------------
The capture loop runs on the main thread, not a background one, and a
device that will not recover — too many consecutive failed reads in a row
— raises out of it rather than looping forever hoping. Doc section 20.1's
own table says camera's restart "recreates shm, consumers re-attach": that
is the *process* restart `run.py` already performs on any crash, and a
supervisor-restarted camera is a clean fix (a fresh `FrameWriter`, a fresh
device handle) in a way that a live process trying to self-heal a wedged
V4L2 device is not. Running the loop on the main thread, rather than a
daemon thread whose uncaught exception would only get logged
(`log.py`'s `threading.excepthook`) and leave the process silently doing
nothing, is what makes that crash actually reach `run.py`.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from hotpot.common import atomicio, config, framebus, health, log, wire
from hotpot.camera import capture as capture_mod
from hotpot.camera import mjpeg

CORE_HOST = "127.0.0.1"
CORE_PORT = 8765          # doc section 4.1 default; overridden by cfg["core"]["control_port"]

# doc section 8.5-ish: camera owns this file (atomicio.py's own comment on
# per-file single-writer ownership).
_ROOT = Path(__file__).resolve().parents[3]
CAMERA_SETTINGS_PATH = _ROOT / "state" / "camera_settings.json"

# Bind on every interface: doc section 8.6's `camera.host_for_browser` is
# what a client *embeds in a URL*, which on the ODYSSEY's dual-NIC-plus-WiFi
# setup (doc section 1.4) may not be the interface this process itself
# should restrict its listener to. Binding wide and letting the config value
# be purely client-facing is the simpler of the two to get wrong safely.
BIND_HOST = "0.0.0.0"

# Consecutive failed reads before this process gives up on the device and
# crashes out (see module docstring). At 30fps this is roughly one second
# of a webcam that has stopped answering — long enough to absorb a single
# dropped USB frame, short enough that "the camera died" is noticed quickly.
MAX_CONSECUTIVE_READ_FAILURES = 30

log_ = logging.getLogger("hotpot.camera.main")

# The three controls `CaptureInfo`/`state/camera_settings.json` already
# track by name, doc §6.6's original set — mapped to the field `set_control`
# writes back into `self.info`. Every other control name goes through the
# additive `"controls"` dict `_write_camera_settings` writes instead (see
# its own docstring).
_TRACKED_CONTROLS = {
    "exposure": "exposure_absolute",
    "white_balance": "white_balance_temperature",
    "focus": "focus_absolute",
}


def default_encode_jpeg(frame_bytes: bytes, width: int, height: int,
                        target_width: int) -> bytes:
    """BGR bytes -> a JPEG at `target_width`, preserving aspect ratio.

    `cv2`/`numpy` imported locally — see `capture.py`'s docstring for why:
    this function must not make the module unimportable, or `FakeCapture`
    unusable, on a machine with neither installed.
    """
    import cv2
    import numpy as np

    arr = np.frombuffer(frame_bytes, dtype=np.uint8).reshape((height, width, 3))
    if target_width and target_width != width:
        target_height = max(1, round(target_width * height / width))
        arr = cv2.resize(arr, (target_width, target_height))
    ok, buf = cv2.imencode(".jpg", arr, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    if not ok:
        raise RuntimeError("cv2.imencode failed")
    return buf.tobytes()


@dataclass
class Stats:
    frames_written: int = 0
    frames_encoded: int = 0
    read_failures: int = 0
    last_frame_id: int = -1
    last_ts_ns: int = 0
    started_monotonic: float = field(default_factory=time.monotonic)


class CameraProcess:
    """Owns every piece build item 2 lists: the open device, the shm
    writer, the MJPEG server, the control link. Constructed with an
    already-built `Capture` — `main()` decides real-vs-fake, this class
    does not (doc section 19.4's backend-abstraction discipline, applied
    to camera the same way `classifier`/`voice` apply it to their own
    backends)."""

    def __init__(self, cfg: Dict[str, Any], cap: capture_mod.Capture, *,
                 settings_path: Path = CAMERA_SETTINGS_PATH,
                 encode_jpeg: Callable[[bytes, int, int, int], bytes] = default_encode_jpeg,
                 core_host: str = CORE_HOST,
                 core_port: Optional[int] = None,
                 bind_host: str = BIND_HOST,
                 shm_name: str = framebus.SHM_NAME,
                 mjpeg_port: Optional[int] = None,
                 prior_settings: Optional[Dict[str, Any]] = None) -> None:
        self._cfg = cfg
        self._cam_cfg = cfg.get("camera", {})
        self._cap = cap
        self._settings_path = settings_path
        self._encode_jpeg = encode_jpeg
        self._core_host = core_host
        self._core_port = (core_port if core_port is not None
                           else config.get(cfg, "core.control_port", CORE_PORT))
        self._bind_host = bind_host
        self._shm_name = shm_name
        self._mjpeg_port_override = mjpeg_port
        # Only read for the doc §12.8 dev-panel controls' own replay-on-start
        # (below) — the exposure/WB/focus prior-application path is entirely
        # inside `capture.py`'s `_lock_controls`, unchanged, and already had
        # this file passed in via `_build_capture`.
        self._prior_settings = prior_settings or {}

        self.info: Optional[capture_mod.CaptureInfo] = None
        self.stats = Stats()
        self._writer: Optional[framebus.FrameWriter] = None
        self._frame_holder = mjpeg.LatestFrame()
        self._mjpeg: Optional[mjpeg.MjpegServer] = None
        self._client: Optional[wire.Client] = None
        self._heartbeat: Optional[health.Heartbeat] = None
        self._stop = threading.Event()
        # Guards every `self._cap` call the dev-panel control API makes
        # (list/get/set) against the capture loop's own `self._cap.read()`
        # on the main thread — the same class of cross-thread race `core`'s
        # `state_lock` was added for (M2.5's own note: "no lock ... raced
        # the tablet's WebSocket thread"). `read()` itself stays unlocked —
        # it runs every frame and a human-paced control edit is an
        # acceptable, brief race against it, not worth serialising the
        # whole capture loop behind.
        self._cap_lock = threading.Lock()
        self._extra_controls: Dict[str, capture_mod.ControlState] = {}

    # -- lifecycle -----------------------------------------------------

    def start(self) -> None:
        """Open the device, publish settings, stand up the ring and the
        MJPEG server, bring the control link up, then announce readiness.
        Order matches doc section 21's build-item-2 list and the
        readiness discussion in the module docstring."""
        self.info = self._cap.open()
        self._write_camera_settings(self.info)
        self._restore_extra_controls()

        self._writer = framebus.FrameWriter(self.info.width, self.info.height,
                                            name=self._shm_name)

        mjpeg_port = (self._mjpeg_port_override if self._mjpeg_port_override is not None
                     else int(self._cam_cfg.get("mjpeg_port", 8081)))
        self._mjpeg = mjpeg.MjpegServer(self._bind_host, mjpeg_port,
                                        self._frame_holder, self._info_snapshot,
                                        self._controls_snapshot,
                                        self._set_control_json)
        self._mjpeg.start()

        self._client = wire.Client(self._core_host, self._core_port, "camera")
        self._heartbeat = health.Heartbeat(self._client.send, who="camera")
        self._client.start()
        self._heartbeat.start()

        log_.info("camera: ready — %dx%d shm ring, mjpeg on port %d",
                  self.info.width, self.info.height, self._mjpeg.port)
        log.ready("camera")

    def run_forever(self) -> None:
        """The capture loop. Blocks the calling thread until `stop()` is
        called from elsewhere, or the device stops answering (raises —
        see module docstring)."""
        mjpeg_fps = float(self._cam_cfg.get("mjpeg_fps", 8))
        encode_interval = (1.0 / mjpeg_fps) if mjpeg_fps > 0 else None
        mjpeg_width = int(self._cam_cfg.get("mjpeg_width", self.info.width))
        last_encode = 0.0
        consecutive_failures = 0

        while not self._stop.is_set():
            frame_bytes = self._cap.read()
            if frame_bytes is None:
                consecutive_failures += 1
                self.stats.read_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_READ_FAILURES:
                    raise capture_mod.CameraError(
                        f"{consecutive_failures} consecutive failed reads — "
                        "device is not answering")
                time.sleep(0.01)
                continue
            consecutive_failures = 0

            ts_ns = time.time_ns()
            frame_id = self._writer.write(frame_bytes, ts_ns=ts_ns)
            self.stats.frames_written += 1
            self.stats.last_frame_id = frame_id
            self.stats.last_ts_ns = ts_ns

            now = time.monotonic()
            if encode_interval is None or (now - last_encode) >= encode_interval:
                try:
                    jpeg = self._encode_jpeg(frame_bytes, self.info.width,
                                             self.info.height, mjpeg_width)
                    self._frame_holder.publish(jpeg, frame_id)
                    self.stats.frames_encoded += 1
                except Exception:
                    log_.exception("camera: mjpeg encode failed for frame %d",
                                   frame_id)
                last_encode = now

    def stop(self) -> None:
        """Idempotent. Tears down in reverse of `start()`."""
        self._stop.set()
        if self._heartbeat is not None:
            self._heartbeat.stop()
        if self._client is not None:
            self._client.stop()
        if self._mjpeg is not None:
            self._mjpeg.stop()
        if self._writer is not None:
            self._writer.close()
            self._writer.unlink()
        self._cap.close()

    # -- live controls, doc §12.8's dev-panel "Camera controls" card ------
    # The dev panel and `_restore_extra_controls` below both go through
    # `set_control` — one path for "a human just moved a slider" and "the
    # last run's slider position is being restored," not two.

    def list_controls(self) -> List[capture_mod.ControlSpec]:
        with self._cap_lock:
            return self._cap.list_controls()

    def control_states(self) -> Dict[str, capture_mod.ControlState]:
        with self._cap_lock:
            return self._cap.get_control_states()

    def set_control(self, name: str, *, auto: Optional[bool] = None,
                    value: Optional[int] = None) -> capture_mod.ControlState:
        """Applies one control change and persists it. `CameraError`
        propagates to the caller (an HTTP 400 in `mjpeg.py`, a log line in
        `_restore_extra_controls`) rather than being swallowed here — the
        two callers have different ideas of what "non-fatal" means, and
        only they know which."""
        with self._cap_lock:
            state = self._cap.set_control(name, auto=auto, value=value)
            all_states = (self._cap.get_control_states()
                         if name in _TRACKED_CONTROLS else None)

        if name in _TRACKED_CONTROLS:
            # No per-field lock in the on-disk schema (doc §6.6 predates
            # per-control independence) — `locked` becomes the aggregate
            # "are all three still manual right now." Turning any one back
            # to Auto drops it, so a restart re-converges all three fresh
            # rather than silently re-freezing the other two to old
            # numbers next to a control the developer just deliberately
            # unlocked.
            still_locked = all(
                all_states[n].auto is False
                for n in _TRACKED_CONTROLS if n in all_states)
            self.info = replace(self.info, **{_TRACKED_CONTROLS[name]: state.value},
                                controls_locked=still_locked)
        else:
            self._extra_controls[name] = state
        self._write_camera_settings(self.info)
        return state

    def _restore_extra_controls(self) -> None:
        """The non-tracked half of `state/camera_settings.json`'s prior
        settings — brightness/contrast/etc, doc §12.8's new knobs, not
        doc §6.6's original three (those are `capture.py`'s job, already
        done by the time `open()` returns). A knob this backend doesn't
        support (or never had a value saved) is logged and skipped, not
        fatal — unlike the exposure/WB/focus lock, these aren't required
        for a classifier dataset to be reproducible."""
        for name, saved in self._prior_settings.get("controls", {}).items():
            try:
                self.set_control(name, auto=saved.get("auto"),
                                 value=saved.get("value"))
            except capture_mod.CameraError as e:
                log_.warning("camera: could not restore control %r from "
                            "state/camera_settings.json: %s", name, e)

    # -- internals -------------------------------------------------------

    def _write_camera_settings(self, info: capture_mod.CaptureInfo) -> None:
        """Doc section 6.6: exposure/WB/focus and `field_level` are one
        coupled parameter and must be recorded together, in the one file
        that answers "under what light was this dataset taken".

        **This file is also read back as `prior_settings` on the next run,
        which makes `locked` the most important key in it** — it is what
        separates "this is the light, reproduce it" from "this is what the
        controls happened to read while their autos were still hunting".
        Writing the numbers without it caused a yellow cast that survived
        restarts (2026-08-12); see `capture.CaptureInfo.controls_locked`.
        """
        atomicio.write_json(self._settings_path, {
            # The backend's own `device` (a V4L2 path on Linux, a
            # DirectShow index on Windows) is the honest value here —
            # `cam_cfg["device"]` alone would misreport a Windows run,
            # which never uses it. Falls back to config for a backend
            # (FakeCapture, in tests) that has no `.device` at all.
            "device": getattr(self._cap, "device", self._cam_cfg.get("device")),
            "capture": [info.width, info.height],
            "fps": info.fps,
            "fourcc": info.fourcc,
            "exposure_absolute": info.exposure_absolute,
            "white_balance_temperature": info.white_balance_temperature,
            "focus_absolute": info.focus_absolute,
            "locked": info.controls_locked,
            "field_level": config.get(self._cfg, "of.field_level", 1.0),
            # Additive: doc §12.8's dev-panel controls beyond the original
            # three above (brightness, saturation, ...). Old files without
            # this key read back as `{}` via `.get("controls", {})`
            # (`_restore_extra_controls`), same "absent reads safe" rule
            # `locked` itself already established.
            "controls": {name: {"auto": st.auto, "value": st.value}
                        for name, st in self._extra_controls.items()},
            "written_at": time.time(),
        })

    def _info_snapshot(self) -> Dict[str, Any]:
        """`/info.json` — doc section 12.8's capture resolution, actual
        FPS, frame_id, and shm slot, exposed here; wiring it into the
        developer panel is build item 4, not this one."""
        uptime = time.monotonic() - self.stats.started_monotonic
        fps = self.stats.frames_written / uptime if uptime > 0 else 0.0
        slot = (self.stats.last_frame_id % self._writer.slot_count
               if self._writer is not None and self.stats.last_frame_id >= 0
               else None)
        return {
            "width": self.info.width if self.info else None,
            "height": self.info.height if self.info else None,
            "requested_fps": self._cam_cfg.get("fps"),
            "actual_fps": round(fps, 2),
            "frame_id": self.stats.last_frame_id,
            "shm_slot": slot,
            "shm_name": self._shm_name,
            "frames_written": self.stats.frames_written,
            "frames_encoded": self.stats.frames_encoded,
            "read_failures": self.stats.read_failures,
            "uptime_s": round(uptime, 1),
        }

    def _controls_snapshot(self) -> List[Dict[str, Any]]:
        """`/controls.json` — plain dicts, spec and live state merged per
        control, so `mjpeg.py` can serialise this with no knowledge of
        `capture.py`'s dataclasses (the same split `_info_snapshot` above
        already draws for `/info.json`)."""
        specs = self.list_controls()
        states = self.control_states()
        out = []
        for spec in specs:
            state = states.get(spec.name)
            out.append({
                "name": spec.name, "label": spec.label,
                "auto_capable": spec.auto_capable,
                "min": spec.min, "max": spec.max, "step": spec.step,
                "unit": spec.unit, "default": spec.default,
                "auto": state.auto if state else None,
                "value": state.value if state else None,
            })
        return out

    def _set_control_json(self, name: str, auto: Optional[bool],
                          value: Optional[int]) -> Dict[str, Any]:
        """`POST /control`'s response body — `mjpeg.py`'s callable seam,
        same reasoning as `_controls_snapshot` above. `CameraError`
        deliberately propagates uncaught; `mjpeg.py`'s own `_control`
        handler is what turns it into an HTTP 400."""
        state = self.set_control(name, auto=auto, value=value)
        return {"name": state.name, "auto": state.auto, "value": state.value}


def _build_capture(cam_cfg: Dict[str, Any],
                   prior: Optional[Dict[str, object]]) -> capture_mod.Capture:
    """Real backend, chosen by platform — not a doc build item (see
    `capture.py`'s `WindowsCapture` docstring). The ODYSSEY rig is always
    Linux, so this only ever picks `WindowsCapture` on a dev machine;
    `sys.platform` is what `V4L2Capture`'s own docstring already names as
    the dev/deploy split, so it is what decides here too, not a config
    flag someone could leave wrong on the rig.
    """
    width, height = cam_cfg.get("capture", [1920, 1080])
    fps = cam_cfg.get("fps", 30)
    if sys.platform.startswith("win"):
        # Dev-only key, deliberately absent from doc section 8.6's schema
        # and `config/system.default.json`: `config.get`'s own default
        # param is exactly for an optional key nothing on the rig needs.
        index = config.get({"camera": cam_cfg}, "camera.windows_device_index", 0)
        return capture_mod.WindowsCapture(index, width, height, fps,
                                          prior_settings=prior)
    device = cam_cfg.get("device", "/dev/video0")
    return capture_mod.V4L2Capture(device, width, height, fps,
                                   prior_settings=prior)


def main() -> None:
    """What `python -m hotpot.camera.main` runs."""
    log.setup("camera")
    cfg = config.load()
    cam_cfg = cfg.get("camera", {})

    prior = atomicio.read_json(CAMERA_SETTINGS_PATH, default=None)
    cap = _build_capture(cam_cfg, prior)

    proc = CameraProcess(cfg, cap, prior_settings=prior)
    proc.start()
    try:
        proc.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        proc.stop()


if __name__ == "__main__":
    main()
