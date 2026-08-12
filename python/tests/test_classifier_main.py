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


class NoisyReader(FakeReader):
    """A reader that advances `frame_id` and adds a different, deterministic
    noise pattern to each frame — what a real sensor does, and the only
    thing averaging can actually help with.

    `served` counts frames handed out and `repeat` makes it serve each
    frame_id more than once, which is what a consumer polling faster than
    the camera writes will really see.
    """

    def __init__(self, image, *, amplitude=40, repeat=1):
        super().__init__(image)
        self.amplitude = amplitude
        self.repeat = repeat
        self.served = 0
        self.distinct_ids = set()

    def read(self):
        self.reads += 1
        fid = self.served // self.repeat
        self.served += 1
        self.distinct_ids.add(fid)
        # Zero-mean over a full cycle of 4 ids, so the average of any
        # multiple of 4 frames is exactly the clean image. Deterministic:
        # a random fixture would make the assertion below flaky.
        offset = (fid % 4) - 1.5
        noisy = np.clip(self._image.astype(np.float32)
                        + offset * self.amplitude / 1.5, 0, 255)
        return FakeFrame(noisy.astype(np.uint8).tobytes(), frame_id=fid)


# Identity: the table-crop warp is a documented no-op at this matrix, so
# every pixel-value assertion below (crop content, quadrant means) holds
# exactly as it did before the warp step existed. `TestWarpThenCrop` below
# is what actually exercises a non-identity `h`.
IDENTITY_H = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


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

    def test_detect_dots_is_gone_along_with_automated_calibration(self):
        # Automated dot-projection calibration was removed outright (it
        # needed a dark, room-light-free rig this project never achieved —
        # CLAUDE.md's M4h/M4i/M4j); `detect_dots` must not silently still
        # work.
        w = self.build()
        w.on_message({"t": "cmd", "id": 4, "op": "detect_dots"})
        reply = self.wait()
        self.assertFalse(reply["ok"])
        self.assertIn("unknown command", reply["error"])

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

    def test_crop_rect_reports_the_offset_it_actually_used(self):
        patch, x0, y0 = cmain.crop_rect(food_field(), (330, 250, 100, 100))
        self.assertEqual((x0, y0), (330, 250))
        self.assertTrue((patch == 250).all())

    def test_crop_rect_offset_reflects_clamping_not_the_request(self):
        # A caller that adds the offset back to a point found in the crop
        # must get where the crop actually started, not where it asked to
        # start — otherwise a clamped ROI would shift every point by
        # however much it hung off the edge.
        _patch, x0, y0 = cmain.crop_rect(food_field(), (-50, -30, 100, 100))
        self.assertEqual((x0, y0), (0, 0))


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
                "labels": ["mushroom", "prawn"], "burst": 1,
                "h": IDENTITY_H, "stage_size": [640, 480]}
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

    def test_a_capture_with_no_homography_is_refused(self):
        w = self.build(image=food_field())
        w.on_message(self.cmd(h=None))
        reply = self.wait()
        self.assertFalse(reply["ok"])
        self.assertIn("homography", reply["error"])

    def test_a_capture_with_a_malformed_homography_is_refused(self):
        w = self.build(image=food_field())
        w.on_message(self.cmd(h=[[1, 0], [0, 1]]))
        reply = self.wait()
        self.assertFalse(reply["ok"])
        self.assertIn("homography", reply["error"])

    def test_a_capture_with_no_stage_size_is_refused(self):
        w = self.build(image=food_field())
        w.on_message(self.cmd(stage_size=None))
        reply = self.wait()
        self.assertFalse(reply["ok"])
        self.assertIn("stage size", reply["error"])

    def test_capture_warps_before_cropping(self):
        # A non-identity homography must actually be applied: a rect
        # placed in the warped canvas at (350, 50) has to come out holding
        # whatever the homography maps there FROM the raw frame, not
        # whatever raw pixel happens to sit at that same (x, y).
        #
        # shift_h moves raw content +200px in x. Warped position (350, 50)
        # therefore holds raw content from (150, 50) — food_field()'s
        # TOP-LEFT quadrant, value 10 — never the raw pixel at (350, 50)
        # itself, which is the TOP-RIGHT quadrant, value 90. A classifier
        # that forgot to warp would crop the wrong quadrant and this test
        # would see ~90 instead of ~10.
        import cv2
        shift_h = [[1.0, 0.0, 200.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        w = self.build(image=food_field())
        w.on_message(self.cmd(rects=[[350, 50, 100, 100, 0]], labels=["egg"],
                              h=shift_h, stage_size=[900, 480]))
        self.wait()
        jpg = next((self.captures / "egg").glob("*.jpg"))
        img = cv2.imread(str(jpg))
        self.assertLess(int(img.mean()), 50)


class TestRingRecovery(WorkerCase):

    def test_a_dead_segment_is_dropped_and_reattached_next_time(self):
        # Doc section 20.1: camera's restart "recreates shm, consumers
        # re-attach". A classifier that held a corpse reader forever would
        # never work again after the first camera restart. `capture` is
        # just a convenient command that touches the ring — any command
        # that reads a frame exercises the same reattachment path.
        reader = FakeReader(food_field())
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

        cmd = {"t": "cmd", "id": 1, "op": "capture",
               "rects": [[10, 10, 100, 100, 0]], "labels": ["mushroom"],
               "burst": 1, "h": IDENTITY_H, "stage_size": [640, 480]}
        worker.on_message(cmd)
        self.assertFalse(self.wait()["ok"])
        self.assertTrue(reader.closed)

        worker.on_message({**cmd, "id": 2})
        reply = self.wait()
        self.assertEqual(reply["t"], "captured")
        self.assertEqual(len(opens), 2)


class TestFrameAveraging(unittest.TestCase):
    """Restored from the old solver, which measured why it is needed: at
    calibration exposure a dot stands only 25-50 grey levels above the
    board, "which is the same order as this sensor's frame-to-frame noise".
    """

    def source(self, reader):
        return cmain.RingSource(open_reader=lambda: reader)

    def clean_field(self):
        """Mid-grey rather than `dot_field`'s 0/255. The noise has to fit
        inside the representable range at every pixel or clipping makes it
        asymmetric, and asymmetric noise does not cancel — the fixture
        would then be testing clipping rather than averaging.
        """
        img = np.full((240, 320, 3), 60, dtype=np.uint8)
        ys, xs = np.ogrid[:240, :320]
        for cx, cy in ((80, 60), (240, 60), (240, 180), (80, 180)):
            img[(xs - cx) ** 2 + (ys - cy) ** 2 <= 144] = 200
        return img

    def test_averaging_cancels_the_noise(self):
        clean = self.clean_field()
        reader = NoisyReader(clean)
        got = self.source(reader).averaged_frame(8)
        # 8 frames spans two full noise cycles, so the average is the clean
        # image to within rounding.
        self.assertLess(float(np.abs(got.astype(np.float32)
                                     - clean.astype(np.float32)).max()), 2.0)

    def test_a_single_frame_is_much_worse(self):
        # The control. Without it, the assertion above could be passing on
        # a fixture that was never noisy.
        clean = self.clean_field()
        reader = NoisyReader(clean)
        one = self.source(reader).frame()
        self.assertGreater(float(np.abs(one.astype(np.float32)
                                        - clean.astype(np.float32)).max()), 20.0)

    def test_it_collects_distinct_frames_not_one_frame_n_times(self):
        # THE TRAP. `frame()` returns whatever is currently in the ring, so
        # a loop that just called it `count` times would average a frame
        # with itself — a no-op that produces a clean-looking result and a
        # plausible runtime. Serving each frame_id 3 times simulates a
        # consumer polling faster than the camera writes.
        reader = NoisyReader(dot_field(), repeat=3)
        self.source(reader).averaged_frame(6)
        self.assertGreaterEqual(len(reader.distinct_ids), 6)

    def test_a_count_of_one_or_less_is_just_a_frame(self):
        reader = NoisyReader(dot_field())
        self.assertEqual(self.source(reader).averaged_frame(1).shape,
                         dot_field().shape)
        self.assertEqual(reader.reads, 1)

    def test_a_stalled_ring_falls_back_rather_than_hanging(self):
        # FakeReader never advances frame_id, so nothing new ever arrives.
        # Degrading to one frame is right: a slow camera should cost the
        # calibration its noise floor, not the ability to calibrate.
        reader = FakeReader(dot_field())
        got = self.source(reader).averaged_frame(40, timeout_s=0.2)
        self.assertEqual(got.shape, dot_field().shape)


if __name__ == "__main__":
    unittest.main()
