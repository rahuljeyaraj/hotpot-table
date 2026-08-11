"""Tests for core/calibrator.py — M2 build item 3, wired (doc section 21).

Run from the repo root:

    python -m unittest discover -s python/tests -v

No XIAO and no pyserial: the ScaleReader is the real one, with no serial
port opened, fed from a thread through the same `feed()` the reader thread
uses. That is deliberate — this module's whole job is the seam between
scale.py and loadcell_cal.py, so a fake reader would test the seam
against nothing.

The counts are the rig's, measured on COM5 on 2026-08-11: bins 0, 3, 4
and 7 read large negative values with an empty bin, so an inverted cell
here is the ordinary hardware and not a contrived case. `CPG`'s magnitude
is doc section 8.3's example, 214.77 counts per gram.
"""

import dataclasses
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hotpot.common import atomicio                             # noqa: E402
from hotpot.core import calibrator, loadcell_cal, scale         # noqa: E402

# Empty-bin counts, measured on the rig 2026-08-11 (CLAUDE.md's table).
EMPTY = [-286992, 163203, 1513, -473574, -390799, 46181, 204281, -3617]

CPG = 214.77            # doc section 8.3's example
INVERTED_CPG = -214.77  # a cell mounted upside down; counts fall with mass

# Long enough to clear MIN_CAPTURE_SAMPLES even with Windows' coarse sleep
# granularity (0.3s / 15.6ms ~ 19 samples), short enough that the whole
# file stays a few seconds.
CAPTURE_S = 0.3


def with_mass(counts, i, grams, cpg=CPG):
    """`counts` with `grams` sitting in bin i, at `cpg` counts per gram."""
    out = list(counts)
    out[i] = int(round(counts[i] + cpg * grams))
    return out


def wait_for(predicate, timeout=2.0):
    """Poll until true. Returns the outcome rather than asserting, so the
    caller's own assertion names what was being waited on."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


class Rig:
    """A real ScaleReader with no XIAO behind it.

    `counts` and `jitter` are live: a test changes them between flows the
    way a hand puts a weight in a bin. Nothing waits for the median-of-5
    to cross before a capture, and nothing needs to — a 0.3s window is
    ~20 samples, so at most the first one carries the old value and the
    median cannot see it.

    Jitter alternates +/- so that the `pstdev` a capture measures is the
    jitter itself and the median is unmoved by it. A random wobble would
    make the noise assertions flaky for no gain in realism.
    """

    def __init__(self, counts=None, jitter=0.0, **kw):
        self.reader = scale.ScaleReader("COM-TEST", **kw)
        self.base = list(EMPTY if counts is None else counts)
        self.counts = list(self.base)
        self.jitter = float(jitter)
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        def run():
            k = 0
            while not self._stop.is_set():
                off = self.jitter if k % 2 == 0 else -self.jitter
                self.reader.feed([int(c + off) for c in self.counts])
                k += 1
                time.sleep(0.002)

        self._thread = threading.Thread(target=run, name="rig", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    def put(self, i, grams, cpg=CPG):
        """Place `grams` in bin i, from the empty baseline."""
        self.counts = with_mass(self.base, i, grams, cpg)

    def empty(self):
        self.counts = list(self.base)

    def grams(self, i):
        return self.reader.read().grams[i]


class CalibratorTestCase(unittest.TestCase):
    """A calibrator writing into a throwaway state/ directory."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "state", "loadcell_cal.json")

    def rig(self, counts=None, jitter=0.0, feed=True, **kw):
        r = Rig(counts, jitter, **kw)
        self.addCleanup(r.stop)
        if feed:
            r.start()
        return r

    def calibrator(self, rig, **kw):
        return calibrator.Calibrator(rig.reader, path=self.path,
                                     capture_s=CAPTURE_S, **kw)

    def saved(self):
        """The calibration as a restarted core would read it back."""
        return loadcell_cal.Calibration.load(self.path)


class TestEndToEnd(CalibratorTestCase):
    """Doc section 12.4's flow, all the way from a capture window to a
    live gram reading on the other side of the reader.
    """

    def test_tare_then_calibrate_makes_the_live_reading_read_grams(self):
        rig = self.rig()
        cal = self.calibrator(rig)

        tared = cal.tare(3)
        self.assertGreaterEqual(tared.samples, scale.MIN_CAPTURE_SAMPLES)
        # A first tare cannot quote grams — nothing has been weighed on
        # this cell yet — so it sends the operator to step 2 instead of
        # printing a 0 g it did not measure.
        self.assertIsNone(tared.grams)
        self.assertIn("Calibrate", tared.message)

        rig.put(3, 500.0)
        done = cal.calibrate(3, 500.0)
        self.assertAlmostEqual(done.grams, 500.0, delta=1.0)
        self.assertEqual(done.message, "Done. Bin 3 reads 500 g.")

        # The reader the 60Hz loop reads is the one that got calibrated —
        # the point of sharing one Calibration object. Take the weight out
        # again and the live reading has to follow it back down.
        rig.empty()
        self.assertTrue(wait_for(lambda: abs(rig.grams(3)) < 2.0),
                        f"bin 3 read {rig.grams(3)}g with an empty bin")

    def test_an_inverted_cell_needs_no_operator_input(self):
        """Doc section 21, M2: 'repeat for an inverted cell → also reads
        correctly, with no operator input about orientation.' There is no
        argument in this API that could carry that input.
        """
        rig = self.rig()
        cal = self.calibrator(rig)
        cal.tare(0)
        rig.put(0, 500.0, INVERTED_CPG)
        cal.calibrate(0, 500.0)
        self.assertLess(self.saved().bins[0].counts_per_gram, 0.0)
        self.assertAlmostEqual(rig.grams(0), 500.0, delta=1.0)

    def test_a_calibration_survives_a_restart(self):
        rig = self.rig()
        cal = self.calibrator(rig)
        cal.tare(5)
        rig.put(5, 200.0)
        cal.calibrate(5, 200.0)

        reloaded = self.saved()
        self.assertEqual(atomicio.read_json(self.path)["schema"],
                         loadcell_cal.CAL_SCHEMA)
        self.assertAlmostEqual(reloaded.bins[5].counts_per_gram,
                               cal.cal.bins[5].counts_per_gram)
        self.assertAlmostEqual(reloaded.grams(5, rig.reader.read().counts[5]),
                               200.0, delta=1.0)

    def test_a_re_tare_keeps_the_calibration_on_disk(self):
        """Doc section 12.4 has two buttons because re-zeroing a drifted
        cell must not cost a full recalibration.
        """
        rig = self.rig()
        cal = self.calibrator(rig)
        cal.tare(2)
        rig.put(2, 500.0)
        self.assertIsNotNone(cal.calibrate(2, 500.0).grams)

        rig.empty()
        rig.counts[2] += 900            # the cell drifted, empty
        self.assertTrue(wait_for(lambda: rig.grams(2) > 2.0))
        again = cal.tare(2)
        self.assertAlmostEqual(again.grams, 0.0, delta=1.0)
        self.assertEqual(again.message, "Done. Bin 2 reads 0 g.")
        self.assertTrue(self.saved().calibrated(2))
        self.assertAlmostEqual(self.saved().bins[2].counts_per_gram,
                               CPG, delta=1.0)

    def test_only_the_named_bin_moves(self):
        rig = self.rig()
        cal = self.calibrator(rig)
        cal.tare(6)
        for i in range(8):
            if i != 6:
                self.assertEqual(self.saved().bins[i],
                                 loadcell_cal.BinCal(i=i), f"bin {i}")


class TestTheVerificationReading(CalibratorTestCase):
    """The number in "Done. Bin 3 reads 500 g." is a second measurement,
    not the capture read back through its own fit — see the module
    docstring's TRAP note.
    """

    def test_a_stale_link_reports_no_number_but_still_saves(self):
        """The only way `grams` can be None here is if it came from the
        live slot: the capture succeeded, so the fit itself would print
        500 g quite happily.
        """
        # stale_s 0.0 makes every reading stale by definition, which is
        # what a XIAO unplugged the instant the capture ended looks like.
        rig = self.rig(stale_s=0.0)
        cal = self.calibrator(rig)
        cal.cal.tare(4, EMPTY[4])       # a tare already on record
        rig.put(4, 500.0)
        done = cal.calibrate(4, 500.0)

        self.assertIsNone(done.grams)
        self.assertNotIn("500", done.message)
        self.assertIn("cable", done.message)
        # Saved anyway: the capture was a real measurement.
        self.assertTrue(self.saved().calibrated(4))


class TestRefusals(CalibratorTestCase):
    """Doc section 12.4 step 3: show why, and do not save."""

    def test_a_dead_link_writes_no_file_at_all(self):
        rig = self.rig(feed=False)
        cal = self.calibrator(rig)
        with self.assertRaises(scale.ScaleError):
            cal.tare(0)
        self.assertFalse(os.path.exists(self.path))

    def test_calibrate_before_tare_is_refused(self):
        """An untared bin on this rig fits ~4x too steep and passes doc
        section 9.6's sanity check, so nothing downstream would catch it.
        """
        rig = self.rig(counts=with_mass(EMPTY, 1, 500.0))
        cal = self.calibrator(rig)
        with self.assertRaises(loadcell_cal.CalibrationError) as ctx:
            cal.calibrate(1, 500.0)
        self.assertIn("Tare", str(ctx.exception))
        self.assertFalse(os.path.exists(self.path))

    def test_a_failed_sanity_check_leaves_the_good_numbers_on_disk(self):
        rig = self.rig()
        cal = self.calibrator(rig)
        cal.tare(7)
        rig.put(7, 500.0)
        cal.calibrate(7, 500.0)
        good = self.saved().bins[7].counts_per_gram

        # The operator forgot to put the weight in — the loaded capture is
        # the zero, so counts_per_gram collapses toward 0.
        rig.empty()
        self.assertTrue(wait_for(lambda: abs(rig.grams(7)) < 2.0))
        with self.assertRaises(loadcell_cal.CalibrationError):
            cal.calibrate(7, 500.0)

        self.assertAlmostEqual(self.saved().bins[7].counts_per_gram, good)
        self.assertAlmostEqual(cal.cal.bins[7].counts_per_gram, good)
        self.assertAlmostEqual(rig.grams(7), 0.0, delta=1.0)

    def test_a_ref_mass_of_zero_is_refused_and_saves_nothing(self):
        rig = self.rig()
        cal = self.calibrator(rig)
        cal.tare(0)
        before = atomicio.read_json(self.path)
        with self.assertRaises(loadcell_cal.CalibrationError):
            cal.calibrate(0, 0.0)
        self.assertEqual(atomicio.read_json(self.path), before)

    def test_a_bad_bin_index_is_a_caller_bug_not_an_operator_message(self):
        rig = self.rig()
        cal = self.calibrator(rig)
        for i in (-1, 8, 99):
            with self.assertRaises(IndexError):
                cal.tare(i)
            with self.assertRaises(IndexError):
                cal.calibrate(i, 500.0)

    def test_no_operator_message_mentions_counts_sign_or_orientation(self):
        """Doc section 12.4: 'the operator is never shown or asked about
        counts, sign, multipliers, or orientation.' Every string that
        screen can show is collected here, including the two successful
        ones.
        """
        messages = []
        rig = self.rig(feed=False)
        cal = self.calibrator(rig)
        with self.assertRaises(scale.ScaleError) as ctx:
            cal.tare(0)                 # dead link
        messages.append(str(ctx.exception))
        with self.assertRaises(loadcell_cal.CalibrationError) as ctx:
            cal.calibrate(0, 500.0)     # never tared
        messages.append(str(ctx.exception))

        rig.start()
        messages.append(cal.tare(0).message)
        with self.assertRaises(loadcell_cal.CalibrationError) as ctx:
            cal.calibrate(0, 500.0)     # no weight in the bin
        messages.append(str(ctx.exception))
        rig.put(0, 500.0)
        messages.append(cal.calibrate(0, 500.0).message)
        messages.append(str(calibrator.BusyError(
            "One bin is already being set up — wait for it to finish.")))

        for msg in messages:
            low = msg.lower()
            for forbidden in ("count", "sign", "invert", "orientation",
                              "multiplier", "median", "per gram"):
                self.assertNotIn(forbidden, low, msg)


class TestOneAtATime(CalibratorTestCase):
    """Two flows at once would have the second capture photograph
    whatever bin state the first one left behind.
    """

    def test_a_second_flow_during_a_capture_is_refused(self):
        rig = self.rig()
        cal = self.calibrator(rig)
        cal.tare(2)                     # so calibrate(2) gets past its
                                        # precondition and reaches the lock
        started = threading.Event()
        done = []

        def slow():
            started.set()
            done.append(cal.tare(0, seconds=0.6))

        t = threading.Thread(target=slow, daemon=True)
        t.start()
        self.assertTrue(started.wait(1.0))
        time.sleep(0.05)                # inside the capture window now
        with self.assertRaises(calibrator.BusyError):
            cal.tare(1)
        with self.assertRaises(calibrator.BusyError):
            cal.calibrate(2, 500.0)

        t.join(3.0)
        self.assertEqual(len(done), 1)
        # And the lock is released, so the next bin works normally.
        self.assertEqual(cal.tare(1).bin, 1)

    def test_a_refused_flow_does_not_hold_the_lock(self):
        rig = self.rig(feed=False)
        cal = self.calibrator(rig)
        with self.assertRaises(scale.ScaleError):
            cal.tare(0)
        rig.start()
        self.assertEqual(cal.tare(0).bin, 0)


class TestWriteFailure(CalibratorTestCase):
    """Memory ahead of disk is the quiet failure: the table bills right
    all evening, then comes back from a restart uncalibrated.
    """

    def test_a_failed_write_rolls_the_bin_back(self):
        rig = self.rig()
        cal = self.calibrator(rig)
        cal.tare(1)
        rig.put(1, 500.0)
        cal.calibrate(1, 500.0)
        good = dataclasses.replace(cal.cal.bins[1])

        rig.put(1, 250.0)
        with mock.patch.object(atomicio, "write_json",
                               side_effect=OSError("no space left on device")):
            with self.assertRaises(OSError):
                cal.calibrate(1, 250.0)

        self.assertEqual(cal.cal.bins[1], good)
        self.assertEqual(self.saved().bins[1], good)

    def test_a_failed_first_write_leaves_the_bin_uncalibrated(self):
        rig = self.rig()
        cal = self.calibrator(rig)
        with mock.patch.object(atomicio, "write_json",
                               side_effect=OSError("read-only file system")):
            with self.assertRaises(OSError):
                cal.tare(4)
        self.assertEqual(cal.cal.bins[4], loadcell_cal.BinCal(i=4))
        self.assertFalse(os.path.exists(self.path))


class TestNoise(CalibratorTestCase):
    """Doc section 8.3's `noise_counts_rms`, in the grams doc section
    12.4's indicator wants. CLAUDE.md flags four channels at 1000-1500
    counts rms and asks what that is in grams; this is the answer.
    """

    def test_noise_is_reported_in_grams_and_flags_a_bin_that_cannot_settle(self):
        rig = self.rig(jitter=1200.0)   # bin 0's rms on the rig, roughly
        cal = self.calibrator(rig)
        tared = cal.tare(0)
        # Uncalibrated, so counts cannot be turned into grams yet.
        self.assertIsNone(tared.noise_g)

        rig.put(0, 500.0)
        done = cal.calibrate(0, 500.0)
        self.assertAlmostEqual(done.noise_g, 1200.0 / CPG, delta=1.0)
        # 5.6g of noise against doc section 9.5's +/-2g settle band: this
        # bin would never satisfy the settle test, so the classifier would
        # never be triggered for it.
        self.assertTrue(done.noisy)

    def test_a_quiet_channel_is_not_flagged(self):
        rig = self.rig(jitter=40.0)     # bins 2, 3 and 7 on the rig
        cal = self.calibrator(rig)
        cal.tare(2)
        rig.put(2, 500.0)
        done = cal.calibrate(2, 500.0)
        self.assertLess(done.noise_g, 1.0)
        self.assertFalse(done.noisy)

    def test_calibrate_keeps_the_empty_bin_noise(self):
        """Noise measured with a mass in the bin is the mass settling and
        the tray rocking, not the channel.
        """
        rig = self.rig(jitter=100.0)
        cal = self.calibrator(rig)
        cal.tare(3)
        rig.put(3, 500.0)
        rig.jitter = 4000.0             # a hand steadying the weight
        cal.calibrate(3, 500.0)
        self.assertAlmostEqual(self.saved().bins[3].noise_counts_rms,
                               100.0, delta=10.0)


if __name__ == "__main__":
    unittest.main()
