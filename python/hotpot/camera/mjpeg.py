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
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from hotpot.camera.capture import CameraError

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
                 get_info: Callable[[], Dict[str, Any]],
                 get_controls: Callable[[], List[Dict[str, Any]]],
                 set_control: Callable[[str, Optional[bool], Optional[int]],
                                       Dict[str, Any]]) -> None:
        self.host = host
        self.port = port
        self._frame = frame
        self._get_info = get_info
        self._get_controls = get_controls
        self._set_control = set_control
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> int:
        handler = _make_handler(self._frame, self._get_info,
                                self._get_controls, self._set_control)
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


def _make_handler(frame: LatestFrame, get_info: Callable[[], Dict[str, Any]],
                  get_controls: Callable[[], List[Dict[str, Any]]],
                  set_control: Callable[[str, Optional[bool], Optional[int]],
                                        Dict[str, Any]]):
    class Handler(BaseHTTPRequestHandler):
        # BaseHTTPRequestHandler logs every request to stderr by default,
        # bypassing common/log.py entirely and defeating the point of its
        # line-buffered, UTF-8, ring-fed setup (doc section 10.2's readiness
        # protocol also lives on stdout and does not need MJPEG access-log
        # noise interleaved with it).
        def log_message(self, fmt: str, *args: Any) -> None:
            log.debug("%s - %s", self.address_string(), fmt % args)

        def do_GET(self) -> None:
            # BUG, found 2026-08-12 running against a real browser for the
            # first time (M3.3 was never observed against real hardware
            # until then): `self.path` is the raw request target,
            # `/stream.mjpg?t=...`, and index.html's own retry logic
            # (`loadLiveImg()`) always appends that cache-busting query
            # string. An exact-match `==` against the bare path 404s on
            # literally every load, which is indistinguishable from
            # "camera offline" to the <img>'s onerror handler — the stream
            # worked from the first request, this dispatch never accepted
            # one. `/snapshot.jpg` and `/info.json` are not fetched with a
            # query string anywhere in this repo today, but the same bug
            # is latent for either the moment one is.
            path = urlsplit(self.path).path
            if path == "/stream.mjpg":
                self._stream()
            elif path == "/snapshot.jpg":
                self._snapshot()
            elif path == "/info.json":
                self._info()
            elif path == "/controls.json":
                self._controls()
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            if path == "/control":
                self._control()
            else:
                self.send_error(404)

        def do_OPTIONS(self) -> None:
            # The dev panel's POST /control sends a JSON body with an
            # explicit Content-Type header, which is not one of the CORS
            # "simple request" content types — the browser sends a
            # preflight OPTIONS here first and never even attempts the POST
            # if this doesn't answer with the right Access-Control-* trio.
            # Without this handler BaseHTTPRequestHandler 501s the
            # preflight and every slider/button in the panel fails with a
            # bare "Failed to fetch", found 2026-08-13 clicking through the
            # Developer tab in a real browser for the first time.
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

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
            # Unlike /stream.mjpg's <img> tag, the developer panel (M3
            # build item 4) reaches this with `fetch()` from the staff
            # view's own origin (core's :8090) to camera's :8081 — a
            # cross-origin request a browser blocks without this header,
            # where an <img> load is exempt. No credentials flow here, so
            # a wildcard origin costs nothing.
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        def _controls(self) -> None:
            """`/controls.json` — doc §12.8's new dev-panel card: every
            control the open backend exposes, spec and live state already
            merged into plain dicts by `CameraProcess.controls_snapshot()`
            (this module has no reason to know `capture.py`'s dataclass
            shapes). Same cross-origin reasoning as `/info.json` above."""
            self._send_json(200, {"controls": get_controls()})

        def _control(self) -> None:
            """`POST /control` — `{"name", "auto", "value"}`, either or
            both of the last two omitted/`null`. A bad body or an
            unsupported/unknown control name is a 400 with a reason, not a
            500 — this is a developer clicking a slider, not a wire
            protocol that has to survive malformed input from anywhere
            else."""
            length = int(self.headers.get("Content-Length", 0) or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._send_json(400, {"error": "invalid JSON body"})
                return
            name = body.get("name")
            if not isinstance(name, str) or not name:
                self._send_json(400, {"error": "\"name\" is required"})
                return
            auto = body.get("auto")
            if auto is not None and not isinstance(auto, bool):
                self._send_json(
                    400, {"error": "\"auto\" must be a boolean or omitted"})
                return
            value = body.get("value")
            if value is not None and not isinstance(value, (int, float)):
                self._send_json(
                    400, {"error": "\"value\" must be a number or omitted"})
                return
            try:
                result = set_control(name, auto,
                                     int(value) if value is not None else None)
            except CameraError as e:
                self._send_json(400, {"error": str(e)})
                return
            self._send_json(200, result)

        def _send_json(self, code: int, obj: Dict[str, Any]) -> None:
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    return Handler
