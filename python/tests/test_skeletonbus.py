"""Tests for common/skeletonbus.py — RIG_FEEDBACK item 11 diagnostic.

Same fake-socket-plus-one-real-round-trip structure as test_cursorbus.py,
scoped down to what this module actually adds on top of that one: many
points per hand instead of one, no `role`, one target instead of two.
The drain-to-latest rules themselves are exercised here too rather than
assumed identical, since a future edit to one module is not guaranteed to
be mirrored in the other.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hotpot.common import skeletonbus  # noqa: E402


def skel_hand(handedness="Right", conf=0.9, points=((1.0, 2.0), (3.0, 4.0))):
    return skeletonbus.SkeletonHand(handedness=handedness, conf=conf,
                                    points=[tuple(p) for p in points])


class FakeSocket:
    def __init__(self, incoming=None):
        self.incoming = list(incoming or [])
        self.sent = []
        self.closed = False
        self.fail_next = 0

    def setblocking(self, flag):
        self.blocking = flag

    def recvfrom(self, size):
        if not self.incoming:
            raise BlockingIOError("empty")
        return self.incoming.pop(0), ("127.0.0.1", 9999)

    def sendto(self, payload, target):
        if self.fail_next > 0:
            self.fail_next -= 1
            raise OSError("simulated ICMP port unreachable")
        self.sent.append((payload, target))

    def close(self):
        self.closed = True


def datagram(seq, hands=(), ts=1.0):
    return skeletonbus.encode(
        skeletonbus.SkeletonFrame(seq=seq, ts=ts, hands=list(hands)))


class TestEncodeDecode(unittest.TestCase):

    def test_round_trip_keeps_every_point(self):
        frame = skeletonbus.SkeletonFrame(seq=5, ts=1.5, hands=[
            skel_hand("Left", 0.8, [(10.0, 20.0), (30.0, 40.5)]),
        ])
        out = skeletonbus.decode(skeletonbus.encode(frame))
        self.assertEqual(out.seq, 5)
        self.assertEqual(len(out.hands), 1)
        self.assertEqual(out.hands[0].handedness, "Left")
        self.assertEqual(out.hands[0].points,
                         [(10.0, 20.0), (30.0, 40.5)])

    def test_the_wire_shape_has_no_role_and_no_id(self):
        # Unlike cursorbus.Hand — there is no track yet at this point in
        # the pipeline (skeletonbus.py's own module docstring).
        import json
        raw = json.loads(datagram(1, [skel_hand()]).decode())
        self.assertEqual(set(raw), {"seq", "ts", "hands"})
        self.assertEqual(set(raw["hands"][0]),
                         {"handedness", "conf", "points"})

    def test_garbage_decodes_to_none(self):
        self.assertIsNone(skeletonbus.decode(b"{not json"))
        self.assertIsNone(skeletonbus.decode(b"[1,2,3]"))
        self.assertIsNone(skeletonbus.decode(b'{"ts":1.0,"hands":[]}'))

    def test_a_hand_with_no_points_is_dropped(self):
        f = skeletonbus.decode(
            b'{"seq":1,"hands":[{"handedness":"Left","conf":0.5,"points":[]}]}')
        self.assertEqual(f.hands, [])

    def test_a_nan_point_is_dropped_but_a_good_one_beside_it_survives(self):
        f = skeletonbus.decode(
            b'{"seq":1,"hands":[{"handedness":null,"conf":0.5,'
            b'"points":[[NaN,1],[2,3]]}]}')
        self.assertEqual(f.hands[0].points, [(2.0, 3.0)])

    def test_an_unknown_handedness_string_becomes_none_not_a_dropped_hand(self):
        # A hand is still a hand even if MediaPipe's label is unreadable —
        # only a coordinate failure should remove one from the frame.
        f = skeletonbus.decode(
            b'{"seq":1,"hands":[{"handedness":"sideways","conf":0.5,'
            b'"points":[[1,2]]}]}')
        self.assertEqual(len(f.hands), 1)
        self.assertIsNone(f.hands[0].handedness)


class TestDrainToLatest(unittest.TestCase):

    def make(self, incoming):
        sock = FakeSocket(incoming)
        return skeletonbus.Receiver(sock=sock), sock

    def test_an_empty_socket_returns_none(self):
        rx, _ = self.make([])
        self.assertIsNone(rx.recv_latest())

    def test_a_backlog_collapses_to_the_newest(self):
        rx, sock = self.make([datagram(i) for i in range(5)])
        f = rx.recv_latest()
        self.assertEqual(f.seq, 4)
        self.assertEqual(rx.dropped_stale, 4)
        self.assertEqual(sock.incoming, [])

    def test_out_of_order_arrival_keeps_the_highest_seq(self):
        rx, _ = self.make([datagram(1), datagram(5), datagram(3)])
        self.assertEqual(rx.recv_latest().seq, 5)

    def test_a_late_straggler_on_the_next_tick_is_gated_away(self):
        rx, sock = self.make([datagram(5)])
        self.assertEqual(rx.recv_latest().seq, 5)
        sock.incoming.append(datagram(3))
        self.assertIsNone(rx.recv_latest())

    def test_a_malformed_datagram_does_not_stop_the_drain(self):
        rx, _ = self.make([b"garbage", datagram(9)])
        self.assertEqual(rx.recv_latest().seq, 9)
        self.assertEqual(rx.malformed, 1)

    def test_the_drain_is_bounded(self):
        rx, sock = self.make(
            [datagram(i) for i in range(skeletonbus.MAX_DRAIN + 50)])
        rx.recv_latest()
        self.assertEqual(len(sock.incoming), 50)

    def test_reset_sequence_reopens_the_gate(self):
        rx, sock = self.make([datagram(40000)])
        self.assertEqual(rx.recv_latest().seq, 40000)
        sock.incoming.append(datagram(0))
        self.assertIsNone(rx.recv_latest())
        rx.reset_sequence()
        sock.incoming.append(datagram(0))
        self.assertEqual(rx.recv_latest().seq, 0)


class TestSender(unittest.TestCase):

    def test_sends_to_its_one_target_only(self):
        sock = FakeSocket()
        tx = skeletonbus.Sender(target=("127.0.0.1", 8772), sock=sock)
        tx.send([skel_hand()], ts=1.0)
        self.assertEqual(len(sock.sent), 1)
        self.assertEqual(sock.sent[0][1], ("127.0.0.1", 8772))

    def test_seq_advances_once_per_send(self):
        sock = FakeSocket()
        tx = skeletonbus.Sender(sock=sock)
        seqs = [tx.send([], ts=0.0).seq for _ in range(3)]
        self.assertEqual(seqs, [0, 1, 2])

    def test_a_send_failure_does_not_raise(self):
        sock = FakeSocket()
        sock.fail_next = 1
        tx = skeletonbus.Sender(sock=sock)
        tx.send([], ts=0.0)   # must not raise
        self.assertEqual(tx.failed, 1)


class TestOverRealUdp(unittest.TestCase):

    def test_a_real_datagram_arrives_and_decodes(self):
        rx = skeletonbus.Receiver(host="127.0.0.1", port=0)
        self.addCleanup(rx.close)
        tx = skeletonbus.Sender(target=("127.0.0.1", rx.port))
        self.addCleanup(tx.close)

        tx.send([skel_hand("Right", 0.7, [(941.2, 510.8)])], ts=2.0)

        frame = None
        for _ in range(200):
            frame = rx.recv_latest()
            if frame is not None:
                break
            import time
            time.sleep(0.005)
        self.assertIsNotNone(frame, "no datagram arrived over real UDP")
        self.assertEqual(frame.seq, 0)
        self.assertAlmostEqual(frame.hands[0].points[0][0], 941.2, places=1)


if __name__ == "__main__":
    unittest.main()
