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
import tempfile
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


def _no_serial_port():
    """`Core`'s `scale_open_port` for every test below.

    SCALE_PORT's default is this dev machine's real COM5, and on this
    machine COM5 is a live XIAO (CLAUDE.md, verified 2026-08-11) —
    leaving this unset would have Core's own reader thread racing real
    hardware counts against whatever a test just fed in through
    scale.feed(). test_scale.py and test_calibrator.py never touch a
    real port either; this is the same discipline at the Core level.
    """
    raise OSError("no serial port in tests")


class CoreCase(unittest.TestCase):
    """A real Core on ephemeral loopback ports, torn down after."""

    def setUp(self):
        hlog.reset()
        self.addCleanup(hlog.reset)
        # cal_path: a throwaway file, never state/loadcell_cal.json. That
        # is the one file doc section 9.6 calls out as able to silently
        # mis-bill, and every Core built here reads it at construction
        # (loadcell_cal.py's docstring: missing is a normal first boot) —
        # a test run must never read, and TestBinsTab below must never
        # write, the real one.
        self._cal_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._cal_dir.cleanup)
        self.core = coremain.start(
            control_host="127.0.0.1", control_port=0,
            web_host="127.0.0.1", web_port=0,
            cal_path=os.path.join(self._cal_dir.name, "loadcell_cal.json"),
            scale_open_port=_no_serial_port)
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

    def of_client(self, timeout=DEADLINE):
        """A wire.Client named `of`, plus the list of `state` messages it
        has received so far and the lock guarding that list. Shared by
        every case that needs to observe a broadcast rather than poke
        Cart/BinMap directly — TestStateBroadcast (build item 3) and
        TestDeveloperPanelMockControls (build item 5) both do.
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

    def test_shape_matches_doc_4_3(self):
        c, msgs, lock = self.of_client()
        self.wait_for_n(msgs, lock, 1)
        with lock:
            msg = msgs[0]
        self.assertEqual(msg["t"], "state")
        self.assertEqual(msg["mode"], "serving")
        self.assertEqual(msg["locale"], "en")
        self.assertEqual(len(msg["bins"]), 8)
        self.assertEqual(msg["widgets"], [])
        self.assertEqual(msg["overlay"], {"kind": "none"})
        self.assertIn("style", msg["fluid"])
        self.assertIn("amount", msg["total"])
        self.assertIn("text", msg["total"])

    def test_bin_labels_never_fall_back_to_the_hidden_id(self):
        """The regression guard for the leak at core/main.py's `label =`.

        It used to read `item.names.get(self.locale, item.id)`, so the
        first locale missing one translation projected the hidden training
        label onto a plate. Switching Core to a locale nothing in the
        catalogue names must degrade to English, never to `soya_chunks`.

        Driven through _bin_msg rather than the wire because the locale is
        fixed to English at construction (build item 4) and the broadcast
        would never carry another one today — the point is that the
        *lookup* is safe when M6 makes the locale switchable.
        """
        self.core.locale = "ja"          # no ja.json, no ja names anywhere
        ids = self.core.catalogue.ids()
        for i in range(8):
            item = self.core.catalogue.item(ids[i])
            label = self.core._bin_msg(i)["label"]
            self.assertEqual(label, item.names["en"])
            self.assertNotEqual(label, item.id)
            self.assertNotEqual(label, item.class_name)

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
        """Pokes Cart directly rather than through the developer panel's
        WebSocket path — TestDeveloperPanelMockControls (build item 5)
        covers that path; this one isolates the broadcaster itself from
        it. Doc section 12.8's cycle includes 45g.
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

    def test_a_sub_deadband_pick_does_not_move_the_wire_at_all(self):
        """I5 on the wire, not just in Cart.

        45g then 6g: the second pick is inside the 10g deadband, so the
        plate must keep saying 45g AND keep saying the price of 45g. The
        check that can fail is the price — reading it off true removed
        grams instead would put 5.51-worth of noodles on a plate labelled
        45g, and would make the running total twitch on load-cell noise at
        M2 while the grams beside it sat still.
        """
        c, msgs, lock = self.of_client()
        self.wait_for_n(msgs, lock, 1)

        self.core.cart.mock_pick(0, 45)
        self.core.cart.mock_pick(0, 6)          # 51g truly gone, 45g shown

        item = self.core.catalogue.item(self.core.catalogue.ids()[0])
        shown_price = round(45 / 100.0 * item.price_per_100g, 2)
        true_price = round(51 / 100.0 * item.price_per_100g, 2)
        self.assertNotEqual(shown_price, true_price,
                            "fixture is useless if both grams price the same")

        def settled():
            with lock:
                return msgs and msgs[-1]["bins"][0]["picked"] == 45
        self.assertTrue(wait_for(settled), "bin 0's pick never appeared")

        with lock:
            last = msgs[-1]
        self.assertEqual(last["bins"][0]["grams"], 449)     # live weight is truth
        self.assertEqual(last["bins"][0]["picked"], 45)     # display is deadbanded
        self.assertEqual(last["bins"][0]["price"], shown_price)
        self.assertEqual(last["total"]["amount"], shown_price)

    def test_every_bin_line_agrees_with_its_own_grams(self):
        """The plate a diner reads must survive them doing the arithmetic
        (doc section 21: "verify by arithmetic, not by watching"). Walks
        the whole 8-bin array so this cannot pass by one bin happening to
        line up.
        """
        c, msgs, lock = self.of_client()
        self.wait_for_n(msgs, lock, 1)

        for i, grams in enumerate((45, 6, 120, 3, 25, 80, 45, 6)):
            self.core.cart.mock_pick(i, grams)

        def any_pick_visible():
            with lock:
                return msgs and msgs[-1]["bins"][2]["picked"] == 120
        self.assertTrue(wait_for(any_pick_visible), "no pick reached the wire")

        with lock:
            last = msgs[-1]
        ids = self.core.catalogue.ids()
        for i, b in enumerate(last["bins"]):
            item = self.core.catalogue.item(ids[i])
            self.assertEqual(
                b["price"], round(b["picked"] / 100.0 * item.price_per_100g, 2),
                f"bin {i}: {b['picked']}g does not price to {b['price']}")
        self.assertAlmostEqual(last["total"]["amount"],
                               sum(b["price"] for b in last["bins"]), places=2)

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


class TestDeveloperPanelMockControls(CoreCase):
    """doc section 21 build item 5: the staff view's mock pick/put-back
    buttons, over the real WebSocket this time — TestStateBroadcast's
    mock-pick test pokes Cart directly and says outright that it bypasses
    this path because it "isn't built yet". It is now.
    """

    def test_mock_pick_over_the_websocket_reaches_cart(self):
        _, of_msgs, of_lock = self.of_client()
        self.wait_for_n(of_msgs, of_lock, 1)

        w = self.ws()
        self.recv_json(w)   # the pips seed
        w.send(json.dumps({"t": "mock_pick", "bin": 3, "grams": 45}))

        def bin3_picked_45():
            with of_lock:
                return any(m["bins"][3]["picked"] == 45 for m in of_msgs)
        self.assertTrue(wait_for(bin3_picked_45),
                         "a WS mock_pick never showed up in a state broadcast")

    def test_mock_putback_over_the_websocket_reaches_cart(self):
        _, of_msgs, of_lock = self.of_client()
        self.wait_for_n(of_msgs, of_lock, 1)
        self.core.cart.mock_pick(5, 80)

        w = self.ws()
        self.recv_json(w)
        w.send(json.dumps({"t": "mock_putback", "bin": 5, "grams": 25}))

        def bin5_picked_55():
            with of_lock:
                return any(m["bins"][5]["picked"] == 55 for m in of_msgs)   # 80 - 25
        self.assertTrue(wait_for(bin5_picked_55),
                         "a WS mock_putback never showed up in a state broadcast")

    def test_the_doc_12_8_cycle_values_all_work_in_sequence(self):
        """{45,6,120,3,25,80} — doc section 12.8's exact cycle, applied to
        one bin in order, same as the M1 acceptance test (doc section 21)
        checks by arithmetic on the table.
        """
        _, of_msgs, of_lock = self.of_client()
        self.wait_for_n(of_msgs, of_lock, 1)
        w = self.ws()
        self.recv_json(w)

        cycle = [45, 6, 120, 3, 25, 80]
        for g in cycle:
            w.send(json.dumps({"t": "mock_pick", "bin": 2, "grams": g}))

        expected_total = sum(cycle)

        def done():
            with of_lock:
                return any(m["bins"][2]["picked"] == expected_total for m in of_msgs)
        self.assertTrue(wait_for(done), "the full cycle never landed on bin 2")

    def test_bad_bin_index_is_ignored_not_a_crash(self):
        _, of_msgs, of_lock = self.of_client()
        self.wait_for_n(of_msgs, of_lock, 1)
        w = self.ws()
        self.recv_json(w)
        w.send(json.dumps({"t": "mock_pick", "bin": 99, "grams": 45}))
        # >= cart.DEFAULT_DEADBAND_G (10g) — otherwise this pick alone
        # would never snap `shown_g`/`picked` at all (doc section 9.2's
        # deadband, I5), and the test would be checking the wrong thing.
        w.send(json.dumps({"t": "mock_pick", "bin": 1, "grams": 45}))

        def bin1_picked_45():
            with of_lock:
                return any(m["bins"][1]["picked"] == 45 for m in of_msgs)
        self.assertTrue(wait_for(bin1_picked_45),
                         "a valid message after a bad one never went through")

    def test_negative_grams_is_ignored_not_a_crash(self):
        _, of_msgs, of_lock = self.of_client()
        self.wait_for_n(of_msgs, of_lock, 1)
        w = self.ws()
        self.recv_json(w)
        w.send(json.dumps({"t": "mock_pick", "bin": 4, "grams": -45}))
        w.send(json.dumps({"t": "mock_pick", "bin": 4, "grams": 45}))

        def bin4_picked_45():
            with of_lock:
                return any(m["bins"][4]["picked"] == 45 for m in of_msgs)
        self.assertTrue(wait_for(bin4_picked_45),
                         "a valid message after a bad one never went through")


class ScaleRig:
    """A calibrated synthetic load cell, shared by the classes below that
    need Cart to be driven by real weights rather than mock picks.

    Calibrates bins by writing straight to core.cal (the same object
    core.scale.cal is — Core.__init__'s own docstring) rather than
    driving the Tare/Calibrate wizard's 2s capture windows: that flow is
    TestBinsTab's job, and going around it keeps these tests about Cart,
    not about calibrator.py.
    """

    CPG = 200.0                # counts per gram, arbitrary but round
    ZERO_COUNTS = -83422.0     # doc section 8.3's own example (TestBinsTab)

    def calibrate_bin(self, i, ref_mass_g):
        self.core.cal.tare(i, self.ZERO_COUNTS)
        self.core.cal.calibrate(i, self.grams_to_counts(ref_mass_g), ref_mass_g)

    def grams_to_counts(self, grams):
        return self.ZERO_COUNTS + self.CPG * grams

    def feed(self, counts):
        """Push `counts` into core.scale in the background. Returns the
        live list (mutate an index to change what the "rig" reads) and
        the stop Event (set it to simulate the XIAO going away mid-test,
        not just at teardown) — same shape as TestBinsTab.feed(), plus
        the early-stop handle this file's staleness tests need.
        """
        counts = list(counts)
        stop = threading.Event()

        def run():
            while not stop.is_set():
                self.core.scale.feed(list(counts))
                time.sleep(0.01)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        self.addCleanup(stop.set)
        return counts, stop


class TestScaleWiredIntoCart(ScaleRig, CoreCase):
    """M2 build item 5 (doc section 21): Cart's live grams come from
    core/scale.py's real reading once a bin has one — main.py's
    _apply_scale_to_cart — replacing reliance on the M1 mock seed as an
    ongoing source of truth. TestDeveloperPanelMockControls above is the
    regression proof that an uncalibrated bin (every bin, in every test
    up there — none of them ever call scale.feed()) is untouched by any
    of this and the mock buttons still work exactly as before.
    """

    def test_first_real_reading_baselines_instead_of_pricing_the_mock_gap(self):
        """A fresh Cart is seeded to MOCK_SEED_GRAMS (500g). Bin 0's real
        weight is calibrated to read 300g — ordinary set_live_grams() on
        top of the mock seed would price this as a 200g phantom pick the
        instant the scale comes online. It must not.
        """
        self.calibrate_bin(0, ref_mass_g=300.0)
        _, of_msgs, of_lock = self.of_client()
        counts = [self.ZERO_COUNTS] * 8
        counts[0] = self.grams_to_counts(300.0)
        self.feed(counts)

        def bin0_grams_300():
            with of_lock:
                return any(m["bins"][0]["grams"] == 300 for m in of_msgs)
        self.assertTrue(wait_for(bin0_grams_300), "bin 0 never showed the real weight")

        with of_lock:
            last = of_msgs[-1]
        self.assertEqual(last["bins"][0]["grams"], 300)
        self.assertEqual(last["bins"][0]["picked"], 0,
                         "the mock-seed-to-real gap was billed as a phantom pick")
        self.assertEqual(last["bins"][0]["price"], 0.0)
        self.assertEqual(last["total"]["amount"], 0.0)

    def test_removing_real_mass_after_baseline_bills_by_arithmetic(self):
        """Doc section 21's M2 acceptance test, at the Cart level: remove
        ~100g from a calibrated bin and the total rises by the correct,
        arithmetically-checkable amount.
        """
        self.calibrate_bin(1, ref_mass_g=400.0)
        _, of_msgs, of_lock = self.of_client()
        counts, _ = self.feed(
            [self.grams_to_counts(400.0) if i == 1 else self.ZERO_COUNTS
             for i in range(8)])

        def baselined():
            with of_lock:
                return any(m["bins"][1]["grams"] == 400 for m in of_msgs)
        self.assertTrue(wait_for(baselined), "bin 1 never baselined to its real weight")

        counts[1] = self.grams_to_counts(300.0)     # 100g removed

        def picked_100():
            with of_lock:
                return any(m["bins"][1]["picked"] == 100 for m in of_msgs)
        self.assertTrue(wait_for(picked_100), "the 100g removal never reached a broadcast")

        with of_lock:
            last = of_msgs[-1]
        item = self.core.catalogue.item(self.core.catalogue.ids()[1])
        expected_price = round(100 / 100.0 * item.price_per_100g, 2)
        self.assertEqual(last["bins"][1]["price"], expected_price)
        self.assertEqual(last["total"]["amount"], expected_price)

    def test_a_dead_link_freezes_the_bin_instead_of_billing_from_it(self):
        """Doc section 9.5 / doc section 21's M2 acceptance: "no billing
        occurs from the frozen reading." Once a baselined bin goes stale,
        the broadcast must keep repeating exactly the last real numbers —
        not drift, not zero, not re-price.
        """
        self.calibrate_bin(2, ref_mass_g=250.0)
        _, of_msgs, of_lock = self.of_client()
        counts, stop = self.feed(
            [self.grams_to_counts(250.0) if i == 2 else self.ZERO_COUNTS
             for i in range(8)])

        def baselined():
            with of_lock:
                return any(m["bins"][2]["grams"] == 250 for m in of_msgs)
        self.assertTrue(wait_for(baselined), "bin 2 never baselined")

        counts[2] = self.grams_to_counts(200.0)      # 50g removed, seen once

        def picked_50():
            with of_lock:
                return any(m["bins"][2]["picked"] == 50 for m in of_msgs)
        self.assertTrue(wait_for(picked_50), "the 50g removal never reached a broadcast")

        stop.set()                                    # the XIAO goes away
        time.sleep(self.core.scale.stale_s + 0.3)      # cross the staleness threshold
        with of_lock:
            of_msgs.clear()
        self.wait_for_n(of_msgs, of_lock, 3)
        with of_lock:
            frozen = [m["bins"][2] for m in of_msgs]
        for b in frozen:
            self.assertEqual(b["grams"], 200)
            self.assertEqual(b["picked"], 50)

    def test_overlay_goes_to_error_only_after_a_baselined_bin_is_lost(self):
        """Before any bin has ever been calibrated the scale is stale from
        boot (no XIAO in tests) — that is the ordinary M1 mock-only demo
        (doc section 12.8) and must not permanently cover the table in a
        fault screen. The overlay must flip to "error" only once a bin
        that was genuinely billing from real weight goes dark.
        """
        _, of_msgs, of_lock = self.of_client()
        self.wait_for_n(of_msgs, of_lock, 3)
        with of_lock:
            self.assertTrue(all(m["overlay"] == {"kind": "none"} for m in of_msgs),
                            "overlay fired before any bin ever had real data")
            of_msgs.clear()

        self.calibrate_bin(6, ref_mass_g=350.0)
        counts, stop = self.feed(
            [self.grams_to_counts(350.0) if i == 6 else self.ZERO_COUNTS
             for i in range(8)])

        def baselined():
            with of_lock:
                return any(m["bins"][6]["grams"] == 350 for m in of_msgs)
        self.assertTrue(wait_for(baselined), "bin 6 never baselined")
        with of_lock:
            self.assertTrue(any(m["overlay"] == {"kind": "none"} for m in of_msgs),
                            "overlay fired while the link was still healthy")
            of_msgs.clear()

        stop.set()
        time.sleep(self.core.scale.stale_s + 0.3)

        def overlay_is_error():
            with of_lock:
                return any(m["overlay"] == {"kind": "error"} for m in of_msgs)
        self.assertTrue(wait_for(overlay_is_error), "overlay never flagged the dead link")


class TestBinsTab(CoreCase):
    """M2 build item 4 (doc section 21): the staff view's Bins tab (doc
    section 12.4) — the periodic `bins` broadcast the 8 cards read, and
    the Tare/Calibrate wizard driving core/calibrator.py over the real
    WebSocket.

    No XIAO and no real state/loadcell_cal.json (CoreCase.setUp points
    every Core at a throwaway cal_path): counts are fed straight into
    core.scale.feed() in the background, the same shape as
    test_calibrator.py's Rig, aimed at the reader Core itself
    constructed rather than a standalone one.
    """

    # Windows' sleep granularity is ~15.6ms (CLAUDE.md, M2.2's own
    # finding) — test_calibrator.py's CAPTURE_S is 0.3s for exactly this
    # reason (~19 samples even at that granularity), and this reuses it
    # rather than picking a shorter window that could starve
    # MIN_CAPTURE_SAMPLES on a busy CI box.
    CAPTURE_S = 0.3
    CPG = 200.0

    # Doc section 8.3's own example zero_counts, negated — and it has to
    # be *some* nonzero value: BinCal()'s first-boot default is
    # zero_counts=0.0, so a tare fed literal zeros would set
    # zero_counts back to 0.0 and be indistinguishable from "never
    # tared" by BinCal.tared's own "!= first-boot default" check. Real
    # cells never actually read exactly 0 empty (CLAUDE.md's per-channel
    # table), so this was never a live-rig concern — only a synthetic
    # all-zero fixture's.
    EMPTY = [-83422] * 8

    def setUp(self):
        super().setUp()
        self.core.calibrator.capture_s = self.CAPTURE_S

    def feed(self, counts):
        """Push `counts` into core.scale in the background until the test
        ends. Returns the list so the test can mutate it in place to
        change what the "rig" reads mid-flow (test_calibrator.py's
        Rig.put()/empty() do the same thing with their own list).
        """
        counts = list(counts)
        stop = threading.Event()

        def run():
            while not stop.is_set():
                self.core.scale.feed(list(counts))
                time.sleep(0.002)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        self.addCleanup(stop.set)
        return counts

    def enter_setting(self, w):
        """M2.6 decision 4: Tare and Calibrate are gated behind setting
        mode, so every flow in this class now opens with the operator
        tapping ENTER SETTING MODE. Doc section 12.4's own steps — empty
        the bin, then place a reference mass in it — are ordinary picks
        in serving mode and would bill; the mode is what makes them safe.

        Sent on the same connection as the tare/calibrate that follows,
        so ordering is guaranteed: web/server.py gives each connection
        one thread and dispatches its frames in sequence.
        """
        w.send(json.dumps({"t": "set_mode", "mode": "setting"}))
        msg = self.recv_until(
            w, lambda m: m.get("t") == "mode" and m.get("mode") == "setting")
        self.assertIsNotNone(msg, "never entered setting mode")
        return msg

    def bins_msg(self, w, pred=lambda m: True, timeout=DEADLINE):
        return self.recv_until(
            w, lambda m: m.get("t") == "bins" and pred(m), timeout)

    def cal_result(self, w, bin_, op, timeout=DEADLINE):
        return self.recv_until(
            w, lambda m: (m.get("t") == "cal_result" and m.get("bin") == bin_
                         and m.get("op") == op),
            timeout)

    def test_bins_message_has_eight_cards_all_uncalibrated_at_boot(self):
        w = self.ws()
        self.recv_json(w)   # the pips seed
        msg = self.bins_msg(w)
        self.assertIsNotNone(msg, "no bins message arrived")
        self.assertEqual(len(msg["bins"]), 8)
        for b in msg["bins"]:
            self.assertIsNone(b["grams"])
            self.assertFalse(b["calibrated"])
            self.assertFalse(b["tared"])
            self.assertIsNone(b["noise_g"])
            self.assertIsNone(b["noise_dots"])
            self.assertFalse(b["noisy"])
            # Seeded 1:1 from the catalogue at conf 1.0 (core/main.py's
            # _seed_binmap) — the Bins tab shows a name even though it
            # cannot show grams yet.
            self.assertTrue(b["label"])
            self.assertTrue(b["sub"])

    def test_tare_over_the_websocket_reaches_the_calibrator(self):
        self.feed(self.EMPTY)
        w = self.ws()
        self.recv_json(w)
        self.enter_setting(w)
        w.send(json.dumps({"t": "tare", "bin": 3}))
        res = self.cal_result(w, 3, "tare")
        self.assertIsNotNone(res, "tare never produced a cal_result")
        self.assertTrue(res["ok"])
        # A first-ever tare has nothing to quote grams from yet
        # (calibrator.py's own docstring) — it sends the operator on to
        # Calibrate instead of printing a 0g it never measured.
        self.assertIsNone(res["grams"])
        self.assertIn("Calibrate", res["message"])
        self.assertTrue(self.core.cal.bins[3].tared)
        self.assertFalse(self.core.cal.bins[3].calibrated)

    def test_calibrate_before_tare_is_refused_with_the_operator_message(self):
        self.feed(self.EMPTY)
        w = self.ws()
        self.recv_json(w)
        self.enter_setting(w)
        w.send(json.dumps({"t": "calibrate", "bin": 1, "ref_mass_g": 500}))
        res = self.cal_result(w, 1, "calibrate")
        self.assertIsNotNone(res)
        self.assertFalse(res["ok"])
        self.assertIn("Tare", res["message"])
        self.assertIn("bin 1", res["message"])
        self.assertFalse(self.core.cal.bins[1].calibrated)

    def test_full_tare_then_calibrate_cycle_reaches_the_next_bins_broadcast(self):
        counts = self.feed(self.EMPTY)
        w = self.ws()
        self.recv_json(w)
        self.enter_setting(w)

        w.send(json.dumps({"t": "tare", "bin": 5}))
        self.assertTrue(self.cal_result(w, 5, "tare")["ok"])

        counts[5] = int(self.CPG * 500.0)   # 500g now sitting in bin 5
        w.send(json.dumps({"t": "calibrate", "bin": 5, "ref_mass_g": 500}))
        done = self.cal_result(w, 5, "calibrate")
        self.assertTrue(done["ok"], done)
        self.assertAlmostEqual(done["grams"], 500.0, delta=5.0)

        msg = self.bins_msg(w, lambda m: m["bins"][5]["calibrated"])
        self.assertIsNotNone(msg, "the calibration never reached a bins broadcast")
        b5 = msg["bins"][5]
        self.assertTrue(b5["tared"])
        self.assertAlmostEqual(b5["grams"], 500, delta=5)

    def test_bad_bin_index_is_ignored_not_a_crash(self):
        self.feed(self.EMPTY)
        w = self.ws()
        self.recv_json(w)
        self.enter_setting(w)
        w.send(json.dumps({"t": "tare", "bin": 99}))
        w.send(json.dumps({"t": "tare", "bin": 2}))
        res = self.cal_result(w, 2, "tare")
        self.assertIsNotNone(res, "a valid tare after a bad one never went through")

    def test_non_finite_ref_mass_is_ignored_not_saved(self):
        """A NaN or Infinity survives `ref_mass_g <= 0` (every comparison
        against NaN is False) and would otherwise reach
        loadcell_cal.calibrate() and get written into the calibration
        file — doc section 9.6's one number that can silently mis-bill.
        """
        self.feed(self.EMPTY)
        w = self.ws()
        self.recv_json(w)
        self.enter_setting(w)
        w.send(json.dumps({"t": "tare", "bin": 2}))
        self.assertTrue(self.cal_result(w, 2, "tare")["ok"])

        w.send(json.dumps({"t": "calibrate", "bin": 2, "ref_mass_g": float("nan")}))
        # Proof the link survived, rather than waiting out a full DEADLINE
        # for a bin-2 result that a working guard never sends at all: a
        # valid tare on a different bin still completes.
        w.send(json.dumps({"t": "tare", "bin": 4}))
        self.assertTrue(self.cal_result(w, 4, "tare")["ok"])
        self.assertFalse(self.core.cal.bins[2].calibrated)

    def test_tare_and_calibrate_are_refused_in_serving_mode(self):
        """M2.6 decision 4. Doc section 12.4's flow needs an empty bin and
        then a reference mass placed in it — both are ordinary picks in
        serving mode, and both would bill. This is the check that replaced
        the per-bin cal_begin/cal_end freeze.

        MUTATION CHECKED: drop the `in_setting` guard from
        `_handle_cal()` and both halves go red.
        """
        self.feed(self.EMPTY)
        w = self.ws()
        self.recv_json(w)
        # No enter_setting() — that is the point.
        w.send(json.dumps({"t": "tare", "bin": 3}))
        res = self.cal_result(w, 3, "tare")
        self.assertIsNotNone(res, "a refused tare sent no cal_result at all")
        self.assertFalse(res["ok"])
        self.assertIn("setting mode", res["message"])
        self.assertFalse(self.core.cal.bins[3].tared,
                          "a serving-mode tare reached the calibration file")

        w.send(json.dumps({"t": "calibrate", "bin": 3, "ref_mass_g": 500}))
        res = self.cal_result(w, 3, "calibrate")
        self.assertFalse(res["ok"])
        self.assertIn("setting mode", res["message"])
        self.assertFalse(self.core.cal.bins[3].calibrated)

    def test_the_whole_calibration_flow_bills_nothing_in_setting_mode(self):
        """What the deleted cal_begin/cal_end freeze used to guarantee per
        bin, now guaranteed mode-wide: an operator empties a bin by hand,
        places a 650 g reference mass, calibrates, and lifts the mass back
        out — and none of it is a pick.

        650 g is chosen to differ from MOCK_SEED_GRAMS' 500 g so a
        coincidental match cannot hide a regression. Exit is what
        re-baselines, and after it the table must read zero.
        """
        counts = self.feed(self.EMPTY)
        _, of_msgs, of_lock = self.of_client()
        w = self.ws()
        self.recv_json(w)
        self.enter_setting(w)

        def clear():
            with of_lock:
                of_msgs.clear()

        def all_bin6_unpicked():
            with of_lock:
                return of_msgs and all(m["bins"][6]["picked"] == 0 for m in of_msgs)

        w.send(json.dumps({"t": "tare", "bin": 6}))
        self.assertTrue(self.cal_result(w, 6, "tare")["ok"])

        counts[6] = 0     # a counts value distinct from the -83422 zero
        w.send(json.dumps({"t": "calibrate", "bin": 6, "ref_mass_g": 650}))
        done = self.cal_result(w, 6, "calibrate")
        self.assertTrue(done["ok"], done)
        self.assertAlmostEqual(done["grams"], 650.0, delta=5.0)

        # The reference mass is sitting in a now-calibrated bin. Nothing
        # must bill: the mode, not a per-bin freeze, is what stops it.
        clear()
        self.wait_for_n(of_msgs, of_lock, 5)
        self.assertTrue(all_bin6_unpicked(),
                        "the reference mass billed while in setting mode")

        # The operator lifts the reference mass back out. This is the
        # exact step that used to bill 650 g as a phantom pick.
        counts[6] = self.EMPTY[6]
        clear()
        self.wait_for_n(of_msgs, of_lock, 5)
        self.assertTrue(all_bin6_unpicked(),
                        "removing the reference mass billed in setting mode")

        # Exit re-baselines all eight bins against what they hold now.
        clear()
        w.send(json.dumps({"t": "set_mode", "mode": "serving"}))

        def rebaselined():
            with of_lock:
                return any(m["bins"][6]["grams"] == 0 for m in of_msgs)
        self.assertTrue(wait_for(rebaselined), "bin 6 never re-baselined on exit")
        with of_lock:
            last = of_msgs[-1]
        self.assertEqual(last["bins"][6]["picked"], 0)
        self.assertEqual(last["total"]["amount"], 0.0)


class TestMode(ScaleRig, CoreCase):
    """M2.6: the SERVING/SETTING mode, end to end over the real
    WebSocket — `set_mode` in, `mode` broadcast out, and the billing gate
    and exit re-baseline those two drive.
    """

    def mode_msg(self, w, pred=lambda m: True, timeout=DEADLINE):
        return self.recv_until(
            w, lambda m: m.get("t") == "mode" and pred(m), timeout)

    def set_mode(self, w, mode):
        w.send(json.dumps({"t": "set_mode", "mode": mode}))

    def state_mode(self, of_msgs, of_lock, want):
        def seen():
            with of_lock:
                return any(m["mode"] == want for m in of_msgs)
        return wait_for(seen)

    # -- the join seed ---------------------------------------------------

    def test_a_joining_tablet_is_told_the_mode_as_well_as_the_pips(self):
        """web/server.py's on_join now takes a list (build item 7). Both
        messages, in order — without the second, a tablet that joins
        mid-run renders the wrong action-bar button until someone
        touches something.
        """
        w = self.ws()
        first = self.recv_json(w)
        second = self.recv_json(w)
        self.assertEqual(first["t"], "pips")
        self.assertEqual(second["t"], "mode")
        self.assertEqual(second["mode"], "serving")
        self.assertFalse(second["cart_active"])
        self.assertIsNone(second["refused"])

    def test_a_tablet_joining_during_setting_mode_is_told_setting(self):
        w = self.ws()
        self.recv_json(w)
        self.recv_json(w)
        self.set_mode(w, "setting")
        self.assertIsNotNone(self.mode_msg(w, lambda m: m["mode"] == "setting"))

        w2 = self.ws()          # a second tablet, opened after the change
        self.recv_json(w2)
        self.assertEqual(self.recv_json(w2)["mode"], "setting")

    # -- the toggle ------------------------------------------------------

    def test_full_toggle_round_trip_over_a_real_websocket(self):
        w = self.ws()
        self.recv_json(w)
        self.recv_json(w)

        self.set_mode(w, "setting")
        msg = self.mode_msg(w, lambda m: m["mode"] == "setting")
        self.assertIsNotNone(msg, "set_mode setting never came back")
        self.assertIsNone(msg["refused"])
        self.assertIs(self.core.fsm.state, coremain.fsm.State.SETTING)

        self.set_mode(w, "serving")
        msg = self.mode_msg(w, lambda m: m["mode"] == "serving")
        self.assertIsNotNone(msg, "set_mode serving never came back")
        self.assertIs(self.core.fsm.state, coremain.fsm.State.IDLE)

    def test_the_mode_reaches_the_state_message_in_both_modes(self):
        """Doc section 4.3's `mode` field, derived from fsm.state at last
        — it was hardcoded "diner" from M1 until this milestone.

        MUTATION CHECKED: hardcode `_state_msg()`'s mode back to
        MODE_SERVING and this goes red on the setting half.
        """
        _, of_msgs, of_lock = self.of_client()
        w = self.ws()
        self.recv_json(w)
        self.recv_json(w)
        self.assertTrue(self.state_mode(of_msgs, of_lock, "serving"))

        self.set_mode(w, "setting")
        self.assertIsNotNone(self.mode_msg(w, lambda m: m["mode"] == "setting"))
        with of_lock:
            of_msgs.clear()
        self.assertTrue(self.state_mode(of_msgs, of_lock, "setting"),
                        "the state message never said setting")

        self.set_mode(w, "serving")
        self.assertIsNotNone(self.mode_msg(w, lambda m: m["mode"] == "serving"))
        with of_lock:
            of_msgs.clear()
        self.assertTrue(self.state_mode(of_msgs, of_lock, "serving"))

    def test_a_bad_mode_value_is_ignored_not_a_crash(self):
        w = self.ws()
        self.recv_json(w)
        self.recv_json(w)
        w.send(json.dumps({"t": "set_mode", "mode": "banana"}))
        self.set_mode(w, "setting")      # proof the link survived
        self.assertIsNotNone(self.mode_msg(w, lambda m: m["mode"] == "setting"))

    # -- the refusal (doc section 9.1) -----------------------------------

    def test_entering_with_an_active_cart_is_refused_and_the_reason_reaches_the_wire(self):
        """Doc section 9.1: "One wrong keypress must not destroy a diner's
        order. The staff view shows *why* it is refused."

        MUTATION CHECKED: make can_enter_setting() always return None and
        this goes red.
        """
        w = self.ws()
        self.recv_json(w)
        self.recv_json(w)
        w.send(json.dumps({"t": "mock_pick", "bin": 2, "grams": 45}))
        self.assertIsNotNone(self.mode_msg(w, lambda m: m["cart_active"]),
                             "cart_active never flipped after a real pick")

        self.set_mode(w, "setting")
        msg = self.mode_msg(w, lambda m: m["refused"] is not None)
        self.assertIsNotNone(msg, "a refused set_mode said nothing at all")
        self.assertEqual(msg["mode"], "serving", "refused and yet the mode changed")
        self.assertTrue(msg["cart_active"])
        self.assertIsInstance(msg["refused"], str)
        self.assertTrue(msg["refused"].strip())
        self.assertIs(self.core.fsm.state, coremain.fsm.State.IDLE)

    def test_cancel_order_then_entering_setting_mode_works(self):
        """Doc section 9.1's pairing: the refusal offers "cancel the order
        first", so that has to actually clear the way. Without core's
        cancel handler this is the loop an operator cannot get out of.
        """
        w = self.ws()
        self.recv_json(w)
        self.recv_json(w)
        w.send(json.dumps({"t": "mock_pick", "bin": 2, "grams": 45}))
        self.assertIsNotNone(self.mode_msg(w, lambda m: m["cart_active"]))

        w.send(json.dumps({"t": "cancel_order"}))
        self.assertIsNotNone(self.mode_msg(w, lambda m: not m["cart_active"]),
                             "cancel_order never cleared the cart")

        self.set_mode(w, "setting")
        msg = self.mode_msg(w, lambda m: m["mode"] == "setting")
        self.assertIsNotNone(msg, "setting mode was still refused after cancel_order")
        self.assertIsNone(msg["refused"])

    def test_cart_active_is_broadcast_without_being_asked(self):
        """The action bar pre-warns off this field, so it has to arrive on
        a change rather than on a round trip — and only on a change, not
        on every one of the 60 state ticks a second.
        """
        w = self.ws()
        self.recv_json(w)
        self.recv_json(w)
        w.send(json.dumps({"t": "mock_pick", "bin": 4, "grams": 120}))
        self.assertIsNotNone(self.mode_msg(w, lambda m: m["cart_active"]))

        w.send(json.dumps({"t": "mock_putback", "bin": 4, "grams": 120}))
        self.assertIsNotNone(self.mode_msg(w, lambda m: not m["cart_active"]))

    def test_an_unchanged_mode_is_not_rebroadcast_every_tick(self):
        """"On change, not on a timer" (build item 6). The state loop calls
        _publish_mode() 60 times a second; if it sent every time, a tablet
        would be taking 60 mode frames a second forever.

        MUTATION CHECKED: drop the `key == self._last_mode_key` early
        return and this goes red.
        """
        w = self.ws()
        self.recv_json(w)
        self.recv_json(w)
        seen = 0
        end = time.time() + 0.5
        while time.time() < end:
            try:
                msg = self.recv_json(w, timeout=max(0.05, end - time.time()))
            except TimeoutError:
                break
            if msg.get("t") == "mode":
                seen += 1
        self.assertEqual(seen, 0,
                          f"{seen} unsolicited mode frames in 0.5s with nothing changing")

    # -- the billing gate ------------------------------------------------

    def test_scale_readings_do_not_reach_cart_in_setting_mode(self):
        """The whole point of the milestone: staff lifting a tray out is
        not a pick.

        MUTATION CHECKED: drop the `State.SETTING` early return from
        _apply_scale_to_cart() and this goes red — the 400 g shows up as
        a live pick while the mode is still on.
        """
        self.calibrate_bin(0, ref_mass_g=500.0)
        _, of_msgs, of_lock = self.of_client()
        counts, _ = self.feed(
            [self.grams_to_counts(500.0) if i == 0 else self.ZERO_COUNTS
             for i in range(8)])

        def baselined():
            with of_lock:
                return any(m["bins"][0]["grams"] == 500 for m in of_msgs)
        self.assertTrue(wait_for(baselined), "bin 0 never baselined")

        w = self.ws()
        self.recv_json(w)
        self.recv_json(w)
        self.set_mode(w, "setting")
        self.assertIsNotNone(self.mode_msg(w, lambda m: m["mode"] == "setting"))

        # The tray comes out. In serving mode this would be a 400 g pick.
        counts[0] = self.grams_to_counts(100.0)
        with of_lock:
            of_msgs.clear()
        self.wait_for_n(of_msgs, of_lock, 10)
        with of_lock:
            frozen = [m["bins"][0] for m in of_msgs]
        for b in frozen:
            self.assertEqual(b["grams"], 500, "a setting-mode weight change reached Cart")
            self.assertEqual(b["picked"], 0)

    def test_exit_rebaselines_against_current_weight_not_stale(self):
        """**THE M2.6 TRAP, driven as the real scenario.**

        Enter setting mode, swap a bin's tray for a much lighter one
        (~400 g of difference), exit. `_apply_scale_to_cart()` was off the
        whole time, so live_g still says 500 g while the bin now holds
        100 g. `reset_session()` does `start_g[i] = live_g[i]`. Without
        the weight refresh first, start_g captures 500, the next tick sets
        live_g to 100, and `removed_g` becomes 400 — **the next diner is
        billed for the tray swap.**

        If only one test survived this milestone, it should be this one.

        MUTATION CHECKED: delete the `self._refresh_weights()` call from
        fsm.exit_setting() and this goes red with picked == 400 and a
        non-zero total. Emptying `_refresh_weights_from_scale()`'s body in
        core/main.py goes red the same way.
        """
        self.calibrate_bin(0, ref_mass_g=500.0)
        _, of_msgs, of_lock = self.of_client()
        counts, _ = self.feed(
            [self.grams_to_counts(500.0) if i == 0 else self.ZERO_COUNTS
             for i in range(8)])

        def baselined():
            with of_lock:
                return any(m["bins"][0]["grams"] == 500 for m in of_msgs)
        self.assertTrue(wait_for(baselined), "bin 0 never baselined to its real weight")

        w = self.ws()
        self.recv_json(w)
        self.recv_json(w)
        self.set_mode(w, "setting")
        self.assertIsNotNone(self.mode_msg(w, lambda m: m["mode"] == "setting"))

        # Staff swap the full tray for a nearly empty one.
        counts[0] = self.grams_to_counts(100.0)
        time.sleep(0.3)            # let the reader's median window catch up

        self.set_mode(w, "serving")
        self.assertIsNotNone(self.mode_msg(w, lambda m: m["mode"] == "serving"))

        # The table must read the new tray's weight and bill nothing.
        with of_lock:
            of_msgs.clear()

        def shows_the_new_tray():
            with of_lock:
                return any(m["bins"][0]["grams"] == 100 for m in of_msgs)
        self.assertTrue(wait_for(shows_the_new_tray),
                        "bin 0 never picked up the swapped-in tray's weight")

        self.wait_for_n(of_msgs, of_lock, 5)
        with of_lock:
            after = list(of_msgs)
        for m in after:
            self.assertEqual(m["bins"][0]["picked"], 0,
                              "the tray swap billed as a pick — the weight refresh is missing")
            self.assertEqual(m["bins"][0]["price"], 0.0)
            self.assertEqual(m["total"]["amount"], 0.0)

    def test_exit_locks_the_bin_map(self):
        """binmap.locked has been persisted and loaded since M1 with no
        writer anywhere. Setting-mode exit is it (doc section 8.2), and
        M7 build item 4 is what will need it.
        """
        w = self.ws()
        self.recv_json(w)
        self.recv_json(w)
        self.assertFalse(self.core.binmap.locked)

        self.set_mode(w, "setting")
        self.assertIsNotNone(self.mode_msg(w, lambda m: m["mode"] == "setting"))
        self.assertFalse(self.core.binmap.locked,
                          "the map locked on entry — doc 8.2 says it unlocks while setting")

        self.set_mode(w, "serving")
        self.assertIsNotNone(self.mode_msg(w, lambda m: m["mode"] == "serving"))
        self.assertTrue(self.core.binmap.locked)


class TestNoiseDots(unittest.TestCase):
    """coremain._noise_dots — doc section 12.4's "●●●●●●○○" bar. A UI
    heuristic (the doc gives a mockup, not a formula), so these pin down
    the contract the constants' own comment claims, not a doc-given
    number.
    """

    def test_unmeasured_is_none_not_zero(self):
        self.assertIsNone(coremain._noise_dots(None, 2.0))

    def test_zero_noise_is_a_full_bar(self):
        self.assertEqual(coremain._noise_dots(0.0, 2.0), coremain.NOISE_DOTS)

    def test_exactly_at_the_settle_tolerance_is_half_full(self):
        self.assertEqual(coremain._noise_dots(2.0, 2.0), coremain.NOISE_DOTS // 2)

    def test_at_or_past_twice_the_tolerance_is_empty(self):
        self.assertEqual(coremain._noise_dots(4.0, 2.0), 0)
        self.assertEqual(coremain._noise_dots(400.0, 2.0), 0)


class TestStateSnapshotIsAtomic(unittest.TestCase):
    """Core.state_lock — a `state` message is one instant, not two.

    Builds a Core and never start()s it: the real 60Hz broadcaster would
    otherwise be racing the very thing being measured, and a test whose
    verdict depends on which thread won is not a test.
    """

    def setUp(self):
        hlog.reset()
        self.addCleanup(hlog.reset)
        cal_dir = tempfile.TemporaryDirectory()   # see CoreCase.setUp
        self.addCleanup(cal_dir.cleanup)
        self.core = coremain.Core(control_host="127.0.0.1", control_port=0,
                                  web_host="127.0.0.1", web_port=0,
                                  cal_path=os.path.join(cal_dir.name, "loadcell_cal.json"),
                                  scale_open_port=_no_serial_port)

    def test_a_pick_cannot_land_between_the_bins_and_the_total(self):
        """The tear this lock exists to stop, forced rather than waited for.

        _state_msg() serialises `bins` and then `total` — dict literals
        evaluate left to right — so there is a real instant where the bins
        array is finished and the total is not. Holding that instant open
        and mutating the cart inside it is the whole test.
        """
        core = self.core
        core.cart.mock_pick(0, 45)            # something already in the cart

        bins_done = threading.Event()
        real_total_msg = core._total_msg

        def slow_total_msg():
            bins_done.set()
            time.sleep(0.2)                   # the window, held open
            return real_total_msg()

        core._total_msg = slow_total_msg

        def picker():
            bins_done.wait(DEADLINE)
            core._handle_mock("mock_pick", {"bin": 7, "grams": 120})

        t = threading.Thread(target=picker)
        t.start()
        msg = core._state_msg()
        t.join(DEADLINE)
        self.assertFalse(t.is_alive(), "the picker thread never finished")

        self.assertAlmostEqual(
            msg["total"]["amount"], sum(b["price"] for b in msg["bins"]),
            places=2,
            msg="the total counted a pick that the bins array had already missed")

    def test_the_pick_is_not_lost_only_deferred(self):
        """The lock must delay a mutation, never drop it — the next message
        has to carry it. A 'fix' that swallowed the write would pass the
        test above and be far worse than the tear it replaced.
        """
        core = self.core
        core._handle_mock("mock_pick", {"bin": 7, "grams": 120})
        msg = core._state_msg()
        self.assertEqual(msg["bins"][7]["picked"], 120)


class TestUnitSuffixIsLocalised(unittest.TestCase):
    """The "/100g" on a bin's price line is a word, not punctuation (I2)."""

    def setUp(self):
        hlog.reset()
        self.addCleanup(hlog.reset)

    def test_the_suffix_comes_from_the_locale_file_not_the_source(self):
        """Loads a locale whose suffix is the Chinese one. If core builds
        the string with "/100g" baked into an f-string, the price line is
        the one part of the plate that stays English after a locale
        switch — which is exactly what this asserts against.
        """
        repo_root = os.path.join(os.path.dirname(__file__), "..", "..")
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(repo_root, "data", "catalogue.json"),
                      encoding="utf-8") as f:
                catalogue = f.read()
            with open(os.path.join(tmp, "catalogue.json"), "w",
                      encoding="utf-8") as f:
                f.write(catalogue)
            os.mkdir(os.path.join(tmp, "locales"))
            with open(os.path.join(tmp, "locales", "en.json"), "w",
                      encoding="utf-8") as f:
                json.dump({"_currency": {"symbol": "¥", "rate": 1.0, "decimals": 2},
                           "total": "总计",
                           "per_100g": "/100克"}, f, ensure_ascii=False)

            core = coremain.Core(control_host="127.0.0.1", control_port=0,
                                 web_host="127.0.0.1", web_port=0,
                                 data_dir=tmp,
                                 cal_path=os.path.join(tmp, "loadcell_cal.json"),
                                 scale_open_port=_no_serial_port)
            msg = core._state_msg()

        self.assertTrue(msg["bins"][0]["resolved"], "fixture bin is not billable")
        self.assertTrue(
            msg["bins"][0]["sub"].endswith("/100克"),
            f"price line kept an English unit: {msg['bins'][0]['sub']!r}")
        self.assertEqual(msg["total"]["label"], "总计")


class TestStop(unittest.TestCase):

    def test_stop_is_clean_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            core = coremain.start(control_host="127.0.0.1", control_port=0,
                                  web_host="127.0.0.1", web_port=0,
                                  cal_path=os.path.join(tmp, "loadcell_cal.json"),
                                  scale_open_port=_no_serial_port)
            core.stop()
            core.stop()   # must not raise


if __name__ == "__main__":
    unittest.main()
