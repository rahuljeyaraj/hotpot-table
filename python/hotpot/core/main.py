"""Core process — M0 scope (doc section 21, build item 7), plus M1 build
item 3: the domain modules wired in and broadcasting `state` at 60Hz.

What exists here: the one control server every other process dials into,
the client registry that turns hellos and heartbeats into the six status
pips, a minimal staff view that pushes those pips to the browser over a
WebSocket, and — new in M1 — the five pure domain modules from build item
2 (pricing, cart, binmap, i18n, fsm) held as Core's state and serialised
into doc section 4.3's `state` message, sent to `of` at a fixed 60Hz.

**Do NOT** (M0 build list, doc section 21): open the camera, open the
serial port, touch MediaPipe, or write any oF code. This file does none
of those — it does not even know those things exist yet.

Host and port are hardcoded to the doc section 4.1 defaults, same as
common/stub.py and for the same reason: config loading is not built until
it has a reader that needs more than one key.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from hotpot.common import health, log, wire
from hotpot.core import binmap, cart, fsm, i18n, pricing
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

# M1 has no load cells (M2) and no classifier (M6): every bin starts full
# of a fixed placeholder weight so the mock pick/put-back cycle (doc
# section 12.8) has something to remove grams from. Overwritten for real
# once core/scale.py's median-of-5 reading exists — see cart.py's
# docstring ("Where live_g comes from is not this module's business").
MOCK_SEED_GRAMS = 500.0


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
                              on_join=self._pips_msg, on_message=self._on_web_message)
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
        self.fsm = fsm.Fsm(self.cart)

        self._state_seq = 0
        self._state_stop = threading.Event()
        self._state_thread: Optional[threading.Thread] = None

    # -- lifecycle -----------------------------------------------------

    def start(self) -> None:
        self.registry.start()
        self.control.start()
        self.web.start()
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

    # -- developer-panel mock controls (doc section 12.8, build item 5) -----

    def _on_web_message(self, msg: Dict[str, Any]) -> None:
        """Everything the staff view can send. M1 has exactly one pair:
        the mock pick/put-back buttons that stand in for load cells until
        M2 wires up core/scale.py. Both go through cart.py's mock_pick/
        mock_putback — the same entry point test_core_main.py's
        TestStateBroadcast pokes directly, closing the gap that test's
        docstring calls out ("bypasses the developer panel, not built
        yet").
        """
        t = msg.get("t")
        if t == "mock_pick" or t == "mock_putback":
            self._handle_mock(t, msg)
            return
        _log.debug("web: unhandled message type %r from a tablet", t)

    def _handle_mock(self, t: str, msg: Dict[str, Any]) -> None:
        i = msg.get("bin")
        grams = msg.get("grams")
        if not isinstance(i, int) or not (0 <= i < binmap.NUM_BINS):
            _log.warning("web: %s with bad bin %r — ignored", t, i)
            return
        if not isinstance(grams, (int, float)) or grams <= 0:
            _log.warning("web: %s bin %d with bad grams %r — ignored", t, i, grams)
            return
        if t == "mock_pick":
            self.cart.mock_pick(i, float(grams))
        else:
            self.cart.mock_putback(i, float(grams))

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
            due += STATE_INTERVAL
            now = time.monotonic()
            if due <= now:
                due = now + STATE_INTERVAL
            if self._state_stop.wait(due - now):
                return

    def _broadcast_state(self) -> None:
        self.control.broadcast(self._state_msg(), only=["of"])

    def _state_msg(self) -> Dict[str, Any]:
        msg = {
            "t": "state",
            "seq": self._state_seq,
            "ts": time.time(),
            "mode": "diner",   # STAFF isn't a state this milestone's Fsm has
            "locale": self.locale,
            # M8 hasn't built the fluid renderer yet; the shape is correct
            # per doc section 4.3, "mala" is the documented diner default,
            # and enabled:False is the honest statement that nothing is
            # rendering it yet.
            "fluid": {"style": "mala", "enabled": False, "intensity": 0.6},
            "bins": [self._bin_msg(i) for i in range(binmap.NUM_BINS)],
            "total": self._total_msg(),
            "widgets": [],      # no widget exists before BROTH/SPICE/etc. (M6)
            "overlay": {"kind": "none"},
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
            label = item.names.get(self.locale, item.id)
            per_100g = self.locales.currency(item.price_per_100g, self.locale)
            sub = f"{per_100g['text']}/100g"
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
