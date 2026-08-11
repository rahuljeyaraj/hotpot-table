"""Tests for core/geometry_store.py — M4 build item 1 (doc sections 5.3,
8.4, 8.5, 9.1).

Run from the repo root:

    python -m unittest discover -s python/tests -v

Every store here is built against a throwaway directory, never the repo's
own `state/` — the same rule `test_calibrator.py` follows for
`loadcell_cal.json`, and for the same reason: these two files decide where
every bin rect is, so a test run that clobbered them would silently
un-calibrate a rig.

**What is deliberately NOT tested here:** that the homography points the
right way. Doc section 5.3 says outright that a reprojection check passes
by construction, and there is no code in `geometry_store.py` that claims
to do it. `test_the_store_has_no_verify_method` below is the check that
nobody adds one later.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hotpot.common import atomicio, geometry  # noqa: E402
from hotpot.core import geometry_store as gs  # noqa: E402


# Camera -> stage, with real perspective in it (see test_geometry.py).
CAM_TO_STAGE = [
    [1.1, 0.05, 30.0],
    [-0.04, 1.2, -20.0],
    [0.00012, 0.00007, 1.0],
]


class StoreCase(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.h_path = Path(self.dir.name) / "homography.json"
        self.r_path = Path(self.dir.name) / "bin_rects.json"

    def store(self):
        return gs.GeometryStore(homography_path=self.h_path,
                                rects_path=self.r_path)

    def calibrated_store(self):
        s = self.store()
        s.set_homography(CAM_TO_STAGE, rms_px=1.4, n_points=15,
                         keystone_fingerprint="abc123",
                         camera_size=(1920, 1080))
        s.set_cam_rects([(100 + 50 * i, 200, 300, 220) for i in range(8)])
        return s


class TestFirstBoot(StoreCase):
    """Doc section 9.1: "this is the first-boot path and it must work on a
    fresh clone with an empty `state/`."
    """

    def test_an_empty_state_dir_is_uncalibrated_and_does_not_raise(self):
        s = self.store()
        self.assertFalse(s.calibrated)
        self.assertFalse(s.has_homography)
        self.assertFalse(s.has_rects)
        self.assertIsNone(s.h)

    def test_stage_rects_are_all_none_without_a_homography(self):
        s = self.store()
        s.set_cam_rects([(10, 10, 20, 20)] * 8)
        # The rects exist; the space to put them in does not. oF must get
        # an absence and fall back to the CAD layout, not a rect at 10,10.
        self.assertTrue(s.has_rects)
        self.assertEqual(s.stage_rects, [None] * 8)

    def test_a_homography_with_no_rects_is_still_uncalibrated(self):
        s = self.store()
        s.set_homography(CAM_TO_STAGE)
        self.assertTrue(s.has_homography)
        self.assertFalse(s.calibrated)

    def test_seven_rects_and_a_hole_is_not_calibrated(self):
        s = self.calibrated_store()
        s.set_cam_rect(5, None)
        self.assertFalse(s.has_rects)
        self.assertFalse(s.calibrated)


class TestDerivedStageRects(StoreCase):

    def test_setting_a_camera_rect_derives_its_stage_rect(self):
        s = self.calibrated_store()
        cam = s.cam_rects[3]
        want = geometry.apply_rect(CAM_TO_STAGE, cam)
        for got, w in zip(s.stage_rects[3], want):
            self.assertAlmostEqual(got, w)

    def test_installing_a_homography_re_derives_every_stage_rect(self):
        s = self.store()
        s.set_cam_rects([(100, 100, 200, 200)] * 8)
        self.assertEqual(s.stage_rects, [None] * 8)
        s.set_homography(CAM_TO_STAGE)
        self.assertTrue(all(r is not None for r in s.stage_rects))

    def test_a_zero_width_rect_is_refused(self):
        # A zero-width rect crops an empty image, which reaches the
        # classifier as a blank and comes back as a confident wrong label.
        s = self.calibrated_store()
        with self.assertRaises(ValueError):
            s.set_cam_rect(0, (10, 10, 0, 50))

    def test_stage_rects_are_never_written_to_disk(self):
        # Doc section 8.4: "Stage-space rects are derived at load time and
        # never persisted — persisting a derived value invites the two
        # copies to disagree."
        s = self.calibrated_store()
        s.save_rects()
        raw = json.loads(self.r_path.read_text(encoding="utf-8"))
        self.assertNotIn("stage", json.dumps(raw))
        for entry in raw["bins"]:
            self.assertEqual(set(entry) - {"i", "cam"}, set())


class TestPersistence(StoreCase):

    def test_a_saved_homography_reloads_identically(self):
        s = self.calibrated_store()
        s.save_homography()
        again = self.store()
        self.assertTrue(again.has_homography)
        self.assertEqual(again.n_points, 15)
        self.assertAlmostEqual(again.rms_px, 1.4)
        self.assertEqual(again.keystone_fingerprint, "abc123")
        for row_a, row_b in zip(again.h, CAM_TO_STAGE):
            for a, b in zip(row_a, row_b):
                self.assertAlmostEqual(a, b)

    def test_saved_rects_reload_and_re_derive(self):
        s = self.calibrated_store()
        s.save_homography()
        s.save_rects()
        again = self.store()
        self.assertTrue(again.calibrated)
        for got, want in zip(again.cam_rects, s.cam_rects):
            for a, b in zip(got, want):
                self.assertAlmostEqual(a, b, places=2)
        self.assertTrue(all(r is not None for r in again.stage_rects))

    def test_the_homography_file_matches_doc_8_5s_schema(self):
        s = self.calibrated_store()
        s.save_homography()
        raw = json.loads(self.h_path.read_text(encoding="utf-8"))
        self.assertEqual(raw["schema"], 3)
        for key in ("H_cam_to_stage", "computed_at", "n_points", "rms_px",
                    "keystone_fingerprint", "camera_size", "stage_size"):
            self.assertIn(key, raw)
        self.assertEqual(raw["stage_size"], [1920, 1080])

    def test_saving_a_partial_rect_set_is_refused(self):
        # Six rects on disk would load as "the file exists" on the next
        # boot and take the table straight to IDLE with two bins unplaced.
        s = self.calibrated_store()
        s.set_cam_rect(2, None)
        with self.assertRaises(geometry.GeometryError):
            s.save_rects()
        self.assertFalse(self.r_path.exists())

    def test_saving_with_no_homography_is_refused(self):
        with self.assertRaises(geometry.GeometryError):
            self.store().save_homography()

    def test_a_corrupt_homography_file_boots_uncalibrated_rather_than_crashing(self):
        atomicio.write_json(self.h_path, {"schema": 3,
                                          "H_cam_to_stage": "not a matrix"})
        s = self.store()
        self.assertFalse(s.calibrated)

    def test_a_singular_homography_on_disk_boots_uncalibrated(self):
        # Two identical rows: it loads, parses, and cannot be inverted.
        # "The file is there but nonsense" has to land in the same place as
        # "the file is not there", or a fresh clone crashes at boot.
        atomicio.write_json(self.h_path, {
            "schema": 3,
            "H_cam_to_stage": [[1, 2, 3], [1, 2, 3], [4, 5, 6]],
        })
        s = self.store()
        self.assertFalse(s.has_homography)


class TestKeystoneStaleness(StoreCase):
    """Doc section 8.5: "oF reports its fingerprint in `stat`; if it
    differs from the one recorded here, core raises 'calibration stale —
    keystone changed'."
    """

    def test_a_changed_fingerprint_is_stale(self):
        self.assertTrue(self.calibrated_store().keystone_is_stale("different"))

    def test_the_same_fingerprint_is_not_stale(self):
        self.assertFalse(self.calibrated_store().keystone_is_stale("abc123"))

    def test_an_unknown_live_fingerprint_is_not_stale(self):
        # oF has not connected yet. Shouting "calibration stale" every time
        # the table is slow to start teaches the operator to ignore it.
        s = self.calibrated_store()
        self.assertFalse(s.keystone_is_stale(None))
        self.assertFalse(s.keystone_is_stale(""))

    def test_an_uncalibrated_table_is_not_stale(self):
        self.assertFalse(self.store().keystone_is_stale("anything"))


class TestHumanVerification(StoreCase):
    """Doc section 12.6's Verify step and doc section 5.3's TRAP."""

    def test_the_store_has_no_verify_method(self):
        # The whole point. A `verify()` that reprojected the stage rects
        # through the same H would pass on a homography that is upside
        # down — it is the definition of an inverse, not evidence. This
        # test exists so the method cannot quietly reappear.
        for name in ("verify", "check_homography", "verify_rects",
                     "self_check"):
            self.assertFalse(hasattr(gs.GeometryStore, name),
                             f"GeometryStore grew a {name}() — doc section "
                             "5.3's TRAP says this cannot be verified in code")

    def test_marking_verified_records_a_timestamp_and_survives_a_save(self):
        s = self.calibrated_store()
        self.assertIsNone(s.verified_at)
        s.mark_verified(when=1754838400.0)
        s.save_rects()
        again = self.store()
        self.assertEqual(again.verified_at, 1754838400.0)

    def test_clearing_verification_sticks(self):
        s = self.calibrated_store()
        s.mark_verified()
        s.clear_verified()
        s.save_rects()
        self.assertIsNone(self.store().verified_at)


class TestTableGeometryMirror(unittest.TestCase):
    """The C++ header `TableGeometry.h` holds the same numbers and enforces
    them with `static_assert`. These are the Python half of that pair — an
    edit made on one side and not the other fails here rather than moving
    four trays 50 mm on the rig.
    """

    def test_the_x_chain_spans_the_table(self):
        # 92 + 200 + 50 + 200 + 440 + 200 + 50 + 200 + 92 = 1524
        left = gs.BIN_ORIGINS_MM[0][0]
        right_edge = gs.BIN_ORIGINS_MM[3][0] + gs.BIN_W_MM
        self.assertAlmostEqual(left, 92.0)
        self.assertAlmostEqual(right_edge + 92.0, gs.TABLE_W_MM, places=2)

    def test_the_y_chain_spans_the_table(self):
        # 177 + 255 + 50 + 255 + 177.4 = 914.4
        far_top = gs.BIN_ORIGINS_MM[0][1]
        near_bottom = gs.BIN_ORIGINS_MM[4][1] + gs.BIN_H_MM
        self.assertAlmostEqual(far_top, 177.0)
        self.assertAlmostEqual(near_bottom + 177.4, gs.TABLE_H_MM, places=2)

    def test_the_pot_gap_is_440mm(self):
        gap = gs.BIN_ORIGINS_MM[2][0] - (gs.BIN_ORIGINS_MM[1][0] + gs.BIN_W_MM)
        self.assertAlmostEqual(gap, 440.0)

    def test_the_row_gap_is_50mm(self):
        gap = gs.BIN_ORIGINS_MM[4][1] - (gs.BIN_ORIGINS_MM[0][1] + gs.BIN_H_MM)
        self.assertAlmostEqual(gap, 50.0)

    def test_cad_rects_land_inside_the_stage(self):
        for x, y, w, h in gs.cad_bin_rects_stage():
            self.assertGreaterEqual(x, 0)
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(x + w, gs.STAGE_SIZE[0])
            self.assertLessEqual(y + h, gs.STAGE_SIZE[1])

    def test_mm_to_stage_uses_two_different_scales(self):
        # The table's aspect (1.667) is not the projector's (1.778). One
        # uniform scale would put the near row ~50 mm out.
        x, _ = gs.mm_to_stage(100.0, 0.0)
        _, y = gs.mm_to_stage(0.0, 100.0)
        self.assertNotAlmostEqual(x, y, places=3)


class TestLegacySeed(StoreCase):
    """Doc section 21 M4 build item 5 and doc section 7.1's "the measured
    values inside bin_offsets.json … become the seed".
    """

    OFFSETS = {"hLineDeltaMM": [5.0, -3.0, 5.0, -5.0],
               "offsetXMM": -4.0, "offsetYMM": 3.0,
               "vLineDeltaMM": [0.0, -4.0, 2.0, -4.0, 2.0, -6.0, 3.0, 0.0]}

    def test_the_repo_s_own_legacy_file_still_parses(self):
        # If docs/legacy/bin_offsets.json is ever reshaped, this is what
        # says so, rather than eight rects quietly collapsing to the CAD
        # layout on the next rig setup.
        raw = atomicio.read_json(gs.LEGACY_OFFSETS_PATH, None)
        self.assertIsInstance(raw, dict)
        self.assertEqual(len(raw.get("hLineDeltaMM", [])), 4)
        self.assertEqual(len(raw.get("vLineDeltaMM", [])), 8)

    def test_the_seed_nudges_the_cad_rects_rather_than_replacing_them(self):
        cad = gs.cad_bin_rects_stage()
        legacy = gs.legacy_bin_rects_stage(self.OFFSETS)
        for c, l in zip(cad, legacy):
            # Every delta in the file is under 7 mm, so no rect may move
            # more than ~10 mm (~13 px) in origin or change size by more
            # than ~15 mm. A reading of the file that produced a bigger
            # move than that would be the wrong reading.
            self.assertLess(abs(c[0] - l[0]), 20.0)
            self.assertLess(abs(c[1] - l[1]), 20.0)
            self.assertLess(abs(c[2] - l[2]), 25.0)
            self.assertLess(abs(c[3] - l[3]), 25.0)

    def test_the_offsets_actually_change_something(self):
        # Guards the reconstruction against silently degrading to "apply
        # nothing" — which would still produce eight plausible rects.
        cad = gs.cad_bin_rects_stage()
        legacy = gs.legacy_bin_rects_stage(self.OFFSETS)
        self.assertNotEqual([tuple(round(v, 3) for v in r) for r in cad],
                            [tuple(round(v, 3) for v in r) for r in legacy])

    def test_a_malformed_offsets_file_falls_back_to_the_cad_layout(self):
        got = gs.legacy_bin_rects_stage({"hLineDeltaMM": [1.0],
                                         "vLineDeltaMM": []})
        self.assertEqual(got, gs.cad_bin_rects_stage())

    def test_seeding_without_a_homography_raises(self):
        # Camera space does not exist until the camera has been solved
        # against the projector. Doc section 21 orders build item 3 before
        # build item 5 for exactly this reason.
        s = self.store()
        with self.assertRaises(geometry.GeometryError):
            s.seed_cam_rects_from_table()

    def test_seeded_camera_rects_project_back_onto_the_table(self):
        s = self.store()
        s.set_homography(CAM_TO_STAGE)
        s.seed_cam_rects_from_table()
        # The check that can fail: the seed went through H^-1, so the
        # derived stage rects must land back ON the legacy mm layout.
        # (This is NOT the doc 5.3 TRAP — the reference here is the
        # independently-computed mm geometry, not the rects themselves. A
        # homography pointing the wrong way puts these hundreds of pixels
        # out, or off the stage entirely.)
        #
        # Containment, not equality: the round trip boxes the quad twice
        # (see seed_cam_rects_from_table's docstring), so each rect comes
        # back larger — 26% against this deliberately harsh synthetic
        # camera. Measured, not guessed, and the dominant term is not the
        # perspective at all: it is the ~2.5 degrees of ROTATION in the
        # off-diagonal terms, since the bounding box of a rotated
        # rectangle grows by roughly its own size times the sine of the
        # angle. Drop the perspective to a realistic near-vertical value
        # and the inflation only falls from 26% to 10%.
        #
        # Larger is the safe direction (I9: a cutout patch that is too
        # small leaves a dark crescent on the food), and only the SEED is
        # boxed twice — a rect the operator dragged is boxed once, on the
        # way to stage space.
        want = gs.legacy_bin_rects_stage()
        for got, w in zip(s.stage_rects, want):
            self.assertLessEqual(got[0], w[0] + 0.01)
            self.assertLessEqual(got[1], w[1] + 0.01)
            self.assertGreaterEqual(got[0] + got[2], w[0] + w[2] - 0.01)
            self.assertGreaterEqual(got[1] + got[3], w[1] + w[3] - 0.01)
            self.assertLess(got[2] - w[2], 0.30 * w[2])
            self.assertLess(got[3] - w[3], 0.30 * w[3])

    def test_seeding_does_not_write_the_file(self):
        # Doc section 12.6: "Save is explicit." A seed the operator has not
        # looked at must not become the saved calibration.
        s = self.store()
        s.set_homography(CAM_TO_STAGE)
        s.seed_cam_rects_from_table()
        self.assertFalse(self.r_path.exists())


if __name__ == "__main__":
    unittest.main()
