"""Tests for core/scale.py — M2 build item 2 (doc section 21).

Run from the repo root:

    python -m unittest discover -s python/tests -v

No XIAO and no pyserial are needed: the port is injected (`open_port`),
and the sample path is driven through `feed()` at explicit timestamps.
Wall-clock sleeps appear in exactly one place — the capture-window tests,
where the thing under test *is* a duration.

The counts are the rig's. Bins 0, 1, 3 and 4 on COM5 read large negative
values empty (2026-08-11), so the negative-counts cases here are the
ordinary hardware, not a contrived one.
"""

import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hotpot.core import loadcell_cal, scale  # noqa: E402


def calibrated(counts_per_gram=200.0, zero=0.0, bins=range(8)):
    """A Calibration where the listed bins read grams and the rest do not."""
    cal = loadcell_cal.Calibration()
    for i in bins:
        cal.tare(i, zero)
        cal.calibrate(i, zero + counts_per_gram * 500.0, 500.0)
    return cal


def line(*counts):
    """One firmware line, byte for byte as main.cpp prints it."""
    return ("raw " + " ".join(str(c) for c in counts) + "\r\n").encode("ascii")


class FakeSerial:
    """A serial port that hands out a scripted sequence.

    An item may be bytes (one readline) or an exception instance (raised
    from readline, as an unplug does). Once the script is exhausted the
    port goes quiet — b"" after a short pause — which is pyserial's read
    timeout, not an error.
    """

    def __init__(self, items):
        self._items = list(items)
        self._lock = threading.Lock()
        self.closed = False
        self.reads = 0

    def readline(self):
        with self._lock:
            self.reads += 1
            if self._items:
                item = self._items.pop(0)
            else:
                item = None
        if item is None:
            time.sleep(0.005)
            return b""
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self):
        self.closed = True


def wait_for(predicate, timeout=2.0):
    """Poll until true. Returns the outcome rather than asserting, so the
    caller's own assertion names what was actually being waited on."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


class TestParseLine(unittest.TestCase):
    """Doc section 4.9's parser requirements, against the format read out
    of firmware/loadcells/src/main.cpp and verified on COM5.
    """

    def test_the_firmware_line_parses(self):
        got = scale.parse_line(line(1, 2, 3, 4, 5, 6, 7, 8))
        self.assertEqual(got, [1, 2, 3, 4, 5, 6, 7, 8])

    def test_negative_counts_parse(self):
        """Bins 0, 1, 3 and 4 read large negatives empty on this rig."""
        got = scale.parse_line(line(-287431, -190228, 46, -473932,
                                    -12007, 1323, 203, 48))
        self.assertEqual(got[3], -473932)

    def test_truncated_head_of_a_line_is_dropped(self):
        """The first line after open is usually cut short."""
        self.assertIsNone(scale.parse_line(b"raw 8123 -47"))

    def test_truncated_tail_of_a_line_is_dropped(self):
        """Eight tokens, no `raw` — a plausible-looking half line, and the
        one that would mis-weigh every bin if it were salvaged."""
        self.assertIsNone(scale.parse_line(b"39 812 44 71 5 6 7 8\r\n"))

    def test_junk_is_dropped_not_raised_on(self):
        for junk in (b"", b"\r\n", b"hello\r\n", b"raw\r\n",
                     b"raw 1 2 3 4 5 6 7 8 9\r\n", b"raw a b c d e f g h\r\n"):
            self.assertIsNone(scale.parse_line(junk), junk)

    def test_line_noise_is_dropped(self):
        self.assertIsNone(scale.parse_line(b"raw 1 2 \xff\xfe 5 6 7 8\r\n"))

    def test_floats_are_junk_because_the_firmware_prints_longs(self):
        self.assertIsNone(scale.parse_line(b"raw 1.5 2 3 4 5 6 7 8\r\n"))

    def test_extra_whitespace_is_tolerated(self):
        self.assertEqual(scale.parse_line("  raw  1 2 3 4 5 6 7 8  \n"),
                         [1, 2, 3, 4, 5, 6, 7, 8])


class TestMedianFilter(unittest.TestCase):
    """Doc section 9.5's smoothing.

    Every reader here is built with `avg_window=1` — the moving average
    (`TestMovingAverage`, below) is a second stage on top of this one, and
    a default `avg_window` would blend these exact-value assertions
    across several median outputs instead of checking the median alone.
    `avg_window=1` is not a special "off" flag, it is the parameter's own
    identity value (average of one thing is that thing), so this isolates
    the stage under test the same way each test here already pins
    `median_window` down explicitly.
    """

    def setUp(self):
        self.cal = calibrated(counts_per_gram=100.0)

    def test_a_single_bad_read_is_discarded_not_smeared(self):
        """Median, not mean — the doc's stated reason. A mean would move
        every one of the five samples' output.
        """
        r = scale.ScaleReader("COM-TEST", cal=self.cal, median_window=5,
                              avg_window=1)
        for c in (1000, 1000, 999_999, 1000, 1000):
            r.feed([c] * 8, now=1000.0)
        self.assertEqual(r.read(now=1000.0).counts[0], 1000.0)

    def test_window_size_is_a_parameter(self):
        """The rig runs at 10.7Hz, not doc 4.9's 78Hz, so median-of-5
        spans 465ms. If the lag shows on the table the fix is a smaller
        window here — which is only possible if nothing hardcodes 5.
        """
        r = scale.ScaleReader("COM-TEST", cal=self.cal, median_window=3,
                              avg_window=1)
        for c in (100, 200, 300, 400, 500):
            r.feed([c] * 8, now=1000.0)
        # Last three are 300/400/500; a window of 5 would give 300.
        self.assertEqual(r.read(now=1000.0).counts[0], 400.0)

    def test_a_partly_filled_window_still_reads(self):
        """First sample after open must not wait for five."""
        r = scale.ScaleReader("COM-TEST", cal=self.cal, median_window=5,
                              avg_window=1)
        r.feed([500] * 8, now=1000.0)
        self.assertEqual(r.read(now=1000.0).counts[0], 500.0)

    def test_each_channel_is_filtered_independently(self):
        r = scale.ScaleReader("COM-TEST", cal=self.cal, median_window=3,
                              avg_window=1)
        r.feed([10, 20, 30, 40, 50, 60, 70, 80], now=1000.0)
        r.feed([11, 21, 31, 41, 51, 61, 71, 81], now=1000.1)
        r.feed([12, 22, 32, 42, 52, 62, 72, 82], now=1000.2)
        self.assertEqual(r.read(now=1000.2).counts,
                         [11.0, 21.0, 31.0, 41.0, 51.0, 61.0, 71.0, 81.0])

    def test_median_window_of_zero_is_refused(self):
        with self.assertRaises(ValueError):
            scale.ScaleReader("COM-TEST", median_window=0)

    def test_a_short_sample_is_refused(self):
        r = scale.ScaleReader("COM-TEST")
        with self.assertRaises(ValueError):
            r.feed([1, 2, 3])


class TestMovingAverage(unittest.TestCase):
    """2026-08-26: a second low-pass stage on top of the median, added
    because the median alone still let the displayed weight jump around
    on ordinary channel noise — not an outlier, so nothing for a median
    to discard. `avg_window` averages the median's own output.
    """

    def setUp(self):
        self.cal = calibrated(counts_per_gram=100.0)

    def test_avg_window_of_one_is_the_plain_median(self):
        """The identity case: averaging one value is that value, so this
        must reproduce median-of-3 exactly with no second stage visible.
        """
        r = scale.ScaleReader("COM-TEST", cal=self.cal, median_window=3,
                              avg_window=1)
        for c in (100, 200, 300, 400, 500):
            r.feed([c] * 8, now=1000.0)
        self.assertEqual(r.read(now=1000.0).counts[0], 400.0)

    def test_the_average_runs_over_median_outputs_not_raw_counts(self):
        """median_window=1 makes the median stage a no-op (the median of
        one sample is that sample), which isolates what avg_window alone
        does: the last 3 raw values, averaged.
        """
        r = scale.ScaleReader("COM-TEST", cal=self.cal, median_window=1,
                              avg_window=3)
        for c in (100, 200, 300, 400, 500):
            r.feed([c] * 8, now=1000.0)
        # Last three raw samples are 300/400/500 -> mean 400.
        self.assertEqual(r.read(now=1000.0).counts[0], 400.0)

    def test_a_partly_filled_avg_window_still_reads(self):
        """First sample after open must not wait for avg_window samples,
        the same rule median_window already follows."""
        r = scale.ScaleReader("COM-TEST", cal=self.cal, median_window=1,
                              avg_window=5)
        r.feed([500] * 8, now=1000.0)
        self.assertEqual(r.read(now=1000.0).counts[0], 500.0)

    def test_a_single_outlier_is_still_rejected_before_averaging(self):
        """The two stages compose: the median discards the spike, so the
        average only ever sees clean values and is not dragged toward it.
        """
        r = scale.ScaleReader("COM-TEST", cal=self.cal, median_window=5,
                              avg_window=3)
        for c in (1000, 1000, 1000, 1000, 1000):
            r.feed([c] * 8, now=1000.0)
        r.feed([999_999] * 8, now=1000.1)
        self.assertEqual(r.read(now=1000.1).counts[0], 1000.0)

    def test_each_channel_is_averaged_independently(self):
        r = scale.ScaleReader("COM-TEST", cal=self.cal, median_window=1,
                              avg_window=2)
        r.feed([10, 20, 30, 40, 50, 60, 70, 80], now=1000.0)
        r.feed([12, 22, 32, 42, 52, 62, 72, 82], now=1000.1)
        self.assertEqual(r.read(now=1000.1).counts,
                         [11.0, 21.0, 31.0, 41.0, 51.0, 61.0, 71.0, 81.0])

    def test_avg_window_of_zero_is_refused(self):
        with self.assertRaises(ValueError):
            scale.ScaleReader("COM-TEST", avg_window=0)

    def test_settle_is_evaluated_against_the_averaged_value(self):
        """The settle detector must see what read() sees — otherwise a
        bin could report `settled` against a number the display never
        actually showed.
        """
        r = scale.ScaleReader("COM-TEST", cal=self.cal, median_window=1,
                              avg_window=3, settle_ms=1.0, settle_tol_g=2.0)
        # Averaged over 3, this steps 100g -> 100g -> 100g -> then a
        # value far enough that the AVERAGE (not the raw sample) crosses
        # the tolerance band, restarting the settle window.
        for k in range(4):
            r.feed([10000] * 8, now=1000.0 + k * 0.01)
        self.assertTrue(r.read(now=1000.03).settled[0])
        r.feed([10000 + 100 * 50] * 8, now=1000.04)   # +50g raw, one sample
        # Averaged over the last 3 (100, 100, 150g raw) -> ~116.7g, still
        # more than 2g from the 100g ref, so settle must have restarted.
        self.assertFalse(r.read(now=1000.04).settled[0])


class TestGrams(unittest.TestCase):

    def test_counts_convert_through_the_calibration(self):
        cal = calibrated(counts_per_gram=200.0, zero=1000.0)
        r = scale.ScaleReader("COM-TEST", cal=cal)
        r.feed([1000 + 200 * 250] * 8, now=1000.0)
        for g in r.read(now=1000.0).grams:
            self.assertAlmostEqual(g, 250.0)

    def test_an_inverted_cell_reads_positive_grams(self):
        """No orientation input anywhere in this file either (M2's Do NOT)."""
        cal = loadcell_cal.Calibration()
        cal.tare(3, -473932)
        cal.calibrate(3, -473932 - 214.77 * 500.0, 500.0)
        r = scale.ScaleReader("COM-TEST", cal=cal)
        r.feed([0, 0, 0, int(-473932 - 214.77 * 100.0), 0, 0, 0, 0],
               now=1000.0)
        self.assertAlmostEqual(r.read(now=1000.0).grams[3], 100.0, places=1)

    def test_an_uncalibrated_bin_reads_none_among_calibrated_ones(self):
        cal = calibrated(bins=[0, 1, 2, 3, 4, 6, 7])
        r = scale.ScaleReader("COM-TEST", cal=cal)
        r.feed([100] * 8, now=1000.0)
        got = r.read(now=1000.0).grams
        self.assertIsNone(got[5])
        self.assertEqual(len([g for g in got if g is not None]), 7)


class TestStaleness(unittest.TestCase):
    """Doc section 9.5's 0.5s, and doc section 21's M2 acceptance: 'no
    billing occurs from the frozen reading.'
    """

    def setUp(self):
        self.cal = calibrated()
        self.r = scale.ScaleReader("COM-TEST", cal=self.cal)

    def test_a_reader_that_has_never_read_is_stale(self):
        self.assertTrue(self.r.read(now=1000.0).stale)
        self.assertEqual(self.r.read(now=1000.0).grams, [None] * 8)

    def test_fresh_within_the_window(self):
        self.r.feed([200 * 300] * 8, now=1000.0)
        fresh = self.r.read(now=1000.4)
        self.assertFalse(fresh.stale)
        self.assertAlmostEqual(fresh.grams[0], 300.0)

    def test_stale_past_the_window(self):
        self.r.feed([200 * 300] * 8, now=1000.0)
        self.assertTrue(self.r.read(now=1000.6).stale)

    def test_a_frozen_reading_reports_none_grams_not_the_last_weight(self):
        """The one that stops a dead XIAO from billing. 0.0 g would be a
        real reading — a bin a diner had emptied — and would bill; None
        cannot. Fails by construction if read() returns the last counts.
        """
        self.r.feed([200 * 300] * 8, now=1000.0)
        frozen = self.r.read(now=1005.0)
        self.assertIsNone(frozen.counts)
        self.assertEqual(frozen.grams, [None] * 8)
        self.assertNotIn(0.0, frozen.grams)

    def test_a_stale_bin_is_never_settled(self):
        for t in range(10):
            self.r.feed([200 * 300] * 8, now=1000.0 + t * 0.093)
        self.assertTrue(all(self.r.read(now=1000.9).settled))
        self.assertFalse(any(self.r.read(now=1005.0).settled))

    def test_the_window_is_a_parameter(self):
        r = scale.ScaleReader("COM-TEST", cal=self.cal, stale_s=2.0)
        r.feed([100] * 8, now=1000.0)
        self.assertFalse(r.read(now=1001.5).stale)


class TestSettle(unittest.TestCase):
    """Doc section 9.5: settled when the gram value has stayed within ±2g
    for settle_ms. At the rig's 10.7Hz the default 300ms is ~4 samples.
    """

    SAMPLE = 0.093        # 10.7Hz, measured on COM5

    def setUp(self):
        # 100 counts/gram, so 1 count is 0.01 g and the counts below read
        # as convenient gram values.
        self.cal = calibrated(counts_per_gram=100.0)
        self.r = scale.ScaleReader("COM-TEST", cal=self.cal, median_window=1)

    def feed_steady(self, grams, n, t0=1000.0):
        for k in range(n):
            self.r.feed([int(grams * 100)] * 8, now=t0 + k * self.SAMPLE)
        return t0 + (n - 1) * self.SAMPLE

    def test_a_steady_bin_settles_after_settle_ms(self):
        t = self.feed_steady(500.0, 5)
        self.assertTrue(self.r.read(now=t).settled[0])

    def test_it_does_not_settle_before_settle_ms(self):
        """300ms is ~4 samples at 10.7Hz; three is not enough."""
        t = self.feed_steady(500.0, 3)
        self.assertFalse(self.r.read(now=t).settled[0])

    def test_a_step_restarts_the_window(self):
        self.feed_steady(500.0, 6)
        self.r.feed([int(455.0 * 100)] * 8, now=1000.0 + 6 * self.SAMPLE)
        self.assertFalse(self.r.read(now=1000.0 + 6 * self.SAMPLE).settled[0])

    def test_a_slow_ramp_never_settles(self):
        """The trap this check exists for: comparing each sample with the
        previous one passes here forever — food poured in at 1g a sample
        never breaks a ±2g step — and would tell the classifier to
        photograph a bin that is still filling. Anchoring the window to
        its opening value is what makes it fail. Revert `_update_settle`
        to a previous-sample comparison and this test goes green wrongly.
        """
        for k in range(40):
            self.r.feed([int((500.0 + k * 1.0) * 100)] * 8,
                        now=1000.0 + k * self.SAMPLE)
            self.assertFalse(
                self.r.read(now=1000.0 + k * self.SAMPLE).settled[0],
                f"settled while still ramping, at sample {k}")

    def test_noise_inside_the_tolerance_still_settles(self):
        """±2g is a band, not a demand for identical samples. Bin 4 is the
        noisy channel on this rig (1993 counts rms uncalibrated).
        """
        for k, wobble in enumerate([0.0, 1.2, -0.9, 1.8, -1.5, 0.4, 1.0]):
            self.r.feed([int((500.0 + wobble) * 100)] * 8,
                        now=1000.0 + k * self.SAMPLE)
        self.assertTrue(self.r.read(now=1000.0 + 6 * self.SAMPLE).settled[0])

    def test_an_uncalibrated_bin_is_never_settled(self):
        """Unmeasurable is not steady. A bin with no grams must not
        trigger the classifier by sitting still at None.
        """
        cal = calibrated(bins=[0, 1, 2, 3, 4, 6, 7])
        r = scale.ScaleReader("COM-TEST", cal=cal, median_window=1)
        for k in range(10):
            r.feed([50_000] * 8, now=1000.0 + k * self.SAMPLE)
        got = r.read(now=1000.0 + 9 * self.SAMPLE)
        self.assertTrue(got.settled[0])
        self.assertFalse(got.settled[5])

    def test_bins_settle_independently(self):
        for k in range(8):
            counts = [50_000] * 8
            counts[6] = 50_000 + k * 500       # bin 6 is ramping 5g a sample
            self.r.feed(counts, now=1000.0 + k * self.SAMPLE)
        got = self.r.read(now=1000.0 + 7 * self.SAMPLE)
        self.assertTrue(got.settled[0])
        self.assertFalse(got.settled[6])

    def test_tolerance_and_duration_are_parameters(self):
        r = scale.ScaleReader("COM-TEST", cal=self.cal, median_window=1,
                              settle_ms=1000.0, settle_tol_g=0.5)
        for k in range(6):
            r.feed([50_000] * 8, now=1000.0 + k * self.SAMPLE)
        self.assertFalse(r.read(now=1000.0 + 5 * self.SAMPLE).settled[0])
        for k in range(6, 14):
            r.feed([50_000] * 8, now=1000.0 + k * self.SAMPLE)
        self.assertTrue(r.read(now=1000.0 + 13 * self.SAMPLE).settled[0])


class TestSerialThread(unittest.TestCase):
    """The thread, against a scripted port. No XIAO involved."""

    def reader(self, items, **kw):
        port = FakeSerial(items)
        r = scale.ScaleReader("COM-TEST", cal=calibrated(),
                              open_port=lambda: port, **kw)
        self.addCleanup(r.stop)
        return r, port

    def test_lines_from_the_port_reach_the_slot(self):
        r, _ = self.reader([line(*([200 * 300] * 8))])
        r.start()
        self.assertTrue(wait_for(lambda: r.samples >= 1))
        self.assertAlmostEqual(r.read().grams[0], 300.0)
        self.assertFalse(r.read().stale)

    def test_a_truncated_first_line_is_dropped_and_the_next_is_read(self):
        """Doc section 4.9: 'the first line after open is usually
        truncated'. It must cost one sample, not the stream.
        """
        r, _ = self.reader([b"5 6 7 8\r\n", line(*([100] * 8))])
        r.start()
        self.assertTrue(wait_for(lambda: r.samples >= 1))
        self.assertEqual(r.bad_lines, 1)

    def test_junk_never_kills_the_thread(self):
        r, _ = self.reader([b"\x00\xff garbage\r\n", b"raw nope\r\n",
                            b"raw 1 2 3\r\n", line(*([100] * 8))])
        r.start()
        self.assertTrue(wait_for(lambda: r.samples >= 1))
        self.assertEqual(r.bad_lines, 3)

    def test_an_unplug_mid_stream_reopens_the_port(self):
        """A dead reader thread would take staleness down with it, and
        staleness is what stops a frozen weight from billing.
        """
        ports = []

        def opener():
            p = FakeSerial([line(*([100] * 8)), OSError("device disconnected")]
                           if not ports else [line(*([100] * 8))])
            ports.append(p)
            return p

        r = scale.ScaleReader("COM-TEST", cal=calibrated(), open_port=opener)
        self.addCleanup(r.stop)
        r.start()
        self.assertTrue(wait_for(lambda: len(ports) >= 2, 5.0),
                        "the port was never reopened after the unplug")
        self.assertTrue(ports[0].closed)
        self.assertTrue(wait_for(lambda: r.opens >= 2, 5.0))

    def test_a_missing_port_at_startup_is_retried_not_fatal(self):
        """The XIAO not being plugged in yet is the ordinary boot state."""
        attempts = []

        def opener():
            attempts.append(time.time())
            if len(attempts) < 2:
                raise OSError("could not open port COM-TEST")
            return FakeSerial([line(*([100] * 8))])

        r = scale.ScaleReader("COM-TEST", cal=calibrated(), open_port=opener)
        self.addCleanup(r.stop)
        r.start()
        self.assertTrue(wait_for(lambda: r.samples >= 1, 5.0))
        self.assertTrue(r.read().stale is False)

    def test_a_silent_port_goes_stale_without_raising(self):
        """Some unplugs return nothing at all rather than raising. The
        staleness clock has to be what notices, so the empty read must not
        be mistaken for data.
        """
        r, _ = self.reader([line(*([100] * 8))], stale_s=0.1)
        r.start()
        self.assertTrue(wait_for(lambda: r.samples >= 1))
        self.assertTrue(wait_for(lambda: r.read().stale, 2.0))
        self.assertEqual(r.samples, 1)

    def test_stop_is_prompt_and_idempotent(self):
        r, port = self.reader([line(*([100] * 8))])
        r.start()
        self.assertTrue(wait_for(lambda: r.samples >= 1))
        t0 = time.time()
        r.stop()
        r.stop()
        self.assertLess(time.time() - t0, 2.0)
        self.assertTrue(wait_for(lambda: port.closed, 1.0))

    def test_starting_twice_is_refused(self):
        r, _ = self.reader([])
        r.start()
        with self.assertRaises(RuntimeError):
            r.start()

    def test_status_reports_the_measured_rate(self):
        """Doc 4.9's ~78Hz went unchallenged until it was measured. The
        staff view gets the real number.
        """
        r, _ = self.reader([])
        for k in range(11):
            r.feed([100] * 8, now=1000.0 + k * 0.093)
        st = r.status(now=1000.0 + 10 * 0.093)
        self.assertAlmostEqual(st["hz"], 10.75, places=1)
        self.assertEqual(st["samples"], 11)
        self.assertFalse(st["stale"])

    def test_status_before_any_sample(self):
        r, _ = self.reader([])
        st = r.status(now=1000.0)
        self.assertTrue(st["stale"])
        self.assertIsNone(st["age"])
        self.assertEqual(st["hz"], 0.0)
        self.assertFalse(st["open"])

    def test_the_rate_falls_to_zero_when_the_device_goes_quiet(self):
        """**2026-08-25, the scales-offline investigation.**

        `_rate` is a deque of arrival timestamps that nothing prunes by
        age, so a device that stops sending used to leave its last
        healthy rate in there indefinitely — the staff view showed
        `10.38 Hz` beside "no connection" for fourteen minutes, and that
        frozen number is most of why the fault first read as a live link
        rather than a silent board.

        Capable of failing: drop the staleness gate from `hz()` and the
        second assertion reads ~10.75.
        """
        r, _ = self.reader([])
        for k in range(11):
            r.feed([100] * 8, now=1000.0 + k * 0.093)
        healthy = 1000.0 + 10 * 0.093
        self.assertAlmostEqual(r.status(now=healthy)["hz"], 10.75, places=1)

        # Nothing further arrives. Well past stale_s, and then far past
        # it — the number must not merely decay, it must read zero.
        for later in (healthy + 1.0, healthy + 60.0, healthy + 900.0):
            with self.subTest(quiet_for=later - healthy):
                st = r.status(now=later)
                self.assertTrue(st["stale"])
                self.assertEqual(st["hz"], 0.0)

    def test_a_silent_device_is_distinguishable_from_an_absent_one(self):
        """The distinction the Bins tab could not draw on 2026-08-25:
        `open` says the port is there, `stale` says nothing is coming out
        of it. Both true at once is a board that has stopped talking —
        a different fault, and a different fix, from a cable that is out.
        """
        # One good line, then the port goes quiet with no error — which
        # is exactly what a hung board looks like from this side.
        r, _ = self.reader([line(*([100] * 8))])
        r.start()
        self.assertTrue(wait_for(lambda: r.samples >= 1))
        self.assertTrue(wait_for(lambda: r.status()["stale"], 2.0))
        st = r.status()
        self.assertTrue(st["open"], "the port is still open")
        self.assertTrue(st["stale"], "and nothing is arriving on it")
        self.assertEqual(st["hz"], 0.0)
        self.assertEqual(st["port"], "COM-TEST")
        r.stop()


class TestCaptureWindow(unittest.TestCase):
    """Doc section 9.6's `median(counts, 2s window)`, and doc section
    8.3's `noise_counts_rms`. This is what the Bins tab's Tare and
    Calibrate buttons collect before handing over to loadcell_cal.
    """

    def feeder(self, reader, samples, period=0.002):
        """Push samples in the background while capture() is blocked."""
        stop = threading.Event()

        def run():
            k = 0
            while not stop.is_set():
                reader.feed(samples[k % len(samples)])
                k += 1
                time.sleep(period)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        self.addCleanup(stop.set)
        return stop

    def test_median_and_noise_over_the_window(self):
        r = scale.ScaleReader("COM-TEST")
        self.feeder(r, [[100] * 8, [104] * 8, [102] * 8, [98] * 8, [96] * 8])
        cap = r.capture(0.3)
        self.assertGreaterEqual(cap.n, scale.MIN_CAPTURE_SAMPLES)
        self.assertAlmostEqual(cap.counts[0], 100.0, delta=2.0)
        self.assertGreater(cap.noise_rms[0], 0.0)

    def test_capture_takes_raw_samples_not_the_median_filtered_slot(self):
        """Measuring noise through the smoother would report the
        smoother's noise, not the cell's — understating a bad channel by
        roughly the window size, which is the one thing doc 8.3's
        indicator exists to catch. Bin 4 on this rig is 40x bin 3.
        """
        r = scale.ScaleReader("COM-TEST", median_window=5)
        # One spike in five, which is exactly what a median-of-5 exists to
        # remove: the filtered channel is dead flat while the cell itself
        # is swinging 2000 counts. An alternating +/-2000 would NOT test
        # this — its median alternates too, and the check would pass
        # whichever samples capture() reduced.
        spike = [[0, 0, 0, 0, v, 0, 0, 0] for v in (0, 0, 0, 0, 2000)]
        self.feeder(r, spike)
        cap = r.capture(0.3)
        self.assertGreater(cap.noise_rms[4], 500.0)
        self.assertLess(cap.noise_rms[3], 1.0)
        # And the filtered slot really is flat, so the assertion above can
        # only be met by raw samples.
        self.assertEqual(r.read().counts[4], 0.0)

    def test_a_dead_link_is_refused_rather_than_calibrated_from(self):
        r = scale.ScaleReader("COM-TEST")
        with self.assertRaises(scale.ScaleError):
            r.capture(0.05)

    def test_the_refusal_message_mentions_no_counts(self):
        """Doc section 12.4: the operator is never shown counts, sign or
        orientation — this string lands on that screen.
        """
        r = scale.ScaleReader("COM-TEST")
        with self.assertRaises(scale.ScaleError) as ctx:
            r.capture(0.05)
        msg = str(ctx.exception).lower()
        for forbidden in ("count", "sign", "invert", "orientation", "median"):
            self.assertNotIn(forbidden, msg)

    def test_a_capture_feeds_loadcell_cal_directly(self):
        """The two modules meet here and nowhere else: scale.py collects
        counts, loadcell_cal.py does the maths on them.
        """
        cal = loadcell_cal.Calibration()
        r = scale.ScaleReader("COM-TEST", cal=cal)
        self.feeder(r, [[-473932] * 8, [-473930] * 8, [-473934] * 8])
        empty = r.capture(0.3)
        cal.tare(3, empty.counts[3], empty.noise_rms[3])
        self.assertAlmostEqual(cal.grams(3, empty.counts[3]) or 0.0, 0.0)

    def test_capture_does_not_disturb_the_live_reading(self):
        """It runs on the Bins tab's thread; the 60Hz loop keeps reading."""
        r = scale.ScaleReader("COM-TEST", cal=calibrated(), median_window=1)
        self.feeder(r, [[200 * 250] * 8])
        cap = r.capture(0.2)
        self.assertGreaterEqual(cap.n, scale.MIN_CAPTURE_SAMPLES)
        self.assertAlmostEqual(r.read().grams[0], 250.0)

    def test_a_finished_capture_stops_collecting(self):
        r = scale.ScaleReader("COM-TEST")
        self.feeder(r, [[7] * 8])
        r.capture(0.15)
        time.sleep(0.05)
        self.assertEqual(r._collectors, [])


if __name__ == "__main__":
    unittest.main()
