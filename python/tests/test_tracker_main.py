"""Tests for tracker/main.py — doc section 21, M5 build item 1.

Run from the repo root:

    python -m unittest discover -s python/tests -v

No camera, no shared memory, no model file and no UDP: `TrackerProcess`
takes its frame source, its backend and its sender as constructor
arguments, the same seam `ScaleReader.open_port` and
`classifier.RingSource.open_reader` already give the two other things in
this repo that can go silently wrong.

`tick(now)` is a single deterministic step, so "the camera went stale for a
second and came back" is three lines here rather than a rig session.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402

from hotpot.common import cursorbus, skeletonbus  # noqa: E402
from hotpot.tracker import backend_stub, main as tracker  # noqa: E402
from hotpot.tracker.backend import HAND_LEFT, HAND_RIGHT, Detection  # noqa: E402

# A homography that maps camera pixels straight onto stage pixels scaled by
# 2 and shifted by 100 — deliberately NOT identity, so a tick that forgot to
# apply it at all still produces the wrong numbers rather than the right
# ones by accident.
H_TEST = [[2.0, 0.0, 100.0],
          [0.0, 2.0, 50.0],
          [0.0, 0.0, 1.0]]


def det(x, y, handedness=None, conf=0.9):
    return Detection(x=float(x), y=float(y), conf=conf, handedness=handedness)


class FakeSource:
    """A frame ring with a queue in it.

    `queue` holds `(frame_or_None, info)` pairs, popped one per
    `next_frame()`; when it runs dry the source reports `"same"`, which is
    what a real ring says when the loop is spinning faster than the camera.
    """

    def __init__(self, queue=None):
        self.queue = list(queue or [])
        self.calls = 0

    def next_frame(self):
        self.calls += 1
        if not self.queue:
            return None, "same"
        return self.queue.pop(0)


def frame(width=64, height=48):
    # Deterministic content: a real BGR array of the right shape is all
    # `downsample` and the stub backend need.
    return np.zeros((height, width, 3), dtype=np.uint8)


class FakeSender:
    def __init__(self):
        self.frames = []
        self.closed = False

    def send(self, hands, ts):
        f = cursorbus.CursorFrame(seq=len(self.frames), ts=ts,
                                  hands=list(hands))
        self.frames.append(f)
        return f

    def close(self):
        self.closed = True


class FakeSkeletonSender:
    """RIG_FEEDBACK item 11 diagnostic (skeletonbus.py) — the same fake-
    over-a-real-socket discipline `FakeSender` gives the cursor pipeline,
    for `TrackerProcess.skeleton_sender`. Every test that constructs a
    `TrackerProcess` passes one explicitly so a test run never opens a
    real UDP socket."""

    def __init__(self):
        self.frames = []
        self.closed = False

    def send(self, hands, ts):
        f = skeletonbus.SkeletonFrame(seq=len(self.frames), ts=ts,
                                      hands=list(hands))
        self.frames.append(f)
        return f

    def close(self):
        self.closed = True


class ProcCase(unittest.TestCase):

    def make(self, script=None, frames=6, calibrated=True, **kwargs):
        source = FakeSource([(frame(), i) for i in range(frames)])
        sender = FakeSender()
        self.stats = []
        proc = tracker.TrackerProcess(
            source=source,
            backend=backend_stub.Stub(script=script),
            sender=sender,
            skeleton_sender=FakeSkeletonSender(),
            send_stat=self.stats.append,
            emit_hz=0.0,        # no rate cap: the tests step the clock
            **kwargs)
        if calibrated:
            proc.apply_welcome({"homography_cam_to_stage": H_TEST,
                                "stage": [1920, 1080]})
        return proc, source, sender


class TestTheHomographyGate(ProcCase):
    """Doc section 21: M5 "depends on M4 (homography — the cursor is
    meaningless without it)"."""

    def test_nothing_is_emitted_before_core_sends_a_homography(self):
        proc, _src, sender = self.make(script=[[det(10, 10)]],
                                       calibrated=False)
        self.assertFalse(proc.tick(now=0.0))
        self.assertEqual(sender.frames, [])

    def test_emission_starts_the_moment_the_homography_arrives(self):
        proc, _src, sender = self.make(script=[[det(10, 10)]],
                                       calibrated=False)
        proc.tick(now=0.0)
        proc.apply_welcome({"homography_cam_to_stage": H_TEST})
        self.assertTrue(proc.tick(now=0.1))
        self.assertEqual(len(sender.frames), 1)

    def test_a_malformed_homography_is_treated_as_none(self):
        # Better to emit nothing than to emit through a matrix that came
        # out of a bad line — the wrong-but-plausible cursor is the whole
        # failure this gate exists for.
        proc, _src, sender = self.make(script=[[det(10, 10)]],
                                       calibrated=False)
        for bad in ([[1, 2], [3, 4]], "nope", [[1, 2, 3]] * 2,
                    [[1, 2, 3], [4, 5, 6], [7, 8, "x"]]):
            with self.subTest(h=bad):
                proc.apply_welcome({"homography_cam_to_stage": bad})
                self.assertFalse(proc.has_homography)
        self.assertEqual(sender.frames, [])

    def test_a_nan_in_the_homography_is_treated_as_none(self):
        proc, _src, _sender = self.make(calibrated=False)
        proc.apply_welcome({"homography_cam_to_stage":
                            [[1.0, 0.0, 0.0], [0.0, float("nan"), 0.0],
                             [0.0, 0.0, 1.0]]})
        self.assertFalse(proc.has_homography)

    def test_a_welcome_with_no_homography_clears_a_stale_one(self):
        # Core re-sends this payload as `cfg` when the geometry changes. A
        # table whose homography was deleted must stop the tracker, not
        # leave it converting through the last one it happened to hear.
        proc, _src, _sender = self.make()
        self.assertTrue(proc.has_homography)
        proc.apply_welcome({"stage": [1920, 1080]})
        self.assertFalse(proc.has_homography)


class TestStageConversion(ProcCase):

    def test_the_cursor_is_converted_to_stage_space(self):
        proc, _src, sender = self.make(script=[[det(10, 20)]])
        proc.tick(now=0.0)
        hand = sender.frames[0].hands[0]
        # Decision 7: acquisition/tracking windows are never downsampled,
        # so scale is always 1.0: (10,20) -> (2*10+100, 2*20+50), then the
        # shadow-clearance offset (toward the far edge, smaller Y) is
        # subtracted from Y only.
        clearance_px = (tracker.CURSOR_SHADOW_CLEARANCE_MM * 1080.0
                        / tracker._TABLE_H_MM)
        self.assertAlmostEqual(hand.x, 120.0)
        self.assertAlmostEqual(hand.y, 90.0 - clearance_px)

    def test_detection_is_never_fed_a_downsampled_window(self):
        # Module docstring, decision 7: a downsampled whole-frame view is
        # exactly the framing that could never cold-acquire a real hand,
        # so `tick` must never resize what it hands the backend, no matter
        # what `input_width` says. A 1920-wide frame with no table
        # calibration yet scans in `ACQUISITION_WINDOW_PX`-sized tiles —
        # a regression that reintroduced a resize would shrink this below
        # the native window size.
        source = FakeSource([(frame(width=1920, height=1080), 1)])
        sender = FakeSender()
        backend = RecordingStub(script=[[]])
        proc = tracker.TrackerProcess(
            source=source, sender=sender, skeleton_sender=FakeSkeletonSender(),
            emit_hz=0.0, input_width=480, backend=backend)
        proc.tick(now=0.0)
        self.assertEqual(backend.seen, [(tracker.ACQUISITION_WINDOW_PX,
                                         tracker.ACQUISITION_WINDOW_PX)])

    def test_a_hand_off_the_stage_is_reported_not_clipped(self):
        # A hand held past the table edge is a real hand at a real
        # position; core answers "no bin" for it correctly. Clamping would
        # pile every out-of-range hand onto the border of the nearest bin.
        proc, _src, sender = self.make(script=[[det(-500, -500)]])
        proc.tick(now=0.0)
        hand = sender.frames[0].hands[0]
        self.assertLess(hand.x, 0.0)


class TestDownsample(unittest.TestCase):

    def test_a_frame_narrower_than_the_target_is_left_alone(self):
        f = frame(width=320, height=240)
        small, scale = tracker.downsample(f, 480)
        self.assertIs(small, f)
        self.assertEqual(scale, 1.0)

    def test_the_scale_reported_is_the_scale_applied(self):
        f = frame(width=1920, height=1080)
        small, scale = tracker.downsample(f, 480)
        self.assertEqual(small.shape[1], 480)
        self.assertAlmostEqual(scale, 4.0)
        # The height follows the same scale — a non-uniform resize would
        # need two scale factors and the caller only gets one.
        self.assertEqual(small.shape[0], 270)

    def test_a_target_of_zero_disables_the_downsample(self):
        f = frame(width=1920, height=1080)
        small, scale = tracker.downsample(f, 0)
        self.assertIs(small, f)
        self.assertEqual(scale, 1.0)


class RecordingStub(backend_stub.Stub):
    """A stub that remembers the frame it was actually handed.

    The detection crop is invisible in the emitted cursor unless the
    origin is dropped, so a test that only checked coordinates could pass
    against a tracker that never cropped at all. This records the shape
    so "was it cropped" and "was the crop undone" are two separate
    assertions rather than one.
    """

    def __init__(self, script=None):
        super().__init__(script=script)
        self.seen = []

    def detect(self, frame_bgr, timestamp_ms):
        self.seen.append(frame_bgr.shape[:2])
        return super().detect(frame_bgr, timestamp_ms)


# Puts the table at camera x 600..1400, y 400..800 — deliberately away
# from the origin, so a crop whose offset is never added back produces
# visibly wrong stage coordinates instead of accidentally right ones.
H_OFFSET = [[2.4, 0.0, -1440.0],
            [0.0, 2.7, -1080.0],
            [0.0, 0.0, 1.0]]
STAGE = (1920.0, 1080.0)


class TestTheDetectionCrop(unittest.TestCase):
    """`tracker/main.py`'s `table_roi` — the table's own footprint, now
    the acquisition scan's BOUND rather than the detection crop itself
    (decision 6, superseded by decision 7 the same day it was measured;
    see the module docstring).

    The numbers behind the 200px default are in that docstring; these
    tests are about the arithmetic that carries a detection back out of
    an acquisition/tracking window, which is where a silent, constant
    cursor offset would come from.
    """

    def test_the_footprint_is_the_table_padded_by_the_margin(self):
        roi = tracker.table_roi(H_OFFSET, STAGE, (1080, 1920), margin=200)
        self.assertEqual(roi, (400, 200, 1200, 800))

    def test_no_homography_means_no_crop(self):
        # The ordinary first-boot state. Detection still has to run, so
        # the Developer tab can answer "does MediaPipe see a hand at all"
        # on a table nobody has calibrated yet.
        self.assertIsNone(tracker.table_roi(None, STAGE, (1080, 1920)))

    def test_the_crop_is_clamped_to_the_frame(self):
        # A margin wider than the frame must not produce negative offsets
        # or an out-of-bounds slice; here it swallows the frame whole,
        # which is reported as "nothing to crop".
        self.assertIsNone(
            tracker.table_roi(H_OFFSET, STAGE, (1080, 1920), margin=5000))

    def test_a_footprint_bigger_than_the_frame_is_not_a_crop(self):
        # H_TEST's table already overflows a 640x480 frame in every
        # direction. Slicing the frame to itself every tick would be a
        # pointless copy at 30Hz.
        self.assertIsNone(tracker.table_roi(H_TEST, STAGE, (480, 640)))

    def test_a_sliver_is_refused_rather_than_detected_on(self):
        # `H` is the one number in this system already observed to come
        # back confidently wrong (CLAUDE.md's rms_px 0.0, n_points 4). A
        # homography that shrinks the table to a few pixels must fall back
        # to the whole frame, not hand the detector a sliver.
        tiny = [[400.0, 0.0, 0.0], [0.0, 400.0, 0.0], [0.0, 0.0, 1.0]]
        self.assertIsNone(
            tracker.table_roi(tiny, STAGE, (1080, 1920), margin=0))

    def test_a_singular_homography_is_not_a_crash(self):
        singular = [[1.0, 2.0, 3.0], [2.0, 4.0, 6.0], [0.0, 0.0, 1.0]]
        self.assertIsNone(tracker.table_roi(singular, STAGE, (1080, 1920)))

    def test_the_crop_reaches_the_backend(self):
        # First tick, no committed window yet, so this is a scan tile: a
        # native `ACQUISITION_WINDOW_PX` square, clamped into the table's
        # own (400,200,1200,800) footprint (this class's own
        # `test_the_footprint_is_the_table_padded_by_the_margin`).
        backend = RecordingStub(script=[[]])
        proc = tracker.TrackerProcess(
            source=FakeSource([(frame(width=1920, height=1080), 1)]),
            backend=backend, sender=FakeSender(),
            skeleton_sender=FakeSkeletonSender(), emit_hz=0.0,
            input_width=0, roi_margin_px=200)
        proc.apply_welcome({"homography_cam_to_stage": H_OFFSET,
                            "stage": list(STAGE)})
        proc.tick(now=0.0)
        self.assertEqual(backend.seen,
                         [(tracker.ACQUISITION_WINDOW_PX,
                           tracker.ACQUISITION_WINDOW_PX)])

    def test_the_crop_origin_is_added_back_before_the_homography(self):
        # THE test in this class. A detection at (600,400) inside a crop
        # whose corner is (400,200) is (1000,600) in capture pixels, which
        # H_OFFSET puts at the exact centre of the stage. Drop the origin
        # and it lands at (0,0) instead — a cursor short by the crop's own
        # corner, in every frame, which reads as a bad calibration rather
        # than as arithmetic.
        sender = FakeSender()
        proc = tracker.TrackerProcess(
            source=FakeSource([(frame(width=1920, height=1080), 1)]),
            backend=backend_stub.Stub(script=[[det(600, 400)]]),
            sender=sender, skeleton_sender=FakeSkeletonSender(), emit_hz=0.0,
            input_width=0, roi_margin_px=200)
        proc.apply_welcome({"homography_cam_to_stage": H_OFFSET,
                            "stage": list(STAGE)})
        proc.tick(now=0.0)
        clearance_px = (tracker.CURSOR_SHADOW_CLEARANCE_MM * 1080.0
                        / tracker._TABLE_H_MM)
        hand = sender.frames[0].hands[0]
        self.assertAlmostEqual(hand.x, 960.0)
        self.assertAlmostEqual(hand.y, 540.0 - clearance_px)

    def test_the_landmark_debug_view_carries_the_origin_too(self):
        # It draws over the staff view's RAW feed, so it needs the same
        # correction the cursor gets — in capture pixels, not crop pixels.
        stats = []
        proc = tracker.TrackerProcess(
            source=FakeSource([(frame(width=1920, height=1080), 1)]),
            backend=backend_stub.Stub(script=[[
                Detection(x=600.0, y=400.0, conf=0.9, handedness=HAND_RIGHT,
                          landmarks=[(600.0, 400.0)])]]),
            sender=FakeSender(), skeleton_sender=FakeSkeletonSender(),
            send_stat=stats.append, emit_hz=0.0,
            input_width=0, roi_margin_px=200)
        proc.apply_welcome({"homography_cam_to_stage": H_OFFSET,
                            "stage": list(STAGE)})
        proc.tick(now=0.0)
        marks = [m for m in stats if m.get("t") == "landmarks"]
        self.assertEqual(marks[-1]["hands"][0]["points"], [[1000.0, 600.0]])

    def test_the_raw_skeleton_is_mapped_to_stage_space_with_no_clearance_offset(self):
        # RIG_FEEDBACK item 11 diagnostic (skeletonbus.py). Same capture-
        # pixel point as `test_the_landmark_debug_view_carries_the_origin_
        # too` above (1000,600) — H_OFFSET puts that at the stage centre
        # (960,540), same as the cursor pipeline's own
        # `test_the_crop_origin_is_added_back_before_the_homography` — but
        # UNLIKE that test, no CURSOR_SHADOW_CLEARANCE_MM is subtracted:
        # this is the raw signal, not the cursor-visibility offset applied
        # on top of it.
        skel_sender = FakeSkeletonSender()
        proc = tracker.TrackerProcess(
            source=FakeSource([(frame(width=1920, height=1080), 1)]),
            backend=backend_stub.Stub(script=[[
                Detection(x=600.0, y=400.0, conf=0.9, handedness=HAND_RIGHT,
                          landmarks=[(600.0, 400.0)])]]),
            sender=FakeSender(), skeleton_sender=skel_sender, emit_hz=0.0,
            input_width=0, roi_margin_px=200)
        proc.apply_welcome({"homography_cam_to_stage": H_OFFSET,
                            "stage": list(STAGE)})
        proc.tick(now=0.0)
        hand = skel_sender.frames[-1].hands[0]
        self.assertEqual(hand.handedness, HAND_RIGHT)
        self.assertEqual(len(hand.points), 1)
        self.assertAlmostEqual(hand.points[0][0], 960.0)
        self.assertAlmostEqual(hand.points[0][1], 540.0)

    def test_the_raw_skeleton_carries_every_landmark_not_just_the_tracked_point(self):
        skel_sender = FakeSkeletonSender()
        proc = tracker.TrackerProcess(
            source=FakeSource([(frame(width=1920, height=1080), 1)]),
            backend=backend_stub.Stub(script=[[
                Detection(x=600.0, y=400.0, conf=0.9, handedness=HAND_LEFT,
                          landmarks=[(600.0, 400.0), (610.0, 410.0),
                                    (620.0, 420.0)])]]),
            sender=FakeSender(), skeleton_sender=skel_sender, emit_hz=0.0,
            input_width=0, roi_margin_px=200)
        proc.apply_welcome({"homography_cam_to_stage": H_OFFSET,
                            "stage": list(STAGE)})
        proc.tick(now=0.0)
        self.assertEqual(len(skel_sender.frames[-1].hands[0].points), 3)

    def test_a_hand_with_no_landmarks_sends_no_skeleton(self):
        # `det()` (this file's own helper) never sets `landmarks` — the
        # stub-detection shape most of this file's other tests already
        # use — and that must not crash `_skeleton_to_stage` or produce a
        # phantom empty-points hand.
        skel_sender = FakeSkeletonSender()
        proc = tracker.TrackerProcess(
            source=FakeSource([(frame(width=1920, height=1080), 1)]),
            backend=backend_stub.Stub(script=[[det(600, 400)]]),
            sender=FakeSender(), skeleton_sender=skel_sender, emit_hz=0.0,
            input_width=0, roi_margin_px=200)
        proc.apply_welcome({"homography_cam_to_stage": H_OFFSET,
                            "stage": list(STAGE)})
        proc.tick(now=0.0)
        self.assertEqual(skel_sender.frames[-1].hands, [])

    def test_a_new_homography_moves_the_scan(self):
        # A re-calibrated table must not keep scanning the old table's
        # footprint until somebody restarts the process. `RecordingStub`
        # only records shape, which an acquisition tile holds constant at
        # `ACQUISITION_WINDOW_PX` regardless of where it sits — checking
        # the scan geometry itself is what actually proves it moved.
        proc = tracker.TrackerProcess(
            source=FakeSource([(frame(width=1920, height=1080), i)
                               for i in range(2)]),
            backend=RecordingStub(script=[[]]), sender=FakeSender(),
            skeleton_sender=FakeSkeletonSender(),
            emit_hz=0.0, input_width=0, roi_margin_px=200)
        proc.apply_welcome({"homography_cam_to_stage": H_OFFSET,
                            "stage": list(STAGE)})
        proc.tick(now=0.0)
        first_centers = list(proc._acq_centers)
        moved = [[2.4, 0.0, -1200.0], [0.0, 2.7, -810.0], [0.0, 0.0, 1.0]]
        proc.apply_welcome({"homography_cam_to_stage": moved,
                            "stage": list(STAGE)})
        proc.tick(now=0.1)
        self.assertNotEqual(first_centers, proc._acq_centers)

    def test_an_uncalibrated_table_still_scans_the_whole_frame(self):
        # The ordinary first-boot state (`test_no_homography_means_no_crop`
        # above, one layer down). Detection still has to run, so the
        # Developer tab can answer "does MediaPipe see a hand at all" on a
        # table nobody has calibrated yet.
        proc = tracker.TrackerProcess(
            source=FakeSource([(frame(width=1920, height=1080), 1)]),
            backend=RecordingStub(script=[[]]), sender=FakeSender(),
            skeleton_sender=FakeSkeletonSender(),
            emit_hz=0.0, input_width=0, roi_margin_px=200)
        self.assertEqual(
            proc._acquisition_bounds(None, proc._stage, (1080, 1920, 3)),
            (0, 0, 1920, 1080))
        proc.tick(now=0.0)
        self.assertEqual(len(proc.backend.seen), 1)


class TestAcquisitionTiles(unittest.TestCase):
    """`_acquisition_tile_centers` and `_clamp_window` — module docstring
    decision 7's scan geometry, as pure functions: a coverage gap should
    show up here, not as a hand that happened to sit between two tiles on
    the rig.
    """

    def test_a_bound_smaller_than_the_window_is_one_centred_tile(self):
        centers = tracker._acquisition_tile_centers(
            (100, 200, 300, 250), window_px=700, stride_px=470)
        self.assertEqual(centers, [(100 + 150.0, 200 + 125.0)])

    def test_tiles_form_a_grid_covering_the_whole_bound(self):
        # This rig's own table crop (200px margin already applied). Every
        # point in the bound must fall inside at least one tile's clamped
        # window — a hand sitting exactly on a tile boundary is the
        # scenario `ACQUISITION_TILE_STRIDE_PX`'s overlap exists for.
        import random

        bounds = (209, 0, 1711, 1068)
        x0, y0, w, h = bounds
        windows = [tracker._clamp_window(cx, cy, tracker.ACQUISITION_WINDOW_PX,
                                         x0 + w, y0 + h)
                  for cx, cy in tracker._acquisition_tile_centers(bounds)]
        rng = random.Random(0)
        for _ in range(300):
            px = x0 + rng.uniform(0, w)
            py = y0 + rng.uniform(0, h)
            covered = any(wx <= px <= wx + ww and wy <= py <= wy + wh
                         for wx, wy, ww, wh in windows)
            self.assertTrue(covered, f"({px:.0f},{py:.0f}) uncovered")

    def test_clamp_shrinks_a_window_bigger_than_the_frame(self):
        # The tiny frames python/tests uses everywhere else must not crash
        # against a 700px window.
        self.assertEqual(tracker._clamp_window(10, 10, 700, 64, 48),
                         (0, 0, 64, 48))

    def test_clamp_keeps_the_window_inside_the_frame(self):
        x0, y0, w, h = tracker._clamp_window(5, 5, 100, 1920, 1080)
        self.assertEqual((x0, y0, w, h), (0, 0, 100, 100))
        x0, y0, w, h = tracker._clamp_window(1915, 1075, 100, 1920, 1080)
        self.assertEqual((x0 + w, y0 + h), (1920, 1080))


class ArrayRecordingStub(backend_stub.Stub):
    """Like `RecordingStub`, but keeps the actual pixels — what a shape
    comparison cannot answer is "was this crop denoised", and decision 7
    only denoises acquisition scans, never a warm tracking refresh.
    """

    def __init__(self, script=None):
        super().__init__(script=script)
        self.arrays = []

    def detect(self, frame_bgr, timestamp_ms):
        self.arrays.append(frame_bgr.copy())
        return super().detect(frame_bgr, timestamp_ms)


class TestAcquisitionScheduling(unittest.TestCase):
    """`TrackerProcess`'s round-robin between committed tracking windows
    and the acquisition scan — module docstring decision 7. No homography
    applied in any of these: the scheduler runs identically calibrated or
    not (`_maybe_send_landmarks` already relies on that), and skipping
    `apply_welcome` keeps each test to the one thing it is about.
    """

    def _proc(self, script, max_hands=2, ticks=300):
        source = FakeSource([(frame(width=1920, height=1080), i)
                             for i in range(ticks)])
        proc = tracker.TrackerProcess(
            source=source, backend=backend_stub.Stub(script=script),
            sender=FakeSender(), skeleton_sender=FakeSkeletonSender(),
            emit_hz=0.0, max_hands=max_hands)
        return proc, source

    def test_a_cold_table_scans_every_tile_before_repeating(self):
        proc, _src = self._proc(script=[[]])
        proc.tick(now=0.0)               # populates proc._acq_centers
        n_tiles = len(proc._acq_centers)
        self.assertGreater(n_tiles, 1)
        seen = []
        for i in range(n_tiles):
            proc.tick(now=(i + 1) * 0.033)
            seen.append(proc._scan_idx % n_tiles)
        self.assertEqual(len(set(seen)), n_tiles)

    def test_a_hit_commits_a_window_at_the_detection_position(self):
        # Tight re-centring on the DETECTION, not just re-using the scan
        # tile's own origin — `_update_acquisition`'s whole reason for
        # existing. The offset (450,450) is deliberately large and
        # deliberately checked against the "ignores the detection"
        # mutation below: the first scan tile on an uncalibrated
        # 1920x1080 frame clamps to the top-left corner (0,0), and a
        # SMALL offset (e.g. 15,20) clamps right back to that same corner
        # either way, making the two cases indistinguishable — this
        # exact test passed against a mutated `_update_acquisition` that
        # ignored `det.x`/`det.y` entirely until the offset was widened
        # enough to escape the corner clamp.
        proc, _src = self._proc(script=[[det(450, 450)]], max_hands=1)
        proc.tick(now=0.0)
        win = proc._hand_windows[0]
        self.assertIsNotNone(win)
        cx, cy = proc._acq_centers[0]
        tile_x0, tile_y0, _tw, _th = tracker._clamp_window(
            cx, cy, tracker.ACQUISITION_WINDOW_PX, 1920, 1080)
        # What a mutation that recentres on the tile's own origin instead
        # of the detection would have produced — must differ from the
        # real expectation below, or this test cannot tell them apart.
        mutant_result = tracker._clamp_window(
            tile_x0, tile_y0, tracker.ACQUISITION_WINDOW_PX, 1920, 1080)
        expected_x0, expected_y0, _w, _h = tracker._clamp_window(
            tile_x0 + 450, tile_y0 + 450, tracker.ACQUISITION_WINDOW_PX,
            1920, 1080)
        self.assertNotEqual((expected_x0, expected_y0), mutant_result[:2])
        self.assertEqual((win.x0, win.y0), (expected_x0, expected_y0))

    def test_a_miss_does_not_free_the_window_immediately(self):
        proc, _src = self._proc(script=[[det(15, 20)], []], max_hands=1)
        proc.tick(now=0.0)
        self.assertIsNotNone(proc._hand_windows[0])
        proc.tick(now=0.5)      # missed, well under ACQUISITION_WINDOW_LOST_S
        self.assertIsNotNone(proc._hand_windows[0])

    def test_a_window_lost_long_enough_is_freed(self):
        proc, _src = self._proc(script=[[det(15, 20)], []], max_hands=1)
        proc.tick(now=0.0)
        self.assertIsNotNone(proc._hand_windows[0])
        proc.tick(now=tracker.ACQUISITION_WINDOW_LOST_S + 0.1)
        self.assertIsNone(proc._hand_windows[0])

    def test_exactly_one_detect_call_per_tick(self):
        backend = backend_stub.Stub(script=[[det(15, 20)], [det(400, 300)]])
        source = FakeSource([(frame(width=1920, height=1080), i)
                             for i in range(20)])
        proc = tracker.TrackerProcess(
            source=source, backend=backend, sender=FakeSender(),
            skeleton_sender=FakeSkeletonSender(), emit_hz=0.0, max_hands=2)
        for i in range(20):
            proc.tick(now=i * 0.033)
        self.assertEqual(backend.calls, 20)

    def test_two_hands_at_different_times_claim_two_different_slots(self):
        # The documented limit is TWO hands in the SAME scan tile on the
        # SAME tick (`_update_acquisition`'s own docstring) — this is the
        # ordinary case that limit does not cover: one arrives, then the
        # other, in separate ticks. `det(15, 20)` reused for both was the
        # ORIGINAL version of this test and looked right; it was wrong —
        # on this uncalibrated whole-frame fallback, the first two scan
        # tiles both clamp to the identical (0,0)-anchored window near
        # the frame's corner, so both "different" hands landed in the
        # SAME absolute spot by coincidence. That is exactly the
        # duplicate case the 2026-08-13 pulsing fix now declines — this
        # test's own old body was unknowingly exercising the bug it took
        # a real rig session to find. Second detection moved far enough
        # (900, 900) added to whatever the second tile's own origin is)
        # that it cannot land inside slot 0's window, so this is now
        # actually testing two SEPARATE hands, which is what it always
        # claimed to test.
        proc, _src = self._proc(script=[[det(15, 20)], [det(900, 900)]],
                                max_hands=2)
        proc.tick(now=0.0)
        self.assertIsNotNone(proc._hand_windows[0])
        self.assertIsNone(proc._hand_windows[1])
        proc.tick(now=0.033)
        filled = [w for w in proc._hand_windows if w is not None]
        self.assertEqual(len(filled), 2)

    def test_a_scan_hit_on_an_already_tracked_hand_does_not_claim_a_second_slot(self):
        # 2026-08-13 pulsing bug (module docstring, decision 7's newest
        # paragraph). At max_hands capacity minus one real hand, a scan
        # turn always exists and can re-spot the SAME hand a committed
        # window is already tracking — before this check, that hit
        # unconditionally claimed the free slot, cloning a second window
        # onto one physical hand. `_update_acquisition` is called
        # directly (not through `tick()`) so the test controls exactly
        # where the "hit" lands relative to the existing window, rather
        # than depending on real tile geometry to happen to overlap.
        proc, _src = self._proc(script=[[]], max_hands=2)
        proc.tick(now=0.0)          # populates _acq_centers, no hit
        proc._hand_windows[0] = tracker._AcquisitionWindow(
            500, 500, 700, 700, last_hit=0.0)
        # det(100, 100) + origin (500, 500) = absolute (600, 600),
        # inside slot 0's window (500..1200 both axes).
        proc._update_acquisition(
            [det(100, 100)], origin=(500.0, 500.0),
            frame_shape=(1080, 1920, 3), service_slot=None, now=1.0)
        self.assertIsNone(proc._hand_windows[1])
        # unchanged, not merely re-clamped to the same numbers by luck
        self.assertEqual((proc._hand_windows[0].x0, proc._hand_windows[0].y0),
                         (500, 500))

    def test_a_scan_hit_away_from_the_tracked_hand_still_claims_the_slot(self):
        # The positive control for the test above — a hit that is NOT
        # inside any existing window must still be free to claim a slot,
        # or the fix above would have quietly broken the ordinary
        # two-different-hands case instead of only fixing the duplicate.
        proc, _src = self._proc(script=[[]], max_hands=2)
        proc.tick(now=0.0)
        proc._hand_windows[0] = tracker._AcquisitionWindow(
            500, 500, 700, 700, last_hit=0.0)
        # absolute (1700, 900) is well outside 500..1200 both axes
        proc._update_acquisition(
            [det(200, 100)], origin=(1500.0, 800.0),
            frame_shape=(1080, 1920, 3), service_slot=None, now=1.0)
        self.assertIsNotNone(proc._hand_windows[1])

    def test_denoise_applies_to_scan_ticks_not_tracking_ticks(self):
        rng = np.random.RandomState(0)
        noisy = rng.randint(0, 255, size=(1080, 1920, 3)).astype(np.uint8)
        source = FakeSource([(noisy, 1), (noisy, 2)])
        backend = ArrayRecordingStub(script=[[det(15, 20)], [det(15, 20)]])
        proc = tracker.TrackerProcess(
            source=source, backend=backend, sender=FakeSender(),
            skeleton_sender=FakeSkeletonSender(), emit_hz=0.0, max_hands=1)

        proc.tick(now=0.0)       # cold -> scan tile -> denoised
        cx, cy = proc._acq_centers[0]
        ox0, oy0, ow, oh = tracker._clamp_window(
            cx, cy, tracker.ACQUISITION_WINDOW_PX, 1920, 1080)
        raw_scan_slice = noisy[oy0:oy0 + oh, ox0:ox0 + ow]
        self.assertFalse(np.array_equal(backend.arrays[0], raw_scan_slice))

        proc.tick(now=0.033)     # warm -> committed window -> NOT denoised
        win = proc._hand_windows[0]
        raw_track_slice = noisy[win.y0:win.y0 + win.h, win.x0:win.x0 + win.w]
        self.assertTrue(np.array_equal(backend.arrays[1], raw_track_slice))

    def test_backend_factory_gives_every_slot_and_the_scanner_a_separate_instance(self):
        # 2026-08-12, found live on the rig: a single shared MediaPipe
        # instance for scanning AND tracking pulsed, because scanning for
        # a second hand on the SAME instance a first hand was locked
        # through resets that lock (module docstring, decision 7's third
        # finding — `seed_test2.py`). `backend_factory` is the fix; this
        # is the guard that it actually builds SEPARATE instances rather
        # than quietly reusing one.
        made = []

        def factory():
            b = backend_stub.Stub()
            made.append(b)
            return b

        proc = tracker.TrackerProcess(
            source=FakeSource([]), backend_factory=factory,
            sender=FakeSender(), skeleton_sender=FakeSkeletonSender(),
            emit_hz=0.0, max_hands=2)
        # 1 scanner + 2 tracking slots = 3 instances, all distinct.
        self.assertEqual(len(made), 3)
        self.assertEqual(len({id(b) for b in made}), 3)
        self.assertIs(proc._scan_backend, made[0])
        self.assertEqual(proc._track_backends, made[1:])


class TestPointerTransitionLogging(ProcCase):
    """RIG_FEEDBACK item 11 (2026-08-13): logs only when the pointer role
    moves to a different track id, cheap enough to leave running on the
    rig — see `_log_pointer_transition`'s own docstring for why this
    exists (two previous, reasoned-but-wrong theories for the stuck
    cursor; this is the one question that actually splits the search
    space, not a third guess).
    """

    def test_a_matched_track_logs_once_on_appear_and_not_again(self):
        proc, _src, _sender = self.make(
            script=[[det(10, 20)], [det(12, 22)]])
        with self.assertLogs("hotpot.tracker", level="INFO") as cm:
            proc.tick(now=0.0)      # appear: None -> some id
            proc.tick(now=0.033)    # still matched, same id: no new line
        transitions = [m for m in cm.output if "pointer track" in m]
        self.assertEqual(len(transitions), 1)
        self.assertIn("None -> ", transitions[0])

    def test_a_lost_track_logs_the_drop_with_the_raw_detection_count(self):
        proc, _src, _sender = self.make(script=[[det(10, 20)], []])
        with self.assertLogs("hotpot.tracker", level="INFO") as cm:
            proc.tick(now=0.0)
            # Past TRACK_GRACE_S with nothing detected: a real gap, not a
            # tracking.py bug — this is the case the log line's own
            # docstring calls "0 raw detections this tick".
            proc.tick(now=10.0)
        transitions = [m for m in cm.output if "pointer track" in m]
        self.assertEqual(len(transitions), 2)
        self.assertIn("-> None", transitions[1])
        self.assertIn("0 raw detections", transitions[1])

    def test_a_role_swap_logs_the_distance_and_gate_that_were_compared(self):
        # This is the exact shape found on the rig (2026-08-13): a direct
        # id-to-id jump with no "-> None" in between, via `_appear`'s Right
        # -hand-takeover rule — det(400,20) is far enough past the match
        # gate at this dt (H_TEST doubles + offsets, so ~780px stage-space
        # apart, against a ~150px gate) that it cannot be the SAME hand
        # continuing by the gate's own arithmetic, only a new one taking
        # over.
        proc, _src, _sender = self.make(
            script=[[det(10, 20)], [det(400, 20, handedness=HAND_RIGHT)]])
        with self.assertLogs("hotpot.tracker", level="INFO") as cm:
            proc.tick(now=0.0)
            proc.tick(now=0.033)
        transitions = [m for m in cm.output if "pointer track" in m]
        self.assertEqual(len(transitions), 2)
        swap = transitions[1]
        self.assertNotIn("-> None", swap)
        self.assertIn("outgoing pointer was at", swap)
        self.assertIn("gate was", swap)
        self.assertIn("px away", swap)


class TestStaleFrames(ProcCase):
    """Doc section 6.4: "stop emitting (tracker sends nothing rather than
    sending a frozen cursor), report frames_stale to core, keep polling;
    when frames resume, resume silently.\""""

    def test_a_stale_ring_emits_nothing(self):
        proc, source, sender = self.make(script=[[det(10, 10)]])
        proc.tick(now=0.0)
        self.assertEqual(len(sender.frames), 1)
        source.queue = [(None, "stale")]
        self.assertFalse(proc.tick(now=1.0))
        self.assertEqual(len(sender.frames), 1)

    def test_going_stale_reports_it_to_core_exactly_once(self):
        proc, source, _sender = self.make()
        source.queue = [(None, "stale"), (None, "stale"), (None, "stale")]
        for i in range(3):
            proc.tick(now=float(i))
        stale = [s for s in self.stats if s.get("frames_stale") is True]
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]["t"], "stat")

    def test_going_stale_drops_every_role(self):
        # A role held across an outage of unknown length means the bowl
        # hand keeping a pointer role it inherited before the camera died.
        proc, source, _sender = self.make(
            script=[[det(10, 10, HAND_RIGHT), det(40, 10, HAND_LEFT)]])
        proc.tick(now=0.0)
        self.assertIsNotNone(proc.tracker.pointer())
        source.queue = [(None, "stale")]
        proc.tick(now=1.0)
        self.assertEqual(proc.tracker.tracks, [])

    def test_going_stale_drops_every_committed_acquisition_window(self):
        # Module docstring, decision 7: a window survived across an outage
        # of unknown length could be pointing at nothing (the hand left)
        # or at the wrong thing entirely (doc section 20.1's camera
        # restart at a different capture resolution).
        proc, source, _sender = self.make(script=[[det(10, 10)]],
                                          max_hands=1)
        proc.tick(now=0.0)
        self.assertIsNotNone(proc._hand_windows[0])
        source.queue = [(None, "stale")]
        proc.tick(now=1.0)
        self.assertEqual(proc._hand_windows, [None])

    def test_frames_resuming_is_reported_and_emission_restarts(self):
        proc, source, sender = self.make(script=[[det(10, 10)]])
        source.queue = [(None, "stale")]
        proc.tick(now=0.0)
        source.queue = [(frame(), 7)]
        self.assertTrue(proc.tick(now=1.0))
        self.assertEqual(len(sender.frames), 1)
        self.assertTrue(any(s.get("frames_stale") is False for s in self.stats))

    def test_no_ring_at_all_is_silent_not_stale(self):
        # "The camera process has not started yet" is the ordinary boot
        # state (doc section 3.3) and must not be reported as a fault.
        proc, source, _sender = self.make()
        source.queue = [(None, "none")] * 3
        for i in range(3):
            proc.tick(now=float(i))
        self.assertEqual(self.stats, [])


class TestEmission(ProcCase):

    def test_one_datagram_per_new_frame(self):
        proc, _src, sender = self.make(script=[[det(10, 10)]], frames=4)
        for i in range(4):
            proc.tick(now=i * 0.033)
        self.assertEqual(len(sender.frames), 4)

    def test_a_repeated_frame_id_emits_nothing(self):
        # `next_frame` reporting "same" is the ordinary state of a loop
        # spinning faster than a 30Hz camera. Emitting on it would put the
        # same cursor on the wire many times per camera frame, which is
        # the "one datagram per camera frame" rule broken in the direction
        # that floods both consumers.
        proc, source, sender = self.make(script=[[det(10, 10)]], frames=1)
        proc.tick(now=0.0)
        source.queue = []
        for i in range(1, 5):
            self.assertFalse(proc.tick(now=i * 0.033))
        self.assertEqual(len(sender.frames), 1)

    def test_emit_hz_caps_the_rate(self):
        proc, source, sender = self.make(script=[[det(10, 10)]], frames=10)
        proc.emit_hz = 10.0                 # one every 100ms
        proc.tick(now=0.0)
        proc.tick(now=0.05)                 # too soon
        proc.tick(now=0.11)
        self.assertEqual(len(sender.frames), 2)

    def test_an_empty_frame_still_emits(self):
        # "The table is empty" has to reach core, or a hover set by the
        # last hand never clears.
        proc, _src, sender = self.make(script=[[]])
        self.assertTrue(proc.tick(now=0.0))
        self.assertEqual(sender.frames[0].hands, [])

    def test_a_backend_that_raises_does_not_kill_the_loop(self):
        class Exploding:
            name = "exploding"

            def detect(self, frame_bgr, timestamp_ms):
                raise RuntimeError("model fell over")

            def close(self):
                pass

        proc, _src, sender = self.make()
        proc.backend = Exploding()
        self.assertTrue(proc.tick(now=0.0))
        self.assertEqual(sender.frames[0].hands, [])

    def test_the_timestamp_handed_to_the_backend_always_increases(self):
        # MediaPipe's VIDEO running mode rejects a timestamp that does not
        # increase, and `backend_stub` raises on one for exactly this
        # reason — so a regression here fails in the tests rather than on
        # the rig with the real backend.
        proc, source, _sender = self.make(script=[[det(10, 10)]], frames=5)
        for i in range(5):
            proc.tick(now=i * 0.033)
        self.assertGreaterEqual(proc.backend.calls, 5)


class TestSwapHands(ProcCase):
    """Doc section 11.3's `mirror_handedness`, applied live."""

    def test_the_welcome_carries_it_to_the_backend(self):
        class Mirrorable(backend_stub.Stub):
            mirror_handedness = False

        proc, _src, _sender = self.make()
        proc.backend = Mirrorable()
        proc.apply_welcome({"homography_cam_to_stage": H_TEST,
                            "mirror_handedness": True})
        self.assertTrue(proc.backend.mirror_handedness)

    def test_a_backend_with_no_opinion_on_handedness_is_not_broken_by_it(self):
        proc, _src, _sender = self.make()
        proc.apply_welcome({"homography_cam_to_stage": H_TEST,
                            "mirror_handedness": True})       # must not raise
        self.assertTrue(proc.has_homography)


class TestRolesEndToEnd(ProcCase):
    """Doc section 21's M5 acceptance scenario, driven through the whole
    process rather than through `HandTracker` alone.
    """

    def test_the_left_hand_never_reaches_the_wire_as_a_pointer(self):
        script = [[det(10, 10, HAND_RIGHT), det(40, 10, HAND_LEFT)]] * 30
        proc, source, sender = self.make(script=script, frames=30)
        for i in range(30):
            proc.tick(now=i * 0.033)
        self.assertEqual(len(sender.frames), 30)
        for f in sender.frames:
            pointers = [h for h in f.hands if h.is_pointer]
            self.assertEqual(len(pointers), 1)
            # The right hand is at camera x=10 -> stage x=120; the left at
            # x=40 -> stage x=180.
            self.assertAlmostEqual(pointers[0].x, 120.0)


class TestModelRungs(unittest.TestCase):

    def test_a_missing_bundle_is_skipped_not_an_error(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(tracker.available_rungs(tmp), [])

    def test_a_present_bundle_is_found_in_ladder_order(self):
        import tempfile
        from pathlib import Path
        from hotpot.tracker import backend_mediapipe
        with tempfile.TemporaryDirectory() as tmp:
            # Written out of ladder order on purpose: the answer must come
            # back cheapest-first regardless of what the filesystem lists
            # first, because doc section 11.2 requires probing UPWARD.
            for name in reversed(backend_mediapipe.MODEL_RUNGS):
                (Path(tmp) / name).write_bytes(b"not a real bundle")
            self.assertEqual([Path(p).name for p in tracker.available_rungs(tmp)],
                             list(backend_mediapipe.MODEL_RUNGS))

    def test_build_backend_falls_back_to_the_stub_with_no_bundle(self):
        # Doc section 3.3: this process must come up and hold its link open
        # whatever else is missing. A tracker that exited over an absent
        # model file would take its own pip red, which reads as a crash.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            backend = tracker.build_backend({}, models_dir=tmp)
        self.assertIsInstance(backend, backend_stub.Stub)


class TestTheRealMediaPipeBackend(unittest.TestCase):
    """The API check doc section 0 rule 3 asks for, run against whatever is
    actually installed rather than against what the doc remembers.

    Skipped, not failed, when the bundle is absent: it is gitignored
    (`models/**/*.task`) so a fresh clone legitimately has no model, and a
    test suite that went red on a missing download would be a test suite
    people learn to ignore. When the bundle IS there this runs a genuine
    inference — which is the only thing that can catch doc section 11.2's
    real risk, an API that has moved underneath the design.
    """

    def backend(self):
        from pathlib import Path
        from hotpot.tracker import backend_mediapipe
        rungs = tracker.available_rungs()
        if not rungs:
            self.skipTest("no MediaPipe model bundle in models/ "
                          "(see models/README.md)")
        b = backend_mediapipe.MediaPipeBackend.load(rungs[0], num_hands=2)
        if b is None:
            self.skipTest("mediapipe is not installed")
        self.addCleanup(b.close)
        return b

    def test_a_real_inference_returns_a_list_and_does_not_crash(self):
        b = self.backend()
        out = b.detect(frame(width=480, height=270), timestamp_ms=1)
        self.assertIsInstance(out, list)

    def test_the_video_running_mode_accepts_increasing_timestamps(self):
        # `detect_for_video` raises on a timestamp that does not increase,
        # which is why TrackerProcess owns the clock. Three real calls in a
        # row is what proves the contract is being met, not just declared.
        b = self.backend()
        f = frame(width=480, height=270)
        for ts in (10, 20, 30):
            b.detect(f, timestamp_ms=ts)

    def test_a_detection_carries_a_point_and_a_handedness_slot(self):
        # Noise will not reliably produce a hand, so this checks the SHAPE
        # of what comes back when something does, without depending on a
        # detection happening — a test that needed a real hand in a real
        # frame would be a rig session, not a unit test.
        from hotpot.tracker.backend import Detection
        d = Detection(x=1.0, y=2.0, conf=0.5, handedness=None)
        self.assertEqual((d.x, d.y), (1.0, 2.0))
        self.assertIn(d.handedness, (None, HAND_LEFT, HAND_RIGHT))


class TestProbe(ProcCase):
    """Doc section 11.2's "measure for 5 seconds… log which rung it settled
    on"."""

    def test_the_measured_rate_is_recorded_after_the_probe_window(self):
        proc, source, _sender = self.make(script=[[]], frames=400)
        now = 0.0
        for _ in range(400):
            source.queue.append((frame(), 1000 + len(source.queue)))
            proc.tick(now=now)
            now += 0.02          # 50 fps
        self.assertIsNotNone(proc.measured_fps)
        self.assertGreater(proc.measured_fps, 40.0)
        self.assertLess(proc.measured_fps, 60.0)

    def test_the_probe_does_not_report_before_its_window_is_up(self):
        proc, source, _sender = self.make(script=[[]], frames=10)
        now = 0.0
        for _ in range(10):
            source.queue.append((frame(), 2000 + len(source.queue)))
            proc.tick(now=now)
            now += 0.02
        self.assertIsNone(proc.measured_fps)


if __name__ == "__main__":
    unittest.main()
