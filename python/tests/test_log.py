"""Tests for common/log.py.

Run from the repo root:

    python -m unittest discover -s python/tests -v

Two things here are contracts with code that does not exist yet, and are
tested as such: the `HOTPOT-READY` line that run.py waits for (doc section
10.2), and the ring the staff view's log tail reads (doc section 12.8).

Every test resets the module afterwards. setup() owns the root logger and
sys.excepthook process-wide, so a test that left it installed would silently
change what every later test in the discovery run sees.
"""

import io
import logging
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hotpot.common import log as hlog  # noqa: E402


def text_stream(encoding="utf-8"):
    """A real TextIOWrapper over a BytesIO, so buffering behaves for real.

    io.StringIO would not: it has no encoding and no reconfigure, so it
    cannot show either of the two things this module does to stdout.
    """
    raw = io.BytesIO()
    return raw, io.TextIOWrapper(raw, encoding=encoding, newline="\n")


class LogCase(unittest.TestCase):

    def setUp(self):
        hlog.reset()
        self.addCleanup(hlog.reset)
        self.raw, self.stream = text_stream()

    def written(self):
        return self.raw.getvalue().decode("utf-8")


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------

class TestSetup(LogCase):

    def test_writes_a_formatted_line(self):
        hlog.setup("core", stream=self.stream)
        logging.getLogger("hotpot.wire").info("bound %s:%d", "0.0.0.0", 8770)
        line = self.written().strip()
        self.assertIn("INFO", line)
        self.assertIn("hotpot.wire", line)
        self.assertIn("bound 0.0.0.0:8770", line)
        # HH:MM:SS.mmm, no date: the date is in the log filename, and the
        # launcher prefixes the process name (doc section 10.2).
        self.assertRegex(line, r"^\d\d:\d\d:\d\d\.\d\d\d ")
        self.assertNotIn("core", line.split(":")[-1])

    def test_each_record_reaches_the_pipe_immediately(self):
        """The kill -9 in the M0 acceptance test must not eat the last lines.

        Two mechanisms, because they cover different writers: the handler
        flushes each record, and the stream itself is put in line-buffered
        mode so that a plain print() from application code lands too. Python
        block-buffers a pipe at 8 KB, and a child's stdout is a pipe.
        """
        hlog.setup("camera", stream=self.stream)
        self.assertTrue(self.stream.line_buffering)

        logging.getLogger("x").info("about to be killed")
        self.assertIn("about to be killed", self.written())

        print("and so is this", file=self.stream)
        self.assertIn("and so is this", self.written())

    def test_chinese_survives_an_ascii_stdout(self):
        """Doc section 8.1 labels are Chinese; the dev box is not UTF-8."""
        raw, stream = text_stream(encoding="ascii")
        hlog.setup("core", stream=stream)
        logging.getLogger("x").info("bin 3 is 香菇")
        self.assertIn("香菇", raw.getvalue().decode("utf-8"))

    def test_respects_the_level(self):
        hlog.setup("core", "warning", stream=self.stream)
        log = logging.getLogger("x")
        log.info("quiet")
        log.warning("loud")
        self.assertNotIn("quiet", self.written())
        self.assertIn("loud", self.written())

    def test_is_idempotent(self):
        ring = hlog.setup("core", stream=self.stream)
        again = hlog.setup("core", stream=self.stream)
        self.assertIs(again, ring)
        logging.getLogger("x").warning("once")
        self.assertEqual(self.written().count("once"), 1)
        self.assertEqual(len(ring.tail()), 1)

    def test_takes_over_a_pre_existing_handler(self):
        """A library that logged at import time must not double every line."""
        stray = logging.StreamHandler(io.StringIO())
        logging.getLogger().addHandler(stray)
        self.addCleanup(logging.getLogger().removeHandler, stray)

        hlog.setup("core", stream=self.stream)
        logging.getLogger("x").warning("once")
        self.assertEqual(self.written().count("once"), 1)

    def test_ring_is_returned_and_also_reachable(self):
        ring = hlog.setup("core", stream=self.stream)
        self.assertIs(hlog.ring(), ring)

    def test_reset_leaves_nothing_installed(self):
        before = (sys.excepthook, threading.excepthook)
        hlog.setup("core", stream=self.stream)
        hlog.reset()
        self.assertEqual((sys.excepthook, threading.excepthook), before)
        self.assertIsNone(hlog.ring())
        self.assertEqual(logging.getLogger().handlers, [])


class TestResolveLevel(LogCase):

    def test_names_numbers_and_none(self):
        self.assertEqual(hlog.resolve_level("debug"), logging.DEBUG)
        self.assertEqual(hlog.resolve_level("WARNING"), logging.WARNING)
        self.assertEqual(hlog.resolve_level(logging.ERROR), logging.ERROR)
        self.assertEqual(hlog.resolve_level("30"), 30)
        self.assertEqual(hlog.resolve_level(None), hlog.DEFAULT_LEVEL)

    def test_reads_the_environment(self):
        os.environ[hlog.LEVEL_ENV] = "debug"
        self.addCleanup(os.environ.pop, hlog.LEVEL_ENV, None)
        self.assertEqual(hlog.resolve_level(), logging.DEBUG)

    def test_nonsense_falls_back_instead_of_raising(self):
        """A bad env var at 1am on the board must not stop a process."""
        os.environ[hlog.LEVEL_ENV] = "verbose"
        self.addCleanup(os.environ.pop, hlog.LEVEL_ENV, None)
        err = io.StringIO()
        real, sys.stderr = sys.stderr, err
        try:
            self.assertEqual(hlog.resolve_level(), hlog.DEFAULT_LEVEL)
        finally:
            sys.stderr = real
        self.assertIn("verbose", err.getvalue())


# ---------------------------------------------------------------------------
# The ring (doc section 12.8)
# ---------------------------------------------------------------------------

class TestRing(LogCase):

    def test_records_carry_what_the_panel_needs(self):
        ring = hlog.setup("core", stream=self.stream)
        logging.getLogger("hotpot.health").warning("camera late")
        rec = ring.tail()[-1]
        self.assertEqual(rec["who"], "core")
        self.assertEqual(rec["name"], "hotpot.health")
        self.assertEqual(rec["level"], logging.WARNING)
        self.assertEqual(rec["levelname"], "WARNING")
        self.assertEqual(rec["msg"], "camera late")
        self.assertEqual(rec["seq"], 1)
        self.assertIsInstance(rec["ts"], float)

    def test_tracebacks_land_in_the_ring(self):
        ring = hlog.setup("core", stream=self.stream)
        try:
            raise ValueError("serial port vanished")
        except ValueError:
            logging.getLogger("x").exception("scale read failed")
        msg = ring.tail()[-1]["msg"]
        self.assertIn("scale read failed", msg)
        self.assertIn("ValueError: serial port vanished", msg)

    def test_level_filter_then_limit(self):
        """50 errors means 50 errors, not the errors among the last 50."""
        ring = hlog.Ring(size=100)
        for i in range(40):
            ring.add("core", logging.INFO, "x", f"info {i}")
        for i in range(3):
            ring.add("core", logging.ERROR, "x", f"error {i}")
        for i in range(40):
            ring.add("core", logging.INFO, "x", f"more {i}")

        errors = ring.tail(limit=10, min_level=logging.ERROR)
        self.assertEqual([r["msg"] for r in errors],
                         ["error 0", "error 1", "error 2"])

    def test_tail_is_oldest_first_and_limited_from_the_end(self):
        ring = hlog.Ring(size=100)
        for i in range(10):
            ring.add("core", logging.INFO, "x", str(i))
        self.assertEqual([r["msg"] for r in ring.tail(limit=3)], ["7", "8", "9"])

    def test_after_returns_only_what_is_new(self):
        """How the panel resumes a stream without gaps or repeats."""
        ring = hlog.Ring()
        ring.add("core", logging.INFO, "x", "a")
        mark = ring.seq
        ring.add("core", logging.INFO, "x", "b")
        ring.add("core", logging.INFO, "x", "c")
        self.assertEqual([r["msg"] for r in ring.tail(after=mark)], ["b", "c"])
        self.assertEqual(ring.tail(after=ring.seq), [])

    def test_is_bounded_and_drops_the_oldest(self):
        ring = hlog.Ring(size=5)
        for i in range(20):
            ring.add("core", logging.INFO, "x", str(i))
        self.assertEqual(len(ring), 5)
        self.assertEqual([r["msg"] for r in ring.tail()],
                         ["15", "16", "17", "18", "19"])
        # seq keeps counting past the drop, so the panel can tell that it
        # missed 15 lines rather than seeing them renumbered.
        self.assertEqual(ring.seq, 20)

    def test_tail_hands_out_copies(self):
        ring = hlog.Ring()
        ring.add("core", logging.INFO, "x", "original")
        ring.tail()[0]["msg"] = "tampered"
        self.assertEqual(ring.tail()[0]["msg"], "original")

    def test_survives_concurrent_writers(self):
        """Beats, wire reads and the main loop all log at once."""
        ring = hlog.Ring(size=10000)

        def spam():
            for i in range(200):
                ring.add("core", logging.INFO, "x", str(i))

        threads = [threading.Thread(target=spam) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(ring.seq, 1600)
        self.assertEqual(len(ring), 1600)
        self.assertEqual(len({r["seq"] for r in ring.tail(limit=0)}), 1600)


# ---------------------------------------------------------------------------
# Readiness (doc section 10.2)
# ---------------------------------------------------------------------------

class TestReady(LogCase):

    def test_prints_the_exact_documented_line(self):
        hlog.ready("tracker", self.stream)
        self.assertEqual(self.written(), "HOTPOT-READY tracker\n")

    def test_carries_no_log_formatting(self):
        """run.py matches this line; a formatter change must not break it."""
        hlog.setup("tracker", stream=self.stream)
        hlog.ready("tracker", self.stream)
        self.assertEqual(self.written().splitlines()[-1], "HOTPOT-READY tracker")

    def test_is_flushed(self):
        hlog.ready("voice", self.stream)
        self.assertIn(b"HOTPOT-READY", self.raw.getvalue())

    def test_is_ready_line_round_trips(self):
        hlog.ready("classifier", self.stream)
        self.assertEqual(hlog.is_ready_line(self.written()), "classifier")

    def test_is_ready_line_survives_the_launcher_prefix(self):
        self.assertEqual(
            hlog.is_ready_line("camera | HOTPOT-READY camera\r\n"), "camera")

    def test_is_ready_line_rejects_other_lines(self):
        self.assertIsNone(hlog.is_ready_line("12:00:00.000 INFO x: starting"))
        self.assertIsNone(hlog.is_ready_line(""))
        self.assertIsNone(hlog.is_ready_line("HOTPOT-READY"))


# ---------------------------------------------------------------------------
# Crashes
# ---------------------------------------------------------------------------

class TestExceptHooks(LogCase):

    def test_an_uncaught_exception_is_logged(self):
        ring = hlog.setup("core", stream=self.stream)
        try:
            raise RuntimeError("no shm segment")
        except RuntimeError:
            sys.excepthook(*sys.exc_info())

        rec = ring.tail()[-1]
        self.assertEqual(rec["level"], logging.CRITICAL)
        self.assertIn("RuntimeError: no shm segment", rec["msg"])
        self.assertIn("no shm segment", self.written())

    def test_ctrl_c_is_left_alone(self):
        """Ctrl-C is the documented shutdown, not a crash (doc section 10.2)."""
        seen = []
        sys.excepthook = lambda *a: seen.append(a)
        ring = hlog.setup("core", stream=self.stream)
        try:
            raise KeyboardInterrupt()
        except KeyboardInterrupt:
            sys.excepthook(*sys.exc_info())
        self.assertEqual(len(seen), 1)
        self.assertEqual(ring.tail(), [])

    def test_a_thread_that_dies_is_logged(self):
        ring = hlog.setup("core", stream=self.stream)

        def explode():
            raise ValueError("serial thread gave up")

        t = threading.Thread(target=explode, name="scale")
        t.start()
        t.join()

        msgs = [r["msg"] for r in ring.tail()]
        self.assertTrue(any("serial thread gave up" in m for m in msgs), msgs)
        self.assertTrue(any("scale" in m for m in msgs), msgs)


if __name__ == "__main__":
    unittest.main()
