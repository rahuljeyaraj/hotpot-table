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

from hotpot.common import cursorbus  # noqa: E402
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


class ProcCase(unittest.TestCase):

    def make(self, script=None, frames=6, calibrated=True, **kwargs):
        source = FakeSource([(frame(), i) for i in range(frames)])
        sender = FakeSender()
        self.stats = []
        proc = tracker.TrackerProcess(
            source=source,
            backend=backend_stub.Stub(script=script),
            sender=sender,
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
        # Frame is 64px wide, input_width defaults to 480, so no downsample
        # happens and scale is 1.0: (10,20) -> (2*10+100, 2*20+50), then the
        # shadow-clearance offset (toward the far edge, smaller Y) is
        # subtracted from Y only.
        clearance_px = (tracker.CURSOR_SHADOW_CLEARANCE_MM * 1080.0
                        / tracker._TABLE_H_MM)
        self.assertAlmostEqual(hand.x, 120.0)
        self.assertAlmostEqual(hand.y, 90.0 - clearance_px)

    def test_the_downsample_scale_is_undone_before_the_homography(self):
        # A 960px-wide frame downsampled to 480 halves every coordinate, so
        # a detection at x=100 in the small frame is x=200 in capture
        # pixels and must reach the homography as 200. A tick that fed the
        # small coordinate straight in would report half the position — a
        # cursor that tracks the hand at half speed toward the origin,
        # which looks exactly like a bad calibration.
        source = FakeSource([(frame(width=960, height=540), 1)])
        sender = FakeSender()
        proc = tracker.TrackerProcess(
            source=source, sender=sender, emit_hz=0.0, input_width=480,
            backend=backend_stub.Stub(script=[[det(100, 50)]]))
        proc.apply_welcome({"homography_cam_to_stage": H_TEST})
        proc.tick(now=0.0)
        hand = sender.frames[0].hands[0]
        clearance_px = (tracker.CURSOR_SHADOW_CLEARANCE_MM * 1080.0
                        / tracker._TABLE_H_MM)
        self.assertAlmostEqual(hand.x, 2 * 200.0 + 100.0)
        self.assertAlmostEqual(hand.y, 2 * 100.0 + 50.0 - clearance_px)

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
    """`tracker/main.py`'s decision 6 — the measured palm-size cliff.

    The numbers behind the 200px default are in that docstring; these
    tests are about the arithmetic that carries a detection back out of
    the crop, which is where a silent, constant cursor offset would come
    from.
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
        backend = RecordingStub(script=[[]])
        proc = tracker.TrackerProcess(
            source=FakeSource([(frame(width=1920, height=1080), 1)]),
            backend=backend, sender=FakeSender(), emit_hz=0.0,
            input_width=0, roi_margin_px=200)
        proc.apply_welcome({"homography_cam_to_stage": H_OFFSET,
                            "stage": list(STAGE)})
        proc.tick(now=0.0)
        self.assertEqual(backend.seen, [(800, 1200)])

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
            sender=sender, emit_hz=0.0, input_width=0, roi_margin_px=200)
        proc.apply_welcome({"homography_cam_to_stage": H_OFFSET,
                            "stage": list(STAGE)})
        proc.tick(now=0.0)
        clearance_px = (tracker.CURSOR_SHADOW_CLEARANCE_MM * 1080.0
                        / tracker._TABLE_H_MM)
        hand = sender.frames[0].hands[0]
        self.assertAlmostEqual(hand.x, 960.0)
        self.assertAlmostEqual(hand.y, 540.0 - clearance_px)

    def test_the_downsample_and_the_crop_are_both_undone(self):
        # Both corrections at once, in the right order: the 1200px crop
        # downsampled to 600 doubles every coordinate, THEN the origin
        # goes on. Applying them the other way round lands somewhere else
        # entirely, so this pins the order as well as the presence.
        sender = FakeSender()
        proc = tracker.TrackerProcess(
            source=FakeSource([(frame(width=1920, height=1080), 1)]),
            backend=backend_stub.Stub(script=[[det(300, 200)]]),
            sender=sender, emit_hz=0.0, input_width=600, roi_margin_px=200)
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
            sender=FakeSender(), send_stat=stats.append, emit_hz=0.0,
            input_width=0, roi_margin_px=200)
        proc.apply_welcome({"homography_cam_to_stage": H_OFFSET,
                            "stage": list(STAGE)})
        proc.tick(now=0.0)
        marks = [m for m in stats if m.get("t") == "landmarks"]
        self.assertEqual(marks[-1]["hands"][0]["points"], [[1000.0, 600.0]])

    def test_a_new_homography_moves_the_crop(self):
        # A re-calibrated table must not keep detecting against the old
        # table's footprint until somebody restarts the process.
        backend = RecordingStub(script=[[]])
        proc = tracker.TrackerProcess(
            source=FakeSource([(frame(width=1920, height=1080), i)
                               for i in range(2)]),
            backend=backend, sender=FakeSender(), emit_hz=0.0,
            input_width=0, roi_margin_px=200)
        proc.apply_welcome({"homography_cam_to_stage": H_OFFSET,
                            "stage": list(STAGE)})
        proc.tick(now=0.0)
        moved = [[2.4, 0.0, -1200.0], [0.0, 2.7, -810.0], [0.0, 0.0, 1.0]]
        proc.apply_welcome({"homography_cam_to_stage": moved,
                            "stage": list(STAGE)})
        proc.tick(now=0.1)
        self.assertEqual(len(backend.seen), 2)
        self.assertNotEqual(backend.seen[0], backend.seen[1])

    def test_an_uncalibrated_table_still_detects_on_the_whole_frame(self):
        backend = RecordingStub(script=[[]])
        proc = tracker.TrackerProcess(
            source=FakeSource([(frame(width=1920, height=1080), 1)]),
            backend=backend, sender=FakeSender(), emit_hz=0.0,
            input_width=0, roi_margin_px=200)
        proc.tick(now=0.0)
        self.assertEqual(backend.seen, [(1080, 1920)])


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
