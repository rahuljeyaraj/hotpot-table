"""Tests for core/web/server.py.

Run from the repo root:

    python -m unittest discover -s python/tests -v

No WebSocket library exists on either side of this on purpose (see the
module docstring in core/web/server.py), so the test client is hand-rolled
too: real loopback sockets, a real HTTP upgrade handshake, real RFC 6455
framing. A mocked WebSocket would only prove the mock was self-consistent.
"""

import base64
import http.client
import json
import os
import socket
import struct
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

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


class _WSClient:
    """A minimal hand-rolled WebSocket client: handshake plus framing,
    nothing else. Mirrors core/web/server.py's own framing so a bug shared
    by both sides is still visible to a real browser — which is the one
    thing that matters here.
    """

    def __init__(self, host, port, path="/ws", timeout=DEADLINE):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self._buf = b""
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self.sock.sendall(req.encode("ascii"))
        head = self._read_until(b"\r\n\r\n")
        self.status_line = head.split(b"\r\n", 1)[0].decode("ascii")
        if " 101 " not in self.status_line:
            raise ConnectionError(f"handshake refused: {self.status_line!r}")

    # -- raw io --------------------------------------------------------

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

    # -- frames ----------------------------------------------------------

    def recv_frame(self):
        head = self._recv_exact(2)
        if head is None:
            return None
        b0, b1 = head
        opcode = b0 & 0x0F
        length = b1 & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8))[0]
        payload = self._recv_exact(length) if length else b""
        return opcode, payload

    def recv_json(self):
        opcode, payload = self.recv_frame()
        assert opcode == 0x1, f"expected a text frame, got opcode {opcode}"
        return json.loads(payload.decode("utf-8"))

    def send_frame(self, opcode, payload=b""):
        # Client->server frames must be masked (RFC 6455 5.1); the server
        # rejects nothing unmasked, it just always expects a mask key here.
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        n = len(payload)
        header = bytes([0x80 | opcode])
        if n < 126:
            header += bytes([0x80 | n])
        elif n < (1 << 16):
            header += bytes([0x80 | 126]) + struct.pack("!H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack("!Q", n)
        self.sock.sendall(header + mask + masked)

    def close(self):
        try:
            self.send_frame(0x8, b"")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


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
            c.close()
        self.srv.stop()

    def ws(self, path="/ws"):
        c = _WSClient("127.0.0.1", self.port, path)
        self._clients.append(c)
        return c


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
        c = self.ws()
        self.assertIn(" 101 ", c.status_line)
        self.assertTrue(wait_for(lambda: len(self.srv.hub) == 1))

    def test_close_frame_removes_it_from_the_hub(self):
        c = self.ws()
        self.assertTrue(wait_for(lambda: len(self.srv.hub) == 1))
        c.close()
        self.assertTrue(wait_for(lambda: len(self.srv.hub) == 0),
                        "connection stayed in the hub after a close frame")

    def test_ping_is_answered_with_pong(self):
        c = self.ws()
        c.send_frame(0x9, b"hello")
        opcode, payload = c.recv_frame()
        self.assertEqual(opcode, 0xA)
        self.assertEqual(payload, b"hello")


class TestOnJoin(ServerCase):
    on_join = staticmethod(lambda: {"t": "seed", "n": 6})

    def test_new_connection_gets_the_seed_message_immediately(self):
        c = self.ws()
        self.assertEqual(c.recv_json(), {"t": "seed", "n": 6})


class TestBroadcast(ServerCase):

    def test_reaches_every_open_connection(self):
        a, b = self.ws(), self.ws()
        self.assertTrue(wait_for(lambda: len(self.srv.hub) == 2))
        n = self.srv.broadcast({"t": "pips", "pips": []})
        self.assertEqual(n, 2)
        self.assertEqual(a.recv_json(), {"t": "pips", "pips": []})
        self.assertEqual(b.recv_json(), {"t": "pips", "pips": []})

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
        c = _WSClient("127.0.0.1", port)
        self.assertTrue(wait_for(lambda: len(srv.hub) == 1))

        srv.stop()

        # The peer socket must observe the close, not merely time out.
        c.sock.settimeout(DEADLINE)
        frame = c.recv_frame()
        self.assertTrue(frame is None or frame[0] == 0x8)

        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=1.0)


if __name__ == "__main__":
    unittest.main()
