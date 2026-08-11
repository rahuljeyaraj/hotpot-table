"""Core process — M0 scope (doc section 21, build item 7), M1 build item
3 (the domain modules wired in and broadcasting `state` at 60Hz), M2
build items 4 and 5 (the staff view's Bins tab, doc section 12.4, and
real grams wired into pricing), and now M2.6: the SERVING/SETTING mode.

What exists here: the one control server every other process dials into,
the client registry that turns hellos and heartbeats into the six status
pips, a minimal staff view that pushes those pips to the browser over a
WebSocket, the five pure domain modules from M1 build item 2 (pricing,
cart, binmap, i18n, fsm) held as Core's state and serialised into doc
section 4.3's `state` message sent to `of` at a fixed 60Hz, and — new
here — the load-cell reader and calibrator (core/scale.py,
core/calibrator.py) that the Bins tab drives over a second, lower-rate
broadcast to the web hub.

M2 build item 5 ("wire real grams into pricing") is done: every state
tick, `_apply_scale_to_cart()` reads `self.scale.read()` and feeds any
bin with a real (non-None) grams value into Cart — set_live_grams() from
then on, but seed_live_grams() the first time, so the M1 mock seed's
placeholder weight never gets priced against a real one (cart.py's own
docstring). A bin the scale cannot weigh (uncalibrated, or no XIAO at
all) is left exactly where the developer panel's mock controls put it —
doc section 12.8's "stays forever as a test harness" is what that
sentence was for. The Bins tab's grams still come straight from
`self.scale.read()` rather than Cart, but for a bin Cart has already
adopted the two numbers are now the same reading one tick apart, not two
independent sources.

M2.6 adds the mode itself. `_state_msg()`'s `"mode"` is now derived from
`fsm.state` instead of the hardcoded `"diner"` it carried since M1 —
doc section 4.3 has specified that field since v3.0 and nothing had ever
produced it. `_apply_scale_to_cart()` returns immediately in SETTING, and
`fsm.exit_setting()` refreshes every bin's weight (via
`_refresh_weights_from_scale` below), re-baselines and locks the bin map
on the way out. That mode-wide gate is what let M2.4's `_calibrating`
dict, `CAL_FREEZE_TIMEOUT_S`, `_handle_cal_session` and the
`cal_begin`/`cal_end` wire messages all be deleted: they were the per-bin
stand-in for a "not billing" state that did not exist yet. Tare and
Calibrate now require SETTING (M2.6 decision 4), and the tablet drives
the mode with `set_mode`, answered by a broadcast `mode` message.

**Do NOT** (M0 build list, doc section 21): open the camera, touch
MediaPipe, or write any oF code. This file does none of those.

Host and port are hardcoded to the doc section 4.1 defaults, same as
common/stub.py and for the same reason: config loading is not built until
it has a reader that needs more than one key. `SCALE_PORT` below is the
same story — doc section 8.6's config example has no serial-port key yet.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from hotpot.common import health, log, wire
from hotpot.core import binmap, calibrator, cart, fsm, i18n, loadcell_cal, pricing, scale
from hotpot.core.web import server as web

_log = logging.getLogger("hotpot.core")

CONTROL_HOST = "127.0.0.1"     # doc 4.1: only sibling processes on this
                                # same machine ever dial the control port.
CONTROL_PORT = 8765            # doc 4.1: core.control_port default

# 0.0.0.0, not 127.0.0.1: the staff view is read from a tablet (doc
# section 12.1, "assume a tablet"), which reaches this over the LAN, not
# loopback. Binding to loopback would work on the dev machine and fail
# silently on the rig.
WEB_HOST = "0.0.0.0"
WEB_PORT = 8090                # doc 4.1: core.web_port default (was 8080;
                                # moved off it — Windows dev machines keep
                                # squatting it via a stale WSL2 portproxy
                                # relay to a stopped distro, see CLAUDE.md)

STATIC_ROOT = Path(__file__).resolve().parent / "web" / "static"

# core/main.py -> core -> hotpot -> python -> repo root. Same hardcoded-
# until-something-needs-config-loading rationale as the ports above.
DATA_DIR = Path(__file__).resolve().parents[3] / "data"

# Doc section 4.3: "sent at a fixed 60Hz, whether or not anything
# changed" — a fixed-rate stream is what lets oF's tweener trust silence
# never means "core is dead" (of/main.py checks staleness, not gaps).
STATE_HZ = 60.0
STATE_INTERVAL = 1.0 / STATE_HZ

# Every bin starts full of a fixed placeholder weight so the mock
# pick/put-back cycle (doc section 12.8) has something to remove grams
# from before any bin has a real scale reading — first boot, an
# uncalibrated bin, or no XIAO at all. Per bin, this is a one-time seed:
# _apply_scale_to_cart() replaces it with the real weight (via
# cart.seed_live_grams(), never plain set_live_grams() — see that
# module's docstring on why the hand-off needs its own entry point) the
# first time core/scale.py reports one, and never reads it again after
# that. Not "removed" by M2 build item 5 — still needed as long as a
# bin can be diner-facing before it has been calibrated.
MOCK_SEED_GRAMS = 500.0

# Doc section 8.6's config example has no serial-port key (a known gap —
# config loading is not built, CLAUDE.md's "Known gaps" list). Hardcoded
# the same way CONTROL_PORT/WEB_PORT are, and matches this dev rig's XIAO
# (CLAUDE.md, verified 2026-08-11). Not the deploy machine's port — that
# is unmeasured and waits on config loading, same as every other §8.6 key.
SCALE_PORT = "COM5"

# Doc section 12.4's Bins tab is "live" but does not need `state`'s 60Hz —
# nobody reads eight numbers that fast. 10Hz (every 6th state tick) is
# comfortably above flicker perception for a static numeric readout and a
# sixth of the traffic to a tablet that may be on Wi-Fi.
BINS_BROADCAST_EVERY = 6

# Doc section 12.4's "●●●●●●○○" noise-indicator dot bar. The doc gives the
# mockup, not a formula, so this is a UI heuristic, not a check anything
# bills from: 8 dots, spanning 2x the settle tolerance so a cell exactly
# at doc section 9.5's settle boundary — the number that actually
# matters — reads exactly half full, a quieter cell reads fuller, and a
# cell at or past twice the tolerance reads empty.
NOISE_DOTS = 8
NOISE_BAR_SPAN_MULT = 2.0

# Doc section 4.3's `mode` field, both values. Derived from fsm.State, not
# stored: two places that can disagree about which mode the table is in is
# exactly the bug M2.6 exists to remove.
MODE_SERVING = "serving"
MODE_SETTING = "setting"


def _seed_binmap(catalogue: pricing.Catalogue) -> binmap.BinMap:
    """M1's fixed hand-built bin map (binmap.py's docstring, doc section
    21 build item 2): no classifier and no Setup-tab wizard exist yet to
    populate this for real, so every bin is paired with a catalogue item
    in catalogue.json's order, one-to-one, at conf 1.0 — comfortably
    clear of DEFAULT_CONF_FLOOR — so bins are billable from boot.
    """
    bm = binmap.BinMap()
    ids = catalogue.ids()
    for i in range(binmap.NUM_BINS):
        if i < len(ids):
            bm.set_bin(i, item_id=ids[i], conf=1.0, source="mock")
    return bm


def _seed_cart() -> cart.Cart:
    """Every bin starts at MOCK_SEED_GRAMS, then reset_session() (I6's
    re-baseline) sets start_g to match — so removed grams is 0 at boot,
    not a negative clamp from an empty tray. See MOCK_SEED_GRAMS above.
    """
    c = cart.Cart()
    for i in range(cart.NUM_BINS):
        c.set_live_grams(i, MOCK_SEED_GRAMS)
    c.reset_session()
    return c


def _noise_dots(noise_g: Optional[float], settle_tol_g: float) -> Optional[int]:
    """Doc section 12.4's dot bar, 0-8. `None` (not 0) when the bin has
    never been calibrated — there is no noise number to show, and 0 dots
    would read as "extremely noisy" rather than "unmeasured".
    """
    if noise_g is None:
        return None
    span = settle_tol_g * NOISE_BAR_SPAN_MULT
    quiet = 1.0 - (noise_g / span if span > 0 else 1.0)
    return round(NOISE_DOTS * max(0.0, min(1.0, quiet)))


class Core:
    """Everything M0 wires up, held together so tests and main() can start
    and stop it as one unit rather than three separately-ordered pieces.
    """

    def __init__(
        self,
        control_host: str = CONTROL_HOST,
        control_port: int = CONTROL_PORT,
        web_host: str = WEB_HOST,
        web_port: int = WEB_PORT,
        static_root: Path = STATIC_ROOT,
        data_dir: Path = DATA_DIR,
        scale_port: str = SCALE_PORT,
        cal_path: Path = calibrator.CAL_PATH,
        scale_open_port: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.registry = health.Registry(on_change=self._on_pip_change)
        self.control = wire.Server(
            control_host, control_port,
            on_message=self._on_message,
            on_connect=self._on_connect,
            on_disconnect=self._on_disconnect,
            name="core",
        )
        self.web = web.Server(web_host, web_port, static_root,
                              on_join=self._join_msgs, on_message=self._on_web_message)
        # Core beats its own pip from its own loop rather than being
        # hardcoded green (common/health.py's rationale): a wedged main
        # loop with a live web thread is a real failure and this is what
        # makes it show up red instead of hiding behind the other five.
        self._self_beat = health.Heartbeat(self._beat_self, who="core")

        # -- M1 build item 3: the domain state the broadcaster sends ----
        data_dir = Path(data_dir)
        self.catalogue = pricing.Catalogue.load(data_dir / "catalogue.json")
        self.locale = i18n.DEFAULT_LOCALE   # build item 4: English only, for now
        self.locales = i18n.Locales.load(data_dir / "locales", locales=(self.locale,))
        self.binmap = _seed_binmap(self.catalogue)
        self.cart = _seed_cart()
        # binmap and refresh_weights are both here so that fsm.py owns all
        # three of doc section 9.1's setting-exit steps — refresh, then
        # re-baseline, then lock — rather than leaving any of them to a
        # caller who might do two and forget the third. Read
        # fsm.exit_setting()'s docstring before changing either.
        self.fsm = fsm.Fsm(self.cart, self.binmap,
                           refresh_weights=self._refresh_weights_from_scale)

        # -- M2 build item 4: the Bins tab's reader and calibrator ------
        # calibrator.py's own docstring gives this exact wiring order:
        # load the saved calibration, hand it to the reader (never a
        # copy — see that module's docstring on why), and hand the
        # reader to the Calibrator. Building it here, always, rather
        # than only once a tablet opens the Bins tab: a missing or
        # unplugged XIAO is the ordinary boot state (scale.py's own
        # docstring) and the reader's backoff loop handles it quietly.
        #
        # `cal_path` is a parameter (defaulting to the real doc section
        # 8.3 file), not a hardcoded read of calibrator.CAL_PATH, purely
        # so a test can point it at a throwaway file the way
        # test_calibrator.py already does — this is the one file in the
        # repo doc section 9.6 calls out as able to silently mis-bill,
        # and a test run must never read or write the real one.
        #
        # `scale_open_port` exists for the same reason and is not a
        # hypothetical: SCALE_PORT's default is this dev machine's real
        # port, and on it COM5 is a live XIAO (verified 2026-08-11) — a
        # test that leaves this None gets Core's own reader thread
        # racing real hardware counts against whatever it just fed in
        # through scale.feed(). scale.ScaleReader's own docstring built
        # this hook for exactly this: "the numbers in here can silently
        # mis-bill, so they have to be reachable from a test" with no
        # port attached.
        self.cal = loadcell_cal.Calibration.load(cal_path)
        self.scale = scale.ScaleReader(scale_port, cal=self.cal,
                                       open_port=scale_open_port)
        self.calibrator = calibrator.Calibrator(self.scale, path=cal_path)

        # M2 build item 5: which bins have ever had a real scale reading
        # applied to Cart. False means still on the M1 mock seed (or a
        # mock pick/put-back on top of it) — see _apply_scale_to_cart().
        self._scale_baselined = [False] * cart.NUM_BINS

        # The last (mode, cart_active) pair actually put on the wire. The
        # `mode` message is broadcast on change, not on a timer (M2.6 —
        # same model as _on_pip_change, no new clock), and this is what
        # "on change" is compared against. None means nothing has been
        # sent yet, so the first tick always sends one.
        self._last_mode_key: Optional[tuple] = None

        # I1 says core owns all state; it does not say core touches it from
        # one thread, and core does not. Reads happen on the 60Hz broadcast
        # thread; writes arrive on whichever of the `websockets` library's
        # per-connection threads a tablet is attached to. Every mutation of
        # cart/binmap/fsm and every read that builds a `state` message takes
        # this, so a message is always a snapshot of one instant rather than
        # a mix of two.
        #
        # The damage today is one frame wide and nobody could see it. It
        # stops being cosmetic at M2 (core/scale.py's serial thread writes
        # grams at ~78Hz — doc §9.5 locks its own slot, which says nothing
        # about the cart it feeds) and it stops being survivable at M6,
        # where finalisation is `cart.finalize()` then an order write then
        # `reset_session()`: read between the first and third and the table
        # broadcasts snapped shown_g against a start_g that has not been
        # re-baselined yet, which is a recap disagreeing with the bill. That
        # is the I4 failure arriving by a door I4 does not guard.
        #
        # RLock, not Lock: doc §9.1's triggers already nest (fsm.cancel()
        # calls cart.reset_session()), and a future handler that takes this
        # and then calls one of those must not deadlock on itself.
        self.state_lock = threading.RLock()

        self._state_seq = 0
        self._state_stop = threading.Event()
        self._state_thread: Optional[threading.Thread] = None

    # -- lifecycle -----------------------------------------------------

    def start(self) -> None:
        self.registry.start()
        self.control.start()
        self.web.start()
        self.scale.start()
        self._self_beat.start()
        # BOOT -> IDLE always succeeds until M4 adds the UNCALIBRATED
        # check (fsm.py's docstring); M1 has nothing to wait on.
        self.fsm.boot_complete()
        self._state_thread = threading.Thread(
            target=self._state_loop, name="core-state", daemon=True)
        self._state_thread.start()

    def stop(self) -> None:
        self._state_stop.set()
        if self._state_thread is not None and self._state_thread.is_alive():
            self._state_thread.join(2.0)
        self._self_beat.stop()
        self.scale.stop()
        self.web.stop()
        self.control.stop()
        self.registry.stop()

    @property
    def control_port(self) -> int:
        return self.control.port

    @property
    def web_port(self) -> int:
        return self.web.port

    # -- wire.Server callbacks -------------------------------------------

    def _on_connect(self, conn: wire.Connection) -> None:
        hello = conn.hello or {}
        pid = hello.get("pid")
        ver = hello.get("ver")
        self.registry.connected(
            conn.who,
            pid=pid if isinstance(pid, int) else None,
            ver=ver if isinstance(ver, int) else None,
        )

    def _on_message(self, conn: wire.Connection, msg: Dict[str, Any]) -> None:
        if self.registry.handle(conn.who, msg):
            return
        # M0 speaks nothing else on the control link yet. An unrecognised
        # `t` from a known process is worth a log line, not a dropped
        # link — wire.py's job is framing, not protocol enforcement.
        _log.debug("core: %s sent unhandled message type %r", conn.who, msg.get("t"))

    def _on_disconnect(self, conn: wire.Connection, reason: str) -> None:
        self.registry.disconnected(conn.who, reason)

    # -- self heartbeat ----------------------------------------------------

    def _beat_self(self, msg: Dict[str, Any]) -> bool:
        self.registry.beat("core", msg.get("ts"))
        return True

    # -- pushing pips to the staff view -------------------------------------

    def _pips_msg(self) -> Dict[str, Any]:
        return {"t": "pips", "pips": self.registry.snapshot()}

    def _on_pip_change(self, who: str, old: str, new: str) -> None:
        self.web.broadcast(self._pips_msg())

    def _join_msgs(self) -> list:
        """Everything a tablet cannot derive on its own, sent the moment
        it attaches (web/server.py's `on_join`, which takes a list as of
        M2.6). The pips alone stopped being enough once the action bar
        existed: a tablet that joins mid-run would render ENTER SETTING
        MODE while the table was already in setting mode, and stay wrong
        until someone touched something.

        Not the `bins` message — that one is already on a 10Hz timer, so
        a joining tablet waits at most 100ms for it.
        """
        return [self._pips_msg(), self._mode_msg()]

    # -- the mode (doc sections 4.3, 9.1, 12.2 — M2.6) ----------------------

    def _mode_msg(self, refused: Optional[str] = None) -> Dict[str, Any]:
        """The `mode` message the staff view's action bar reads.

        `cart_active` rides along so the action bar can pre-warn — grey
        the primary button's subtitle, show "an order is in progress" —
        without a round trip to ask. `refused` carries
        `fsm.can_enter_setting()`'s reason on a rejected `set_mode` and is
        null otherwise; it is the *why* doc section 9.1 requires the staff
        view to show.
        """
        with self.state_lock:
            mode = (MODE_SETTING if self.fsm.state is fsm.State.SETTING
                    else MODE_SERVING)
            active = self.cart.is_active()
        return {"t": "mode", "mode": mode, "cart_active": active,
                "refused": refused}

    def _publish_mode(self, refused: Optional[str] = None) -> None:
        """Broadcast `mode` when either field flips — or unconditionally
        when there is a refusal to deliver, since a refused `set_mode`
        changes neither field and would otherwise be silent.

        Broadcast rather than a direct reply to the tablet that asked:
        web/server.py's `on_message` hands this callback the decoded frame
        with no connection handle (server.py's docstring). That is
        actively right here rather than merely tolerated — the mode is
        global state and every attached tablet must agree on it, so
        answering only the asker would be the bug.

        Called from the state loop, so this is also what notices a mock
        pick or a real pick crossing the deadband and flipping
        `cart_active`. Deliberately not on its own timer: it sends
        nothing at all on a tick where nothing changed.
        """
        msg = self._mode_msg(refused)
        key = (msg["mode"], msg["cart_active"])
        if refused is None and key == self._last_mode_key:
            return
        self._last_mode_key = key
        self.web.broadcast(msg)

    # -- developer-panel mock controls (doc section 12.8, build item 5) -----
    # -- and the Bins tab's Tare/Calibrate wizard (doc section 12.4, M2) ----

    def _on_web_message(self, msg: Dict[str, Any]) -> None:
        """Everything the staff view can send: M1's mock pick/put-back
        pair (cart.py's mock_pick/mock_putback — the same entry point
        test_core_main.py's TestStateBroadcast pokes directly) and now
        M2's tare/calibrate pair (calibrator.Calibrator's two flows).
        """
        t = msg.get("t")
        if t == "mock_pick" or t == "mock_putback":
            self._handle_mock(t, msg)
            return
        if t == "tare" or t == "calibrate":
            self._handle_cal(t, msg)
            return
        if t == "set_mode":
            self._handle_set_mode(msg)
            return
        if t == "cancel_order":
            self._handle_cancel_order()
            return
        _log.debug("web: unhandled message type %r from a tablet", t)

    def _handle_set_mode(self, msg: Dict[str, Any]) -> None:
        """Doc section 12.2's action bar, both directions (M2.6).

        The transition happens under state_lock and the broadcast happens
        outside it — _broadcast_state's own rule, and it matters more
        here: exit_setting() reads the scale and re-baselines all eight
        bins, so holding the domain lock across the socket write would
        let a wedged tablet stall the 60Hz state loop.
        """
        want = msg.get("mode")
        if want not in (MODE_SERVING, MODE_SETTING):
            _log.warning("web: set_mode with bad mode %r — ignored", want)
            return
        refused = None
        with self.state_lock:
            if want == MODE_SETTING:
                # can_enter_setting() first, then enter: the refusal has a
                # reason the tablet has to show (doc section 9.1), and
                # enter_setting()'s bool cannot carry it.
                refused = self.fsm.can_enter_setting()
                if refused is None:
                    self.fsm.enter_setting()
            else:
                self.fsm.exit_setting()
        self._publish_mode(refused=refused)

    def _handle_cancel_order(self) -> None:
        """Doc section 12.2's second action-bar button, and the first
        caller `fsm.cancel()` has ever had — it has existed since M1
        build item 2 with nothing wired to it.

        Confirmation is the tablet's job (index.html), not this one: by
        the time a frame arrives here the operator has already said yes,
        and re-asking over the wire would need a round trip the wire
        protocol has no shape for.

        **The fallback below is not belt-and-braces — without it this
        button does nothing at all today, and the M2.6 plan did not
        anticipate that.** `fsm.cancel()` is SELECTING -> IDLE, and
        *nothing in this codebase yet moves the table into SELECTING*:
        `hand_present()` is M5's tracker and `staff_start()` is a Start
        button that does not exist. So a diner picking 50 g leaves the
        cart active while the FSM sits in IDLE, `cancel()` returns False,
        and the cart is never cleared — which would leave setting mode
        permanently refused with a "cancel the order first" button that
        cannot fix it. That is exactly the refusal loop doc section 9.1's
        pairing exists to prevent.

        `reset_session()` is doc section 9.1's own shared function, called
        here rather than re-derived, so this is not the inlining that rule
        forbids. Once M5 drives IDLE -> SELECTING for real, `cancel()`
        will handle every live case and this falls back to unreachable.
        """
        with self.state_lock:
            if not self.fsm.cancel() and self.cart.is_active():
                self.cart.reset_session()
        self._publish_mode()

    def _handle_mock(self, t: str, msg: Dict[str, Any]) -> None:
        i = msg.get("bin")
        grams = msg.get("grams")
        if not isinstance(i, int) or not (0 <= i < binmap.NUM_BINS):
            _log.warning("web: %s with bad bin %r — ignored", t, i)
            return
        if not isinstance(grams, (int, float)) or grams <= 0:
            _log.warning("web: %s bin %d with bad grams %r — ignored", t, i, grams)
            return
        with self.state_lock:
            if t == "mock_pick":
                self.cart.mock_pick(i, float(grams))
            else:
                self.cart.mock_putback(i, float(grams))

    def _handle_cal(self, t: str, msg: Dict[str, Any]) -> None:
        """Doc section 12.4's Tare and Calibrate buttons.

        Blocking — a capture window is a duration, calibrator.py's own
        docstring says so — and that is fine here: web/server.py gives
        every tablet its own thread, so one operator's 2s capture stalls
        only their own screen, which is exactly what the wizard is asking
        them to wait through, not anyone else's connection.

        Replies by broadcast, not a direct answer to the asking tablet:
        web/server.py's `on_message` hands this callback the decoded
        frame only, with no handle back to the connection it arrived on
        (see server.py's docstring). Every attached tablet sees the
        result, which matches how the six pips and the Bins tab's own
        `bins` message already work, rather than growing the wire
        protocol to answer one screen.
        """
        i = msg.get("bin")
        if not isinstance(i, int) or isinstance(i, bool) or not (0 <= i < binmap.NUM_BINS):
            _log.warning("web: %s with bad bin %r — ignored", t, i)
            return
        # M2.6 decision 4: both flows are gated behind setting mode. Doc
        # section 12.4's own steps require an empty bin and then a
        # reference mass placed in it — both of those are ordinary picks
        # in serving mode, and would bill. The mode is what makes them
        # safe, which is why this replaced the per-bin cal_begin/cal_end
        # freeze rather than sitting alongside it.
        with self.state_lock:
            in_setting = self.fsm.state is fsm.State.SETTING
        if not in_setting:
            self.web.broadcast({
                "t": "cal_result", "bin": i, "op": t, "ok": False,
                "message": "Enter setting mode first — the table is still billing.",
            })
            return
        try:
            if t == "tare":
                result = self.calibrator.tare(i)
            else:
                ref_mass_g = msg.get("ref_mass_g", calibrator.DEFAULT_REF_MASS_G)
                # isfinite, not just isinstance: a NaN or Infinity ref
                # mass survives `ref_mass_g <= 0` (every comparison
                # against NaN is False) and would otherwise reach
                # loadcell_cal.calibrate() and get written into
                # state/loadcell_cal.json — the one file doc section
                # 9.6's docstring calls out as able to silently mis-bill.
                if (not isinstance(ref_mass_g, (int, float))
                        or isinstance(ref_mass_g, bool)
                        or not math.isfinite(ref_mass_g)):
                    _log.warning("web: calibrate bin %d with bad ref_mass_g %r "
                                "— ignored", i, ref_mass_g)
                    return
                result = self.calibrator.calibrate(i, float(ref_mass_g))
        except calibrator.OPERATOR_ERRORS as e:
            self.web.broadcast({"t": "cal_result", "bin": i, "op": t,
                                "ok": False, "message": str(e)})
            return
        self.web.broadcast({
            "t": "cal_result", "bin": i, "op": t, "ok": True,
            "message": result.message, "grams": result.grams,
            "noise_g": result.noise_g, "noisy": result.noisy,
        })

    # -- wiring the scale into Cart (doc section 21, M2 build item 5) -------

    def _apply_scale_to_cart(self) -> None:
        """Every state tick: any bin the scale can currently weigh
        overwrites Cart's live grams; a bin it cannot (uncalibrated, or a
        stale/missing XIAO — core/scale.py's Reading.grams is None for
        both, on purpose) is left untouched, still driven by the
        developer panel's mock controls (doc section 12.8).

        **In setting mode this does nothing at all.** That is the whole
        point of M2.6: staff lifting a tray out is not a diner pick, so
        no weight change reaches Cart while the mode is live, and
        fsm.exit_setting() re-baselines all eight bins on the way out.
        This one early return replaced M2.4's `_calibrating` dict, its
        CAL_FREEZE_TIMEOUT_S dropped-tablet timeout and the
        cal_begin/cal_end wire messages — all of which existed only
        because there was no mode-wide "not billing" state to say this in.

        Caller holds state_lock — this mutates Cart, the same rule every
        other cart.py call site in this file already follows.
        """
        if self.fsm.state is fsm.State.SETTING:
            return
        reading = self.scale.read()
        for i in range(cart.NUM_BINS):
            g = reading.grams[i]
            if g is None:
                continue
            if self._scale_baselined[i]:
                self.cart.set_live_grams(i, g)
            else:
                # First real reading this bin has ever had: seed, not
                # set, so the gap to the M1 mock seed never prices as a
                # phantom pick (cart.py's seed_live_grams docstring).
                self.cart.seed_live_grams(i, g)
                self._scale_baselined[i] = True

    def _refresh_weights_from_scale(self) -> None:
        """Step 1 of fsm.exit_setting(), handed to Fsm as a callback so
        that module never has to know a serial port exists.

        **Read fsm.exit_setting()'s docstring before touching this.** It
        is the step whose absence bills the next diner for a tray swap:
        `_apply_scale_to_cart()` above has been returning early for the
        whole of setting mode, so every bin's live_g is still the weight
        it had when the mode was entered, and reset_session() is about to
        copy exactly that into start_g.

        seed_live_grams(), not set_live_grams(): the point is to move the
        bin's whole session onto its current real weight without pricing
        the difference, which is the same one-time hand-off M2 build item
        5 uses for a bin's first ever reading. A bin the scale cannot
        weigh keeps its mock/placeholder value, same rule as everywhere
        else — there is no real number to move it to.

        Caller (Fsm, from a handler that already holds it) holds
        state_lock.
        """
        reading = self.scale.read()
        for i in range(cart.NUM_BINS):
            g = reading.grams[i]
            if g is None:
                continue
            self.cart.seed_live_grams(i, g)
            self._scale_baselined[i] = True

    def _overlay_msg(self) -> Dict[str, Any]:
        """Doc section 9.5: "the table shows a fault overlay" when a bin
        that was billing from real weight can no longer be read — not
        merely "the scale has never been calibrated", which is the
        ordinary state of the M1 mock-only demo (doc section 12.8) and
        must not permanently cover the table in a fault screen. Only a
        bin that has crossed into `_scale_baselined` and then lost its
        reading counts: that is the "dead XIAO mid-session" case doc
        section 21's M2 acceptance test means, not "never plugged in".
        """
        reading = self.scale.read()
        lost = any(self._scale_baselined[i] and reading.grams[i] is None
                  for i in range(cart.NUM_BINS))
        return {"kind": "error"} if lost else {"kind": "none"}

    # -- the Bins tab (doc section 12.4, M2 build item 4) --------------------

    def _bins_tab_msg(self) -> Dict[str, Any]:
        """8 cards' worth of data. Grams still come straight from
        `self.scale.read()`, not Cart: this tab shows a bin's raw scale
        reading regardless of whether Cart has adopted it yet (a bin
        still on the M1 mock seed reads its real weight here well before
        _apply_scale_to_cart's first seed_live_grams() call catches up,
        which is correct — the Bins tab is a scale diagnostic, not a
        billing view).

        Read without `state_lock`: that lock protects the billing
        snapshot (cart+binmap+total as one instant, __init__'s own
        docstring), and this tab bills nothing. `Calibrator`'s own
        `_busy` lock already serialises concurrent tare/calibrate calls
        on this same `self.cal`, so the worst case here is one broadcast
        tick showing a card mid-update — cosmetic, not a mis-bill.
        """
        status = self.scale.status()
        reading = self.scale.read()
        settle_tol_g = self.scale.settle_tol_g
        bins = []
        for i in range(binmap.NUM_BINS):
            b = self.binmap.bins[i]
            item = self.catalogue.item(b.item_id)
            resolved = item is not None and self.binmap.resolved(i)
            if resolved:
                label = item.display_name(self.locale)
                per_100g = self.locales.currency(item.price_per_100g, self.locale)
                sub = f"{per_100g['text']}{self.locales.translate('per_100g', self.locale)}"
            else:
                label, sub = "", ""
            cal_bin = self.cal.bins[i]
            noise_g = cal_bin.noise_grams()
            grams = reading.grams[i]
            bins.append({
                "i": i,
                "label": label,
                "sub": sub,
                "grams": None if grams is None else round(grams),
                "calibrated": cal_bin.calibrated,
                "tared": cal_bin.tared,
                "noise_g": None if noise_g is None else round(noise_g, 1),
                "noisy": noise_g is not None and noise_g > settle_tol_g,
                "noise_dots": _noise_dots(noise_g, settle_tol_g),
            })
        return {
            "t": "bins",
            "serial": {"open": status["open"], "stale": status["stale"],
                      "hz": status["hz"]},
            "bins": bins,
        }

    # -- state broadcaster (doc section 4.3) --------------------------------

    def _state_loop(self) -> None:
        # Absolute deadlines, resynchronised rather than caught up — same
        # rationale as health.Heartbeat._run: if this thread was starved
        # for a while, firing a burst of queued-up state messages proves
        # nothing and just spends the send queue's budget (wire.py's
        # DEFAULT_SEND_QUEUE) for no benefit to `of`, which only ever
        # wants the latest one.
        due = time.monotonic()
        while not self._state_stop.is_set():
            self._broadcast_state()
            # _state_msg() has already advanced _state_seq for this tick
            # (it increments before returning), so this divides the same
            # 60Hz clock down to ~10Hz for the Bins tab rather than
            # running a second timer.
            if self._state_seq % BINS_BROADCAST_EVERY == 0:
                self.web.broadcast(self._bins_tab_msg())
            # Sends nothing unless mode or cart_active actually flipped
            # (_publish_mode's own check), so this is the "on change, not
            # on a timer" model reusing an existing clock rather than a
            # 60Hz mode stream.
            self._publish_mode()
            due += STATE_INTERVAL
            now = time.monotonic()
            if due <= now:
                due = now + STATE_INTERVAL
            if self._state_stop.wait(due - now):
                return

    def _broadcast_state(self) -> None:
        # The lock is taken inside _state_msg() and released before the
        # send, deliberately: broadcast() does socket I/O, and holding a
        # domain lock across it would let a wedged `of` link stall the
        # tablet's next mock pick.
        self.control.broadcast(self._state_msg(), only=["of"])

    def _state_msg(self) -> Dict[str, Any]:
        with self.state_lock:
            self._apply_scale_to_cart()
            msg = {
                "t": "state",
                "seq": self._state_seq,
                "ts": time.time(),
                "mode": (MODE_SETTING if self.fsm.state is fsm.State.SETTING
                          else MODE_SERVING),
                "locale": self.locale,
                # M8 hasn't built the fluid renderer yet; the shape is correct
                # per doc section 4.3, "mala" is the documented diner default,
                # and enabled:False is the honest statement that nothing is
                # rendering it yet.
                "fluid": {"style": "mala", "enabled": False, "intensity": 0.6},
                "bins": [self._bin_msg(i) for i in range(binmap.NUM_BINS)],
                "total": self._total_msg(),
                "widgets": [],      # no widget exists before BROTH/SPICE/etc. (M6)
                "overlay": self._overlay_msg(),
            }
            self._state_seq += 1
            return msg

    def _total_msg(self) -> Dict[str, Any]:
        # Doc §4.3's total is {amount, text} only — this adds `label`
        # (I2: oF does no lookup, so the "Total"/"总计" caption has to
        # arrive resolved from here, the same as every other diner-facing
        # string, not get hardcoded in UiLayer). Reuses the "total" key
        # already sitting in every locale file (data/locales/en.json) —
        # Locales.translate() falls back to "en" then to the key itself,
        # so a future locale missing the key degrades to English/"total"
        # rather than a blank caption.
        # shown_total(), not total(): the table shows the deadbanded number
        # so it stops twitching (I5), and so it agrees with the per-bin
        # lines above it. total() stays the billed number and is what M6
        # writes to SQLite — see pricing.py's two docstrings for why they
        # are separate and where they converge.
        out = self.locales.currency(
            pricing.shown_total(self.cart, self.binmap, self.catalogue),
            self.locale)
        out["label"] = self.locales.translate("total", self.locale)
        return out

    def _bin_msg(self, i: int) -> Dict[str, Any]:
        b = self.binmap.bins[i]
        item = self.catalogue.item(b.item_id)
        # Doc section 9.3: unresolved <=> no item_id, low conf, or (belt
        # and braces, matching pricing.total()'s own check) a stale
        # item_id the catalogue no longer has.
        resolved = item is not None and self.binmap.resolved(i)
        # One number drives both the grams the diner reads and the money
        # beside it, so the line checks out when someone does the
        # arithmetic by hand (doc section 21's M1 acceptance test asks for
        # exactly that). Deadbanded, per I5 — see pricing.shown_total().
        shown = pricing.display_grams(self.cart.shown_g[i])
        picked = int(shown)
        if resolved:
            # display_name(), never names.get(locale, item.id): item.id is
            # the hidden training label (pricing.Item's docstring), and the
            # old fallback put it on the projected surface the moment a
            # locale was missing one name. The catalogue is validated at
            # load so this call cannot fail to find a name.
            label = item.display_name(self.locale)
            per_100g = self.locales.currency(item.price_per_100g, self.locale)
            # The unit suffix is a locale string, not punctuation. I2 puts
            # every diner-facing word on this side of the wire, and zh wants
            # "/100克" — an f-string with "g" baked into it would have made
            # the price line the one part of the plate that stayed English
            # after the locale switch.
            sub = f"{per_100g['text']}{self.locales.translate('per_100g', self.locale)}"
            price = self.locales.currency(
                pricing.bin_price(shown, item.price_per_100g),
                self.locale,
            )["amount"]
        else:
            label, sub, price = "", "", 0.0
        return {
            "i": i,
            "label": label,
            "sub": sub,
            "grams": round(self.cart.live_g[i]),
            "picked": picked,
            "price": price,
            # hover/dwell (hl in {hover,disabled}) is M5's tracker; low
            # stock (lowstock) needs a threshold nothing sets yet (doc
            # section 22, P3). M1 only ever says picked or none.
            "hl": "picked" if picked > 0 else "none",
            "stock": "ok",
            "resolved": resolved,
        }


def start(**kwargs: Any) -> Core:
    """Wire everything up and start it. Returns immediately once every
    listener is bound — the caller decides what "ready" means from there.
    """
    core = Core(**kwargs)
    core.start()
    return core


def main() -> None:
    """Block until killed. What `python -m hotpot.core.main` runs."""
    log.setup("core")
    core = start()
    # After both ports are bound (doc section 10.2: "say it after the
    # port is bound"), not before — run.py's tier 3 is waiting on this
    # exact line to know core is genuinely serving.
    log.ready("core")
    try:
        threading.Event().wait()
    finally:
        core.stop()


if __name__ == "__main__":
    main()
