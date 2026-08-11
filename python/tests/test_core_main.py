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
        self.assertEqual(msg["mode"], "diner")
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
        w.send(json.dumps({"t": "tare", "bin": 2}))
        self.assertTrue(self.cal_result(w, 2, "tare")["ok"])

        w.send(json.dumps({"t": "calibrate", "bin": 2, "ref_mass_g": float("nan")}))
        # Proof the link survived, rather than waiting out a full DEADLINE
        # for a bin-2 result that a working guard never sends at all: a
        # valid tare on a different bin still completes.
        w.send(json.dumps({"t": "tare", "bin": 4}))
        self.assertTrue(self.cal_result(w, 4, "tare")["ok"])
        self.assertFalse(self.core.cal.bins[2].calibrated)


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
