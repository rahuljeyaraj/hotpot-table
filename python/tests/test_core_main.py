"""Tests for core/main.py — M0 build item 7.

Run from the repo root:

    python -m unittest discover -s python/tests -v

Exercises the whole wiring end to end: a real wire.Client stands in for a
sibling process, a real WebSocket client stands in for a staff tablet, and
the assertion is that a hello/heartbeat/disconnect on one side shows up as
a pip transition pushed out the other — the actual thing M0.7 has to
prove, not just that each half works in isolation.
"""

import base64
import json
import os
import socket
import struct
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hotpot.common import health  # noqa: E402
from hotpot.common import log as hlog  # noqa: E402
from hotpot.common import wire  # noqa: E402
from hotpot.core import main as coremain  # noqa: E402

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


class _WSClient:
    """The same minimal hand-rolled client as test_web.py.

    Duplicated rather than imported: every test file in this suite builds
    its own fixtures rather than reaching into a sibling test module, and
    a WebSocket client small enough to hand-roll is small enough to
    hand-roll twice.
    """

    def __init__(self, host, port, path="/ws", timeout=DEADLINE):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self._buf = b""
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\n"
               "Upgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode("ascii"))
        head = self._read_until(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(f"handshake refused: {head!r}")

    def _read_until(self, sep):
        while sep not in self._buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("peer closed during handshake")
            self._buf += chunk
        idx = self._buf.index(sep) + len(sep)
        head, self._buf = self._buf[:idx], self._buf[idx:]
        return head

    def _recv_exact(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(4096)
            if not chunk:
                return None
            self._buf += chunk
        data, self._buf = self._buf[:n], self._buf[n:]
        return data

    def recv_json(self):
        head = self._recv_exact(2)
        if head is None:
            return None
        _, b1 = head
        length = b1 & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8))[0]
        payload = self._recv_exact(length) if length else b""
        return json.loads(payload.decode("utf-8")) if payload else None

    def recv_until(self, pred, timeout=DEADLINE):
        """Read frames until one satisfies `pred`, or give up. Needed
        because a pip change is pushed as a fresh broadcast, not a diff —
        the message that matters may not be the very next one on the wire
        if two transitions land close together.
        """
        prior_timeout = self.sock.gettimeout()
        self.sock.settimeout(0.2)
        end = time.time() + timeout
        try:
            while time.time() < end:
                try:
                    msg = self.recv_json()
                except socket.timeout:
                    continue
                if msg is not None and pred(msg):
                    return msg
        finally:
            self.sock.settimeout(prior_timeout)
        return None

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def pip_colour(msg, who):
    for p in msg.get("pips", []):
        if p["who"] == who:
            return p["colour"]
    return None


class CoreCase(unittest.TestCase):
    """A real Core on ephemeral loopback ports, torn down after."""

    def setUp(self):
        hlog.reset()
        self.addCleanup(hlog.reset)
        self.core = coremain.start(
            control_host="127.0.0.1", control_port=0,
            web_host="127.0.0.1", web_port=0)
        self._wire_clients = []
        self._ws_clients = []

    def tearDown(self):
        for c in self._wire_clients:
            c.stop()
        for c in self._ws_clients:
            c.close()
        self.core.stop()

    def wire_client(self, who):
        c = wire.Client("127.0.0.1", self.core.control_port, who)
        self._wire_clients.append(c)
        c.start()
        return c

    def ws(self):
        c = _WSClient("127.0.0.1", self.core.web_port)
        self._ws_clients.append(c)
        return c


class TestBinding(CoreCase):

    def test_both_ports_are_bound_by_the_time_start_returns(self):
        self.assertNotEqual(self.core.control_port, 0)
        self.assertNotEqual(self.core.web_port, 0)


class TestSixPipsSeeded(CoreCase):

    def test_all_six_process_names_appear_even_before_anyone_connects(self):
        msg = self.ws().recv_json()
        self.assertEqual(msg["t"], "pips")
        whos = {p["who"] for p in msg["pips"]}
        self.assertEqual(whos, set(health.PROCESSES))

    def test_a_never_connected_process_is_red(self):
        msg = self.ws().recv_json()
        self.assertEqual(pip_colour(msg, "camera"), "red")


class TestConnectAndDisconnect(CoreCase):

    def test_hello_lights_the_pip_green_over_the_websocket(self):
        w = self.ws()
        w.recv_json()   # the seed snapshot; camera is still red in it
        self.wire_client("camera")
        msg = w.recv_until(lambda m: pip_colour(m, "camera") == "green")
        self.assertIsNotNone(msg, "camera never turned green on the socket")

    def test_disconnect_turns_it_red_again(self):
        w = self.ws()
        w.recv_json()
        client = self.wire_client("camera")
        self.assertTrue(client.wait_connected(DEADLINE))
        self.assertIsNotNone(w.recv_until(lambda m: pip_colour(m, "camera") == "green"))

        client.stop()
        msg = w.recv_until(lambda m: pip_colour(m, "camera") == "red")
        self.assertIsNotNone(msg, "camera never turned red again after the link dropped")


class TestSelfHeartbeat(CoreCase):

    def test_core_lights_its_own_pip(self):
        """common/health.py's rule: core proves its main loop is alive by
        beating its own pip, rather than being hardcoded green — a wedged
        loop with a live web thread must still show up red.
        """
        self.assertTrue(wait_for(lambda: self.core.registry.status("core") == "up"))


class TestStop(unittest.TestCase):

    def test_stop_is_clean_and_idempotent(self):
        core = coremain.start(control_host="127.0.0.1", control_port=0,
                              web_host="127.0.0.1", web_port=0)
        core.stop()
        core.stop()   # must not raise


if __name__ == "__main__":
    unittest.main()
