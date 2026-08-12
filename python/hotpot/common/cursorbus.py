"""common/cursorbus.py — the cursor link: UDP, one datagram per frame
(doc sections 4, 4.6; doc section 21, M5 build item 2).

This is the second transport in the system and it exists because the first
one is wrong for this traffic. Doc section 4's table says why in one line:

    A lost cursor packet is worthless 16ms later. TCP would *queue* stale
    ones — a 200ms hiccup then delivers a burst in order and the hand
    visibly replays through history.

So: UDP, localhost, fire-and-forget, and the receiver's job is not "read
the next packet" but **drain to latest**. Everything in this module exists
to make that one rule impossible to get wrong at a call site.

The two rules, and they are different
-------------------------------------
1. **Within one drain**, keep the highest `seq` and discard the rest. Doc
   section 4 states this outright. Reading one packet per tick would build
   exactly the backlog UDP was chosen to avoid, only in userspace.
2. **Across drains**, never deliver a `seq` at or below one already
   delivered. The doc does not say this and it is not the same rule: UDP
   may reorder, so a datagram that lost a race can arrive on the *next*
   tick, after its successor has already been drawn. Handing it over would
   move the cursor backwards for one frame — a visible twitch, and the
   miniature version of the replay-through-history failure rule 1 exists
   to prevent. `Receiver` therefore holds `last_seq` and gates on it.

Why not `t`-tagged JSONL like the control link
-----------------------------------------------
Doc section 4.6 fixes the payload shape and it has no `t` field. That is
correct rather than an oversight: a datagram is one whole message by
construction, so there is no framing to do and nothing to demultiplex —
this socket carries exactly one kind of message and always will. JSON at
all (rather than a struct) is doc section 4.6's own call: "at 60Hz and
~150 bytes this is free, and being human-readable during bring-up is
worth more than the bytes."

No threads
----------
Neither class starts one. The tracker sends from its own capture loop and
core drains from its 60Hz state loop, both of which already exist and both
of which must not be handed a callback that fires on someone else's
thread. `recv_latest()` is non-blocking and returns `None` when there is
nothing new, which is the shape a polling loop wants.
"""

from __future__ import annotations

import json
import logging
import socket
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_log = logging.getLogger("hotpot.cursorbus")

# Doc section 4.1's defaults.
OF_PORT = 8770        # cursor.of_port    — oF's listener
CORE_PORT = 8771      # cursor.core_port  — core's listener
LOCALHOST = "127.0.0.1"

# Doc section 4.6's two roles. `pointer` selects; `ambient` never does.
ROLE_POINTER = "pointer"
ROLE_AMBIENT = "ambient"
ROLES = (ROLE_POINTER, ROLE_AMBIENT)

# A cursor datagram is ~150 bytes (doc section 4.6). 8 KB is a hundredfold
# margin and still far below the ~64 KB a single UDP datagram could carry,
# so a buffer this size can never truncate a real packet — and a truncated
# datagram is silently discarded by the kernel rather than delivered short,
# which would be an unparseable line with no way to tell why.
RECV_BUFFER = 8192

# How many datagrams one drain will read before giving up and using the
# best it has. Only reachable if the sender is outrunning the receiver by
# a wide margin (a stalled core catching up on a second of tracker
# traffic). Bounded so a drain can never become an unbounded loop inside
# a 60Hz tick — the thing that would turn a small stall into a wedge.
MAX_DRAIN = 512


@dataclass
class Hand:
    """One tracked hand, in **stage space** (doc section 5.1's canonical
    space). The tracker converts out of camera space before sending, so
    core and oF both receive stage coordinates and cannot disagree about
    where a hand is (doc section 5.3).
    """

    id: int
    role: str
    x: float
    y: float
    conf: float

    @property
    def is_pointer(self) -> bool:
        return self.role == ROLE_POINTER

    def to_json(self) -> Dict[str, Any]:
        return {"id": self.id, "role": self.role,
                "x": round(self.x, 1), "y": round(self.y, 1),
                "conf": round(self.conf, 2)}

    @classmethod
    def from_json(cls, raw: Any) -> Optional["Hand"]:
        """`None` for anything that is not a usable hand, never a hand with
        a plausible-looking default in it. A cursor at (0, 0) with conf 0
        would hit-test against the top-left corner of the table as if a
        real hand were there.
        """
        if not isinstance(raw, dict):
            return None
        try:
            x = float(raw["x"])
            y = float(raw["y"])
        except (KeyError, TypeError, ValueError):
            return None
        if x != x or y != y:            # NaN survives every comparison
            return None
        role = raw.get("role")
        if role not in ROLES:
            return None
        hid = raw.get("id")
        if not isinstance(hid, int) or isinstance(hid, bool):
            return None
        conf = raw.get("conf", 0.0)
        if not isinstance(conf, (int, float)) or isinstance(conf, bool):
            conf = 0.0
        return cls(id=hid, role=role, x=x, y=y, conf=float(conf))


@dataclass
class CursorFrame:
    """Doc section 4.6's datagram: `{"seq":..,"ts":..,"hands":[...]}`."""

    seq: int
    ts: float
    hands: List[Hand] = field(default_factory=list)

    def pointer(self) -> Optional[Hand]:
        """The one hand that may select, or None.

        Doc section 11.3 assigns the pointer role to at most one tracked
        id at a time, so this returns the first rather than choosing
        between candidates — a frame with two pointers is a tracker bug,
        and picking a winner here would hide it. The highest-confidence
        one is not "safer": it would make the selecting hand swap
        mid-gesture, which is exactly what step 3's role lock forbids.
        """
        for h in self.hands:
            if h.is_pointer:
                return h
        return None

    def to_json(self) -> Dict[str, Any]:
        return {"seq": self.seq, "ts": round(self.ts, 3),
                "hands": [h.to_json() for h in self.hands]}


def encode(frame: CursorFrame) -> bytes:
    """One frame to one datagram. No trailing newline — a datagram is its
    own frame and adding one would only invite someone to write a line
    splitter for a transport that cannot need one.
    """
    return json.dumps(frame.to_json(), separators=(",", ":")).encode("utf-8")


def decode(data: bytes) -> Optional[CursorFrame]:
    """One datagram to one frame, or `None` if it is not usable.

    Returning None rather than raising, for the same reason `wire.decode`
    does: a garbled datagram must be dropped and counted, never allowed to
    take down the loop that is drawing the table.
    """
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
    hands: List[Hand] = []
    if isinstance(raw_hands, list):
        for raw in raw_hands:
            hand = Hand.from_json(raw)
            if hand is not None:
                hands.append(hand)
    return CursorFrame(seq=seq, ts=float(ts), hands=hands)


# ---------------------------------------------------------------------------
# Sending — the tracker's side
# ---------------------------------------------------------------------------

class Sender:
    """One UDP socket, N destinations, one shared sequence number.

    **The shared seq is the point of sending to both ports from one
    object.** Doc section 4.6 sends the same datagram to `of` and to
    `core`, and both of them gate on `seq` (see `Receiver`). Two senders
    with two counters would let the two consumers disagree about which
    frame is newest — the table drawing a hand core had already gated
    away — and the disagreement would only ever show up as an
    intermittent, unreproducible jitter on one surface.

    Never raises on a send. A UDP datagram to a port nobody is listening
    on is the ordinary state of the world here: doc section 3.3 makes any
    process able to start, die and restart in any order, so the tracker
    will routinely be running before oF and after core has died. Sends are
    counted, not reported — a cursor packet has nobody to tell.
    """

    def __init__(self, targets: Optional[Sequence[Tuple[str, int]]] = None,
                 sock: Optional[Any] = None) -> None:
        self.targets: List[Tuple[str, int]] = [
            (str(h), int(p)) for h, p in
            (targets if targets is not None
             else [(LOCALHOST, OF_PORT), (LOCALHOST, CORE_PORT)])
        ]
        # Injectable for the same reason `ScaleReader.open_port` is: a test
        # must be able to see exactly what went on the wire without binding
        # a real port, and a test that binds real ports is a test that
        # fails on a machine where something else already has them.
        self._sock = sock if sock is not None else socket.socket(
            socket.AF_INET, socket.SOCK_DGRAM)
        self._seq = 0
        self.sent = 0
        self.failed = 0

    @property
    def seq(self) -> int:
        """The seq the last `send()` used. -1 before the first."""
        return self._seq - 1

    def send(self, hands: Iterable[Hand], ts: float) -> CursorFrame:
        """Build one frame, stamp it with the next seq, put it on every
        target. Returns the frame, so a caller that also wants to log or
        display it does not have to rebuild it.
        """
        frame = CursorFrame(seq=self._seq, ts=ts, hands=list(hands))
        self._seq += 1
        payload = encode(frame)
        for target in self.targets:
            try:
                self._sock.sendto(payload, target)
                self.sent += 1
            except OSError as e:
                # Windows raises ConnectionResetError here after an earlier
                # datagram drew an ICMP port-unreachable — i.e. the failure
                # is reported one send late and against the wrong packet.
                # Nothing to do about it and nothing worth doing: the next
                # send to a listener that has come back up succeeds.
                self.failed += 1
                _log.debug("cursorbus: send to %s:%d failed: %s",
                           target[0], target[1], e)
        return frame

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Receiving — core's and oF's side (oF has its own C++ copy of this rule)
# ---------------------------------------------------------------------------

class Receiver:
    """A bound UDP port with exactly one read operation: drain to latest.

    There is deliberately no `recv_one()`. Doc section 4's receiver rule is
    the whole reason this transport was chosen, and an API that offers a
    per-packet read offers a caller the one thing the design forbids.
    """

    def __init__(self, host: str = LOCALHOST, port: int = CORE_PORT,
                 sock: Optional[Any] = None) -> None:
        self.host = host
        self.port = port
        if sock is not None:
            self._sock = sock
        else:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind((host, port))
            # Port 0 means "let the OS pick", which is how a test binds
            # without racing whatever else is on this machine.
            self.port = self._sock.getsockname()[1]
        self._sock.setblocking(False)

        # Rule 2 in the module docstring. -1 rather than 0 so that a
        # sender's very first frame (seq 0) is not gated away.
        self.last_seq = -1
        self.received = 0
        self.dropped_stale = 0
        self.malformed = 0

    def recv_latest(self) -> Optional[CursorFrame]:
        """Read every datagram waiting on the socket, return the newest one
        worth acting on, and discard the rest.

        `None` means nothing NEW arrived — either the socket was empty or
        everything in it was stale or malformed. It does not mean "no
        hands": a frame with an empty `hands` list is a real answer (the
        table is empty) and is returned as one.
        """
        best: Optional[CursorFrame] = None
        for _ in range(MAX_DRAIN):
            try:
                data, _addr = self._sock.recvfrom(RECV_BUFFER)
            except BlockingIOError:
                break
            except OSError as e:
                # See Sender.send: on Windows a UDP socket can surface an
                # earlier ICMP error here. Not fatal, and not a reason to
                # stop draining what is already buffered behind it.
                _log.debug("cursorbus: recv on %d failed: %s", self.port, e)
                break
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
        """Forget the seq gate.

        Needed because the tracker's counter restarts at 0 when the tracker
        restarts (doc section 3.3 makes that ordinary), and a receiver that
        had reached seq 40,000 would gate away every packet from the new
        process — permanently, and looking exactly like a dead tracker.
        Callers detect that as a long silence, not as an out-of-order
        packet; `Receiver` cannot tell the two apart from one datagram, so
        it does not try to guess.
        """
        self.last_seq = -1

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass
