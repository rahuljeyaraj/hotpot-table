"""Tests for common/wire.py.

Run from the repo root:

    python -m unittest discover -s python/tests -v

Every test here has to be able to fail. Several of them did while this
module was being written, which is the only reason to trust the rest:
the reconnect test failed until the backoff loop stopped penalising a
link that had worked, and the duplicate-name test failed until hello
started evicting the stale connection.

These use real loopback sockets rather than fakes. The thing being tested
is behaviour against a real TCP stack — partial reads, a peer vanishing,
a port coming back — and a fake socket would only test the fake.
"""

import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hotpot.common import wire  # noqa: E402


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


class Collector:
    """Thread-safe sink for messages arriving on a callback thread."""

    def __init__(self):
        self.msgs = []
        self.conns = []
        self.gone = []
        self._lock = threading.Lock()

    def on_message(self, conn, msg):
        with self._lock:
            self.msgs.append((conn.who, msg))

    def on_client_message(self, msg):
        with self._lock:
            self.msgs.append(msg)

    def on_connect(self, conn):
        with self._lock:
            self.conns.append(conn.who)

    def on_disconnect(self, conn, reason):
        with self._lock:
            self.gone.append((conn.who, reason))

    def count(self, t=None):
        with self._lock:
            if t is None:
                return len(self.msgs)
            return sum(1 for m in self.msgs
                       if (m[1] if isinstance(m, tuple) else m).get("t") == t)


# ---------------------------------------------------------------------------
# Framing
# ---------------------------------------------------------------------------

class TestFraming(unittest.TestCase):

    def test_round_trip(self):
        msg = {"t": "state", "seq": 90211, "total": {"amount": 41.2}}
        self.assertEqual(wire.decode(wire.encode(msg).rstrip(b"\n")), msg)

    def test_one_line_per_message(self):
        self.assertEqual(wire.encode({"t": "hb"}).count(b"\n"), 1)

    def test_embedded_newline_is_escaped_not_emitted(self):
        # If a label ever contained a newline, naive framing would split one
        # message into two unparseable halves.
        line = wire.encode({"t": "x", "s": "a\nb"})
        self.assertEqual(line.count(b"\n"), 1)
        self.assertEqual(wire.decode(line.rstrip(b"\n"))["s"], "a\nb")

    def test_chinese_is_not_escaped(self):
        line = wire.encode({"t": "state", "label": "香菇"})
        self.assertIn("香菇".encode("utf-8"), line)
        self.assertEqual(wire.decode(line.rstrip(b"\n"))["label"], "香菇")

    def test_malformed_decodes_to_none(self):
        self.assertIsNone(wire.decode(b"{not json"))
        self.assertIsNone(wire.decode(b"[1,2,3]"))       # not an object
        self.assertIsNone(wire.decode(b"\xff\xfe"))      # not utf-8

    def test_reader_splits_across_chunks(self):
        r = wire.LineReader()
        self.assertEqual(r.feed(b'{"t":"a"'), [])
        self.assertEqual(r.feed(b'}\n{"t":"b"}\n'), [b'{"t":"a"}', b'{"t":"b"}'])

    def test_reader_skips_blank_lines(self):
        r = wire.LineReader()
        self.assertEqual(r.feed(b'\n\n{"t":"a"}\n'), [b'{"t":"a"}'])

    def test_reader_overflows_on_a_line_that_never_ends(self):
        r = wire.LineReader(max_line=64)
        r.feed(b"x" * 128)
        self.assertTrue(r.overflowed)
        self.assertEqual(r.feed(b'{"t":"a"}\n'), [])     # stays shut


# ---------------------------------------------------------------------------
# Server + Client
# ---------------------------------------------------------------------------

class LinkTestCase(unittest.TestCase):
    """Brings up a server on an ephemeral port and tears everything down."""

    def setUp(self):
        self.srv_sink = Collector()
        self.cli_sink = Collector()
        self._clients = []
        self.cfg = {"stage": [1920, 1080], "emit_hz": 60}
        self.server = wire.Server(
            "127.0.0.1", 0,
            on_message=self.srv_sink.on_message,
            on_connect=self.srv_sink.on_connect,
            on_disconnect=self.srv_sink.on_disconnect,
            welcome_cfg=lambda conn, hello: self.cfg,
            name="core")
        self.port = self.server.start()

    def tearDown(self):
        for c in self._clients:
            c.stop()
        self.server.stop()

    def client(self, who="tracker", **kw):
        c = wire.Client("127.0.0.1", self.port, who, **kw)
        self._clients.append(c)
        return c


class TestHandshake(LinkTestCase):

    def test_client_connects_and_is_welcomed_with_config(self):
        c = self.client(on_message=self.cli_sink.on_client_message)
        c.start()
        self.assertTrue(c.wait_connected(DEADLINE), "client never got a welcome")
        self.assertEqual(c.cfg, self.cfg)
        self.assertEqual(self.srv_sink.conns, ["tracker"])

    def test_hello_carries_pid_and_version(self):
        c = self.client()
        c.start()
        self.assertTrue(c.wait_connected(DEADLINE))
        conn = wait_for(lambda: self.server.client("tracker"))
        self.assertEqual(conn.hello["pid"], os.getpid())
        self.assertEqual(conn.hello["ver"], wire.PROTOCOL_VERSION)

    def test_no_welcome_means_not_connected(self):
        """A server that accepts but never welcomes must not look connected.

        This is the honest version of "the link is up": a bare TCP accept
        proves a socket exists, not that core is on the other end of it.
        """
        import socket
        mute = socket.socket()
        mute.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        mute.bind(("127.0.0.1", 0))
        mute.listen(2)
        port = mute.getsockname()[1]
        accepted = []
        threading.Thread(target=lambda: accepted.append(mute.accept()),
                         daemon=True).start()
        c = wire.Client("127.0.0.1", port, "tracker")
        self._clients.append(c)
        c.start()
        try:
            self.assertTrue(wait_for(lambda: accepted, 10.0), "never even accepted")
            self.assertFalse(c.wait_connected(1.0))
            self.assertFalse(c.connected)
            # And nothing may be sent to it. Whatever is on the far end has
            # not identified itself as core, so telemetry aimed at core must
            # not go there.
            self.assertFalse(c.send({"t": "hb"}))
            self.assertEqual(c.stats["dropped_while_down"], 1)
        finally:
            c.stop()
            mute.close()

    def test_second_connection_with_the_same_name_evicts_the_first(self):
        """The crash-restart shape: a process comes back before the OS has
        torn down its old socket. Core must talk to the new one.
        """
        a = self.client("tracker")
        a.start()
        self.assertTrue(a.wait_connected(DEADLINE))
        first = self.server.client("tracker")

        b = self.client("tracker")
        b.start()
        self.assertTrue(b.wait_connected(DEADLINE))

        self.assertTrue(wait_for(lambda: not first.alive), "stale link stayed open")
        live = wait_for(lambda: self.server.client("tracker"))
        self.assertIsNot(live, first)
        self.assertTrue(wait_for(lambda: len(self.server.clients()) == 1))


class TestTraffic(LinkTestCase):

    def test_client_to_server(self):
        c = self.client()
        c.start()
        self.assertTrue(c.wait_connected(DEADLINE))
        self.assertTrue(c.send({"t": "kw", "word": "done", "conf": 0.87}))
        self.assertTrue(wait_for(lambda: self.srv_sink.count("kw") == 1))
        who, msg = self.srv_sink.msgs[0]
        self.assertEqual(who, "tracker")
        self.assertEqual(msg["word"], "done")

    def test_server_to_client(self):
        c = self.client(on_message=self.cli_sink.on_client_message)
        c.start()
        self.assertTrue(c.wait_connected(DEADLINE))
        self.assertTrue(self.server.send_to("tracker", {"t": "cmd", "op": "stop"}))
        self.assertTrue(wait_for(lambda: self.cli_sink.count("cmd") == 1))

    def test_welcome_is_not_delivered_as_an_application_message(self):
        c = self.client(on_message=self.cli_sink.on_client_message)
        c.start()
        self.assertTrue(c.wait_connected(DEADLINE))
        time.sleep(0.2)
        self.assertEqual(self.cli_sink.count("welcome"), 0)

    def test_broadcast_reaches_every_welcomed_client_and_can_be_filtered(self):
        sinks = {}
        for who in ("of", "tracker", "classifier"):
            sinks[who] = Collector()
            c = self.client(who, on_message=sinks[who].on_client_message)
            c.start()
        for c in self._clients:
            self.assertTrue(c.wait_connected(DEADLINE))

        self.assertEqual(self.server.broadcast({"t": "state", "seq": 1}), 3)
        for who in sinks:
            self.assertTrue(wait_for(lambda w=who: sinks[w].count("state") == 1), who)

        self.assertEqual(self.server.broadcast({"t": "evt", "kind": "sound"},
                                               only=["of"]), 1)
        self.assertTrue(wait_for(lambda: sinks["of"].count("evt") == 1))
        time.sleep(0.2)
        self.assertEqual(sinks["tracker"].count("evt"), 0)

    def test_ordering_is_preserved_under_a_burst(self):
        """The 60Hz state stream: nothing may arrive out of order.

        The burst is bounded by the send queue on purpose. A burst deeper
        than the queue is *defined* as a wedged peer (see the drop rule), so
        a bigger one would be exercising the reset path, not this one.
        """
        n = wire.DEFAULT_SEND_QUEUE // 2
        c = self.client(on_message=self.cli_sink.on_client_message)
        c.start()
        self.assertTrue(c.wait_connected(DEADLINE))
        for i in range(n):
            self.assertTrue(self.server.send_to("tracker", {"t": "state", "seq": i}))
        self.assertTrue(wait_for(lambda: self.cli_sink.count("state") == n, 10.0))
        seqs = [m["seq"] for m in self.cli_sink.msgs if m.get("t") == "state"]
        self.assertEqual(seqs, list(range(n)))

    def test_a_malformed_line_is_skipped_and_the_link_survives(self):
        import socket
        s = socket.create_connection(("127.0.0.1", self.port))
        try:
            s.sendall(b'{"t":"hello","who":"tracker","pid":1,"ver":3}\n')
            self.assertTrue(wait_for(lambda: self.server.client("tracker")))
            s.sendall(b'{"t":"kw"  <-- garbage\n')
            s.sendall(b'{"t":"kw","word":"done"}\n')
            self.assertTrue(wait_for(lambda: self.srv_sink.count("kw") == 1))
            conn = self.server.client("tracker")
            self.assertTrue(conn.alive)
            self.assertEqual(conn.stats["malformed"], 1)
        finally:
            s.close()

    def test_a_message_before_hello_is_ignored(self):
        import socket
        s = socket.create_connection(("127.0.0.1", self.port))
        try:
            s.sendall(b'{"t":"kw","word":"done"}\n')
            time.sleep(0.3)
            self.assertEqual(self.srv_sink.count("kw"), 0)
        finally:
            s.close()

    def test_an_oversized_line_resets_the_link(self):
        import socket
        s = socket.create_connection(("127.0.0.1", self.port))
        try:
            s.sendall(b'{"t":"hello","who":"tracker","pid":1,"ver":3}\n')
            conn = wait_for(lambda: self.server.client("tracker"))
            s.sendall(b"x" * (wire.MAX_LINE_BYTES + 1024))
            self.assertTrue(wait_for(lambda: not conn.alive), "link survived a flood")
        finally:
            s.close()


class TestFailure(LinkTestCase):

    def test_send_while_down_is_dropped_not_queued_forever(self):
        c = self.client()
        c.start()
        self.assertTrue(c.wait_connected(DEADLINE))
        self.server.stop()
        self.assertTrue(wait_for(lambda: not c.connected))
        self.assertFalse(c.send({"t": "hb"}))
        self.assertEqual(c.stats["dropped_while_down"], 1)

    def test_client_keeps_retrying_while_core_is_absent(self):
        """The whole point of doc section 3.3: start order does not matter.

        The check is `attempts >= 2`, not `thread.is_alive()`. An earlier
        version asserted only that the thread existed after 1.5s, which
        proved nothing: a refused connect to loopback takes ~2s on Windows,
        so that window expired before the first attempt had even returned,
        and the test passed with the retry loop deleted. Counting a second
        attempt is the smallest observation that can only happen if the loop
        went round.
        """
        import socket
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
        probe.close()

        c = wire.Client("127.0.0.1", dead_port, "tracker")
        self._clients.append(c)
        c.start()
        self.assertTrue(wait_for(lambda: c.attempts >= 2, 20.0),
                        "client stopped retrying: %r" % (c.stats,))
        self.assertFalse(c.connected)
        self.assertTrue(c._thread.is_alive(), "client exited because core was absent")

    def test_client_reconnects_after_core_dies_and_comes_back(self):
        c = self.client(on_connect=lambda cfg: self.cli_sink.conns.append("up"))
        c.start()
        self.assertTrue(c.wait_connected(DEADLINE))
        port = self.port

        self.server.stop()
        self.assertTrue(wait_for(lambda: not c.connected), "client did not notice death")

        self.cfg = {"stage": [1920, 1080], "emit_hz": 30}   # core came back changed
        self.server = wire.Server("127.0.0.1", port,
                                  welcome_cfg=lambda conn, hello: self.cfg,
                                  name="core")
        self.server.start()

        self.assertTrue(c.wait_connected(DEADLINE * 3), "client never came back")
        self.assertEqual(c.cfg["emit_hz"], 30, "stale config after reconnect")
        self.assertEqual(c.connects, 2)
        self.assertTrue(c.send({"t": "hb"}))

    def test_server_sees_a_client_disappear(self):
        c = self.client()
        c.start()
        self.assertTrue(c.wait_connected(DEADLINE))
        c.stop()
        self.assertTrue(wait_for(lambda: self.srv_sink.gone), "no disconnect callback")
        self.assertEqual(self.srv_sink.gone[0][0], "tracker")
        self.assertEqual(self.server.clients(), [])

    def test_a_peer_that_stops_reading_gets_its_link_reset(self):
        """The drop rule. A wedged peer must not be able to wedge core.

        The client here never reads: it completes the handshake by hand and
        then goes silent, so core's outbound queue fills.

        The assertion is on the *reason*, not just on the link dying. An
        earlier version of this test checked only `not conn.alive` and passed
        even with the queue rule deleted, because the socket's own send
        timeout was quietly killing the link first. A check that cannot tell
        which mechanism fired is not a check on either of them.
        """
        import socket
        s = socket.create_connection(("127.0.0.1", self.port))
        try:
            s.sendall(b'{"t":"hello","who":"of","pid":1,"ver":3}\n')
            conn = wait_for(lambda: self.server.client("of"))
            self.assertTrue(conn)
            blob = "x" * 4096
            sent = 0
            end = time.time() + 30.0
            while conn.alive and time.time() < end:
                conn.send({"t": "state", "seq": sent, "pad": blob})
                sent += 1
            self.assertFalse(conn.alive, "core kept queueing for a dead reader")
            self.assertIn("overflow", conn.stats["reason"],
                          "the link died, but not of the queue rule: "
                          + conn.stats["reason"])
            self.assertGreater(conn.stats["dropped"], 0)
        finally:
            s.close()

    def test_send_queue_trips_before_the_socket_timeout_does(self):
        """The sizing of DEFAULT_SEND_QUEUE, checked rather than asserted.

        At the 60Hz state rate the queue must be the mechanism that notices a
        wedged peer. If SEND_TIMEOUT were the faster of the two, the tolerance
        would stop being the documented queue depth and start being whatever
        the kernel's socket buffers happen to be on the deploy board.
        """
        self.assertLess(wire.DEFAULT_SEND_QUEUE / 60.0, wire.SEND_TIMEOUT)

    def test_a_handler_that_raises_does_not_kill_the_link(self):
        def boom(conn, msg):
            raise RuntimeError("handler bug")

        self.server.stop()
        self.server = wire.Server("127.0.0.1", 0, on_message=boom,
                                  welcome_cfg=lambda c, h: {}, name="core")
        self.port = self.server.start()
        c = self.client()
        c.start()
        self.assertTrue(c.wait_connected(DEADLINE))
        c.send({"t": "kw", "word": "done"})
        time.sleep(0.3)
        self.assertTrue(self.server.client("tracker").alive)
        self.assertTrue(c.send({"t": "kw", "word": "again"}))


class TestShutdown(unittest.TestCase):

    def test_stop_leaves_no_threads_and_frees_the_port(self):
        """An orphan holding a port is the failure M0 exists to prevent."""
        import socket
        before = {t.name for t in threading.enumerate()}
        server = wire.Server("127.0.0.1", 0, welcome_cfg=lambda c, h: {})
        port = server.start()
        c = wire.Client("127.0.0.1", port, "tracker")
        c.start()
        self.assertTrue(c.wait_connected(DEADLINE))

        c.stop()
        server.stop()

        self.assertTrue(wait_for(
            lambda: not [t for t in threading.enumerate()
                         if t.name not in before and t.name.startswith("wire-")]),
            "wire threads outlived stop(): "
            + repr([t.name for t in threading.enumerate() if t.name.startswith("wire-")]))

        rebind = socket.socket()
        try:
            rebind.bind(("127.0.0.1", port))     # raises if the port is still held
        finally:
            rebind.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
