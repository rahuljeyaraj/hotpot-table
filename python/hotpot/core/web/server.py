"""HTTP + WebSocket for the staff view (doc section 12, `core.web_port`).

M0 scope only: serve the static SPA shell and push whatever core hands it
over a WebSocket. Everything else in doc section 12 — tabs, the MJPEG
feed, bin cards, calibration wizards — arrives with the milestone that
has something real to show there. This file does not know what a pip or
a bin is; `core/main.py` decides what goes down the socket.

Built on the `websockets` package (see `python/requirements.txt`) rather
than a hand-rolled RFC 6455 implementation: a maintained library beats
reimplementing a wire protocol, full stop. Specifically
`websockets.sync.server` — the *sync* flavour, not the more commonly
documented asyncio one — because the rest of this codebase is threaded
(wire.py, health.py) and the sync API matches that model directly: one
thread per connection, and `ServerConnection.send()` is documented as
safe to call from any thread, which is exactly what's needed to push a
pip change from `health.Registry`'s callback thread.

Static files and the WebSocket share one port (doc section 4.1:
`core.web_port` is "HTTP + WebSocket") via `process_request`: a plain GET
is answered directly with the file's bytes there, ending the connection;
`/ws` returns `None` and lets the library's own opening handshake
continue. That hook is standard `websockets` API, not a workaround.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response
from websockets.sync.server import Server as _WSServer
from websockets.sync.server import ServerConnection, serve

log = logging.getLogger("hotpot.web")

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}


# ---------------------------------------------------------------------------
# Every live connection, and the one thing done to all of them at once
# ---------------------------------------------------------------------------

class Hub:
    """Every live WebSocket. Thread-safe: joins arrive on the library's own
    per-connection accept threads, broadcasts arrive from application code
    on whatever thread noticed a change worth sending.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # who -> cached remote_address, not a bare set: remove() logs the
        # peer, and by the time it runs the socket may already be closed
        # (stop() closes connections from a different thread than the one
        # blocked reading them), which makes conn.remote_address raise
        # instead of returning a value. The address is read once, while
        # the connection is definitely still open, right after add().
        self._conns: Dict[ServerConnection, str] = {}

    def add(self, conn: ServerConnection) -> None:
        peer = str(conn.remote_address)
        with self._lock:
            self._conns[conn] = peer
        log.info("web: staff view connected (%s), %d now attached", peer, len(self._conns))

    def remove(self, conn: ServerConnection) -> None:
        with self._lock:
            peer = self._conns.pop(conn, "?")
        log.info("web: staff view disconnected (%s), %d left", peer, len(self._conns))

    def broadcast(self, obj: Any) -> int:
        """Send one JSON object to every attached connection. Returns how
        many took it. Encoding once and sending the same text everywhere
        is why this takes an object rather than a per-connection loop at
        the call site.
        """
        payload = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
        n = 0
        for conn in self._snapshot():
            try:
                conn.send(payload)
                n += 1
            except ConnectionClosed:
                pass   # it will be removed once its handler thread notices
        return n

    def _snapshot(self):
        with self._lock:
            return list(self._conns)

    def __len__(self) -> int:
        with self._lock:
            return len(self._conns)


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
        self._server: Optional[_WSServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def port(self) -> int:
        return self._port if self._server is None else self._server.socket.getsockname()[1]

    def start(self) -> int:
        self._server = serve(
            self._handle, self.host, self._port,
            process_request=self._process_request,
            server_header="hotpot-core",
        )
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="web-server", daemon=True)
        self._thread.start()
        log.info("web: staff view listening on http://%s:%d",
                  *self._server.socket.getsockname())
        return self.port

    def broadcast(self, obj: Any) -> int:
        return self.hub.broadcast(obj)

    def stop(self) -> None:
        if self._server is None:
            return
        # shutdown() only stops accepting new connections (websockets.sync
        # keeps per-connection threads non-daemon, deliberately, so they
        # are never torn down mid-write) — every open one has to be closed
        # by hand or this hangs waiting for a tablet that is still open.
        self._server.shutdown()
        for conn in self.hub._snapshot():
            conn.close()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(2.0)
        self._server = None

    # -- websockets callbacks ---------------------------------------------

    def _process_request(self, connection: ServerConnection,
                          request: Request) -> Optional[Response]:
        path = request.path.split("?", 1)[0]
        if path == "/ws":
            return None                          # continue the WS handshake
        return self._serve_static(path)

    def _serve_static(self, path: str) -> Response:
        root = self._static_root.resolve()
        rel = path.lstrip("/") or "index.html"
        try:
            resolved = (root / rel).resolve().relative_to(root)
        except ValueError:
            return Response(403, "Forbidden", Headers(), b"outside the static root")
        full = root / resolved
        if not full.is_file():
            return Response(404, "Not Found", Headers(), b"not found")
        data = full.read_bytes()
        headers = Headers([
            ("Content-Type", _CONTENT_TYPES.get(full.suffix, "application/octet-stream")),
            ("Content-Length", str(len(data))),
            ("Cache-Control", "no-store"),
        ])
        return Response(200, "OK", headers, data)

    def _handle(self, connection: ServerConnection) -> None:
        self.hub.add(connection)
        try:
            if self._on_join is not None:
                connection.send(json.dumps(self._on_join(), separators=(",", ":"),
                                           ensure_ascii=False))
            for _ in connection:
                pass   # nothing expected from the staff view in M0; discard
        except ConnectionClosed:
            pass
        finally:
            self.hub.remove(connection)
