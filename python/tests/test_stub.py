"""Tests for common/stub.py.

Run from the repo root:

    python -m unittest discover -s python/tests -v

stub.py is the whole of M0's camera/tracker/classifier/voice (doc section
21, build item 6): connect, say hello, heartbeat, print HOTPOT-READY, do
nothing else. These use a real wire.Server, the same way test_wire.py
does, because the property that matters most here — readiness printed
before the link is even up — is exactly the kind of ordering a mock would
get right by construction and a real reconnect loop would not.
"""

import io
import os
import socket
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hotpot.common import log as hlog  # noqa: E402
from hotpot.common import stub  # noqa: E402
from hotpot.common import wire  # noqa: E402

DEADLINE = 5.0


def wait_for(pred, timeout=DEADLINE, tick=0.01):
    """Poll until pred() is truthy. Returns the value, or False on timeout."""
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(tick)
    return False


def text_stream():
    """A real TextIOWrapper over a BytesIO — see test_log.py for why."""
    raw = io.BytesIO()
    return raw, io.TextIOWrapper(raw, encoding="utf-8", newline="\n")


def dead_port() -> int:
    """A port nothing is listening on: bind, read the number, close it."""
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


class Collector:
    """Thread-safe sink for what arrives on the server side."""

    def __init__(self):
        self.msgs = []
        self.connected = []
        self._lock = threading.Lock()

    def on_message(self, conn, msg):
        with self._lock:
            self.msgs.append((conn.who, msg))

    def on_connect(self, conn):
        with self._lock:
            self.connected.append(conn.who)

    def count(self, t):
        with self._lock:
            return sum(1 for _, m in self.msgs if m.get("t") == t)


class StubCase(unittest.TestCase):
    """A real core-side Server on an ephemeral port, torn down after."""

    def setUp(self):
        hlog.reset()
        self.addCleanup(hlog.reset)
        self.sink = Collector()
        self.server = wire.Server("127.0.0.1", 0,
                                  on_message=self.sink.on_message,
                                  on_connect=self.sink.on_connect,
                                  name="core")
        self.port = self.server.start()
        self._stubs = []

    def tearDown(self):
        for s in self._stubs:
            s.stop()
        self.server.stop()

    def start(self, who, port=None):
        """stub.start against the fixture server, with captured streams."""
        log_raw, log_stream = text_stream()
        ready_raw, ready_stream = text_stream()
        s = stub.start(who, "127.0.0.1", self.port if port is None else port,
                       log_stream=log_stream, ready_stream=ready_stream)
        # Kept alive on the stub: a TextIOWrapper with no other referent
        # closes its underlying BytesIO when garbage-collected, which would
        # make a later .getvalue() raise "I/O operation on closed file".
        s.log_stream, s.log_raw = log_stream, log_raw
        s.ready_stream, s.ready_raw = ready_stream, ready_raw
        self._stubs.append(s)
        return s


class TestConnects(StubCase):

    def test_says_hello_and_is_welcomed(self):
        s = self.start("camera")
        self.assertTrue(wait_for(lambda: "camera" in self.sink.connected),
                        "camera never said hello")
        self.assertTrue(s.client.wait_connected(DEADLINE))

    def test_heartbeats_reach_core(self):
        self.start("tracker")
        self.assertTrue(wait_for(lambda: self.sink.count("hb") >= 1),
                        "no heartbeat arrived")


class TestReadiness(StubCase):

    def test_prints_ready_immediately_even_though_nothing_is_listening(self):
        """Doc section 10.3: camera is tier 1, core is tier 2. A camera
        whose readiness waited for a welcome would deadlock the start
        order, so readiness has to fire the moment the stub is up, not
        once it has actually connected.
        """
        s = self.start("camera", port=dead_port())
        self.assertEqual(s.ready_raw.getvalue(), b"HOTPOT-READY camera\n")
        self.assertFalse(s.client.connected)

    def test_ready_line_names_the_calling_process(self):
        s = self.start("voice")
        self.assertEqual(s.ready_raw.getvalue(), b"HOTPOT-READY voice\n")

    def test_keeps_retrying_after_announcing_ready(self):
        """Doc section 3.3: start order must not matter. A stub announced
        as ready against a dead port has to go on trying core, not give up
        having said its one line.
        """
        s = self.start("classifier", port=dead_port())
        self.assertTrue(wait_for(lambda: s.client.attempts >= 2, 20.0),
                        "stub stopped retrying: %r" % (s.client.stats,))


class TestStop(StubCase):

    def test_stop_drops_the_link_and_the_heartbeat(self):
        s = self.start("classifier")
        self.assertTrue(s.client.wait_connected(DEADLINE))
        s.stop()
        self.assertFalse(s.client.connected)
        self.assertFalse(s.heartbeat.running)


if __name__ == "__main__":
    unittest.main()
