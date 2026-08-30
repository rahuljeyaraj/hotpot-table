"""Classifier process — the vision process (doc sections 3, 3.2, 4.7;
doc section 21, M4 build items 2 and 7).

Was `common/stub.py` from M0 through M4.1. From here on it is what doc
section 3 gives `classifier`: it attaches to the shared-memory frame ring,
sleeps until core wakes it, and does all frame analysis that is not hand
tracking. Doc section 3.2's phrasing is the right mental model — this is
"the vision process". Camera-to-projector calibration is table geometry the
operator places by hand (`core/geometry_store.fit_from_corners`) rather than
anything detected from a frame — automated dot-projection calibration
(`detect_dots`, formerly `classifier/dots.py`) was removed: it needed a
dark, room-light-free rig this project never achieved (CLAUDE.md's
M4h/M4i/M4j).

Of doc section 4.7's commands, this process implements:

    capture       M4 build item 7 — dataset crops, answered `captured`
    stop          cancels a live command

`classify` is not implemented and deliberately so: it needs a backend
(doc section 19.4's `backend_ei.py` / `backend_stub.py`) and a trained
model, which is M7. An unknown or unimplemented `op` is answered with an
error rather than ignored — a wizard waiting on a reply that will never
come is a hung screen with nothing to look at.

Why the work runs on a worker thread
------------------------------------
`wire.Client`'s `on_message` runs on the link's own read thread. A capture
burst is ten frames a couple of seconds apart (doc section 12.7) and a dot
detection is tens of milliseconds; doing either inline would stall the
heartbeat the link is also responsible for, and core would mark this
process dead in three seconds (doc section 4.2) in the middle of a
successful capture. One worker thread, one command at a time — the
classifier has no reason to run two analyses at once, and serialising
them means a `stop` has exactly one thing to cancel.

Reconnecting to the ring
------------------------
The ring belongs to `camera`, which may not be up yet, may die, and comes
back with a new segment (doc section 20.1: "recreate shm, consumers
re-attach"). So the reader is opened lazily and re-opened on any failure,
rather than once at startup — a classifier that exited because the camera
was slow to start would break doc section 3.3's "any process may start,
die and restart in any order".
"""

from __future__ import annotations

import concurrent.futures
import logging
import queue
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from hotpot.classifier import backend_ei, backend_rf, backend_stub
from hotpot.common import atomicio, config, framebus, geometry, health, log, wire

_log = logging.getLogger("hotpot.classifier")

CORE_HOST = "127.0.0.1"
CORE_PORT = 8765          # doc section 4.1 default

_ROOT = Path(__file__).resolve().parents[3]
CAPTURES_DIR = _ROOT / "datasets" / "captures"
CAMERA_SETTINGS_PATH = _ROOT / "state" / "camera_settings.json"

# Doc section 6.4's staleness bound. A command that arrives while the
# camera is dead must fail with that sentence rather than analyse a frame
# from thirty seconds ago and report a confident answer about it.
STALE_S = 0.5

# Doc section 12.7's burst default: N frames, a fixed INTERVAL apart —
# "so the operator can nudge the tray between frames". Originally specified
# as a total period (N frames over M seconds); changed to frames+interval
# because a total period forces the operator to do the arithmetic backwards
# ("if I want 3s to rearrange the tray, what do I even type here") and
# because a period gives no per-shot signal at all — see `capture_progress`
# below, which needs a fixed per-shot gap to be worth sending.
DEFAULT_BURST = 1
DEFAULT_INTERVAL_S = 2.0
MAX_BURST = 60
MAX_INTERVAL_S = 30.0

JPEG_QUALITY = 92         # dataset images; visibly lossless at bin-crop size


class ClassifierError(Exception):
    """Something the operator needs told about in a sentence — no ring, a
    stale camera, a rect off the edge of the frame. Distinct from a bug,
    which is logged with a traceback and answered with a generic error.
    """


# ---------------------------------------------------------------------------
# Frames
# ---------------------------------------------------------------------------

class RingSource:
    """The frame ring, opened lazily and re-opened after any failure.

    `frame()` returns a numpy BGR array or raises `ClassifierError` with a
    sentence an operator can act on. It never returns a stale frame: doc
    section 6.4 makes staleness the consumer's job to notice, and a
    calibration solved against the last frame before the camera died would
    be a homography nobody could explain later.
    """

    def __init__(self, name: str = framebus.SHM_NAME,
                 open_reader: Optional[Callable[[], Any]] = None) -> None:
        self.name = name
        # Injectable for the same reason `ScaleReader.open_port` is: the
        # tests must be able to drive this with no camera process and no
        # shared-memory segment created by anyone else.
        self._open_reader = open_reader or (lambda: framebus.FrameReader(name))
        self._reader: Optional[Any] = None

    def _ensure(self):
        if self._reader is None:
            try:
                self._reader = self._open_reader()
            except (FileNotFoundError, ValueError) as e:
                raise ClassifierError(
                    "no camera frames yet — is the camera process running?"
                ) from e
        return self._reader

    def drop(self) -> None:
        """Forget the current reader so the next call re-attaches. Called
        after any read failure, because the ordinary cause is that camera
        restarted and this segment is now a corpse.
        """
        reader, self._reader = self._reader, None
        if reader is not None:
            try:
                reader.close()
            except Exception:      # noqa: BLE001 - closing a dead segment
                pass

    def frame(self):
        import numpy as np      # noqa: WPS433 - local, see geometry.fit

        reader = self._ensure()
        try:
            if reader.is_stale(timeout_s=STALE_S):
                raise ClassifierError(
                    "the camera stopped sending frames — nothing to look at")
            f = reader.read()
        except ClassifierError:
            raise
        except Exception as e:  # noqa: BLE001 - a dead segment reads as anything
            self.drop()
            raise ClassifierError(
                "lost the connection to the camera's frames") from e
        if f is None:
            raise ClassifierError(
                "could not read a clean frame — the camera may have just "
                "restarted")
        arr = np.frombuffer(f.data, dtype=np.uint8)
        return arr.reshape((reader.height, reader.width, reader.channels))

    def averaged_frame(self, count: int, *, timeout_s: float = 3.0):
        """`count` DISTINCT frames, averaged into one.

        Restored from the old solver, which measured why it is needed
        (`tools/calibration/solve_homography.py`): during a solve the dots
        sit only 25-50 grey levels above the board, "which is the same
        order as this sensor's frame-to-frame noise. Averaging is what
        makes the outer dots separable at all." Nothing is warped or
        shifted, so the result is still an ordinary camera frame — it is
        the same scene with the noise divided by sqrt(count).

        Distinct is the load-bearing word. `frame()` returns whatever is
        currently in the ring, so a tight loop would re-read one frame
        `count` times and average noise with itself, which is a no-op that
        looks exactly like a working one. Frames are collected by watching
        `frame_id` change.

        Falls back to however many arrived before `timeout_s`, and to a
        single `frame()` if the ring is not advancing at all — a slow or
        stalled camera should degrade the calibration's noise floor, not
        refuse to calibrate.
        """
        import numpy as np      # noqa: WPS433 - local, see geometry.fit

        if count <= 1:
            return self.frame()
        reader = self._ensure()
        acc = None
        collected = 0
        seen_ids = set()
        deadline = time.monotonic() + timeout_s
        while collected < count and time.monotonic() < deadline:
            if reader.is_stale(timeout_s=STALE_S):
                raise ClassifierError(
                    "the camera stopped sending frames — nothing to look at")
            f = reader.read()
            if f is None or f.frame_id in seen_ids:
                # Nothing new yet. The ring is written at capture rate and
                # read here as fast as the loop goes round, so this is the
                # ordinary case, not an error.
                time.sleep(0.005)
                continue
            seen_ids.add(f.frame_id)
            arr = np.frombuffer(f.data, dtype=np.uint8).astype(np.float32)
            acc = arr if acc is None else acc + arr
            collected += 1
        if acc is None or collected == 0:
            return self.frame()
        if collected < count:
            _log.warning("classifier: averaged only %d of %d frames before "
                         "the timeout — is the camera keeping up?",
                         collected, count)
        out = (acc / collected).astype(np.uint8)
        return out.reshape((reader.height, reader.width, reader.channels))

    @property
    def size(self) -> Optional[Tuple[int, int]]:
        if self._reader is None:
            return None
        return (self._reader.width, self._reader.height)


# ---------------------------------------------------------------------------
# The commands
# ---------------------------------------------------------------------------

def crop_rect(frame, rect) -> Tuple[Any, int, int]:
    """One camera-space rect out of a frame, clamped to the frame, plus
    the top-left corner it was actually cropped at.

    The offset is what lets a caller put a detected point back into full
    camera-space after finding it in the crop — see `_detect_dots`'s ROI,
    which crops away everything outside the table (a room lamp at the
    frame edge, chiefly — CLAUDE.md's M4i) before running the detector,
    and then has to undo exactly this offset on every point it gets back.

    Clamped rather than refused when a rect hangs off the edge: an
    operator dragging a rect on the Setup tab can easily push one a few
    pixels past the frame border, and losing a whole capture session to
    that is worse than a crop a few pixels narrower than asked for. A rect
    entirely outside the frame is a different thing and does raise —
    there is no image there at all.
    """
    x, y, w, h = (float(v) for v in rect)
    fh, fw = frame.shape[:2]
    x0 = max(0, int(round(x)))
    y0 = max(0, int(round(y)))
    x1 = min(fw, int(round(x + w)))
    y1 = min(fh, int(round(y + h)))
    if x1 <= x0 or y1 <= y0:
        raise ClassifierError(
            f"rect {rect} is outside the {fw}x{fh} camera frame")
    return frame[y0:y1, x0:x1], x0, y0


def crop(frame, rect) -> Any:
    """`crop_rect` without the offset — dataset capture (`_capture` below)
    writes camera-space rects into the sidecar itself, so it has no need
    for where the crop landed."""
    return crop_rect(frame, rect)[0]


def _safe_label(label: Any) -> str:
    """A label becomes a directory name, so it may not become `..` or an
    absolute path. Doc section 8.1's `class_name` values are plain
    identifiers; anything else is either a typo or an attempt.
    """
    text = str(label or "").strip()
    keep = [c for c in text if c.isalnum() or c in ("_", "-")]
    out = "".join(keep).strip("-_").lower()
    if not out:
        raise ClassifierError(f"{label!r} is not a usable label")
    return out


class Classifier:
    """The process body, separated from `main()` so tests can drive it
    with a fake ring and a fake link — the same split `CameraProcess` and
    `Core` already use.
    """

    def __init__(self, *,
                 source: Optional[RingSource] = None,
                 send: Optional[Callable[[Dict[str, Any]], Any]] = None,
                 captures_dir: Path = CAPTURES_DIR,
                 settings_path: Path = CAMERA_SETTINGS_PATH,
                 backend: Optional[Any] = None) -> None:
        self.source = source or RingSource()
        self.send = send or (lambda msg: None)
        self.captures_dir = Path(captures_dir)
        self.settings_path = Path(settings_path)
        # Doc section 19.4's backend split: `main()` picks the real one
        # off `config.classifier.backend`; a test (or an M0-era boot with
        # no model built yet) gets `StubBackend`'s deterministic cycle so
        # `classify` always has something to answer with rather than the
        # M7-not-built refusal this used to be unconditionally.
        self.backend = backend or backend_stub.StubBackend()

        self._queue: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._cancel = threading.Event()

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run,
                                        name="classifier-work", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._cancel.set()
        self._queue.put(None)
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(3.0)

    # -- the link ----------------------------------------------------------

    def on_message(self, msg: Dict[str, Any]) -> None:
        """`wire.Client`'s callback. Runs on the link's read thread, so it
        does no work at all beyond queueing — see the module docstring.
        """
        if msg.get("t") != "cmd":
            return
        if msg.get("op") == "stop":
            # Handled here, not on the worker: the whole point of `stop`
            # is that it arrives while the worker is busy.
            self._cancel.set()
            return
        self._queue.put(msg)

    def _run(self) -> None:
        while not self._stop.is_set():
            msg = self._queue.get()
            if msg is None:
                return
            self._cancel.clear()
            try:
                self._dispatch(msg)
            except ClassifierError as e:
                self._error(msg, str(e))
            except Exception:      # noqa: BLE001 - a worker must not die
                _log.exception("classifier: %r failed", msg.get("op"))
                self._error(msg, "the classifier hit an internal error — "
                                 "see the log")

    def _dispatch(self, msg: Dict[str, Any]) -> None:
        op = msg.get("op")
        if op == "capture":
            self._capture(msg)
        elif op == "classify":
            self._classify(msg)
        else:
            raise ClassifierError(f"unknown command {op!r}")

    def _error(self, msg: Dict[str, Any], text: str) -> None:
        self.send({"t": "result", "id": msg.get("id"), "op": msg.get("op"),
                   "ok": False, "error": text})

    # -- capture (doc sections 4.7, 12.7 — M4 build item 7) ----------------

    def _capture(self, msg: Dict[str, Any]) -> None:
        """Doc section 12.7's dataset capture.

        The lighting rule is enforced by not having a lighting path
        here. Doc section 12.7 is explicit: "capture must run with the
        bin patches lit exactly as serving mode lights them… The Capture
        tab must therefore drive the same bin-patch path as serving mode,
        not its own." This process cannot change what the projector is
        doing and has no way to ask — it photographs whatever is on the
        table. The only way the rule could be broken is if the *table*
        were showing something else, and that is gated on the core side
        (`core/main.py` refuses a capture while dot calibration's black
        field is up).

        The table crop happens here, not in core. Core owns the
        homography and the camera bin grid but never touches a frame (a
        hard invariant), so it sends `h` and `stage_size` alongside the
        grid-derived `rects` and this is the process that actually warps —
        `common.geometry.warp_frame_to_stage` — before cropping. `rects`
        are in the warped frame's own pixel space, the same space the
        camera bin grid is dragged in (`core/bin_grid.py`'s docstring).
        """
        import cv2      # noqa: WPS433

        rects = msg.get("rects") or []
        labels = msg.get("labels") or []
        if not rects:
            raise ClassifierError("no rects to capture")
        if len(labels) != len(rects):
            raise ClassifierError(
                f"{len(rects)} rects but {len(labels)} labels — every crop "
                "must be told what it is a picture of")
        h = msg.get("h")
        if (not isinstance(h, list) or len(h) != 3
                or any(not isinstance(row, list) or len(row) != 3
                       for row in h)):
            raise ClassifierError(
                "no homography to crop against — calibrate the table "
                "corners before capturing")
        stage_size = msg.get("stage_size")
        if (not isinstance(stage_size, list) or len(stage_size) != 2
                or not all(isinstance(v, (int, float)) for v in stage_size)):
            raise ClassifierError("no stage size to warp the frame into")
        burst = msg.get("burst", DEFAULT_BURST)
        burst = int(burst) if isinstance(burst, (int, float)) else DEFAULT_BURST
        burst = max(1, min(MAX_BURST, burst))
        # `interval` is seconds between one shot and the next, directly —
        # not a total period divided across the burst. That division used
        # to live here (`seconds / burst`), which made the tablet's "over
        # N seconds" field the wrong knob: raising the frame count silently
        # shortened every gap, right when the operator wanted more time to
        # rearrange the tray, not less.
        interval = msg.get("interval", DEFAULT_INTERVAL_S)
        interval = (float(interval) if isinstance(interval, (int, float))
                    else DEFAULT_INTERVAL_S)
        interval = max(0.0, min(MAX_INTERVAL_S, interval))
        gap = interval if burst > 1 else 0.0

        # Read once for the whole burst, not per frame: doc section 12.7's
        # sidecar records "the exposure/WB and `field_level` values from
        # `state/camera_settings.json`", and those cannot change during a
        # burst without the capture being invalid anyway.
        lighting = atomicio.read_json(self.settings_path, {})

        files: List[str] = []
        for shot in range(burst):
            if self._cancel.is_set():
                break
            if shot:
                # `_cancel.wait` rather than `time.sleep`: a `stop` sent
                # mid-gap must land within a tick, not wait out the whole
                # interval the operator was told they had left.
                if self._cancel.wait(gap):
                    break
            raw_frame = self.source.frame()
            # The table crop: everything from here on works in the same
            # warped canvas the camera bin grid was dragged against, not
            # the raw sensor frame — see this method's own docstring.
            frame = geometry.warp_frame_to_stage(raw_frame, h, stage_size)
            stamp = int(time.time() * 1000)
            for idx, (rect, label) in enumerate(zip(rects, labels)):
                bin_i = rect[4] if len(rect) > 4 else idx
                safe = _safe_label(label)
                patch = crop(frame, rect[:4])
                out_dir = self.captures_dir / safe
                out_dir.mkdir(parents=True, exist_ok=True)
                name = f"{stamp}_bin{int(bin_i)}"
                jpg = out_dir / f"{name}.jpg"
                ok, buf = cv2.imencode(".jpg", patch,
                                       [int(cv2.IMWRITE_JPEG_QUALITY),
                                        JPEG_QUALITY])
                if not ok:
                    raise ClassifierError("could not encode a crop as JPEG")
                # atomicio, not a plain write: a capture session interrupted
                # by a power cut must not leave a half-written JPEG that
                # `tools/export_edgeimpulse.py` later uploads as training
                # data (doc section 20.4's rule, applied to the dataset).
                atomicio.write_bytes(jpg, buf.tobytes())
                atomicio.write_json(out_dir / f"{name}.json", {
                    "bin": int(bin_i),
                    "label": safe,
                    "rect_cam": [float(v) for v in rect[:4]],
                    "ts": time.time(),
                    "lighting": lighting,
                })
                files.append(str(jpg))
            if burst > 1:
                # Doc section 12.7's counter-and-countdown: one message per
                # shot, sent only for a real burst (a single "Capture all"
                # has nothing to count up to). `_resolve_classifier_reply`
                # does not consume this — core relays it straight to every
                # tablet so the operator sees the count and the next-shot
                # countdown live, not only the final tally.
                self.send({"t": "capture_progress", "id": msg.get("id"),
                           "shot": shot + 1, "burst": burst,
                           "interval": gap})

        # Doc section 4.7's reply shape: `{"t":"captured","id":..,
        # "files":[...]}`.
        self.send({"t": "captured", "id": msg.get("id"), "files": files,
                   "cancelled": self._cancel.is_set()})

    # -- classify (doc sections 4.7, 19.4 — M7 build items 1-3) -------------

    def _classify(self, msg: Dict[str, Any]) -> None:
        """Doc section 4.7's `classify` — one pass over every rect core
        sends, answered with a label and confidence per bin.

        Shares `_capture`'s table-crop discipline and for the same reason:
        core owns the homography and the camera bin grid but never touches
        a frame (a hard invariant), so it sends `h`/`stage_size` alongside
        `rects` and this is the process that warps
        (`geometry.warp_frame_to_stage`) before cropping — the model must
        see the same canvas the operator dragged the bin grid against, not
        raw sensor pixels.

        `msg.get("mode")` is accepted but not branched on: core always
        sends `"once"` today (`core/main.py`'s `_classify_pass` — a
        deliberate simplification of the doc's literal `mode:"live"` wire
        example, explained in that method's own docstring) and repeats it
        on its own timer while in SETTING mode, so "live" classification is
        a property of the *caller's* loop, not a persistent command this
        process would otherwise have to run and separately cancel.

        One backend call per rect, not one batched call — `ClassifierBackend.
        classify()` (doc section 19.4) takes a single crop by design, and a
        confident wrong label on bin 3 must never be able to come from
        code that also looked at bin 4's pixels.

        The 8 calls run concurrently, not one after another — found to
        matter by measurement, not by inspection. `EiCppBackend.
        classify()` shells out to a compiled binary; timed directly on the
        dev machine at ~0.6s per call, which makes 8 sequential calls
        ~5s — right against `core/main.py`'s own `CLASSIFY_LIVE_TIMEOUT_S`
        (5.0) before this method's own warp/crop work or the wire round
        trip are even counted, and in practice a real running core timed
        out on every single live pass because of it (confirmed against it
        directly: zero `classify` broadcasts over several seconds in
        SETTING). Safe to parallelise: each call is an independent,
        CPU-bound subprocess with no shared state (`EiCppBackend` writes
        its own uniquely-named temp file per call; `subprocess.run`
        releases the GIL while the child runs), so a thread pool actually
        runs them at once rather than merely interleaving Python bytecode.
        A bin's own failure is still caught and reported per-bin, same as
        before — concurrency changes nothing about doc section 9.3's "one
        bin's backend failure must not blank the other seven".

        Traded away by this: `_capture`'s per-shot `stop` checkpoint.
        With every bin dispatched at once there is no "next bin" left for
        a `stop` arriving mid-pass to skip — accepted, because a full pass
        is now ~1s instead of ~5s+, so there is much less to interrupt.
        What still holds: a `stop` already set before this method is even
        entered still does no work at all (checked once, up front, same
        spirit as `_capture`'s own first-iteration check).
        """
        import cv2      # noqa: WPS433

        rects = msg.get("rects") or []
        if not rects:
            raise ClassifierError("no rects to classify")
        h = msg.get("h")
        if (not isinstance(h, list) or len(h) != 3
                or any(not isinstance(row, list) or len(row) != 3
                       for row in h)):
            raise ClassifierError(
                "no homography to crop against — calibrate the table "
                "corners before classifying")
        stage_size = msg.get("stage_size")
        if (not isinstance(stage_size, list) or len(stage_size) != 2
                or not all(isinstance(v, (int, float)) for v in stage_size)):
            raise ClassifierError("no stage size to warp the frame into")

        started = time.monotonic()
        raw_frame = self.source.frame()
        frame = geometry.warp_frame_to_stage(raw_frame, h, stage_size)

        def classify_one(idx_rect: Tuple[int, Any]) -> Dict[str, Any]:
            idx, rect = idx_rect
            bin_i = int(rect[4]) if len(rect) > 4 else idx
            try:
                patch = crop(frame, rect[:4])
                label, conf = self.backend.classify(patch)
            except backend_ei.ClassifierBackendError as e:
                # One bin's backend failure (a missing binary, a timed-out
                # subprocess) must not blank out the seven bins that
                # worked — doc section 9.3 already treats "no item_id" as
                # unresolved, which is exactly the right outcome for a bin
                # this pass could not answer for, not a reason to fail the
                # whole command.
                _log.warning("classifier: bin %d classify failed: %s",
                            bin_i, e)
                label, conf = None, 0.0
            return {"i": bin_i, "label": label, "conf": conf}

        if self._cancel.is_set():
            bins: List[Dict[str, Any]] = []
        else:
            with concurrent.futures.ThreadPoolExecutor(
                    max_workers=len(rects)) as pool:
                # `map` preserves `rects`' own order in the result — not a
                # correctness requirement (core keys every entry off its
                # own "i"), just keeps the reply's bin order predictable.
                bins = list(pool.map(classify_one, enumerate(rects)))

        ms = round((time.monotonic() - started) * 1000)
        self.send({"t": "result", "id": msg.get("id"), "bins": bins, "ms": ms})


# ---------------------------------------------------------------------------
# Process entry point
# ---------------------------------------------------------------------------

def build_backend(cfg: Dict[str, Any]) -> Any:
    """Doc section 19.4's selection line: "Selected by `config.classifier.
    backend`." Five values today — `"stub"` (the committed default,
    config/system.default.json), `"ei_cpp"` (this project's native-binary
    substitute for doc section 19.4's `ImageImpulseRunner`, see backend_ei.
    py's own module docstring for why), `"roboflow"`/`"roboflow_onnx"`
    (`docs/ROBOFLOW_PATHWAY.md` §6 step 7 — Path A/Path B of the second,
    additive training/deploy path beside Edge Impulse, see backend_rf.py's
    own module docstring), and anything else falls back to `"stub"` with a
    loud warning rather than crashing the process a typo'd config value
    would otherwise take down. Keep the existing fallback behaviour
    exactly — that rule predates this addition and still applies to it.
    """
    name = config.get(cfg, "classifier.backend", "stub")
    if name == "stub":
        return backend_stub.StubBackend()
    if name == "ei_cpp":
        return backend_ei.EiCppBackend()
    if name == "roboflow":
        return backend_rf.RoboflowInferenceBackend()
    if name == "roboflow_onnx":
        return backend_rf.RoboflowOnnxBackend()
    _log.warning("classifier: unknown classifier.backend %r — using the "
                "stub instead of refusing to start", name)
    return backend_stub.StubBackend()


def main() -> None:
    log.setup("classifier")
    cfg = config.load()
    host = config.get(cfg, "core.host", CORE_HOST)
    port = config.get(cfg, "core.control_port", CORE_PORT)

    worker = Classifier(backend=build_backend(cfg))
    client = wire.Client(host, port, "classifier",
                         on_message=worker.on_message)
    worker.send = client.send
    beat = health.Heartbeat(client.send, who="classifier")

    worker.start()
    client.start()
    beat.start()
    # Same readiness rule as `common/stub.py`: this process's job is to
    # hold a reconnecting link open and be ready to look at frames. It
    # must not wait for camera — doc section 3.3 makes start order an
    # optimisation, not a dependency.
    log.ready("classifier")
    try:
        threading.Event().wait()
    finally:
        beat.stop()
        client.stop()
        worker.stop()


if __name__ == "__main__":
    main()
