"""Tests for classifier/rf_deploy.py — the Roboflow sibling of
test_ei_deploy.py, doc `ROBOFLOW_PATHWAY.md` §6 step 4.

Neither Path ever touches the network here: Path A is driven through a
`RoboflowInferenceBackend` built with a fake `model_factory` (the same
seam test_backend_rf.py already exercises); Path B is driven through a
fake `client` object standing in for `rf_client`, the same DI shape
`core/main.py` gives `ei_client`/`ei_deploy`.

Run from the repo root:

    python -m unittest discover -s python/tests -v
"""

import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hotpot.classifier import rf_deploy, rf_store  # noqa: E402
from hotpot.classifier.backend_rf import RoboflowInferenceBackend  # noqa: E402


def _make_zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


class FakeRfModel:
    def __init__(self, class_names):
        self.class_names = class_names

    def infer(self, image):
        return {"top": self.class_names[0], "confidence": 1.0}


class TestDeployPathA(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project_path = Path(self._tmp.name) / "rf_project.json"

    def test_no_linked_project_raises(self):
        with self.assertRaises(rf_deploy.RfDeployError):
            rf_deploy.deploy_path_a(project_path=self.project_path)

    def test_warms_the_backends_model_cache(self):
        rf_store.save_project(self.project_path, "ws", "proj", "key",
                              version="1")
        calls = []

        def factory(**kw):
            calls.append(kw)
            return FakeRfModel(["a", "b"])
        backend = RoboflowInferenceBackend(
            project_path=self.project_path, model_factory=factory)
        rf_deploy.deploy_path_a(project_path=self.project_path, backend=backend)
        self.assertEqual(len(calls), 1)

    def test_a_backend_failure_is_wrapped_in_rf_deploy_error(self):
        rf_store.save_project(self.project_path, "ws", "proj", "key",
                              version="1")

        def boom(**kw):
            raise RuntimeError("no network")
        backend = RoboflowInferenceBackend(
            project_path=self.project_path, model_factory=boom)
        with self.assertRaises(rf_deploy.RfDeployError):
            rf_deploy.deploy_path_a(project_path=self.project_path, backend=backend)

    def test_progress_stages_are_reported(self):
        rf_store.save_project(self.project_path, "ws", "proj", "key",
                              version="1")
        backend = RoboflowInferenceBackend(
            project_path=self.project_path,
            model_factory=lambda **kw: FakeRfModel(["a"]))
        stages = []
        rf_deploy.deploy_path_a(project_path=self.project_path,
                                backend=backend, on_progress=stages.append)
        self.assertEqual(stages, ["loading", "done"])


class FakeRfClient:
    def __init__(self, downloaded_bytes, *, suffix=".onnx", raise_error=None):
        self._bytes = downloaded_bytes
        self._suffix = suffix
        self._raise = raise_error
        self.calls = []

    def download_weights(self, workspace, project, version, api_key, dest_dir):
        self.calls.append((workspace, project, version, api_key, dest_dir))
        if self._raise:
            raise self._raise
        path = Path(dest_dir) / f"weights{self._suffix}"
        path.write_bytes(self._bytes)
        return str(path)


class TestDeployPathB(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project_path = Path(self._tmp.name) / "rf_project.json"
        self.models_dir = Path(self._tmp.name) / "models"

    def test_a_bare_onnx_download_is_written_and_recorded(self):
        client = FakeRfClient(b"fake onnx bytes")
        result = rf_deploy.deploy_path_b(
            "ws", "proj", "3", "key", project_path=self.project_path,
            models_dir=self.models_dir, model_filename="m.onnx", client=client)
        self.assertEqual((self.models_dir / "m.onnx").read_bytes(),
                         b"fake onnx bytes")
        self.assertFalse(result["class_list_written"])
        project = rf_store.load_project(self.project_path)
        self.assertEqual(project["model_file"], "m.onnx")
        self.assertEqual(project["version"], "3")

    def test_a_zip_bundling_the_onnx_and_a_class_list_is_unpacked(self):
        zbytes = _make_zip({
            "weights.onnx": b"onnx bytes here",
            "class_names.json": json.dumps(["a", "b", "c"]),
        })
        client = FakeRfClient(zbytes, suffix=".zip")
        result = rf_deploy.deploy_path_b(
            "ws", "proj", "3", "key", project_path=self.project_path,
            models_dir=self.models_dir, model_filename="m.onnx", client=client)
        self.assertEqual((self.models_dir / "m.onnx").read_bytes(),
                         b"onnx bytes here")
        self.assertTrue(result["class_list_written"])
        classes = json.loads((self.models_dir / "m.classes.json").read_text())
        self.assertEqual(classes, ["a", "b", "c"])

    def test_a_zip_with_no_recognisable_class_list_still_deploys_the_model(self):
        zbytes = _make_zip({"weights.onnx": b"onnx bytes", "readme.txt": b"hi"})
        client = FakeRfClient(zbytes, suffix=".zip")
        result = rf_deploy.deploy_path_b(
            "ws", "proj", "3", "key", project_path=self.project_path,
            models_dir=self.models_dir, model_filename="m.onnx", client=client)
        self.assertTrue((self.models_dir / "m.onnx").exists())
        self.assertFalse(result["class_list_written"])
        self.assertFalse((self.models_dir / "m.classes.json").exists())

    def test_a_zip_with_no_onnx_at_all_raises(self):
        zbytes = _make_zip({"readme.txt": b"hi"})
        client = FakeRfClient(zbytes, suffix=".zip")
        with self.assertRaises(rf_deploy.RfDeployError):
            rf_deploy.deploy_path_b(
                "ws", "proj", "3", "key", project_path=self.project_path,
                models_dir=self.models_dir, model_filename="m.onnx", client=client)

    def test_a_zip_with_two_onnx_files_refuses_to_guess(self):
        zbytes = _make_zip({"a.onnx": b"1", "b.onnx": b"2"})
        client = FakeRfClient(zbytes, suffix=".zip")
        with self.assertRaises(rf_deploy.RfDeployError):
            rf_deploy.deploy_path_b(
                "ws", "proj", "3", "key", project_path=self.project_path,
                models_dir=self.models_dir, model_filename="m.onnx", client=client)

    def test_a_download_failure_is_wrapped_in_rf_deploy_error(self):
        client = FakeRfClient(b"", raise_error=RuntimeError("paid plan required"))
        with self.assertRaises(rf_deploy.RfDeployError):
            rf_deploy.deploy_path_b(
                "ws", "proj", "3", "key", project_path=self.project_path,
                models_dir=self.models_dir, model_filename="m.onnx", client=client)

    def test_redeploying_under_a_new_filename_wipes_the_old_one(self):
        client = FakeRfClient(b"v1 bytes")
        rf_deploy.deploy_path_b(
            "ws", "proj", "1", "key", project_path=self.project_path,
            models_dir=self.models_dir, model_filename="old.onnx", client=client)
        self.assertTrue((self.models_dir / "old.onnx").exists())

        client2 = FakeRfClient(b"v2 bytes")
        rf_deploy.deploy_path_b(
            "ws", "proj", "2", "key", project_path=self.project_path,
            models_dir=self.models_dir, model_filename="new.onnx", client=client2)
        self.assertTrue((self.models_dir / "new.onnx").exists())
        self.assertFalse((self.models_dir / "old.onnx").exists())

    def test_redeploying_under_the_same_filename_overwrites_cleanly(self):
        client = FakeRfClient(b"v1 bytes")
        rf_deploy.deploy_path_b(
            "ws", "proj", "1", "key", project_path=self.project_path,
            models_dir=self.models_dir, model_filename="m.onnx", client=client)
        client2 = FakeRfClient(b"v2 bytes, longer than before")
        rf_deploy.deploy_path_b(
            "ws", "proj", "2", "key", project_path=self.project_path,
            models_dir=self.models_dir, model_filename="m.onnx", client=client2)
        self.assertEqual((self.models_dir / "m.onnx").read_bytes(),
                         b"v2 bytes, longer than before")

    def test_progress_stages_are_reported(self):
        client = FakeRfClient(b"bytes")
        stages = []
        rf_deploy.deploy_path_b(
            "ws", "proj", "1", "key", project_path=self.project_path,
            models_dir=self.models_dir, model_filename="m.onnx", client=client,
            on_progress=stages.append)
        self.assertEqual(stages, ["downloading", "extracting", "writing", "done"])


if __name__ == "__main__":
    unittest.main()
