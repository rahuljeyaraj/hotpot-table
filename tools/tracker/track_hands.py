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
    Every run writes logs/detect_<label>_exp<exposure>_<stamp>.csv, one row per
    frame: brightness in, hands out, at a stated exposure. --label says what
    the operator was doing, and is recorded verbatim - nothing here infers a
    gesture from the landmarks.

    ONE EXPOSURE PER RUN, deliberately. Sweeping inside the loop would put
    several settings in one file and leave the camera's settling time tangled
    up with the measurement. To sweep, invoke the script once per value; each
    run is then a separate file that names its own setting.

    It exists because "the tracker sees nothing" has two very different causes
    that look identical while running: the frame went dark (section 21 of
    CLAUDE.md), or the pose itself is hard for the model. One line of CSV
    separates them after the fact. The startup mean-grey reading cannot - it is
    sampled once during warmup and is stale the moment it prints.

DEBUG VIEW
    --debug opens one window showing the frame EXACTLY as handed to MediaPipe -
    after rotation, colour conversion and any resize - never the raw grab. It
    answers a question the CSV cannot: the Windows Camera app shows a usable,
    grainy hand in the same dark room where this logs mean grey ~20 and detects
    nothing, so the image the model actually receives has to be looked at.

    THE WINDOW IS EVIDENCE, SO NOTHING MAY IMPROVE THE PIXELS. No brightening,
    no histogram equalisation, no denoise, no gamma. Only landmarks, a box and
    text are drawn on top. A window that shows a nicer frame than the model got
    is worse than no window, because it disproves the wrong thing.

    Everything else is unchanged by --debug: the CSV is still written and OSC is
    still sent.
"""

import argparse
import csv
import json
import sys
import time
import urllib.request
from collections import deque
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

# CAP_PROP_AUTO_EXPOSURE is a two-state enum wearing a float's clothing: 0.25
# means manual, 0.75 means auto. Nothing in between does anything.
AUTO_EXPOSURE_MANUAL = 0.25
AUTO_EXPOSURE_AUTO = 0.75

# The 21-landmark skeleton, copied from MediaPipe's own HAND_CONNECTIONS.
#
# Hardcoded rather than imported: it used to live in
# mediapipe.python.solutions.hands, and that whole legacy module is GONE as of
# mediapipe 1.0 (same removal as the docstring's HandLandmarker note). The
# landmark indices themselves are unchanged, so this list is still current.
HAND_CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 4),            # thumb
    (0, 5), (5, 6), (6, 7), (7, 8),            # index
    (5, 9), (9, 10), (10, 11), (11, 12),       # middle
    (9, 13), (13, 14), (14, 15), (15, 16),     # ring
    (13, 17), (17, 18), (18, 19), (19, 20),    # pinky
    (0, 17),                                   # palm base
)


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
    #
    # --auto-exposure hands that decision back to the camera for one run. It is
    # a DIAGNOSTIC, not the fix for a dark room - CLAUDE.md section 21 is
    # explicit that auto is not the lever, because it hunts under projector
    # light. It is here to answer one question: does this sensor produce a
    # usable frame at all when it is allowed to choose its own settings, the way
    # the Windows Camera app lets it? If auto is bright and manual is not, the
    # problem is the exposure value, not the room.
    if args.auto_exposure:
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, AUTO_EXPOSURE_AUTO)
    else:
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, args.auto_exposure_value)
        if args.exposure is not None:
            cap.set(cv2.CAP_PROP_EXPOSURE, args.exposure)

    # Gain is the other half of the brightness story and this script has never
    # touched it, so it has been sitting at whatever the driver felt like.
    # Exposure buys light by integrating longer, which costs motion blur; gain
    # buys it by amplifying, which costs noise. A hand blurred across 30 fps and
    # a hand buried in noise fail detection differently, so they are separate
    # knobs rather than one "brightness".
    if args.gain is not None:
        cap.set(cv2.CAP_PROP_GAIN, args.gain)

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

    # MSMF is free to clamp a requested exposure to something the sensor
    # actually supports, or to ignore it, and the capture keeps running either
    # way. A sweep point that never changed would otherwise look like a real
    # measurement of a flat detection rate.
    #
    # READ THIS BEFORE TRUSTING THE NUMBER. CLAUDE.md section 16 records that
    # this driver keeps REPORTING -4 whatever it was asked for. So agreement
    # between the two figures below is not evidence the setting took, and
    # disagreement is not evidence it failed - the property is simply not a
    # reliable channel here.
    #
    # The mean grey line underneath is the evidence. Across two runs at
    # different --exposure values, grey MUST move. If it does not, the setting
    # did not take and both runs are the same measurement.
    reported = cap.get(cv2.CAP_PROP_EXPOSURE)
    if args.auto_exposure:
        print(f"exposure set     : AUTO (--auto-exposure), device reports "
              f"{reported:g} - the camera is free to change it while running")
    else:
        print(f"exposure set     : requested {args.exposure}, device reports "
              f"{reported:g}"
              + ("" if reported == args.exposure else "   <- differ, see below"))

    # Same caveat as exposure, and worth restating because gain is new here:
    # section 16 of CLAUDE.md records that this MSMF driver reports back a
    # stored number rather than what the sensor is doing, so these two figures
    # agreeing is NOT evidence the gain took, and disagreeing is not evidence it
    # failed. The mean grey below - and the on-screen grey under --debug - is
    # the evidence. Change --gain, watch grey move, or it did not take.
    if args.gain is not None:
        reported_gain = cap.get(cv2.CAP_PROP_GAIN)
        print(f"gain set         : requested {args.gain}, device reports "
              f"{reported_gain:g}"
              + ("" if reported_gain == args.gain else
                 "   <- differ, read-back is unreliable on MSMF, see below"))

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

def exposure_tag(args):
    """What this run's exposure is called in the filename and in every row.

    %g so -4.0 becomes "-4" and -3.5 stays "-3.5" - the filename carries the
    setting, so two sweep points can never be told apart only by their
    timestamps, and a stray copied file still says what it is.

    An --auto-exposure run is tagged "auto" rather than with the number that was
    not applied. Filing it under exp-4 would put a run the camera chose the
    exposure for in the same bucket as the runs that pinned it, and the whole
    point of the sweep is that one file means one setting.
    """
    if args.auto_exposure:
        return "auto"
    if args.exposure is None:
        return "none"
    return f"{args.exposure:g}"


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

    def __init__(self, directory, label, window_s, exposure_tag):
        self.label = label
        self.window_s = window_s
        self.exposure = exposure_tag

        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = directory / f"detect_{label}_exp{self.exposure}_{stamp}.csv"

        self._fh = self.path.open("w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._fh)
        self._writer.writerow(
            ["iso_time", "frame_idx", "mean_grey", "hands", "exposure"]
        )
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
            self.exposure,
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
            f"[{self.label} exp{self.exposure}] {self.window_s:.0f}s  "
            f"mean grey {grey:6.1f}/255  detected {rate * 100:5.1f}%  "
            f"({self._window_detected}/{frames} frames)",
            file=sys.stderr,
        )
        self._reset_window(now)

    def close(self):
        self._fh.close()


# --- the frame MediaPipe sees ----------------------------------------------

def prepare_for_mediapipe(frame):
    """Raw BGR camera frame -> the exact array the model is handed.

    THE ONLY PLACE THAT TRANSFORMATION HAPPENS. Rotation, colour conversion and
    any resize added later all belong in here, because --debug renders this
    return value and nothing else. Do the work anywhere else and the debug
    window silently starts lying about the input.

    Today that is: rotate 180, BGR -> RGB, no resize. The rotation is a COPY -
    cv2.rotate returns a new array - so the caller's raw frame stays raw and
    nothing downstream can pick up the rotated one by accident.
    """
    upright = cv2.rotate(frame, cv2.ROTATE_180)
    return cv2.cvtColor(upright, cv2.COLOR_BGR2RGB)


# --- debug view -------------------------------------------------------------

def list_displays():
    """Every monitor as (x, y, w, h, is_primary), in desktop coordinates.

    cv2.moveWindow takes virtual-desktop coordinates, so an origin is all that
    is needed to put the debug window on a chosen screen. Left unplaced, HighGUI
    lets Windows decide, and Windows is entitled to decide "the projector" -
    which on this rig means the debug view lands on the table, on top of the UI
    it is meant to be debugging.

    THIS INDEX IS NOT THE ONE IN bin/data/display.txt. That file holds a GLFW
    monitor index for the oF app, and GLFW always enumerates the primary monitor
    first while Win32 does not, so the two orders disagree. Copying a number
    from one into the other puts the window on the wrong screen. Pick from the
    list this script prints.

    Deliberately does not touch the process's DPI awareness. Whatever HighGUI's
    coordinates mean, these come from the same process and mean the same thing,
    and that self-consistency is the only property needed here.
    """
    if sys.platform != "win32":
        return []

    import ctypes
    from ctypes import wintypes

    class RECT(ctypes.Structure):
        _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                    ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                    ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]

    MONITORINFOF_PRIMARY = 1
    user32 = ctypes.windll.user32
    found = []

    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                               ctypes.POINTER(RECT), wintypes.LPARAM)

    def visit(handle, _hdc, _rect, _param):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            r = info.rcMonitor
            found.append((r.left, r.top, r.right - r.left, r.bottom - r.top,
                          bool(info.dwFlags & MONITORINFOF_PRIMARY)))
        return True

    try:
        user32.EnumDisplayMonitors(None, None, proto(visit), 0)
    except OSError:
        return []
    return found


def choose_display(displays, wanted):
    """Which monitor the debug window goes on, and why.

    Default is the primary. The projector is the extended display on this rig -
    the oF app is pointed at a non-zero GLFW index - so primary is the operator's
    monitor, which is where a debug window belongs. It is a default, not an
    assumption: --debug-display overrides it, and the list is printed either way.
    """
    if not displays:
        return None

    if wanted is not None:
        if not 0 <= wanted < len(displays):
            raise TrackerError(
                f"--debug-display {wanted} but only {len(displays)} "
                f"monitor(s) present, numbered 0 to {len(displays) - 1}"
            )
        return wanted

    for i, d in enumerate(displays):
        if d[4]:
            return i
    return 0


class DebugView:
    """One window showing the model's actual input, with its actual output.

    The frame drawn here is prepare_for_mediapipe()'s return value round-tripped
    back to BGR for imshow - the same bytes the model got, reinterpreted for the
    display, not re-derived from the raw grab.

    NOTHING IN HERE MAY IMPROVE THE IMAGE. No brightening, equalisation,
    denoise or gamma. The window exists to show that a mean-grey-20 frame really
    is what MediaPipe is being asked to find a hand in; a prettied-up frame
    would answer a question nobody asked.

    Text is outlined rather than sitting on a filled panel, so the only pixels
    it hides are the glyphs themselves.
    """

    WINDOW = "track_hands --debug"

    FONT = cv2.FONT_HERSHEY_SIMPLEX
    FONT_SCALE = 0.6
    FONT_THICKNESS = 2            # see _text - both passes must share it
    LINE_H = 30

    WIDTH = 1280                  # window, not image - see _place
    HEIGHT = 720
    INSET = 60

    SKELETON = (0, 255, 0)        # BGR
    LANDMARK = (0, 165, 255)
    PALM = (255, 0, 255)          # landmark 9 - the one that becomes OSC
    BOX = (255, 200, 0)
    OK = (150, 255, 150)
    WARN = (0, 80, 255)

    def __init__(self, cap, args):
        self.cap = cap
        self.args = args
        self.window_s = args.report_interval

        # Sliding windows, not the tumbling one DetectionLog reports on. A
        # tumbling window reads zero for four seconds after every reset, which
        # on a live display looks like detection dropping out.
        self._detections = deque()   # (t, hands > 0)
        self._frame_times = deque()  # t

        # cap.get() goes to the driver, and on MSMF that is not free. At 30 fps
        # it would be 60 property reads a second to keep two numbers on screen
        # that move slowly if at all, so they are refreshed twice a second.
        self._props_at = 0.0
        self._exposure = float("nan")
        self._gain = float("nan")

        # WINDOW_NORMAL so a 1920x1080 frame can be dragged smaller than the
        # screen. That scales the WINDOW; the image handed to imshow is still
        # full resolution and unresampled by this script.
        cv2.namedWindow(self.WINDOW, cv2.WINDOW_NORMAL | cv2.WINDOW_KEEPRATIO)
        cv2.resizeWindow(self.WINDOW, self.WIDTH, self.HEIGHT)
        self._place()

    def _place(self):
        """Put the window on the operator's monitor, not on the table.

        Sized to fit the target monitor rather than to the frame: the frame is
        1920x1080 and the window is a scaled view of it, so there is nothing to
        gain from matching it and a real cost to overflowing the screen.
        """
        displays = list_displays()
        index = choose_display(displays, self.args.debug_display)
        if index is None:
            print("debug window     : could not enumerate monitors, leaving "
                  "placement to the window manager - use --debug-display if "
                  "it lands on the projector")
            return

        for i, (x, y, w, h, primary) in enumerate(displays):
            mark = " <- debug window" if i == index else ""
            print(f"  display [{i}]   : {w}x{h} at ({x},{y})"
                  f"{'  PRIMARY' if primary else ''}{mark}")

        x, y, w, h, _ = displays[index]
        width = min(self.WIDTH, w - 2 * self.INSET)
        height = min(self.HEIGHT, h - 2 * self.INSET)
        cv2.resizeWindow(self.WINDOW, width, height)

        # Inset rather than flush to the origin, for the reason main.cpp records
        # about the oF window: Windows shifts a decorated window a few pixels
        # off the requested spot, and a title bar at y=0 can end up under the
        # screen edge with no way to drag it.
        cv2.moveWindow(self.WINDOW, x + self.INSET, y + self.INSET)

    # -- overlays ----------------------------------------------------------

    def _text(self, img, text, org, colour):
        """Outlined text, both layers at the SAME thickness.

        That is not a style choice. cv2.putText's glyph ADVANCE depends on
        thickness - measured on OpenCV 5.0, one 36-character string is 286 px
        wide at thickness 1 and 305 px at thickness 2 or more. So the obvious
        way to outline text, a fat black pass under a thin coloured pass, is
        wrong: the two start flush at the left and drift apart to the right,
        and every line ends in a black ghost of its own tail.

        Same thickness in both passes, offset by position instead. The four
        diagonal offsets are what makes it an outline rather than a shadow,
        which matters here because the text sits on a grainy frame with no
        panel behind it and has to stay legible over both black and white.
        """
        x, y = org
        for dx, dy in ((-2, -2), (2, -2), (-2, 2), (2, 2)):
            cv2.putText(img, text, (x + dx, y + dy), self.FONT,
                        self.FONT_SCALE, (0, 0, 0), self.FONT_THICKNESS,
                        cv2.LINE_AA)
        cv2.putText(img, text, (x, y), self.FONT, self.FONT_SCALE, colour,
                    self.FONT_THICKNESS, cv2.LINE_AA)

    def _draw_hand(self, img, landmarks, handedness, index):
        h, w = img.shape[:2]
        pts = [(int(lm.x * w), int(lm.y * h)) for lm in landmarks]

        # The box a classifier crop would be taken from, drawn from the landmark
        # extents. Translucent fill so it never hides the pixels underneath -
        # the frame is the evidence, the box is annotation.
        #
        # Extents exactly, with no padding: this is what the landmarks claim,
        # not a crop recommendation. A real classifier crop will want margin
        # around it, and that margin is that consumer's decision to make.
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0, y0 = max(0, min(xs)), max(0, min(ys))
        x1, y1 = min(w - 1, max(xs)), min(h - 1, max(ys))

        fill = img.copy()
        cv2.rectangle(fill, (x0, y0), (x1, y1), self.BOX, cv2.FILLED)
        cv2.addWeighted(fill, 0.20, img, 0.80, 0, dst=img)
        cv2.rectangle(img, (x0, y0), (x1, y1), self.BOX, 2)

        for a, b in HAND_CONNECTIONS:
            cv2.line(img, pts[a], pts[b], self.SKELETON, 2, cv2.LINE_AA)
        for i, p in enumerate(pts):
            if i == PALM_LANDMARK:
                continue
            cv2.circle(img, p, 4, self.LANDMARK, cv2.FILLED, cv2.LINE_AA)

        # Landmark 9 last and larger: it is the only one that leaves this
        # process, so on screen it should be the one that stands out.
        cv2.circle(img, pts[PALM_LANDMARK], 7, self.PALM, cv2.FILLED,
                   cv2.LINE_AA)

        # Handedness survives the 180 rotation untouched - a rotation is not a
        # mirror, so chirality is preserved and the label means the same thing
        # it would on the unrotated frame.
        if handedness:
            top = handedness[0]
            caption = f"#{index} {top.category_name} {top.score:.2f}"
        else:
            caption = f"#{index} handedness: none"
        self._text(img, caption, (x0, max(18, y0 - 10)), self.BOX)

        self._text(img, f"box {x1 - x0}x{y1 - y0} px", (x0, min(h - 8, y1 + 20)),
                   self.BOX)

    def _draw_status(self, img, mean_grey):
        asked = ("driver default" if self.args.exposure is None
                 else f"{self.args.exposure:g}")
        exposure = "AUTO" if self.args.auto_exposure else f"{asked} fixed"
        gain_req = "not set" if self.args.gain is None else f"{self.args.gain}"

        grey_ok = mean_grey >= self.args.min_mean_grey
        lines = [
            (f"mean grey  {mean_grey:6.1f}/255"
             + ("" if grey_ok else
                f"   TOO DARK (floor {self.args.min_mean_grey:g})"),
             self.OK if grey_ok else self.WARN),
            (f"exposure   {exposure}   device reports {self._exposure:g}",
             self.OK),
            (f"gain       requested {gain_req}   device reports "
             f"{self._gain:g}", self.OK),
            (f"detected   {self._detection_rate() * 100:5.1f}%  over the last "
             f"{self.window_s:.0f}s", self.OK),
            (f"fps        {self._fps():5.1f}", self.OK),
            (f"input      {img.shape[1]}x{img.shape[0]}  rot180  RGB  "
             f"(as handed to MediaPipe)", self.OK),
        ]
        for i, (text, colour) in enumerate(lines):
            self._text(img, text, (14, self.LINE_H * (i + 1)), colour)

        # An auto-exposure frame must never be mistaken for a fixed one - the
        # two are not comparable measurements, and a screenshot outlives the
        # command line that produced it. So the mode gets its own banner rather
        # than a word in a list.
        banner, colour = (
            ("AUTO EXPOSURE - camera is choosing, values will drift",
             self.WARN)
            if self.args.auto_exposure else
            (f"EXPOSURE FIXED at {asked}", self.OK)
        )
        self._text(img, banner, (14, self.LINE_H * (len(lines) + 1) + 10),
                   colour)

    # -- rolling numbers ---------------------------------------------------

    def _trim(self, q, now, window):
        while q and now - q[0][0] > window:
            q.popleft()

    def _detection_rate(self):
        if not self._detections:
            return 0.0
        hit = sum(1 for _, d in self._detections if d)
        return hit / len(self._detections)

    def _fps(self):
        if len(self._frame_times) < 2:
            return 0.0
        span = self._frame_times[-1][0] - self._frame_times[0][0]
        return (len(self._frame_times) - 1) / span if span > 0 else 0.0

    # -- per frame ---------------------------------------------------------

    def show(self, rgb, result, mean_grey):
        """Draw one frame. False means the operator asked to stop."""
        now = time.perf_counter()

        self._detections.append((now, bool(result.hand_landmarks)))
        self._trim(self._detections, now, self.window_s)
        self._frame_times.append((now, None))
        self._trim(self._frame_times, now, 1.0)

        if now - self._props_at >= 0.5:
            self._exposure = self.cap.get(cv2.CAP_PROP_EXPOSURE)
            self._gain = self.cap.get(cv2.CAP_PROP_GAIN)
            self._props_at = now

        # The model's input, byte for byte, put back in imshow's channel order.
        canvas = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

        for i, landmarks in enumerate(result.hand_landmarks):
            handedness = (result.handedness[i]
                          if i < len(result.handedness) else None)
            self._draw_hand(canvas, landmarks, handedness, i)

        self._draw_status(canvas, mean_grey)
        cv2.imshow(self.WINDOW, canvas)

        # waitKey is what pumps the window's event queue, so it has to be called
        # every frame whether or not a key is wanted.
        key = cv2.waitKey(1) & 0xFF
        return key not in (ord("q"), 27)

    def close(self):
        cv2.destroyWindow(self.WINDOW)


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

    log = DetectionLog(args.log_dir, args.label, args.report_interval,
                       exposure_tag(args))
    print(f"detection log    : {log.path}")

    view = DebugView(cap, args) if args.debug else None
    if view is not None:
        print("debug window     : showing the frame as handed to MediaPipe, "
              "unmodified - q or esc to stop")

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

            rgb = prepare_for_mediapipe(frame)

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

            # After the CSV write and after the OSC send, so a debug run
            # measures and reports exactly what a normal run does.
            if view is not None and not view.show(rgb, result, mean_grey):
                print("\nstopped from the debug window")
                break

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
        if view is not None:
            view.close()
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
    p.add_argument("--gain", type=int, default=None,
                   help="CAP_PROP_GAIN. Left alone if not given. Read-back is "
                        "unreliable on MSMF (CLAUDE.md section 16), so judge "
                        "it by mean grey, not by the value printed at startup")
    p.add_argument("--auto-exposure", action="store_true",
                   help="let the camera run auto-exposure instead of pinning "
                        "it. DIAGNOSTIC ONLY - section 21 says auto is not the "
                        "fix for a dark room, it hunts under projector light. "
                        "Logs are tagged expauto, and the --debug window says "
                        "so in red")
    p.add_argument("--auto-exposure-value", type=float,
                   default=AUTO_EXPOSURE_MANUAL,
                   help="raw CAP_PROP_AUTO_EXPOSURE value used for MANUAL "
                        "mode; 0.25 on this driver. Only for a camera that "
                        "spells manual differently")
    p.add_argument("--debug", action="store_true",
                   help="open a window showing the frame exactly as handed to "
                        "MediaPipe, with all 21 landmarks, the skeleton, "
                        "handedness, the detection box and live "
                        "grey/exposure/gain/rate/fps. Changes nothing else: "
                        "the CSV is still written and OSC is still sent")
    p.add_argument("--debug-display", type=int, default=None,
                   help="which monitor the --debug window opens on, indexed "
                        "into the list printed at startup. Defaults to the "
                        "primary, so it does not land on the projector. NOT "
                        "the same numbering as bin/data/display.txt, which is "
                        "a GLFW index for the oF app")
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
