"""Tests for classifier/backend_stub.py and classifier/backend_ei.py — doc
section 19.4's `ClassifierBackend` Protocol, both implementations.

`backend_ei.EiCppBackend` never launches a real subprocess here: `run` is
an injection point (this module's own docstring compares it to
`WindowsCapture`'s `video_capture_factory`) built for exactly this, so no
test needs `tools/eim_cpp/build/classify.exe` to actually exist or run.

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
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402

from hotpot.classifier import backend_ei, backend_stub  # noqa: E402


def _write_metadata(path: Path, width: int, height: int) -> None:
    # A trimmed stand-in for a real model_metadata.h — only the two
    # #define lines backend_ei._InputDims actually parses.
    path.write_text(
        f"#define EI_CLASSIFIER_INPUT_WIDTH  {width}\n"
        f"#define EI_CLASSIFIER_INPUT_HEIGHT {height}\n")


class TestStubBackend(unittest.TestCase):

    def test_it_cycles_through_labels_deterministically(self):
        b = backend_stub.StubBackend(labels=["a", "b", "c"], conf=0.5)
        got = [b.classify(None)[0] for _ in range(7)]
        self.assertEqual(got, ["a", "b", "c", "a", "b", "c", "a"])

    def test_every_call_reports_the_configured_confidence(self):
        b = backend_stub.StubBackend(labels=["a"], conf=0.77)
        for _ in range(3):
            self.assertEqual(b.classify(None)[1], 0.77)

    def test_a_single_label_repeats_rather_than_erroring(self):
        b = backend_stub.StubBackend(labels=["only"])
        self.assertEqual([b.classify(None)[0] for _ in range(3)],
                         ["only", "only", "only"])

    def test_no_labels_is_refused_at_construction(self):
        # A backend that can never answer is a bug caught at boot, not a
        # ZeroDivisionError the first time core asks it something.
        with self.assertRaises(ValueError):
            backend_stub.StubBackend(labels=[])

    def test_the_default_labels_are_real_catalogue_style_ids(self):
        b = backend_stub.StubBackend()
        self.assertGreater(len(b.labels), 0)
        for label in b.labels:
            self.assertRegex(label, r"^[a-z_]+$")


class FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestEiCppBackend(unittest.TestCase):

    def setUp(self):
        self.crop = np.zeros((50, 60, 3), dtype=np.uint8)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.metadata_path = Path(self._tmp.name) / "model_metadata.h"
        _write_metadata(self.metadata_path, 160, 160)

    def build(self, run, *, binary_exists=True, metadata_path=None):
        backend = backend_ei.EiCppBackend(
            binary_path=Path(__file__),  # any real file — existence check only
            metadata_path=metadata_path or self.metadata_path,
            run=run)
        if not binary_exists:
            backend.binary_path = Path("/no/such/file/classify.exe")
        return backend

    def test_a_clean_run_returns_the_top_label(self):
        payload = json.dumps({"labels": [
            {"label": "button_mushrooms", "value": 0.12},
            {"label": "soya_chunks", "value": 0.83},
            {"label": "white_rusk", "value": 0.05},
        ]})
        backend = self.build(lambda *a, **k: FakeProc(0, payload))
        label, conf = backend.classify(self.crop)
        self.assertEqual(label, "soya_chunks")
        self.assertEqual(conf, 0.83)

    def test_the_crop_is_resized_to_whatever_metadata_h_says(self):
        # The size written must come from vendor/model-parameters/
        # model_metadata.h, not a hardcoded constant — a hardcoded value is
        # exactly the bug that let 2026-08-24's redeploy sit unzipped while
        # the running app kept resizing to the PREVIOUS model's dimensions
        # (see backend_ei.py's module-level comment). Deliberately
        # asymmetric width/height so a width/height swap would also fail.
        import struct
        _write_metadata(self.metadata_path, 120, 64)
        seen = {}

        def fake_run(args, **kwargs):
            raw = Path(args[1]).read_bytes()
            w, h = struct.unpack("<ii", raw[:8])
            seen["w"], seen["h"] = w, h
            seen["nbytes"] = len(raw) - 8
            return FakeProc(0, json.dumps(
                {"labels": [{"label": "x", "value": 1.0}]}))

        backend = self.build(fake_run)
        # BGR crop with a distinct blue channel, so a BGR->RGB mixup would
        # be visible if this test asserted on pixel values (it does not
        # need to — the size/byte-count check below is what matters here).
        crop = np.zeros((10, 10, 3), dtype=np.uint8)
        crop[:, :, 0] = 200  # B
        backend.classify(crop)
        self.assertEqual((seen["w"], seen["h"]), (120, 64))
        self.assertEqual(seen["nbytes"], 120 * 64 * 3)

    def test_a_redeploy_is_picked_up_without_recreating_the_backend(self):
        # The whole point of reading model_metadata.h per-call instead of
        # once at construction: a classifier process already running when
        # ei_deploy.rebuild() finishes must use the new model on its very
        # next classify() call, with no restart.
        import struct
        widths_seen = []

        def fake_run(args, **kwargs):
            raw = Path(args[1]).read_bytes()
            w, _h = struct.unpack("<ii", raw[:8])
            widths_seen.append(w)
            return FakeProc(0, json.dumps(
                {"labels": [{"label": "x", "value": 1.0}]}))

        backend = self.build(fake_run)
        backend.classify(self.crop)

        # A redeploy: rewrite the file Edge Impulse's own export step would
        # have overwritten, with a new mtime (some filesystems have coarse
        # mtime resolution, so nudge it explicitly rather than relying on
        # wall-clock time to have moved on between the two writes).
        _write_metadata(self.metadata_path, 224, 224)
        os.utime(self.metadata_path, (time.time() + 5, time.time() + 5))
        backend.classify(self.crop)

        self.assertEqual(widths_seen, [160, 224])

    def test_a_missing_metadata_file_raises_a_clear_error(self):
        backend = self.build(
            lambda *a, **k: FakeProc(0, "{}"),
            metadata_path=Path(self._tmp.name) / "does_not_exist.h")
        with self.assertRaises(backend_ei.ClassifierBackendError) as ctx:
            backend.classify(self.crop)
        self.assertIn("does not exist", str(ctx.exception))

    def test_a_missing_binary_raises_before_touching_the_filesystem(self):
        backend = self.build(lambda *a, **k: FakeProc(0, "{}"),
                             binary_exists=False)
        with self.assertRaises(backend_ei.ClassifierBackendError) as ctx:
            backend.classify(self.crop)
        self.assertIn("does not exist", str(ctx.exception))

    def test_a_nonzero_exit_raises_with_stderr_in_the_message(self):
        backend = self.build(lambda *a, **k: FakeProc(2, "", "bad input"))
        with self.assertRaises(backend_ei.ClassifierBackendError) as ctx:
            backend.classify(self.crop)
        self.assertIn("bad input", str(ctx.exception))

    def test_a_timeout_raises_a_clear_error(self):
        import subprocess

        def timeout(*a, **k):
            raise subprocess.TimeoutExpired(cmd="classify", timeout=3.0)
        backend = self.build(timeout)
        with self.assertRaises(backend_ei.ClassifierBackendError) as ctx:
            backend.classify(self.crop)
        self.assertIn("longer than", str(ctx.exception))

    def test_unparseable_stdout_raises_rather_than_crashing(self):
        backend = self.build(lambda *a, **k: FakeProc(0, "not json"))
        with self.assertRaises(backend_ei.ClassifierBackendError):
            backend.classify(self.crop)

    def test_stdout_missing_the_labels_key_raises(self):
        backend = self.build(lambda *a, **k: FakeProc(0, "{}"))
        with self.assertRaises(backend_ei.ClassifierBackendError):
            backend.classify(self.crop)

    def test_the_temp_file_is_cleaned_up_even_on_failure(self):
        seen_path = {}

        def fake_run(args, **kwargs):
            seen_path["p"] = Path(args[1])
            return FakeProc(1, "", "boom")
        backend = self.build(fake_run)
        with self.assertRaises(backend_ei.ClassifierBackendError):
            backend.classify(self.crop)
        self.assertFalse(seen_path["p"].exists())

    def test_windows_gets_the_exe_suffix_by_default(self):
        # _default_binary_path() is platform-sensitive; this only checks
        # the branch this dev machine's own tests actually exercise, per
        # `platform.system()` — not a claim about what a Linux CI run of
        # this same test would see (it would see the no-suffix branch,
        # correctly).
        import platform
        path = backend_ei._default_binary_path()
        if platform.system() == "Windows":
            self.assertEqual(path.suffix, ".exe")
        else:
            self.assertEqual(path.suffix, "")


if __name__ == "__main__":
    unittest.main()
