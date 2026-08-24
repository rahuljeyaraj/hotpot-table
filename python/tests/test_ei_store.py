"""Tests for classifier/ei_store.py -- the on-disk record of the linked
Edge Impulse project.

Run from the repo root:

    python -m unittest discover -s python/tests -v
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hotpot.classifier import ei_store  # noqa: E402


class TempPathCase(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "ei_project.json"


class TestLoadProject(TempPathCase):

    def test_missing_file_is_none_not_an_error(self):
        self.assertIsNone(ei_store.load_project(self.path))

    def test_round_trips_what_save_wrote(self):
        ei_store.save_project(self.path, 1087506, "ei_xyz", "hotpot-ingredients")
        got = ei_store.load_project(self.path)
        self.assertEqual(got, {"project_id": 1087506, "api_key": "ei_xyz",
                               "project_name": "hotpot-ingredients"})

    def test_save_overwrites_a_previous_link(self):
        ei_store.save_project(self.path, 1, "ei_a", "old-name")
        ei_store.save_project(self.path, 2, "ei_b", "new-name")
        got = ei_store.load_project(self.path)
        self.assertEqual(got["project_id"], 2)
        self.assertEqual(got["api_key"], "ei_b")
        self.assertEqual(got["project_name"], "new-name")

    def test_the_file_on_disk_is_plain_json(self):
        ei_store.save_project(self.path, 1087506, "ei_xyz", "hotpot-ingredients")
        with open(self.path) as f:
            raw = json.load(f)
        self.assertEqual(raw["project_id"], 1087506)


class TestRemoveProject(TempPathCase):

    def test_removing_nothing_reports_false(self):
        self.assertFalse(ei_store.remove_project(self.path))

    def test_removes_a_saved_link_and_reports_true(self):
        ei_store.save_project(self.path, 1087506, "ei_xyz", "hotpot-ingredients")
        self.assertTrue(ei_store.remove_project(self.path))
        self.assertIsNone(ei_store.load_project(self.path))


if __name__ == "__main__":
    unittest.main()
