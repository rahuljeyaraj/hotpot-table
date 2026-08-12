"""Tests for core/bin_grid.py — the grid that replaced 8 independently-
dragged rects (see that module's docstring for why).

Run from the repo root:

    python -m unittest discover -s python/tests -v

Every store here is built against a throwaway directory, never the repo's
own `state/` — same rule `test_geometry_store.py` follows, and for the
same reason: this file decides where every bin is.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hotpot.common import atomicio  # noqa: E402
from hotpot.core import bin_grid as bg  # noqa: E402
from hotpot.core import geometry_store as gs  # noqa: E402


# A grid with 4 h-lines and 8 v-lines, well inside the 1920x1080 stage.
H_LINES = [100.0, 300.0, 500.0, 700.0]
V_LINES = [50.0, 250.0, 350.0, 550.0, 650.0, 850.0, 950.0, 1150.0]


class TestBinGrid(unittest.TestCase):

    def test_rects_pair_columns_and_rows_far_row_first(self):
        grid = bg.BinGrid(h_lines=H_LINES, v_lines=V_LINES)
        rects = grid.rects()
        self.assertEqual(len(rects), bg.NUM_BINS)
        # Bin 0: col 0, row 0 (far-left).
        self.assertEqual(rects[0], (50.0, 100.0, 200.0, 200.0))
        # Bin 3: col 3, row 0 (far-right).
        self.assertEqual(rects[3], (950.0, 100.0, 200.0, 200.0))
        # Bin 4: col 0, row 1 (near-left) — same v-lines as bin 0.
        self.assertEqual(rects[4], (50.0, 500.0, 200.0, 200.0))

    def test_every_bin_in_a_row_shares_its_top_and_bottom(self):
        # The entire point of a grid over 8 independent rects: there is no
        # way to drag bin 1's top edge without moving bin 0's, bin 2's and
        # bin 3's along with it — a real gap still separates the bins
        # left-to-right, but nothing can leave one bin's row visibly
        # higher or lower than its neighbours'.
        grid = bg.BinGrid(h_lines=H_LINES, v_lines=V_LINES)
        rects = grid.rects()
        for i in (1, 2, 3):
            self.assertEqual(rects[i][1], rects[0][1])
            self.assertEqual(rects[i][1] + rects[i][3], rects[0][1] + rects[0][3])

    def test_every_bin_in_a_column_shares_its_left_and_right(self):
        grid = bg.BinGrid(h_lines=H_LINES, v_lines=V_LINES)
        rects = grid.rects()
        for i in range(4):
            self.assertEqual(rects[i + 4][0], rects[i][0])
            self.assertEqual(rects[i + 4][0] + rects[i + 4][2],
                             rects[i][0] + rects[i][2])

    def test_wrong_h_line_count_is_refused(self):
        with self.assertRaises(bg.BinGridError):
            bg.BinGrid(h_lines=[1.0, 2.0], v_lines=V_LINES)

    def test_wrong_v_line_count_is_refused(self):
        with self.assertRaises(bg.BinGridError):
            bg.BinGrid(h_lines=H_LINES, v_lines=[1.0, 2.0])

    def test_a_crossed_pair_is_refused(self):
        # h_lines[0] > h_lines[1] would crop a negative-height bin.
        bad = [300.0, 100.0, 500.0, 700.0]
        with self.assertRaises(bg.BinGridError):
            bg.BinGrid(h_lines=bad, v_lines=V_LINES)

    def test_a_degenerate_pair_is_refused(self):
        # Equal, not just crossed — a zero-size bin crops an empty image.
        bad = [100.0, 100.0, 500.0, 700.0]
        with self.assertRaises(bg.BinGridError):
            bg.BinGrid(h_lines=bad, v_lines=V_LINES)

    def test_a_non_finite_line_is_refused(self):
        bad = [100.0, float("nan"), 500.0, 700.0]
        with self.assertRaises(bg.BinGridError):
            bg.BinGrid(h_lines=bad, v_lines=V_LINES)

    def test_json_round_trip(self):
        grid = bg.BinGrid(h_lines=H_LINES, v_lines=V_LINES)
        again = bg.BinGrid.from_json(grid.to_json())
        self.assertEqual(again.h_lines, grid.h_lines)
        self.assertEqual(again.v_lines, grid.v_lines)

    def test_from_json_needs_both_line_lists(self):
        with self.assertRaises(bg.BinGridError):
            bg.BinGrid.from_json({"h_lines": H_LINES})


class TestCadSeed(unittest.TestCase):

    def test_cad_grid_rects_land_inside_the_stage(self):
        for x, y, w, h in bg.cad_bin_grid_stage().rects():
            self.assertGreaterEqual(x, 0)
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(x + w, gs.STAGE_SIZE[0])
            self.assertLessEqual(y + h, gs.STAGE_SIZE[1])

    def test_cad_grid_matches_table_geometry_s_bin_zero(self):
        # BINS[0] is (92mm, 177mm, 200mm, 255mm) — cross-check the grid's
        # own derivation against geometry_store's independently-defined
        # mm_to_stage, the same discipline test_geometry_store.py's
        # TestTableGeometryMirror uses.
        want_x, want_y = gs.mm_to_stage(92.0, 177.0)
        want_x1, want_y1 = gs.mm_to_stage(92.0 + 200.0, 177.0 + 255.0)
        rect = bg.cad_bin_grid_stage().rects()[0]
        self.assertAlmostEqual(rect[0], want_x, places=3)
        self.assertAlmostEqual(rect[1], want_y, places=3)
        self.assertAlmostEqual(rect[0] + rect[2], want_x1, places=3)
        self.assertAlmostEqual(rect[1] + rect[3], want_y1, places=3)


class TestLegacySeed(unittest.TestCase):
    """Doc section 21 M4 build item 5 and doc section 7.1's "the measured
    values … become the seed" — now pure line arithmetic, see bin_grid.py's
    module docstring for what changed and why.
    """

    OFFSETS = {"hLineDeltaMM": [5.0, -3.0, 5.0, -5.0],
               "offsetXMM": -4.0, "offsetYMM": 3.0,
               "vLineDeltaMM": [0.0, -4.0, 2.0, -4.0, 2.0, -6.0, 3.0, 0.0]}

    def test_the_repo_s_own_legacy_file_still_parses(self):
        raw = atomicio.read_json(gs.LEGACY_OFFSETS_PATH, None)
        self.assertIsInstance(raw, dict)
        self.assertEqual(len(raw.get("hLineDeltaMM", [])), bg.NUM_H_LINES)
        self.assertEqual(len(raw.get("vLineDeltaMM", [])), bg.NUM_V_LINES)

    def test_the_seed_nudges_the_cad_lines_rather_than_replacing_them(self):
        cad = bg.cad_bin_grid_stage()
        legacy = bg.legacy_bin_grid_stage(self.OFFSETS)
        # Every delta in the file is under 7mm (~9px on this stage scale)
        # — a nudge, not a relayout. No boxing-twice growth exists any
        # more (this module's docstring), so the tolerance is tight.
        for c, l in zip(cad.h_lines, legacy.h_lines):
            self.assertLess(abs(c - l), 15.0)
        for c, l in zip(cad.v_lines, legacy.v_lines):
            self.assertLess(abs(c - l), 15.0)

    def test_the_offsets_actually_change_something(self):
        cad = bg.cad_bin_grid_stage()
        legacy = bg.legacy_bin_grid_stage(self.OFFSETS)
        self.assertNotEqual([round(v, 3) for v in cad.h_lines],
                            [round(v, 3) for v in legacy.h_lines])

    def test_a_malformed_offsets_file_falls_back_to_the_cad_layout(self):
        got = bg.legacy_bin_grid_stage({"hLineDeltaMM": [1.0], "vLineDeltaMM": []})
        cad = bg.cad_bin_grid_stage()
        self.assertEqual(got.h_lines, cad.h_lines)
        self.assertEqual(got.v_lines, cad.v_lines)

    def test_seeding_needs_no_homography(self):
        # The whole point of a grid seed over the old rect seed: this is
        # pure line-position arithmetic in stage px, so it works with no
        # camera solved at all — unlike the rect version's H^-1 round trip.
        grid = bg.legacy_bin_grid_stage(self.OFFSETS)
        self.assertEqual(len(grid.h_lines), bg.NUM_H_LINES)
        self.assertEqual(len(grid.v_lines), bg.NUM_V_LINES)


class StoreCase(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "bin_grid_camera.json"

    def store(self):
        return bg.BinGridStore(self.path)


class TestBinGridStore(StoreCase):

    def test_a_fresh_store_has_no_grid(self):
        s = self.store()
        self.assertFalse(s.has_grid)
        self.assertEqual(s.rects(), [None] * bg.NUM_BINS)

    def test_set_grid_does_not_save(self):
        s = self.store()
        s.set_grid(H_LINES, V_LINES)
        self.assertTrue(s.has_grid)
        self.assertFalse(self.path.exists())

    def test_save_then_reload(self):
        s = self.store()
        s.set_grid(H_LINES, V_LINES)
        s.save()
        again = self.store()
        self.assertTrue(again.has_grid)
        self.assertEqual(again.grid.h_lines, H_LINES)
        self.assertEqual(again.grid.v_lines, V_LINES)

    def test_saving_with_no_grid_is_refused(self):
        with self.assertRaises(bg.BinGridError):
            self.store().save()
        self.assertFalse(self.path.exists())

    def test_a_corrupt_file_loads_as_no_grid_rather_than_crashing(self):
        atomicio.write_json(self.path, {"schema": 1, "h_lines": "nope"})
        s = self.store()
        self.assertFalse(s.has_grid)

    def test_seed_from_table_does_not_save(self):
        s = self.store()
        s.seed_from_table()
        self.assertTrue(s.has_grid)
        self.assertFalse(self.path.exists())

    def test_seeding_needs_no_prior_grid_or_homography(self):
        # Unlike the old rect seed (geometry_store.seed_cam_rects_from_table),
        # this never raises for lack of a homography — see bin_grid.py's
        # module docstring.
        s = self.store()
        grid = s.seed_from_table()
        self.assertEqual(len(grid.rects()), bg.NUM_BINS)

    def test_setting_a_grid_clears_verification(self):
        s = self.store()
        s.set_grid(H_LINES, V_LINES)
        s.mark_verified()
        s.set_grid(H_LINES, V_LINES)
        self.assertIsNone(s.verified_at)

    def test_verification_survives_a_save_and_reload(self):
        s = self.store()
        s.set_grid(H_LINES, V_LINES)
        s.mark_verified(when=1754838400.0)
        s.save()
        again = self.store()
        self.assertEqual(again.verified_at, 1754838400.0)

    def test_the_saved_file_matches_the_documented_shape(self):
        s = self.store()
        s.set_grid(H_LINES, V_LINES)
        s.save()
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        for key in ("schema", "written", "verified_at", "h_lines", "v_lines"):
            self.assertIn(key, raw)
        self.assertEqual(raw["h_lines"], H_LINES)
        self.assertEqual(raw["v_lines"], V_LINES)


if __name__ == "__main__":
    unittest.main()
