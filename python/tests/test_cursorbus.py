"""Tests for common/cursorbus.py — M5 build item 2 (doc sections 4, 4.6).

Run from the repo root:

    python -m unittest discover -s python/tests -v

Two ways of driving it, on purpose:

- A **fake socket** for everything about ordering and framing. Real UDP
  gives no way to force "these three datagrams are already buffered, in
  this order" — the kernel may deliver them in any order or drop them —
  so a test written against a real socket could pass on a version of
  `recv_latest` that returns the LAST packet rather than the HIGHEST seq,
  which is exactly the mutation that matters. The fake queue makes the
  reordering deterministic and the check therefore capable of failing.
- A **real bound socket pair** for one round trip, so the fake is checked
  against the thing it is standing in for at least once.
"""

import os
import socket
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hotpot.common import cursorbus  # noqa: E402


def hand(hid=1, role=cursorbus.ROLE_POINTER, x=100.0, y=200.0, conf=0.9):
    return cursorbus.Hand(id=hid, role=role, x=x, y=y, conf=conf)


class FakeSocket:
    """Enough socket for both classes: a queue in, a list out.

    `recvfrom` raises BlockingIOError when empty, which is what a real
    non-blocking UDP socket does and what `recv_latest`'s drain loop
    terminates on.
    """

    def __init__(self, incoming=None):
        self.incoming = list(incoming or [])
        self.sent = []          # (payload, target)
        self.closed = False
        self.fail_next = 0

    # -- receiver side --
    def setblocking(self, flag):
        self.blocking = flag

    def recvfrom(self, size):
        if not self.incoming:
            raise BlockingIOError("empty")
        return self.incoming.pop(0), ("127.0.0.1", 9999)

    # -- sender side --
    def sendto(self, payload, target):
        if self.fail_next > 0:
            self.fail_next -= 1
            raise OSError("simulated ICMP port unreachable")
        self.sent.append((payload, target))

    def close(self):
        self.closed = True


def datagram(seq, hands=(), ts=1.0):
    return cursorbus.encode(
        cursorbus.CursorFrame(seq=seq, ts=ts, hands=list(hands)))


class TestEncodeDecode(unittest.TestCase):

    def test_round_trip_keeps_every_field(self):
        frame = cursorbus.CursorFrame(seq=42, ts=1754838400.117, hands=[
            hand(3, cursorbus.ROLE_POINTER, 941.2, 510.8, 0.93),
            hand(4, cursorbus.ROLE_AMBIENT, 300.1, 700.4, 0.81),
        ])
        out = cursorbus.decode(cursorbus.encode(frame))
        self.assertEqual(out.seq, 42)
        self.assertAlmostEqual(out.ts, 1754838400.117, places=2)
        self.assertEqual([h.id for h in out.hands], [3, 4])
        self.assertEqual([h.role for h in out.hands],
                         [cursorbus.ROLE_POINTER, cursorbus.ROLE_AMBIENT])
        self.assertAlmostEqual(out.hands[0].x, 941.2, places=1)
        self.assertAlmostEqual(out.hands[1].y, 700.4, places=1)

    def test_the_wire_shape_is_doc_4_6s(self):
        # Not a round trip: the round trip above would pass on any pair of
        # matching encode/decode implementations, including one that
        # invented its own field names. This checks the bytes against the
        # doc, which is the contract oF's C++ parser is written to.
        import json
        raw = json.loads(datagram(7, [hand(3, cursorbus.ROLE_POINTER,
                                          941.2, 510.8, 0.93)]).decode())
        self.assertEqual(set(raw), {"seq", "ts", "hands"})
        self.assertEqual(set(raw["hands"][0]), {"id", "role", "x", "y", "conf"})
        self.assertNotIn("t", raw)   # not the control protocol

    def test_a_datagram_that_is_not_json_decodes_to_none(self):
        self.assertIsNone(cursorbus.decode(b"{not json"))
        self.assertIsNone(cursorbus.decode(b"\xff\xfe\xfd"))

    def test_a_json_array_is_not_a_frame(self):
        self.assertIsNone(cursorbus.decode(b"[1,2,3]"))

    def test_a_frame_with_no_seq_is_dropped(self):
        self.assertIsNone(cursorbus.decode(b'{"ts":1.0,"hands":[]}'))

    def test_a_bool_seq_is_not_an_int_seq(self):
        # True == 1 in Python, so a bare isinstance(seq, int) check accepts
        # it and the gate in recv_latest then compares booleans to ints.
        self.assertIsNone(cursorbus.decode(b'{"seq":true,"hands":[]}'))


class TestBadHandsAreDroppedNotDefaulted(unittest.TestCase):
    """A hand missing a coordinate must vanish, never arrive at (0,0).

    (0,0) is the top-left corner of the table and a real hit-test target,
    so a defaulted hand is not a cosmetic bug — it is a phantom pointer
    parked over the corner of the stage for as long as the tracker keeps
    sending malformed hands.
    """

    def test_a_hand_with_no_x_is_dropped(self):
        f = cursorbus.decode(b'{"seq":1,"hands":[{"id":1,"role":"pointer","y":5}]}')
        self.assertEqual(f.hands, [])

    def test_a_hand_with_a_nan_coordinate_is_dropped(self):
        f = cursorbus.decode(b'{"seq":1,"hands":[{"id":1,"role":"pointer","x":NaN,"y":5}]}')
        self.assertEqual(f.hands, [])

    def test_a_hand_with_an_unknown_role_is_dropped(self):
        f = cursorbus.decode(
            b'{"seq":1,"hands":[{"id":1,"role":"third_hand","x":1,"y":2}]}')
        self.assertEqual(f.hands, [])

    def test_a_good_hand_beside_a_bad_one_survives(self):
        f = cursorbus.decode(
            b'{"seq":1,"hands":[{"id":1,"role":"pointer","x":1,"y":2},'
            b'{"role":"ambient","x":3,"y":4}]}')
        self.assertEqual(len(f.hands), 1)
        self.assertEqual(f.hands[0].id, 1)


class TestPointer(unittest.TestCase):

    def test_pointer_returns_the_pointer_not_the_first_hand(self):
        f = cursorbus.CursorFrame(seq=1, ts=0.0, hands=[
            hand(1, cursorbus.ROLE_AMBIENT, 10.0, 10.0),
            hand(2, cursorbus.ROLE_POINTER, 20.0, 20.0),
        ])
        self.assertEqual(f.pointer().id, 2)

    def test_no_pointer_is_none_not_a_fallback_to_ambient(self):
        f = cursorbus.CursorFrame(seq=1, ts=0.0, hands=[
            hand(1, cursorbus.ROLE_AMBIENT, 10.0, 10.0)])
        self.assertIsNone(f.pointer())


class TestDrainToLatest(unittest.TestCase):
    """Doc section 4: "read the socket non-blocking until it is empty,
    keep the highest seq, discard the rest.\""""

    def make(self, incoming):
        sock = FakeSocket(incoming)
        return cursorbus.Receiver(sock=sock), sock

    def test_an_empty_socket_returns_none(self):
        rx, _ = self.make([])
        self.assertIsNone(rx.recv_latest())

    def test_one_datagram_comes_back(self):
        rx, _ = self.make([datagram(0, [hand()])])
        f = rx.recv_latest()
        self.assertEqual(f.seq, 0)
        self.assertEqual(len(f.hands), 1)

    def test_a_backlog_collapses_to_the_newest(self):
        rx, sock = self.make([datagram(i) for i in range(5)])
        f = rx.recv_latest()
        self.assertEqual(f.seq, 4)
        self.assertEqual(rx.dropped_stale, 4)
        # The socket really was emptied — a second call has nothing left,
        # which is what "never process a backlog" means in practice.
        self.assertEqual(sock.incoming, [])
        self.assertIsNone(rx.recv_latest())

    def test_out_of_order_arrival_keeps_the_highest_seq_not_the_last_read(self):
        # THE test in this file. A `recv_latest` that simply overwrites its
        # answer with each datagram it reads passes every other test here
        # and fails this one, because 3 arrives after 5.
        rx, _ = self.make([datagram(1), datagram(5), datagram(3)])
        self.assertEqual(rx.recv_latest().seq, 5)

    def test_a_late_straggler_on_the_next_tick_is_gated_away(self):
        # Rule 2: reordering across drains. Delivering seq 3 here would
        # step the cursor backwards for one frame.
        rx, sock = self.make([datagram(5)])
        self.assertEqual(rx.recv_latest().seq, 5)
        sock.incoming.append(datagram(3))
        self.assertIsNone(rx.recv_latest())
        self.assertEqual(rx.dropped_stale, 1)

    def test_the_same_seq_twice_is_not_delivered_twice(self):
        rx, sock = self.make([datagram(5)])
        self.assertEqual(rx.recv_latest().seq, 5)
        sock.incoming.append(datagram(5))
        self.assertIsNone(rx.recv_latest())

    def test_a_frame_with_no_hands_is_an_answer_not_a_none(self):
        # "The table is empty" and "nothing arrived" are different facts:
        # the first must clear a hover, the second must leave it alone.
        rx, _ = self.make([datagram(0, [])])
        f = rx.recv_latest()
        self.assertIsNotNone(f)
        self.assertEqual(f.hands, [])

    def test_a_malformed_datagram_does_not_stop_the_drain(self):
        rx, _ = self.make([b"garbage", datagram(9)])
        self.assertEqual(rx.recv_latest().seq, 9)
        self.assertEqual(rx.malformed, 1)

    def test_the_drain_is_bounded(self):
        # A stalled consumer must not turn one 16ms tick into an unbounded
        # loop over a second of buffered traffic.
        rx, sock = self.make([datagram(i) for i in range(cursorbus.MAX_DRAIN + 50)])
        rx.recv_latest()
        self.assertEqual(len(sock.incoming), 50)

    def test_reset_sequence_reopens_the_gate_for_a_restarted_tracker(self):
        rx, sock = self.make([datagram(40000)])
        self.assertEqual(rx.recv_latest().seq, 40000)
        sock.incoming.append(datagram(0))
        self.assertIsNone(rx.recv_latest())     # a restarted tracker looks stale
        rx.reset_sequence()
        sock.incoming.append(datagram(0))
        self.assertEqual(rx.recv_latest().seq, 0)


class TestSender(unittest.TestCase):

    def test_one_send_reaches_every_target_with_one_seq(self):
        sock = FakeSocket()
        tx = cursorbus.Sender(targets=[("127.0.0.1", 8770), ("127.0.0.1", 8771)],
                              sock=sock)
        tx.send([hand()], ts=1.0)
        self.assertEqual(len(sock.sent), 2)
        payloads = {p for p, _ in sock.sent}
        self.assertEqual(len(payloads), 1)   # byte-identical to both
        ports = sorted(t[1] for _, t in sock.sent)
        self.assertEqual(ports, [8770, 8771])

    def test_seq_advances_once_per_send_not_once_per_target(self):
        # Two targets, so an implementation that incremented inside the
        # send loop would produce 0, 1 for the first frame and 2, 3 for the
        # second — and core and oF would then disagree about which frame is
        # newest, which is the failure the shared counter exists to stop.
        sock = FakeSocket()
        tx = cursorbus.Sender(targets=[("127.0.0.1", 1), ("127.0.0.1", 2)],
                              sock=sock)
        seqs = [tx.send([], ts=0.0).seq for _ in range(3)]
        self.assertEqual(seqs, [0, 1, 2])

    def test_a_dead_target_does_not_stop_the_live_one(self):
        sock = FakeSocket()
        sock.fail_next = 1
        tx = cursorbus.Sender(targets=[("127.0.0.1", 1), ("127.0.0.1", 2)],
                              sock=sock)
        tx.send([], ts=0.0)
        self.assertEqual(len(sock.sent), 1)
        self.assertEqual(tx.failed, 1)

    def test_the_default_targets_are_doc_4_1s_two_ports(self):
        tx = cursorbus.Sender(sock=FakeSocket())
        self.assertEqual(tx.targets,
                         [("127.0.0.1", 8770), ("127.0.0.1", 8771)])


class TestOverRealUdp(unittest.TestCase):
    """One round trip over a genuinely bound socket, so the fake above is
    checked against the thing it stands in for.
    """

    def test_a_real_datagram_arrives_and_decodes(self):
        rx = cursorbus.Receiver(host="127.0.0.1", port=0)
        self.addCleanup(rx.close)
        tx = cursorbus.Sender(targets=[("127.0.0.1", rx.port)])
        self.addCleanup(tx.close)

        tx.send([hand(3, cursorbus.ROLE_POINTER, 941.2, 510.8, 0.93)], ts=2.0)

        # UDP on loopback is not instantaneous. Poll rather than sleep a
        # fixed amount: a fixed sleep is either flaky or slow.
        frame = None
        for _ in range(200):
            frame = rx.recv_latest()
            if frame is not None:
                break
            import time
            time.sleep(0.005)
        self.assertIsNotNone(frame, "no datagram arrived over real UDP")
        self.assertEqual(frame.seq, 0)
        self.assertAlmostEqual(frame.pointer().x, 941.2, places=1)

    def test_a_receiver_on_port_zero_reports_the_port_it_actually_got(self):
        rx = cursorbus.Receiver(host="127.0.0.1", port=0)
        self.addCleanup(rx.close)
        self.assertNotEqual(rx.port, 0)

    def test_a_real_datagram_survives_a_preceding_icmp_port_unreachable(self):
        """**Reproduces the bug that made every real-machine verification of
        this module look like silence, until it was chased down to here.**

        Windows (not POSIX — the failure this guards is Windows-only)
        delivers a queued `WSAECONNRESET` to the FIRST `recvfrom()` a
        socket makes on a LOCAL PORT that ever drew back an ICMP "port
        unreachable" — even a brand-new socket that never sent anything
        itself, and even when a real, correctly-addressed datagram is
        already queued behind it. On this dev machine, before the fix in
        `_disable_windows_connreset`, this made a freshly-bound `Receiver`
        never accept a single frame for the rest of the process's life —
        because every `run.py` restart leaves exactly this kind of stale
        ICMP behind on these two well-known ports, and this reproduces that
        directly rather than trusting the dev-rig observation alone.

        The steps: bind a throwaway port, close it (nobody home now), send
        a datagram INTO it from a still-open local socket bound to a
        second, target port (drawing the ICMP unreachable back to that
        target port), close the sender, then bind cursorbus.Receiver to
        that SAME target port and send it one real, well-formed cursor
        frame. Before the fix, that frame is eaten by the queued reset and
        this test times out with `frame is None`.
        """
        # A port that will definitely refuse the datagram sent to it below.
        dead = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        dead.bind(("127.0.0.1", 0))
        dead_port = dead.getsockname()[1]
        dead.close()

        # The port a Receiver will bind to in a moment. Used first by a
        # plain socket to draw the ICMP unreachable back onto it.
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.bind(("127.0.0.1", 0))
        target_port = probe.getsockname()[1]
        probe.sendto(b"x", ("127.0.0.1", dead_port))
        time.sleep(0.05)     # let the ICMP reply queue against target_port
        probe.close()

        rx = cursorbus.Receiver(host="127.0.0.1", port=target_port)
        self.addCleanup(rx.close)
        tx = cursorbus.Sender(targets=[("127.0.0.1", target_port)])
        self.addCleanup(tx.close)
        tx.send([hand(9, cursorbus.ROLE_POINTER, 42.0, 7.0, 0.9)], ts=1.0)

        frame = None
        for _ in range(200):
            frame = rx.recv_latest()
            if frame is not None:
                break
            time.sleep(0.005)
        self.assertIsNotNone(
            frame, "the real datagram was lost behind a stale ICMP reset")
        self.assertAlmostEqual(frame.pointer().x, 42.0, places=1)
        self.assertEqual(rx.port, rx._sock.getsockname()[1])


if __name__ == "__main__":
    unittest.main()
