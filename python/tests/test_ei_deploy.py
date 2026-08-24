"""Tests for classifier/ei_deploy.py — the unzip-over-vendor / rebuild
steps `core/main.py`'s `_handle_ei_download` now drives automatically
(test_core_main.py's TestEdgeImpulseTab covers THAT wiring, against a
FakeEiDeploy; this file is the real implementation, in isolation).

`rebuild()` itself is not exercised here — it shells out to a real MSVC
build (rebuild.bat) that takes real minutes and needs Visual Studio
installed, which a unit test must not depend on. Its subprocess-call shape
(non-zero exit -> EiDeployError with the output attached, a missing script
-> EiDeployError, a non-Windows platform -> EiDeployError) is covered
against a fake `subprocess.run`-shaped callable via monkeypatching
`ei_deploy.subprocess.run`, the same seam backend_ei.py's `run=` gives
EiCppBackend, since ei_deploy.rebuild() doesn't expose one of its own (it
has exactly one real caller, core/main.py, which is why DI happens at the
module level here instead).
"""

from __future__ import annotations

import io
import os
import platform
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hotpot.classifier import ei_deploy  # noqa: E402


def _make_zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


class TestUnzipOverVendor(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vendor_dir = Path(self._tmp.name) / "vendor"

    def test_extracts_a_fresh_zip_into_an_absent_vendor_dir(self):
        zip_bytes = _make_zip({
            "model-parameters/model_metadata.h": "#define X 1\n",
            "tflite-model/tflite_learn_1095598_3_compiled.cpp": "// model\n",
        })
        ei_deploy.unzip_over_vendor(zip_bytes, self.vendor_dir)
        self.assertEqual(
            (self.vendor_dir / "model-parameters" / "model_metadata.h").read_text(),
            "#define X 1\n")
        self.assertTrue((self.vendor_dir / "tflite-model"
                         / "tflite_learn_1095598_3_compiled.cpp").exists())

    def test_a_redeploy_removes_the_previous_models_stale_source_file(self):
        # The bug this function exists to prevent: CMakeLists.txt globs
        # tflite-model/*.cpp, so leaving the OLD model's differently-named
        # .cpp sitting next to the new one would compile/link both.
        old_zip = _make_zip({
            "tflite-model/tflite_learn_1087506_5_compiled.cpp": "// old\n",
            "model-parameters/model_metadata.h": "#define OLD 1\n",
        })
        ei_deploy.unzip_over_vendor(old_zip, self.vendor_dir)
        self.assertTrue((self.vendor_dir / "tflite-model"
                         / "tflite_learn_1087506_5_compiled.cpp").exists())

        new_zip = _make_zip({
            "tflite-model/tflite_learn_1095598_3_compiled.cpp": "// new\n",
            "model-parameters/model_metadata.h": "#define NEW 1\n",
        })
        ei_deploy.unzip_over_vendor(new_zip, self.vendor_dir)

        self.assertFalse((self.vendor_dir / "tflite-model"
                          / "tflite_learn_1087506_5_compiled.cpp").exists())
        self.assertTrue((self.vendor_dir / "tflite-model"
                         / "tflite_learn_1095598_3_compiled.cpp").exists())

    def test_a_path_traversal_entry_is_refused_before_touching_vendor_dir(self):
        (self.vendor_dir).mkdir(parents=True)
        sentinel = self.vendor_dir / "keep-me.txt"
        sentinel.write_text("still here?")

        evil_zip = _make_zip({"../../evil.txt": "pwned"})
        with self.assertRaises(ei_deploy.EiDeployError):
            ei_deploy.unzip_over_vendor(evil_zip, self.vendor_dir)

        # Refused BEFORE the wipe -- an existing vendor/ survives a bad zip
        # rather than being deleted and left half-populated.
        self.assertTrue(sentinel.exists())


class TestRebuild(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.eim_cpp_dir = Path(self._tmp.name)
        (self.eim_cpp_dir / "rebuild.bat").write_text("@echo off\n")

    def test_a_missing_rebuild_script_raises_before_any_subprocess_call(self):
        empty_dir = Path(self._tmp.name) / "no-script-here"
        empty_dir.mkdir()
        with mock.patch.object(ei_deploy.subprocess, "run") as run:
            with self.assertRaises(ei_deploy.EiDeployError):
                ei_deploy.rebuild(empty_dir)
            run.assert_not_called()

    @unittest.skipUnless(platform.system() == "Windows",
                         "rebuild() only knows the MSVC path")
    def test_a_nonzero_exit_raises_with_the_scripts_output_attached(self):
        fake_result = mock.Mock(returncode=1, stdout="configuring...\n",
                                stderr="cmake configure failed\n")
        with mock.patch.object(ei_deploy.subprocess, "run",
                               return_value=fake_result) as run:
            with self.assertRaises(ei_deploy.EiDeployError) as ctx:
                ei_deploy.rebuild(self.eim_cpp_dir)
            self.assertIn("cmake configure failed", str(ctx.exception))
        run.assert_called_once()

    @unittest.skipUnless(platform.system() == "Windows",
                         "rebuild() only knows the MSVC path")
    def test_a_clean_run_calls_on_output_and_does_not_raise(self):
        fake_result = mock.Mock(returncode=0, stdout="rebuild.bat: OK\n",
                                stderr="")
        seen = []
        with mock.patch.object(ei_deploy.subprocess, "run",
                               return_value=fake_result):
            ei_deploy.rebuild(self.eim_cpp_dir, on_output=seen.append)
        self.assertIn("rebuild.bat: OK\n", seen)

    def test_non_windows_is_refused_without_attempting_a_subprocess_call(self):
        with mock.patch.object(ei_deploy.platform, "system",
                               return_value="Linux"):
            with mock.patch.object(ei_deploy.subprocess, "run") as run:
                with self.assertRaises(ei_deploy.EiDeployError):
                    ei_deploy.rebuild(self.eim_cpp_dir)
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
