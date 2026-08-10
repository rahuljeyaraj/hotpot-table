"""HTTP + WebSocket for the staff view (doc section 12, `core.web_port`).

M0 scope only: serve the static SPA shell and push whatever core hands it
over a WebSocket. Everything else in doc section 12 — tabs, the MJPEG
feed, bin cards, calibration wizards — arrives with the milestone that
has something real to show there. This file does not know what a pip or
a bin is; `core/main.py` decides what goes down the socket.

Stdlib only, on purpose. Doc section 4 argues against a new build
dependency for the control transport because the deploy machine's 64 GB
eMMC and "2am before judging" failure mode punish every added package —
that argument does not stop applying just because this half of the
system is Python. A WebSocket server is a ~60-line handshake plus framing
(RFC 6455); that is cheaper than explaining why aiohttp or websockets is
vendored, and it keeps `python -m unittest discover` dependency-free.

Threading model
----------------
`http.server.ThreadingHTTPServer`: one thread per accepted connection. A
static GET returns its thread in microseconds. A WebSocket upgrade keeps
its thread for the connection's whole life, blocked in `read_loop()`
reading control frames — ping answered with pong, close ends the loop.
The staff view is normally one tablet, occasionally two during setup, so
a thread per socket costs nothing here; this is not the 60Hz control link
in wire.py and does not need that file's queue-and-backoff machinery.

Pushing to clients happens off that thread entirely: `Server.broadcast`
is called from whatever thread produced the change (in M0, a
health.Registry status transition) and fans one pre-encoded frame out to
every connection the Hub currently holds.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import logging
import socket
import struct
import threading
from pathlib import Path
from typing import Any, Callable, Optional, Set, Tuple

log = logging.getLogger("hotpot.web")

# RFC 6455 section 1.3 — fixed, not a secret, just the spec's handshake salt.
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

_OP_TEXT = 0x1
_OP_CLOSE = 0x8
_OP_PING = 0x9
_OP_PONG = 0xA

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}


# ---------------------------------------------------------------------------
# RFC 6455 framing — just enough to send text and answer ping/close
# ---------------------------------------------------------------------------

def _accept_key(client_key: str) -> str:
    digest = hashlib.sha1((client_key + _WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def _encode_frame(opcode: int, payload: bytes) -> bytes:
    """A server->client frame. Never masked — RFC 6455 5.1 reserves masking
    for client->server; a masked server frame is itself a protocol error.
    """
    header = bytes([0x80 | opcode])
    n = len(payload)
    if n < 126:
        header += bytes([n])
    elif n < (1 << 16):
        header += bytes([126]) + struct.pack("!H", n)
    else:
        header += bytes([127]) + struct.pack("!Q", n)
    return header + payload


def _recv_exact(sock: socket.socket, n: int) -> Optional[bytes]:
    """`n` bytes or None if the peer went away before delivering them."""
    if n == 0:
        return b""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def _read_frame(sock: socket.socket) -> Optional[Tuple[int, bytes]]:
    """One client->server frame, unmasked. None on EOF or a broken frame.

    No fragmentation support (opcode 0x0 continuation) — the staff view
    sends nothing but pings and its final close in M0, and refusing to
    reassemble a message this server never asked for is simpler than
    silently mishandling one.
    """
    head = _recv_exact(sock, 2)
    if head is None:
        return None
    b0, b1 = head
    opcode = b0 & 0x0F
    masked = bool(b1 & 0x80)
    length = b1 & 0x7F
    if length == 126:
        ext = _recv_exact(sock, 2)
        if ext is None:
            return None
        length = struct.unpack("!H", ext)[0]
    elif length == 127:
        ext = _recv_exact(sock, 8)
        if ext is None:
            return None
        length = struct.unpack("!Q", ext)[0]
    mask_key = b""
    if masked:
        mask_key = _recv_exact(sock, 4)
        if mask_key is None:
            return None
    payload = _recv_exact(sock, length)
    if payload is None:
        return None
    if masked:
        payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    return opcode, payload


# ---------------------------------------------------------------------------
# One upgraded connection
# ---------------------------------------------------------------------------

class WSConnection:
    """One accepted WebSocket, from the 101 response onward.

    `close()` shuts the socket down (not just closes it) before closing it,
    the same two-step wire.py's `_Link.close` uses: a bare close() on one
    thread while another is blocked in recv() on the same fd is not
    guaranteed to wake that recv() on every platform, shutdown() is.
    """

    def __init__(self, sock: socket.socket, peer: str) -> None:
        self._sock = sock
        self.peer = peer
        self._send_lock = threading.Lock()
        self._closed = threading.Event()

    @property
    def alive(self) -> bool:
        return not self._closed.is_set()

    def send_text(self, payload: bytes) -> bool:
        """One text frame. False if the connection was already gone."""
        if self._closed.is_set():
            return False
        try:
            with self._send_lock:
                self._sock.sendall(_encode_frame(_OP_TEXT, payload))
            return True
        except OSError:
            self.close()
            return False

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        try:
            with self._send_lock:
                self._sock.sendall(_encode_frame(_OP_CLOSE, b""))
        except OSError:
            pass
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass

    def read_loop(self) -> None:
        """Block until the peer closes. Runs on the handler's own thread.

        Every data frame is read and discarded — the pip stream is one-way
        in M0, core has nothing to receive. Ping is answered with pong so
        an idle tablet's browser does not time the link out on its own.
        """
        try:
            while self.alive:
                frame = _read_frame(self._sock)
                if frame is None:
                    break
                opcode, payload = frame
                if opcode == _OP_CLOSE:
                    break
                if opcode == _OP_PING:
                    try:
                        with self._send_lock:
                            self._sock.sendall(_encode_frame(_OP_PONG, payload))
                    except OSError:
                        break
        except OSError:
            pass
        finally:
            self.close()


# ---------------------------------------------------------------------------
# Every live connection, and the one thing done to all of them at once
# ---------------------------------------------------------------------------

class Hub:
    """Every live WebSocket. Thread-safe: joins arrive on accept threads,
    broadcasts arrive from application code on whatever thread noticed a
    change worth sending.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._conns: Set[WSConnection] = set()

    def add(self, conn: WSConnection) -> None:
        with self._lock:
            self._conns.add(conn)
        log.info("web: staff view connected (%s), %d now attached",
                  conn.peer, len(self._conns))

    def remove(self, conn: WSConnection) -> None:
        with self._lock:
            self._conns.discard(conn)
        log.info("web: staff view disconnected (%s), %d left",
                  conn.peer, len(self._conns))

    def broadcast(self, obj: Any) -> int:
        """Send one JSON object to every attached connection. Returns how
        many took it. Encoding once and sending the same bytes everywhere
        is why this takes an object rather than a pre-built connection loop
        at the call site.
        """
        payload = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        n = 0
        for conn in self._snapshot():
            if conn.send_text(payload):
                n += 1
        return n

    def _snapshot(self):
        with self._lock:
            return list(self._conns)

    def __len__(self) -> int:
        with self._lock:
            return len(self._conns)


# ---------------------------------------------------------------------------
# HTTP handler — static files, or an upgrade to WebSocket
# ---------------------------------------------------------------------------

class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = "hotpot-core/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        log.debug("%s - %s", self.address_string(), fmt % args)

    def do_GET(self) -> None:
        if self.headers.get("Upgrade", "").lower() == "websocket":
            self._do_upgrade()
        else:
            self._serve_static()

    def _do_upgrade(self) -> None:
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            self.send_error(400, "missing Sec-WebSocket-Key")
            return
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", _accept_key(key))
        self.end_headers()

        hub: Hub = self.server.hub               # type: ignore[attr-defined]
        on_join = self.server.on_join             # type: ignore[attr-defined]
        conn = WSConnection(self.connection, self.client_address[0])
        hub.add(conn)
        try:
            if on_join is not None:
                conn.send_text(json.dumps(on_join(), separators=(",", ":"),
                                          ensure_ascii=False).encode("utf-8"))
            conn.read_loop()
        finally:
            hub.remove(conn)
        # Tell BaseHTTPRequestHandler the connection is spent — it has
        # already been handed to WSConnection and shut down there.
        self.close_connection = True

    def _serve_static(self) -> None:
        root: Path = self.server.static_root      # type: ignore[attr-defined]
        rel = (self.path.split("?", 1)[0].split("#", 1)[0]).lstrip("/") or "index.html"
        try:
            path = (root / rel).resolve().relative_to(root)
        except ValueError:
            self.send_error(403, "outside the static root")
            return
        full = root / path
        if not full.is_file():
            self.send_error(404, "not found")
            return
        data = full.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", _CONTENT_TYPES.get(full.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


class _HTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------

class Server:
    """The staff view's one listener: HTTP for the SPA, WebSocket for push.

    Binding happens in `start()`, not construction — the same split
    wire.Server uses, and for the same reason: building one of these
    should never have the side effect of grabbing a port, which matters
    to a test that constructs one and never starts it.

    `on_join`, if given, is called with no arguments for every new
    WebSocket connection and its return value is sent immediately — so a
    tablet that opens the page a minute into the run sees the current pip
    row at once rather than waiting for the next transition.
    """

    def __init__(self, host: str, port: int, static_root: Path, *,
                 on_join: Optional[Callable[[], Any]] = None) -> None:
        self.host = host
        self._port = port
        self._static_root = Path(static_root)
        self._on_join = on_join
        self.hub = Hub()
        self._httpd: Optional[_HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def port(self) -> int:
        return self._port if self._httpd is None else self._httpd.server_address[1]

    def start(self) -> int:
        httpd = _HTTPServer((self.host, self._port), _Handler)
        httpd.static_root = self._static_root.resolve()   # type: ignore[attr-defined]
        httpd.hub = self.hub                                # type: ignore[attr-defined]
        httpd.on_join = self._on_join                        # type: ignore[attr-defined]
        self._httpd = httpd
        self._thread = threading.Thread(
            target=httpd.serve_forever, name="web-server", daemon=True)
        self._thread.start()
        log.info("web: staff view listening on http://%s:%d",
                  httpd.server_address[0], self.port)
        return self.port

    def broadcast(self, obj: Any) -> int:
        return self.hub.broadcast(obj)

    def stop(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        for conn in self.hub._snapshot():
            conn.close()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(2.0)
        self._httpd.server_close()
        self._httpd = None
