"""Tests for common/framebus.py — M3 build item 1 (doc section 6).

Run from the repo root:

    python -m unittest discover -s python/tests -v

No camera and no second process are needed: a `FrameWriter` and a
`FrameReader` share a ring inside one test, the same way `test_scale.py`
drives `ScaleReader.feed()` with no XIAO attached. Every segment name is
unique per test (`unique_name()`) so tests can run in any order, or
after a previous run crashed mid-test, without colliding.
"""

import os
import struct
import sys
import unittest
import uuid
from multiprocessing import shared_memory

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hotpot.common import framebus  # noqa: E402


def unique_name() -> str:
    # Windows shared-memory names are also limited in what they accept;
    # hex keeps this well inside any platform's rules.
    return "hotpot_test_" + uuid.uuid4().hex[:16]


class FramebusTestCase(unittest.TestCase):
    """Common setup: a writer + reader pair on a throwaway ring name,
    both torn down in reverse creation order via addCleanup's stack."""

    def make_writer(self, width=4, height=3, channels=1, slot_count=8):
        name = unique_name()
        w = framebus.FrameWriter(width, height, name=name,
                                  slot_count=slot_count, channels=channels)
        self.addCleanup(w.close)
        self.addCleanup(w.unlink)
        return w

    def make_reader(self, writer, max_retries=framebus.DEFAULT_MAX_RETRIES):
        r = framebus.FrameReader(writer.name, max_retries=max_retries)
        self.addCleanup(r.close)
        return r


class TestRoundTrip(FramebusTestCase):

    def test_no_frame_published_yet_reads_none(self):
        w = self.make_writer()
        r = self.make_reader(w)
        self.assertIsNone(r.read())

    def test_write_then_read_one_frame(self):
        w = self.make_writer(width=4, height=3, channels=1)
        r = self.make_reader(w)
        pixels = bytes(range(12))
        frame_id = w.write(pixels, ts_ns=1_000_000_000)
        self.assertEqual(frame_id, 0)

        frame = r.read()
        self.assertIsNotNone(frame)
        self.assertEqual(frame.frame_id, 0)
        self.assertEqual(frame.ts_ns, 1_000_000_000)
        self.assertEqual(frame.data, pixels)

    def test_read_returns_a_copy_not_a_view(self):
        # If read() ever returns a view into the ring instead of a copy,
        # the next write silently mutates a frame the caller still holds.
        w = self.make_writer(width=4, height=3, channels=1)
        r = self.make_reader(w)
        w.write(bytes([1] * 12), ts_ns=1)
        frame = r.read()
        w.write(bytes([2] * 12), ts_ns=2)
        self.assertEqual(frame.data, bytes([1] * 12))

    def test_second_write_advances_frame_id_and_is_read(self):
        w = self.make_writer()
        r = self.make_reader(w)
        w.write(bytes(range(12)), ts_ns=1)
        second = bytes(range(12, 24))
        frame_id = w.write(second, ts_ns=2)
        self.assertEqual(frame_id, 1)

        frame = r.read()
        self.assertEqual(frame.frame_id, 1)
        self.assertEqual(frame.data, second)

    def test_wraparound_reads_the_latest_slot_correctly(self):
        # slot_count=3: write 5 frames (ids 0..4), which wraps twice.
        # The latest frame must still come back correctly — a writer
        # that publishes into the wrong slot on wraparound would corrupt
        # this silently rather than crash.
        w = self.make_writer(width=2, height=2, channels=1, slot_count=3)
        r = self.make_reader(w)
        last = None
        for i in range(5):
            last = bytes([i] * 4)
            w.write(last, ts_ns=i)
        frame = r.read()
        self.assertEqual(frame.frame_id, 4)
        self.assertEqual(frame.data, last)


class TestValidation(FramebusTestCase):

    def test_write_wrong_size_raises_and_does_not_publish(self):
        w = self.make_writer(width=4, height=3, channels=1)  # frame_size=12
        r = self.make_reader(w)
        with self.assertRaises(ValueError):
            w.write(bytes(11))
        # A rejected write must not have touched write_counter — a half
        # -applied "publish" here is exactly the seqlock's failure mode.
        self.assertIsNone(r.read())

    def test_constructor_rejects_nonpositive_dimensions(self):
        name = unique_name()
        with self.assertRaises(ValueError):
            framebus.FrameWriter(0, 3, name=name)
        with self.assertRaises(ValueError):
            framebus.FrameWriter(4, 0, name=name)

    def test_constructor_rejects_zero_slot_count(self):
        name = unique_name()
        with self.assertRaises(ValueError):
            framebus.FrameWriter(4, 3, name=name, slot_count=0)

    def test_reader_missing_ring_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            framebus.FrameReader(unique_name())

    def test_reader_rejects_bad_magic(self):
        name = unique_name()
        shm = shared_memory.SharedMemory(name=name, create=True, size=128)
        self.addCleanup(shm.close)
        self.addCleanup(shm.unlink)
        struct.pack_into(framebus._HEADER_FMT, shm.buf, 0,
                          0xDEADBEEF, framebus.VERSION, 4, 3, 1, 1, 0,
                          bytes(32))
        with self.assertRaises(ValueError):
            framebus.FrameReader(name)

    def test_reader_rejects_wrong_version(self):
        name = unique_name()
        shm = shared_memory.SharedMemory(name=name, create=True, size=128)
        self.addCleanup(shm.close)
        self.addCleanup(shm.unlink)
        struct.pack_into(framebus._HEADER_FMT, shm.buf, 0,
                          framebus.MAGIC, 99, 4, 3, 1, 1, 0, bytes(32))
        with self.assertRaises(ValueError):
            framebus.FrameReader(name)


class TestTornRead(FramebusTestCase):
    """Doc section 6.3's retry, forced rather than trusted.

    slot_count=1 makes every write land in the same slot as the one
    being read, so a write from the hook is guaranteed to tear the read
    in flight rather than merely being likely to.
    """

    def test_retry_recovers_from_a_torn_read(self):
        w = self.make_writer(width=2, height=2, channels=1, slot_count=1)
        r = self.make_reader(w)
        frame0 = bytes([0, 0, 0, 0])
        w.write(frame0, ts_ns=100)

        frame1 = bytes([9, 9, 9, 9])
        fired = []

        def hook():
            if not fired:
                fired.append(True)
                w.write(frame1, ts_ns=200)

        r._torn_read_hook = hook
        frame = r.read()

        self.assertEqual(fired, [True])
        self.assertIsNotNone(frame)
        # The retry must land on a self-consistent frame — the one the
        # hook just published, not a frame_id/data mismatch stitched
        # from the torn attempt and the second one.
        self.assertEqual(frame.frame_id, 1)
        self.assertEqual(frame.ts_ns, 200)
        self.assertEqual(frame.data, frame1)

    def test_persistent_tearing_gives_up_after_max_retries_rather_than_hang(self):
        w = self.make_writer(width=2, height=2, channels=1, slot_count=1)
        r = self.make_reader(w, max_retries=3)
        w.write(bytes([0, 0, 0, 0]), ts_ns=1)

        calls = {"n": 0}

        def always_interferes():
            calls["n"] += 1
            w.write(bytes([calls["n"]] * 4), ts_ns=calls["n"])

        r._torn_read_hook = always_interferes
        result = r.read()

        self.assertIsNone(result)
        self.assertEqual(calls["n"], 3)


class TestStaleness(FramebusTestCase):

    def test_no_frame_ever_published_is_stale(self):
        w = self.make_writer()
        r = self.make_reader(w)
        self.assertTrue(r.is_stale())
        self.assertIsNone(r.peek_ts_ns())

    def test_recent_frame_is_not_stale(self):
        w = self.make_writer()
        r = self.make_reader(w)
        now = 10_000_000_000
        w.write(bytes(12), ts_ns=now - 10_000_000)  # 10ms old
        self.assertFalse(r.is_stale(now_ns=now))

    def test_old_frame_is_stale(self):
        w = self.make_writer()
        r = self.make_reader(w)
        now = 10_000_000_000
        w.write(bytes(12), ts_ns=now - 600_000_000)  # 600ms old
        self.assertTrue(r.is_stale(now_ns=now, timeout_s=0.5))

    def test_staleness_threshold_is_configurable(self):
        w = self.make_writer()
        r = self.make_reader(w)
        now = 10_000_000_000
        w.write(bytes(12), ts_ns=now - 600_000_000)  # 600ms old
        self.assertFalse(r.is_stale(now_ns=now, timeout_s=1.0))


class TestMultipleReaders(FramebusTestCase):

    def test_two_readers_see_the_same_frame_independently(self):
        w = self.make_writer()
        r1 = self.make_reader(w)
        r2 = self.make_reader(w)
        pixels = bytes(range(12))
        w.write(pixels, ts_ns=42)
        self.assertEqual(r1.read().data, pixels)
        self.assertEqual(r2.read().data, pixels)


if __name__ == "__main__":
    unittest.main()
