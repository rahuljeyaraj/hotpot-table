"""Tests for classifier/rf_client.py — the Roboflow sibling of
test_ei_client.py, doc `ROBOFLOW_PATHWAY.md` §6 step 3.

Two test seams, matching rf_client.py's own two tracks: `_urlopen` (same
FakeUrlopen shape test_ei_client.py already uses, for the REST half) and
`_roboflow_client` (a fake workspace()/project() chain, for the SDK half).
Neither touches the network; no test here imports the real `roboflow`
package.

Run from the repo root:

    python -m unittest discover -s python/tests -v
"""

import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hotpot.classifier import rf_client  # noqa: E402


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
    """Records every request and replays a queue of canned (status,
    json-or-bytes) responses, in order — same shape test_ei_client.py's
    own FakeUrlopen uses.
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
    err = urllib.error.HTTPError(url, status, "error", {}, io.BytesIO(raw))
    return err


class RfClientRestTestCase(unittest.TestCase):

    def setUp(self):
        self.fake = FakeUrlopen()
        self._real_urlopen = rf_client._urlopen
        rf_client._urlopen = self.fake
        self.addCleanup(self._restore)

    def _restore(self):
        rf_client._urlopen = self._real_urlopen


class TestCheckApiKey(RfClientRestTestCase):

    def test_returns_the_parsed_response(self):
        self.fake.add(200, {"workspace": "rahuls-workspace-mqtgo"})
        resp = rf_client.check_api_key("k")
        self.assertEqual(resp["workspace"], "rahuls-workspace-mqtgo")
        self.assertIn("api_key=k", self.fake.calls[0].full_url)

    def test_a_bad_key_raises_rf_client_error_with_the_message(self):
        self.fake.add(401, {"error": "invalid api key"})
        with self.assertRaises(rf_client.RFClientError) as ctx:
            rf_client.check_api_key("bad")
        self.assertIn("invalid api key", str(ctx.exception))


class TestGetProject(RfClientRestTestCase):

    def test_hits_the_workspace_project_endpoint(self):
        self.fake.add(200, {"type": "single-label-classification"})
        resp = rf_client.get_project("ws", "proj", "k")
        self.assertEqual(resp["type"], "single-label-classification")
        self.assertIn("/ws/proj", self.fake.calls[0].full_url)

    def test_a_missing_project_raises(self):
        self.fake.add(404, {"error": "not found"})
        with self.assertRaises(rf_client.RFClientError):
            rf_client.get_project("ws", "nope", "k")


class TestTrain(RfClientRestTestCase):

    def test_returns_the_job_id_from_the_id_field(self):
        self.fake.add(200, {"id": "job-123"})
        job_id = rf_client.train("ws", "proj", "1", "k", "fast")
        self.assertEqual(job_id, "job-123")
        req = self.fake.calls[0]
        self.assertIn("/ws/proj/1/train", req.full_url)
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body["model_type"], "fast")

    def test_falls_back_to_job_id_field(self):
        self.fake.add(200, {"job_id": "job-456"})
        self.assertEqual(rf_client.train("ws", "proj", "1", "k", "fast"), "job-456")

    def test_falls_back_to_jobid_field(self):
        self.fake.add(200, {"jobId": "job-789"})
        self.assertEqual(rf_client.train("ws", "proj", "1", "k", "fast"), "job-789")

    def test_an_ok_response_with_no_recognised_id_field_raises(self):
        self.fake.add(200, {"success": True})
        with self.assertRaises(rf_client.RFClientError):
            rf_client.train("ws", "proj", "1", "k", "fast")

    def test_a_refused_train_raises(self):
        self.fake.add(402, {"error": "insufficient credits"})
        with self.assertRaises(rf_client.RFClientError) as ctx:
            rf_client.train("ws", "proj", "1", "k", "fast")
        self.assertIn("insufficient credits", str(ctx.exception))


class TestJobStatusAndWait(RfClientRestTestCase):

    def test_wait_returns_once_status_reads_success(self):
        self.fake.add(200, {"status": "running"})
        self.fake.add(200, {"status": "success"})
        polls = []
        rf_client.wait_for_training("ws", "proj", "k", "job-1",
                                    on_poll=lambda: polls.append(1),
                                    poll_interval_s=0)
        self.assertEqual(len(polls), 1)

    def test_wait_raises_on_a_failed_status(self):
        self.fake.add(200, {"status": "failed"})
        with self.assertRaises(rf_client.RFClientError):
            rf_client.wait_for_training("ws", "proj", "k", "job-1",
                                        poll_interval_s=0)

    def test_wait_also_understands_the_edge_impulse_style_finished_shape(self):
        # Fallback for a Roboflow response shaped more like EI's own
        # {"finished": bool, "finishedSuccessful": bool} — unconfirmed
        # either way (module VERIFY note), both shapes are tried.
        self.fake.add(200, {"finished": True, "finishedSuccessful": True})
        rf_client.wait_for_training("ws", "proj", "k", "job-1", poll_interval_s=0)

    def test_wait_times_out_rather_than_polling_forever(self):
        self.fake.add(200, {"status": "running"})
        # timeout_s=-1: the deadline is already in the past before the
        # first poll even runs, so this raises after exactly one queued
        # response — a positive-but-tiny timeout would be a clock-
        # resolution race on a fast machine (both calls landing at the
        # same monotonic() tick), which -1 makes impossible.
        with self.assertRaises(rf_client.RFClientError) as ctx:
            rf_client.wait_for_training("ws", "proj", "k", "job-1",
                                        poll_interval_s=0, timeout_s=-1)
        self.assertIn("timed out", str(ctx.exception))


class FakeProject:
    def __init__(self, *, upload_error=None, version="1", models=None):
        self.uploads = []
        self._upload_error = upload_error
        self._version = version
        self.generate_version_calls = []
        self._models = models if models is not None else [object()]

    def upload(self, image_path, annotation=None, split=None):
        if self._upload_error:
            raise self._upload_error
        self.uploads.append((image_path, annotation, split))

    def generate_version(self, settings):
        self.generate_version_calls.append(settings)
        return self._version

    def version(self, v):
        return self

    def models(self):
        return self._models


class FakeWorkspace:
    def __init__(self, project):
        self._project = project

    def project(self, name):
        return self._project


class FakeRoboflow:
    def __init__(self, project):
        self._project = project

    def workspace(self, name):
        return FakeWorkspace(self._project)


class RfClientSdkTestCase(unittest.TestCase):

    def setUp(self):
        self._real = rf_client._roboflow_client
        self.addCleanup(self._restore)

    def _restore(self):
        rf_client._roboflow_client = self._real

    def use(self, project):
        rf_client._roboflow_client = lambda api_key: FakeRoboflow(project)
        return project


class TestUploadImage(RfClientSdkTestCase):

    def test_uploads_with_the_class_name_as_the_annotation(self):
        project = self.use(FakeProject())
        rf_client.upload_image("ws", "proj", "k", "/tmp/a.jpg", "soya_chunks")
        self.assertEqual(project.uploads,
                         [("/tmp/a.jpg", "soya_chunks", "train")])

    def test_an_sdk_failure_is_wrapped(self):
        self.use(FakeProject(upload_error=RuntimeError("quota exceeded")))
        with self.assertRaises(rf_client.RFClientError) as ctx:
            rf_client.upload_image("ws", "proj", "k", "/tmp/a.jpg", "x")
        self.assertIn("quota exceeded", str(ctx.exception))


class TestUploadCaptures(RfClientSdkTestCase):

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.captures = Path(self._tmp.name)
        for label, names in {"a": ["1.jpg", "2.jpg"], "b": ["3.jpg"]}.items():
            d = self.captures / label
            d.mkdir()
            for n in names:
                (d / n).write_bytes(b"x")
                (d / (n + ".json")).write_text("{}")  # provenance sidecar

    def test_uploads_every_image_and_skips_sidecars(self):
        project = self.use(FakeProject())
        result = rf_client.upload_captures("ws", "proj", "k", self.captures)
        self.assertEqual(result["uploaded"], {"a": 2, "b": 1})
        self.assertEqual(result["failures"], [])
        self.assertEqual(len(project.uploads), 3)
        for _path, annotation, split in project.uploads:
            self.assertIn(annotation, ("a", "b"))
            self.assertEqual(split, "train")

    def test_no_captures_at_all_raises(self):
        empty = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(empty, ignore_errors=True))
        self.use(FakeProject())
        with self.assertRaises(rf_client.RFClientError):
            rf_client.upload_captures("ws", "proj", "k", empty)

    def test_a_partial_failure_is_reported_not_fatal(self):
        self.use(FakeProject(upload_error=RuntimeError("server hiccup")))
        result = rf_client.upload_captures("ws", "proj", "k", self.captures)
        self.assertEqual(result["uploaded"], {})
        self.assertEqual(len(result["failures"]), 3)

    def test_on_progress_is_called(self):
        self.use(FakeProject())
        ticks = []
        rf_client.upload_captures("ws", "proj", "k", self.captures,
                                  on_progress=lambda **kw: ticks.append(kw))
        self.assertGreater(len(ticks), 0)
        self.assertEqual(ticks[-1]["uploaded"], 3)
        self.assertEqual(ticks[-1]["total"], 3)


class TestGenerateVersion(RfClientSdkTestCase):

    def test_returns_the_version_as_a_string(self):
        self.use(FakeProject(version=3))
        v = rf_client.generate_version("ws", "proj", "k")
        self.assertEqual(v, "3")
        self.assertIsInstance(v, str)

    def test_passes_settings_through(self):
        project = self.use(FakeProject())
        rf_client.generate_version("ws", "proj", "k", settings={"augment": True})
        self.assertEqual(project.generate_version_calls, [{"augment": True}])


class TestDownloadWeights(RfClientSdkTestCase):

    def test_returns_the_path_to_the_newly_appeared_file(self):
        class FakeModel:
            def download(self_inner):
                Path("weights.onnx").write_bytes(b"onnx bytes")

        self.use(FakeProject(models=[FakeModel()]))
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        cwd = os.getcwd()
        try:
            path = rf_client.download_weights("ws", "proj", "1", "k", tmp)
        finally:
            os.chdir(cwd)
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(Path(path).read_bytes(), b"onnx bytes")

    def test_no_models_on_the_version_raises(self):
        self.use(FakeProject(models=[]))
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        cwd = os.getcwd()
        try:
            with self.assertRaises(rf_client.RFClientError):
                rf_client.download_weights("ws", "proj", "1", "k", tmp)
        finally:
            os.chdir(cwd)

    def test_restores_the_working_directory_even_on_failure(self):
        class BoomModel:
            def download(self_inner):
                raise RuntimeError("paid plan required")

        self.use(FakeProject(models=[BoomModel()]))
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        cwd = os.getcwd()
        with self.assertRaises(rf_client.RFClientError):
            rf_client.download_weights("ws", "proj", "1", "k", tmp)
        self.assertEqual(os.getcwd(), cwd)


if __name__ == "__main__":
    unittest.main()
