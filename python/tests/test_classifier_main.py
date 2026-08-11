"""Tests for classifier/main.py — the vision process (doc sections 3.2,
4.7; doc section 21 M4 build items 2 and 7).

Run from the repo root:

    python -m unittest discover -s python/tests -v

**No camera process and no shared memory.** `RingSource` takes an
`open_reader` callable for exactly the reason `ScaleReader` takes
`open_port`: the thing that would otherwise need hardware is one
injection point, and everything above it is ordinary code. `FakeReader`
below is a numpy array with the four attributes `RingSource` actually
reads.

Captures go to a throwaway directory, never the repo's own
`datasets/captures/`.
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402

from hotpot.classifier import main as cmain  # noqa: E402


class FakeFrame:
    def __init__(self, data, frame_id=1, ts_ns=None):
        self.data = data
        self.frame_id = frame_id
        self.ts_ns = ts_ns if ts_ns is not None else time.time_ns()


class FakeReader:
    """The four attributes and two methods `RingSource` uses, and nothing
    else — a real `FrameReader` needs a live camera process to attach to.
    """

    def __init__(self, image, stale=False):
        self.height, self.width = image.shape[:2]
        self.channels = image.shape[2] if image.ndim == 3 else 1
        self._image = image
        self.stale = stale
        self.closed = False
        self.reads = 0
        self.next_read_raises = None

    def is_stale(self, timeout_s=0.5):
        return self.stale

    def read(self):
        self.reads += 1
        if self.next_read_raises is not None:
            exc, self.next_read_raises = self.next_read_raises, None
            raise exc
        return FakeFrame(self._image.tobytes())

    def close(self):
        self.closed = True


def dot_field(w=640, h=480):
    img = np.zeros((h, w, 3), dtype=np.uint8)
    ys, xs = np.ogrid[:h, :w]
    for cx, cy in ((160, 120), (480, 120), (480, 360), (160, 360)):
        img[(xs - cx) ** 2 + (ys - cy) ** 2 <= 144] = 255
    return img


def food_field(w=640, h=480):
    """A frame with a distinguishable value per quadrant, so a crop can be
    checked for having come from the right place rather than merely for
    having the right size.
    """
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:h // 2, :w // 2] = 10
    img[:h // 2, w // 2:] = 90
    img[h // 2:, :w // 2] = 170
    img[h // 2:, w // 2:] = 250
    return img


class WorkerCase(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.captures = Path(self.dir.name) / "captures"
        self.settings = Path(self.dir.name) / "camera_settings.json"
        self.sent = []
        self.got = threading.Event()

    def build(self, image=None, reader=None, stale=False):
        self.reader = reader or FakeReader(
            image if image is not None else dot_field(), stale=stale)
        source = cmain.RingSource(open_reader=lambda: self.reader)
        worker = cmain.Classifier(source=source, send=self._send,
                                  captures_dir=self.captures,
                                  settings_path=self.settings)
        worker.start()
        self.addCleanup(worker.stop)
        return worker

    def _send(self, msg):
        self.sent.append(msg)
        self.got.set()

    def wait(self, timeout=5.0):
        self.assertTrue(self.got.wait(timeout), "the worker never replied")
        self.got.clear()
        return self.sent[-1]


class TestDetectDots(WorkerCase):

    def test_a_dot_pattern_comes_back_as_doc_4_7s_points(self):
        w = self.build()
        w.on_message({"t": "cmd", "id": 20, "op": "detect_dots", "expect": 4})
        reply = self.wait()
        self.assertEqual(reply["t"], "dots")
        self.assertEqual(reply["id"], 20)
        self.assertEqual(len(reply["points"]), 4)
        self.assertEqual(len(reply["points"][0]), 2)
        self.assertIn("ms", reply)

    def test_the_expect_count_is_echoed_back(self):
        # Core matches a reply to the pass it asked for; a late reply from
        # the previous pass has to be recognisable as one.
        w = self.build()
        w.on_message({"t": "cmd", "id": 7, "op": "detect_dots", "expect": 15})
        self.assertEqual(self.wait()["expect"], 15)

    def test_a_stale_camera_is_refused_rather_than_analysed(self):
        # Doc section 6.4. Solving a homography against the last frame
        # before the camera died produces a calibration nobody can explain.
        w = self.build(stale=True)
        w.on_message({"t": "cmd", "id": 1, "op": "detect_dots", "expect": 4})
        reply = self.wait()
        self.assertFalse(reply["ok"])
        self.assertIn("stopped sending frames", reply["error"])

    def test_no_ring_at_all_is_a_sentence_not_a_traceback(self):
        def boom():
            raise FileNotFoundError("no ring")
        worker = cmain.Classifier(source=cmain.RingSource(open_reader=boom),
                                  send=self._send,
                                  captures_dir=self.captures)
        worker.start()
        self.addCleanup(worker.stop)
        worker.on_message({"t": "cmd", "id": 1, "op": "detect_dots"})
        reply = self.wait()
        self.assertFalse(reply["ok"])
        self.assertIn("camera process running", reply["error"])

    def test_the_threshold_can_be_overridden_from_the_command(self):
        img = np.zeros((200, 200, 3), dtype=np.uint8)
        ys, xs = np.ogrid[:200, :200]
        img[(xs - 100) ** 2 + (ys - 100) ** 2 <= 144] = 150   # dim dots
        w = self.build(image=img)
        w.on_message({"t": "cmd", "id": 1, "op": "detect_dots"})
        self.assertEqual(self.wait()["points"], [])
        w.on_message({"t": "cmd", "id": 2, "op": "detect_dots",
                      "threshold": 100})
        self.assertEqual(len(self.wait()["points"]), 1)


class TestUnknownCommands(WorkerCase):

    def test_classify_says_it_is_not_built_yet(self):
        # Core waits on a reply. Silence here is a wizard hung on a
        # screen with nothing to look at.
        w = self.build()
        w.on_message({"t": "cmd", "id": 17, "op": "classify", "rects": []})
        reply = self.wait()
        self.assertFalse(reply["ok"])
        self.assertIn("M7", reply["error"])

    def test_an_unknown_op_is_answered_not_dropped(self):
        w = self.build()
        w.on_message({"t": "cmd", "id": 3, "op": "levitate"})
        self.assertFalse(self.wait()["ok"])

    def test_a_non_cmd_message_is_ignored(self):
        w = self.build()
        w.on_message({"t": "welcome", "cfg": {}})
        self.assertFalse(self.got.wait(0.3))


class TestCrop(unittest.TestCase):

    def test_a_crop_comes_from_where_it_was_asked_for(self):
        frame = food_field()
        patch = cmain.crop(frame, (330, 250, 100, 100))
        self.assertTrue((patch == 250).all())

    def test_a_rect_hanging_off_the_edge_is_clamped(self):
        # An operator dragging on the Setup tab pushes a rect a few pixels
        # past the border constantly. Losing a session to that is worse
        # than a crop a few pixels narrower than asked for.
        patch = cmain.crop(food_field(), (600, 440, 100, 100))
        self.assertEqual(patch.shape[:2], (40, 40))

    def test_a_rect_entirely_outside_the_frame_raises(self):
        with self.assertRaises(cmain.ClassifierError):
            cmain.crop(food_field(), (2000, 2000, 50, 50))


class TestSafeLabel(unittest.TestCase):

    def test_an_ordinary_label_survives(self):
        self.assertEqual(cmain._safe_label("soya_chunks"), "soya_chunks")

    def test_a_path_traversal_label_is_refused(self):
        # A label becomes a directory name. `../../` must not become one.
        self.assertEqual(cmain._safe_label("../../etc"), "etc")
        with self.assertRaises(cmain.ClassifierError):
            cmain._safe_label("../..")

    def test_an_empty_label_raises(self):
        with self.assertRaises(cmain.ClassifierError):
            cmain._safe_label("   ")


class TestCapture(WorkerCase):

    def cmd(self, **over):
        base = {"t": "cmd", "id": 21, "op": "capture",
                "rects": [[10, 10, 100, 100, 0], [330, 250, 100, 100, 6]],
                "labels": ["mushroom", "prawn"], "burst": 1}
        base.update(over)
        return base

    def test_one_capture_writes_a_folder_per_label(self):
        w = self.build(image=food_field())
        w.on_message(self.cmd())
        reply = self.wait()
        self.assertEqual(reply["t"], "captured")
        self.assertEqual(len(reply["files"]), 2)
        self.assertTrue((self.captures / "mushroom").is_dir())
        self.assertTrue((self.captures / "prawn").is_dir())

    def test_the_filename_matches_doc_12_7(self):
        # "datasets/captures/<label>/<unixms>_bin<i>.jpg"
        w = self.build(image=food_field())
        w.on_message(self.cmd())
        self.wait()
        names = [p.name for p in (self.captures / "prawn").glob("*.jpg")]
        self.assertEqual(len(names), 1)
        stem, _, ext = names[0].rpartition(".")
        stamp, _, tail = stem.partition("_")
        self.assertTrue(stamp.isdigit())
        self.assertEqual(tail, "bin6")

    def test_a_sidecar_records_the_bin_rect_and_the_lighting(self):
        # Doc section 12.7: "plus a sidecar .json with bin index, rect,
        # timestamp, and the exposure/WB and field_level values from
        # state/camera_settings.json."
        self.settings.write_text(json.dumps(
            {"exposure": 250, "wb": 4600, "field_level": 1.0}),
            encoding="utf-8")
        w = self.build(image=food_field())
        w.on_message(self.cmd())
        self.wait()
        side = next((self.captures / "prawn").glob("*.json"))
        data = json.loads(side.read_text(encoding="utf-8"))
        self.assertEqual(data["bin"], 6)
        self.assertEqual(data["rect_cam"], [330.0, 250.0, 100.0, 100.0])
        self.assertEqual(data["lighting"]["field_level"], 1.0)
        self.assertIn("ts", data)

    def test_the_crop_saved_is_the_crop_asked_for(self):
        # The whole point of a dataset capture: the JPEG must be the bin's
        # own patch, not the whole frame. Checked by pixel value, since
        # food_field() gives each quadrant its own.
        import cv2
        w = self.build(image=food_field())
        w.on_message(self.cmd())
        self.wait()
        jpg = next((self.captures / "prawn").glob("*.jpg"))
        img = cv2.imread(str(jpg))
        self.assertEqual(img.shape[:2], (100, 100))
        self.assertGreater(int(img.mean()), 200)

    def test_mismatched_rects_and_labels_are_refused(self):
        # An unlabelled crop is training data nobody can use, and a
        # mislabelled one is worse than none.
        w = self.build(image=food_field())
        w.on_message(self.cmd(labels=["mushroom"]))
        reply = self.wait()
        self.assertFalse(reply["ok"])
        self.assertIn("labels", reply["error"])

    def test_a_burst_writes_one_file_per_shot_per_bin(self):
        w = self.build(image=food_field())
        w.on_message(self.cmd(burst=3, seconds=0.15))
        reply = self.wait(timeout=10)
        self.assertEqual(len(reply["files"]), 6)

    def test_a_burst_is_spread_over_the_seconds_it_was_given(self):
        # Doc section 12.7's reason for a burst is pose variation — "so
        # the operator can nudge the tray between frames". A burst that
        # fired four identical frames in 3 ms would satisfy the file count
        # and defeat the purpose.
        w = self.build(image=food_field())
        started = time.monotonic()
        w.on_message(self.cmd(burst=4, seconds=0.8))
        self.wait(timeout=10)
        self.assertGreater(time.monotonic() - started, 0.5)

    def test_stop_cancels_a_burst_in_flight(self):
        w = self.build(image=food_field())
        w.on_message(self.cmd(burst=20, seconds=6.0))
        time.sleep(0.4)
        w.on_message({"t": "cmd", "op": "stop"})
        reply = self.wait(timeout=10)
        self.assertTrue(reply["cancelled"])
        self.assertLess(len(reply["files"]), 40)

    def test_an_absurd_burst_is_clamped_rather_than_obeyed(self):
        w = self.build(image=food_field())
        w.on_message(self.cmd(burst=100000, seconds=0.2,
                              rects=[[10, 10, 20, 20, 0]], labels=["egg"]))
        reply = self.wait(timeout=20)
        self.assertLessEqual(len(reply["files"]), cmain.MAX_BURST)

    def test_a_capture_with_no_rects_is_refused(self):
        w = self.build(image=food_field())
        w.on_message(self.cmd(rects=[], labels=[]))
        self.assertFalse(self.wait()["ok"])


class TestRingRecovery(WorkerCase):

    def test_a_dead_segment_is_dropped_and_reattached_next_time(self):
        # Doc section 20.1: camera's restart "recreates shm, consumers
        # re-attach". A classifier that held a corpse reader forever would
        # never work again after the first camera restart.
        reader = FakeReader(dot_field())
        reader.next_read_raises = OSError("segment gone")
        opens = []

        def opener():
            opens.append(1)
            return reader
        source = cmain.RingSource(open_reader=opener)
        worker = cmain.Classifier(source=source, send=self._send,
                                  captures_dir=self.captures)
        worker.start()
        self.addCleanup(worker.stop)

        worker.on_message({"t": "cmd", "id": 1, "op": "detect_dots"})
        self.assertFalse(self.wait()["ok"])
        self.assertTrue(reader.closed)

        worker.on_message({"t": "cmd", "id": 2, "op": "detect_dots"})
        reply = self.wait()
        self.assertEqual(reply["t"], "dots")
        self.assertEqual(len(opens), 2)


if __name__ == "__main__":
    unittest.main()
