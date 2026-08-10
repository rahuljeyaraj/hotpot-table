"""Tests for core/main.py — M0 build item 7.

Run from the repo root (after `pip install -r python/requirements.txt`):

    python -m unittest discover -s python/tests -v

Exercises the whole wiring end to end: a real wire.Client stands in for a
sibling process, a real WebSocket client (the `websockets` library, same
as core/web/server.py uses on the server side) stands in for a staff
tablet, and the assertion is that a hello/heartbeat/disconnect on one
side shows up as a pip transition pushed out the other — the actual thing
M0.7 has to prove, not just that each half works in isolation.
"""

import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from websockets.sync.client import connect  # noqa: E402

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
            try:
                c.close()
            except Exception:
                pass
        self.core.stop()

    def wire_client(self, who):
        c = wire.Client("127.0.0.1", self.core.control_port, who)
        self._wire_clients.append(c)
        c.start()
        return c

    def ws(self):
        c = connect(f"ws://127.0.0.1:{self.core.web_port}/ws", open_timeout=DEADLINE)
        self._ws_clients.append(c)
        return c

    def recv_json(self, c, timeout=DEADLINE):
        return json.loads(c.recv(timeout=timeout))

    def recv_until(self, c, pred, timeout=DEADLINE):
        """Read frames until one satisfies `pred`, or give up. Needed
        because a pip change is pushed as a fresh broadcast, not a diff —
        the message that matters may not be the very next one on the wire
        if two transitions land close together.
        """
        end = time.time() + timeout
        while time.time() < end:
            remaining = max(0.05, end - time.time())
            try:
                msg = self.recv_json(c, timeout=remaining)
            except TimeoutError:
                break
            if pred(msg):
                return msg
        return None


class TestBinding(CoreCase):

    def test_both_ports_are_bound_by_the_time_start_returns(self):
        self.assertNotEqual(self.core.control_port, 0)
        self.assertNotEqual(self.core.web_port, 0)


class TestSixPipsSeeded(CoreCase):

    def test_all_six_process_names_appear_even_before_anyone_connects(self):
        msg = self.recv_json(self.ws())
        self.assertEqual(msg["t"], "pips")
        whos = {p["who"] for p in msg["pips"]}
        self.assertEqual(whos, set(health.PROCESSES))

    def test_a_never_connected_process_is_red(self):
        msg = self.recv_json(self.ws())
        self.assertEqual(pip_colour(msg, "camera"), "red")


class TestConnectAndDisconnect(CoreCase):

    def test_hello_lights_the_pip_green_over_the_websocket(self):
        w = self.ws()
        self.recv_json(w)   # the seed snapshot; camera is still red in it
        self.wire_client("camera")
        msg = self.recv_until(w, lambda m: pip_colour(m, "camera") == "green")
        self.assertIsNotNone(msg, "camera never turned green on the socket")

    def test_disconnect_turns_it_red_again(self):
        w = self.ws()
        self.recv_json(w)
        client = self.wire_client("camera")
        self.assertTrue(client.wait_connected(DEADLINE))
        self.assertIsNotNone(
            self.recv_until(w, lambda m: pip_colour(m, "camera") == "green"))

        client.stop()
        msg = self.recv_until(w, lambda m: pip_colour(m, "camera") == "red")
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
