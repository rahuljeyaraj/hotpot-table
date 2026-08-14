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
import math
import os
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from websockets.sync.client import connect  # noqa: E402

from hotpot.common import atomicio  # noqa: E402
from hotpot.common import cursorbus  # noqa: E402
from hotpot.common import geometry  # noqa: E402
from hotpot.common import health  # noqa: E402
from hotpot.common import log as hlog  # noqa: E402
from hotpot.common import wire  # noqa: E402
from hotpot.core import bin_grid  # noqa: E402
from hotpot.core import fsm  # noqa: E402
from hotpot.core import geometry_store  # noqa: E402
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


# A camera->stage homography with real perspective in it, and a grid whose
# 8 derived rects do not overlap. Enough to make both stores' "calibrated"
# predicate true, which is all most of this file needs.
_FIXTURE_H = [[1.1, 0.05, 30.0], [-0.04, 1.2, -20.0], [0.00012, 0.00007, 1.0]]
_FIXTURE_H_LINES = [200.0, 420.0, 600.0, 820.0]
_FIXTURE_V_LINES = [100.0, 400.0, 550.0, 850.0, 1000.0, 1300.0, 1450.0, 1750.0]
# The same 8 rects the grid above implies — [100+(i%4)*450, 200+(i//4)*400,
# 300, 220] — kept as a flat list so call sites that want a rect list
# (rather than a grid to drag) do not have to re-derive it.
_FIXTURE_RECTS = bin_grid.BinGrid(h_lines=_FIXTURE_H_LINES,
                                  v_lines=_FIXTURE_V_LINES).rects()


class CoreCase(unittest.TestCase):
    """A real Core on ephemeral loopback ports, torn down after."""

    # Subclasses set this False to exercise doc section 9.1's first-boot
    # path — a fresh clone with an empty `state/`.
    calibrated_fixture = True
    # Subclasses set this False to exercise `classifier.enabled: false`
    # (2026-08-14) — every other CoreCase test wants the boot/live classify
    # passes `TestClassifyLive` already covers, on by default same as Core's
    # own constructor default.
    classify_enabled = True

    def write_calibration(self):
        atomicio.write_json(self.h_path, {
            "schema": 3, "H_cam_to_stage": _FIXTURE_H, "computed_at": 1.0,
            "n_points": 15, "rms_px": 1.1, "keystone_fingerprint": "fixture",
            "camera_size": [1920, 1080], "stage_size": [1920, 1080]})
        atomicio.write_json(self.g_path, {
            "schema": 1, "written": 1.0,
            "h_lines": _FIXTURE_H_LINES, "v_lines": _FIXTURE_V_LINES})

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
        # homography_path/camera_grid_path are throwaway for exactly the
        # same reason (M4.1): those two files decide where every bin is,
        # and a test that saved one would silently move the rig's trays.
        self.h_path = os.path.join(self._cal_dir.name, "homography.json")
        self.g_path = os.path.join(self._cal_dir.name, "bin_grid_camera.json")
        self.pg_path = os.path.join(self._cal_dir.name, "bin_grid_projector.json")
        self.v_path = os.path.join(self._cal_dir.name, "view_rotation.json")
        # **A CALIBRATED table by default (M4.6).** Doc section 9.1 boots
        # an empty `state/` to UNCALIBRATED, where nothing bills at all —
        # so every test in this file that is about pricing, the mode, or
        # the `state` message would otherwise be testing a table that
        # refuses to serve. That is real behaviour and it has its own case
        # (`TestUncalibratedBoot`); the rest of the file wants a table
        # that has been set up, which is what M1 through M3 implicitly
        # assumed when there was no other possibility.
        if self.calibrated_fixture:
            self.write_calibration()
        self.core = coremain.start(
            control_host="127.0.0.1", control_port=0,
            web_host="127.0.0.1", web_port=0,
            cal_path=os.path.join(self._cal_dir.name, "loadcell_cal.json"),
            homography_path=self.h_path, camera_grid_path=self.g_path,
            projector_grid_path=self.pg_path,
            view_rotation_path=self.v_path,
            classify_enabled=self.classify_enabled,
            # An ephemeral cursor port, never doc section 4.1's real 8771
            # (M5). Same class of reason as `cal_path` and the grid paths:
            # a test that bound the live port would fight a running rig on
            # this machine, and — worse — would quietly receive a real
            # tracker's hands and start hovering bins mid-test.
            cursor_port=0,
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
        # RIG_FEEDBACK_2026-08-12.md items 4-7: Done/Cancel/Language were
        # removed outright 2026-08-13 (placeholders, developer's own call).
        # `widgets_for()` always returns none now.
        self.assertEqual(msg["widgets"], [])
        self.assertEqual(msg["overlay"], {"kind": "none"})
        self.assertIn("style", msg["fluid"])
        self.assertIn("amount", msg["total"])
        self.assertIn("text", msg["total"])

    def test_bin_labels_never_fall_back_to_the_hidden_id(self):
        """The regression guard for the leak at core/main.py's `label =`.

        It used to read `item.names.get(self.locale, item.id)`, so the
        first locale missing one translation projected the hidden training
        label onto a plate. `_bin_msg`'s `label` is now `short_display_name()`
        (VISUAL_LAYER.md's `shortLabel`, not localised at all), so switching
        the locale can no longer be what triggers the leak — the check that
        still matters is that the plate string is never the hidden id or
        class_name, whatever produced it.

        Driven through _bin_msg rather than the wire because the locale is
        fixed to English at construction (build item 4) and the broadcast
        would never carry another one today.
        """
        self.core.locale = "ja"          # no ja.json, no ja names anywhere
        ids = self.core.catalogue.ids()
        for i in range(8):
            item = self.core.catalogue.item(ids[i])
            label = self.core._bin_msg(i)["label"]
            self.assertEqual(label, item.short_label)
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
            self.assertEqual(b["label"], item.short_label)
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
        self.assertEqual(coremain.NOT_IN_SETTING_MSG, res["message"])
        self.assertFalse(self.core.cal.bins[3].tared,
                          "a serving-mode tare reached the calibration file")

        w.send(json.dumps({"t": "calibrate", "bin": 3, "ref_mass_g": 500}))
        res = self.cal_result(w, 3, "calibrate")
        self.assertFalse(res["ok"])
        self.assertEqual(coremain.NOT_IN_SETTING_MSG, res["message"])
        self.assertFalse(self.core.cal.bins[3].calibrated)

    def test_tare_all_zeroes_every_bin_from_one_capture(self):
        """Setting the table means eight empty bins at once, so Tare — the
        one step whose whole content is "the bin is empty" — has a bulk
        version. One capture window, not eight: every bin's zero comes
        from the same instant, and it takes 2s rather than 16s.

        MUTATION CHECKED: make `tare_all()` skip the loop body for any bin
        but 0 and this goes red on bins 1-7.
        """
        self.feed(self.EMPTY)
        w = self.ws()
        self.recv_json(w)
        self.enter_setting(w)

        for b in range(8):
            self.assertFalse(self.core.cal.bins[b].tared)

        w.send(json.dumps({"t": "tare_all"}))
        res = self.recv_until(
            w, lambda m: m.get("t") == "cal_result" and m.get("op") == "tare_all")
        self.assertIsNotNone(res, "tare_all never produced a cal_result")
        self.assertTrue(res["ok"], res)
        self.assertIsNone(res["bin"], "a bulk result named a single bin")
        for b in range(8):
            self.assertTrue(self.core.cal.bins[b].tared, f"bin {b} was not tared")
            self.assertFalse(self.core.cal.bins[b].calibrated,
                              "a tare must never set counts_per_gram")

    def test_tare_all_is_refused_in_serving_mode(self):
        self.feed(self.EMPTY)
        w = self.ws()
        self.recv_json(w)
        w.send(json.dumps({"t": "tare_all"}))
        res = self.recv_until(
            w, lambda m: m.get("t") == "cal_result" and m.get("op") == "tare_all")
        self.assertIsNotNone(res)
        self.assertFalse(res["ok"])
        self.assertEqual(coremain.NOT_IN_SETTING_MSG, res["message"])
        for b in range(8):
            self.assertFalse(self.core.cal.bins[b].tared)

    def test_no_refusal_message_says_billing(self):
        """The system had grown two words for one idea — the table banner
        said NOT BILLING while the mode was named SERVING. One word now,
        and it is the one that is already the mode's name.

        MUTATION CHECKED: put "billing" back in NOT_IN_SETTING_MSG and
        this goes red.
        """
        self.feed(self.EMPTY)
        w = self.ws()
        self.recv_json(w)
        w.send(json.dumps({"t": "tare", "bin": 3}))
        res = self.cal_result(w, 3, "tare")
        self.assertNotIn("billing", res["message"].lower())
        self.assertIn("serving", res["message"].lower())
        self.assertNotIn("billing", coremain.NOT_IN_SETTING_MSG.lower())

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
        """web/server.py's on_join takes a list (M2.6 build item 7). All
        five messages, in order — without the second, a tablet that joins
        mid-run renders the wrong action-bar button until someone touches
        something; without the third (M3 build item 3) the Live tab has no
        `<img>` src until the next full reconnect; without the fourth
        (M4 build item 3) the Setup tab cannot tell a calibrated table
        from an uncalibrated one; without the fifth (M4n) a projector-grid
        editor has nothing to seed its fields from.
        """
        w = self.ws()
        first = self.recv_json(w)
        second = self.recv_json(w)
        third = self.recv_json(w)
        fourth = self.recv_json(w)
        fifth = self.recv_json(w)
        sixth = self.recv_json(w)
        self.assertEqual(first["t"], "pips")
        self.assertEqual(second["t"], "mode")
        self.assertEqual(second["mode"], "serving")
        self.assertFalse(second["cart_active"])
        self.assertIsNone(second["refused"])
        self.assertEqual(third["t"], "camera")
        self.assertEqual(fourth["t"], "geometry")
        self.assertEqual(fifth["t"], "projector_grid")
        # M4 build item 7: without this the Capture tab has no rects to
        # crop previews from and no per-label counts.
        self.assertEqual(sixth["t"], "capture_info")

    def test_a_tablet_joining_during_setting_mode_is_told_setting(self):
        w = self.ws()
        self.recv_json(w)
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


class TestCameraJoinMessage(unittest.TestCase):
    """M3 build item 3: the Live tab's `<img>` src arrives on the join
    seed, built from whatever `camera_host`/`camera_port` Core was given —
    `main()` is the only caller that fills those from config.py; every
    test-built Core takes the constructor's hardcoded defaults instead, the
    same split cal_path/scale_open_port already use to keep a test off
    real files. Never started — this only checks the message _camera_msg()
    builds, no socket needed.
    """

    def setUp(self):
        hlog.reset()
        self.addCleanup(hlog.reset)
        self._cal_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._cal_dir.cleanup)

    def _core(self, **kwargs):
        return coremain.Core(
            control_host="127.0.0.1", control_port=0,
            web_host="127.0.0.1", web_port=0,
            cal_path=os.path.join(self._cal_dir.name, "loadcell_cal.json"),
            scale_open_port=_no_serial_port, **kwargs)

    def test_defaults_match_config_system_default_json(self):
        """MUTATION CHECKED: change CAMERA_PORT and this goes red unless
        config/system.default.json's camera.mjpeg_port is edited to match —
        the two are meant to agree even though nothing enforces it in code.
        """
        core = self._core()
        msg = core._camera_msg()
        self.assertEqual(msg, {"t": "camera", "host": "localhost", "port": 8081})

    def test_a_custom_host_and_port_reach_the_message(self):
        core = self._core(camera_host="odyssey.local", camera_port=9001)
        msg = core._camera_msg()
        self.assertEqual(msg["host"], "odyssey.local")
        self.assertEqual(msg["port"], 9001)


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


class TestManualCalibrationOverTheWire(CoreCase):
    """The only calibration path — automated dot-projection calibration was
    removed (it needed a dark, room-light-free rig this project never
    achieved; see CLAUDE.md's M4h/M4i/M4j). 4 corner points in, over the
    real WebSocket, a homography out — this class checks it holds end to
    end, not just in `test_geometry_store.py`'s `TestManualCorners`.
    """

    # An empty `state/`: this whole class is about producing the geometry a
    # calibrated table already has.
    calibrated_fixture = False

    CAM_TO_STAGE = [[1.1, 0.05, 30.0], [-0.04, 1.2, -20.0],
                    [0.00012, 0.00007, 1.0]]

    def _clicks(self, cam_to_stage=None):
        """The 4 "operator clicks" for a given camera<-stage truth, in the
        fixed front-left/front-right/back-right/back-left order
        `GeometryStore.fit_from_corners` expects.
        """
        # near (front) is the HIGH-y stage edge, far (back) is LOW-y —
        # `GeometryStore._manual_corners_stage`'s own convention, matching
        # `BIN_ORIGINS_MM` (far row y_mm=177, near row y_mm=482).
        from hotpot.common import geometry as geo
        h = cam_to_stage or self.CAM_TO_STAGE
        stage_to_cam = geo.invert(h)
        w, ht = geometry_store.STAGE_SIZE
        corners = [(0.0, float(ht)), (float(w), float(ht)),
                   (float(w), 0.0), (0.0, 0.0)]
        return [list(geo.apply(stage_to_cam, p)) for p in corners]

    def enter_setting(self, ws):
        ws.send(json.dumps({"t": "set_mode", "mode": "setting"}))
        deadline = time.monotonic() + DEADLINE
        while time.monotonic() < deadline:
            msg = self.recv_json(ws)
            if msg.get("t") == "mode" and msg.get("mode") == "setting":
                return
        self.fail("never entered setting mode")

    def collect(self, ws, want, timeout=DEADLINE):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self.recv_json(ws, timeout=deadline - time.monotonic())
            if msg.get("t") == want:
                return msg
        self.fail(f"no {want} message arrived")

    def test_four_clicks_write_a_homography(self):
        ws = self.ws()
        self.enter_setting(ws)
        ws.send(json.dumps({"t": "manual_calibrate", "points": self._clicks()}))
        result = self.collect(ws, "manual_calibrate_result")
        self.assertTrue(result["ok"], result.get("message"))
        self.assertTrue(self.core.geometry.has_homography)

    def test_the_solved_homography_matches_the_clicks_it_was_built_from(self):
        # The reference is the matrix the clicks were generated from, which
        # core never saw — not a reprojection of core's own points (§5.3).
        from hotpot.common import geometry as geo
        ws = self.ws()
        self.enter_setting(ws)
        ws.send(json.dumps({"t": "manual_calibrate", "points": self._clicks()}))
        self.collect(ws, "manual_calibrate_result")
        probe = (700.0, 400.0)
        probe_cam = geo.apply(geo.invert(self.CAM_TO_STAGE), probe)
        got = geo.apply(self.core.geometry.h, probe_cam)
        self.assertAlmostEqual(got[0], probe[0], places=1)
        self.assertAlmostEqual(got[1], probe[1], places=1)

    def test_recovers_the_homography_through_a_180_degree_mount(self):
        # The click-order convention, end to end this time: a screen-
        # position-based ordering would silently invert here and still
        # report zero error. test_geometry_store.py's TestManualCorners
        # proves the same thing against the bare method.
        from hotpot.common import geometry as geo
        flipped = [[-1.0, 0.0, 1920.0], [0.0, -1.0, 1080.0], [0.0, 0.0, 1.0]]
        ws = self.ws()
        self.enter_setting(ws)
        ws.send(json.dumps({"t": "manual_calibrate",
                            "points": self._clicks(flipped)}))
        self.collect(ws, "manual_calibrate_result")
        probe = (700.0, 400.0)
        probe_cam = geo.apply(geo.invert(flipped), probe)
        got = geo.apply(self.core.geometry.h, probe_cam)
        self.assertAlmostEqual(got[0], probe[0], places=1)
        self.assertAlmostEqual(got[1], probe[1], places=1)

    def test_calibration_is_refused_in_serving_mode(self):
        ws = self.ws()
        ws.send(json.dumps({"t": "manual_calibrate", "points": self._clicks()}))
        result = self.collect(ws, "manual_calibrate_result")
        self.assertFalse(result["ok"])
        self.assertEqual(coremain.NOT_IN_SETTING_MSG, result["message"])
        self.assertFalse(self.core.geometry.has_homography)

    def test_wrong_point_count_is_refused(self):
        ws = self.ws()
        self.enter_setting(ws)
        ws.send(json.dumps({"t": "manual_calibrate",
                            "points": self._clicks()[:3]}))
        result = self.collect(ws, "manual_calibrate_result")
        self.assertFalse(result["ok"])
        self.assertFalse(self.core.geometry.has_homography)

    def test_a_nan_coordinate_is_refused(self):
        ws = self.ws()
        self.enter_setting(ws)
        bad = self._clicks()
        bad[0] = [float("nan"), 0.0]
        ws.send(json.dumps({"t": "manual_calibrate", "points": bad}))
        result = self.collect(ws, "manual_calibrate_result")
        self.assertFalse(result["ok"])
        self.assertFalse(self.core.geometry.has_homography)

    def test_collinear_clicks_are_refused(self):
        ws = self.ws()
        self.enter_setting(ws)
        ws.send(json.dumps({"t": "manual_calibrate",
                            "points": [[0.0, 0.0], [100.0, 0.0],
                                       [200.0, 0.0], [300.0, 0.0]]}))
        result = self.collect(ws, "manual_calibrate_result")
        self.assertFalse(result["ok"])
        self.assertFalse(self.core.geometry.has_homography)

    def test_a_first_solve_seeds_the_camera_grid_from_the_measured_layout(self):
        # M4 build item 5's seed — without it the operator opens the grid
        # editor onto an empty canvas with nothing to drag.
        ws = self.ws()
        self.enter_setting(ws)
        self.assertFalse(self.core.camera_grid.has_grid)
        ws.send(json.dumps({"t": "manual_calibrate", "points": self._clicks()}))
        self.collect(ws, "manual_calibrate_result")
        self.assertTrue(self.core.camera_grid.has_grid)

    def test_a_confirmed_solve_persists_the_corner_points(self):
        # Step 3: the 4 clicks that produced the homography ride along
        # with it, so step 4's drag-corner UI can re-seed its handles
        # instead of opening onto a blind default rect.
        ws = self.ws()
        self.enter_setting(ws)
        clicks = self._clicks()
        ws.send(json.dumps({"t": "manual_calibrate", "points": clicks}))
        self.collect(ws, "manual_calibrate_result")
        self.assertEqual(self.core.geometry.corner_points,
                         [tuple(p) for p in clicks])
        again = geometry_store.GeometryStore(
            homography_path=self.core.geometry.homography_path)
        self.assertEqual(again.corner_points, [tuple(p) for p in clicks])
        self.assertEqual(self.core._geometry_msg()["corner_points"], clicks)

    def test_the_seed_is_not_saved_until_the_operator_says_so(self):
        ws = self.ws()
        self.enter_setting(ws)
        ws.send(json.dumps({"t": "manual_calibrate", "points": self._clicks()}))
        self.collect(ws, "manual_calibrate_result")
        self.assertFalse(self.core.camera_grid.path.exists())

    def test_a_re_solve_does_not_throw_away_a_hand_dragged_grid(self):
        # The homography moved by a pixel or two; the trays did not.
        ws = self.ws()
        self.enter_setting(ws)
        ws.send(json.dumps({"t": "manual_calibrate", "points": self._clicks()}))
        self.collect(ws, "manual_calibrate_result")
        mine_h = [150.0, 380.0, 590.0, 810.0]
        mine_v = [90.0, 390.0, 540.0, 840.0, 990.0, 1290.0, 1440.0, 1740.0]
        ws.send(json.dumps({"t": "set_grid", "h_lines": mine_h, "v_lines": mine_v}))
        self.collect(ws, "grid_result")
        ws.send(json.dumps({"t": "manual_calibrate", "points": self._clicks()}))
        self.collect(ws, "manual_calibrate_result")
        self.assertEqual(self.core.camera_grid.grid.h_lines, mine_h)
        self.assertEqual(self.core.camera_grid.grid.v_lines, mine_v)

    def test_the_join_seed_tells_a_tablet_the_geometry(self):
        ws = self.ws()
        seeds = [self.recv_json(ws) for _ in range(6)]
        kinds = {m["t"] for m in seeds}
        self.assertEqual(kinds, {"pips", "mode", "camera", "geometry",
                                 "projector_grid", "capture_info"})
        geo_msg = next(m for m in seeds if m["t"] == "geometry")
        self.assertFalse(geo_msg["calibrated"])
        self.assertIsNone(geo_msg["h_lines"])
        self.assertIsNone(geo_msg["v_lines"])
        # step 3: the corner-points seed and the display rotation, both
        # present on the very first join, before any calibration exists.
        self.assertIsNone(geo_msg["corner_points"])
        self.assertEqual(geo_msg["view_rotation_deg"], 180)

    def test_ofs_keystone_fingerprint_reaches_the_staleness_check(self):
        # Doc §8.5: oF reports its fingerprint in `stat`; a different one
        # after a solve means somebody nudged the keystone.
        of_client = self.wire_client("of")
        self.assertTrue(of_client.wait_connected(DEADLINE))
        of_client.send({"t": "stat", "fps": 60.0,
                        "keystone_fingerprint": "aaaa"})
        deadline = time.monotonic() + DEADLINE
        while (self.core._keystone_fingerprint != "aaaa"
               and time.monotonic() < deadline):
            time.sleep(0.02)
        self.assertEqual(self.core._keystone_fingerprint, "aaaa")

        ws = self.ws()
        self.enter_setting(ws)
        ws.send(json.dumps({"t": "manual_calibrate", "points": self._clicks()}))
        self.collect(ws, "manual_calibrate_result")
        self.assertEqual(self.core.geometry.keystone_fingerprint, "aaaa")
        self.assertFalse(self.core._geometry_msg()["keystone_stale"])

        of_client.send({"t": "stat", "fps": 60.0,
                        "keystone_fingerprint": "bbbb"})
        deadline = time.monotonic() + DEADLINE
        while (self.core._keystone_fingerprint != "bbbb"
               and time.monotonic() < deadline):
            time.sleep(0.02)
        self.assertTrue(self.core._geometry_msg()["keystone_stale"])


class TestSetViewRotationOverTheWire(CoreCase):
    """Step 3's other new wire message: the Setup tab's future Rotate
    control (drag-corner rebuild step 4 — no UI sends this yet). A
    display preference, not calibration data, gated the same way as every
    other Setup-tab action.
    """

    calibrated_fixture = False

    def enter_setting(self, ws):
        ws.send(json.dumps({"t": "set_mode", "mode": "setting"}))
        deadline = time.monotonic() + DEADLINE
        while time.monotonic() < deadline:
            msg = self.recv_json(ws)
            if msg.get("t") == "mode" and msg.get("mode") == "setting":
                return
        self.fail("never entered setting mode")

    def collect(self, ws, want, timeout=DEADLINE):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self.recv_json(ws, timeout=deadline - time.monotonic())
            if msg.get("t") == want:
                return msg
        self.fail(f"no {want} message arrived")

    def test_a_rotation_over_the_socket_is_saved_and_broadcast(self):
        ws = self.ws()
        self.enter_setting(ws)
        ws.send(json.dumps({"t": "set_view_rotation", "deg": 90}))
        result = self.collect(ws, "set_view_rotation_result")
        self.assertTrue(result["ok"], result.get("message"))
        self.assertEqual(self.core.geometry.view_rotation_deg, 90)
        geo_msg = self.collect(ws, "geometry")
        self.assertEqual(geo_msg["view_rotation_deg"], 90)

    def test_it_survives_a_reload(self):
        ws = self.ws()
        self.enter_setting(ws)
        ws.send(json.dumps({"t": "set_view_rotation", "deg": 270}))
        self.collect(ws, "set_view_rotation_result")
        again = geometry_store.GeometryStore(
            homography_path=self.core.geometry.homography_path,
            view_rotation_path=self.core.geometry.view_rotation_path)
        self.assertEqual(again.view_rotation_deg, 270)

    def test_refused_outside_setting_mode(self):
        ws = self.ws()
        ws.send(json.dumps({"t": "set_view_rotation", "deg": 90}))
        result = self.collect(ws, "set_view_rotation_result")
        self.assertFalse(result["ok"])
        self.assertEqual(coremain.NOT_IN_SETTING_MSG, result["message"])
        self.assertEqual(self.core.geometry.view_rotation_deg, 180)


class TestSetupTabGrid(CoreCase):
    """M4 build item 4's server half, reworked around the bin grid
    (`core/bin_grid.py`): grid dragging saved explicitly, and the legacy
    seed. No separate Verify step any more (dropped 2026-08-12, M4n) — the
    operator watches the rectified feed the grid is drawn on live, every
    frame, while dragging; Save is the only confirmation there is anything
    left to record.

    **What is NOT here is the point.** There is no test that reprojects a
    saved grid through `H` and checks anything — the camera grid is not
    derived from the homography at all any more (`bin_grid.py`'s
    docstring), and even where a homography check would have applied, doc
    section 5.3's TRAP says a reprojection check passes by construction on
    a homography pointing the wrong way.
    """

    # Starts with no saved geometry and installs a homography by hand:
    # this class is about the grid, and a fixture that already had one
    # would make every "saves the grid" assertion pass before the code
    # under test ran.
    calibrated_fixture = False

    CAM_TO_STAGE = [[1.1, 0.05, 30.0], [-0.04, 1.2, -20.0],
                    [0.00012, 0.00007, 1.0]]

    def setUp(self):
        super().setUp()
        self.core.geometry.set_homography(self.CAM_TO_STAGE, rms_px=1.1,
                                          n_points=15)
        self.ws_ = self.ws()
        for _ in range(6):
            self.recv_json(self.ws_)
        self.ws_.send(json.dumps({"t": "set_mode", "mode": "setting"}))
        self.drain_until("mode")

    def drain_until(self, want, timeout=DEADLINE):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self.recv_json(self.ws_, timeout=deadline - time.monotonic())
            if msg.get("t") == want:
                return msg
        self.fail(f"no {want} message arrived")

    def grid(self, dx=0.0):
        return {"h_lines": [200.0 + dx, 420.0 + dx, 600.0 + dx, 820.0 + dx],
                "v_lines": [100.0 + dx, 400.0 + dx, 550.0 + dx, 850.0 + dx,
                            1000.0 + dx, 1300.0 + dx, 1450.0 + dx, 1750.0 + dx]}

    def test_saving_a_grid_writes_the_file_and_derives_rects(self):
        self.ws_.send(json.dumps({"t": "set_grid", **self.grid()}))
        reply = self.drain_until("grid_result")
        self.assertTrue(reply["ok"], reply["message"])
        self.assertTrue(self.core.camera_grid.has_grid)
        self.assertTrue(all(r is not None for r in self.core.camera_grid.rects()))

    def test_stage_rect_stays_none_on_the_state_message(self):
        # Doc §5.3: the camera grid feeds the classifier and core's own
        # hit test, never oF — the projector grid (a later, separate step)
        # is what will fill `state.bins[].rect` in. This is the "unchanged
        # behaviour, not a regression" this repo's own comments call for.
        self.ws_.send(json.dumps({"t": "set_grid", **self.grid()}))
        self.drain_until("grid_result")
        msg = self.core._state_msg()
        self.assertIsNone(msg["bins"][0]["rect"])

    def test_saving_is_refused_in_serving_mode(self):
        # Moving the grid changes what the classifier crops (and, once the
        # projector grid exists, the light-pass cutout), so a save while a
        # diner is at the table would change what they are being billed
        # and photographed against mid-order.
        self.ws_.send(json.dumps({"t": "set_mode", "mode": "serving"}))
        self.drain_until("mode")
        self.ws_.send(json.dumps({"t": "set_grid", **self.grid()}))
        reply = self.drain_until("grid_result")
        self.assertFalse(reply["ok"])
        self.assertFalse(self.core.camera_grid.has_grid)

    def test_a_short_line_list_is_refused(self):
        bad = self.grid()
        bad["v_lines"] = bad["v_lines"][:6]
        self.ws_.send(json.dumps({"t": "set_grid", **bad}))
        self.assertFalse(self.drain_until("grid_result")["ok"])

    def test_a_nan_line_is_refused(self):
        bad = self.grid()
        bad["h_lines"][2] = float("nan")
        self.ws_.send(json.dumps({"t": "set_grid", **bad}))
        self.assertFalse(self.drain_until("grid_result")["ok"])
        self.assertFalse(self.core.camera_grid.has_grid)

    def test_a_crossed_line_pair_is_refused(self):
        bad = self.grid()
        bad["v_lines"][0], bad["v_lines"][1] = bad["v_lines"][1], bad["v_lines"][0]
        self.ws_.send(json.dumps({"t": "set_grid", **bad}))
        self.assertFalse(self.drain_until("grid_result")["ok"])

    def test_the_seed_lands_a_grid_without_saving(self):
        # Doc §12.6: "Save is explicit" — which applies to a seed more
        # than to anything, since nobody has looked at it yet.
        self.ws_.send(json.dumps({"t": "seed_grid"}))
        reply = self.drain_until("grid_result")
        self.assertTrue(reply["ok"], reply["message"])
        geo = self.drain_until("geometry")
        self.assertEqual(len(geo["h_lines"]), bin_grid.NUM_H_LINES)
        self.assertEqual(len(geo["v_lines"]), bin_grid.NUM_V_LINES)
        self.assertFalse(self.core.camera_grid.path.exists())

    def test_seeding_needs_no_homography(self):
        # Unlike the old rect version, the grid seed is pure line
        # arithmetic (bin_grid.py's docstring) — it must not refuse for
        # lack of a homography the way seed_cam_rects_from_table used to.
        core = self.core
        core.geometry._h = None
        core.geometry._h_inv = None
        self.ws_.send(json.dumps({"t": "seed_grid"}))
        reply = self.drain_until("grid_result")
        self.assertTrue(reply["ok"], reply["message"])
        self.assertTrue(self.core.camera_grid.has_grid)

    def test_a_saved_grid_survives_a_reload(self):
        self.ws_.send(json.dumps({"t": "set_grid", **self.grid()}))
        self.drain_until("grid_result")
        again = bin_grid.BinGridStore(self.core.camera_grid.path)
        self.assertTrue(again.has_grid)


class TestProjectorGrid(CoreCase):
    """M4n: `bin_grid.py`'s second `BinGridStore`, aimed at
    `self.projector_grid` instead of `self.camera_grid`. The two handlers
    are `_handle_set_grid`/`_handle_seed_grid`'s own template (no verify
    handler on either grid any more — dropped 2026-08-12), so these tests
    are `TestSetupTabGrid`'s own template — same cases, `_projector`
    message names, and one case that class does not have: the projector
    grid, and only the projector grid, is what reaches `state.bins[].rect`.

    **No homography anywhere in this class.** Unlike `TestSetupTabGrid`,
    setUp() does not install one — `bin_grid.py`'s docstring is explicit
    that this grid needs none, and a test that installed one anyway could
    hide a handler that wrongly required it.
    """

    calibrated_fixture = False

    def setUp(self):
        super().setUp()
        self.ws_ = self.ws()
        for _ in range(6):
            self.recv_json(self.ws_)
        self.ws_.send(json.dumps({"t": "set_mode", "mode": "setting"}))
        self.drain_until("mode")

    def drain_until(self, want, timeout=DEADLINE):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self.recv_json(self.ws_, timeout=deadline - time.monotonic())
            if msg.get("t") == want:
                return msg
        self.fail(f"no {want} message arrived")

    def grid(self, dx=0.0):
        return {"h_lines": [200.0 + dx, 420.0 + dx, 600.0 + dx, 820.0 + dx],
                "v_lines": [100.0 + dx, 400.0 + dx, 550.0 + dx, 850.0 + dx,
                            1000.0 + dx, 1300.0 + dx, 1450.0 + dx, 1750.0 + dx]}

    def test_saving_a_grid_writes_the_file_and_derives_rects(self):
        self.ws_.send(json.dumps({"t": "set_grid_projector", **self.grid()}))
        reply = self.drain_until("grid_projector_result")
        self.assertTrue(reply["ok"], reply["message"])
        self.assertTrue(self.core.projector_grid.has_grid)
        self.assertTrue(all(r is not None for r in self.core.projector_grid.rects()))

    def test_saving_needs_no_homography(self):
        self.assertIsNone(self.core.geometry.h)
        self.ws_.send(json.dumps({"t": "set_grid_projector", **self.grid()}))
        reply = self.drain_until("grid_projector_result")
        self.assertTrue(reply["ok"], reply["message"])

    def test_the_saved_grid_reaches_the_state_message_rect(self):
        # This is the whole point of M4n: the camera grid never reaches
        # `state.bins[].rect` (test_stage_rect_stays_none_on_the_state_
        # message above) — the projector grid is what does.
        self.ws_.send(json.dumps({"t": "set_grid_projector", **self.grid()}))
        self.drain_until("grid_projector_result")
        msg = self.core._state_msg()
        rect = msg["bins"][0]["rect"]
        self.assertIsNotNone(rect)
        self.assertEqual(rect, [round(v, 1) for v in
                                self.core.projector_grid.rects()[0]])

    def test_the_camera_grid_alone_does_not_reach_the_rect(self):
        self.ws_.send(json.dumps({"t": "set_grid",
                                  **{"h_lines": [1.0, 2.0, 3.0, 4.0],
                                     "v_lines": [1.0, 2.0, 3.0, 4.0,
                                                 5.0, 6.0, 7.0, 8.0]}}))
        self.drain_until("grid_result")
        msg = self.core._state_msg()
        self.assertIsNone(msg["bins"][0]["rect"])

    def test_saving_is_refused_in_serving_mode(self):
        self.ws_.send(json.dumps({"t": "set_mode", "mode": "serving"}))
        self.drain_until("mode")
        self.ws_.send(json.dumps({"t": "set_grid_projector", **self.grid()}))
        reply = self.drain_until("grid_projector_result")
        self.assertFalse(reply["ok"])
        self.assertFalse(self.core.projector_grid.has_grid)

    def test_a_short_line_list_is_refused(self):
        bad = self.grid()
        bad["v_lines"] = bad["v_lines"][:6]
        self.ws_.send(json.dumps({"t": "set_grid_projector", **bad}))
        self.assertFalse(self.drain_until("grid_projector_result")["ok"])

    def test_a_nan_line_is_refused(self):
        bad = self.grid()
        bad["h_lines"][2] = float("nan")
        self.ws_.send(json.dumps({"t": "set_grid_projector", **bad}))
        self.assertFalse(self.drain_until("grid_projector_result")["ok"])
        self.assertFalse(self.core.projector_grid.has_grid)

    def test_a_crossed_line_pair_is_refused(self):
        bad = self.grid()
        bad["v_lines"][0], bad["v_lines"][1] = bad["v_lines"][1], bad["v_lines"][0]
        self.ws_.send(json.dumps({"t": "set_grid_projector", **bad}))
        self.assertFalse(self.drain_until("grid_projector_result")["ok"])

    def test_the_seed_lands_a_grid_without_saving(self):
        self.ws_.send(json.dumps({"t": "seed_grid_projector"}))
        reply = self.drain_until("grid_projector_result")
        self.assertTrue(reply["ok"], reply["message"])
        pg = self.drain_until("projector_grid")
        self.assertEqual(len(pg["h_lines"]), bin_grid.NUM_H_LINES)
        self.assertEqual(len(pg["v_lines"]), bin_grid.NUM_V_LINES)
        self.assertFalse(self.core.projector_grid.path.exists())

    def test_a_seeded_but_unsaved_grid_still_reaches_the_state_rect(self):
        # `_bin_msg` reads whatever is in memory, saved or not — the same
        # rule the camera grid's rects() already followed before this
        # class existed. A seed is the starting position an operator nudges
        # from while watching the table move; it must be visible there
        # immediately, not only after Save.
        self.ws_.send(json.dumps({"t": "seed_grid_projector"}))
        self.drain_until("grid_projector_result")
        msg = self.core._state_msg()
        self.assertIsNotNone(msg["bins"][0]["rect"])

    def test_a_saved_grid_survives_a_reload(self):
        self.ws_.send(json.dumps({"t": "set_grid_projector", **self.grid()}))
        self.drain_until("grid_projector_result")
        again = bin_grid.BinGridStore(self.core.projector_grid.path)
        self.assertTrue(again.has_grid)

    def test_join_sends_a_projector_grid_message(self):
        ws = self.ws()
        msgs = [self.recv_json(ws) for _ in range(6)]
        types = [m.get("t") for m in msgs]
        self.assertIn("projector_grid", types)


class TestCaptureTab(CoreCase):
    """M4 build item 7's server half, doc section 12.7.

    **The tests that matter here are the refusals**, because doc section
    21's acceptance list turns the lighting requirement into a rule about
    design: "Every capture is taken with the bin patches lit exactly as
    serving mode lights them. If the Capture tab has its own lighting
    path, that is a bug to fix before collecting a single image, not
    after." There is no lighting code in core or in the tab, so the only
    way that rule can break is a capture overlapping the one state where
    the field is NOT what serving mode shows — dot calibration's black
    field. That is the refusal below.
    """

    def setUp(self):
        super().setUp()
        self.ws_ = self.ws()
        for _ in range(6):
            self.recv_json(self.ws_)
        self.sent_cmds = []

        def on_message(msg):
            if msg.get("t") == "cmd" and msg.get("op") == "capture":
                self.sent_cmds.append(msg)
                # A real burst sends `capture_progress` asides before its
                # final `captured` reply (classifier/main.py's `_capture`)
                # — mimicked here, single-file, for a real burst only.
                burst = msg.get("burst", 1)
                if burst > 1:
                    client.send({"t": "capture_progress", "id": msg.get("id"),
                                 "shot": 1, "burst": burst,
                                 "interval": msg.get("interval", 2)})
                client.send({"t": "captured", "id": msg.get("id"),
                             "files": ["a.jpg"] * len(msg.get("rects") or []),
                             "cancelled": False})
        client = self.wire_client("classifier", on_message=on_message)
        self.assertTrue(client.wait_connected(DEADLINE))
        self.enter_setting()

    def enter_setting(self):
        self.ws_.send(json.dumps({"t": "set_mode", "mode": "setting"}))
        self.drain_until("mode")

    def drain_until(self, want, timeout=DEADLINE):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self.recv_json(self.ws_, timeout=deadline - time.monotonic())
            if msg.get("t") == want:
                return msg
        self.fail(f"no {want} message arrived")

    LABELS = ["mushroom", "tofu", "egg", "baby_corn",
              "soya_chunks", "dried_prawns", "curly_noodle", "long_noodle"]

    def test_a_capture_reaches_the_classifier_with_cores_own_grid(self):
        # Doc §4.7: "`rects` are camera space — the classifier never sees
        # stage space." And they are CORE's rects (derived from its own
        # camera grid), not the tablet's, so an unsaved drag can never
        # reach the dataset. The homography and stage size ride along too
        # — core never touches a frame, so the classifier is the one that
        # warps before it crops (classifier/main.py's docstring).
        self.ws_.send(json.dumps({"t": "capture", "labels": self.LABELS,
                                  "burst": 1}))
        reply = self.drain_until("capture_result")
        self.assertTrue(reply["ok"], reply["message"])
        self.assertEqual(len(self.sent_cmds), 1)
        cmd = self.sent_cmds[0]
        self.assertEqual(len(cmd["rects"]), 8)
        self.assertEqual(cmd["labels"], self.LABELS)
        own_rects = self.core.camera_grid.rects()
        for i, r in enumerate(cmd["rects"]):
            self.assertEqual(len(r), 5)
            self.assertEqual(r[4], i)   # doc §12.7's `_bin<i>` in the name
            self.assertEqual(r[:4], list(own_rects[i]))
        self.assertEqual(cmd["h"], self.core.geometry.h)
        self.assertEqual(cmd["stage_size"], list(self.core.geometry.stage_size))

    def test_a_capture_is_refused_in_serving_mode(self):
        # Not for the lighting — §14.5 makes setting mode's field
        # identical to serving mode's. Because the operator is reaching
        # over trays, which in serving mode is a pick and would bill.
        self.ws_.send(json.dumps({"t": "set_mode", "mode": "serving"}))
        self.drain_until("mode")
        self.ws_.send(json.dumps({"t": "capture", "labels": self.LABELS}))
        reply = self.drain_until("capture_result")
        self.assertFalse(reply["ok"])
        self.assertEqual(self.sent_cmds, [])

    def test_a_capture_is_refused_with_no_bin_grid(self):
        self.core.camera_grid.grid = None
        self.ws_.send(json.dumps({"t": "capture", "labels": self.LABELS}))
        reply = self.drain_until("capture_result")
        self.assertFalse(reply["ok"])
        self.assertIn("grid", reply["message"])
        self.assertEqual(self.sent_cmds, [])

    def test_a_capture_is_refused_with_no_homography(self):
        self.core.geometry._h = None
        self.core.geometry._h_inv = None
        self.ws_.send(json.dumps({"t": "capture", "labels": self.LABELS}))
        reply = self.drain_until("capture_result")
        self.assertFalse(reply["ok"])
        self.assertIn("calibrated", reply["message"])
        self.assertEqual(self.sent_cmds, [])

    def test_a_missing_label_is_refused(self):
        # An unlabelled crop is training data nobody can use; a
        # mislabelled one is worse than none.
        bad = list(self.LABELS)
        bad[5] = "   "
        self.ws_.send(json.dumps({"t": "capture", "labels": bad}))
        self.assertFalse(self.drain_until("capture_result")["ok"])
        self.assertEqual(self.sent_cmds, [])

    def test_a_short_label_list_is_refused(self):
        self.ws_.send(json.dumps({"t": "capture", "labels": self.LABELS[:5]}))
        self.assertFalse(self.drain_until("capture_result")["ok"])

    def test_a_burst_is_passed_through(self):
        # `interval` is seconds BETWEEN shots, not a total period — core
        # does no maths on it, just relays it to the classifier verbatim.
        self.ws_.send(json.dumps({"t": "capture", "labels": self.LABELS,
                                  "burst": 10, "interval": 2}))
        self.drain_until("capture_result")
        self.assertEqual(self.sent_cmds[0]["burst"], 10)
        self.assertEqual(self.sent_cmds[0]["interval"], 2)

    def test_capture_progress_is_relayed_live_not_treated_as_the_reply(self):
        # Doc §12.7's counter-and-countdown. This has to be a broadcast
        # that arrives WHILE the burst is still running and does NOT
        # resolve `_send_classifier_cmd`'s waiter — if it did,
        # `_handle_capture` would treat the shot-1 aside as the final
        # answer and never see the real `captured` reply that follows.
        self.ws_.send(json.dumps({"t": "capture", "labels": self.LABELS,
                                  "burst": 10, "interval": 2}))
        progress = self.drain_until("capture_progress")
        self.assertEqual(progress["shot"], 1)
        self.assertEqual(progress["burst"], 10)
        self.assertEqual(progress["interval"], 2)
        reply = self.drain_until("capture_result")
        self.assertTrue(reply["ok"], reply["message"])

    def test_a_missing_classifier_is_a_sentence(self):
        for c in self._wire_clients:
            c.stop()
        self._wire_clients = []
        self.ws_.send(json.dumps({"t": "capture", "labels": self.LABELS}))
        reply = self.drain_until("capture_result", timeout=15.0)
        self.assertFalse(reply["ok"])

    def test_the_join_seed_carries_the_capture_tabs_defaults(self):
        # Doc §12.7: "Each crop has a label selector defaulting to the
        # current bin-map item."
        ws = self.ws()
        seeds = [self.recv_json(ws) for _ in range(6)]
        info = next(m for m in seeds if m["t"] == "capture_info")
        self.assertEqual(len(info["rects"]), 8)
        self.assertEqual(len(info["labels"]), 8)
        self.assertIn("empty_tray", info["choices"])
        self.assertIn("no_tray", info["choices"])
        self.assertIn("counts", info)

    def test_the_label_default_is_the_class_name_not_the_display_name(self):
        # Doc §8.1's hidden-label rule runs the OTHER way here than it
        # does on the table: `names` is what a diner reads, `class_name`
        # is what the model emits, and a training folder called "Fish
        # Ball" is a folder the model can never produce a label for.
        info = self.core._capture_msg()
        for i, label in enumerate(info["labels"]):
            item = self.core.catalogue.item(self.core.binmap.bins[i].item_id)
            if item is None:
                continue
            self.assertEqual(label, item.class_name)
            self.assertNotEqual(label, item.display_name("en"))


class TestClassifyLive(CoreCase):
    """Doc section 19's M7 build items 2-3: `_classify_pass`/
    `_classify_loop`, and §9.3's unresolved-bin exit gate.

    A real classify pass runs unconditionally right after `core.start()`
    (build item 2's startup scan) — every `CoreCase` already has a
    calibrated fixture, so that pass is in flight from `setUp()` onward in
    every test in this class, same as every other CoreCase test in the
    file. It races nothing here: no fake classifier is connected until a
    test wires one up, so early attempts just see "not connected" and
    return, same tolerance `_apply_scale_to_cart` has for an unplugged
    scale.
    """

    def fake_classifier(self, answer_for):
        """`answer_for(rects) -> bins list` builds the `result` reply's
        `bins` field for whatever rects this pass sent — a callable, not a
        fixed list, so a test can make the answer depend on which bin
        indices were actually asked about.
        """
        self.classify_cmds = []

        def on_message(msg):
            if msg.get("t") == "cmd" and msg.get("op") == "classify":
                self.classify_cmds.append(msg)
                client.send({"t": "result", "id": msg.get("id"),
                            "bins": answer_for(msg.get("rects") or []),
                            "ms": 5})
        client = self.wire_client("classifier", on_message=on_message)
        self.assertTrue(client.wait_connected(DEADLINE))
        return client

    def test_a_pass_writes_the_reply_into_the_bin_map(self):
        self.fake_classifier(lambda rects: [
            {"i": r[4], "label": "soya_chunks", "conf": 0.88} for r in rects])
        self.core._classify_pass()
        b = self.core.binmap.bins[0]
        self.assertEqual(b.item_id, "soya_chunks")
        self.assertEqual(b.conf, 0.88)
        self.assertEqual(b.source, "classifier")

    def test_a_pass_sends_cores_own_grid_same_as_capture(self):
        # Doc §4.7: rects are camera space, from core's own camera grid —
        # never a tablet's, and h/stage_size ride along because core never
        # touches a frame (classifier/main.py's `_classify` docstring).
        self.fake_classifier(lambda rects: [])
        self.core._classify_pass()
        self.assertEqual(len(self.classify_cmds), 1)
        cmd = self.classify_cmds[0]
        self.assertEqual(cmd["mode"], "once")
        own_rects = self.core.camera_grid.rects()
        for i, r in enumerate(cmd["rects"]):
            self.assertEqual(r[4], i)
            self.assertEqual(r[:4], list(own_rects[i]))
        self.assertEqual(cmd["h"], self.core.geometry.h)
        self.assertEqual(cmd["stage_size"], list(self.core.geometry.stage_size))

    def test_a_low_confidence_answer_leaves_the_bin_unresolved(self):
        # binmap.resolved()'s own rule (doc §9.3) — nothing classify-
        # specific needs to enforce this separately, and this test is what
        # checks that claim rather than assuming it.
        self.fake_classifier(lambda rects: [
            {"i": r[4], "label": "soya_chunks", "conf": 0.10} for r in rects])
        self.core._classify_pass()
        self.assertFalse(self.core.binmap.resolved(0))

    def test_no_classifier_connected_leaves_the_mock_seed_untouched(self):
        # `_seed_binmap` starts every bin resolved at conf 1.0 (source
        # "mock"). A classifier that never answers must not downgrade a
        # table that was billable a moment ago to unresolved for no
        # reason — same tolerance _apply_scale_to_cart has for a bin the
        # scale cannot currently weigh.
        before = self.core.binmap.bins[0]
        self.core._classify_pass()
        after = self.core.binmap.bins[0]
        self.assertEqual((before.item_id, before.conf, before.source),
                         (after.item_id, after.conf, after.source))

    def test_a_classifier_error_reply_is_tolerated(self):
        def on_message(msg):
            if msg.get("t") == "cmd" and msg.get("op") == "classify":
                client.send({"t": "result", "id": msg.get("id"),
                            "ok": False, "error": "no camera frames yet"})
        client = self.wire_client("classifier", on_message=on_message)
        self.assertTrue(client.wait_connected(DEADLINE))
        before = self.core.binmap.bins[0].item_id
        self.core._classify_pass()   # must not raise
        self.assertEqual(self.core.binmap.bins[0].item_id, before)

    def test_the_live_loop_only_runs_while_setting(self):
        # White-box: replaces `_classify_pass` with a counter and shrinks
        # the loop's own interval, rather than waiting out real 0.5s ticks
        # — deterministic and fast either way, and it is `_classify_loop`'s
        # own gating being tested, not the real network path (the other
        # tests in this class already cover that).
        calls = []
        self.core._classify_pass = lambda: calls.append(
            self.core.fsm.state)
        self.core._classify_hz = 200.0   # 5ms ticks
        t = threading.Thread(target=self.core._classify_loop, daemon=True)
        t.start()
        self.addCleanup(self.core._classify_stop.set)
        try:
            time.sleep(0.05)   # several ticks while still SERVING
            with_serving = len(calls)
            self.assertGreaterEqual(with_serving, 1)   # the boot scan

            self.ws_ = self.ws()
            for _ in range(6):
                self.recv_json(self.ws_)
            self.ws_.send(json.dumps({"t": "set_mode", "mode": "setting"}))
            self.assertTrue(wait_for(
                lambda: self.core.fsm.state is fsm.State.SETTING))
            time.sleep(0.05)
            self.assertGreater(len(calls), with_serving)
            self.assertTrue(all(s is fsm.State.SETTING
                                for s in calls[with_serving:]))
        finally:
            self.core._classify_stop.set()
            t.join(2.0)

    def test_a_pass_broadcasts_the_raw_result_to_every_tablet(self):
        # The Developer tab's Classifier card (added after the developer's
        # own report: "I switched between two items and don't see any
        # change") reads this directly — `_bin_msg`/`_bins_tab_msg` only
        # ever show a label once it clears doc §9.3's 65% confidence
        # floor, so a low-confidence guess (the ordinary case with this
        # thin dataset) never reached ANY tablet before this broadcast
        # existed, indistinguishable from the classifier not running at
        # all. This checks the raw number reaches the wire ungated.
        self.fake_classifier(lambda rects: [
            {"i": r[4], "label": "soya_chunks", "conf": 0.10} for r in rects])
        self.ws_ = self.ws()
        for _ in range(6):
            self.recv_json(self.ws_)
        self.core._classify_pass()
        msg = self.drain_until("classify")
        self.assertEqual(msg["ms"], 5)
        by_i = {b["i"]: b for b in msg["bins"]}
        self.assertEqual(by_i[0]["label"], "soya_chunks")
        self.assertEqual(by_i[0]["conf"], 0.10)
        # binmap.resolved() would say False at this confidence — confirms
        # the broadcast is genuinely ungated, not just a low number that
        # happens to still clear the floor.
        self.assertFalse(self.core.binmap.resolved(0))

    def test_no_classifier_connected_sends_no_classify_broadcast(self):
        # A skipped pass (doc's own "nothing to show for it, not a fault
        # to raise") must not send an empty/misleading `classify` message
        # either — silence, same as the bin map being left untouched.
        self.ws_ = self.ws()
        for _ in range(6):
            self.recv_json(self.ws_)
        self.core._classify_pass()
        seen_types = []
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            try:
                seen_types.append(self.recv_json(
                    self.ws_, timeout=deadline - time.monotonic()).get("t"))
            except TimeoutError:
                break
        self.assertNotIn("classify", seen_types)

    def test_exit_is_blocked_while_a_bin_is_unresolved(self):
        self.core.binmap.set_bin(0, item_id=None, conf=0.0, source="mock")
        self.ws_ = self.ws()
        for _ in range(6):
            self.recv_json(self.ws_)
        self.ws_.send(json.dumps({"t": "set_mode", "mode": "setting"}))
        self.drain_until("mode")

        self.ws_.send(json.dumps({"t": "set_mode", "mode": "serving"}))
        reply = self.drain_until("mode")
        self.assertEqual(reply["mode"], "setting")   # exit did NOT happen
        self.assertIsNotNone(reply["refused"])
        self.assertIn("unresolved", reply["refused"])
        self.assertIs(self.core.fsm.state, fsm.State.SETTING)

    def test_confirm_pushes_the_exit_through_anyway(self):
        self.core.binmap.set_bin(0, item_id=None, conf=0.0, source="mock")
        self.ws_ = self.ws()
        for _ in range(6):
            self.recv_json(self.ws_)
        self.ws_.send(json.dumps({"t": "set_mode", "mode": "setting"}))
        self.drain_until("mode")

        self.ws_.send(json.dumps({"t": "set_mode", "mode": "serving",
                                  "confirm": True}))
        reply = self.drain_until("mode")
        self.assertEqual(reply["mode"], "serving")
        self.assertIsNone(reply["refused"])
        self.assertIs(self.core.fsm.state, fsm.State.IDLE)

    def test_exit_is_not_blocked_when_every_bin_is_resolved(self):
        # The mock seed already resolves every bin at boot — the ordinary
        # case, and the one every other CoreCase test in this file already
        # relies on implicitly. Named explicitly here as the control for
        # the two tests above.
        self.ws_ = self.ws()
        for _ in range(6):
            self.recv_json(self.ws_)
        self.ws_.send(json.dumps({"t": "set_mode", "mode": "setting"}))
        self.drain_until("mode")
        self.ws_.send(json.dumps({"t": "set_mode", "mode": "serving"}))
        reply = self.drain_until("mode")
        self.assertEqual(reply["mode"], "serving")
        self.assertIsNone(reply["refused"])

    def drain_until(self, want, timeout=DEADLINE):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self.recv_json(self.ws_, timeout=deadline - time.monotonic())
            if msg.get("t") == want:
                return msg
        self.fail(f"no {want} message arrived")


class TestClassifierDisabledByConfig(CoreCase):
    """`classifier.enabled: false` (2026-08-14, doc §8.6) — the model is
    not properly tuned yet, so `_classify_loop` must call `_classify_pass`
    NEVER, not even the doc §19 boot scan, while this is set.
    """

    classify_enabled = False

    def test_the_boot_scan_never_runs(self):
        calls = []
        self.core._classify_pass = lambda: calls.append(True)
        # The real boot scan already ran once inside `setUp()`'s
        # `coremain.start()`, before this test could patch it out — this
        # re-runs the loop's own boot branch directly, same white-box
        # approach `test_the_live_loop_only_runs_while_setting` uses for
        # the periodic branch below.
        t = threading.Thread(target=self.core._classify_loop, daemon=True)
        self.core._classify_stop.set()   # exits the periodic wait immediately
        t.start()
        t.join(2.0)
        self.assertEqual(calls, [])

    def test_the_periodic_pass_never_runs_even_in_setting(self):
        calls = []
        self.core._classify_pass = lambda: calls.append(True)
        self.core._classify_hz = 200.0   # 5ms ticks
        t = threading.Thread(target=self.core._classify_loop, daemon=True)
        t.start()
        self.addCleanup(self.core._classify_stop.set)
        try:
            self.ws_ = self.ws()
            for _ in range(6):
                self.recv_json(self.ws_)
            self.ws_.send(json.dumps({"t": "set_mode", "mode": "setting"}))
            self.assertTrue(wait_for(
                lambda: self.core.fsm.state is fsm.State.SETTING))
            time.sleep(0.05)
            self.assertEqual(calls, [])
        finally:
            self.core._classify_stop.set()
            t.join(2.0)


class TestUncalibratedBoot(CoreCase):
    """M4 build item 6, doc section 9.1: "BOOT always goes to UNCALIBRATED
    if `homography.json` or `bin_rects.json` is missing… This is the
    first-boot path and it must work on a fresh clone with an empty
    `state/`."

    Every Core here starts against an empty throwaway directory, which is
    exactly that fresh clone.
    """

    calibrated_fixture = False

    def test_an_empty_state_dir_boots_uncalibrated(self):
        self.assertIs(self.core.fsm.state, coremain.fsm.State.UNCALIBRATED)
        self.assertFalse(self.core.fsm.serving)

    def test_the_table_is_told_so(self):
        self.assertEqual(self.core._state_msg()["overlay"],
                         {"kind": "uncalibrated"})

    def test_the_tablet_is_told_so_on_join(self):
        # Doc §9.1: "the staff view opens on the calibration wizard."
        ws = self.ws()
        seeds = [self.recv_json(ws) for _ in range(6)]
        mode = next(m for m in seeds if m["t"] == "mode")
        self.assertTrue(mode["uncalibrated"])

    def test_nothing_bills_while_uncalibrated(self):
        # THE point of the state. A table that does not know which tray is
        # which must not weigh food out of one and charge for it.
        self.core.scale.cal.bins[0].zero_counts = 0.0
        self.core.scale.cal.bins[0].counts_per_gram = 1.0
        self.core.scale.feed([500] + [0] * 7)
        before = self.core._state_msg()["bins"][0]["grams"]
        self.core.scale.feed([300] + [0] * 7)
        after = self.core._state_msg()["bins"][0]["grams"]
        self.assertEqual(before, after)
        self.assertEqual(self.core._state_msg()["total"]["amount"], 0.0)

    def test_a_hand_cannot_start_a_session(self):
        # Doc §9.1: "serving mode is unreachable." A diner can wave at an
        # uncalibrated table all day and nothing starts.
        with self.core.state_lock:
            self.assertFalse(self.core.fsm.hand_present())
            self.assertFalse(self.core.fsm.staff_start())
        self.assertIs(self.core.fsm.state, coremain.fsm.State.UNCALIBRATED)

    def test_setting_mode_is_still_reachable_because_that_is_the_fix(self):
        # Calibration is a setting-mode activity. If entering setting mode
        # were blocked here, the state would be unescapable.
        with self.core.state_lock:
            self.assertIsNone(self.core.fsm.can_enter_setting())
            self.assertTrue(self.core.fsm.enter_setting())

    def test_exiting_setting_mode_without_geometry_returns_to_uncalibrated(self):
        # The trap this state has of its own: doc §9.1's diagram writes
        # setting-exit as SETTING -> IDLE, which on a first boot would
        # open a table that has no idea which tray is which.
        with self.core.state_lock:
            self.core.fsm.enter_setting()
            self.core.fsm.exit_setting()
        self.assertIs(self.core.fsm.state, coremain.fsm.State.UNCALIBRATED)

    def test_saving_the_geometry_completes_calibration_and_opens_the_table(self):
        self.core.geometry.set_homography(_FIXTURE_H, rms_px=1.0, n_points=15)
        ws = self.ws()
        for _ in range(6):
            self.recv_json(ws)
        ws.send(json.dumps({"t": "set_mode", "mode": "setting"}))
        deadline = time.monotonic() + DEADLINE
        while time.monotonic() < deadline:
            if self.recv_json(ws).get("t") == "mode":
                break
        ws.send(json.dumps({"t": "set_grid", "h_lines": _FIXTURE_H_LINES,
                            "v_lines": _FIXTURE_V_LINES}))
        deadline = time.monotonic() + DEADLINE
        while time.monotonic() < deadline:
            if self.recv_json(ws).get("t") == "grid_result":
                break
        with self.core.state_lock:
            self.core.fsm.exit_setting()
        self.assertIs(self.core.fsm.state, coremain.fsm.State.IDLE)
        self.assertTrue(self.core.fsm.serving)
        self.assertEqual(self.core._state_msg()["overlay"], {"kind": "none"})

    def test_the_tablet_is_told_when_the_table_stops_being_uncalibrated(self):
        """MUTATION-DRIVEN: dropping `uncalibrated` from `_publish_mode`'s
        on-change key was checked and **no test went red**, so this is
        that test.

        The `mode` message is broadcast on change, not on a timer (M2.6),
        and "change" is a comparison against a key. Leave `uncalibrated`
        out of that key and a table that becomes calibrated without its
        mode or cart also changing never tells the tablet — which sits
        there showing "this table has not been set up yet" over a table
        that has been, with no way to clear it but a reload.

        Today the ordinary path also flips `mode`, so the bug is latent.
        It stops being latent the moment anything else completes
        calibration — restoring a backup, or M7 writing a bin map.
        """
        ws = self.ws()
        for _ in range(6):
            self.recv_json(ws)
        self.core.geometry.set_homography(_FIXTURE_H, rms_px=1.0, n_points=15)
        self.core.camera_grid.set_grid(_FIXTURE_H_LINES, _FIXTURE_V_LINES)
        self.core._check_calibration_complete()
        deadline = time.monotonic() + DEADLINE
        while time.monotonic() < deadline:
            msg = self.recv_json(ws, timeout=deadline - time.monotonic())
            if msg.get("t") == "mode":
                self.assertFalse(msg["uncalibrated"])
                return
        self.fail("the tablet was never told the table is calibrated now")

    def test_a_half_saved_geometry_does_not_open_the_table(self):
        # A homography and no rects is not a calibrated table: the eighth
        # bin would render from a fallback nobody chose.
        self.core.geometry.set_homography(_FIXTURE_H, rms_px=1.0, n_points=15)
        with self.core.state_lock:
            self.assertFalse(self.core.fsm.calibration_complete())
        self.assertIs(self.core.fsm.state, coremain.fsm.State.UNCALIBRATED)


class TestStop(unittest.TestCase):

    def test_stop_is_clean_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            core = coremain.start(control_host="127.0.0.1", control_port=0,
                                  web_host="127.0.0.1", web_port=0,
                                  cal_path=os.path.join(tmp, "loadcell_cal.json"),
                                  scale_open_port=_no_serial_port)
            core.stop()
            core.stop()   # must not raise


class TestHoverAndDwellOverTheWire(CoreCase):
    """Doc section 9.4 through a real Core: real UDP datagrams in, real
    `state` messages out over a real socket.

    Not `DwellTracker` driven directly — `test_hover.py` does that. What
    this covers is the wiring: that a datagram reaches the hit test, that
    the hover reaches `state.bins[].hl`, and that ambient hands are gone by
    the time either happens.
    """

    def cursor_sender(self):
        tx = cursorbus.Sender(targets=[("127.0.0.1", self.core.cursor.port)])
        self.addCleanup(tx.close)
        return tx

    def bin_centre(self, i):
        rect = self.core.camera_grid.rects()[i]
        return rect[0] + rect[2] / 2, rect[1] + rect[3] / 2

    def send_pointer(self, tx, x, y):
        tx.send([cursorbus.Hand(id=1, role=cursorbus.ROLE_POINTER,
                                x=x, y=y, conf=0.9)], ts=time.time())

    def send_ambient(self, tx, x, y):
        tx.send([cursorbus.Hand(id=2, role=cursorbus.ROLE_AMBIENT,
                                x=x, y=y, conf=0.9)], ts=time.time())

    def wait_for_state(self, msgs, lock, pred, timeout=DEADLINE):
        """Wait for a `state` message satisfying `pred`, keeping the cursor
        stream alive — a single datagram may land between two ticks, so a
        test that sent once and waited could time out for a reason that has
        nothing to do with the behaviour being checked.
        """
        end = time.time() + timeout
        while time.time() < end:
            with lock:
                for m in reversed(msgs[-30:]):
                    if pred(m):
                        return m
            time.sleep(0.02)
        return None

    def test_a_pointer_over_a_bin_lights_that_bin(self):
        c, msgs, lock = self.of_client()
        tx = self.cursor_sender()
        x, y = self.bin_centre(3)
        for _ in range(20):
            self.send_pointer(tx, x, y)
            time.sleep(0.02)
        got = self.wait_for_state(
            msgs, lock, lambda m: m["bins"][3]["hl"] == "hover")
        self.assertIsNotNone(got, "bin 3 never went to hover")
        # ...and only that bin.
        self.assertEqual([b["i"] for b in got["bins"] if b["hl"] == "hover"],
                         [3])

    def test_an_ambient_hand_over_a_bin_lights_nothing(self):
        # Doc section 21's M5 acceptance test: "Left hand over bin 3 →
        # nothing happens to the UI. Try hard to make it select."
        c, msgs, lock = self.of_client()
        tx = self.cursor_sender()
        x, y = self.bin_centre(3)
        for _ in range(30):
            self.send_ambient(tx, x, y)
            time.sleep(0.02)
        self.wait_for_n(msgs, lock, 10)
        with lock:
            recent = list(msgs[-10:])
        for m in recent:
            self.assertTrue(all(b["hl"] != "hover" for b in m["bins"]),
                            "an ambient hand produced a hover")

    def test_a_pointer_starts_a_session(self):
        # Doc section 9.1's IDLE -> SELECTING, which has had no driver
        # since M1 (`fsm.hand_present()` existed with nothing calling it).
        self.assertIs(self.core.fsm.state, coremain.fsm.State.IDLE)
        tx = self.cursor_sender()
        x, y = self.bin_centre(0)
        end = time.time() + DEADLINE
        while (time.time() < end
               and self.core.fsm.state is not coremain.fsm.State.SELECTING):
            self.send_pointer(tx, x, y)
            time.sleep(0.02)
        self.assertIs(self.core.fsm.state, coremain.fsm.State.SELECTING)

    def test_an_ambient_hand_alone_does_not_start_a_session(self):
        # A bowl set down on the table must not open an order.
        tx = self.cursor_sender()
        x, y = self.bin_centre(0)
        for _ in range(25):
            self.send_ambient(tx, x, y)
            time.sleep(0.02)
        self.assertIs(self.core.fsm.state, coremain.fsm.State.IDLE)

    def test_no_widgets_appear_once_a_session_starts(self):
        # RIG_FEEDBACK_2026-08-12.md items 4-7, 2026-08-13: Done/Cancel/
        # Language are removed outright — `widgets_for()` always returns
        # none, so `state.widgets` must stay empty even in SELECTING, not
        # just in IDLE (`test_shape_matches_doc_4_3` already covers IDLE).
        c, msgs, lock = self.of_client()
        tx = self.cursor_sender()
        x, y = self.bin_centre(0)
        end = time.time() + DEADLINE
        while (time.time() < end
               and self.core.fsm.state is not coremain.fsm.State.SELECTING):
            self.send_pointer(tx, x, y)
            time.sleep(0.02)
        self.assertIs(self.core.fsm.state, coremain.fsm.State.SELECTING)
        self.wait_for_n(msgs, lock, 5)
        with lock:
            recent = list(msgs[-5:])
        for m in recent:
            self.assertEqual(m["widgets"], [])

    def test_dwelling_where_done_used_to_be_does_nothing(self):
        # The dwell mechanism (`DwellTracker`, `_fire_widget`'s dispatch
        # table) is kept intact and unused for a future real widget set —
        # this guards against it silently reactivating: with no widgets
        # ever produced, holding a hand over the old Done rect for well
        # past the (shortened) dwell time must fire nothing and touch
        # nothing.
        self.core.dwell.dwell_ms = 200.0
        c, msgs, lock = self.of_client()
        evts = []

        def on_msg(m):
            if m.get("t") == "evt":
                evts.append(m)

        c.stop()
        self._wire_clients.remove(c)
        c2 = self.wire_client("of", on_message=on_msg)
        self.assertTrue(c2.wait_connected(DEADLINE))

        tx = self.cursor_sender()
        bx, by = self.bin_centre(0)
        end = time.time() + DEADLINE
        while (time.time() < end
               and self.core.fsm.state is not coremain.fsm.State.SELECTING):
            self.send_pointer(tx, bx, by)
            time.sleep(0.02)

        rects = coremain.hover.layout()
        dx = rects["done"][0] + rects["done"][2] / 2
        dy = rects["done"][1] + rects["done"][3] / 2
        end = time.time() + 1.0
        while time.time() < end:
            self.send_pointer(tx, dx, dy)
            time.sleep(0.02)
        self.assertFalse(any(e.get("id") == "dwell_fire" for e in evts),
                         "dwelling the old Done rect fired something")

    def test_no_language_widget_on_the_wire(self):
        # Language is gone with the other two (RIG_FEEDBACK items 4-7) —
        # `test_hover.py`'s `test_locale_count_does_not_resurrect_language`
        # covers `widgets_for()` itself across locale counts; this is the
        # over-the-wire half, that a real Core never puts one on `state`.
        c, msgs, lock = self.of_client()
        self.wait_for_n(msgs, lock, 1)
        with lock:
            widgets = msgs[0]["widgets"]
        self.assertEqual(widgets, [])

    def test_hover_outranks_picked_on_a_bin_already_taken_from(self):
        # A bin the diner has already taken from must still respond to
        # their hand. `picked` is a fact about the whole session and stays
        # true for the rest of it; `hover` is live feedback that the table
        # has seen this hand right now. Ordering them the other way round
        # makes every bin go dead after its first pick — and it passes
        # every other test in this class, because they all hover an
        # untouched bin.
        with self.core.state_lock:
            self.core.cart.mock_pick(5, 120.0)
        c, msgs, lock = self.of_client()
        tx = self.cursor_sender()
        x, y = self.bin_centre(5)
        for _ in range(30):
            self.send_pointer(tx, x, y)
            time.sleep(0.02)
        got = self.wait_for_state(
            msgs, lock, lambda m: m["bins"][5]["hl"] == "hover")
        self.assertIsNotNone(
            got, "a bin that had been picked from never showed hover")
        self.assertGreater(got["bins"][5]["picked"], 0,
                           "the fixture did not actually register a pick")

    def test_hover_never_bills(self):
        # Doc section 9.4: "hover on a bin is feedback only. It never
        # bills. Billing is weight, always" (I4).
        c, msgs, lock = self.of_client()
        tx = self.cursor_sender()
        x, y = self.bin_centre(2)
        for _ in range(30):
            self.send_pointer(tx, x, y)
            time.sleep(0.02)
        got = self.wait_for_state(
            msgs, lock, lambda m: m["bins"][2]["hl"] == "hover")
        self.assertIsNotNone(got)
        self.assertEqual(got["bins"][2]["picked"], 0)
        self.assertEqual(got["total"]["amount"], 0.0)

    def test_the_staff_view_is_told_where_the_hands_are(self):
        # Doc section 12.3's hand markers.
        ws = self.ws()
        tx = self.cursor_sender()
        x, y = self.bin_centre(1)
        # BOTH hands in ONE datagram. A datagram is a complete snapshot of
        # the table (doc section 4.6), so sending two single-hand frames
        # would be two statements — "one pointer", then "one ambient" —
        # and drain-to-latest would correctly keep only the second.
        for _ in range(30):
            tx.send([cursorbus.Hand(id=1, role=cursorbus.ROLE_POINTER,
                                    x=x, y=y, conf=0.9),
                     cursorbus.Hand(id=2, role=cursorbus.ROLE_AMBIENT,
                                    x=50.0, y=50.0, conf=0.8)],
                    ts=time.time())
            time.sleep(0.02)
        msg = self.recv_until(ws, lambda m: (m.get("t") == "hands"
                                             and m.get("hands")))
        self.assertIsNotNone(msg, "no `hands` message reached the tablet")
        roles = {h["role"] for h in msg["hands"]}
        self.assertIn("pointer", roles)


class TestTrackerWelcomeConfig(CoreCase):
    """Doc section 4.2's `welcome.cfg`, which was `{}` for every client
    from M0 to M4 (a known gap in CLAUDE.md) because nothing needed it
    until the tracker.

    Doc section 5.3: "Core pushes it to `tracker` in the `welcome`
    message. Tracker converts MediaPipe output to stage space before
    sending."
    """

    def welcome_cfg(self, who):
        got = {}
        done = threading.Event()

        def on_connect(cfg):
            got.update(cfg or {})
            done.set()

        c = wire.Client("127.0.0.1", self.core.control_port, who,
                        on_connect=on_connect)
        self._wire_clients.append(c)
        c.start()
        self.assertTrue(done.wait(DEADLINE), f"{who} never got a welcome")
        return got

    def test_the_tracker_is_told_the_homography(self):
        cfg = self.welcome_cfg("tracker")
        self.assertEqual(cfg["homography_cam_to_stage"], _FIXTURE_H)

    def test_the_tracker_is_told_the_stage_size_and_emit_rate(self):
        cfg = self.welcome_cfg("tracker")
        self.assertEqual(cfg["stage"], [1920, 1080])
        self.assertEqual(cfg["emit_hz"], coremain.TRACKER_EMIT_HZ)
        self.assertIs(cfg["mirror_handedness"], False)

    def test_the_tracker_is_told_mediapipe_is_enabled_while_serving(self):
        # 2026-08-14: `mediapipe_enabled` — every CoreCase fixture boots
        # SERVING, so a tracker connecting fresh must be told to detect.
        cfg = self.welcome_cfg("tracker")
        self.assertIs(cfg["mediapipe_enabled"], True)

    def test_nobody_else_is_given_a_config(self):
        # `of` holds only rig calibration it reads off disk itself (I2) and
        # the classifier is told what to do per command (doc section 4.7).
        # Sending either of them a homography would be handing a second
        # copy of the geometry to a process that has no business deriving
        # anything from it.
        self.assertEqual(self.welcome_cfg("of"), {})
        self.assertEqual(self.welcome_cfg("classifier"), {})


class TestTrackerWelcomeOnAnUncalibratedTable(CoreCase):
    calibrated_fixture = False

    def test_the_homography_is_null_not_identity(self):
        # Identity is a matrix that WORKS: it would put camera pixels
        # straight onto the stage and produce confident cursors in the
        # wrong place, on a table doc section 9.1 says is not usable yet.
        # An absent matrix stops the tracker emitting at all.
        got = {}
        done = threading.Event()

        def on_connect(cfg):
            got.update({"cfg": cfg})
            done.set()

        c = wire.Client("127.0.0.1", self.core.control_port, "tracker",
                        on_connect=on_connect)
        self._wire_clients.append(c)
        c.start()
        self.assertTrue(done.wait(DEADLINE))
        self.assertIsNone(got["cfg"]["homography_cam_to_stage"])


class TestTrackerConfigIsPushedOnChange(CoreCase):
    calibrated_fixture = False

    def test_a_new_homography_reaches_a_tracker_that_is_already_connected(self):
        # The tracker holds no config of its own (doc section 4.2) and was
        # told "no homography" when it connected. A solve that did not
        # reach it would leave the table calibrated and the cursor dead
        # until the next restart — with nothing on any screen saying so.
        #
        # Waits for the push that actually CARRIES a homography, not just
        # any push: `set_mode` below now triggers its own earlier `cfg`
        # push too (2026-08-14's `mediapipe_enabled`, sent on every real
        # mode transition), which would otherwise race this test's single
        # `got_push` event against `manual_calibrate`'s later one.
        pushed = []
        got_homography = threading.Event()

        def on_msg(m):
            if m.get("t") == "cfg":
                pushed.append(m)
                if m["cfg"].get("homography_cam_to_stage") is not None:
                    got_homography.set()

        c = self.wire_client("tracker", on_message=on_msg)
        self.assertTrue(c.wait_connected(DEADLINE))

        ws = self.ws()
        ws.send(json.dumps({"t": "set_mode", "mode": "setting"}))
        ws.send(json.dumps({"t": "manual_calibrate",
                            "points": [[100, 900], [1800, 900],
                                       [1700, 200], [200, 200]]}))
        self.assertTrue(got_homography.wait(DEADLINE),
                        "the tracker was never told about the new homography")
        self.assertIsNotNone(pushed[-1]["cfg"]["homography_cam_to_stage"])


class TestTrackerToldAboutSettingMode(CoreCase):
    """2026-08-14: `_tracker_cfg()`'s `mediapipe_enabled`, live-pushed by
    `_handle_set_mode` the same way `_push_tracker_cfg` already pushes a
    new homography — doc section 4.2's `cfg`, sent to an already-connected
    tracker rather than waiting for its next reconnect.
    """

    def enabled_pushes(self):
        pushed = []

        def on_msg(m):
            if m.get("t") == "cfg" and "mediapipe_enabled" in m.get("cfg", {}):
                pushed.append(m["cfg"]["mediapipe_enabled"])
        c = self.wire_client("tracker", on_message=on_msg)
        self.assertTrue(c.wait_connected(DEADLINE))
        return pushed

    def test_entering_setting_mode_pushes_false(self):
        pushed = self.enabled_pushes()
        ws = self.ws()
        ws.send(json.dumps({"t": "set_mode", "mode": "setting"}))
        self.assertTrue(wait_for(
            lambda: self.core.fsm.state is fsm.State.SETTING))
        self.assertTrue(wait_for(lambda: pushed and pushed[-1] is False))

    def test_exiting_setting_mode_pushes_true_again(self):
        pushed = self.enabled_pushes()
        ws = self.ws()
        ws.send(json.dumps({"t": "set_mode", "mode": "setting"}))
        self.assertTrue(wait_for(
            lambda: self.core.fsm.state is fsm.State.SETTING))
        ws.send(json.dumps({"t": "set_mode", "mode": "serving"}))
        self.assertTrue(wait_for(
            lambda: self.core.fsm.state is not fsm.State.SETTING))
        self.assertTrue(wait_for(lambda: pushed and pushed[-1] is True))

    def test_a_refused_set_mode_pushes_nothing(self):
        # An unresolved bin refuses SETTING->SERVING with a confirm prompt
        # (doc section 9.3) — the mode did not change, so re-pushing the
        # tracker's config would be a lie about what just happened.
        with self.core.state_lock:
            self.core.binmap.set_bin(0, item_id=None, conf=0.0, source="mock")
        ws = self.ws()
        ws.send(json.dumps({"t": "set_mode", "mode": "setting"}))
        self.assertTrue(wait_for(
            lambda: self.core.fsm.state is fsm.State.SETTING))
        pushed = self.enabled_pushes()
        ws.send(json.dumps({"t": "set_mode", "mode": "serving"}))
        # Several `mode` broadcasts precede the refusal on this connection
        # (the join seed, the earlier successful entry into SETTING) — the
        # one this test wants is the first with a non-null `refused`.
        deadline = time.monotonic() + DEADLINE
        msg = None
        while time.monotonic() < deadline:
            got = self.recv_json(ws, timeout=deadline - time.monotonic())
            if got.get("t") == "mode" and got.get("refused") is not None:
                msg = got
                break
        self.assertIsNotNone(msg, "no refused mode message arrived")
        time.sleep(0.1)
        self.assertEqual(pushed, [])


if __name__ == "__main__":
    unittest.main()
