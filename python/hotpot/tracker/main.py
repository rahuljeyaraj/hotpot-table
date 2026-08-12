"""Tracker process — MediaPipe Hands, roles, and the cursor datagram
(doc sections 3, 4.6, 6.4, 11; doc section 21, M5 build item 1).

Was `common/stub.py` from M0 through M4. From here on it is doc section
11.1's pipeline:

    attach shm -> read latest frame -> downsample -> MediaPipe Hands ->
    landmark 9 -> camera->stage homography -> role assignment -> UDP to
    of and core

Five decisions in here are not obvious from that line, and four of them
were forced by something already built.

**1. The point is warped, not the frame — and this is a change of course
from what M4m expected.** `core/bin_grid.py`'s docstring says the warped
"table crop" is "what MediaPipe will run on (M5, not built)". It is not,
and the reason is physical rather than architectural: `warp_frame_to_stage`
crops to the table, and hands *reach in from outside the table*. A diner's
hand enters over the near edge with the wrist and forearm off-table
entirely, so warping first throws away the part of the hand a palm detector
most needs to see — and it fails as "detection is unreliable near the near
edge", which reads exactly like a lighting or a model problem. So this
process runs MediaPipe on the whole (downsampled) camera frame and puts the
single cursor point through `H_cam_to_stage`, which is doc section 11.1's
own pipeline order, unchanged.

Nothing downstream notices the difference: the warped canvas IS stage space
(`warp_frame_to_stage(frame, H, stage_size)`), so the camera bin grid's
lines and this cursor are already in one space either way. The classifier
still warps its own frames — it crops rectangles and has no hands to lose.

**2. No homography means no datagrams at all.** Doc section 21 makes M5
depend on M4 with a reason attached: "the cursor is meaningless without
it." A cursor emitted in camera pixels but labelled stage space would
hit-test against real bin rects and produce confident, wrong hovers. Core
sends `H` in `welcome` (doc section 5.3); until it does, this process
tracks nothing and says so once.

**3. Stale frames stop the emission, they do not freeze it.** Doc section
6.4: "tracker sends nothing rather than sending a frozen cursor", plus
`{"t":"stat","frames_stale":true}` to core. The tracks are reset too — a
role held across an outage of unknown length would mean the bowl hand
keeping a pointer role it inherited before the camera died.

**4. The frame ring reader is local rather than `classifier.main`'s.**
Not duplication for its own sake: the two want opposite failure behaviour.
`RingSource.frame()` raises a sentence for an operator to read, because a
classifier command has a human waiting on a reply. This one returns None
and keeps polling, because there is nobody waiting and the correct
response to "no frame this instant" at 30Hz is to try again in 5ms. A
shared class would have to serve both and would end up with a flag.

**5. Doc section 11.2's model ladder does not exist in the installed
MediaPipe.** `model_complexity` was a Solutions-API parameter and
`mp.solutions` is gone in mediapipe 1.0.0 (see `backend_mediapipe.py`'s
docstring — verified, not remembered). The ladder is now which `.task`
bundle you load, and Google publishes one. The probe below is still real:
it measures the achieved rate over the first few seconds and logs it, and
it will climb if a second bundle ever lands in `models/`. What it cannot
do is pretend a one-rung ladder was climbed.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from hotpot.common import config, cursorbus, framebus, geometry, health, log, wire
from hotpot.tracker import backend_mediapipe, backend_stub, tracking
from hotpot.tracker.backend import Backend, Detection

_log = logging.getLogger("hotpot.tracker")

CORE_HOST = "127.0.0.1"
CORE_PORT = 8765          # doc section 4.1 default

_ROOT = Path(__file__).resolve().parents[3]
MODELS_DIR = _ROOT / "models"

# Doc section 6.4's staleness bound, the same one `classifier/main.py`
# uses. Past this the camera is dead or stalled and this process must go
# quiet rather than repeat its last cursor.
STALE_S = 0.5

# Doc section 6.5: "tracker downsamples with cv2.resize before MediaPipe
# (cheap, and MediaPipe wants small)". 480 wide keeps a hand comfortably
# over 100px across at the rig's framing while cutting the pixels
# MediaPipe touches by 16x against 1920. Config-overridable
# (`tracker.input_width`) because it is the first knob to reach for if the
# ODYSSEY cannot hold rate — it is a quality/speed trade, not a constant.
DEFAULT_INPUT_WIDTH = 480

# Doc section 11.2's probe: "start at 0, measure for 5 seconds, and if the
# measured rate is above 45 fps try 1 and keep it only if it stays above
# 25." Kept as three named numbers because they are three different
# claims, and the middle one is the only one that is a preference.
PROBE_SECONDS = 5.0
PROBE_CLIMB_ABOVE_FPS = 45.0
PROBE_KEEP_ABOVE_FPS = 25.0

# How long the loop sleeps when there is no new frame. The ring is written
# at ~30Hz, so this is a sixth of a frame interval — short enough that the
# newest frame is picked up promptly, long enough that an idle tracker is
# not a busy loop on a board with four cores and no spare one (doc 10.4).
IDLE_SLEEP_S = 0.005

# Developer feedback running M5 on the rig (2026-08-12): the cursor, drawn
# at landmark 9's own stage position, sits under the hand's shadow and is
# invisible most of the time — the projected field is the table's only
# light (CLAUDE.md's "hard invariant"), so a hand over its own cursor
# blocks it outright, it is not merely "partly covered". Shifted here,
# upstream of both core's hit test and oF's rendering, so the visible dot
# and whatever it is hovering never disagree — doc section 9.4: "core
# hit-tests stage-space cursors against stage-space rects," the same
# points oF draws. Direction is toward the far edge (smaller stage Y —
# TableGeometry.h's "+y from far edge towards the diner"): this module's
# own docstring establishes hands always reach in from the near edge, so
# that is the one direction clear of the arm/hand behind the tracked
# point, for every bin and every widget. Magnitude is deliberately less
# than a full hand length (roughly a fingertip's reach from landmark 9,
# the middle-finger MCP) so it does not overshoot a bin (255mm tall) or a
# widget onto whatever is beyond it. **Not yet physically confirmed** —
# first pass, chosen by reasoning, still owes a rig observation of the
# cursor actually clearing a hand at the table edges.
CURSOR_SHADOW_CLEARANCE_MM = 70.0

# This rig's plywood (TableGeometry.h/geometry_store.py's TABLE_H_MM).
# Duplicated rather than imported: this process does not import `core`
# (doc's process separation), the same reason geometry_store.py's own
# TABLE_W_MM/TABLE_H_MM are themselves a duplicate of TableGeometry.h's.
_TABLE_H_MM = 914.4


# ---------------------------------------------------------------------------
# Frames
# ---------------------------------------------------------------------------

class FrameSource:
    """The shared-memory ring, opened lazily and re-opened after failure.

    Lazy and re-opening for `classifier/main.py`'s reason, which applies
    identically here: camera may not be up yet, may die, and comes back
    with a **new** segment (doc section 20.1), so a reader attached once at
    startup would hold a corpse forever after the first camera restart.
    """

    def __init__(self, name: str = framebus.SHM_NAME,
                 open_reader: Optional[Callable[[], Any]] = None) -> None:
        self.name = name
        self._open_reader = open_reader or (lambda: framebus.FrameReader(name))
        self._reader: Optional[Any] = None
        self.last_frame_id: int = -1

    def drop(self) -> None:
        reader, self._reader = self._reader, None
        if reader is not None:
            try:
                reader.close()
            except Exception:      # noqa: BLE001 - closing a dead segment
                pass

    def next_frame(self):
        """`(array, frame_id)` for a frame not seen before, or `(None,
        reason)` where reason is one of `"none"`, `"stale"`, `"same"`.

        The reason matters to the caller: `"stale"` is doc section 6.4's
        camera-is-dead case and has to be reported to core and reset the
        tracks, while `"same"` is the ordinary state of a loop spinning
        faster than the camera and means nothing at all.
        """
        import numpy as np      # noqa: WPS433 - local, see geometry.fit

        if self._reader is None:
            try:
                self._reader = self._open_reader()
            except (FileNotFoundError, ValueError):
                return None, "none"
        reader = self._reader
        try:
            if reader.is_stale(timeout_s=STALE_S):
                return None, "stale"
            frame = reader.read()
        except Exception:      # noqa: BLE001 - a dead segment reads as anything
            self.drop()
            return None, "none"
        if frame is None:
            return None, "none"
        if frame.frame_id == self.last_frame_id:
            return None, "same"
        self.last_frame_id = frame.frame_id
        arr = np.frombuffer(frame.data, dtype=np.uint8)
        return arr.reshape((reader.height, reader.width, reader.channels)), \
            frame.frame_id


def downsample(frame, target_width: int):
    """`(small_frame, scale)` where `scale` multiplies a coordinate in the
    small frame back up to the original's pixels.

    Returned rather than recomputed by the caller so there is exactly one
    place the two can disagree. A wrong scale here does not crash: it puts
    every cursor at a fraction of its true position, which looks like a bad
    homography and would be debugged as one.
    """
    import cv2      # noqa: WPS433

    height, width = frame.shape[:2]
    if target_width <= 0 or width <= target_width:
        return frame, 1.0
    scale = width / float(target_width)
    target_height = max(1, int(round(height / scale)))
    small = cv2.resize(frame, (target_width, target_height),
                       interpolation=cv2.INTER_AREA)
    return small, scale


# ---------------------------------------------------------------------------
# Model rungs (doc section 11.2, translated — see the module docstring)
# ---------------------------------------------------------------------------

def available_rungs(models_dir: Path = MODELS_DIR) -> List[str]:
    """The model bundles actually present, cheapest first. Absent files are
    skipped rather than being an error — a rig with one bundle has a
    one-rung ladder, which is a fact about the rig, not a fault.
    """
    out = []
    for name in backend_mediapipe.MODEL_RUNGS:
        path = Path(models_dir) / name
        if path.is_file():
            out.append(str(path))
    return out


# ---------------------------------------------------------------------------
# The process body
# ---------------------------------------------------------------------------

class TrackerProcess:
    """Everything except the sockets' lifecycle, so a test can drive it one
    tick at a time with a fake ring and a scripted backend — the same split
    `CameraProcess`, `Classifier` and `Core` already use.
    """

    def __init__(self, *,
                 source: Optional[FrameSource] = None,
                 backend: Optional[Backend] = None,
                 sender: Optional[cursorbus.Sender] = None,
                 send_stat: Optional[Callable[[Dict[str, Any]], Any]] = None,
                 input_width: int = DEFAULT_INPUT_WIDTH,
                 emit_hz: float = 60.0) -> None:
        self.source = source or FrameSource()
        self.backend: Backend = backend or backend_stub.Stub()
        self.sender = sender or cursorbus.Sender()
        self.send_stat = send_stat or (lambda msg: None)
        self.input_width = input_width
        self.emit_hz = emit_hz
        self.tracker = tracking.HandTracker()

        # Doc section 5.3: core owns `H_cam_to_stage` and pushes it in
        # `welcome`. None until it has. Held under a lock because `welcome`
        # arrives on the control link's read thread while the capture loop
        # reads it — the same split every other link in this tree has.
        self._lock = threading.Lock()
        self._h: Optional[List[List[float]]] = None
        self._stage = (1920.0, 1080.0)

        self._warned_no_h = False
        self._stale = False
        self._last_emit: Optional[float] = None
        # MediaPipe's VIDEO mode rejects a timestamp that does not
        # increase, and this process owns the clock (backend.py's
        # docstring) so a backend swap mid-probe cannot restart it.
        self._timestamp_ms = 0

        self.frames_seen = 0
        self.emitted = 0
        self._probe_started: Optional[float] = None
        self._probe_frames = 0
        self.measured_fps: Optional[float] = None

        self._stop = threading.Event()

    # -- configuration from core (doc section 4.2's `welcome`) -------------

    def apply_welcome(self, cfg: Dict[str, Any]) -> None:
        """Doc section 4.2: "core replies to hello with the client's current
        configuration, so clients hold no config of their own beyond how to
        find core."

        Tolerant of every field being absent: a `welcome` from a core that
        has no homography yet is the ordinary first-boot case, not an
        error. A malformed `H` is treated as no `H` — better to emit
        nothing than to emit through a matrix that came out of a bad line.
        """
        if not isinstance(cfg, dict):
            return
        h = cfg.get("homography_cam_to_stage")
        with self._lock:
            if _is_matrix3x3(h):
                self._h = [[float(v) for v in row] for row in h]
                self._warned_no_h = False
            else:
                self._h = None
            stage = cfg.get("stage")
            if (isinstance(stage, (list, tuple)) and len(stage) == 2
                    and all(isinstance(v, (int, float)) for v in stage)):
                self._stage = (float(stage[0]), float(stage[1]))
        hz = cfg.get("emit_hz")
        if isinstance(hz, (int, float)) and 0 < hz <= 240:
            self.emit_hz = float(hz)
        mirror = cfg.get("mirror_handedness")
        if isinstance(mirror, bool):
            self.set_mirror_handedness(mirror)

    def set_mirror_handedness(self, mirror: bool) -> None:
        """Doc section 11.3's swap-hands switch, applied live.

        Set on the backend rather than held here because the label has to
        be flipped at the one place it is produced — anything else means
        two spellings of the same hand existing at once somewhere in the
        pipeline. Backends that have no opinion on handedness (the stub)
        simply do not have the attribute.
        """
        if hasattr(self.backend, "mirror_handedness"):
            self.backend.mirror_handedness = bool(mirror)

    @property
    def has_homography(self) -> bool:
        with self._lock:
            return self._h is not None

    # -- one iteration -----------------------------------------------------

    def tick(self, now: Optional[float] = None) -> bool:
        """Read at most one new frame, track, emit. True if a datagram
        went out.

        Returning a bool rather than sleeping internally is what lets the
        tests step this deterministically; `run_forever` below owns the
        sleeping.
        """
        now = time.monotonic() if now is None else now

        # Rate cap FIRST, before a frame is pulled. Doc section 4.6 is "one
        # datagram per camera frame" and `emit_hz` (doc section 8.6) is a
        # ceiling on that, not a clock of its own — so a camera slower than
        # emit_hz simply emits at camera rate, and this is a skip rather
        # than a wait.
        #
        # Before the read, not after, and that ordering is load-bearing: a
        # frame consumed and then discarded advances `last_frame_id` past a
        # frame nobody looked at, so the NEXT tick would see the one after
        # it as "the newest" and the cap would silently halve the effective
        # rate. Skipping the read leaves the ring alone and the next tick
        # picks up whatever is newest then, which is what a cap should do.
        #
        # `_last_emit` starts at None rather than 0.0 so the very first
        # tick is never gated. With 0.0 it depended on `time.monotonic()`
        # being far from zero — true in production, and exactly the kind of
        # accident that holds until someone passes a clock in.
        if (self.emit_hz > 0 and self._last_emit is not None
                and (now - self._last_emit) < (1.0 / self.emit_hz)):
            return False

        frame, info = self.source.next_frame()
        if frame is None:
            if info == "stale":
                self._on_stale()
            return False
        self._on_frames_resumed()
        self.frames_seen += 1

        with self._lock:
            h = self._h
            stage = self._stage
        if h is None:
            if not self._warned_no_h:
                _log.warning("tracker: no camera->stage homography from core "
                             "yet — tracking nothing. Calibrate the table "
                             "corners on the Setup tab (doc 12.6).")
                self._warned_no_h = True
            return False

        small, scale = downsample(frame, self.input_width)
        self._timestamp_ms += 1
        try:
            detections = self.backend.detect(small, self._timestamp_ms)
        except Exception:      # noqa: BLE001 - a detector must not kill the loop
            _log.exception("tracker: %s raised during detect", self.backend.name)
            detections = []

        staged = self._to_stage(detections, scale, h, stage)
        hands = self.tracker.update(staged, now)
        self.sender.send(hands, ts=time.time())
        self._last_emit = now
        self.emitted += 1
        self._count_probe_frame(now)
        return True

    def _to_stage(self, detections: Sequence[Detection], scale: float,
                  h: Sequence[Sequence[float]],
                  stage) -> List[Detection]:
        """Downsampled-frame pixels -> capture pixels -> stage space.

        Two steps, in this order, and neither is optional. The backend
        returned coordinates in the small frame it was handed
        (`backend.py`'s docstring), and `H_cam_to_stage` was solved against
        the camera's **capture** resolution (doc section 8.5's
        `camera_size`), so applying `H` to a downsampled coordinate would
        be applying it to a point in a space it was never fitted for.

        Points off the stage are kept, not clipped. A hand held over the
        table edge is a real hand at a real position, and core's hit tests
        answer "no bin" for it correctly; clamping would pile every
        out-of-range hand onto the border of the nearest bin.
        """
        # CURSOR_SHADOW_CLEARANCE_MM converted to this stage's own Y scale —
        # `stage` comes from core (doc section 5.3), not hardcoded, the same
        # geometry_store.mm_to_stage does for the fixed TABLE_H_MM.
        clearance_px = CURSOR_SHADOW_CLEARANCE_MM * stage[1] / _TABLE_H_MM

        out: List[Detection] = []
        for det in detections:
            try:
                sx, sy = geometry.apply(h, (det.x * scale, det.y * scale))
            except geometry.GeometryError:
                # A point that maps to infinity through a badly conditioned
                # matrix. Dropping the hand is right: there is no position
                # to report, and reporting a huge number would be a cursor
                # somewhere off in the corner of nothing.
                continue
            out.append(Detection(x=sx, y=sy - clearance_px, conf=det.conf,
                                 handedness=det.handedness))
        return out

    # -- doc section 6.4's staleness ---------------------------------------

    def _on_stale(self) -> None:
        if self._stale:
            return
        self._stale = True
        _log.warning("tracker: no camera frames for %.1fs — going quiet "
                     "(doc 6.4)", STALE_S)
        # Doc section 6.4's second bullet, verbatim in shape.
        self.send_stat({"t": "stat", "who": "tracker", "frames_stale": True})
        # Roles do not survive an outage — see HandTracker.reset's docstring.
        self.tracker.reset()

    def _on_frames_resumed(self) -> None:
        if not self._stale:
            return
        self._stale = False
        _log.info("tracker: camera frames resumed")
        self.send_stat({"t": "stat", "who": "tracker", "frames_stale": False})

    # -- doc section 11.2's probe ------------------------------------------

    def _count_probe_frame(self, now: float) -> None:
        if self.measured_fps is not None:
            return
        if self._probe_started is None:
            self._probe_started = now
            self._probe_frames = 0
            return
        self._probe_frames += 1
        elapsed = now - self._probe_started
        if elapsed < PROBE_SECONDS:
            return
        self.measured_fps = self._probe_frames / elapsed if elapsed > 0 else 0.0
        _log.info("tracker: %s held %.1f fps over %.0fs (%d frames)",
                  self.backend.name, self.measured_fps, elapsed,
                  self._probe_frames)
        self._report_rung()

    def _report_rung(self) -> None:
        """Doc section 11.2's "log which rung it settled on".

        Says plainly when there is nothing to climb to. The alternative —
        logging "settled on rung 0" for a ladder with one rung — reads like
        a probe that ran and would stop anyone ever asking why the higher
        model is not being used.
        """
        rungs = available_rungs()
        if self.measured_fps is None:
            return
        if len(rungs) < 2:
            _log.info("tracker: one model bundle available (%s) — nothing to "
                      "probe upward to. Doc 11.2's ladder needs a second "
                      "`.task` bundle in models/ to have a second rung.",
                      rungs[0] if rungs else "none")
            return
        if self.measured_fps > PROBE_CLIMB_ABOVE_FPS:
            _log.info("tracker: %.1f fps is above %.0f — a heavier bundle is "
                      "worth trying (doc 11.2). Set it in models/ and "
                      "restart; keep it only above %.0f fps.",
                      self.measured_fps, PROBE_CLIMB_ABOVE_FPS,
                      PROBE_KEEP_ABOVE_FPS)

    # -- the loop ----------------------------------------------------------

    def run_forever(self) -> None:
        while not self._stop.is_set():
            if not self.tick():
                # Nothing to do this instant. Every reason for that (no new
                # frame, no camera, no homography, rate-capped) is answered
                # by the same short wait.
                self._stop.wait(IDLE_SLEEP_S)

    def stop(self) -> None:
        self._stop.set()
        self.backend.close()
        self.sender.close()


def _is_matrix3x3(h: Any) -> bool:
    if not isinstance(h, (list, tuple)) or len(h) != 3:
        return False
    for row in h:
        if not isinstance(row, (list, tuple)) or len(row) != 3:
            return False
        for v in row:
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                return False
            if v != v:      # NaN
                return False
    return True


def build_backend(cfg: Dict[str, Any],
                  models_dir: Optional[Path] = None) -> Backend:
    """The real detector if everything it needs is present, else the stub.

    Falling back rather than exiting is doc section 3.3's rule: this
    process's job is to come up and hold its link open. A tracker that
    refused to start over a missing model file would take its own pip red,
    which reads as a crash rather than as "download the bundle".

    `models_dir` is a parameter rather than a module read for the reason
    every other path in this repo is (`cal_path`, `camera_grid_path`): a
    default argument binds at definition time, so a test that reassigned
    the module constant would still be handed the real `models/` — and on
    this machine that directory has a real bundle in it, so the fallback
    test would have passed by loading MediaPipe rather than by falling
    back. Found by that test failing.
    """
    models_dir = Path(models_dir) if models_dir is not None else MODELS_DIR
    rungs = available_rungs(models_dir)
    if not rungs:
        _log.warning("tracker: no MediaPipe model bundle in %s — running the "
                     "stub backend, no hands will be tracked. See "
                     "models/README.md.", models_dir)
        return backend_stub.Stub()
    mirror = bool(config.get(cfg, "tracker.mirror_handedness", False))
    max_hands = int(config.get(cfg, "tracker.max_hands", 2) or 2)
    real = backend_mediapipe.MediaPipeBackend.load(
        rungs[0], num_hands=max_hands, mirror_handedness=mirror)
    return real if real is not None else backend_stub.Stub()


def main() -> None:
    log.setup("tracker")
    cfg = config.load()
    host = config.get(cfg, "core.host", CORE_HOST)
    port = config.get(cfg, "core.control_port", CORE_PORT)

    proc = TrackerProcess(
        backend=build_backend(cfg),
        sender=cursorbus.Sender([
            ("127.0.0.1", int(config.get(cfg, "cursor.of_port",
                                         cursorbus.OF_PORT))),
            ("127.0.0.1", int(config.get(cfg, "cursor.core_port",
                                         cursorbus.CORE_PORT))),
        ]),
        input_width=int(config.get(cfg, "tracker.input_width",
                                   DEFAULT_INPUT_WIDTH)),
        emit_hz=float(config.get(cfg, "tracker.emit_hz", 60)),
    )

    client = wire.Client(host, port, "tracker",
                         on_connect=proc.apply_welcome,
                         on_message=lambda msg: _on_control(proc, msg))
    proc.send_stat = client.send
    beat = health.Heartbeat(client.send, who="tracker")

    client.start()
    beat.start()
    # Same readiness rule as every other client (common/stub.py): ready
    # means the link is open and the loop is running, not that core has
    # answered — doc section 3.3 makes start order an optimisation.
    log.ready("tracker")
    try:
        proc.run_forever()
    finally:
        beat.stop()
        client.stop()
        proc.stop()


def _on_control(proc: TrackerProcess, msg: Dict[str, Any]) -> None:
    """The one thing core tells the tracker after `welcome`: that a config
    it cares about changed (doc section 11.3's swap-hands button, which is
    "fastest to determine by trying it" and therefore has to apply without
    a restart).
    """
    if msg.get("t") == "cfg":
        proc.apply_welcome(msg.get("cfg") or {})


if __name__ == "__main__":
    main()
