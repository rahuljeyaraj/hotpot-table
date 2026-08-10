"""Tests for common/health.py.

Run from the repo root:

    python -m unittest discover -s python/tests -v

The registry tests drive an injected clock rather than sleeping. That is not
only for speed: the thresholds are 2s and 3s, and a test that slept would
have to sleep past them with enough slack to survive a loaded box, which
means it could never assert a boundary tightly enough to catch the sign of a
comparison being wrong. With a fake clock, 1.9s and 2.1s are exact.

Two tests deliberately do not use the fake clock — the heartbeat rate and the
ticker thread — because in both cases the thing under test *is* the real
timing, and a fake clock would only prove the fake advances.
"""

import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hotpot.common import health  # noqa: E402


DEADLINE = 5.0          # generous: CI and a loaded dev box are both slow


def wait_for(pred, timeout=DEADLINE, tick=0.01):
    """Poll until pred() is truthy. Returns the value, or False on timeout."""
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(tick)
    return False


class FakeClock:
    """A monotonic clock that only moves when a test says so."""

    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt
        return self.t


class Sink:
    """Thread-safe collector for beats arriving on the heartbeat thread."""

    def __init__(self, ok=True):
        self.msgs = []
        self.ok = ok
        self.raises = False
        self._lock = threading.Lock()

    def send(self, msg):
        with self._lock:
            self.msgs.append(msg)
        if self.raises:
            raise RuntimeError("sender exploded")
        return self.ok

    def count(self):
        with self._lock:
            return len(self.msgs)


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------

class TestHeartbeat(unittest.TestCase):

    def test_beats_at_the_configured_rate(self):
        sink = Sink()
        hb = health.Heartbeat(sink.send, who="tracker", interval=0.05)
        hb.start()
        self.addCleanup(hb.stop)
        # 0.5s at 20Hz is 10 beats. Assert a wide band: the point is that it
        # keeps beating on schedule, not that the OS scheduler is exact.
        self.assertTrue(wait_for(lambda: sink.count() >= 8))
        time.sleep(0.1)
        self.assertLess(sink.count(), 40)

    def test_beat_is_the_documented_shape(self):
        sink = Sink()
        hb = health.Heartbeat(sink.send, who="voice", interval=10.0)
        self.assertTrue(hb.beat_now())
        msg = sink.msgs[0]
        self.assertEqual(msg["t"], "hb")
        self.assertIsInstance(msg["ts"], float)
        # ts is a wall clock, per doc section 4.2 — not a monotonic reading,
        # which on Linux is seconds since boot and would be nonsense in a log.
        self.assertLess(abs(msg["ts"] - time.time()), 5.0)
        self.assertEqual(set(msg), {"t", "ts"})

    def test_keeps_beating_while_the_link_is_down(self):
        """The rule from doc section 20.2: never react to core being absent."""
        sink = Sink(ok=False)
        hb = health.Heartbeat(sink.send, who="camera", interval=0.05)
        hb.start()
        self.addCleanup(hb.stop)
        self.assertTrue(wait_for(lambda: hb.dropped >= 5))
        self.assertEqual(hb.sent, 0)
        self.assertTrue(hb.running)

        sink.ok = True          # core came back
        before = hb.sent
        self.assertTrue(wait_for(lambda: hb.sent >= before + 3))

    def test_a_raising_sender_does_not_kill_the_thread(self):
        sink = Sink()
        sink.raises = True
        hb = health.Heartbeat(sink.send, who="of", interval=0.05)
        hb.start()
        self.addCleanup(hb.stop)
        self.assertTrue(wait_for(lambda: sink.count() >= 5))
        self.assertTrue(hb.running)
        self.assertEqual(hb.sent, 0)        # counted as dropped, not sent

    def test_stop_is_prompt_and_final(self):
        sink = Sink()
        hb = health.Heartbeat(sink.send, who="classifier", interval=0.05)
        hb.start()
        self.assertTrue(wait_for(lambda: sink.count() >= 2))
        hb.stop()
        self.assertFalse(hb.running)
        settled = sink.count()
        time.sleep(0.2)
        self.assertEqual(sink.count(), settled)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestRegistry(unittest.TestCase):

    def setUp(self):
        self.clock = FakeClock()
        self.changes = []
        self.reg = health.Registry(
            on_change=lambda who, old, new: self.changes.append((who, old, new)),
            clock=self.clock)

    def test_six_pips_exist_before_anything_connects(self):
        snap = self.reg.snapshot()
        self.assertEqual([p["who"] for p in snap],
                         ["camera", "tracker", "classifier", "voice", "core", "of"])
        self.assertTrue(all(p["status"] == health.DOWN for p in snap))
        self.assertTrue(all(p["colour"] == "red" for p in snap))
        # Never beaten is not the same fact as beaten just now.
        self.assertTrue(all(p["age"] is None for p in snap))

    def test_hello_alone_turns_it_green(self):
        self.reg.connected("tracker", pid=4412, ver=3)
        self.assertEqual(self.reg.status("tracker"), health.UP)
        self.assertIn(("tracker", health.DOWN, health.UP), self.changes)
        entry = next(p for p in self.reg.snapshot() if p["who"] == "tracker")
        self.assertEqual(entry["pid"], 4412)
        self.assertEqual(entry["restarts"], 0)

    def test_silence_goes_amber_then_red_at_the_documented_thresholds(self):
        self.reg.connected("camera")

        self.clock.advance(1.9)
        self.reg.tick()
        self.assertEqual(self.reg.status("camera"), health.UP)

        self.clock.advance(0.2)         # 2.1s: two beats missed
        self.reg.tick()
        self.assertEqual(self.reg.status("camera"), health.LATE)

        self.clock.advance(0.8)         # 2.9s: still inside the doc's 3s
        self.reg.tick()
        self.assertEqual(self.reg.status("camera"), health.LATE)

        self.clock.advance(0.2)         # 3.1s: three beats missed, dead
        self.reg.tick()
        self.assertEqual(self.reg.status("camera"), health.DOWN)

    def test_a_beat_brings_it_back(self):
        self.reg.connected("voice")
        self.clock.advance(3.5)
        self.reg.tick()
        self.assertEqual(self.reg.status("voice"), health.DOWN)

        self.reg.beat("voice", ts=time.time())
        self.assertEqual(self.reg.status("voice"), health.UP)
        # Amber is skipped here, and that is correct: the clock jumped the
        # whole way in one step, so no observation ever placed this process
        # in the amber band. A listener is told what was seen, not what a
        # finer-grained clock would have seen. With the real 250ms ticker the
        # band is always passed through — TestRegistryTicker asserts that.
        self.assertEqual(
            [c for c in self.changes if c[0] == "voice"],
            [("voice", health.DOWN, health.UP),
             ("voice", health.UP, health.DOWN),
             ("voice", health.DOWN, health.UP)])

    def test_transitions_fire_once_not_every_tick(self):
        self.reg.connected("of")
        self.clock.advance(2.5)
        for _ in range(10):
            self.reg.tick()
        self.clock.advance(1.0)
        for _ in range(10):
            self.reg.tick()

        # down->up on hello, up->late at 2.5s, late->down at 3.5s. Twenty
        # ticks, three callbacks: the pip is a level, the callback is an edge.
        self.assertEqual([c for c in self.changes if c[0] == "of"],
                         [("of", health.DOWN, health.UP),
                          ("of", health.UP, health.LATE),
                          ("of", health.LATE, health.DOWN)])

    def test_a_dropped_link_is_red_immediately(self):
        """Doc section 20.1: a TCP disconnect is how `of` dying is noticed."""
        self.reg.connected("of")
        self.reg.disconnected("of", "peer closed")
        self.assertEqual(self.reg.status("of"), health.DOWN)     # no 3s wait
        entry = next(p for p in self.reg.snapshot() if p["who"] == "of")
        self.assertEqual(entry["reason"], "peer closed")
        self.assertIsNone(entry["age"])
        self.assertIsNone(entry["uptime"])

    def test_a_restart_counts_as_one(self):
        self.reg.connected("classifier", pid=100)
        self.clock.advance(1.0)
        self.reg.disconnected("classifier", "killed")
        self.clock.advance(1.0)
        self.reg.connected("classifier", pid=200)

        entry = next(p for p in self.reg.snapshot() if p["who"] == "classifier")
        self.assertEqual(self.reg.status("classifier"), health.UP)
        self.assertEqual(entry["restarts"], 1)
        self.assertEqual(entry["pid"], 200)
        self.assertEqual(entry["uptime"], 0.0)      # uptime is of this process
        self.assertEqual(entry["reason"], "")

    def test_failed_is_sticky_until_a_new_process_says_hello(self):
        self.reg.connected("voice")
        self.reg.mark_failed("voice", "5 failures in 60s")
        self.assertEqual(self.reg.status("voice"), health.FAILED)
        self.assertEqual(health.COLOUR[health.FAILED], "red")

        # A beat from the process the launcher has given up on does not
        # retract the launcher's verdict.
        self.reg.beat("voice")
        self.assertEqual(self.reg.status("voice"), health.FAILED)
        self.clock.advance(10.0)
        self.reg.tick()
        self.assertEqual(self.reg.status("voice"), health.FAILED)

        self.reg.connected("voice")
        self.assertEqual(self.reg.status("voice"), health.UP)
        self.assertIn(("voice", health.FAILED, health.UP), self.changes)

    def test_a_wrong_wall_clock_cannot_affect_liveness(self):
        """The NTP-step failure the module docstring is about."""
        self.reg.connected("camera")
        self.reg.beat("camera", ts=time.time() - 86400.0)   # a day behind
        self.assertEqual(self.reg.status("camera"), health.UP)
        entry = next(p for p in self.reg.snapshot() if p["who"] == "camera")
        self.assertLess(entry["skew"], -86000.0)            # recorded
        self.assertEqual(entry["age"], 0.0)                 # but not used

        self.reg.beat("tracker", ts=time.time() + 86400.0)  # a day ahead
        self.assertEqual(self.reg.status("tracker"), health.UP)
        self.clock.advance(3.5)
        self.reg.tick()
        self.assertEqual(self.reg.status("tracker"), health.DOWN)

    def test_handle_consumes_only_heartbeats(self):
        self.assertTrue(self.reg.handle("of", {"t": "hb", "ts": time.time()}))
        self.assertEqual(self.reg.status("of"), health.UP)

        self.assertFalse(self.reg.handle("of", {"t": "stat", "fps": 59.8}))
        self.assertFalse(self.reg.handle("of", {}))

        # A garbled ts must not stop the beat from counting: the line arrived,
        # so the process is alive, whatever it thinks the time is.
        self.reg.disconnected("of")
        self.assertTrue(self.reg.handle("of", {"t": "hb", "ts": "half past"}))
        self.assertEqual(self.reg.status("of"), health.UP)

    def test_an_unexpected_process_is_tracked_after_the_six(self):
        self.reg.beat("gizmo")
        names = [p["who"] for p in self.reg.snapshot()]
        self.assertEqual(names[:6], list(health.PROCESSES))
        self.assertEqual(names[6], "gizmo")
        self.assertEqual(self.reg.status("gizmo"), health.UP)

    def test_core_can_beat_itself_without_a_hello(self):
        self.reg.beat("core")
        entry = next(p for p in self.reg.snapshot() if p["who"] == "core")
        self.assertEqual(entry["status"], health.UP)
        self.assertEqual(entry["uptime"], 0.0)      # started, not None
        self.assertEqual(entry["restarts"], 0)

    def test_all_up_and_not_up(self):
        self.assertFalse(self.reg.all_up())
        self.assertEqual(self.reg.not_up(), list(health.PROCESSES))
        for who in health.PROCESSES:
            self.reg.connected(who)
        self.assertTrue(self.reg.all_up())
        self.assertEqual(self.reg.not_up(), [])

        self.reg.disconnected("voice")
        self.assertFalse(self.reg.all_up())
        self.assertEqual(self.reg.not_up(), ["voice"])


class TestRegistryTicker(unittest.TestCase):
    """The one registry test on the real clock: a pip must go red on its own.

    Everything above calls tick() by hand, which would pass just as well if
    nothing ever ticked. This is the test that catches that.
    """

    def test_the_pip_goes_red_with_no_traffic_and_no_caller(self):
        changes = []
        # The amber band has to be several ticks wide or this test is a race:
        # the ticker samples every health.TICK, and a band narrower than one
        # sample interval plus load jitter can legitimately be stepped over.
        # In production the band is 1s against a 250ms tick, four samples.
        reg = health.Registry(late_after=0.3, dead_after=1.5,
                              on_change=lambda w, o, n: changes.append((w, o, n)))
        reg.connected("camera")
        reg.start()
        self.addCleanup(reg.stop)
        self.assertEqual(reg.status("camera"), health.UP)

        self.assertTrue(wait_for(lambda: reg.status("camera") == health.DOWN))
        self.assertIn(("camera", health.LATE, health.DOWN), changes)

        reg.beat("camera")
        self.assertEqual(reg.status("camera"), health.UP)


class TestOverTheWire(unittest.TestCase):
    """The two halves composed over a real socket, which is the only way
    they are ever used.

    health.py deliberately does not import wire.py — it takes a callable and
    a name. That keeps it testable, but it also means nothing above proves
    the pieces fit together, and "fits together" is the whole deliverable.
    This is the M0 acceptance test in miniature: a client connects and its
    pip goes green; the client dies and its pip goes red.
    """

    def test_a_client_beats_its_own_pip_green_and_a_death_turns_it_red(self):
        from hotpot.common import wire

        reg = health.Registry(late_after=0.3, dead_after=1.5)
        srv = wire.Server(
            "127.0.0.1", 0,
            on_connect=lambda c: reg.connected(c.who, pid=(c.hello or {}).get("pid")),
            on_message=lambda c, m: reg.handle(c.who, m),
            on_disconnect=lambda c, r: reg.disconnected(c.who, r))
        port = srv.start()
        self.addCleanup(srv.stop)
        reg.start()
        self.addCleanup(reg.stop)

        self.assertEqual(reg.status("tracker"), health.DOWN)

        client = wire.Client("127.0.0.1", port, "tracker")
        hb = health.Heartbeat(client.send, who="tracker", interval=0.05)
        client.start()
        self.addCleanup(client.stop)
        self.assertTrue(client.wait_connected(DEADLINE))
        hb.start()
        self.addCleanup(hb.stop)

        self.assertTrue(wait_for(lambda: reg.status("tracker") == health.UP))
        # Green from real heartbeats, not just from the hello that preceded
        # them — otherwise this passes with the whole beat path deleted.
        self.assertTrue(wait_for(
            lambda: next(p for p in reg.snapshot()
                         if p["who"] == "tracker")["beats"] >= 3))

        hb.stop()
        client.stop()
        self.assertTrue(wait_for(lambda: reg.status("tracker") == health.DOWN))


if __name__ == "__main__":
    unittest.main()
