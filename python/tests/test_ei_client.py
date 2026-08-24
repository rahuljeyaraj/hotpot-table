"""Tests for classifier/ei_client.py.

No real network call in any test here: `ei_client._urlopen` is a module-
level test seam (that module's own docstring, same role `run` plays in
backend_ei.EiCppBackend) swapped for a fake per test and restored in
tearDown -- this codebase's convention (see test_classifier_backends.py's
`FakeProc`/injected `run`) rather than `unittest.mock`, which nothing else
here uses either.

Run from the repo root:

    python -m unittest discover -s python/tests -v
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hotpot.classifier import ei_client  # noqa: E402


class FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeUrlopen:
    """Records every request it was called with and replays a queue of
    canned (status, json-or-bytes) responses, in order -- one call to
    `add()` per expected request. A response whose status is >= 400 is
    raised as urllib.error.HTTPError instead of returned, matching real
    urlopen's own behaviour (ei_client._request()'s except clause for it).
    """

    def __init__(self):
        self.calls = []
        self._queue = []

    def add(self, status, body):
        self._queue.append((status, body))

    def __call__(self, req, timeout=None):
        self.calls.append(req)
        if not self._queue:
            raise AssertionError("FakeUrlopen: no more canned responses queued")
        status, body = self._queue.pop(0)
        raw = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        if status >= 400:
            raise _http_error(req.full_url, status, raw)
        return FakeResponse(status, raw)


def _http_error(url, status, raw):
    import io
    err = urllib.error.HTTPError(url, status, "error", {}, io.BytesIO(raw))
    return err


class EiClientTestCase(unittest.TestCase):

    def setUp(self):
        self.fake = FakeUrlopen()
        self._real_urlopen = ei_client._urlopen
        ei_client._urlopen = self.fake
        self.addCleanup(self._restore)

    def _restore(self):
        ei_client._urlopen = self._real_urlopen


class TestLogin(EiClientTestCase):

    def test_returns_the_jwt_on_success(self):
        self.fake.add(200, {"success": True, "token": "jwt-abc"})
        token = ei_client.login("me@example.com", "hunter2")
        self.assertEqual(token, "jwt-abc")
        req = self.fake.calls[0]
        self.assertIn("api-login", req.full_url)
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body["username"], "me@example.com")
        self.assertEqual(body["password"], "hunter2")
        self.assertNotIn("totpToken", body)

    def test_totp_rides_the_body_when_given(self):
        self.fake.add(200, {"success": True, "token": "jwt-abc"})
        ei_client.login("me@example.com", "hunter2", totp="123456")
        body = json.loads(self.fake.calls[0].data.decode("utf-8"))
        self.assertEqual(body["totpToken"], "123456")

    def test_totp_required_is_its_own_exception(self):
        self.fake.add(401, {"success": False,
                            "error": "ERR_TOTP_TOKEN_IS_REQUIRED: need a code"})
        with self.assertRaises(ei_client.EITotpRequiredError):
            ei_client.login("me@example.com", "hunter2")

    def test_a_plain_failure_is_eiclienterror_with_eis_message(self):
        self.fake.add(401, {"success": False, "error": "wrong password"})
        with self.assertRaises(ei_client.EIClientError) as ctx:
            ei_client.login("me@example.com", "wrong")
        self.assertIn("wrong password", str(ctx.exception))


class TestCreateProject(EiClientTestCase):

    def test_returns_id_and_api_key_and_defaults_public(self):
        self.fake.add(200, {"success": True, "id": 42, "apiKey": "ei_xyz"})
        project_id, api_key = ei_client.create_project("jwt-abc", "hotpot-ingredients")
        self.assertEqual((project_id, api_key), (42, "ei_xyz"))
        body = json.loads(self.fake.calls[0].data.decode("utf-8"))
        self.assertEqual(body["projectName"], "hotpot-ingredients")
        self.assertEqual(body["projectVisibility"], "public")
        self.assertTrue(body["createApiKey"])
        self.assertEqual(self.fake.calls[0].headers.get("X-jwt-token"), "jwt-abc")


class TestUploadSamples(EiClientTestCase):

    def test_empty_samples_is_a_no_op_no_network_call(self):
        n = ei_client.upload_samples("ei_key", "split", "mushroom", [])
        self.assertEqual(n, 0)
        self.assertEqual(self.fake.calls, [])

    def test_uploads_and_returns_the_count(self):
        self.fake.add(200, {"success": True})
        n = ei_client.upload_samples(
            "ei_key", "split", "mushroom",
            [("mushroom.a.jpg", b"jpeg-bytes"), ("mushroom.b.jpg", b"more-bytes")])
        self.assertEqual(n, 2)
        req = self.fake.calls[0]
        self.assertIn("split/files", req.full_url)
        self.assertEqual(req.headers.get("X-label"), "mushroom")
        self.assertEqual(req.headers.get("X-api-key"), "ei_key")
        self.assertIn(b"mushroom.a.jpg", req.data)
        self.assertIn(b"jpeg-bytes", req.data)

    def test_a_failed_batch_raises_eiclienterror(self):
        self.fake.add(413, {"success": False, "error": "payload too large"})
        with self.assertRaises(ei_client.EIClientError):
            ei_client.upload_samples("ei_key", "split", "mushroom",
                                     [("mushroom.a.jpg", b"x")])


class TestBatched(unittest.TestCase):

    def test_chunks_at_the_given_size(self):
        chunks = list(ei_client._batched(list(range(7)), batch_size=3))
        self.assertEqual(chunks, [[0, 1, 2], [3, 4, 5], [6]])

    def test_empty_input_yields_nothing(self):
        self.assertEqual(list(ei_client._batched([])), [])


class TestIterLabelImages(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = self.dir.name

    def _touch(self, *parts):
        path = os.path.join(self.root, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"x")

    def test_missing_dir_is_empty_not_an_error(self):
        self.assertEqual(ei_client.iter_label_images(
            os.path.join(self.root, "nope")), {})

    def test_groups_images_by_label_and_excludes_sidecars(self):
        self._touch("mushroom", "1_bin0.jpg")
        self._touch("mushroom", "1_bin0.json")
        self._touch("mushroom", "2_bin1.jpeg")
        self._touch("egg", "1_bin2.png")
        by_label = ei_client.iter_label_images(self.root)
        self.assertEqual(set(by_label), {"mushroom", "egg"})
        self.assertEqual(len(by_label["mushroom"]), 2)
        self.assertEqual(len(by_label["egg"]), 1)

    def test_a_label_folder_with_no_images_is_omitted(self):
        self._touch("empty_label", "1_bin0.json")
        self.assertEqual(ei_client.iter_label_images(self.root), {})

    def test_a_stray_file_next_to_the_label_folders_is_ignored(self):
        self._touch("mushroom", "1.jpg")
        with open(os.path.join(self.root, "README.txt"), "w") as f:
            f.write("not a label")
        by_label = ei_client.iter_label_images(self.root)
        self.assertEqual(set(by_label), {"mushroom"})


class TestUploadCaptures(EiClientTestCase):

    def setUp(self):
        super().setUp()
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = self.dir.name
        for name in ("1.jpg", "2.jpg"):
            path = os.path.join(self.root, "mushroom", name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(b"jpeg")

    def test_no_captures_at_all_raises(self):
        empty = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(empty, ignore_errors=True))
        with self.assertRaises(ei_client.EIClientError):
            ei_client.upload_captures("ei_key", empty)

    def test_uploads_everything_and_reports_progress(self):
        self.fake.add(200, {"success": True})
        progress_calls = []
        result = ei_client.upload_captures(
            "ei_key", self.root, on_progress=lambda **kw: progress_calls.append(kw))
        self.assertEqual(result["uploaded"], {"mushroom": 2})
        self.assertEqual(result["failures"], [])
        # At least the initial 0/total tick and one after the batch landed.
        self.assertGreaterEqual(len(progress_calls), 2)
        self.assertEqual(progress_calls[-1]["uploaded"], 2)
        self.assertEqual(progress_calls[-1]["total"], 2)

    def test_a_failed_batch_is_reported_not_raised(self):
        self.fake.add(500, {"success": False, "error": "server exploded"})
        result = ei_client.upload_captures("ei_key", self.root)
        self.assertEqual(result["uploaded"], {})
        self.assertEqual(len(result["failures"]), 1)
        self.assertIn("server exploded", result["failures"][0])


class TestBuildAndDownload(EiClientTestCase):

    def test_build_model_posts_the_locked_deploy_settings(self):
        self.fake.add(200, {"success": True, "id": 7})
        job_id = ei_client.build_model("ei_key", 1087506)
        self.assertEqual(job_id, 7)
        req = self.fake.calls[0]
        self.assertIn("type=zip", req.full_url)
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body["engine"], ei_client.DEPLOY_ENGINE)
        self.assertEqual(body["modelType"], ei_client.DEPLOY_MODEL_TYPE)

    def test_job_status_unwraps_the_job_key(self):
        self.fake.add(200, {"success": True, "job": {"finished": True}})
        self.assertEqual(ei_client.job_status("ei_key", 1, 7), {"finished": True})

    def test_wait_for_job_returns_once_finished_successfully(self):
        self.fake.add(200, {"job": {"finished": False}})
        self.fake.add(200, {"job": {"finished": True, "finishedSuccessful": True}})
        polls = []
        ei_client.wait_for_job("ei_key", 1, 7, on_poll=lambda: polls.append(1),
                               poll_interval_s=0.0)
        self.assertEqual(len(polls), 1)

    def test_wait_for_job_raises_on_an_unsuccessful_finish(self):
        self.fake.add(200, {"job": {"finished": True, "finishedSuccessful": False}})
        with self.assertRaises(ei_client.EIClientError):
            ei_client.wait_for_job("ei_key", 1, 7, poll_interval_s=0.0)

    def test_wait_for_job_raises_on_timeout(self):
        self.fake.add(200, {"job": {"finished": False}})
        # A negative timeout guarantees the deadline is already in the
        # past by the time the first poll's elapsed time is checked,
        # regardless of the host clock's resolution -- 0.0 flaked here on
        # a fast run where two polls landed within the same monotonic()
        # tick.
        with self.assertRaises(ei_client.EIClientError):
            ei_client.wait_for_job("ei_key", 1, 7, poll_interval_s=0.0, timeout_s=-1.0)

    def test_download_model_returns_the_raw_bytes(self):
        self.fake.add(200, b"PK\x03\x04-zip-bytes")
        data = ei_client.download_model("ei_key", 1087506)
        self.assertEqual(data, b"PK\x03\x04-zip-bytes")
        req = self.fake.calls[0]
        self.assertIn("type=zip", req.full_url)
        self.assertIn(f"engine={ei_client.DEPLOY_ENGINE}", req.full_url)

    def test_download_model_raises_eiclienterror_on_http_failure(self):
        self.fake.add(404, b"not found")
        with self.assertRaises(ei_client.EIClientError):
            ei_client.download_model("ei_key", 1087506)


if __name__ == "__main__":
    unittest.main()
