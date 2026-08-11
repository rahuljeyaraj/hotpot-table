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
import threading
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
    on_message = None

    def setUp(self):
        self.srv = web.Server("127.0.0.1", 0, STATIC_ROOT,
                              on_join=type(self).on_join,
                              on_message=type(self).on_message)
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


class TestOnJoinReturningAList(ServerCase):
    """M2.6 build item 7: one seed message stopped being enough once a
    tablet needed both the pips and the current mode on arrival. The
    single-object case above must keep working unchanged — that is the
    whole reason this is a list check rather than a signature change.
    """

    on_join = staticmethod(lambda: [{"t": "pips", "pips": []},
                                    {"t": "mode", "mode": "serving"}])

    def test_every_message_in_the_list_is_sent_in_order(self):
        c = self.ws()
        self.assertEqual(self.recv_json(c), {"t": "pips", "pips": []})
        self.assertEqual(self.recv_json(c), {"t": "mode", "mode": "serving"})


class TestOnJoinReturningAnEmptyList(ServerCase):
    """A list is unpacked, so an empty one sends nothing at all rather
    than a literal "[]" frame the tablet would have to parse and ignore.
    Proven by the connection still being usable afterwards.
    """

    on_join = staticmethod(lambda: [])

    def test_sends_nothing_and_the_connection_still_works(self):
        c = self.ws()
        self.assertTrue(wait_for(lambda: len(self.srv.hub) == 1))
        self.srv.broadcast({"t": "later"})
        self.assertEqual(self.recv_json(c), {"t": "later"})


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


class TestOnMessage(ServerCase):
    """doc section 12.8: the developer panel's mock controls are the first
    thing the staff view ever sends core. Before this, incoming frames
    were discarded outright (M0 through M1 build item 4's docstring said
    so directly) — these confirm the replacement plumbing, not core's
    reaction to any particular message (that's test_core_main.py's job).
    """

    def setUp(self):
        self.received = []
        self.lock = threading.Lock()

        def on_message(msg):
            with self.lock:
                self.received.append(msg)

        type(self).on_message = staticmethod(on_message)
        super().setUp()

    def _wait_received(self, n=1, timeout=DEADLINE):
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                if len(self.received) >= n:
                    return list(self.received)
            time.sleep(0.01)
        return list(self.received)

    def test_a_json_object_frame_reaches_the_callback(self):
        c = self.ws()
        c.send(json.dumps({"t": "mock_pick", "bin": 3, "grams": 45}))
        got = self._wait_received(1)
        self.assertEqual(got, [{"t": "mock_pick", "bin": 3, "grams": 45}])

    def test_unparseable_frame_is_dropped_not_raised(self):
        """The connection must survive a garbled frame — a hostile or
        buggy tablet must not take core's staff view down for everyone
        else attached to it (wire.py's decode() sets the same bar for the
        sibling-process link).
        """
        c = self.ws()
        c.send("not json")
        c.send(json.dumps({"t": "ok"}))
        got = self._wait_received(1)
        self.assertEqual(got, [{"t": "ok"}])

    def test_non_object_frame_is_dropped_not_raised(self):
        c = self.ws()
        c.send(json.dumps([1, 2, 3]))
        c.send(json.dumps({"t": "ok"}))
        got = self._wait_received(1)
        self.assertEqual(got, [{"t": "ok"}])


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
