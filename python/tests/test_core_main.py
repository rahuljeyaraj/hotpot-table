"""Tests for core/main.py — M0 build item 7, plus M1 build item 3 (the
five domain modules wired in and broadcasting `state` at 60Hz).

Run from the repo root (after `pip install -r python/requirements.txt`):

    python -m unittest discover -s python/tests -v

Exercises the whole wiring end to end: a real wire.Client stands in for a
sibling process, a real WebSocket client (the `websockets` library, same
as core/web/server.py uses on the server side) stands in for a staff
tablet, and the assertion is that a hello/heartbeat/disconnect on one
side shows up as a pip transition pushed out the other — the actual thing
M0.7 has to prove, not just that each half works in isolation. The M1
classes below do the same thing for `state`: a wire.Client named `of`
stands in for the renderer and the assertion is that core's Cart/BinMap
mutations show up in the next broadcast, not just that pricing.total()
is right in isolation (test_pricing.py already covers that).
"""

import json
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from websockets.sync.client import connect  # noqa: E402

from hotpot.common import health  # noqa: E402
from hotpot.common import log as hlog  # noqa: E402
from hotpot.common import wire  # noqa: E402
from hotpot.core import main as coremain  # noqa: E402

DEADLINE = 5.0


def wait_for(pred, timeout=DEADLINE, tick=0.01):
    """Poll until pred() is truthy. Returns the value, or False on timeout."""
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(tick)
    return False


def pip_colour(msg, who):
    for p in msg.get("pips", []):
        if p["who"] == who:
            return p["colour"]
    return None


class CoreCase(unittest.TestCase):
    """A real Core on ephemeral loopback ports, torn down after."""

    def setUp(self):
        hlog.reset()
        self.addCleanup(hlog.reset)
        self.core = coremain.start(
            control_host="127.0.0.1", control_port=0,
            web_host="127.0.0.1", web_port=0)
        self._wire_clients = []
        self._ws_clients = []

    def tearDown(self):
        for c in self._wire_clients:
            c.stop()
        for c in self._ws_clients:
            try:
                c.close()
            except Exception:
                pass
        self.core.stop()

    def wire_client(self, who, on_message=None):
        c = wire.Client("127.0.0.1", self.core.control_port, who, on_message=on_message)
        self._wire_clients.append(c)
        c.start()
        return c

    def ws(self):
        c = connect(f"ws://127.0.0.1:{self.core.web_port}/ws", open_timeout=DEADLINE)
        self._ws_clients.append(c)
        return c

    def recv_json(self, c, timeout=DEADLINE):
        return json.loads(c.recv(timeout=timeout))

    def recv_until(self, c, pred, timeout=DEADLINE):
        """Read frames until one satisfies `pred`, or give up. Needed
        because a pip change is pushed as a fresh broadcast, not a diff —
        the message that matters may not be the very next one on the wire
        if two transitions land close together.
        """
        end = time.time() + timeout
        while time.time() < end:
            remaining = max(0.05, end - time.time())
            try:
                msg = self.recv_json(c, timeout=remaining)
            except TimeoutError:
                break
            if pred(msg):
                return msg
        return None


class TestBinding(CoreCase):

    def test_both_ports_are_bound_by_the_time_start_returns(self):
        self.assertNotEqual(self.core.control_port, 0)
        self.assertNotEqual(self.core.web_port, 0)


class TestSixPipsSeeded(CoreCase):

    def test_all_six_process_names_appear_even_before_anyone_connects(self):
        msg = self.recv_json(self.ws())
        self.assertEqual(msg["t"], "pips")
        whos = {p["who"] for p in msg["pips"]}
        self.assertEqual(whos, set(health.PROCESSES))

    def test_a_never_connected_process_is_red(self):
        msg = self.recv_json(self.ws())
        self.assertEqual(pip_colour(msg, "camera"), "red")


class TestConnectAndDisconnect(CoreCase):

    def test_hello_lights_the_pip_green_over_the_websocket(self):
        w = self.ws()
        self.recv_json(w)   # the seed snapshot; camera is still red in it
        self.wire_client("camera")
        msg = self.recv_until(w, lambda m: pip_colour(m, "camera") == "green")
        self.assertIsNotNone(msg, "camera never turned green on the socket")

    def test_disconnect_turns_it_red_again(self):
        w = self.ws()
        self.recv_json(w)
        client = self.wire_client("camera")
        self.assertTrue(client.wait_connected(DEADLINE))
        self.assertIsNotNone(
            self.recv_until(w, lambda m: pip_colour(m, "camera") == "green"))

        client.stop()
        msg = self.recv_until(w, lambda m: pip_colour(m, "camera") == "red")
        self.assertIsNotNone(msg, "camera never turned red again after the link dropped")


class TestSelfHeartbeat(CoreCase):

    def test_core_lights_its_own_pip(self):
        """common/health.py's rule: core proves its main loop is alive by
        beating its own pip, rather than being hardcoded green — a wedged
        loop with a live web thread must still show up red.
        """
        self.assertTrue(wait_for(lambda: self.core.registry.status("core") == "up"))


class TestStateBroadcast(CoreCase):
    """M1 build item 3 (doc section 21): pricing/cart/binmap/i18n/fsm
    wired into Core, serialised as doc section 4.3's `state` message and
    sent to `of` at a fixed rate.
    """

    def of_client(self, timeout=DEADLINE):
        """A wire.Client named `of`, plus the list of `state` messages it
        has received so far and the lock guarding that list.
        """
        msgs = []
        lock = threading.Lock()

        def on_msg(m):
            if m.get("t") == "state":
                with lock:
                    msgs.append(m)

        c = self.wire_client("of", on_message=on_msg)
        self.assertTrue(c.wait_connected(timeout), "of never got a welcome")
        return c, msgs, lock

    def wait_for_n(self, msgs, lock, n, timeout=DEADLINE):
        def enough():
            with lock:
                return len(msgs) >= n
        self.assertTrue(wait_for(enough, timeout), f"never received {n} state message(s)")

    def test_shape_matches_doc_4_3(self):
        c, msgs, lock = self.of_client()
        self.wait_for_n(msgs, lock, 1)
        with lock:
            msg = msgs[0]
        self.assertEqual(msg["t"], "state")
        self.assertEqual(msg["mode"], "diner")
        self.assertEqual(msg["locale"], "en")
        self.assertEqual(len(msg["bins"]), 8)
        self.assertEqual(msg["widgets"], [])
        self.assertEqual(msg["overlay"], {"kind": "none"})
        self.assertIn("style", msg["fluid"])
        self.assertIn("amount", msg["total"])
        self.assertIn("text", msg["total"])

    def test_bins_are_seeded_from_the_catalogue_in_order_and_billable(self):
        c, msgs, lock = self.of_client()
        self.wait_for_n(msgs, lock, 1)
        with lock:
            msg = msgs[0]
        ids = self.core.catalogue.ids()
        for i, b in enumerate(msg["bins"]):
            self.assertTrue(b["resolved"], f"bin {i} not resolved")
            item = self.core.catalogue.item(ids[i])
            self.assertEqual(b["label"], item.names["en"])
            self.assertEqual(b["grams"], 500)   # MOCK_SEED_GRAMS, nothing picked yet
            self.assertEqual(b["picked"], 0)
            self.assertEqual(b["hl"], "none")
            self.assertEqual(b["stock"], "ok")

    def test_total_starts_at_zero(self):
        c, msgs, lock = self.of_client()
        self.wait_for_n(msgs, lock, 1)
        with lock:
            amount = msgs[0]["total"]["amount"]
        self.assertEqual(amount, 0.0)

    def test_a_mock_pick_shows_up_in_the_next_broadcast(self):
        """Bypasses the developer panel (build item 5, not built yet) and
        pokes Cart directly — the same entry point mock controls will
        call. Doc section 12.8's cycle includes 45g.
        """
        c, msgs, lock = self.of_client()
        self.wait_for_n(msgs, lock, 1)

        self.core.cart.mock_pick(3, 45)

        def bin3_picked_45():
            with lock:
                return any(m["bins"][3]["picked"] == 45 for m in msgs)
        self.assertTrue(wait_for(bin3_picked_45), "bin 3's pick never appeared in a broadcast")

        with lock:
            last = msgs[-1]
        item = self.core.catalogue.item(self.core.catalogue.ids()[3])
        expected_price = round(45 / 100.0 * item.price_per_100g, 2)
        self.assertEqual(last["bins"][3]["grams"], 455)      # 500 seeded - 45
        self.assertEqual(last["bins"][3]["price"], expected_price)
        self.assertEqual(last["bins"][3]["hl"], "picked")
        self.assertEqual(last["total"]["amount"], expected_price)

    def test_seq_increases_message_to_message(self):
        c, msgs, lock = self.of_client()
        self.wait_for_n(msgs, lock, 5)
        with lock:
            seqs = [m["seq"] for m in msgs[:5]]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(seqs), len(set(seqs)))   # no repeats
        self.assertGreater(seqs[-1], seqs[0])

    def test_flows_faster_than_an_accidental_1hz_bug_would(self):
        """Not a precise 60Hz measurement — timer coarseness on a dev
        machine makes that flaky — just a floor high enough to catch the
        loop firing at the wrong order of magnitude.
        """
        c, msgs, lock = self.of_client()
        self.wait_for_n(msgs, lock, 30, timeout=3.0)

    def test_only_of_gets_state_not_a_sibling_process(self):
        # tracker is a real process name (health.PROCESSES) but doc
        # section 4.3 is core -> of specifically.
        tracker_msgs = []
        tracker_lock = threading.Lock()

        def on_msg(m):
            with tracker_lock:
                tracker_msgs.append(m)

        tracker = self.wire_client("tracker", on_message=on_msg)
        self.assertTrue(tracker.wait_connected(DEADLINE))

        # Proof the broadcaster is genuinely running during this window,
        # not merely quiet for everyone.
        _, of_msgs, of_lock = self.of_client()
        self.wait_for_n(of_msgs, of_lock, 3)

        with tracker_lock:
            state_msgs = [m for m in tracker_msgs if m.get("t") == "state"]
        self.assertEqual(state_msgs, [])


class TestStop(unittest.TestCase):

    def test_stop_is_clean_and_idempotent(self):
        core = coremain.start(control_host="127.0.0.1", control_port=0,
                              web_host="127.0.0.1", web_port=0)
        core.stop()
        core.stop()   # must not raise


if __name__ == "__main__":
    unittest.main()
