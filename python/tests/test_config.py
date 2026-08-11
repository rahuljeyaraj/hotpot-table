"""Tests for common/config.py.

Run from the repo root:

    python -m unittest discover -s python/tests -v

`load()`'s two jobs: seed `system.json` from the default on first run, and
deep-merge an existing one over the default so an old file still gets new
keys. Both are exercised against a throwaway directory, never the repo's
own `config/`.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hotpot.common import config  # noqa: E402


class TempDirCase(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.default_path = Path(self.dir.name) / "system.default.json"
        self.cfg_path = Path(self.dir.name) / "system.json"

    def write_default(self, obj):
        self.default_path.write_text(json.dumps(obj), encoding="utf-8")


class TestSeeding(TempDirCase):

    def test_missing_config_is_seeded_from_default(self):
        self.write_default({"camera": {"mjpeg_port": 8081}})
        cfg = config.load(self.cfg_path, self.default_path)
        self.assertEqual(cfg["camera"]["mjpeg_port"], 8081)
        self.assertTrue(self.cfg_path.exists())
        self.assertEqual(json.loads(self.cfg_path.read_text(encoding="utf-8")),
                         {"camera": {"mjpeg_port": 8081}})

    def test_seeding_does_not_touch_an_existing_config(self):
        self.write_default({"camera": {"mjpeg_port": 8081}})
        self.cfg_path.write_text(json.dumps({"camera": {"mjpeg_port": 9000}}),
                                 encoding="utf-8")
        config.load(self.cfg_path, self.default_path)
        self.assertEqual(
            json.loads(self.cfg_path.read_text(encoding="utf-8")),
            {"camera": {"mjpeg_port": 9000}})


class TestMerge(TempDirCase):

    def test_local_value_overrides_default(self):
        self.write_default({"camera": {"mjpeg_port": 8081, "fps": 30}})
        self.cfg_path.write_text(json.dumps({"camera": {"mjpeg_port": 9000}}),
                                 encoding="utf-8")
        cfg = config.load(self.cfg_path, self.default_path)
        self.assertEqual(cfg["camera"]["mjpeg_port"], 9000)

    def test_a_key_the_default_added_survives_an_older_local_file(self):
        self.write_default({"camera": {"mjpeg_port": 8081, "mjpeg_fps": 8}})
        self.cfg_path.write_text(json.dumps({"camera": {"mjpeg_port": 9000}}),
                                 encoding="utf-8")
        cfg = config.load(self.cfg_path, self.default_path)
        self.assertEqual(cfg["camera"]["mjpeg_fps"], 8)

    def test_a_whole_top_level_section_only_in_local_is_kept(self):
        self.write_default({"camera": {"mjpeg_port": 8081}})
        self.cfg_path.write_text(json.dumps({"dev": {"panel_enabled": True}}),
                                 encoding="utf-8")
        cfg = config.load(self.cfg_path, self.default_path)
        self.assertEqual(cfg["dev"]["panel_enabled"], True)
        self.assertEqual(cfg["camera"]["mjpeg_port"], 8081)


class TestGet(unittest.TestCase):

    def test_dotted_path(self):
        cfg = {"camera": {"mjpeg_port": 8081}}
        self.assertEqual(config.get(cfg, "camera.mjpeg_port"), 8081)

    def test_missing_path_returns_default(self):
        cfg = {"camera": {"mjpeg_port": 8081}}
        self.assertIsNone(config.get(cfg, "camera.device"))
        self.assertEqual(config.get(cfg, "camera.device", "/dev/video0"),
                         "/dev/video0")

    def test_missing_top_level_returns_default(self):
        cfg = {"camera": {"mjpeg_port": 8081}}
        self.assertEqual(config.get(cfg, "of.field_level", 1.0), 1.0)

    def test_path_through_a_non_dict_returns_default(self):
        cfg = {"camera": {"mjpeg_port": 8081}}
        self.assertIsNone(config.get(cfg, "camera.mjpeg_port.nested"))


if __name__ == "__main__":
    unittest.main()
