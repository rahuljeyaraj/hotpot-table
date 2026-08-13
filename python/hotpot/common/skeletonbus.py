"""common/skeletonbus.py — RIG_FEEDBACK item 11 diagnostic: the raw,
unsmoothed MediaPipe hand skeleton (up to 21 points per hand), in STAGE
space, sent from the tracker straight to oF. Nothing else reads it.

Why this exists
----------------
The developer confirmed the raw MediaPipe skeleton renders smoothly on the
staff view's Developer tab (RIG_FEEDBACK item 9/10) while the actual
cursor on the *projected table* still lags/sticks on a fast hand move
(item 11). The Developer tab's `landmarks` message (tracker/main.py's
`_maybe_send_landmarks`) is CAMERA-pixel space, independent of calibration
by design — useless for drawing on the real table, which is stage space.
This module carries the same per-tick raw detections `_to_stage` already
produces for the cursor pipeline, mapped through the homography, but
BEFORE `tracking.HandTracker.update()` touches them — no matching, no EMA
smoothing (item 8), no role assignment, no hysteresis. What lands on the
table is exactly what MediaPipe said this tick and nothing else, so a
person standing at the rig can watch the raw signal and the processed
cursor side by side on the one surface that actually matters for item 11.

Not an extra field on `cursorbus.CursorFrame`
-----------------------------------------------
Doc §4.6 fixes the cursor datagram's shape byte for byte, and this isn't a
cursor: it's an unbounded number of hands (no role has been assigned yet)
each carrying up to 21 points, not one point per pointer/ambient hand. A
separate, smaller wire shape avoids bending a doc-fixed contract for a
debug view — the same reasoning that keeps `landmarks` (item 10) a
distinct message from `hands` in the web protocol instead of a variant of
it.

Same drain-to-latest discipline as `cursorbus` (see that module's own
docstring for why: TCP would queue and replay stale frames, UDP with a
"keep the newest, across drains too" receiver does not) — this is the
identical failure mode on an identical local, fire-and-forget, tracker-
restarts-constantly UDP socket, so `_disable_windows_connreset` is reused
from `cursorbus` rather than re-derived.

Diagnostic only. `core` never binds this port and never reads this
message; deleting this module deletes nothing anything else depends on.
"""

from __future__ import annotations

import json
import logging
import socket
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from hotpot.common.cursorbus import _disable_windows_connreset

_log = logging.getLogger("hotpot.skeletonbus")

# Not in doc §4.1 — this transport doesn't exist there. Picked to sit right
# after cursorbus's own 8770/8771 pair.
OF_PORT = 8772
LOCALHOST = "127.0.0.1"

RECV_BUFFER = 8192   # matches cursorbus.RECV_BUFFER's own reasoning
MAX_DRAIN = 512      # matches cursorbus.MAX_DRAIN's own reasoning


@dataclass
class SkeletonHand:
    """One hand's raw points, stage space, this tick. No `id` — there is
    no track to number yet; this is upstream of `tracking.py` entirely.
    """

    handedness: Optional[str]
    conf: float
    points: List[Tuple[float, float]]

    def to_json(self) -> Dict[str, Any]:
        return {
            "handedness": self.handedness,
            "conf": round(self.conf, 2),
            "points": [[round(x, 1), round(y, 1)] for x, y in self.points],
        }

    @classmethod
    def from_json(cls, raw: Any) -> Optional["SkeletonHand"]:
        if not isinstance(raw, dict):
            return None
        pts_raw = raw.get("points")
        if not isinstance(pts_raw, list):
            return None
        points: List[Tuple[float, float]] = []
        for p in pts_raw:
            if (not isinstance(p, list) or len(p) != 2
                    or not all(isinstance(v, (int, float)) for v in p)):
                continue
            x, y = float(p[0]), float(p[1])
            if x != x or y != y:      # NaN survives every comparison
                continue
            points.append((x, y))
        if not points:
            return None
        handedness = raw.get("handedness")
        if handedness not in ("Left", "Right", None):
            handedness = None
        conf = raw.get("conf", 0.0)
        if not isinstance(conf, (int, float)) or isinstance(conf, bool):
            conf = 0.0
        return cls(handedness=handedness, conf=float(conf), points=points)


@dataclass
class SkeletonFrame:
    seq: int
    ts: float
    hands: List[SkeletonHand] = field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        return {"seq": self.seq, "ts": round(self.ts, 3),
                "hands": [h.to_json() for h in self.hands]}


def encode(frame: SkeletonFrame) -> bytes:
    return json.dumps(frame.to_json(), separators=(",", ":")).encode("utf-8")


def decode(data: bytes) -> Optional[SkeletonFrame]:
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    seq = obj.get("seq")
    if not isinstance(seq, int) or isinstance(seq, bool):
        return None
    ts = obj.get("ts", 0.0)
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        ts = 0.0
    raw_hands = obj.get("hands")
    hands: List[SkeletonHand] = []
    if isinstance(raw_hands, list):
        for raw in raw_hands:
            hand = SkeletonHand.from_json(raw)
            if hand is not None:
                hands.append(hand)
    return SkeletonFrame(seq=seq, ts=float(ts), hands=hands)


class Sender:
    """One UDP socket, one destination (oF only — core has no use for this
    and is deliberately never a target), one sequence counter. Mirrors
    `cursorbus.Sender`'s never-raises-on-send rule: oF may not be running
    yet, or may have just died, and that is the ordinary state of the
    world here (doc §3.3), not an error worth reporting per-datagram.
    """

    def __init__(self, target: Tuple[str, int] = (LOCALHOST, OF_PORT),
                 sock: Optional[Any] = None) -> None:
        self.target = (str(target[0]), int(target[1]))
        self._sock = sock if sock is not None else socket.socket(
            socket.AF_INET, socket.SOCK_DGRAM)
        self._seq = 0
        self.sent = 0
        self.failed = 0

    def send(self, hands: Sequence[SkeletonHand], ts: float) -> SkeletonFrame:
        frame = SkeletonFrame(seq=self._seq, ts=ts, hands=list(hands))
        self._seq += 1
        payload = encode(frame)
        try:
            self._sock.sendto(payload, self.target)
            self.sent += 1
        except OSError as e:
            # See cursorbus.Sender.send's own comment: Windows can report
            # a PRIOR send's ICMP port-unreachable here, one call late.
            self.failed += 1
            _log.debug("skeletonbus: send to %s:%d failed: %s",
                       self.target[0], self.target[1], e)
        return frame

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


class Receiver:
    """Drain-to-latest, same two rules as `cursorbus.Receiver` — see that
    class's own docstring. Python-side receiver exists for tests and for
    any future Python consumer; oF's real consumer is `SkeletonLink.cpp`,
    a C++ mirror of this class the same way `CursorLink.cpp` mirrors
    `cursorbus.Receiver`.
    """

    def __init__(self, host: str = LOCALHOST, port: int = OF_PORT,
                 sock: Optional[Any] = None) -> None:
        self.host = host
        self.port = port
        if sock is not None:
            self._sock = sock
        else:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind((host, port))
            self.port = self._sock.getsockname()[1]
            _disable_windows_connreset(self._sock)
        self._sock.setblocking(False)

        self.last_seq = -1
        self.received = 0
        self.dropped_stale = 0
        self.malformed = 0

    def recv_latest(self) -> Optional[SkeletonFrame]:
        best: Optional[SkeletonFrame] = None
        for _ in range(MAX_DRAIN):
            try:
                data, _addr = self._sock.recvfrom(RECV_BUFFER)
            except BlockingIOError:
                break
            except OSError as e:
                # Same stale-ICMP-reset case cursorbus.Receiver documents.
                self.malformed += 1
                _log.debug("skeletonbus: recv on %d failed: %s",
                           self.port, e)
                continue
            frame = decode(data)
            if frame is None:
                self.malformed += 1
                continue
            self.received += 1
            if frame.seq <= self.last_seq:
                self.dropped_stale += 1
                continue
            if best is None or frame.seq > best.seq:
                if best is not None:
                    self.dropped_stale += 1
                best = frame
            else:
                self.dropped_stale += 1
        if best is not None:
            self.last_seq = best.seq
        return best

    def reset_sequence(self) -> None:
        self.last_seq = -1

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass
