"""Tests for core/binmap.py — M1 build item 2 (doc section 21).

Run from the repo root:

    python -m unittest discover -s python/tests -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hotpot.core import binmap  # noqa: E402


class TestBinDefaults(unittest.TestCase):

    def test_fresh_bin_is_unset_and_unresolved(self):
        b = binmap.Bin(i=3)
        self.assertIsNone(b.item_id)
        self.assertEqual(b.conf, 0.0)
        self.assertEqual(b.source, "unset")


class TestBinMapConstruction(unittest.TestCase):

    def test_default_construction_is_eight_unresolved_bins(self):
        """Doc section 4.3: 'bins always has exactly 8 entries.'"""
        bm = binmap.BinMap()
        self.assertEqual(len(bm.bins), 8)
        self.assertEqual([b.i for b in bm.bins], list(range(8)))
        for i in range(8):
            self.assertFalse(bm.resolved(i))

    def test_wrong_bin_count_raises(self):
        with self.assertRaises(ValueError):
            binmap.BinMap([binmap.Bin(i=0), binmap.Bin(i=1)])


class TestResolved(unittest.TestCase):
    """Doc section 9.3: unresolved <=> item_id is None or conf < conf_floor."""

    def setUp(self):
        self.bm = binmap.BinMap()

    def test_no_item_id_is_unresolved_regardless_of_confidence(self):
        self.bm.set_bin(0, item_id=None, conf=0.99, source="classifier")
        self.assertFalse(self.bm.resolved(0))

    def test_confidence_at_or_above_floor_is_resolved(self):
        self.bm.set_bin(0, item_id="mushroom", conf=0.65, source="classifier")
        self.assertTrue(self.bm.resolved(0, conf_floor=0.65))

    def test_confidence_below_floor_is_unresolved(self):
        self.bm.set_bin(0, item_id="mushroom", conf=0.64, source="classifier")
        self.assertFalse(self.bm.resolved(0, conf_floor=0.65))

    def test_default_conf_floor_matches_doc_8_6(self):
        self.bm.set_bin(0, item_id="mushroom", conf=0.60, source="classifier")
        self.assertFalse(self.bm.resolved(0))          # default floor 0.65
        self.bm.set_bin(0, item_id="mushroom", conf=0.70, source="classifier")
        self.assertTrue(self.bm.resolved(0))

    def test_set_bin_replaces_the_row_wholesale(self):
        """A stale conf/source pair from a previous assignment must never
        survive alongside a new item_id.
        """
        self.bm.set_bin(0, item_id="mushroom", conf=0.9, source="classifier")
        self.bm.set_bin(0, item_id=None, conf=0.0, source="unset")
        b = self.bm.bins[0]
        self.assertIsNone(b.item_id)
        self.assertEqual(b.conf, 0.0)
        self.assertEqual(b.source, "unset")


class TestJsonRoundTrip(unittest.TestCase):

    def test_round_trip_preserves_every_field(self):
        bm = binmap.BinMap(locked=True)
        bm.set_bin(0, item_id="mushroom", conf=0.94, source="classifier")
        bm.set_bin(1, item_id=None, conf=0.31, source="classifier")
        back = binmap.BinMap.from_json(bm.to_json())
        self.assertTrue(back.locked)
        self.assertEqual(back.bins[0], bm.bins[0])
        self.assertEqual(back.bins[1], bm.bins[1])

    def test_shape_matches_doc_8_2(self):
        bm = binmap.BinMap()
        bm.set_bin(0, item_id="mushroom", conf=0.94, source="classifier")
        raw = bm.to_json()
        self.assertEqual(raw["schema"], 3)
        self.assertIn("written", raw)
        self.assertIn("locked", raw)
        self.assertEqual(len(raw["bins"]), 8)
        self.assertEqual(raw["bins"][0],
                          {"i": 0, "item_id": "mushroom", "conf": 0.94, "source": "classifier"})

    def test_from_json_fills_missing_indices_with_defaults(self):
        """A hand-edited or truncated file naming only some bins must not
        silently drop the rest — they come back unresolved, not absent.
        """
        raw = {"schema": 3, "written": 0.0, "locked": False,
               "bins": [{"i": 5, "item_id": "tofu", "conf": 0.8, "source": "manual"}]}
        bm = binmap.BinMap.from_json(raw)
        self.assertEqual(len(bm.bins), 8)
        self.assertEqual(bm.bins[5].item_id, "tofu")
        for i in [0, 1, 2, 3, 4, 6, 7]:
            self.assertIsNone(bm.bins[i].item_id)

    def test_from_json_ignores_out_of_range_indices(self):
        raw = {"bins": [{"i": 99, "item_id": "ghost", "conf": 1.0, "source": "manual"}]}
        bm = binmap.BinMap.from_json(raw)          # must not raise
        self.assertEqual(len(bm.bins), 8)
        self.assertTrue(all(b.item_id is None for b in bm.bins))


class TestPersistence(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "state", "bin_map.json")

    def test_missing_file_loads_as_a_fresh_unresolved_map(self):
        """Doc section 9.1: a fresh clone with an empty state/ is normal."""
        bm = binmap.BinMap.load(self.path)
        self.assertEqual(len(bm.bins), 8)
        self.assertFalse(bm.resolved(0))
        self.assertFalse(bm.locked)

    def test_save_then_load_round_trips(self):
        bm = binmap.BinMap(locked=True)
        bm.set_bin(2, item_id="egg", conf=0.88, source="classifier")
        bm.save(self.path)

        loaded = binmap.BinMap.load(self.path)
        self.assertTrue(loaded.locked)
        self.assertEqual(loaded.bins[2].item_id, "egg")
        self.assertTrue(loaded.resolved(2))

    def test_save_is_atomic_and_leaves_no_temp_file(self):
        binmap.BinMap().save(self.path)
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.path))),
                          ["bin_map.json"])


if __name__ == "__main__":
    unittest.main()
