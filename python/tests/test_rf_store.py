"""Tests for classifier/rf_store.py — state/rf_project.json, the Roboflow
sibling of test_ei_store.py.

Run from the repo root:

    python -m unittest discover -s python/tests -v
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hotpot.classifier import rf_store  # noqa: E402


class TestRfStore(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "rf_project.json"

    def test_missing_file_is_none_not_an_error(self):
        self.assertIsNone(rf_store.load_project(self.path))

    def test_round_trips_what_save_wrote(self):
        rf_store.save_project(self.path, "rahuls-workspace-mqtgo",
                              "hotpot-ingredients", "sekrit",
                              version="3", model_file="hotpot-rf.onnx")
        got = rf_store.load_project(self.path)
        self.assertEqual(got["workspace"], "rahuls-workspace-mqtgo")
        self.assertEqual(got["project"], "hotpot-ingredients")
        self.assertEqual(got["api_key"], "sekrit")
        self.assertEqual(got["version"], "3")
        self.assertEqual(got["model_file"], "hotpot-rf.onnx")

    def test_version_and_model_file_default_to_none(self):
        rf_store.save_project(self.path, "ws", "proj", "key")
        got = rf_store.load_project(self.path)
        self.assertIsNone(got["version"])
        self.assertIsNone(got["model_file"])

    def test_a_file_written_before_version_existed_still_loads(self):
        # Simulates a link saved by an older build of this module that
        # never wrote `version`/`model_file` at all.
        import json
        self.path.write_text(json.dumps(
            {"workspace": "ws", "project": "proj", "api_key": "key"}))
        got = rf_store.load_project(self.path)
        self.assertIsNone(got["version"])
        self.assertIsNone(got["model_file"])

    def test_save_overwrites_a_previous_link(self):
        rf_store.save_project(self.path, "ws1", "proj1", "key1")
        rf_store.save_project(self.path, "ws2", "proj2", "key2")
        got = rf_store.load_project(self.path)
        self.assertEqual(got["workspace"], "ws2")
        self.assertEqual(got["project"], "proj2")

    def test_save_never_leaves_a_stale_version_from_a_prior_write(self):
        # save_project always writes the WHOLE record — a caller that
        # loads, bumps `version`, and re-saves without re-passing
        # `model_file` must see it dropped to None, not silently carried
        # forward from the previous write.
        rf_store.save_project(self.path, "ws", "proj", "key",
                              version="1", model_file="old.onnx")
        rf_store.save_project(self.path, "ws", "proj", "key", version="2")
        got = rf_store.load_project(self.path)
        self.assertEqual(got["version"], "2")
        self.assertIsNone(got["model_file"])

    def test_the_file_on_disk_is_plain_json(self):
        rf_store.save_project(self.path, "ws", "proj", "key")
        import json
        with open(self.path) as f:
            data = json.load(f)
        self.assertEqual(data["workspace"], "ws")

    def test_removing_nothing_reports_false(self):
        self.assertFalse(rf_store.remove_project(self.path))

    def test_removes_a_saved_link_and_reports_true(self):
        rf_store.save_project(self.path, "ws", "proj", "key")
        self.assertTrue(rf_store.remove_project(self.path))
        self.assertIsNone(rf_store.load_project(self.path))


if __name__ == "__main__":
    unittest.main()
