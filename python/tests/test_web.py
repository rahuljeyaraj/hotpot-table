"""Tests for core/web/server.py.

Run from the repo root (after `pip install -r python/requirements.txt`):

    python -m unittest discover -s python/tests -v

The WebSocket half uses the real `websockets` client library rather than
hand-rolled framing — the whole point of building server.py on top of it
instead of reimplementing RFC 6455 is that both sides get to be real.
"""

import http.client
import json
import os
import socket
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from websockets.exceptions import ConnectionClosed  # noqa: E402
from websockets.sync.client import connect  # noqa: E402

from hotpot.core.web import server as web  # noqa: E402

DEADLINE = 5.0
STATIC_ROOT = os.path.join(os.path.dirname(web.__file__), "static")


def wait_for(pred, timeout=DEADLINE, tick=0.01):
    """Poll until pred() is truthy. Returns the value, or False on timeout."""
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(tick)
    return False


class ServerCase(unittest.TestCase):
    """A real web.Server on an ephemeral loopback port, torn down after."""

    on_join = None

    def setUp(self):
        self.srv = web.Server("127.0.0.1", 0, STATIC_ROOT,
                              on_join=type(self).on_join)
        self.port = self.srv.start()
        self._clients = []

    def tearDown(self):
        for c in self._clients:
            try:
                c.close()
            except Exception:
                pass
        self.srv.stop()

    def ws(self):
        c = connect(f"ws://127.0.0.1:{self.port}/ws", open_timeout=DEADLINE)
        self._clients.append(c)
        return c

    def recv_json(self, c, timeout=DEADLINE):
        return json.loads(c.recv(timeout=timeout))


# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------

class TestStatic(ServerCase):

    def test_serves_index_at_root(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=DEADLINE)
        conn.request("GET", "/")
        resp = conn.getresponse()
        body = resp.read()
        self.assertEqual(resp.status, 200)
        self.assertIn(b"Hot Pot", body)
        conn.close()

    def test_missing_file_is_404(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=DEADLINE)
        conn.request("GET", "/does-not-exist.txt")
        resp = conn.getresponse()
        resp.read()
        self.assertEqual(resp.status, 404)
        conn.close()

    def test_path_traversal_is_refused(self):
        """The static root is `core/web/static/`; `server.py` lives one
        directory up. If `..` ever escaped, this would read the source
        file straight off the disk of a machine whose staff view is
        reachable from a hotel Wi-Fi (doc section 12.1's tablet).
        """
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=DEADLINE)
        conn.request("GET", "/../server.py")
        resp = conn.getresponse()
        resp.read()
        self.assertEqual(resp.status, 403)
        conn.close()


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

class TestHandshake(ServerCase):

    def test_upgrades_and_connects(self):
        self.ws()
        self.assertTrue(wait_for(lambda: len(self.srv.hub) == 1))

    def test_close_removes_it_from_the_hub(self):
        c = self.ws()
        self.assertTrue(wait_for(lambda: len(self.srv.hub) == 1))
        c.close()
        self.assertTrue(wait_for(lambda: len(self.srv.hub) == 0),
                        "connection stayed in the hub after close()")

    def test_ping_gets_a_pong(self):
        """The library answers pings at the protocol layer, before our
        handler ever sees them — this just checks process_request letting
        `/ws` through does not somehow break that.
        """
        c = self.ws()
        pong_received = c.ping()
        self.assertTrue(pong_received.wait(DEADLINE))


class TestOnJoin(ServerCase):
    on_join = staticmethod(lambda: {"t": "seed", "n": 6})

    def test_new_connection_gets_the_seed_message_immediately(self):
        c = self.ws()
        self.assertEqual(self.recv_json(c), {"t": "seed", "n": 6})


class TestBroadcast(ServerCase):

    def test_reaches_every_open_connection(self):
        a, b = self.ws(), self.ws()
        self.assertTrue(wait_for(lambda: len(self.srv.hub) == 2))
        n = self.srv.broadcast({"t": "pips", "pips": []})
        self.assertEqual(n, 2)
        self.assertEqual(self.recv_json(a), {"t": "pips", "pips": []})
        self.assertEqual(self.recv_json(b), {"t": "pips", "pips": []})

    def test_does_not_count_a_connection_that_already_closed(self):
        a = self.ws()
        self.assertTrue(wait_for(lambda: len(self.srv.hub) == 1))
        a.close()
        self.assertTrue(wait_for(lambda: len(self.srv.hub) == 0))
        n = self.srv.broadcast({"t": "noop"})
        self.assertEqual(n, 0)


class TestStop(unittest.TestCase):

    def test_stop_closes_open_connections_and_the_listener(self):
        srv = web.Server("127.0.0.1", 0, STATIC_ROOT)
        port = srv.start()
        c = connect(f"ws://127.0.0.1:{port}/ws", open_timeout=DEADLINE)
        self.assertTrue(wait_for(lambda: len(srv.hub) == 1))

        srv.stop()

        with self.assertRaises(ConnectionClosed):
            c.recv(timeout=DEADLINE)

        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=1.0)


if __name__ == "__main__":
    unittest.main()
