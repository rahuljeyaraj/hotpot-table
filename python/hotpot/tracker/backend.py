"""tracker/backend.py — what a hand detector has to provide, and nothing
more (doc section 19.4's "backend abstraction — mandatory", applied to the
tracker for the same reason it is applied to the classifier and voice).

Doc section 19.4 argues the split as "what makes M1-M6 possible before any
model exists". Here it buys something narrower and more immediate: **the
role assignment in doc section 11.3 is the part of this milestone that can
be wrong in a way nobody notices, and it must be testable without a camera,
without a model file, and without MediaPipe installed at all.** A left hand
that selects is a bug a diner finds, not a test — unless the tracking is
drivable from a list of coordinates, which is what `backend_stub` is for.

The interface is deliberately thin. A backend answers one question:

    detect(frame_bgr) -> list[Detection]

and knows nothing about stage space, homographies, roles, ids, hysteresis
or UDP. All of that is `tracker/tracking.py`'s and `tracker/main.py`'s job,
so all of it is testable against the stub.

`Detection.x`/`.y` are in the pixel coordinates **of the frame that was
passed in** — not the camera's full capture resolution and not stage space.
The tracker downsamples before inference (doc section 6.5: "tracker
downsamples with cv2.resize before MediaPipe — cheap, and MediaPipe wants
small"), so the caller is the only party that knows the scale factor back
to capture resolution, and it applies it. A backend that tried to return
capture-space coordinates would have to be told the scale, which is exactly
the sort of shared secret that goes stale.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

# MediaPipe's own handedness labels. Kept as the strings the library
# actually emits rather than an enum, because doc section 11.3's rule is
# written against them ("else if MediaPipe says 'Right'") and because
# `tracker.mirror_handedness` (doc section 8.6) has to be able to swap
# them without either side having a private encoding.
HAND_LEFT = "Left"
HAND_RIGHT = "Right"


@dataclass
class Detection:
    """One hand found in one frame.

    `x`/`y` are the cursor point already chosen by the backend — doc
    section 11.2's **landmark 9, the middle-finger MCP joint**. The choice
    lives in the backend rather than in the caller because only the backend
    has landmarks at all; what the caller gets is a point, which is all doc
    section 4.6 ever puts on the wire.

    `handedness` is `"Left"`, `"Right"` or None. None is a real answer, not
    a failure: doc section 11.3's step 2 already has a branch for "no
    pointer exists yet" that does not consult handedness, and a backend
    that has no opinion must be able to say so rather than guess a side.

    `landmarks`, added 2026-08-12 for the staff view's Developer tab
    (RIG_FEEDBACK item 10 — "draw every point MediaPipe identifies"), is
    the full raw 21-point hand skeleton in the SAME pixel space as `x`/`y`
    (the frame passed to `detect()`), or None from a backend that has no
    landmarks to give (the stub). Debug-only: `tracking.py` never reads
    it — role assignment, hysteresis and the cursor pipeline are all
    still built on the single chosen point (`x`/`y`) alone, exactly as
    before this field existed.
    """

    x: float
    y: float
    conf: float
    handedness: Optional[str] = None
    landmarks: Optional[List[Tuple[float, float]]] = None


class Backend:
    """The interface. Not an ABC — this codebase has no abc use anywhere
    and a docstring plus two implementations is what every other backend
    split here (camera/capture.py's Capture) already does.
    """

    name = "backend"

    def detect(self, frame_bgr: Any, timestamp_ms: int) -> List[Detection]:
        """Hands in this frame. Never raises for "no hands" — that is an
        empty list, and it is the ordinary answer for most frames of a
        session.

        `timestamp_ms` must increase across calls. MediaPipe's VIDEO
        running mode uses it for its own frame-to-frame tracking and
        rejects a timestamp that goes backwards; the caller owns the clock
        so that a backend swap cannot change the timebase underneath it.
        """
        raise NotImplementedError

    def close(self) -> None:
        pass
