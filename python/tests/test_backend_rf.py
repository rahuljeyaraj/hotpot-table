"""Tests for classifier/backend_rf.py — both Roboflow backends, doc
`ROBOFLOW_PATHWAY.md` §6 step 1.

Neither backend ever touches the network or a real model file here:
`RoboflowInferenceBackend.model_factory` and `RoboflowOnnxBackend.
session_factory` are the test seams (this module's own docstring compares
them to `EiCppBackend`'s `run` and `ScaleReader`'s `open_port`), so no test
needs the `inference` or `onnxruntime` packages installed at all.

Run from the repo root:

    python -m unittest discover -s python/tests -v
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402

from hotpot.classifier import backend_ei, backend_rf, rf_store  # noqa: E402


def _bgr_crop(h=40, w=50):
    """A crop with a distinct blue channel and nothing in red/green — a
    BGR->RGB mixup is visible if a test inspects what the fake model/
    session actually received.
    """
    crop = np.zeros((h, w, 3), dtype=np.uint8)
    crop[:, :, 0] = 200  # B in BGR order
    return crop


class FakeRfModel:
    """Stands in for whatever `inference.get_model()` would return."""

    def __init__(self, class_names, *, input_size=None, top="a", conf=0.9):
        self.class_names = class_names
        if input_size is not None:
            self.input_size = input_size
        self._top = top
        self._conf = conf
        self.seen_images = []

    def infer(self, image):
        self.seen_images.append(image)
        return {"top": self._top, "confidence": self._conf,
                "predictions": [{"class": self._top, "confidence": self._conf}]}


class TestRoboflowInferenceBackend(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project_path = Path(self._tmp.name) / "rf_project.json"
        rf_store.save_project(self.project_path, "ws", "hotpot-ingredients",
                              "key1", version="1")

    def build(self, factory):
        return backend_rf.RoboflowInferenceBackend(
            project_path=self.project_path, model_factory=factory)

    def test_classify_returns_the_top_label_and_confidence(self):
        model = FakeRfModel(["a", "b"], top="b", conf=0.83)
        backend = self.build(lambda **kw: model)
        label, conf = backend.classify(_bgr_crop())
        self.assertEqual(label, "b")
        self.assertEqual(conf, 0.83)

    def test_model_id_is_project_slash_version_no_workspace_prefix(self):
        # Per the plan doc's own appendix ("Local inference"): model_id is
        # "{project}/{version}", not workspace-prefixed.
        seen = {}

        def factory(**kw):
            seen.update(kw)
            return FakeRfModel(["a"])
        self.build(factory).classify(_bgr_crop())
        self.assertEqual(seen["model_id"], "hotpot-ingredients/1")
        self.assertEqual(seen["api_key"], "key1")

    def test_the_model_is_not_loaded_until_the_first_classify_call(self):
        calls = []

        def factory(**kw):
            calls.append(1)
            return FakeRfModel(["a"])
        backend = self.build(factory)
        self.assertEqual(calls, [])  # construction alone must not load it
        backend.classify(_bgr_crop())
        self.assertEqual(calls, [1])

    def test_the_model_is_reused_across_calls(self):
        calls = []

        def factory(**kw):
            calls.append(1)
            return FakeRfModel(["a"])
        backend = self.build(factory)
        backend.classify(_bgr_crop())
        backend.classify(_bgr_crop())
        self.assertEqual(calls, [1])

    def test_bgr_is_converted_to_rgb_before_the_model_sees_it(self):
        model = FakeRfModel(["a"])
        backend = self.build(lambda **kw: model)
        backend.classify(_bgr_crop())
        seen = model.seen_images[0]
        # BGR crop had channel 0 (blue, in BGR order) set to 200. After a
        # real BGR->RGB conversion that 200 must have moved to channel 2
        # (blue, in RGB order) — if the conversion were deleted, channel 0
        # would still carry the 200 and this assertion would go red.
        self.assertEqual(int(seen[0, 0, 0]), 0)     # R
        self.assertEqual(int(seen[0, 0, 2]), 200)   # B

    def test_the_crop_is_resized_to_the_models_own_input_size(self):
        model = FakeRfModel(["a"], input_size=(32, 24))  # (w, h)
        backend = self.build(lambda **kw: model)
        backend.classify(_bgr_crop(h=100, w=100))
        seen = model.seen_images[0]
        self.assertEqual(seen.shape[:2], (24, 32))  # (h, w)

    def test_no_input_size_attribute_leaves_the_crop_unresized(self):
        model = FakeRfModel(["a"])  # no .input_size
        backend = self.build(lambda **kw: model)
        backend.classify(_bgr_crop(h=41, w=53))
        seen = model.seen_images[0]
        self.assertEqual(seen.shape[:2], (41, 53))

    def test_a_model_with_no_class_names_raises_a_classifier_backend_error(self):
        model = FakeRfModel([])
        backend = self.build(lambda **kw: model)
        with self.assertRaises(backend_ei.ClassifierBackendError):
            backend.classify(_bgr_crop())

    def test_no_linked_project_raises_classifier_backend_error(self):
        backend = backend_rf.RoboflowInferenceBackend(
            project_path=Path(self._tmp.name) / "nope.json",
            model_factory=lambda **kw: FakeRfModel(["a"]))
        with self.assertRaises(backend_ei.ClassifierBackendError):
            backend.classify(_bgr_crop())

    def test_a_load_failure_raises_classifier_backend_error_not_the_raw_one(self):
        def boom(**kw):
            raise RuntimeError("network unreachable")
        backend = self.build(boom)
        with self.assertRaises(backend_ei.ClassifierBackendError):
            backend.classify(_bgr_crop())

    def test_a_version_bump_in_the_store_reloads_the_model(self):
        # The class list must be re-read after the artifact changes — the
        # artifact here being state/rf_project.json's own `version` field,
        # bumped by a redeploy (rf_deploy.py).
        calls = []

        def factory(**kw):
            calls.append(kw["model_id"])
            if len(calls) == 1:
                return FakeRfModel(["a", "b"], top="a")
            return FakeRfModel(["x", "y", "z"], top="z")

        backend = self.build(factory)
        label1, _ = backend.classify(_bgr_crop())
        self.assertEqual(label1, "a")

        rf_store.save_project(self.project_path, "ws", "hotpot-ingredients",
                              "key1", version="2")
        label2, _ = backend.classify(_bgr_crop())
        self.assertEqual(label2, "z")
        self.assertEqual(calls, ["hotpot-ingredients/1", "hotpot-ingredients/2"])


class FakeOnnxInput:
    def __init__(self, name, shape):
        self.name = name
        self.shape = shape


class FakeOnnxSession:
    """Stands in for onnxruntime.InferenceSession. `scores` is the raw
    (pre-softmax) output the fake "model" returns for every call.
    """

    def __init__(self, scores, *, shape=(1, 3, 16, 16)):
        self._scores = np.asarray(scores, dtype=np.float32)
        self._shape = shape
        self.seen_tensors = []

    def get_inputs(self):
        return [FakeOnnxInput("images", list(self._shape))]

    def run(self, output_names, feeds):
        self.seen_tensors.append(feeds["images"])
        return [self._scores.reshape(1, -1)]


class TestRoboflowOnnxBackend(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project_path = Path(self._tmp.name) / "rf_project.json"
        self.models_dir = Path(self._tmp.name) / "models"
        self.models_dir.mkdir()
        self.onnx_path = self.models_dir / "hotpot-rf.onnx"
        self.onnx_path.write_bytes(b"not a real onnx file, existence only")
        self.classes_path = self.models_dir / "hotpot-rf.classes.json"
        self.classes_path.write_text(json.dumps(["a", "b", "c"]))
        rf_store.save_project(self.project_path, "ws", "hotpot-ingredients",
                              "key1", version="1", model_file="hotpot-rf.onnx")

    def build(self, session):
        return backend_rf.RoboflowOnnxBackend(
            project_path=self.project_path, models_dir=self.models_dir,
            session_factory=lambda path: session)

    def test_classify_returns_the_argmax_label(self):
        session = FakeOnnxSession([0.1, 5.0, 0.2])  # class "b" wins
        backend = self.build(session)
        label, conf = backend.classify(_bgr_crop())
        self.assertEqual(label, "b")
        self.assertGreater(conf, 0.5)  # softmax over a dominant logit

    def test_bgr_is_converted_to_rgb_before_the_model_sees_it(self):
        session = FakeOnnxSession([1.0, 0.0, 0.0], shape=(1, 3, 8, 8))
        backend = self.build(session)
        backend.classify(_bgr_crop())
        tensor = session.seen_tensors[0][0]  # (C,H,W) after NCHW transpose
        self.assertAlmostEqual(float(tensor[0, 0, 0]), 0.0, places=4)          # R
        self.assertAlmostEqual(float(tensor[2, 0, 0]), 200 / 255.0, places=4)  # B

    def test_the_crop_is_resized_to_the_onnx_inputs_own_shape_nchw(self):
        session = FakeOnnxSession([1.0, 0.0, 0.0], shape=(1, 3, 12, 20))
        backend = self.build(session)
        backend.classify(_bgr_crop(h=99, w=99))
        tensor = session.seen_tensors[0][0]  # (C, H, W)
        self.assertEqual(tensor.shape, (3, 12, 20))

    def test_the_crop_is_resized_to_the_onnx_inputs_own_shape_nhwc(self):
        session = FakeOnnxSession([1.0, 0.0, 0.0], shape=(1, 12, 20, 3))
        backend = self.build(session)
        backend.classify(_bgr_crop(h=99, w=99))
        tensor = session.seen_tensors[0][0]  # (H, W, C)
        self.assertEqual(tensor.shape, (12, 20, 3))

    def test_a_missing_onnx_file_raises_classifier_backend_error_not_a_bare_one(self):
        self.onnx_path.unlink()
        session = FakeOnnxSession([1.0, 0.0, 0.0])
        backend = self.build(session)
        with self.assertRaises(backend_ei.ClassifierBackendError):
            backend.classify(_bgr_crop())

    def test_no_deployed_model_raises_classifier_backend_error(self):
        rf_store.save_project(self.project_path, "ws", "hotpot-ingredients",
                              "key1", version="1", model_file=None)
        session = FakeOnnxSession([1.0, 0.0, 0.0])
        backend = self.build(session)
        with self.assertRaises(backend_ei.ClassifierBackendError):
            backend.classify(_bgr_crop())

    def test_the_class_list_is_re_read_after_the_sidecar_file_changes(self):
        session = FakeOnnxSession([0.0, 0.0, 5.0])  # index 2
        backend = self.build(session)
        label1, _ = backend.classify(_bgr_crop())
        self.assertEqual(label1, "c")

        # Redeploy: rewrite the sidecar with a different order, nudge the
        # mtime so a coarse filesystem clock cannot hide the change.
        self.classes_path.write_text(json.dumps(["x", "y", "z"]))
        os.utime(self.classes_path, (time.time() + 5, time.time() + 5))
        label2, _ = backend.classify(_bgr_crop())
        self.assertEqual(label2, "z")

    def test_a_hardcoded_class_list_would_have_missed_the_redeploy(self):
        # Same shape as backend_ei's own redeploy test: prove the class
        # list actually came from the file, not from whatever the first
        # call happened to see, by checking BOTH answers differ correctly.
        session = FakeOnnxSession([5.0, 0.0, 0.0])  # index 0
        backend = self.build(session)
        label1, _ = backend.classify(_bgr_crop())
        self.classes_path.write_text(json.dumps(["only-one"]))
        os.utime(self.classes_path, (time.time() + 5, time.time() + 5))
        session2 = FakeOnnxSession([1.0])
        # Swap the session too (a redeploy loads a new .onnx, not just a
        # new sidecar) by rebuilding the backend the same way a fresh
        # classify() after a redeploy would.
        backend._session_factory = lambda path: session2
        backend._session = None
        label2, _ = backend.classify(_bgr_crop())
        self.assertEqual(label1, "a")
        self.assertEqual(label2, "only-one")

    def test_a_score_count_mismatch_against_the_class_list_raises(self):
        session = FakeOnnxSession([1.0, 0.0])  # 2 scores, 3 classes on disk
        backend = self.build(session)
        with self.assertRaises(backend_ei.ClassifierBackendError):
            backend.classify(_bgr_crop())

    def test_the_session_is_reused_across_calls(self):
        built = []

        def factory(path):
            built.append(path)
            return FakeOnnxSession([1.0, 0.0, 0.0])
        backend = backend_rf.RoboflowOnnxBackend(
            project_path=self.project_path, models_dir=self.models_dir,
            session_factory=factory)
        backend.classify(_bgr_crop())
        backend.classify(_bgr_crop())
        self.assertEqual(len(built), 1)


if __name__ == "__main__":
    unittest.main()
