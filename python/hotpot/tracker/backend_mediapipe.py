"""tracker/backend_mediapipe.py — MediaPipe Hands, against the API that is
actually installed (doc section 21, M5 build item 1).

**VERIFY, and this one already bit: `mediapipe.solutions.hands` does not
exist in the installed MediaPipe.** Doc sections 11.1-11.3 are written
against the legacy Solutions API — `mp.solutions.hands.Hands(
model_complexity=...)`, `results.multi_hand_landmarks`,
`results.multi_handedness`. The installed version is **mediapipe 1.0.0**,
where `mp.solutions` is gone outright (`AttributeError: module 'mediapipe'
has no attribute 'solutions'`, checked before a line of this was written,
per doc section 0 rule 3). The replacement is the Tasks API:

    from mediapipe.tasks.python import vision
    vision.HandLandmarker.create_from_options(
        vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=...),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=2))
    landmarker.detect_for_video(mp.Image(...), timestamp_ms)
      -> HandLandmarkerResult(handedness, hand_landmarks, hand_world_landmarks)

Signatures verified by `inspect.signature` against the installed package,
not remembered. The consequences that reach the rest of the design:

**1. `model_complexity` no longer exists as a parameter.** It was a
Solutions-only knob selecting between two bundled `.tflite` landmark
models. The Tasks API takes a `.task` **model bundle file** instead, so the
"rung" doc section 11.2 wants to probe is now *which file you point at*,
not an integer. `MODEL_RUNGS` below is that ladder, and `tracker/main.py`
walks it. On a rig with only the one published bundle the ladder has one
rung and the probe has nothing to climb — which is logged plainly rather
than dressed up as a successful probe. Doc section 11.2 needs correcting
and has not been; see CLAUDE.md.

**2. There is no `static_image_mode` and no `min_detection_confidence`
pair.** The Tasks names are `min_hand_detection_confidence`,
`min_hand_presence_confidence` and `min_tracking_confidence`, all three
present and all three defaulting to 0.5. **Only two of the three were
ever actually wired into `HandLandmarkerOptions` until 2026-08-12** —
`min_hand_presence_confidence` was named in this paragraph but never
passed, so it silently ran at MediaPipe's own internal default the whole
time. Found while chasing RIG_FEEDBACK item 2 ("cursor doesn't appear
when the hand is near the table edges") — a hand entering from off-frame
is exactly the case presence confidence (not just detection confidence)
governs, since MediaPipe's own tracking keeps a hand "present" between
palm-detector runs. All three are now `tracker.*` config keys
(`config/system.default.json`) rather than code constants, so they can be
tuned from the rig with no rebuild while watching the Developer tab's raw
landmark view — this file cannot say by itself whether 0.5 is even the
right ballpark for a top-down camera with hands entering from frame
edges, only that all three knobs now reach the object that uses them.

**3. The model file is not in git** (`.gitignore`'s `models/**/*.task`,
same rule as every other weight file). A missing file is not a crash here:
`load()` returns None and `tracker/main.py` falls back to the stub, because
doc section 3.3 requires the process to come up and hold its link open
regardless. See `models/README.md` for the download.

Landmark 8, and why the index is a named constant
--------------------------------------------------
**Changed 2026-08-12, overriding doc section 11.2 — not yet applied back to
the doc.** Section 11.2 picked the middle-finger MCP joint (landmark 9, the
palm centre) over the wrist and the index tip, on the reasoning that the
index tip "moves wildly while gripping tongs." Developer's call: use the
**index fingertip (landmark 8, INDEX_FINGER_TIP)** instead — it is the
point a diner actually thinks of as "where I'm pointing," and unlike the
palm centre it is normally the most exposed, forward-most part of the hand
when reaching for something, which is also why `tracker/main.py`'s own
`CURSOR_SHADOW_CLEARANCE_MM` offset (added for landmark 9's own-hand-
shadow problem) shrank rather than being removed outright — the fingertip
needs far less clearance, not none. The Tasks API returns landmarks in the
same canonical 21-point hand order the Solutions API used, so index 8 is
that joint; `CURSOR_LANDMARK` names it so a future reader does not have to
trust a bare `[8]`.

Coordinates come back **normalised to 0..1** of the image that was passed
in. This class multiplies back up to that image's own pixel size and
nothing more — doc section 6.5 has the caller doing the downsample, so the
caller is the only party that knows the scale back to capture resolution.

180-degree mount compensation, 2026-08-12
------------------------------------------
**Real evidence, not a guess: a Developer-tab screenshot from this rig
showed a hand fully in frame, clearly visible to a person, that MediaPipe
reported zero hands for** — and the frame was visibly upside-down (every
label in the shot reads inverted), matching CLAUDE.md's own M4h finding
("this rig's camera is mounted at 180 degrees", measured 2026-08-08,
commit `b847c0f`). MediaPipe's palm detector is not rotation-invariant; a
hand presented upside-down, especially one already partly cut off by a
frame edge, looks like nothing in its training data. `mount_rotation_deg`
rotates the frame 180° before detection ONLY — every returned coordinate
(`x`/`y` AND every point in `landmarks`) is converted back to the
UNROTATED frame's own pixel space before this class returns anything, so
`tracker/main.py`'s `_to_stage` (which applies `H_cam_to_stage`, itself
solved against the unrotated feed — see `geometry_store.py`'s
`_manual_corners_stage`) and the Developer tab's raw-feed debug view both
keep working against the same coordinate space as before this existed.
Only 0 and 180 are implemented — this rig has never been measured at 90
or 270, and guessing that math in without a frame to test it against
would be exactly the kind of unverified spatial change CLAUDE.md's own
M4h/M4i/M4j history warns is easy to get wrong. An unsupported value logs
once and detects unrotated rather than raising, matching this module's
own "come up and hold the link open regardless" discipline.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from hotpot.tracker.backend import Backend, Detection

_log = logging.getLogger("hotpot.tracker")

# 2026-08-12, overriding doc section 11.2 (see the module docstring):
# canonical MediaPipe hand landmark ordering, index 8 = INDEX_FINGER_TIP.
CURSOR_LANDMARK = 8

# Rotation compensation this class actually implements — see the module
# docstring's "180-degree mount compensation" section. Anything else logs
# once and is treated as 0 (unrotated).
_SUPPORTED_ROTATIONS = (0, 180)

# Doc section 11.2's ladder, translated from "model_complexity 0 then 1"
# into the model bundles the Tasks API actually takes. Ordered cheapest
# first, which is the direction doc section 11.2 requires: "probing upward
# from a rung that works is safe; probing downward from one that does not
# means the first seconds of every run are janky."
#
# Google publishes one bundle today (`hand_landmarker.task`); the two
# named rungs above it are the shape a future full/heavy bundle would slot
# into, and a rung whose file is absent is skipped rather than being an
# error. `tracker/main.py` logs which rung it settled on either way.
MODEL_RUNGS = (
    "hand_landmarker.task",
    "hand_landmarker_full.task",
)


class MediaPipeBackend(Backend):
    """One `HandLandmarker` in VIDEO running mode.

    VIDEO rather than IMAGE: the landmarker then does its own frame-to-
    frame tracking and only re-runs the palm detector when it loses a hand,
    which is most of the reason it can hold a useful rate at all. VIDEO
    rather than LIVE_STREAM: LIVE_STREAM is callback-based and would put
    results on MediaPipe's thread, and this process already has exactly the
    loop that wants them synchronously.
    """

    def __init__(self, landmarker: Any, model_path: str,
                 mirror_handedness: bool = False,
                 mount_rotation_deg: int = 0) -> None:
        self._landmarker = landmarker
        self.model_path = model_path
        self.name = f"mediapipe:{model_path}"
        # Doc section 8.6's `tracker.mirror_handedness`, and doc section
        # 11.3: "the correct value is a property of the physical mounting
        # and is fastest to determine by trying it." Applied here, at the
        # one place the label is produced, so nothing downstream ever sees
        # two spellings of the same hand. Mutable at runtime — the staff
        # view's swap-hands button toggles it live (M5 build item 5).
        self.mirror_handedness = mirror_handedness
        # See the module docstring's "180-degree mount compensation".
        # Starts at 0 (unrotated) — like `_h` in `tracker/main.py`, this is
        # a fact core owns (`geometry.view_rotation_deg`,
        # `state/view_rotation.json`) and pushes over the wire; it is
        # never invented locally, only applied once core has said so.
        # Mutable at runtime for the same reason `mirror_handedness` is.
        self.mount_rotation_deg = mount_rotation_deg
        self._warned_bad_rotation = False

    @classmethod
    def load(cls, model_path: str, *, num_hands: int = 2,
             mirror_handedness: bool = False,
             mount_rotation_deg: int = 0,
             min_detection_confidence: float = 0.5,
             min_presence_confidence: float = 0.5,
             min_tracking_confidence: float = 0.5) -> Optional["MediaPipeBackend"]:
        """Build one, or return None with a logged reason.

        None rather than an exception for every foreseeable environmental
        cause — no mediapipe installed, no model file, an unreadable
        bundle — because the caller's only sane response to all three is
        the same (fall back to the stub and stay up), and turning them into
        exceptions would just move that decision into a `try` at the call
        site.

        The `SIGILL` case doc section 1.4b warns about (an AVX2-only wheel
        on the ODYSSEY's Goldmont Plus) cannot be caught here at all — it
        kills the process. That check is a rig task, not a code path.
        """
        try:
            import mediapipe as mp                      # noqa: WPS433
            from mediapipe.tasks import python as mpp   # noqa: WPS433
            from mediapipe.tasks.python import vision   # noqa: WPS433
        except ImportError as e:
            _log.warning("tracker: mediapipe is not installed (%s) — no hands "
                         "will be tracked", e)
            return None

        import os
        if not os.path.isfile(model_path):
            _log.warning("tracker: no model bundle at %s — see models/README.md "
                         "for the download. No hands will be tracked.",
                         model_path)
            return None

        try:
            options = vision.HandLandmarkerOptions(
                base_options=mpp.BaseOptions(model_asset_path=model_path),
                running_mode=vision.RunningMode.VIDEO,
                num_hands=num_hands,
                min_hand_detection_confidence=min_detection_confidence,
                min_hand_presence_confidence=min_presence_confidence,
                min_tracking_confidence=min_tracking_confidence,
            )
            landmarker = vision.HandLandmarker.create_from_options(options)
        except Exception as e:      # noqa: BLE001 - see the docstring
            _log.exception("tracker: could not open %s: %s", model_path, e)
            return None

        _log.info("tracker: MediaPipe HandLandmarker up on %s (%d hands max)",
                  model_path, num_hands)
        return cls(landmarker, model_path, mirror_handedness=mirror_handedness,
                  mount_rotation_deg=mount_rotation_deg)

    # -- detection ---------------------------------------------------------

    def detect(self, frame_bgr: Any, timestamp_ms: int) -> List[Detection]:
        import numpy as np          # noqa: WPS433
        import cv2                  # noqa: WPS433
        import mediapipe as mp      # noqa: WPS433

        # The ring holds BGR (doc section 6.1's `channels (3, BGR)`) and
        # `mp.Image` is told SRGB, so this conversion is not optional and
        # not cosmetic: skipping it swaps the red and blue channels, which
        # a palm detector trained on skin tones reads as a much weaker
        # signal. It fails as "detection is flaky", never as an error.
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        height, width = rgb.shape[:2]

        # See the module docstring's "180-degree mount compensation".
        # `detect_rgb` is what MediaPipe actually looks at; `rgb`/
        # width/height stay the CALLER's un-rotated frame throughout —
        # every coordinate below is converted back before it leaves this
        # method, so nothing outside this function ever has to know
        # rotation happened.
        rotate_180 = self.mount_rotation_deg == 180
        if self.mount_rotation_deg not in _SUPPORTED_ROTATIONS:
            if not self._warned_bad_rotation:
                _log.warning(
                    "tracker: mount_rotation_deg=%d has no MediaPipe "
                    "compensation implemented (only %s) — detecting "
                    "unrotated", self.mount_rotation_deg, _SUPPORTED_ROTATIONS)
                self._warned_bad_rotation = True
        detect_rgb = cv2.rotate(rgb, cv2.ROTATE_180) if rotate_180 else rgb
        image = mp.Image(image_format=mp.ImageFormat.SRGB,
                         data=np.ascontiguousarray(detect_rgb))

        result = self._landmarker.detect_for_video(image, int(timestamp_ms))

        out: List[Detection] = []
        landmark_sets = getattr(result, "hand_landmarks", None) or []
        handedness_sets = getattr(result, "handedness", None) or []
        for idx, landmarks in enumerate(landmark_sets):
            if len(landmarks) <= CURSOR_LANDMARK:
                continue
            label, score = self._handedness(handedness_sets, idx)
            # Normalised 0..1 of the (possibly rotated) image MediaPipe
            # actually saw, multiplied up to ITS pixels, then rotated back
            # to the caller's original frame — a 180-degree turn has no
            # width/height swap, so this is `(w - x, h - y)` and nothing
            # fancier. All 21 points, not just the cursor one: this is
            # what the Developer tab's raw-landmark debug view draws
            # (RIG_FEEDBACK item 10), and it must be in the SAME space as
            # the cursor point below or the two would visibly disagree.
            points = []
            for lm in landmarks:
                px = float(lm.x) * width
                py = float(lm.y) * height
                if rotate_180:
                    px, py = width - px, height - py
                points.append((px, py))
            cx, cy = points[CURSOR_LANDMARK]
            out.append(Detection(x=cx, y=cy, conf=score, handedness=label,
                                 landmarks=points))
        return out

    def _handedness(self, handedness_sets: Any, idx: int):
        """`("Right", 0.93)` or `(None, 0.0)`.

        Absent rather than guessed when the result has no category for this
        hand: doc section 11.3's step 2 has a branch that does not consult
        handedness at all, and a fabricated "Right" would take the pointer
        role away from a hand that legitimately holds it.
        """
        from hotpot.tracker.backend import HAND_LEFT, HAND_RIGHT

        if idx >= len(handedness_sets):
            return None, 0.0
        categories = handedness_sets[idx]
        if not categories:
            return None, 0.0
        top = categories[0]
        label = getattr(top, "category_name", None) or getattr(
            top, "display_name", None)
        score = float(getattr(top, "score", 0.0) or 0.0)
        if self.mirror_handedness:
            # Doc section 8.6's `tracker.mirror_handedness`. An overhead
            # camera may present a mirrored image, and MediaPipe's label is
            # about the image, not the room.
            if label == HAND_LEFT:
                label = HAND_RIGHT
            elif label == HAND_RIGHT:
                label = HAND_LEFT
        return label, score

    def close(self) -> None:
        try:
            self._landmarker.close()
        except Exception:      # noqa: BLE001 - closing a dead landmarker
            pass
