"""Liveness: who is beating, who has gone quiet (doc sections 4.2, 12.2, 20.1).

Two halves, split the same way wire.py is split:

    Heartbeat   everybody else's side. One thread, one line per second.
    Registry    core's side. Ages every beat and decides the six pips.

They do not know about each other, and neither imports wire. Heartbeat takes
a send callable; Registry takes a name and a timestamp. That keeps the whole
file testable without a socket, and it is why `wire.Client.send`,
`Connection.send` and a list.append in a test are all equally valid senders.

Direction
---------
Heartbeats run **client to core only**. Core never beats back, because the
clients already have a faster and more certain signal for core dying: the TCP
connection drops (doc section 20.1). Adding a reverse heartbeat would be a
second, slower way to learn something already known.

Core's own pip is the exception, and it is not a special case in the code:
core calls `registry.beat("core")` from its own main loop. That is worth more
than hardcoding core green, because it distinguishes "core is serving the
staff view" from "core's main loop is still turning" — a wedged loop with a
live web thread is a real failure and it now shows up red.

Why liveness is measured on a monotonic clock
---------------------------------------------
The `hb` line carries the sender's wall clock, per doc section 4.2, but the
registry ages beats by **when they arrived**, read from `time.monotonic()`.
It never subtracts the sender's timestamp from its own.

This is load-bearing on this hardware. The ODYSSEY has no RTC battery
guarantee and will step its clock when NTP first lands, seconds to years, at
some arbitrary moment during bring-up. Trusting the wire timestamp means a
backwards step makes every process look permanently fresh, and a forwards
step marks all six dead at once. Both would present as a system-wide fault
that vanishes on restart, which is the worst kind of bug to chase.

The sender's timestamp is still recorded, as `skew`. It is a diagnostic — a
process whose clock disagrees with core's is worth knowing about before it
confuses a log — and it is never allowed to affect a status.

The thresholds
--------------
Doc section 4.2 fixes two of the three numbers: beat every 1000ms, dead after
3 missed beats (3s). Amber is named in doc section 12.2 but never given a
threshold, so it is chosen here: **two missed beats**. That leaves a one
second amber band before red, which is long enough to see a process
stuttering — the throttled-board symptom in doc section 12.8 — and short
enough that ordinary scheduler jitter at 1Hz does not flap the pip.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# Doc section 4.2. The interval is the contract between the two halves of
# this file: change it and both thresholds below must move with it.
HEARTBEAT_INTERVAL = 1.0

# Two missed beats: amber. Three: red, which is doc section 4.2 exactly, and
# is what the M0 acceptance test measures when it kill -9's a child.
LATE_AFTER = 2.0
DEAD_AFTER = 3.0

# How often the registry re-ages its entries. A pip must go red 3s after the
# last beat whether or not any traffic arrives to trigger the check, so
# something has to poll. Matches wire.TICK for no reason beyond consistency.
TICK = 0.25

# Statuses. Two of the four map to red on purpose — see COLOUR.
UP = "up"
LATE = "late"
DOWN = "down"
FAILED = "failed"

# Doc section 12.2: green/amber/red. `failed` is red because to anyone
# looking at the table it is red, but it is kept distinct from `down`
# because they call for different actions: `down` is expected to fix itself,
# `failed` means the launcher has stopped trying (doc section 20.2) and a
# human has to intervene.
COLOUR = {UP: "green", LATE: "amber", DOWN: "red", FAILED: "red"}

# The six pips of doc section 12.2, in the order they are drawn. `of` is the
# process name on the wire; the staff view labels that pip `table`. Naming
# them here means a process that has never once connected still has a pip,
# and that pip is red — which is the honest thing to show, and the reason
# the M0 acceptance test can count six of them.
PROCESSES: Tuple[str, ...] = ("camera", "tracker", "classifier", "voice", "core", "of")

log = logging.getLogger("hotpot.health")


# ---------------------------------------------------------------------------
# Heartbeat — everybody else's side
# ---------------------------------------------------------------------------

class Heartbeat:
    """One `{"t":"hb"}` line per interval, forever, on its own thread.

    Deliberately indifferent to whether the link is up. `send` returning
    False means the line was dropped because there was nowhere to put it
    (wire.py's drop rule), which during a core restart is the normal state of
    the world for a few seconds. Dropped beats are counted and nothing else
    happens: when the link returns, the next beat goes out on schedule and
    core sees the process is alive.

    A heartbeat that stopped itself while core was away, or that treated a
    failed send as an error worth escalating, would break the one rule
    everything else here rests on — a client must never react to core being
    absent (doc section 20.2).
    """

    def __init__(
        self,
        send: Callable[[Dict[str, Any]], Any],
        *,
        who: str = "",
        interval: float = HEARTBEAT_INTERVAL,
    ) -> None:
        self._send = send
        self.who = who
        self.interval = interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self.sent = 0
        self.dropped = 0
        self.late = 0           # times this process was too starved to beat on time

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Begin beating. Returns immediately. Safe to call once."""
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"health-hb-{self.who or 'anon'}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop beating. Idempotent. Returns once the thread is gone."""
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(2.0)
        self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def stats(self) -> Dict[str, Any]:
        return {"who": self.who, "sent": self.sent,
                "dropped": self.dropped, "late": self.late}

    # -- io ----------------------------------------------------------------

    def beat_now(self) -> bool:
        """Send one beat immediately. False if it was dropped.

        Public because it is the right thing to call from a wire on_connect
        handler: the link has just come up, and waiting up to a second to
        prove it works is a second of amber for no reason.
        """
        try:
            ok = bool(self._send({"t": "hb", "ts": time.time()}))
        except Exception:
            # A sender that raises is a bug in the sender, not a reason for
            # this process to lose its heartbeat and be declared dead.
            log.exception("%s: heartbeat send raised", self.who or "hb")
            ok = False
        if ok:
            self.sent += 1
        else:
            self.dropped += 1
        return ok

    # -- internals ---------------------------------------------------------

    def _run(self) -> None:
        # Deadlines are absolute so the beat rate does not drift by however
        # long send() took, and they are *resynchronised* rather than caught
        # up on. If this process was descheduled for five seconds, core has
        # already declared it late and firing five instant beats would prove
        # nothing except that five beats can be queued.
        due = time.monotonic()
        while not self._stop.is_set():
            self.beat_now()
            due += self.interval
            now = time.monotonic()
            if due <= now:
                self.late += 1
                due = now + self.interval
            if self._stop.wait(due - now):
                return


# ---------------------------------------------------------------------------
# Registry — core's side
# ---------------------------------------------------------------------------

@dataclass
class Entry:
    """One process, as core currently understands it.

    Every time field is monotonic seconds. 0.0 means never.
    """

    who: str
    status: str = DOWN
    pid: Optional[int] = None
    ver: Optional[int] = None
    beats: int = 0
    connects: int = 0
    last_beat: float = 0.0
    since: float = 0.0          # when status last changed
    up_since: float = 0.0       # when it last came up; the process's uptime
    skew: float = 0.0           # sender's wall clock minus ours, diagnostic only
    reason: str = ""            # why it went down, when that is known

    # The launcher's verdict, kept beside the status rather than inside it.
    # Encoding it as a status would mean clearing it had to invent some
    # intermediate value to pass through, and every listener would then see
    # one transition reporting an `old` it was never told about.
    failed: bool = False

    @property
    def restarts(self) -> int:
        return max(0, self.connects - 1)


class Registry:
    """Who is alive. The source the six pips and doc section 12.8 read from.

    Fed from three places, all of which core already has in front of it:

        connected(who)     a hello arrived      (wire Server on_connect)
        beat(who, ts)      an hb arrived        (wire Server on_message)
        disconnected(who)  the link dropped     (wire Server on_disconnect)

    plus `mark_failed`, which is the launcher's verdict rather than an
    observation (doc section 20.2).

    A dropped TCP connection is treated as immediately fatal rather than
    waiting out the 3s heartbeat timeout. Both are correct, but the
    disconnect is both faster and more certain, and doc section 20.1 already
    names it as how `of` dying is noticed.

    Thread safety: every public method takes the lock, and `on_change` fires
    outside it. Callbacks run on whichever thread caused the transition —
    usually the ticker, sometimes a wire read thread — and must not block,
    for the same reason wire's handlers must not (I1/I3).
    """

    def __init__(
        self,
        expected: Sequence[str] = PROCESSES,
        *,
        interval: float = HEARTBEAT_INTERVAL,
        late_after: float = LATE_AFTER,
        dead_after: float = DEAD_AFTER,
        on_change: Optional[Callable[[str, str, str], None]] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.interval = interval
        self.late_after = late_after
        self.dead_after = dead_after
        self._on_change = on_change
        self._clock = clock

        self._lock = threading.Lock()
        self._entries: Dict[str, Entry] = {}
        self._order: List[str] = list(expected)
        for who in self._order:
            self._entries[who] = Entry(who=who, since=clock())

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Start the ticker thread.

        Optional: a caller that already has a loop can call tick() from it
        instead. Core starts the thread, because the pip deadline in doc
        section 4.2 is 3s of wall time and must not depend on core's loop
        being healthy — the case where it is not is exactly when the pips
        matter most.
        """
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="health-registry", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the ticker thread. Idempotent."""
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(2.0)
        self._thread = None

    # -- feeding it --------------------------------------------------------

    def connected(self, who: str, *, pid: Optional[int] = None,
                  ver: Optional[int] = None) -> None:
        """A hello arrived. Counts as proof of life in its own right."""
        now = self._clock()
        with self._lock:
            e = self._entry(who, now)
            e.connects += 1
            e.pid = pid
            e.ver = ver
            e.reason = ""
            e.up_since = now
            e.last_beat = now       # hello is a beat; do not sit amber until the first hb
            if e.failed:
                # The only honest way out of failed: a new process said hello.
                log.info("health: %s is back after being marked failed", who)
                e.failed = False
            changes = self._refresh_locked(now)
        self._fire(changes)

    def beat(self, who: str, ts: Optional[float] = None) -> None:
        """A heartbeat arrived — or core beating itself from its own loop.

        `ts` is the sender's wall clock and is recorded as skew only. See the
        module docstring for why it is never used to compute an age.
        """
        now = self._clock()
        with self._lock:
            e = self._entry(who, now)
            if e.failed:
                # The launcher has given up on a process that is evidently
                # still talking. Someone is wrong and it is worth saying so
                # out loud, but the launcher owns the restart decision, so
                # the status stands until a fresh hello.
                log.warning("health: beat from %s, which is marked failed", who)
            e.beats += 1
            e.last_beat = now
            if e.up_since == 0.0:
                e.up_since = now    # core self-beating, which never says hello
            if ts is not None:
                e.skew = ts - time.time()
            changes = self._refresh_locked(now)
        self._fire(changes)

    def disconnected(self, who: str, reason: str = "link lost") -> None:
        """The link dropped. Down now, not in three seconds."""
        now = self._clock()
        with self._lock:
            e = self._entry(who, now)
            e.last_beat = 0.0
            e.up_since = 0.0
            e.pid = None
            e.reason = reason
            changes = self._refresh_locked(now)
        self._fire(changes)

    def mark_failed(self, who: str, reason: str = "restart limit reached") -> None:
        """The launcher has stopped restarting this one (doc section 20.2).

        Sticky: only `connected()` clears it. `failed` is a statement about
        the supervisor's intent, not about the socket, so nothing observed on
        the socket can retract it except a genuinely new process arriving.
        """
        now = self._clock()
        with self._lock:
            e = self._entry(who, now)
            e.last_beat = 0.0
            e.up_since = 0.0
            e.reason = reason
            e.failed = True
            changes = self._refresh_locked(now)
        self._fire(changes)

    def handle(self, who: str, msg: Dict[str, Any]) -> bool:
        """Consume `hb` out of core's message dispatch. True if consumed.

        Lets core write `if health.handle(conn.who, msg): return` at the top
        of its handler instead of open-coding the beat, so there is one place
        that knows what a heartbeat looks like on the wire.
        """
        if msg.get("t") != "hb":
            return False
        ts = msg.get("ts")
        self.beat(who, float(ts) if isinstance(ts, (int, float)) else None)
        return True

    # -- reading it --------------------------------------------------------

    def tick(self) -> List[Tuple[str, str, str]]:
        """Re-age every entry. Returns the transitions, after firing them."""
        now = self._clock()
        with self._lock:
            changes = self._refresh_locked(now)
        self._fire(changes)
        return changes

    def status(self, who: str) -> str:
        with self._lock:
            e = self._entries.get(who)
            return e.status if e is not None else DOWN

    def all_up(self) -> bool:
        with self._lock:
            return all(e.status == UP for e in self._entries.values())

    def not_up(self) -> List[str]:
        """Names that are not green, in pip order. For banners and run.py."""
        with self._lock:
            return [w for w in self._order if self._entries[w].status != UP]

    def snapshot(self) -> List[Dict[str, Any]]:
        """The pip row, in draw order, with the doc section 12.8 detail.

        `age` is None rather than 0.0 for a process that has never beaten,
        because "no beat ever" and "beat just now" are opposite facts and a
        zero would render as the healthiest thing on the screen.
        """
        now = self._clock()
        with self._lock:
            out = []
            for who in self._order:
                e = self._entries[who]
                out.append({
                    "who": who,
                    "status": e.status,
                    "colour": COLOUR[e.status],
                    "pid": e.pid,
                    "age": None if e.last_beat == 0.0 else round(now - e.last_beat, 3),
                    "uptime": None if e.up_since == 0.0 else round(now - e.up_since, 1),
                    "for": round(now - e.since, 1),
                    "restarts": e.restarts,
                    "beats": e.beats,
                    "skew": round(e.skew, 3),
                    "reason": e.reason,
                })
            return out

    # -- internals ---------------------------------------------------------

    def _entry(self, who: str, now: float) -> Entry:
        """Lock held. Unknown names are tracked, not rejected.

        An unexpected `who` is a process this build does not know about,
        which is information worth keeping rather than dropping on the floor.
        It lands after the six named pips and never disturbs their order.
        """
        e = self._entries.get(who)
        if e is None:
            log.info("health: tracking an unexpected process %r", who)
            e = Entry(who=who, since=now)
            self._entries[who] = e
            self._order.append(who)
        return e

    def _derive(self, e: Entry, now: float) -> str:
        """Lock held. Status from age alone, which is the whole model."""
        if e.failed:
            return FAILED
        if e.last_beat == 0.0:
            return DOWN
        age = now - e.last_beat
        if age > self.dead_after:
            return DOWN
        if age > self.late_after:
            return LATE
        return UP

    def _set_locked(self, e: Entry, status: str,
                    now: float) -> List[Tuple[str, str, str]]:
        if e.status == status:
            return []
        old, e.status = e.status, status
        e.since = now
        return [(e.who, old, status)]

    def _refresh_locked(self, now: float) -> List[Tuple[str, str, str]]:
        changes: List[Tuple[str, str, str]] = []
        for e in self._entries.values():
            changes.extend(self._set_locked(e, self._derive(e, now), now))
        return changes

    def _fire(self, changes: List[Tuple[str, str, str]]) -> None:
        for who, old, new in changes:
            log.info("health: %s %s -> %s", who, old, new)
            if self._on_change is None:
                continue
            try:
                self._on_change(who, old, new)
            except Exception:
                log.exception("health: on_change raised for %s", who)

    def _run(self) -> None:
        while not self._stop.wait(TICK):
            self.tick()
