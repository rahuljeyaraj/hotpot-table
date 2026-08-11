"""Tests for core/loadcell_cal.py — M2 build item 3 (doc section 21).

Run from the repo root:

    python -m unittest discover -s python/tests -v

The counts in the inverted-cell tests are the real thing: bins 0 and 3 of
the rig on COM5 read around -287,000 and -473,900 counts empty, so the
"negative counts" case here is the ordinary case on this hardware, not a
contrived one.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hotpot.core import loadcell_cal  # noqa: E402


class TestFreshCalibration(unittest.TestCase):

    def test_fresh_is_eight_uncalibrated_bins(self):
        cal = loadcell_cal.Calibration()
        self.assertEqual(len(cal.bins), 8)
        self.assertEqual([b.i for b in cal.bins], list(range(8)))
        for i in range(8):
            self.assertFalse(cal.calibrated(i))

    def test_uncalibrated_bin_reads_none_not_zero(self):
        """Doc section 21, M2: 'no billing occurs from the frozen reading.'
        0.0 g would bill nothing but is a *reading*; None is 'cannot be
        weighed', which is what has to reach pricing.
        """
        cal = loadcell_cal.Calibration()
        self.assertIsNone(cal.grams(0, 12345))

    def test_bad_bin_index_raises(self):
        cal = loadcell_cal.Calibration()
        with self.assertRaises(IndexError):
            cal.tare(8, 0)
        with self.assertRaises(IndexError):
            cal.grams(-1, 0)


class TestTareAndCalibrate(unittest.TestCase):
    """Doc section 9.6's three lines, in order."""

    def setUp(self):
        self.cal = loadcell_cal.Calibration()

    def test_two_point_calibration_reads_the_reference_mass_back(self):
        self.cal.tare(0, 83422)
        self.cal.calibrate(0, 83422 + 214.77 * 500.0, 500.0)
        self.assertAlmostEqual(self.cal.grams(0, 83422), 0.0, places=6)
        self.assertAlmostEqual(
            self.cal.grams(0, 83422 + 214.77 * 500.0), 500.0, places=6)

    def test_tare_does_not_discard_an_existing_calibration(self):
        """Doc section 12.4 offers Tare and Calibrate as separate buttons;
        re-zeroing a drifted cell must not force a full recalibration.
        """
        self.cal.tare(2, 1000)
        self.cal.calibrate(2, 1000 + 200.0 * 500.0, 500.0)
        self.cal.tare(2, 1500)          # cell drifted 500 counts
        self.assertTrue(self.cal.calibrated(2))
        self.assertAlmostEqual(self.cal.bins[2].counts_per_gram, 200.0)
        self.assertAlmostEqual(self.cal.grams(2, 1500), 0.0, places=6)

    def test_clear_returns_a_bin_to_first_boot(self):
        self.cal.tare(4, 500)
        self.cal.calibrate(4, 500 + 100.0 * 500.0, 500.0)
        self.cal.clear(4)
        self.assertFalse(self.cal.calibrated(4))
        self.assertIsNone(self.cal.grams(4, 500))


class TestInvertedCell(unittest.TestCase):
    """Doc section 9.6 and M2's 'Do NOT': the sign is computed, never
    asked. These tests pass the *same* API calls as the upright ones —
    there is no orientation argument to get wrong, which is the point.
    """

    def test_inverted_cell_reads_positive_grams(self):
        cal = loadcell_cal.Calibration()
        # Counts fall as mass is added: 500 g drives it 214.77*500 lower.
        cal.tare(3, -473932)
        cpg = cal.calibrate(3, -473932 - 214.77 * 500.0, 500.0)
        self.assertLess(cpg, 0.0, "inverted cell must yield a negative scale")
        self.assertAlmostEqual(cal.grams(3, -473932), 0.0, places=6)
        self.assertAlmostEqual(
            cal.grams(3, -473932 - 214.77 * 500.0), 500.0, places=6)

    def test_upright_and_inverted_agree_on_the_same_mass(self):
        cal = loadcell_cal.Calibration()
        cal.tare(0, 1000)
        cal.calibrate(0, 1000 + 300.0 * 500.0, 500.0)     # upright
        cal.tare(1, 1000)
        cal.calibrate(1, 1000 - 300.0 * 500.0, 500.0)     # inverted
        self.assertAlmostEqual(cal.grams(0, 1000 + 300.0 * 250.0), 250.0)
        self.assertAlmostEqual(cal.grams(1, 1000 - 300.0 * 250.0), 250.0)


class TestSanityCheck(unittest.TestCase):
    """Doc section 9.6: 'if abs(counts_per_gram) < 10 ... Refuse and say so.'
    Each of these fails by construction if the check is removed.
    """

    def setUp(self):
        self.cal = loadcell_cal.Calibration()
        self.cal.tare(3, 1000)

    def test_disconnected_cell_is_refused(self):
        # A dead DT line barely moves: 500 counts over 500 g is 1/gram.
        with self.assertRaises(loadcell_cal.CalibrationError):
            self.cal.calibrate(3, 1500, 500.0)

    def test_refused_calibration_leaves_the_bin_untouched(self):
        self.cal.calibrate(3, 1000 + 200.0 * 500.0, 500.0)   # a good one
        good = self.cal.bins[3].counts_per_gram
        with self.assertRaises(loadcell_cal.CalibrationError):
            self.cal.calibrate(3, 1001, 500.0)               # a bad one
        self.assertEqual(self.cal.bins[3].counts_per_gram, good)

    def test_inverted_cell_is_not_mistaken_for_a_failure(self):
        """abs(), not <. A cell at -200 counts/gram is fine."""
        self.cal.calibrate(3, 1000 - 200.0 * 500.0, 500.0)
        self.assertTrue(self.cal.calibrated(3))

    def test_zero_or_negative_reference_mass_is_refused(self):
        for bad in (0.0, -500.0):
            with self.assertRaises(loadcell_cal.CalibrationError):
                self.cal.calibrate(3, 1000 + 200.0 * 500.0, bad)

    def test_message_names_the_bin_and_mentions_no_counts(self):
        """Doc section 12.4: the operator is never shown counts, sign,
        multipliers or orientation — and this string goes on that screen.
        """
        with self.assertRaises(loadcell_cal.CalibrationError) as ctx:
            self.cal.calibrate(3, 1001, 500.0)
        msg = str(ctx.exception).lower()
        self.assertIn("bin 3", msg)
        for forbidden in ("count", "sign", "invert", "orientation",
                          "multiplier", "negative"):
            self.assertNotIn(forbidden, msg)


class TestGramsAll(unittest.TestCase):

    def setUp(self):
        self.cal = loadcell_cal.Calibration()
        for i in range(8):
            self.cal.tare(i, 0)
            self.cal.calibrate(i, 100.0 * 500.0, 500.0)

    def test_all_eight_convert(self):
        got = self.cal.grams_all([100.0 * g for g in range(8)])
        self.assertEqual(got, [float(g) for g in range(8)])

    def test_no_serial_data_gives_eight_nones(self):
        """A dead XIAO and an uncalibrated cell must reach pricing by the
        same route: None, not 0.0.
        """
        self.assertEqual(self.cal.grams_all(None), [None] * 8)

    def test_uncalibrated_bin_is_none_among_calibrated_ones(self):
        self.cal.clear(5)
        got = self.cal.grams_all([100.0 * 10] * 8)
        self.assertIsNone(got[5])
        self.assertEqual(len([g for g in got if g is not None]), 7)


class TestNoise(unittest.TestCase):

    def test_noise_reported_in_grams_and_always_positive(self):
        """Doc section 12.4's indicator. Positive even for an inverted
        cell — noise has no direction.
        """
        cal = loadcell_cal.Calibration()
        cal.tare(4, 0, noise_counts_rms=1992.5)
        cal.calibrate(4, -200.0 * 500.0, 500.0)      # inverted
        self.assertAlmostEqual(cal.bins[4].noise_grams(), 1992.5 / 200.0)

    def test_noise_is_none_before_calibration(self):
        cal = loadcell_cal.Calibration()
        cal.tare(4, 0, noise_counts_rms=50.0)
        self.assertIsNone(cal.bins[4].noise_grams())


class TestPersistence(unittest.TestCase):
    """Doc section 8.3's file shape, through atomicio (doc section 20.4)."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "loadcell_cal.json")

    def tearDown(self):
        self.dir.cleanup()

    def test_missing_file_is_a_first_boot_not_an_error(self):
        cal = loadcell_cal.Calibration.load(self.path)
        self.assertEqual(len(cal.bins), 8)
        for i in range(8):
            self.assertFalse(cal.calibrated(i))

    def test_round_trip_preserves_every_field(self):
        cal = loadcell_cal.Calibration()
        cal.tare(0, 83422, noise_counts_rms=18.3)
        cal.calibrate(0, 83422 - 214.77 * 500.0, 500.0, now=1754838400.1)
        cal.save(self.path)

        back = loadcell_cal.Calibration.load(self.path)
        a, b = cal.bins[0], back.bins[0]
        self.assertEqual(b.zero_counts, a.zero_counts)
        self.assertAlmostEqual(b.counts_per_gram, a.counts_per_gram)
        self.assertEqual(b.calibrated_at, 1754838400.1)
        self.assertEqual(b.ref_mass_g, 500.0)
        self.assertEqual(b.noise_counts_rms, 18.3)

    def test_saved_sign_survives_the_round_trip(self):
        """The one field that silently mis-bills if it flips."""
        cal = loadcell_cal.Calibration()
        cal.tare(3, -473932)
        cal.calibrate(3, -473932 - 214.77 * 500.0, 500.0)
        cal.save(self.path)
        back = loadcell_cal.Calibration.load(self.path)
        self.assertLess(back.bins[3].counts_per_gram, 0.0)
        self.assertAlmostEqual(
            back.grams(3, -473932 - 214.77 * 500.0), 500.0, places=6)

    def test_written_shape_matches_doc_section_8_3(self):
        cal = loadcell_cal.Calibration()
        cal.tare(0, 83422, noise_counts_rms=18.3)
        cal.calibrate(0, 83422 - 214.77 * 500.0, 500.0)
        cal.save(self.path)

        from hotpot.common import atomicio
        raw = atomicio.read_json(self.path)
        self.assertEqual(raw["schema"], 3)
        self.assertEqual(len(raw["bins"]), 8)
        self.assertEqual(set(raw["bins"][0]), {
            "i", "zero_counts", "counts_per_gram", "calibrated_at",
            "ref_mass_g", "noise_counts_rms"})

    def test_wrong_schema_raises(self):
        from hotpot.common import atomicio
        atomicio.write_json(self.path, {"schema": 2, "bins": []})
        with self.assertRaises(ValueError):
            loadcell_cal.Calibration.load(self.path)

    def test_short_file_still_gives_eight_indexable_bins(self):
        from hotpot.common import atomicio
        atomicio.write_json(self.path, {
            "schema": 3,
            "bins": [{"i": 6, "zero_counts": 10.0, "counts_per_gram": 200.0}],
        })
        cal = loadcell_cal.Calibration.load(self.path)
        self.assertEqual(len(cal.bins), 8)
        self.assertTrue(cal.calibrated(6))
        self.assertFalse(cal.calibrated(0))

    def test_out_of_range_bin_in_file_is_ignored_not_fatal(self):
        from hotpot.common import atomicio
        atomicio.write_json(self.path, {
            "schema": 3,
            "bins": [{"i": 99, "zero_counts": 1.0, "counts_per_gram": 5.0}],
        })
        cal = loadcell_cal.Calibration.load(self.path)
        self.assertEqual(len(cal.bins), 8)

    def test_a_failed_calibration_is_never_saved_over_a_good_one(self):
        cal = loadcell_cal.Calibration()
        cal.tare(1, 1000)
        cal.calibrate(1, 1000 + 200.0 * 500.0, 500.0)
        cal.save(self.path)
        with self.assertRaises(loadcell_cal.CalibrationError):
            cal.calibrate(1, 1001, 500.0)
        # The Bins tab saves only after calibrate() returns, so disk still
        # holds the good numbers even without re-saving.
        back = loadcell_cal.Calibration.load(self.path)
        self.assertAlmostEqual(back.bins[1].counts_per_gram, 200.0)


if __name__ == "__main__":
    unittest.main()
