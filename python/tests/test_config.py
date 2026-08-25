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


class TestResolveBrowserHost(unittest.TestCase):
    """Developer, 2026-08-25: "the qr code is showing some local host url
    which is not reachable in my phone even if it is in same wifi
    network."

    `camera.host_for_browser` names the host a browser on somebody else's
    device types — a tablet on the Live tab, a phone scanning the
    projected QR. `localhost` there is never an answer to that question,
    so it is treated as "work it out" rather than honoured.

    `lan_ip()` is faked in every case below: the real one asks this
    machine's routing table, so a test that called it would pass or fail
    on whether the machine running the suite happens to be on a network.
    """

    def _with_lan_ip(self, value):
        real = config.lan_ip
        config.lan_ip = lambda: value
        self.addCleanup(setattr, config, "lan_ip", real)

    def test_a_real_host_is_left_alone(self):
        # The opt-out: a rig with a DNS name or a pinned static address
        # must not have it second-guessed.
        self._with_lan_ip("192.168.1.9")
        for host in ("odyssey.local", "10.0.0.5", "hotpot.example.com"):
            with self.subTest(host=host):
                self.assertEqual(config.resolve_browser_host(host), host)

    def test_every_auto_spelling_resolves(self):
        self._with_lan_ip("192.168.1.9")
        for host in ("auto", "AUTO", "localhost", "127.0.0.1", "", None,
                     "  localhost  "):
            with self.subTest(host=host):
                self.assertEqual(config.resolve_browser_host(host),
                                 "192.168.1.9")

    def test_no_lan_address_falls_back_to_localhost(self):
        # Allowed to fail, and it must say so — the alternative is a QR
        # with an invented address in it, which is wrong in exactly the
        # same way and harder to notice.
        self._with_lan_ip(None)
        with self.assertLogs("hotpot.config", level="WARNING") as logs:
            self.assertEqual(config.resolve_browser_host("auto"), "localhost")
        self.assertTrue(any("localhost" in line for line in logs.output))


class TestLanIp(unittest.TestCase):

    def test_it_never_answers_loopback(self):
        """The one property that matters and can be checked anywhere: a
        loopback answer is the bug this function exists to remove, so it
        returns None instead. On a machine with no network at all that is
        the honest answer, and `resolve_browser_host` handles it.
        """
        got = config.lan_ip()
        if got is not None:
            self.assertFalse(got.startswith("127."), got)


if __name__ == "__main__":
    unittest.main()
