"""Tests for camera/mjpeg.py — M3 build item 2's `/stream.mjpg`,
`/snapshot.jpg`, `/info.json` (doc section 21).

Run from the repo root:

    python -m unittest discover -s python/tests -v

A real `MjpegServer` on an ephemeral port, hit with real HTTP requests —
the multipart framing and the "block until a client disconnects" behaviour
are exactly the kind of thing a mock would get right by construction and a
real socket would not (the same reasoning `test_stub.py` gives for using a
real `wire.Server`).
"""

import json
import os
import socket
import sys
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hotpot.camera import mjpeg  # noqa: E402
from hotpot.camera.capture import CameraError  # noqa: E402

DEADLINE = 5.0


def wait_for(pred, timeout=DEADLINE, tick=0.01):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(tick)
    return False


# ---------------------------------------------------------------------------
# LatestFrame
# ---------------------------------------------------------------------------

class TestLatestFrame(unittest.TestCase):

    def test_snapshot_is_none_before_any_publish(self):
        f = mjpeg.LatestFrame()
        self.assertIsNone(f.snapshot())

    def test_publish_then_snapshot(self):
        f = mjpeg.LatestFrame()
        f.publish(b"jpegbytes", frame_id=7)
        jpeg, frame_id, ts = f.snapshot()
        self.assertEqual(jpeg, b"jpegbytes")
        self.assertEqual(frame_id, 7)
        self.assertGreater(ts, 0)

    def test_wait_next_returns_immediately_if_already_newer(self):
        f = mjpeg.LatestFrame()
        f.publish(b"first", frame_id=0)
        got = f.wait_next(after_frame_id=-1, timeout=1.0)
        self.assertEqual(got[0], b"first")

    def test_wait_next_times_out_with_nothing_newer(self):
        f = mjpeg.LatestFrame()
        f.publish(b"first", frame_id=0)
        self.assertIsNone(f.wait_next(after_frame_id=0, timeout=0.1))

    def test_wait_next_wakes_on_a_later_publish(self):
        f = mjpeg.LatestFrame()
        f.publish(b"first", frame_id=0)
        result = {}

        import threading

        def waiter():
            result["got"] = f.wait_next(after_frame_id=0, timeout=2.0)

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.05)
        f.publish(b"second", frame_id=1)
        t.join(2.0)
        self.assertEqual(result["got"][0], b"second")


# ---------------------------------------------------------------------------
# MjpegServer over real HTTP
# ---------------------------------------------------------------------------

class ServerCase(unittest.TestCase):

    def setUp(self):
        self.frame = mjpeg.LatestFrame()
        self.info = {"width": 640, "height": 480, "frame_id": -1}
        self.controls = [{"name": "white_balance", "auto": True, "value": 4600}]
        self.set_control_calls = []
        self._raise = None   # a test sets this to make _set_control fail
        self.server = mjpeg.MjpegServer(
            "127.0.0.1", 0, self.frame, lambda: self.info,
            lambda: self.controls, self._set_control)
        self.port = self.server.start()
        self.addCleanup(self.server.stop)

    def _set_control(self, name, auto, value):
        self.set_control_calls.append((name, auto, value))
        if self._raise is not None:
            raise self._raise
        return {"name": name, "auto": auto if auto is not None else True,
                "value": value if value is not None else 4600}

    def get(self, path):
        return urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}{path}", timeout=DEADLINE)

    def post(self, path, body):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", method="POST",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        return urllib.request.urlopen(req, timeout=DEADLINE)

    def raw_get(self, path, per_read_timeout=1.0, max_bytes=4096):
        """For `/stream.mjpg`: an open-ended response with no overall
        Content-Length, so `http.client`'s length-bounded `read(N)` either
        blocks past whatever was actually sent or under-reads a burst.
        Reading raw off the socket until a read times out — the server has
        genuinely gone quiet, per `WAIT_TICK` — sidesteps guessing the
        exact byte count of a multipart frame."""
        s = socket.create_connection(("127.0.0.1", self.port), timeout=DEADLINE)
        self.addCleanup(s.close)
        s.sendall(
            f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            f"Connection: close\r\n\r\n".encode("ascii"))
        s.settimeout(per_read_timeout)
        buf = b""
        try:
            while len(buf) < max_bytes:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
        except (socket.timeout, TimeoutError):
            pass
        return buf


class TestSnapshot(ServerCase):

    def test_no_frame_yet_is_503(self):
        try:
            self.get("/snapshot.jpg")
            self.fail("expected an HTTPError")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 503)

    def test_returns_the_latest_published_jpeg(self):
        self.frame.publish(b"\xff\xd8fakejpeg", frame_id=3)
        resp = self.get("/snapshot.jpg")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.headers["Content-Type"], "image/jpeg")
        self.assertEqual(resp.read(), b"\xff\xd8fakejpeg")


class TestInfo(ServerCase):

    def test_returns_the_info_callback_as_json(self):
        resp = self.get("/info.json")
        self.assertEqual(resp.headers["Content-Type"], "application/json")
        self.assertEqual(json.loads(resp.read()), self.info)

    def test_reflects_a_later_change_to_the_underlying_info(self):
        self.info["frame_id"] = 42
        resp = self.get("/info.json")
        self.assertEqual(json.loads(resp.read())["frame_id"], 42)

    def test_a_query_string_still_matches_the_route(self):
        # Same latent bug as /stream.mjpg's — nothing fetches this with a
        # query string today, but the dispatch bug was general, not
        # stream-specific.
        resp = self.get("/info.json?t=123")
        self.assertEqual(resp.status, 200)

    def test_allows_cross_origin_fetch(self):
        # M3 build item 4: the developer panel fetches this from the staff
        # view's own origin (core's port), a different origin than
        # camera's — without this header the browser's CORS check drops
        # the response before JS ever sees it.
        resp = self.get("/info.json")
        self.assertEqual(resp.headers["Access-Control-Allow-Origin"], "*")


class TestControlsRoute(ServerCase):
    """`GET /controls.json` — doc §12.8's new dev-panel card. `mjpeg.py`
    only serialises whatever the `get_controls` callback returns; the
    actual spec/state shape is `CameraProcess.controls_snapshot()`'s job
    (test_camera_main.py), not this module's."""

    def test_returns_the_controls_callback_as_json(self):
        resp = self.get("/controls.json")
        self.assertEqual(resp.headers["Content-Type"], "application/json")
        self.assertEqual(json.loads(resp.read()), {"controls": self.controls})

    def test_allows_cross_origin_fetch(self):
        # Same reasoning as /info.json's own test: the staff view fetches
        # this from core's origin, a different one than camera's.
        resp = self.get("/controls.json")
        self.assertEqual(resp.headers["Access-Control-Allow-Origin"], "*")


class TestControlRoute(ServerCase):
    """`POST /control` — a developer moving a slider or flipping a chip,
    not a wire protocol every other process depends on, so a malformed
    request is a 400 with a reason, never a 500."""

    def test_valid_request_calls_set_control_and_returns_its_result(self):
        resp = self.post("/control",
                         {"name": "white_balance", "auto": False, "value": 5000})
        self.assertEqual(resp.status, 200)
        self.assertEqual(self.set_control_calls, [("white_balance", False, 5000)])
        self.assertEqual(json.loads(resp.read()),
                         {"name": "white_balance", "auto": False, "value": 5000})

    def test_omitted_auto_and_value_are_passed_through_as_none(self):
        self.post("/control", {"name": "white_balance"})
        self.assertEqual(self.set_control_calls, [("white_balance", None, None)])

    def test_missing_name_is_400(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/control", {"value": 1})
        self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(self.set_control_calls, [])

    def test_non_boolean_auto_is_400(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/control", {"name": "white_balance", "auto": "yes"})
        self.assertEqual(ctx.exception.code, 400)

    def test_non_numeric_value_is_400(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/control", {"name": "white_balance", "value": "bright"})
        self.assertEqual(ctx.exception.code, 400)

    def test_invalid_json_body_is_400(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/control", method="POST",
            data=b"not json")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=DEADLINE)
        self.assertEqual(ctx.exception.code, 400)

    def test_a_camera_error_from_the_backend_surfaces_as_400_with_its_reason(self):
        self._raise = CameraError("unknown camera control 'nope'")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/control", {"name": "nope", "value": 1})
        self.assertEqual(ctx.exception.code, 400)
        self.assertIn("unknown camera control",
                      json.loads(ctx.exception.read())["error"])

    def test_post_to_an_unknown_path_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/nope", {})
        self.assertEqual(ctx.exception.code, 404)


class TestUnknownPath(ServerCase):

    def test_404(self):
        try:
            self.get("/nope")
            self.fail("expected an HTTPError")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)


class TestStream(ServerCase):

    def test_multipart_framing_carries_a_published_frame(self):
        self.frame.publish(b"\xff\xd8onejpeg", frame_id=1)
        chunk = self.raw_get("/stream.mjpg")
        self.assertIn(b"multipart/x-mixed-replace", chunk)
        self.assertIn(mjpeg.BOUNDARY.encode(), chunk)
        self.assertIn(b"Content-Type: image/jpeg", chunk)
        self.assertIn(b"\xff\xd8onejpeg", chunk)

    def test_a_cache_busting_query_string_still_matches_the_route(self):
        # Regression: index.html's loadLiveImg() always requests
        # /stream.mjpg?t=<timestamp> (M3.3), and do_GET used to match
        # self.path with `==` against the bare route, 404ing on every
        # single browser request — found 2026-08-12 running against a
        # real browser for the first time.
        self.frame.publish(b"\xff\xd8onejpeg", frame_id=1)
        chunk = self.raw_get("/stream.mjpg?t=1234567890")
        self.assertIn(b"200", chunk.split(b"\r\n", 1)[0])
        self.assertIn(b"\xff\xd8onejpeg", chunk)

    def test_a_client_connecting_after_publish_still_gets_the_current_frame(self):
        # wait_next's after_frame_id=-1 start must match "already latest",
        # not only frames published after the connection opens.
        self.frame.publish(b"\xff\xd8late", frame_id=9)
        chunk = self.raw_get("/stream.mjpg")
        self.assertIn(b"\xff\xd8late", chunk)


if __name__ == "__main__":
    unittest.main()
