#!/usr/bin/env python3
"""Track hands over the table and send their positions to openFrameworks as
projector pixels.

    /hand       <id:int> <x:float> <y:float>    one per detected hand per frame
    /hand/none                                  when nothing is detected

Stage 1 of the build order: real hand, one dot, nothing else. Positions are
sent RAW - no smoothing, no filtering, no prediction - so the true jitter is
visible and measurable before anything gets built to hide it.

Run the oF app first (it opens the OSC receiver), then run this.

MEDIAPIPE API
    This uses the Tasks API (HandLandmarker), not the `mediapipe.solutions.hands`
    that CLAUDE.md section 4 and most tutorials describe. That legacy API has
    been REMOVED by Google - it is present in 0.10.14 and gone by 0.10.35. The
    settings carry over exactly:

        max_num_hands=2     -> num_hands=2
        model_complexity=0  -> the "lite" hand_landmarker.task bundle

    Landmark indices are unchanged, so index 9 is still the palm centre.

TWO THINGS HERE ARE EASY TO GET WRONG AND LOOK ALMOST RIGHT
    The homography direction and the rotated/raw coordinate order. Both are
    asserted at startup rather than left to eyeballing - see
    check_homography_direction(), check_round_trip() and rotated_to_raw().

DETECTION LOGGING
    Every run writes logs/detect_<label>_<stamp>.csv, one row per frame:
    brightness in, hands out. --label says what the operator was doing, and is
    recorded verbatim - nothing here infers a gesture from the landmarks.

    It exists because "the tracker sees nothing" has two very different causes
    that look identical while running: the frame went dark (section 21 of
    CLAUDE.md), or the pose itself is hard for the model. One line of CSV
    separates them after the fact. The startup mean-grey reading cannot - it is
    sampled once during warmup and is stale the moment it prints.
"""

import argparse
import csv
import json
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from mediapipe import Image, ImageFormat
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision
from pythonosc.udp_client import SimpleUDPClient

HERE = Path(__file__).resolve().parent
DEFAULT_HOMOGRAPHY = HERE.parent / "calibration" / "homography.json"

# Beside the script, not beside the caller's cwd - the logs of a run belong
# with the tool that made them regardless of where it was launched from.
DEFAULT_LOG_DIR = HERE / "logs"

# The "lite" bundle, i.e. the old model_complexity=0. Google's own copy is the
# authoritative one, so it is fetched rather than vendored.
DEFAULT_MODEL = HERE / "hand_landmarker.task"
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/latest/hand_landmarker.task"
)

# Palm centre. Landmark 9 is the middle-finger MCP knuckle, which barely moves
# as the fingers open and close, unlike any fingertip.
#
# PROVISIONAL. Revisit once tongs are in the picture: what needs tracking then
# is the tip of the tongs, not the hand holding them, and the offset between
# the two will not be a constant.
PALM_LANDMARK = 9


class TrackerError(RuntimeError):
    """Anything that means the operator has to go and fix something."""


# --- homography ------------------------------------------------------------

def load_homography(path):
    """The stored 3x3, which maps RAW camera pixels -> projector pixels.

    Note the direction. solve_homography.py fits
    cv2.findHomography(camera_points, projector_points), and OpenCV maps src to
    dst, so the saved matrix is ALREADY the direction this script needs. It is
    not inverted here.

    Inverting it would not blow up. It would produce a well-formed mapping that
    still puts a dot on the table and still moves it when the hand moves, just
    wrongly - which is exactly why the direction is asserted below rather than
    assumed.
    """
    if not path.exists():
        raise TrackerError(
            f"no homography at {path}\n"
            f"  run tools/calibration/solve_homography.py first"
        )

    data = json.loads(path.read_text(encoding="utf-8"))
    H = np.array(data["matrix"], dtype=np.float64)
    if H.shape != (3, 3):
        raise TrackerError(f"{path}: matrix is {H.shape}, expected (3, 3)")
    return H, data


def check_homography_direction(H, data, tolerance_px):
    """Assert H maps camera -> projector, using the points it was fitted on.

    This is the check that actually catches a reversed matrix. The stored
    correspondences are known good, so pushing the camera points through H must
    land them on the projector points to within the error recorded at solve
    time. A reversed matrix misses by hundreds of pixels and cannot squeak past.
    """
    detection = data.get("detection", {})
    cam = np.array(detection.get("camera_points", []), dtype=np.float64)
    proj = np.array(detection.get("projector_points", []), dtype=np.float64)
    if len(cam) == 0 or len(cam) != len(proj):
        raise TrackerError(
            "homography.json has no usable camera/projector correspondences, "
            "so the direction of the matrix cannot be verified - re-run "
            "solve_homography.py"
        )

    mapped = cv2.perspectiveTransform(cam.reshape(-1, 1, 2), H).reshape(-1, 2)
    errors = np.linalg.norm(mapped - proj, axis=1)

    if errors.max() > tolerance_px:
        raise TrackerError(
            f"the stored camera points do not map onto the stored projector "
            f"points: max error {errors.max():.1f} px against a tolerance of "
            f"{tolerance_px:.1f} px.\n"
            f"  the matrix is probably the wrong way round, or "
            f"homography.json is stale.\n"
            f"  expected direction: camera px -> projector px"
        )

    print(f"direction check  : PASSED, camera -> projector on {len(cam)} solve "
          f"points, max {errors.max():.2f} px")


def check_round_trip(H, H_inv, tolerance_px):
    """Push known projector points out to camera space and back again.

    Catches a matrix that is singular or badly conditioned, which the direction
    check cannot: a poorly conditioned H can still map its own fit points
    acceptably while inverting to nonsense.
    """
    probes = np.array(
        [(x, y) for y in (100.0, 540.0, 980.0) for x in (60.0, 960.0, 1860.0)],
        dtype=np.float64,
    )

    to_camera = cv2.perspectiveTransform(
        probes.reshape(-1, 1, 2), H_inv
    ).reshape(-1, 2)
    back = cv2.perspectiveTransform(
        to_camera.reshape(-1, 1, 2), H
    ).reshape(-1, 2)
    errors = np.linalg.norm(back - probes, axis=1)

    if errors.max() > tolerance_px:
        raise TrackerError(
            f"projector -> camera -> projector round trip drifted "
            f"{errors.max():.3f} px against a tolerance of {tolerance_px:.1f} "
            f"px - the homography is near-singular and cannot be trusted"
        )

    print(f"round trip       : PASSED, max {errors.max():.4f} px over "
          f"{len(probes)} probe points (tolerance {tolerance_px:.1f} px)")


# --- camera ----------------------------------------------------------------

def open_camera(args):
    """Same backend and resolution as solve_homography.py, deliberately.

    MSMF, not DSHOW. Through DSHOW this camera runs auto-exposure that ignores
    CAP_PROP_EXPOSURE and clips the white table to 255. Beyond exposure, the
    homography was solved on an MSMF frame at 1920x1080 - opening the camera
    any other way risks a different crop or field of view, and then the mapping
    is quietly wrong.
    """
    backends = {
        "msmf": cv2.CAP_MSMF,
        "dshow": cv2.CAP_DSHOW,
        "any": cv2.CAP_ANY,
    }
    cap = cv2.VideoCapture(args.camera, backends[args.backend])
    if not cap.isOpened():
        raise TrackerError(
            f"could not open camera index {args.camera} with the "
            f"{args.backend} backend"
        )

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)

    # Exposure must be set explicitly. The driver default on this rig produces a
    # frame averaging 27/255 - the hand is plainly there to a human eye, and
    # MediaPipe finds nothing in it at any rotation or confidence threshold.
    # Raising it lands the average near 121 and the same hand is detected
    # immediately.
    #
    # This is the opposite of what calibration wants. solve_homography.py needs
    # a dark frame so the projected dots stay separable from a white table;
    # tracking needs the table itself lit. Same camera, same backend, opposite
    # exposure - so neither script may rely on the driver default.
    #
    # Manual rather than auto on purpose: auto works today but would hunt once
    # the projector starts painting bright UI, and a hunting exposure changes
    # the image mid-pick. CAP_PROP_EXPOSURE is honoured by MSMF (it is DSHOW
    # that ignores it).
    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, args.auto_exposure)
    if args.exposure is not None:
        cap.set(cv2.CAP_PROP_EXPOSURE, args.exposure)

    actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if (actual_w, actual_h) != (args.width, args.height):
        cap.release()
        raise TrackerError(
            f"asked for {args.width}x{args.height}, camera gave "
            f"{actual_w}x{actual_h} - the homography was solved at "
            f"{args.width}x{args.height} and does not apply at another "
            f"resolution"
        )

    frame = None
    for _ in range(max(1, args.warmup)):
        ok, f = cap.read()  # first frames off a UVC camera are stale or half-exposed
        if ok:
            frame = f

    print(f"camera           : index {args.camera}, {args.backend}, "
          f"{actual_w}x{actual_h}")

    # A too-dark frame is the failure that looks like a broken tracker: the
    # pipeline runs, the FPS is fine, and it just reports zero hands forever.
    # Say so at startup instead of leaving it to be discovered with a hand
    # already over the table.
    if frame is not None:
        mean = float(frame.mean())
        note = "" if mean >= args.min_mean_grey else (
            f"  <- TOO DARK, expect zero detections; raise --exposure"
        )
        print(f"exposure         : mean grey {mean:.1f}/255{note}")

    return cap


def check_capture_resolution(data, args):
    """The frame the homography was solved on must be the frame being fed in."""
    res = data.get("capture_resolution")
    if not res:
        return
    solved = (int(res["width"]), int(res["height"]))
    if solved != (args.width, args.height):
        raise TrackerError(
            f"homography was solved at {solved[0]}x{solved[1]} but this run "
            f"asks for {args.width}x{args.height} - re-solve, or pass "
            f"--width/--height to match"
        )


# --- model -----------------------------------------------------------------

def ensure_model(path):
    """Fetch Google's hand landmark bundle once, on first run."""
    if path.exists():
        return path

    print(f"model            : {path.name} not present, fetching once from "
          f"{MODEL_URL}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(MODEL_URL, path)
    except Exception as e:
        raise TrackerError(
            f"could not download the hand landmark model: {e}\n"
            f"  fetch it by hand from {MODEL_URL}\n"
            f"  and save it as {path}"
        )
    return path


# --- coordinates -----------------------------------------------------------

def rotated_to_raw(x_rot, y_rot, width, height):
    """Undo the 180-degree rotation that was applied for MediaPipe's benefit.

    The camera is mounted 180 degrees relative to the projector, so a frame
    straight off the sensor shows the table - and any hand on it - upside down.
    MediaPipe wants an upright hand, so it is handed a rotated COPY.

    The homography, though, was fitted against RAW camera coordinates. So every
    landmark has to come back into raw space before it goes anywhere near the
    matrix. A 180-degree rotation is its own inverse, hence the same expression
    in both directions.

    Feeding rotated coordinates straight into the homography produces a dot
    that tracks the hand perfectly smoothly and lands in the wrong half of the
    table. Never do it.
    """
    return (width - 1 - x_rot, height - 1 - y_rot)


def camera_to_projector(H, x_cam, y_cam):
    """One raw camera pixel -> one projector pixel."""
    pt = np.array([[[x_cam, y_cam]]], dtype=np.float64)
    mapped = cv2.perspectiveTransform(pt, H)
    return float(mapped[0][0][0]), float(mapped[0][0][1])


# --- detection log ---------------------------------------------------------

class DetectionLog:
    """One CSV row per frame, plus a rolling summary on stderr.

    The summary goes to stderr, not stdout, for the reason in CLAUDE.md section
    21: Python block-buffers stdout when it is redirected to a file, so a
    stdout summary would sit unflushed for minutes - exactly when it is being
    logged, i.e. exactly when it matters. stderr is unbuffered and arrives.

    Rows are flushed as they are written. A run that ends with the process
    killed rather than ctrl-c is still worth reading, and 30 small writes a
    second costs nothing next to MediaPipe.
    """

    def __init__(self, directory, label, window_s):
        self.label = label
        self.window_s = window_s

        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = directory / f"detect_{label}_{stamp}.csv"

        self._fh = self.path.open("w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._fh)
        self._writer.writerow(["iso_time", "frame_idx", "mean_grey", "hands"])
        self._fh.flush()

        self.frame_idx = 0
        self._reset_window(time.perf_counter())

    def _reset_window(self, now):
        self._window_start = now
        self._window_frames = 0
        self._window_detected = 0
        self._window_grey = 0.0

    def record(self, mean_grey, hands):
        """Log this frame, and report if the window has closed."""
        self._writer.writerow([
            datetime.now().isoformat(timespec="milliseconds"),
            self.frame_idx,
            f"{mean_grey:.2f}",
            hands,
        ])
        self._fh.flush()

        self.frame_idx += 1
        self._window_frames += 1
        self._window_grey += mean_grey
        if hands > 0:
            self._window_detected += 1

        now = time.perf_counter()
        if now - self._window_start >= self.window_s:
            self._report(now)

    def _report(self, now):
        frames = self._window_frames
        grey = self._window_grey / frames
        rate = self._window_detected / frames
        print(
            f"[{self.label}] {self.window_s:.0f}s  mean grey {grey:6.1f}/255  "
            f"detected {rate * 100:5.1f}%  ({self._window_detected}/{frames} "
            f"frames)",
            file=sys.stderr,
        )
        self._reset_window(now)

    def close(self):
        self._fh.close()


# --- main loop -------------------------------------------------------------

def make_landmarker(args):
    """HandLandmarker in VIDEO mode.

    VIDEO rather than IMAGE because it keeps tracking state between frames, so
    a hand found once is followed instead of re-detected from scratch - both
    faster and steadier. VIDEO rather than LIVE_STREAM because LIVE_STREAM is
    callback-based and will drop frames to keep up, which would quietly hide
    exactly the jitter and latency this stage exists to measure.
    """
    model_path = ensure_model(args.model)
    options = vision.HandLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
        running_mode=vision.RunningMode.VIDEO,
        num_hands=2,
        min_hand_detection_confidence=args.min_detection_confidence,
        min_hand_presence_confidence=args.min_presence_confidence,
        min_tracking_confidence=args.min_tracking_confidence,
    )
    return vision.HandLandmarker.create_from_options(options)


def run(args):
    H, data = load_homography(args.homography)
    check_capture_resolution(data, args)

    print(f"homography       : {args.homography}")
    print(f"  solved rotation: {data.get('rotation_deg')} deg, mean error "
          f"{data.get('errors_px', {}).get('mean', float('nan')):.2f} px")

    recorded_max = float(data.get("errors_px", {}).get("max", 0.0))
    check_homography_direction(H, data, recorded_max + args.direction_slack_px)

    H_inv = np.linalg.inv(H)
    check_round_trip(H, H_inv, args.round_trip_tolerance_px)

    client = SimpleUDPClient(args.osc_host, args.osc_port)
    print(f"osc              : {args.osc_host}:{args.osc_port}")

    landmarker = make_landmarker(args)
    cap = open_camera(args)

    log = DetectionLog(args.log_dir, args.label, args.report_interval)
    print(f"detection log    : {log.path}")

    frames = 0
    last_report = time.perf_counter()
    started = time.perf_counter()
    print("\ntracking - ctrl-c to stop\n")

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("dropped frame", file=sys.stderr)
                continue

            height, width = frame.shape[:2]

            # Every frame, not once at warmup. A room dimmed after startup is
            # the silent failure in CLAUDE.md section 21, and one warmup
            # reading cannot see it.
            #
            # This is luma grey. The startup exposure line above averages the
            # three BGR channels instead, so the two run a few units apart on
            # the same frame - compare a CSV row against another CSV row, not
            # against the banner.
            mean_grey = float(
                cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).mean()
            )

            # Rotate a COPY, for MediaPipe only. cv2.rotate returns a new
            # array, so `frame` stays raw and nothing downstream can pick up
            # the rotated one by accident.
            upright = cv2.rotate(frame, cv2.ROTATE_180)
            rgb = cv2.cvtColor(upright, cv2.COLOR_BGR2RGB)

            timestamp_ms = int((time.perf_counter() - started) * 1000.0)
            result = landmarker.detect_for_video(
                Image(image_format=ImageFormat.SRGB, data=rgb), timestamp_ms
            )

            hands = result.hand_landmarks
            if hands:
                # id is the detection's index this frame. VIDEO mode keeps its
                # own tracking across frames so the order is usually stable,
                # but it is not a guaranteed persistent identity - two hands
                # can swap ids. Good enough for stage 1; revisit when a hand id
                # has to mean something.
                for hand_id, landmarks in enumerate(hands):
                    lm = landmarks[PALM_LANDMARK]

                    # normalised, relative to the ROTATED frame MediaPipe saw
                    x_rot = lm.x * width
                    y_rot = lm.y * height

                    x_cam, y_cam = rotated_to_raw(x_rot, y_rot, width, height)
                    x_proj, y_proj = camera_to_projector(H, x_cam, y_cam)

                    client.send_message(
                        "/hand", [int(hand_id), float(x_proj), float(y_proj)]
                    )
            else:
                client.send_message("/hand/none", [])

            log.record(mean_grey, len(hands))

            frames += 1
            now = time.perf_counter()
            elapsed = now - last_report
            if elapsed >= 1.0:
                print(f"{frames / elapsed:5.1f} fps   hands {len(hands)}")
                frames = 0
                last_report = now
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        log.close()
        print(f"detection log    : {log.frame_idx} frames written to "
              f"{log.path}")
        landmarker.close()
        cap.release()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--label", required=True,
                   help="what the operator is doing this run, e.g. open or "
                        "fist. Written verbatim into every CSV row and into "
                        "the log filename - it is never inferred from the "
                        "landmarks")
    p.add_argument("--camera", type=int, default=0, help="camera index")
    p.add_argument("--backend", choices=("msmf", "dshow", "any"),
                   default="msmf" if sys.platform == "win32" else "any")
    p.add_argument("--width", type=int, default=1920)
    p.add_argument("--height", type=int, default=1080)
    p.add_argument("--warmup", type=int, default=20,
                   help="frames to discard before tracking starts")
    p.add_argument("--exposure", type=float, default=-4.0,
                   help="CAP_PROP_EXPOSURE; less negative is brighter. The "
                        "driver default is far too dark for detection")
    p.add_argument("--auto-exposure", type=float, default=0.25,
                   help="CAP_PROP_AUTO_EXPOSURE; 0.25 manual, 0.75 auto")
    p.add_argument("--min-mean-grey", type=float, default=60.0,
                   help="warn below this average frame brightness")
    p.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR,
                   help="where the per-frame detection CSV is written")
    p.add_argument("--report-interval", type=float, default=5.0,
                   help="seconds per rolling mean grey / detection rate "
                        "summary on stderr")
    p.add_argument("--homography", type=Path, default=DEFAULT_HOMOGRAPHY)
    p.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    p.add_argument("--osc-host", default="127.0.0.1")
    p.add_argument("--osc-port", type=int, default=12345)
    p.add_argument("--min-detection-confidence", type=float, default=0.5)
    p.add_argument("--min-presence-confidence", type=float, default=0.5)
    p.add_argument("--min-tracking-confidence", type=float, default=0.5)
    p.add_argument("--round-trip-tolerance-px", type=float, default=1.0,
                   help="max allowed drift for projector -> camera -> projector")
    p.add_argument("--direction-slack-px", type=float, default=1.0,
                   help="allowance on top of the recorded reprojection error "
                        "when confirming the matrix direction")
    args = p.parse_args()

    # The label goes into a filename as well as into every row, so a slash or a
    # colon in it would fail at file-open time with an OS error that says
    # nothing about the label. Say it here instead.
    if not args.label or not all(c.isalnum() or c in "-_" for c in args.label):
        raise TrackerError(
            f"--label {args.label!r} must be non-empty and contain only "
            f"letters, digits, dash or underscore - it names the log file"
        )

    run(args)


if __name__ == "__main__":
    try:
        main()
    except TrackerError as e:
        print(f"\nFAILED: {e}", file=sys.stderr)
        sys.exit(1)
