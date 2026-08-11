"""The MJPEG HTTP server — `/stream.mjpg`, `/snapshot.jpg`, `/info.json`
(doc section 21, M3 build item 2).

Plain `http.server`, not a framework. The doc's own bias is the cheapest
tool that works — `core/web/server.py` reaches for the `websockets` package
only because hand-rolling RFC 6455 is real work; an MJPEG stream is just
repeated multipart parts over a plain HTTP connection kept open, which the
standard library already does, and adding a dependency to save writing the
one `do_GET` this needs would be the wrong trade.

Push, not poll
--------------
`LatestFrame` is a single-slot mailbox with a `Condition`: `publish()` sets
the newest JPEG and wakes every waiting `/stream.mjpg` handler, each on its
own thread (`ThreadingHTTPServer`). A handler blocks on the condition
between frames rather than polling on a timer, so the stream's rate is
exactly the camera's encode rate (`camera.mjpeg_fps`, applied by whoever
calls `publish()` — this module does not itself rate-limit) with no extra
latency and no busy loop per connected browser tab.

A disconnected client is detected the only reliable way: writing to it and
catching the failure. There is no cheaper signal available, and browsers
tearing down an `<img>`'s connection on tab close/navigate is exactly the
case this exists to handle without leaking a handler thread per stale tab.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional, Tuple

log = logging.getLogger("hotpot.camera.mjpeg")

BOUNDARY = "hotpotframe"

# How long a /stream.mjpg handler waits for the next frame before checking
# whether it should give up (server stopping, client gone). Bounds shutdown
# latency; small enough to feel instant, large enough to cost nothing.
WAIT_TICK = 0.5


class LatestFrame:
    """The newest encoded JPEG, plus the frame_id it came from. One
    producer (camera's capture loop), any number of consumers (one per
    open `/stream.mjpg` connection, plus `/snapshot.jpg` and `/info.json`
    reading it once each)."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._jpeg: Optional[bytes] = None
        self._frame_id: int = -1
        self._ts: float = 0.0

    def publish(self, jpeg: bytes, frame_id: int) -> None:
        with self._cond:
            self._jpeg = jpeg
            self._frame_id = frame_id
            self._ts = time.time()
            self._cond.notify_all()

    def snapshot(self) -> Optional[Tuple[bytes, int, float]]:
        with self._cond:
            if self._jpeg is None:
                return None
            return self._jpeg, self._frame_id, self._ts

    def wait_next(self, after_frame_id: int,
                  timeout: float) -> Optional[Tuple[bytes, int, float]]:
        """Block until a frame newer than `after_frame_id` is published, or
        `timeout` elapses. `-1` (the initial `after_frame_id` a stream
        handler starts with) matches whatever is already latest, so a
        client connecting mid-stream gets a frame immediately rather than
        waiting for the next one."""
        with self._cond:
            if self._frame_id > after_frame_id and self._jpeg is not None:
                return self._jpeg, self._frame_id, self._ts
            self._cond.wait(timeout)
            if self._frame_id > after_frame_id and self._jpeg is not None:
                return self._jpeg, self._frame_id, self._ts
            return None


class MjpegServer:
    """Owns the `ThreadingHTTPServer`; `start()`/`stop()` bracket its life
    the same shape as `wire.Server`, so `camera/main.py` treats it as one
    more thing to start and stop alongside the control link."""

    def __init__(self, host: str, port: int, frame: LatestFrame,
                 get_info: Callable[[], Dict[str, Any]]) -> None:
        self.host = host
        self.port = port
        self._frame = frame
        self._get_info = get_info
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> int:
        handler = _make_handler(self._frame, self._get_info)
        httpd = ThreadingHTTPServer((self.host, self.port), handler)
        httpd.daemon_threads = True
        self._httpd = httpd
        self.port = httpd.server_address[1]
        self._thread = threading.Thread(
            target=httpd.serve_forever, name="camera-mjpeg", daemon=True)
        self._thread.start()
        log.info("camera: MJPEG server listening on %s:%d", self.host, self.port)
        return self.port

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(2.0)
        self._thread = None


def _make_handler(frame: LatestFrame, get_info: Callable[[], Dict[str, Any]]):
    class Handler(BaseHTTPRequestHandler):
        # BaseHTTPRequestHandler logs every request to stderr by default,
        # bypassing common/log.py entirely and defeating the point of its
        # line-buffered, UTF-8, ring-fed setup (doc section 10.2's readiness
        # protocol also lives on stdout and does not need MJPEG access-log
        # noise interleaved with it).
        def log_message(self, fmt: str, *args: Any) -> None:
            log.debug("%s - %s", self.address_string(), fmt % args)

        def do_GET(self) -> None:
            if self.path == "/stream.mjpg":
                self._stream()
            elif self.path == "/snapshot.jpg":
                self._snapshot()
            elif self.path == "/info.json":
                self._info()
            else:
                self.send_error(404)

        def _stream(self) -> None:
            self.send_response(200)
            self.send_header(
                "Content-Type",
                f"multipart/x-mixed-replace; boundary={BOUNDARY}")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            last_id = -1
            try:
                while True:
                    got = frame.wait_next(last_id, WAIT_TICK)
                    if got is None:
                        continue
                    jpeg, last_id, _ts = got
                    self.wfile.write(f"--{BOUNDARY}\r\n".encode("ascii"))
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(
                        f"Content-Length: {len(jpeg)}\r\n\r\n".encode("ascii"))
                    self.wfile.write(jpeg)
                    self.wfile.write(b"\r\n")
            except (BrokenPipeError, ConnectionResetError, OSError):
                # The browser navigated away or closed the tab. Doc section
                # 12.3's <img> tag is the only client; there is nothing to
                # clean up beyond letting this handler thread end.
                pass

        def _snapshot(self) -> None:
            got = frame.snapshot()
            if got is None:
                self.send_error(503, "no frame published yet")
                return
            jpeg, _frame_id, _ts = got
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(jpeg)))
            self.end_headers()
            try:
                self.wfile.write(jpeg)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        def _info(self) -> None:
            body = json.dumps(get_info()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    return Handler
