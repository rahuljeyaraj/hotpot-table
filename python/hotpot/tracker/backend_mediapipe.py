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
present and all three defaulting to 0.5.

**3. The model file is not in git** (`.gitignore`'s `models/**/*.task`,
same rule as every other weight file). A missing file is not a crash here:
`load()` returns None and `tracker/main.py` falls back to the stub, because
doc section 3.3 requires the process to come up and hold its link open
regardless. See `models/README.md` for the download.

Landmark 9, and why the index is a named constant
--------------------------------------------------
Doc section 11.2 picks the **middle-finger MCP joint** — the palm centre —
over the wrist (too far from where a person feels their hand is) and the
index tip (moves wildly while gripping tongs). The Tasks API returns
landmarks in the same canonical 21-point hand order the Solutions API used,
so index 9 is still that joint; `CURSOR_LANDMARK` names it so a future
reader does not have to trust a bare `[9]`.

Coordinates come back **normalised to 0..1** of the image that was passed
in. This class multiplies back up to that image's own pixel size and
nothing more — doc section 6.5 has the caller doing the downsample, so the
caller is the only party that knows the scale back to capture resolution.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from hotpot.tracker.backend import Backend, Detection

_log = logging.getLogger("hotpot.tracker")

# Doc section 11.2's cursor point. Canonical MediaPipe hand landmark
# ordering, index 9 = MIDDLE_FINGER_MCP.
CURSOR_LANDMARK = 9

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
                 mirror_handedness: bool = False) -> None:
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

    @classmethod
    def load(cls, model_path: str, *, num_hands: int = 2,
             mirror_handedness: bool = False,
             min_detection_confidence: float = 0.5,
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
                min_tracking_confidence=min_tracking_confidence,
            )
            landmarker = vision.HandLandmarker.create_from_options(options)
        except Exception as e:      # noqa: BLE001 - see the docstring
            _log.exception("tracker: could not open %s: %s", model_path, e)
            return None

        _log.info("tracker: MediaPipe HandLandmarker up on %s (%d hands max)",
                  model_path, num_hands)
        return cls(landmarker, model_path, mirror_handedness=mirror_handedness)

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
        image = mp.Image(image_format=mp.ImageFormat.SRGB,
                         data=np.ascontiguousarray(rgb))

        result = self._landmarker.detect_for_video(image, int(timestamp_ms))

        out: List[Detection] = []
        landmark_sets = getattr(result, "hand_landmarks", None) or []
        handedness_sets = getattr(result, "handedness", None) or []
        for idx, landmarks in enumerate(landmark_sets):
            if len(landmarks) <= CURSOR_LANDMARK:
                continue
            point = landmarks[CURSOR_LANDMARK]
            label, score = self._handedness(handedness_sets, idx)
            out.append(Detection(
                # Normalised 0..1 of the image passed in, multiplied back
                # up to THAT image's pixels — never the capture frame's.
                x=float(point.x) * width,
                y=float(point.y) * height,
                conf=score,
                handedness=label,
            ))
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
