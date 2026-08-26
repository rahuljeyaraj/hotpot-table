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

from hotpot.classifier import ei_client, ei_deploy, ei_store
from hotpot.common import atomicio, config, cursorbus, geometry, health, log, wire
from hotpot.core import (bin_grid, binmap, calibrator, cart, fsm,
                         geometry_store, hover, i18n, loadcell_cal, menu,
                         orders, pricing, scale)
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

# doc section 8.6's `camera.host_for_browser`/`camera.mjpeg_port` defaults —
# what the Live tab (M3 build item 3) embeds in the `<img>` src, since the
# MJPEG server is camera's own HTTP listener, a different port than this
# process's. Constructor params, not read from config.py here directly
# (same reason cal_path/scale_open_port are params, not module reads): a
# Core built by a test must never depend on config/system.json. main()
# below is the one place that actually calls config.load().
CAMERA_HOST = "localhost"
CAMERA_PORT = 8081

# Doc section 12.7's dataset tree. Core never writes into it — the
# classifier does, because core never touches a frame (I3) — but core
# counts what is in it for the tab's per-label session counter.
CAPTURES_DIR = Path(__file__).resolve().parents[3] / "datasets" / "captures"

# Doc section 19.2/19.5's Edge Impulse link + fetched deployment — the
# Capture tab's "Edge Impulse" panel below drives ei_client.py/ei_store.py
# against these. EI_PROJECT_NAME is only used the first time `ei_link`
# creates a brand new Studio project; an already-linked project (today,
# `hotpot-ingredients`, id 1087506 — models/README.md) keeps whatever name
# it was created with, read back from ei_store.py.
EI_PROJECT_PATH = ei_store.DEFAULT_PATH
EI_PROJECT_NAME = "hotpot-ingredients"
MODELS_DIR = Path(__file__).resolve().parents[3] / "models"

# Capture-tab label choices with no catalogue entry — these are never
# billable (no price, never `resolved()` by BinMap) and so must never
# live in data/catalogue.json (Catalogue.load() requires a price and an
# `en` name of every item it holds). They exist only so the classifier
# can be trained to recognise the two non-food states a bin is actually
# in some of the time: nothing there at all, or the tray itself lifted
# out (2026-08-13, replaces the earlier single placeholder "empty").
NON_FOOD_CAPTURE_LABELS = ("empty_tray", "no_tray")

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

# Refusal shown when Tare or Calibrate is asked for outside setting mode.
# **"serving", never "billing".** The system had grown two words for one
# idea — the table banner said NOT BILLING while the mode was called
# SERVING — which makes an operator work out that they mean the same
# thing. One word, and it is the one that is already the mode's name.
# It also names the CONTROL the operator has to reach for: the staff
# view's header is one switch labelled Serving, and the word "setting"
# appears nowhere on it, so "enter setting mode" would send them hunting.
NOT_IN_SETTING_MSG = ("Turn Serving off first — the table is still "
                      "serving.")

# Doc section 8.6's `tracker.emit_hz`. Core's default rather than the
# tracker's, because doc section 4.2 makes core the one place a client's
# configuration lives — the tracker is told this in `welcome` and holds no
# copy of its own.
TRACKER_EMIT_HZ = 60.0

# How long the table may sit with no REAL pointer before the idle-table
# phantom hand (common/phantom.py) takes over the fireball and starts
# wandering the bins — developer's own number, 5s, chosen for a fast demo
# loop over the original 15s guess. Unlike POINTER_STALE_S just below,
# this is measured against `_last_real_pointer_at`, which only a
# genuinely real (non-phantom) pointer ever advances — see
# `_apply_phantom`.
PHANTOM_IDLE_S = 5.0

# How long a cursor may go without a NEW datagram before core treats the
# pointer as gone rather than merely between frames (doc section 21's M5
# build item 4, found while verifying it on the rig — see _apply_cursor's
# docstring). Matches oF's own CursorLink::kCursorHoldSeconds so the table
# and core agree about when a hand is "still here".
POINTER_STALE_S = 0.35

# How long a bin stays "hovered" (fire ring lit, `fire_burning` looping,
# info box showing) after a hand actually leaves it, before `fire_stop`
# fires and the info box clears — developer report, 2026-08-26: picking
# from a bin is not one clean enter-then-leave, it is several — a pinch
# taken out, a hand withdrawn to drop it in the bowl, a hand back in for
# more — and wiring `fire_start`/`fire_stop` straight to the raw per-frame
# hit test (the old behaviour) played the catch/put-out one-shots on every
# one of those in/out crossings, which on a real pick is a burst of
# several within a second or two: "very distracting and noisy". Same wire
# field (`hl`/`fire_active`) drives the info box, so it was blinking on
# the same crossings instead of "staying up for a few secs" the way a
# diner reading it needs.
#
# The fix is hysteresis, the same shape `hover.DEFAULT_GRACE_MS` already
# uses for dwell (`hover.py`'s "leaving resets after a 150ms grace"): a
# hand ENTERING a bin (or a different bin) still wins instantly — no
# reason to delay the exciting edge — but a hand LEAVING one is held for
# this long before it counts, so a same-bin re-entry inside the window is
# invisible on the wire (no new `fire_start`, no info-box flicker) and
# only a hand that is actually gone for a couple of seconds clears it.
# Seconds, not `hover.py`'s 150ms, because that grace only has to survive
# one jittery tracker frame; this one has to survive an actual human hand
# leaving the bin's airspace to drop what it picked.
HOVER_EXIT_GRACE_S = 1.5

# How long core waits for a classifier reply to one of doc section 4.7's
# commands — the capture case is a whole burst (doc section 12.7: 10 frames
# over 5 s) plus the JPEG writes.
CLASSIFIER_REPLY_TIMEOUT_S = 30.0

# A SEPARATE, much shorter timeout for `classify` specifically (doc section
# 19's M7 acceptance: "physically swap two trays -> both labels follow
# within ~2s"). Reusing CLASSIFIER_REPLY_TIMEOUT_S's 30s here would let one
# slow classifier pass blow that budget by 15x before core even notices —
# this bounds a single bad/slow pass, not the steady state, which is why it
# is still well above `1/live_hz` (0.5s at the doc section 8.6 default):
# a pass that takes 3s should be logged and skipped, not treated the same
# as a genuinely hung process.
CLASSIFY_LIVE_TIMEOUT_S = 5.0

# Doc section 8.6's `classifier.live_hz` — core's default rather than the
# classifier's, same reasoning as TRACKER_EMIT_HZ above: doc section 4.2
# makes core the one place a client's effective configuration lives.
CLASSIFIER_LIVE_HZ = 2.0

# **Doc section 18.3's 90s CHECKOUT timeout is DELETED, 2026-08-25.**
#
# Developer, verbatim: "i see the qr code dissaperared when it was left
# idel for sometime, that should not happen, no time out. onc can cancell
# or go back, but not self disappear."
#
# The doc's reasoning ("a contest floor has no patience and no diner will
# remember to press anything") was about a table nobody clears. It is
# wrong about the one person the timer actually fires on: a diner who has
# just got their phone out, opened the camera and is lining up the code.
# That takes longer than most UI waits and it is the ONLY thing anybody is
# doing on this screen — so the timeout's whole population is people who
# are using it correctly, and what it does to them is delete the code
# mid-scan, with the order already written and unpaid.
#
# What replaced it is two buttons a person presses (`hover.checkout_widgets`
# — Back and Cancel, both of which void the order) plus the payment itself
# landing on the WebSocket. A table left genuinely abandoned now sits on
# the QR screen until staff touch it, which is visible and recoverable;
# the old behaviour was invisible and lost the diner's scan.
#
# `_checkout_since` is kept — it still stamps when the screen opened, which
# is useful in the log and on the staff view — but nothing reads it as a
# deadline any more.


def _html_escape(s: str) -> str:
    """Everything on the receipt page is data somebody could have typed —
    an item's display name comes from a JSON file a staff member edits.
    """
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# Doc section 18.2's receipt. One file, no external anything: a phone on a
# contest floor may have no route off the table's own network.
_RECEIPT_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Order {code}</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; padding:24px 18px 48px; font:16px/1.5 system-ui,-apple-system,
         "Segoe UI",Roboto,sans-serif; background:#12100e; color:#f2ede2;
         max-width:520px; margin-inline:auto; }}
  h1 {{ font-size:15px; letter-spacing:.14em; text-transform:uppercase;
       color:#8b8378; margin:0 0 4px; font-weight:600; }}
  .code {{ font-size:44px; font-weight:700; letter-spacing:.04em; margin:0 0 2px; }}
  .chosen {{ color:#b4aa9c; margin:0 0 22px; }}
  table {{ width:100%; border-collapse:collapse; margin-bottom:8px; }}
  td {{ padding:11px 0; border-bottom:1px solid rgba(255,255,255,.09); }}
  td.n {{ text-align:right; white-space:nowrap; font-variant-numeric:tabular-nums;
         color:#b4aa9c; width:1%; padding-left:14px; }}
  .total {{ display:flex; justify-content:space-between; align-items:baseline;
           padding-top:14px; font-size:26px; font-weight:700; }}
  .total span:first-child {{ font-size:16px; color:#8b8378; font-weight:600; }}
  button {{ width:100%; margin-top:26px; padding:18px; border:0; border-radius:14px;
           background:#2e8c3c; color:#fff; font-size:19px; font-weight:700;
           cursor:pointer; }}
  button:disabled {{ opacity:.6; }}
  .paid {{ margin-top:26px; padding:18px; border-radius:14px; text-align:center;
          background:rgba(46,140,60,.18); color:#7fd08c; font-weight:700; }}
  footer {{ margin-top:28px; color:#6b6357; font-size:12.5px; text-align:center; }}
</style></head><body>
<h1>Order</h1>
<p class="code">{code}</p>
<p class="chosen">{chosen}</p>
<table>{rows}</table>
<div class="total"><span>Total</span><span>{sym}{total:.2f}</span></div>
<div id="action">{action}</div>
<footer>Demo only. No money moves.</footer>
<script>
function pay() {{
  var b = document.getElementById('pay');
  b.disabled = true; b.textContent = 'Paying...';
  fetch('/pay/{code}').then(function (r) {{ return r.json(); }})
    .then(function (j) {{
      document.getElementById('action').innerHTML = j.ok
        ? '<div class="paid">Paid. Thank you.</div>'
        : '<div class="paid">Could not find that order.</div>';
    }})
    .catch(function () {{
      b.disabled = false; b.textContent = 'Try again';
    }});
}}
</script>
</body></html>"""


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


def _seed_cart(deadband_g: float = cart.DEFAULT_DEADBAND_G) -> cart.Cart:
    """Every bin starts at MOCK_SEED_GRAMS, then reset_session() (I6's
    re-baseline) sets start_g to match — so removed grams is 0 at boot,
    not a negative clamp from an empty tray. See MOCK_SEED_GRAMS above.

    `deadband_g` is doc section 8.6's `core.deadband_g`, threaded in from
    `main()` rather than left on `Cart`'s own default — the key has been
    in `config/system.json` since M3.2 with nothing reading it, so an
    operator editing it got no effect and no warning. See
    `cart.DEFAULT_DEADBAND_G` for what the number does and what bounds
    how far it can drop.
    """
    c = cart.Cart(deadband_g=deadband_g)
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
        scale_filter_path: Path = scale.SCALE_FILTER_PATH,
        scale_open_port: Optional[Callable[[], Any]] = None,
        camera_host: str = CAMERA_HOST,
        camera_port: int = CAMERA_PORT,
        bin_map_path: Path = binmap.BIN_MAP_PATH,
        menu_path: Path = menu.MENU_PATH,
        orders_path: Path = orders.ORDERS_PATH,
        homography_path: Path = geometry_store.HOMOGRAPHY_PATH,
        camera_grid_path: Path = bin_grid.CAMERA_GRID_PATH,
        projector_grid_path: Path = bin_grid.PROJECTOR_GRID_PATH,
        view_rotation_path: Path = geometry_store.VIEW_ROTATION_PATH,
        mirror_handedness: bool = False,
        emit_hz: float = TRACKER_EMIT_HZ,
        cursor_port: int = cursorbus.CORE_PORT,
        phantom_idle_s: float = PHANTOM_IDLE_S,
        dwell_ms: float = hover.DEFAULT_DWELL_MS,
        deadband_g: float = cart.DEFAULT_DEADBAND_G,
        classify_hz: float = CLASSIFIER_LIVE_HZ,
        classify_enabled: bool = True,
        ei_project_path: Path = EI_PROJECT_PATH,
        models_dir: Path = MODELS_DIR,
        ei_client=ei_client,
        ei_deploy=ei_deploy,
    ) -> None:
        self.registry = health.Registry(on_change=self._on_pip_change)
        self.control = wire.Server(
            control_host, control_port,
            on_message=self._on_message,
            on_connect=self._on_connect,
            on_disconnect=self._on_disconnect,
            welcome_cfg=self._welcome_cfg,
            name="core",
        )
        # Doc section 8.6's `tracker.mirror_handedness`, held by core rather
        # than by the tracker because doc section 4.2 says clients "hold no
        # config of their own beyond how to find core" — and because doc
        # section 11.3 wants it toggled live from the staff view, which has
        # no route to the tracker except through here.
        self.mirror_handedness = mirror_handedness
        self.web = web.Server(web_host, web_port, static_root,
                              on_join=self._join_msgs, on_message=self._on_web_message,
                              on_http=self._on_http)
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
        self.bin_map_path = Path(bin_map_path)
        self.binmap = self._load_binmap()
        # -- M6: the checkout flow's own data ---------------------------
        # Loaded at boot and validated there, like the catalogue: a bad
        # `menu.json` stops core on the bench rather than projecting a
        # blank broth plate at a diner three screens into an order.
        self.menu = menu.Menu.load(menu_path)
        self.orders = orders.OrderStore(orders_path)
        # The picker (`hover.spice_widgets`) drops level 0 ("No Spice") as
        # an orderable choice, so the lowest level it offers — today, Mild
        # — is the fallback a session carries until the diner picks. Doc
        # section 17's "no spice is a genuine choice" guarantee is about
        # the DATA still existing (`menu.Menu.load` still requires level
        # 0), not about it being the default; see `hover.spice_widgets`'s
        # own docstring for the developer's call on why it is excluded.
        #
        # **It is a fallback, NOT a pre-selection, since 2026-08-25.**
        # Developer: "spicy button has the mild as default. it should not
        # be the case." Mild briefly arrived pre-ticked, which meant a
        # diner who never looked at the spice screen shipped Mild without
        # deciding, and the screen opened with one card already locked
        # dark. `_spice_chosen` below now starts False, so nothing is
        # marked until a dwell completes — this value only settles what
        # `_spice_level` holds in the meantime.
        self._default_spice_level: int = min(
            (s.level for s in self.menu.spice_levels if s.level > 0),
            default=0)
        # What the diner has chosen so far this session. Cleared by
        # `_end_session`, which is the one place a session ends, so a
        # previous diner's broth can never ride into the next order.
        self._broth_id: str = ""
        self._spice_level: int = self._default_spice_level
        # **Separate from `_spice_level`, because 0 is a real level.** Doc
        # section 17 makes "no spice" a genuine choice rather than an
        # absence, so the int cannot also mean "not chosen yet" — and Pay
        # is gated on a choice having been made. See `_choose_spice`.
        # Starts False: no card is pre-selected — see
        # `_default_spice_level` above for the developer's call.
        self._spice_chosen: bool = False
        # The previous tick's widget ids+rects, for the "the buttons just
        # changed under a resting hand" guard in `_apply_cursor`. Starts
        # as a sentinel rather than `()` so the very first tick counts as
        # a change — a hand already over the table at boot must not have
        # banked dwell against a layout it never saw appear.
        self._widget_shape_prev: Optional[tuple] = None
        # The order written on the SPICE -> CHECKOUT edge, held while the
        # QR is up so the payment callback and the table can find it by
        # code.
        self._order: Optional[orders.Order] = None
        self._order_qr: list = []
        # `time.monotonic()` when CHECKOUT began. Kept for the log and the
        # staff view; **not a deadline** — the 90s timeout that used to
        # read it is deleted (see CHECKOUT_TIMEOUT_S' block above).
        self._checkout_since: Optional[float] = None
        self.cart = _seed_cart(deadband_g)
        # binmap and refresh_weights are both here so that fsm.py owns all
        # three of doc section 9.1's setting-exit steps — refresh, then
        # re-baseline, then lock — rather than leaving any of them to a
        # caller who might do two and forget the third. Read
        # fsm.exit_setting()'s docstring before changing either.
        # `is_calibrated` is a lambda over the store, not the store's
        # current value: doc section 9.1's UNCALIBRATED is entered at boot
        # and left the moment the geometry lands, and setting-mode exit
        # asks again because the operator may have just calibrated. See
        # fsm.Fsm.__init__.
        #
        # Constructed AFTER self.geometry below in reading order but
        # before it in execution — so the store is built first. Kept
        # together with the other domain objects rather than moved down
        # beside the store, because this is where the FSM lives.
        # Doc section 9.1's "calibrated" needs both a homography AND a
        # camera bin grid — they live in two separate stores now
        # (core/bin_grid.py's module docstring on why), so this predicate
        # is what combines them; neither store knows about the other.
        self.fsm = fsm.Fsm(self.cart, self.binmap,
                           refresh_weights=self._refresh_weights_from_scale,
                           is_calibrated=lambda: (self.geometry.has_homography
                                                  and self.camera_grid.has_grid))

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
        # M3 build item 3: the Live tab's `<img>` src. Static for the
        # process's whole life (doc section 8.6 has no runtime reload for
        # it), so it rides the join seed rather than a broadcast.
        self.camera_host = camera_host
        self.camera_port = camera_port

        self.cal = loadcell_cal.Calibration.load(cal_path)
        # 2026-08-26: the Developer tab's window-size controls persist here
        # (scale.SCALE_FILTER_PATH's own docstring) — a developer tuning
        # knob, not a calibration, so a missing/corrupt file is never
        # fatal, just DEFAULT_MEDIAN_WINDOW/DEFAULT_AVG_WINDOW as if
        # nobody had ever touched it.
        self.scale_filter_path = Path(scale_filter_path)
        filter_window = scale.load_filter_window(self.scale_filter_path)
        self.scale = scale.ScaleReader(
            scale_port, cal=self.cal, open_port=scale_open_port,
            median_window=filter_window.get(
                "median_window", scale.DEFAULT_MEDIAN_WINDOW),
            avg_window=filter_window.get(
                "avg_window", scale.DEFAULT_AVG_WINDOW))
        self.calibrator = calibrator.Calibrator(self.scale, path=cal_path)

        # -- M4: geometry (doc sections 5.3, 8.4, 8.5) -------------------
        # Paths are parameters for the same reason `cal_path` is: these
        # files decide where every bin is, and a test run must never read
        # or write the rig's own. Two stores, not one — GeometryStore owns
        # only `H_cam_to_stage` now; `camera_grid` owns the camera-space
        # bin grid it used to also carry (core/bin_grid.py's docstring).
        self.geometry = geometry_store.GeometryStore(
            homography_path=homography_path,
            view_rotation_path=view_rotation_path)
        self.camera_grid = bin_grid.BinGridStore(camera_grid_path)
        # M4n: the second BinGridStore instantiation core/bin_grid.py's
        # docstring always said was coming. Lines dragged (or nudged —
        # there is no camera image to drag them on) while a human watches
        # the ACTUAL PROJECTED TABLE, never derived from `camera_grid` or
        # `self.geometry` — see bin_grid.py's module docstring on why the
        # two grids must never be derived from each other. This is what
        # `_bin_msg` now reads `rect` from for oF.
        self.projector_grid = bin_grid.BinGridStore(projector_grid_path)

        # Doc section 8.5's staleness check needs oF's live fingerprint,
        # which arrives on the `stat` message (doc section 4.5). None
        # until oF has ever connected — and `keystone_is_stale` treats
        # that as "not stale", never as a fault.
        self._keystone_fingerprint: Optional[str] = None

        # Doc section 6.4: which frame consumers currently believe the
        # camera has stalled, by process name. Written from the `stat`
        # handler, read by the developer panel.
        self.frames_stale: Dict[str, bool] = {}

        # In-flight doc section 4.7 commands to the classifier, by id.
        # Each is an Event plus a slot for the reply — the classifier
        # answers on the control link's read thread and the waiter is a
        # tablet's WebSocket thread.
        self._cmd_lock = threading.Lock()
        self._cmd_seq = 0
        self._cmd_waiters: Dict[int, list] = {}

        # Doc section 19.2/19.5: the Edge Impulse link + upload/download
        # panel. `ei_client` is dependency-injected (default: the real
        # network-touching module) so tests can pass a fake with the same
        # function names and never touch the network, the same DI shape
        # `scale_open_port` gives ScaleReader. `ei_deploy` gets the same
        # treatment for the same reason -- its real implementation shells
        # out to an MSVC build (tools/eim_cpp/rebuild.bat, `EiDeployError`
        # on failure) that a test must not actually invoke.
        self._ei_project_path = Path(ei_project_path)
        self._models_dir = Path(models_dir)
        self._ei_client = ei_client
        self._ei_deploy = ei_deploy
        # "link"/"upload"/"download", or None -- in-memory only (like
        # `_cmd_waiters`), guards a double-click firing two of these at
        # once against the same project. Every _handle_ei_* call is
        # already blocking on the tablet's own WebSocket thread, so this
        # is the one thing stopping a SECOND tablet, or a second click
        # from the same one, from starting an overlapping job.
        self._ei_active: Optional[str] = None

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

        self.emit_hz = emit_hz

        # -- M5 build item 4: hover and dwell (doc section 9.4) ----------
        # The UDP listener the tracker sends to (doc section 4.1's
        # `cursor.core_port`). Bound in __init__ rather than start() so a
        # test can ask which port it got before anything is running, the
        # same as `control_port`/`web_port`.
        #
        # A bind failure is fatal here and deliberately so: unlike a
        # missing XIAO or an absent camera, there is no degraded mode —
        # something else is already holding the port this system's cursors
        # arrive on, and a core that came up "fine" with a dead hand link
        # would look identical to a dead tracker.
        self.cursor = cursorbus.Receiver("127.0.0.1", cursor_port)
        self.dwell = hover.DwellTracker(dwell_ms=dwell_ms)
        # The last cursor frame acted on, kept for the staff view's hand
        # markers (doc section 12.3). Not the raw datagram — `None` once
        # the hands have gone quiet, so a tablet does not draw a marker for
        # a hand that left.
        self._hands: list = []
        # The pointer as of the last REAL new datagram, and when that
        # datagram arrived (`time.monotonic()`). Sticky across ticks with
        # nothing new — see `_apply_cursor`'s docstring for why that is
        # load-bearing, not incidental.
        self._pointer: Optional[cursorbus.Hand] = None
        self._pointer_at: Optional[float] = None
        self._hover_bin: Optional[int] = None
        # When the raw hit test last stopped finding a hand over
        # `_hover_bin`. None while a hand IS over it (or while no bin is
        # hovered at all). Stamped once on leaving, same "measured from the
        # edge, not refreshed per frame" shape as `DwellTracker._left_at` —
        # see HOVER_EXIT_GRACE_S.
        self._hover_left_at: Optional[float] = None
        self._widgets: list = []

        # The idle-table phantom hand (common/phantom.py, `_apply_phantom`).
        # `_phantom_pointer` is this tick's synthetic hand, if the tracker
        # is currently emitting one — kept separate from `_pointer` so it
        # can never reach `fsm.hand_present()` or `DwellTracker` (see
        # `_apply_cursor`'s own comment on the split). `_last_real_pointer_
        # at` starts at boot time, not `None` — a table that has never
        # seen a diner at all is exactly the idle case this feature is
        # for, and starting it at "now" gives a fresh boot the same
        # `phantom_idle_s` warm-up a diner walking away gets, rather than
        # firing the instant `start()` returns.
        self._phantom_pointer: Optional[cursorbus.Hand] = None
        self.phantom_idle_s = phantom_idle_s
        self._last_real_pointer_at: float = time.monotonic()
        self._phantom_active: bool = False
        self._phantom_started_at: Optional[float] = None

        self._state_seq = 0
        self._state_stop = threading.Event()
        self._state_thread: Optional[threading.Thread] = None

        # Doc section 19's M7 build items 2-3: a full-table classify pass
        # at boot, then again at `classify_hz` for as long as the table is
        # in SETTING. Its own thread, never the 60Hz `_state_thread` above
        # or a tablet's WebSocket thread — `_send_classifier_cmd`'s own
        # docstring says its blocking wait "must never be called from the
        # 60Hz state loop", and this is the same argument for why
        # `_handle_capture` already runs on a caller's WebSocket thread
        # rather than in here, just with no tablet request to ride.
        self._classify_hz = classify_hz
        # `classifier.enabled`, config §8.6, default false: the model is
        # not properly tuned yet, so a live pass would write untrustworthy
        # labels into `binmap` instead of leaving bins on `_seed_binmap`'s
        # mock placeholders (`_classify_pass`'s own "nothing to show for
        # it" rule, extended to "not tuned" as well as "not connected").
        # The Capture tab's manual dataset photography (`_handle_capture`)
        # is untouched — it never calls `_classify_pass` and collecting
        # training data is the reason to run with a bad model, not a
        # reason to block it.
        self._classify_enabled = classify_enabled
        self._classify_stop = threading.Event()
        self._classify_thread: Optional[threading.Thread] = None

    # -- lifecycle -----------------------------------------------------

    def start(self) -> None:
        self.registry.start()
        self.control.start()
        self.web.start()
        self.scale.start()
        self._self_beat.start()
        # Doc section 9.1's first-boot branch. Logged either way: on a
        # fresh clone with an empty `state/` this is the line that says
        # why the table is showing a calibration banner instead of plates,
        # and it is the first thing anyone will look for.
        self.fsm.boot_complete()
        if self.fsm.state is fsm.State.UNCALIBRATED:
            _log.warning("core: no saved geometry (%s / %s) — booting "
                         "UNCALIBRATED; the staff view opens on Setup",
                         self.geometry.homography_path.name,
                         self.camera_grid.path.name)
        self._state_thread = threading.Thread(
            target=self._state_loop, name="core-state", daemon=True)
        self._state_thread.start()
        self._classify_thread = threading.Thread(
            target=self._classify_loop, name="core-classify", daemon=True)
        self._classify_thread.start()

    def stop(self) -> None:
        self._state_stop.set()
        self._classify_stop.set()
        if self._state_thread is not None and self._state_thread.is_alive():
            self._state_thread.join(2.0)
        if (self._classify_thread is not None
                and self._classify_thread.is_alive()):
            # A single classify pass can legitimately take a couple of
            # seconds (8 bins, each a backend subprocess call up to
            # CLASSIFY_LIVE_TIMEOUT_S) — 2.0s here, matching
            # `_state_thread`'s own join budget, would routinely time out
            # mid-pass and leave a `classify` command in flight with
            # nothing left to receive its reply.
            self._classify_thread.join(CLASSIFY_LIVE_TIMEOUT_S + 1.0)
        self._self_beat.stop()
        self.scale.stop()
        self.cursor.close()
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

    # -- doc section 4.2's `welcome` payload --------------------------------

    def _welcome_cfg(self, conn: wire.Connection,
                     hello: Dict[str, Any]) -> Dict[str, Any]:
        """What a joining client is told about itself.

        Doc section 4.2: "core replies to hello with the client's current
        configuration, so clients hold no config of their own beyond how to
        find core." This was `{}` for every client from M0 to M4 — a known
        gap in CLAUDE.md — because nothing needed it until the tracker,
        which cannot convert a cursor into stage space without core's
        homography (doc section 5.3: "core pushes it to `tracker` in the
        `welcome` message").

        Keyed on `who` rather than sent to everyone: `of` holds only rig
        calibration it reads off disk itself (I2), and the classifier is
        told what to do per command (doc section 4.7), so neither has a
        config to be given here.
        """
        if conn.who == "tracker":
            return self._tracker_cfg()
        return {}

    def _tracker_cfg(self) -> Dict[str, Any]:
        """Doc section 4.2's example payload, with the fields that exist.

        `homography_cam_to_stage` is **`None`, not identity**, when the
        table is uncalibrated. Identity would be a matrix that works — it
        would put camera pixels straight onto the stage and produce
        confident cursors in the wrong place, on a table that is
        specifically not supposed to be usable yet (doc section 9.1's
        UNCALIBRATED). An absent matrix stops the tracker emitting at all,
        which is the honest behaviour and what `tracker/main.py` is written
        against.
        """
        return {
            "homography_cam_to_stage": self.geometry.h,
            "stage": list(self.geometry.stage_size),
            "camera_size": list(self.geometry.camera_size),
            "emit_hz": self.emit_hz,
            "mirror_handedness": self.mirror_handedness,
            # 2026-08-12: added for `backend_mediapipe.py`'s 180-degree
            # mount compensation — this was a display-only preference
            # until now (see `GeometryStore.set_view_rotation`'s own
            # docstring), but the tracker needs the same physical fact
            # for detection quality, and `geometry.view_rotation_deg` is
            # the one place core already tracks it.
            "view_rotation_deg": self.geometry.view_rotation_deg,
            # False for the whole of SETTING mode: staff are reaching into
            # the frame to swap trays, which `tracker/main.py` would
            # otherwise track as a hand. `_handle_set_mode` re-pushes this
            # the instant the mode actually flips, same as it does for
            # `mirror_handedness`, so the tracker does not wait for its
            # next reconnect to stop (or resume) detecting.
            "mediapipe_enabled": not self._in_setting(),
            # `_apply_phantom`'s idle-table attract loop. Included
            # unconditionally (`None`/`False`/`[]` when nothing is
            # running), same as every other field here, so a tracker that
            # has just reconnected picks up an in-progress attract cycle
            # from `welcome` alone rather than waiting for the next
            # transition edge — `phantom_started_at` being wall time (not
            # this process's own monotonic clock) is what makes that
            # resumption land on the SAME point in the path instead of
            # restarting it.
            "phantom_active": self._phantom_active,
            "phantom_started_at": self._phantom_started_at,
            "phantom_bin_centers": self._phantom_bin_centers(),
        }

    def _push_tracker_cfg(self) -> None:
        """Re-send the tracker's config without waiting for it to reconnect.

        Doc section 11.3 makes `mirror_handedness` "fastest to determine by
        trying it", which only works if the staff view's swap-hands button
        takes effect now rather than at the next restart. Sent as `cfg`,
        the same payload `welcome` carries, so there is one shape for the
        tracker to parse and not two.
        """
        self.control.broadcast({"t": "cfg", "cfg": self._tracker_cfg()},
                               only=["tracker"])

    def _on_message(self, conn: wire.Connection, msg: Dict[str, Any]) -> None:
        if self.registry.handle(conn.who, msg):
            return
        t = msg.get("t")
        if t == "stat":
            # Doc section 4.5's telemetry. The only field core acts on is
            # the keystone fingerprint (doc section 8.5) — see
            # `_keystone_fingerprint`'s comment in __init__.
            fp = msg.get("keystone_fingerprint")
            if isinstance(fp, str) and fp:
                self._keystone_fingerprint = fp
            # Doc section 6.4's second bullet: a consumer that notices the
            # camera has stopped "reports {"t":"stat","frames_stale":true}
            # to core". Recorded rather than acted on — the pips already go
            # red on a dead camera process, and this is the different fact
            # that the camera is alive but not producing.
            stale = msg.get("frames_stale")
            if isinstance(stale, bool):
                self.frames_stale[conn.who] = stale
            return
        if t == "landmarks":
            # Staff-view Developer tab debug telemetry only (RIG_FEEDBACK
            # item 10 — "draw every point MediaPipe identifies"), relayed
            # to every connected tablet verbatim. Not state: core stores
            # nothing from it and nothing else in this process ever reads
            # it — the tracker's own raw detections are upstream of the
            # homography, roles and hysteresis that make up `hands`
            # (`_hands_msg`), so they are not a variant of core's state,
            # they are a different, earlier fact about the same frame.
            self.web.broadcast(msg)
            return
        if t in ("dots", "result", "captured"):
            self._resolve_classifier_reply(msg)
            return
        if t == "capture_progress":
            # Doc section 12.7's counter-and-countdown. This is NOT the
            # command's reply — `_send_classifier_cmd`'s waiter stays open,
            # waiting for the eventual `captured` above — it is a live
            # aside sent once per shot so every tablet can show "shot 3 of
            # 10" and count down to the next one while the burst is still
            # running. Broadcast verbatim, same as `landmarks`.
            self.web.broadcast(msg)
            return
        # An unrecognised `t` from a known process is worth a log line,
        # not a dropped link — wire.py's job is framing, not protocol
        # enforcement.
        _log.debug("core: %s sent unhandled message type %r", conn.who, t)

    # -- talking to the classifier (doc section 4.7) -----------------------

    def _send_classifier_cmd(self, op: str, timeout: float, **fields: Any
                             ) -> Optional[Dict[str, Any]]:
        """Send one doc section 4.7 command and block until its reply, or
        until `timeout`.

        Blocking is deliberate and is safe *here specifically*: every
        caller is on a tablet's own WebSocket thread (web/server.py gives
        each connection one), which is the thread whose screen is showing
        the operator a "working…" step. It must never be called from the
        60Hz state loop.

        Correlated by `id` rather than by "the next reply that arrives":
        a late answer from a cancelled command would otherwise be handed
        to whoever asked next, which for a two-pass calibration means the
        fine pass being solved against the coarse pass's four points.
        """
        with self._cmd_lock:
            self._cmd_seq += 1
            cmd_id = self._cmd_seq
            waiter = [threading.Event(), None]
            self._cmd_waiters[cmd_id] = waiter
        try:
            sent = self.control.broadcast(
                {"t": "cmd", "id": cmd_id, "op": op, **fields},
                only=["classifier"])
            if not sent:
                return {"ok": False,
                        "error": "the classifier is not connected"}
            if not waiter[0].wait(timeout):
                return {"ok": False,
                        "error": "the classifier did not answer in time"}
            return waiter[1]
        finally:
            with self._cmd_lock:
                self._cmd_waiters.pop(cmd_id, None)

    def _resolve_classifier_reply(self, msg: Dict[str, Any]) -> None:
        cmd_id = msg.get("id")
        with self._cmd_lock:
            waiter = self._cmd_waiters.get(cmd_id)
        if waiter is None:
            _log.debug("core: classifier reply for unknown command id %r",
                       cmd_id)
            return
        waiter[1] = msg
        waiter[0].set()

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
        return [self._pips_msg(), self._mode_msg(), self._camera_msg(),
                self._geometry_msg(), self._projector_grid_msg(),
                self._capture_msg(), self._ei_msg()]

    # -- the Live tab's MJPEG source (doc §12.3, §5.4 — M3 build item 3) ----

    def _camera_msg(self) -> Dict[str, Any]:
        """Where the camera process's own MJPEG server lives. Core never
        touches a frame (I3) and never proxies one either — this just tells
        the tablet the URL to open directly, built from doc section 8.6's
        `camera.host_for_browser`/`mjpeg_port`. Sent once, on join: nothing
        in this process can make it change mid-run.
        """
        return {"t": "camera", "host": self.camera_host, "port": self.camera_port}

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
            # Doc section 9.1: "the staff view opens on the calibration
            # wizard" in UNCALIBRATED. It rides `mode` rather than
            # `geometry` because the tablet has to know before it renders
            # anything, and `mode` is already the message it waits for.
            #
            # NOT folded into `mode` as a third value: doc section 4.3
            # fixes that field at serving|setting, oF branches on it, and
            # an uncalibrated table genuinely is in one of those two — it
            # is just not allowed to serve from it.
            uncalibrated = self.fsm.state is fsm.State.UNCALIBRATED
        return {"t": "mode", "mode": mode, "cart_active": active,
                "refused": refused, "uncalibrated": uncalibrated}

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
        key = (msg["mode"], msg["cart_active"], msg["uncalibrated"])
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
        if t == "tare_all":
            self._handle_tare_all()
            return
        if t == "set_mode":
            self._handle_set_mode(msg)
            return
        if t == "cancel_order":
            self._handle_cancel_order()
            return
        if t == "manual_calibrate":
            self._handle_manual_calibrate(msg)
            return
        if t == "set_view_rotation":
            self._handle_set_view_rotation(msg)
            return
        if t == "set_grid":
            self._handle_set_grid(msg)
            return
        if t == "seed_grid":
            self._handle_seed_grid()
            return
        if t == "set_grid_projector":
            self._handle_set_grid_projector(msg)
            return
        if t == "seed_grid_projector":
            self._handle_seed_grid_projector()
            return
        if t == "set_bin_override":
            self._handle_set_bin_override(msg)
            return
        if t == "set_scale_filter":
            self._handle_set_scale_filter(msg)
            return
        if t == "capture":
            self._handle_capture(msg)
            return
        if t == "ei_link":
            self._handle_ei_link(msg)
            return
        if t == "ei_upload":
            self._handle_ei_upload(msg)
            return
        if t == "ei_download":
            self._handle_ei_download(msg)
            return
        if t == "ei_unlink":
            self._handle_ei_unlink(msg)
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
                    self._send_evt({"t": "evt", "kind": "sound", "id": "mode_setting"})
            else:
                # Doc section 9.3: exit is blocked with a confirm while any
                # bin is unresolved — same shape as can_enter_setting()
                # above (a reason string the tablet must show), except the
                # operator can push through it once shown, which
                # can_enter_setting()'s refusal never offers. `confirm`
                # rides the same `set_mode` message rather than a second
                # message type: the tablet's dialog resends the identical
                # request with one field added, the same round trip
                # `_handle_cancel_order`'s own comment describes for its
                # confirm ("the tablet's job... by the time a frame arrives
                # here the operator has already said yes").
                unresolved = self._unresolved_bin_count()
                if unresolved and not msg.get("confirm"):
                    refused = (
                        f"{unresolved} bin{'s' if unresolved != 1 else ''} "
                        "unresolved — items taken from them will not be "
                        "charged. Exit anyway?")
                else:
                    self.fsm.exit_setting()
                    self._send_evt({"t": "evt", "kind": "sound", "id": "mode_serving"})
                    # `exit_setting` sets `binmap.locked` (its own step 3,
                    # doc section 8.2: locked is true in serving mode).
                    # Persisted here rather than inside Fsm, which owns no
                    # file and must not learn to: this is the same
                    # "whoever changed the map writes it" rule the override
                    # handler and the classify pass follow.
                    self._save_binmap()
        if refused is not None:
            # Doc section 15.2's `error`, "refused action... soft double
            # thud, never a harsh buzzer" — the operator asked for a mode
            # change the table would not honour.
            self._send_evt({"t": "evt", "kind": "sound", "id": "error"})
        self._publish_mode(refused=refused)
        # A refused set_mode changed nothing, so nothing to re-push. On a
        # real transition, `_tracker_cfg()`'s `mediapipe_enabled` already
        # reads the new `fsm.state` — this just gets the new value to the
        # tracker now instead of at its next reconnect, same as every other
        # push_tracker_cfg call site in this file.
        if refused is None:
            self._push_tracker_cfg()

    def _unresolved_bin_count(self) -> int:
        """Doc section 9.3. Caller holds `state_lock` (matches every other
        `self.binmap`/`self.fsm` read in this file)."""
        return sum(1 for i in range(binmap.NUM_BINS)
                  if not self.binmap.resolved(i))

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
            self.fsm.cancel()
            self._end_session()
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

    def _in_setting(self) -> bool:
        with self.state_lock:
            return self.fsm.state is fsm.State.SETTING

    def _handle_tare_all(self) -> None:
        """Doc section 12.4's Tare, applied to all eight bins at once.

        Setting the table means eight empty bins, so taring them one at a
        time is eight trips through a wizard whose entire content is "the
        bin is empty" — the step that most wanted a bulk version and the
        only one that can have one. Calibrate cannot: each bin needs its
        own reference mass physically in it.

        Answered with one `cal_result` carrying `op: "tare_all"` and no
        `bin`, rather than eight — the operator tapped one button and is
        owed one sentence. The per-bin numbers reach the cards through the
        next `bins` broadcast, 100ms later, which is where they belong.
        """
        if not self._in_setting():
            self.web.broadcast({
                "t": "cal_result", "bin": None, "op": "tare_all",
                "ok": False, "message": NOT_IN_SETTING_MSG,
            })
            return
        try:
            results = self.calibrator.tare_all()
        except calibrator.OPERATOR_ERRORS as e:
            self.web.broadcast({"t": "cal_result", "bin": None,
                                "op": "tare_all", "ok": False,
                                "message": str(e)})
            return
        # A bin with no counts_per_gram yet cannot be quoted in grams at
        # all (calibrator._result's own reasoning), so the summary counts
        # what is now readable rather than claiming eight zeroes it did
        # not measure.
        read = [r for r in results if r.grams is not None]
        noisy = [r.bin for r in results if r.noisy]
        if read:
            message = (f"All {len(results)} bins set as empty. "
                       f"{len(read)} now read in grams.")
        else:
            message = (f"All {len(results)} bins set as empty. Now place a "
                       "known weight in each and tap Calibrate.")
        if noisy:
            message += (" Noisy: bins "
                        + ", ".join(str(b) for b in noisy) + ".")
        self.web.broadcast({
            "t": "cal_result", "bin": None, "op": "tare_all", "ok": True,
            "message": message,
            "bins": [r.bin for r in results],
        })

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
        #
        # The staff view disables both buttons outside setting mode and
        # says why on the card, so an operator should never reach this.
        # It stays as the authority anyway: the rule about what is safe to
        # do to a bin belongs on the side that owns the cart, not in a
        # tablet's markup, and a stale page must not be able to tare a
        # billing table.
        if not self._in_setting():
            self.web.broadcast({
                "t": "cal_result", "bin": i, "op": t, "ok": False,
                "message": NOT_IN_SETTING_MSG,
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

    # -- the bin map on disk (doc section 8.2) ------------------------------

    def _load_binmap(self) -> binmap.BinMap:
        """`state/bin_map.json` if it exists, `_seed_binmap`'s mock if not.

        **2026-08-24, developer: "the items i manually set in the bin tab
        didnt persist after a reload of the app. it should perssist."**
        `BinMap.save`/`load` have existed since M1.2 and NOTHING has ever
        called either — `Core.__init__` rebuilt the mock seed on every
        boot, so a manual override (and, equally, a classify result, and
        `binmap.locked`) lived exactly as long as the process did. This
        method and `_save_binmap` are that gap closed.

        **An item_id the catalogue no longer has is dropped at load, not
        carried.** Catalogue ids have been renamed wholesale once already
        (2026-08-13's substitute-prop pass) and would be again; a stale id
        would otherwise sit in the file forever, unresolvable, with the
        bin silently unbillable and nothing saying why. Dropped bins fall
        back to nothing rather than to the mock seed — a bin a human once
        set to something real must not quietly become whatever
        `catalogue.ids()` happens to list Nth.

        A missing file is a fresh clone (`BinMap.load`'s own docstring),
        and that is the one case that takes the mock seed, exactly as
        every boot did before this.

        **EVERY saved bin is restored, whatever set it — and this was
        the other way round for one day.** The 2026-08-24 version dropped
        any bin whose `source` was `"classifier"` back to the seed,
        answering that day's report ("when u handed over the app to me
        this time, the food label all were wrong"): the model is not
        tuned (`classifier.enabled` is false), it had written guesses
        like bins 4 and 5 both reading `white_rusk`, and persistence had
        just made them permanent.

        That cure was worse than the disease, and the next day's report
        is what showed it: "the bin item label is not getting persisted
        across restarting the app." Four bins on the rig were
        classifier-sourced, so every restart threw them back to
        `catalogue.ids()[i]` — and with the classifier DISABLED nothing
        could ever answer them again, so they sat on a seed value nobody
        chose, permanently. Two bins reading "Wheat Noodles" in the same
        photo is exactly that: bin 0 was a real manual override and bin 1
        was the seed's own first item showing through.

        The real cure for a wrong guess is the manual override, which
        exists now and persists — a human's answer wins and stays won. A
        stale guess is still visible and still fixable; a seed value that
        silently replaces one every boot is neither.
        """
        seed = _seed_binmap(self.catalogue)
        if not Path(self.bin_map_path).exists():
            return seed
        try:
            saved = binmap.BinMap.load(self.bin_map_path)
        except Exception as e:                        # noqa: BLE001
            # A corrupt file must not stop a table from opening — same
            # tolerance `_read_pidfile` gives its own (CLAUDE.md's FIXED
            # section). The seed is visibly approximately right, which is
            # the safer of the two wrong answers.
            _log.warning("core: %s unreadable (%s) — falling back to the "
                         "mock seed", self.bin_map_path, e)
            return seed
        seed.locked = saved.locked
        for b in saved.bins:
            item_id = b.item_id
            if item_id is not None and self.catalogue.item(item_id) is None:
                _log.warning("core: bin %d had %r, which is not in the "
                             "catalogue any more — cleared", b.i, item_id)
                seed.set_bin(b.i, item_id=None, conf=0.0, source="unset")
                continue
            seed.set_bin(b.i, item_id=item_id, conf=b.conf, source=b.source)
        return seed

    def _save_binmap(self) -> None:
        """Write the bin map, and never let a write failure reach a caller.

        Called from every place that changes what is in a bin — a manual
        override, a classify pass that actually moved something, and
        setting-mode exit's `locked` write. A failed write is logged and
        dropped rather than raised: the in-memory map is still correct and
        the table still bills correctly for the rest of the evening, which
        is a much better outcome than a handler that 500s because a disk
        was full.
        """
        try:
            self.binmap.save(self.bin_map_path)
        except Exception as e:                        # noqa: BLE001
            _log.warning("core: could not write %s (%s) — the bin map is "
                         "correct in memory but will not survive a restart",
                         self.bin_map_path, e)

    def _handle_set_bin_override(self, msg: Dict[str, Any]) -> None:
        """Manual fallback for a bin's item, for when the classifier gets
        it wrong. Doc section 9.3's `resolved()` only ever looks at
        `item_id`/`conf` — it does not care how they got set — so a
        human's answer bills exactly like a confident classifier pass
        would, through the same `Bin.source` field `binmap.py`'s own
        comment already reserved for it ("classifier" | "mock" |
        "manual") but nothing before this handler ever wrote.

        **Setting mode required**, same rule as tare/calibrate/set_grid
        on this tab: this changes what a bin bills as, and that must not
        happen under a live diner.

        `item_id: null` clears the override back to `source: "unset"`
        rather than guessing a replacement — the next classify pass
        (which only runs in SETTING, `_classify_loop`'s own docstring)
        is what re-resolves it, same as a bin nobody has ever touched.
        """
        i = msg.get("bin")
        if not isinstance(i, int) or isinstance(i, bool) or not (0 <= i < binmap.NUM_BINS):
            _log.warning("web: set_bin_override with bad bin %r — ignored", i)
            return
        if not self._in_setting():
            self.web.broadcast({"t": "bin_override_result", "bin": i,
                                "ok": False, "message": NOT_IN_SETTING_MSG})
            return
        item_id = msg.get("item_id") or None
        item = self.catalogue.item(item_id)
        if item_id is not None and item is None:
            self.web.broadcast({
                "t": "bin_override_result", "bin": i, "ok": False,
                "message": f"{item_id!r} is not in the catalogue.",
            })
            return
        with self.state_lock:
            if item_id is None:
                self.binmap.set_bin(i, item_id=None, conf=0.0, source="unset")
            else:
                self.binmap.set_bin(i, item_id=item_id, conf=1.0, source="manual")
            # Written inside the lock, so the file can never be a snapshot
            # of a map that never existed — a classify pass on the other
            # thread must not land between the set and the write.
            self._save_binmap()
        name = item.display_name(self.locale) if item is not None else "Auto"
        self.web.broadcast({"t": "bin_override_result", "bin": i, "ok": True,
                            "message": f"Bin {i} set to {name}."})

    # -- classifier: keep a manual override off the auto-detect path --------
    #
    # `_classify_pass` (below) skips any bin whose current `source` is
    # "manual" rather than overwriting it every pass — otherwise a
    # periodic re-scan (which runs throughout setting mode,
    # `_classify_loop`'s own docstring) would stomp the human's answer
    # back to whatever the model guessed on the very next tick, making
    # this handler pointless the moment the accuracy problem it exists
    # for actually occurs.

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
        # `fsm.weighing`, not "not SETTING": doc section 9.1 makes serving
        # unreachable in UNCALIBRATED too, and a table that does not know
        # which tray is which must not weigh food out of one and charge
        # for it. One predicate, so a state added later cannot start
        # billing by omission.
        #
        # **This was `fsm.serving` until M6 and had to change with it.**
        # The two predicates split when the checkout chain landed: a
        # table on the broth screen is serving a diner but must not be
        # weighing, or the total moves under a diner who has already been
        # shown it, and load-cell drift while the QR is up would change an
        # order already written to the database. See `fsm.weighing` —
        # including why backing out to the cart deliberately un-freezes it.
        if not self.fsm.weighing:
            return
        reading = self.scale.read()
        for i in range(cart.NUM_BINS):
            g = reading.grams[i]
            if g is None:
                continue
            if self._scale_baselined[i]:
                # Pricing/display snap every tick, exactly as before —
                # core/scale.py's own settle detector is deliberately NOT
                # in this line, so shown_g keeps snapping the instant the
                # deadband is crossed (I5's rule, doc section 9.2).
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

    # -- classifier live updates (doc section 19's M7, build items 2-3) -----

    def _classify_loop(self) -> None:
        """Runs for the whole life of the process, on its own thread.

        One pass at boot regardless of mode (build item 2's "startup scan:
        all 8 bins at once, slow is fine" — a table rebooted with trays
        already sitting on it should not come up with all 8 bins
        unresolved when the mock seed's placeholders are the only reason
        it would). After that, a pass every `1/classify_hz` seconds, but
        **only while the table is in SETTING** (build item 5: "No re-scan
        after normal diner picks... re-scanning there is pure risk") —
        `_classify_pass` itself is the same call either way, this loop
        just decides when to make it.

        Doc §8.6's `classifier.enabled` gates the whole loop, boot pass
        included — `_classify_stop.wait` still runs every tick so `stop()`
        keeps joining this thread promptly, it just never calls
        `_classify_pass` while disabled.
        """
        if self._classify_enabled:
            self._classify_pass()
        interval = 1.0 / self._classify_hz if self._classify_hz > 0 else 1.0
        while not self._classify_stop.wait(interval):
            if self._classify_enabled and self._in_setting():
                self._classify_pass()

    def _classify_pass(self) -> None:
        """One full-table `classify` command (doc section 4.7), covering
        all 8 bins at once — see `_classify_loop`'s own docstring for when
        this is called and why always `mode:"once"` rather than the doc's
        literal `mode:"live"` wire example.

        Best-effort, quietly: a table with no saved geometry yet (still
        UNCALIBRATED) or a classifier that is not connected/times out
        skips this pass exactly the way `_apply_scale_to_cart` skips an
        unreadable bin — there is nothing to show for it, not a fault to
        raise. `self.binmap`'s existing mock seed (`_seed_binmap`) is left
        untouched until a pass actually succeeds, so a classifier that
        never starts leaves the table exactly as billable as it always was
        rather than downgrading it to unresolved for no reason.
        """
        with self.state_lock:
            has_geo = self.geometry.has_homography and self.camera_grid.has_grid
            if not has_geo:
                return
            rects = [list(r) + [i]
                    for i, r in enumerate(self.camera_grid.rects())]
            h = self.geometry.h
            stage_size = list(self.geometry.stage_size)

        reply = self._send_classifier_cmd(
            "classify", CLASSIFY_LIVE_TIMEOUT_S,
            rects=rects, mode="once", h=h, stage_size=stage_size)
        if not reply or reply.get("ok") is False:
            _log.debug("core: classify pass skipped: %s",
                      (reply or {}).get("error") or "no reply")
            return

        bins = reply.get("bins") or []
        with self.state_lock:
            changed = False
            for entry in bins:
                i = entry.get("i")
                if not isinstance(i, int) or not (0 <= i < binmap.NUM_BINS):
                    continue
                if self.binmap.bins[i].source == "manual":
                    # A human already answered this bin through the Bins
                    # tab's override control — see the comment above
                    # `_handle_set_bin_override`. Skipped here, not there,
                    # so the skip applies to every pass for as long as the
                    # override stands, not just the one after it was set.
                    continue
                before = self.binmap.bins[i]
                item_id = entry.get("label")
                conf = float(entry.get("conf") or 0.0)
                self.binmap.set_bin(i, item_id=item_id, conf=conf,
                                    source="classifier")
                if (before.item_id != item_id or before.source != "classifier"):
                    changed = True
            # **Only on a real change**, and only on the ITEM changing, not
            # the confidence. This loop runs at `classifier.live_hz`
            # throughout setting mode; writing every pass would rewrite the
            # file a couple of times a second for as long as an operator
            # has the tab open, and a conf that wobbles 0.81 -> 0.83 is not
            # news worth a disk write.
            if changed:
                self._save_binmap()

        # Broadcast the RAW pass to every tablet, straight from the reply —
        # never gated by doc section 9.3's confidence floor the way
        # `_bin_msg`/`_bins_tab_msg`'s `label` fields are. Those two exist
        # to answer "is this bin billable"; this one exists to answer "what
        # did the model actually see", which is a different question and
        # needs a different (ungated) answer — the Developer tab's
        # Classifier card is the one place that question gets asked. Not
        # folded into `_bins_tab_msg`'s own broadcast: that one is 10Hz and
        # reads `self.binmap` at read time regardless of source, so it
        # would report a mock/manual bin's old classify result forever.
        self.web.broadcast({
            "t": "classify",
            "bins": [{"i": e.get("i"), "label": e.get("label"),
                      "conf": round(float(e.get("conf") or 0.0), 3)}
                     for e in bins if isinstance(e.get("i"), int)],
            "ms": reply.get("ms"),
        })

    # -- hover and dwell (doc section 9.4 — M5 build item 4) ---------------

    def _apply_cursor(self, now: float) -> None:
        """Drain the cursor socket and turn the newest frame into hover,
        dwell and (if a dwell completed) an action.

        Called from `_state_msg`, so it runs at the 60Hz state rate under
        `state_lock` — the same instant the message it feeds is built. That
        is deliberate: hover, dwell fraction and the bins they describe are
        one snapshot or they are three, and three would let a widget report
        a dwell of 1.0 in the same message that no longer lists it.

        **Drain-to-latest, never a backlog** (doc section 4). `Receiver`
        enforces it; this method must not be given a loop that reads more
        than one frame, or a 200ms stall would replay the hand through
        history — which is the entire reason cursors are UDP.

        **`self._pointer` is sticky across ticks with no new datagram, and
        that is the fix for a real bug found on the rig, not a style
        choice.** The tracker emits at camera rate — doc section 6.5 puts
        that at ~30Hz — while this runs at the state loop's 60Hz. So on
        roughly half of every tick, `self.cursor.recv_latest()` genuinely
        returns `None` — "nothing NEW arrived" (cursorbus.py's own
        docstring), the ordinary shape of two independent clocks, not a
        fault. The first version of this method built a fresh `pointer`
        local from `frame` alone every call, so every one of those ordinary
        empty ticks fed `DwellTracker.update()` a bare `None` — which
        `DwellTracker` correctly reads as "the pointer left" and starts
        decaying through the 150ms grace. Sent to a real running core and
        watched over real UDP: a hand held on Done for six full seconds
        never moved the dwell fraction off 0.0. The unit tests never caught
        this because a Core built in-process (`CoreCase`) and driven by a
        tight Python send loop in the SAME test process happens to interleave
        closely enough that a fresh frame is very often there each tick —
        an accident of two threads sharing one process, not a property the
        real two-OS-process, two-independent-clocks system has.
        """
        frame = self.cursor.recv_latest()
        if frame is not None:
            # `None` means nothing new arrived, which is NOT the same as an
            # empty table: silence must leave the last hover alone (a
            # dropped datagram is normal), while a frame with no hands must
            # clear it. Only the second is a statement about the table.
            self._hands = list(frame.hands)
            # The pointer itself is likewise only ever changed by a REAL
            # new frame — see the docstring above. `frame.pointer()` can
            # legitimately come back None here (an explicit "ambient hands
            # only, or an empty table" frame), and that DOES clear it —
            # the distinction is "no new information" (frame is None, keep
            # the old pointer) versus "new information saying gone" (frame
            # exists and says no pointer, clear it).
            #
            # **A phantom-flagged pointer (common/phantom.py's idle-table
            # attract loop, emitted by the tracker in place of an empty
            # real result) is routed to `_phantom_pointer`, never to
            # `_pointer`.** `_pointer` is what feeds `fsm.hand_present()`
            # and `DwellTracker` below — a phantom hand must never be able
            # to start a session or complete a dwell, only light a bin's
            # hover highlight (see `_phantom_pointer`'s one use, a few
            # lines down).
            raw_pointer = frame.pointer()
            if raw_pointer is not None and raw_pointer.phantom:
                self._pointer = None
                self._phantom_pointer = raw_pointer
            else:
                self._pointer = raw_pointer
                self._phantom_pointer = None
            self._pointer_at = now

        pointer = self._pointer
        phantom_pointer = self._phantom_pointer
        if self._pointer_at is not None \
                and now - self._pointer_at > POINTER_STALE_S:
            # The stream has gone properly silent for a while — not just
            # "no new datagram this tick" but long enough that the tracker
            # itself is plausibly dead or the camera has gone stale (doc
            # section 6.4). A hover/dwell frozen on a hand that is
            # provably no longer being reported is worse than clearing it.
            # Matches oF's own `CursorLink::kCursorHoldSeconds` so the
            # table and core's own idea of "is a hand still here" agree.
            # Clears BOTH pointers — a fully silent link says nothing
            # about the table either way, real or phantom.
            pointer = None
            self._pointer = None
            phantom_pointer = None
            self._phantom_pointer = None

        # Doc section 9.1's IDLE -> SELECTING edge, which has had no driver
        # since M1 (`fsm.hand_present()` existed with nothing calling it —
        # CLAUDE.md's M2.6 notes say so outright, and `_handle_cancel_order`
        # carries a fallback that becomes unreachable the moment this line
        # lands). Only a REAL POINTER starts a session: a bowl set down on
        # the table must not open an order, and neither may the idle-table
        # phantom hand waking itself back up.
        if pointer is not None:
            self._last_real_pointer_at = now
            self.fsm.hand_present()

        self._apply_phantom(now, pointer)

        # `cart_active` gates whether Cancel/Confirm are dwellable at all
        # (hover.widgets_for's own docstring). `cart.is_active()` reads
        # `shown_g`, i.e. the DEADBANDED number — deliberately, and for the
        # same reason M2.6 chose it for the setting-mode refusal: the raw
        # removed grams move with load-cell noise, so gating on those would
        # arm both buttons on an untouched table and never disarm them.
        self._widgets = self._widgets_for_state()

        # **When the buttons change, disarm whatever is under the hand.**
        #
        # A dwell fires with the hand still resting on the button — that
        # is what dwell means — and the usual reason the buttons change is
        # that a dwell just fired. So at the instant a new set arrives, a
        # hand is sitting on top of it having chosen none of it, and
        # whatever now occupies that spot must not start filling on its
        # own. `DwellTracker`'s ordinary re-arm latch covers this only
        # while the id under the hand is unchanged; this covers the case
        # where it is not.
        #
        # **Keyed on the LAYOUT changing, not on a transition having
        # fired**, because the two are not the same event and the case
        # that proved it has no transition in it at all: a payment landing
        # on the WebSocket swaps the payment screen's Back/Cancel for a
        # single Done, in the same FSM state, from another thread. A hand
        # resting where Done lands would have ended the session 1.2s later
        # — with the token the diner is meant to read still on screen.
        #
        # Ids AND rects, but not `enabled` or `dwell`: those two move
        # constantly (a Next button arming the moment a broth is chosen)
        # and re-arming on them would make a dwell that starts as the
        # button enables impossible to complete.
        shape = self._widget_shape()
        if shape != self._widget_shape_prev:
            self._widget_shape_prev = shape
            self.dwell.suppress_until_exit(self._widgets, pointer)

        # The 90s CHECKOUT timeout that used to be checked here is gone —
        # see CHECKOUT_TIMEOUT_S' own block at the top of this module for
        # the report that removed it and what replaced it. Nothing on this
        # tick ends the payment screen; only a person or a payment does.

        # `hover.bin_under` already answers None for a None hand (its own
        # docstring), so this runs unconditionally rather than duplicating
        # that check here — one place decides what "no pointer" means for
        # a hit test.
        #
        # **The idle-table phantom hand hovers bins too, cosmetically —
        # that IS the feature (it lights the fire ring the same way a
        # real hand does).** `pointer or phantom_pointer`: a real hand
        # always wins the highlight the instant one exists (`pointer` is
        # only non-None here when `_apply_phantom` has already confirmed
        # no phantom can be active at the same time — see that method),
        # and `dwell.update()` below still reads `pointer` alone, never
        # `phantom_pointer` — hover is the only thing a phantom hand may
        # ever drive.
        was = self._hover_bin
        raw_hover_bin = hover.bin_under(self.camera_grid.rects(),
                                        pointer or phantom_pointer)
        if raw_hover_bin is not None:
            # A hand IS over a bin this tick — wins immediately, same as
            # before. This also covers switching directly from one bin to
            # another (raw_hover_bin != was): the new bin still catches
            # fire the instant it is entered, no HOVER_EXIT_GRACE_S delay
            # on the exciting edge, only on leaving.
            self._hover_bin = raw_hover_bin
            self._hover_left_at = None
        elif self._hover_bin is not None:
            # Nothing under the hand right now, but a bin is still "lit"
            # from a moment ago. Hold it rather than clearing on the spot
            # — see HOVER_EXIT_GRACE_S for why (a pick is several quick
            # in/out crossings, not one clean exit).
            if self._hover_left_at is None:
                self._hover_left_at = now
            elif now - self._hover_left_at >= HOVER_EXIT_GRACE_S:
                self._hover_bin = None
                self._hover_left_at = None
        if self._hover_bin != was:
            # 2026-08-26, developer request: the bin "catches fire" the
            # instant a hand enters it and "goes off" once it has been
            # gone for HOVER_EXIT_GRACE_S — replaces the old `hover` tick
            # (doc section 15.2) outright, not alongside it. Sent as
            # one-shot `evt`s, same reasoning that sound always had:
            # `state` repeats at 60Hz and a repeated sound would fire
            # sixty times a second (doc section 4.4). The sustained
            # crackle while a hand STAYS in a bin (grace included) is
            # `fire_active` on `state` instead (`_state_msg`), looped on
            # oF's side (`AudioBus::setFireBurningActive`) — the same
            # "state repeats, a discrete evt does not" split
            # `idle_attract`/`attract` already uses for the idle simmer
            # bed.
            #
            # Switching directly from one bin to another (no frame with
            # no bin in between) plays `fire_start` again for the new
            # bin and no `fire_stop` for the old one — the new bin
            # catching fire is the audible event; the old one's flame
            # just isn't there anymore, same as the visual fire ring's
            # own crossfade only ever targets the one currently-hovered
            # bin.
            if self._hover_bin is not None:
                self._send_evt({"t": "evt", "kind": "sound", "id": "fire_start"})
            else:
                self._send_evt({"t": "evt", "kind": "sound", "id": "fire_stop"})

        # 2026-08-26, developer request: no sound at all during a dwell's
        # progress — the doc section 15.2 `dwell_tick` rising-pitch ladder
        # is gone outright, not muted. `_fire_widget` below is the only
        # sound a dwell produces now, once it actually completes.
        fired = self.dwell.update(self._widgets, pointer, now)
        if fired is not None:
            self._fire_widget(fired)

    def _phantom_bin_centers(self) -> list:
        """The camera grid's own bin centres, stage-space — the same
        coordinate space `hover.bin_under` already hit-tests a hand
        against (`core/bin_grid.py`'s module docstring: this grid is what
        MediaPipe, the classifier's crop, and core's own hit test all
        read). Handed to the tracker so `common.phantom.PhantomHand` can
        visit real bins rather than an arbitrary point — see
        `_apply_phantom`.

        An unset bin (`None`) is skipped, same as `hover.bin_under`'s own
        rule; an empty list (no grid at all) is `PhantomHand`'s own
        fallback to draw from, not this method's problem to solve.
        """
        centers = []
        for rect in self.camera_grid.rects():
            if rect is None:
                continue
            rx, ry, rw, rh = rect
            centers.append([rx + rw / 2.0, ry + rh / 2.0])
        return centers

    def _apply_phantom(self, now: float,
                       pointer: Optional[cursorbus.Hand]) -> None:
        """Turn the table over to the idle-table phantom hand once nobody
        real has touched it for `self.phantom_idle_s`, and take it back
        the instant a real hand — or a staff-driven transition out of
        IDLE — says otherwise.

        **Only decides WHEN and WHERE (the bin set); never generates a
        position itself.** `common/phantom.py`'s own module docstring has
        the full reasoning for why the tracker is the one process that
        actually emits it: one sender on the cursor socket, so "a real
        hand always wins" is a property of `TrackerProcess.tick()`'s own
        `if hands: ... elif self._phantom: ...`, not a race this method
        has to referee.

        Pushed as `cfg` **only on the transition edge**, the same
        reasoning `_push_tracker_cfg`'s other live-push call sites already
        give: this runs inside `_apply_cursor`, i.e. every state tick at
        60Hz, and re-broadcasting an unchanged value that often would be
        60Hz of control-link traffic for a fact that changes roughly once
        an idle minute. A reconnecting tracker still gets the current
        value regardless — `_tracker_cfg()` includes it unconditionally,
        the same as every other field `welcome` seeds a fresh client with.

        Caller holds `state_lock` (via `_apply_cursor`).
        """
        idle_for = now - self._last_real_pointer_at

        # **An abandoned, EMPTY SELECTING session auto-cancels back to
        # IDLE after the same idle window**, so the attract loop is
        # reachable at all on a table where `hand_present()` can fire
        # from a hand that never goes on to pick anything — a diner who
        # tapped once and walked off, or (found live, 2026-08-26) a
        # webcam that briefly saw something hand-shaped with nobody
        # actually there. Gated on `cart.is_active()` being False for the
        # same reason the old `CHECKOUT_TIMEOUT_S` (deleted above) was
        # wrong to have one: a cart with real food already taken from it
        # is a diner's order, and this must never touch that — only a
        # session with nothing in it, which costs nobody anything to
        # cancel.
        if (self.fsm.state is fsm.State.SELECTING
                and pointer is None
                and not self.cart.is_active()
                and idle_for >= self.phantom_idle_s):
            self.fsm.cancel()

        want_phantom = (self.fsm.state is fsm.State.IDLE
                        and pointer is None
                        and idle_for >= self.phantom_idle_s
                        and self.camera_grid.has_grid)
        if want_phantom == self._phantom_active:
            return
        self._phantom_active = want_phantom
        # Wall time (`time.time()`), not `time.monotonic()` — this crosses
        # a process boundary (`_push_tracker_cfg` below), and cursorbus's
        # own frame `ts` already assumes wall time is the shared clock the
        # two processes agree on. Doubles as `PhantomHand`'s seed
        # (`set_phantom`'s own docstring on the tracker side).
        self._phantom_started_at = time.time() if want_phantom else None
        self._push_tracker_cfg()

    def _widget_shape(self) -> tuple:
        """What `self._widgets` looks like, for change detection only.

        Ids and rects — the two things that decide what a hand at a given
        point is pointing at. Deliberately NOT `enabled` or `dwell`: both
        move constantly (Next arms the instant a broth is chosen, dwell
        moves every frame), and treating either as a layout change would
        reset a dwell every tick.

        Caller holds `state_lock`.
        """
        return tuple((w.id, w.rect) for w in self._widgets)

    def _widgets_for_state(self) -> list:
        """Which buttons the table is offering right now.

        One place, so a state cannot end up with no way out of it — every
        branch below returns at least one dwellable widget, and the
        fallthrough returns the cart pair rather than an empty list.

        Caller holds `state_lock`.
        """
        st = self.fsm.state
        if st is fsm.State.BROTH:
            # The current choice is passed IN rather than looked up by the
            # layout, so `hover` keeps knowing nothing about the session —
            # it lays rects out and marks whichever id it was told is the
            # chosen one. That is what lets `test_hover` run with no core.
            return hover.broth_widgets(self.menu.broths,
                                       selected_id=self._broth_id)
        if st is fsm.State.SPICE:
            return hover.spice_widgets(
                self.menu.spice_levels,
                selected_level=(self._spice_level
                                if self._spice_chosen else None))
        if st is fsm.State.CHECKOUT:
            return hover.checkout_widgets(
                paid=self._order is not None and self._order.paid)
        # `cart_active` gates whether Cancel/Confirm are dwellable at all
        # (hover.widgets_for's own docstring). `cart.is_active()` reads
        # `shown_g`, i.e. the DEADBANDED number — deliberately, and for
        # the same reason M2.6 chose it for the setting-mode refusal: the
        # raw removed grams move with load-cell noise, so gating on those
        # would arm both buttons on an untouched table and never disarm
        # them.
        return hover.widgets_for(
            selecting=st is fsm.State.SELECTING,
            locales_available=len(self.locales.available()),
            cart_active=self.cart.is_active())

    def _send_evt(self, msg: Dict[str, Any]) -> None:
        """Doc section 4.4's one-shot events. Fire-and-forget: "if oF misses
        one because it just restarted, nothing breaks."
        """
        self.control.broadcast(msg, only=["of"])

    def _end_session(self) -> None:
        """The order is over — re-baseline every bin onto what it weighs
        right now. Doc section 9.1's I6: "re-baseline, never re-tare."

        **Unconditional, deliberately — no `cart.is_active()` guard.**
        Developer, 2026-08-24: "a cancel order or confirmed order should
        set the current weight as the weight of the item, right now a
        cance will clear the cart but if any item is touched all the old
        items get popped up." The guard is exactly how that happened. A
        pick under the display deadband leaves `shown_g` at 0, so
        `is_active()` is False (cart.py's own docstring on why it reads
        the deadbanded number and not the raw one), so the guarded call
        did nothing at all — and `start_g` kept the old baseline. The next
        pick from that bin added to the discarded one and crossed the
        deadband together, so a cancelled order's grams reappeared inside
        the following diner's. Ending a session has to end it for every
        bin, including the ones with nothing visible in them.

        Caller holds `state_lock`.
        """
        self.cart.reset_session()
        # M6: the checkout's own scratch state dies with the session, so
        # a previous diner's broth, spice or order code can never ride
        # into the next one. `_order` in particular is what the QR screen
        # and the payment callback read — leaving it set would let a
        # payment landing minutes later reset a table a new diner is
        # already using.
        self._broth_id = ""
        # Nothing chosen, same as a fresh boot — see
        # `_default_spice_level`'s own comment in `__init__`.
        self._spice_level = self._default_spice_level
        self._spice_chosen = False
        self._order = None
        self._order_qr = []
        self._checkout_since = None

    def _fire_widget(self, widget_id: str) -> None:
        """A dwell completed. One dispatch table, so a widget that fires
        and does nothing is visible as a missing entry rather than as
        silence.

        The guard against a hand left resting on whatever replaces this
        button is NOT here — it is in `_apply_cursor`, keyed on the widget
        layout changing rather than on this method having run, because a
        layout can change without a dwell (the payment landing is the
        case that proved it). See `_widget_shape`.

        Caller holds `state_lock`.
        """
        # 2026-08-26, developer request: the bottom nav buttons (Cancel/
        # Back/Confirm/Language/Done) get no dwell-progress sound and no
        # generic chime — one "single tap" the instant the dwell fires,
        # nothing during the dwell itself. Broth/spice selection is NOT
        # in this set — those fire their own "double tap" from
        # `_choose_broth`/`_choose_spice` below, only when the choice
        # actually changes.
        if widget_id in (hover.CANCEL, hover.BACK, hover.CONFIRM,
                         hover.LANGUAGE, hover.DONE):
            self._send_evt({"t": "evt", "kind": "sound", "id": "single_tap"})
        if widget_id == hover.CANCEL:
            # **Void first, while `_order` still exists.** `_end_session`
            # clears it, so an order written on the payment screen and
            # cancelled from it would otherwise stay `new` in the queue
            # for a kitchen to cook and nobody to pay for.
            self._void_pending_order("cancelled by the diner")
            # `fsm.cancel()` calls `reset_session()` itself on the SELECTING
            # -> IDLE edge; the call below covers every other state, which
            # today is all of them (nothing drives IDLE -> SELECTING until
            # M5's tracker does). Calling it twice is harmless — it is
            # idempotent by construction (`start_g[i] = live_g[i]`).
            self.fsm.cancel()
            self._end_session()
            return
        if widget_id == hover.BACK:
            self._fire_back()
            return
        if widget_id == hover.CONFIRM:
            self._fire_confirm()
            return
        if widget_id == hover.LANGUAGE:
            self._cycle_locale()
            return
        if widget_id == hover.DONE:
            # `DONE` is doc section 9.1's own name for the SELECTING ->
            # BROTH edge and is kept as a synonym of Confirm-in-SELECTING
            # so the voice keyword ("say done", doc section 17.2) has
            # something to fire when M9 lands. No widget carries this id
            # today — `widgets_for` returns Cancel/Confirm.
            self._begin_checkout()
            return
        broth_id = hover.parse_broth_id(widget_id)
        if broth_id is not None:
            self._choose_broth(broth_id)
            return
        level = hover.parse_spice_level(widget_id)
        if level is not None:
            self._choose_spice(level)
            return
        _log.warning("core: widget %r fired with nothing bound to it",
                     widget_id)

    def _fire_confirm(self) -> None:
        """The primary button. It means something different on each
        screen, and this is the one place that decides which.

        It wears a different LABEL on each too — "Next" on the cart and
        broth screens, "Pay" on the spice screen (`hover._nav_row`) — but
        one id, so the dispatch below is on the FSM state, which is the
        thing that actually determines what should happen. Branching on a
        label would mean the wire's wording could change what the table
        does.

            SELECTING  doc section 9.1's "done" — the cart is finished and
                       the diner goes to BROTH.
            BROTH      the chosen broth is accepted and the diner goes to
                       SPICE. Refuses with nothing chosen, though the
                       button is disabled in that case anyway (belt and
                       braces: `enabled` is drawn from a snapshot taken
                       one tick earlier).
            SPICE      the commit. Writes the order and opens the payment
                       screen.
            CHECKOUT   only once the order is PAID, where the button says
                       Done and ends the session. An unpaid payment
                       screen has no primary button at all — see
                       `hover.checkout_widgets` for why a Done there would
                       be a way to clear the table without paying, and
                       why the refusal is repeated here rather than left
                       to the button's absence.

        Caller holds `state_lock`.
        """
        st = self.fsm.state
        if st is fsm.State.CHECKOUT:
            if self._order is None or not self._order.paid:
                _log.info("core: Done on an unpaid checkout — ignored")
                return
            self._finish_checkout()
            return
        if st is fsm.State.BROTH:
            if not self._broth_id:
                _log.info("core: Next on BROTH with no broth chosen — "
                          "staying put")
                return
            if self.fsm.broth_chosen():
                _log.info("core: broth %s accepted, BROTH -> SPICE",
                          self._broth_id)
            return
        if st is fsm.State.SPICE:
            if not self._spice_chosen:
                _log.info("core: Pay on SPICE with no level chosen — "
                          "staying put")
                return
            self._write_order()
            return
        self._begin_checkout()

    def _fire_back(self) -> None:
        """The Back button — one screen backward, never out of the order.

        Offered on every screen after the cart (`hover._nav_row`), because
        before it existed the only way to fix a wrong broth was Cancel,
        which threw away the whole cart. That is the difference the
        developer asked for: "so the user can really navigate to and fro
        without any issues."

        **Backing out of CHECKOUT voids the order that was already
        written.** The row exists in SQLite with a code by then, and a
        diner who is now going to choose a different spice level must not
        leave a payable, cookable order behind them with the old one on
        it. `fsm.back()` cannot do this itself — it owns no database (see
        `fsm._BACK_EDGES`) — so it happens here, before the transition, so
        `_order` is still in hand.

        Caller holds `state_lock`.
        """
        if self.fsm.state is fsm.State.CHECKOUT:
            self._void_pending_order("the diner went back to change it")
        if not self.fsm.back():
            _log.info("core: Back fired in %s, which has nothing behind it",
                      self.fsm.state.value)
            return
        _log.info("core: back to %s", self.fsm.state.value)

    def _void_pending_order(self, why: str) -> None:
        """Mark the order currently on the table `void` and forget it.

        Doc section 9.7's status enum is `new | cooking | served | void`,
        and this is the one thing that writes the last of those. A no-op
        when there is no order — which is every screen before the payment
        one, so callers do not have to check first.

        **Never touches a PAID order.** A payment that landed while the
        diner's hand was travelling toward Back is money that changed
        hands; voiding that row would hide a real transaction from the
        kitchen and from the staff view's queue. The session ends normally
        instead, through the same path `_on_order_paid` already takes.

        Caller holds `state_lock`.
        """
        order = self._order
        if order is None:
            return
        if order.paid:
            _log.info("core: %s is paid — not voiding it (%s)",
                      order.code, why)
            return
        try:
            self.orders.set_status(order.code, "void")
        except Exception as e:                       # noqa: BLE001
            # A void that fails leaves a `new` row in the queue, which a
            # human can see and fix. Raising here would strand the diner
            # on a screen whose Back button did nothing.
            _log.exception("core: could not void %s: %s", order.code, e)
        else:
            _log.info("core: %s voided — %s", order.code, why)
        self._order = None
        self._order_qr = []
        self._checkout_since = None
        self.web.broadcast({"t": "orders", "orders": self.orders.as_dicts()})

    def _begin_checkout(self) -> None:
        """SELECTING -> BROTH, doc section 9.1's `dwell "done"` edge —
        the cart screen's Next button.

        **`cart.finalize()` happens HERE, at the moment the diner leaves
        the cart.** It snaps every bin's shown grams onto the true removed
        grams, dropping the display deadband (doc section 9.2's fix for
        open debt #5) — so the numbers the diner last read on the cart are
        the numbers the order is written from. Doing it at the commit
        instead would mean the cart showed deadbanded grams and the
        receipt showed different ones, which is precisely the discrepancy
        the deadband exists to avoid.

        `fsm.done()` refuses an empty cart (its own docstring), so a hand
        resting on Next at an untouched table cannot start a checkout.

        Caller holds `state_lock`.
        """
        if not self.fsm.done():
            _log.info("core: Next fired but the cart is empty — staying put")
            return
        self.cart.finalize()
        # **The diner's broth and spice are NOT cleared here**, and that
        # changed with the Back button (2026-08-25). This method runs on
        # every SELECTING -> BROTH crossing, including the second one after
        # somebody backed out to the cart to add an item — and wiping their
        # choices for that would punish exactly the correction the Back
        # button exists to make possible. A session's choices are cleared
        # where a session ends: `_end_session`.
        _log.info("core: SELECTING -> BROTH, cart finalised at %.2f",
                  pricing.total(self.cart, self.binmap, self.catalogue))

    def _choose_broth(self, broth_id: str) -> None:
        """Lock a broth in. **Does not advance the screen.**

        Developer, 2026-08-25: "each option button doesnt select and move
        to the next page... only when the button progress completes the
        previous button gets unselected and this button get selected."

        A completed dwell on a plate used to BE the BROTH -> SPICE
        transition, which made the choice invisible: the screen was gone
        before the diner could see what they had picked, and there was no
        way to change it short of Cancel. Now it writes one field, the
        next tick marks that widget `selected` on the wire, and the diner
        moves on when they press Next.

        Re-choosing is just this method again with a different id — the
        old choice is overwritten, so "the previous button gets
        unselected" is a consequence of there being one field rather than
        a rule anything has to enforce.

        Caller holds `state_lock`.
        """
        if self.fsm.state is not fsm.State.BROTH:
            return
        if self.menu.broth(broth_id) is None:
            _log.warning("core: unknown broth %r ignored", broth_id)
            return
        if broth_id == self._broth_id:
            return
        self._broth_id = broth_id
        # 2026-08-26, developer request: one "single tap" cue everywhere
        # (double_tap retired the same day) — plays exactly once, when
        # the choice is actually made, never during the dwell that led
        # up to it.
        self._send_evt({"t": "evt", "kind": "sound", "id": "single_tap"})
        _log.info("core: broth %s selected", broth_id)

    def _choose_spice(self, level: int) -> None:
        """Lock a spice level in. **Does not advance the screen** — same
        change and same reasoning as `_choose_broth`.

        **`_spice_chosen` is a separate flag and has to be.** Level 0 is a
        genuine choice (doc section 17: "many shops offer a level 0 with
        no numbing at all, and this is a normal, expected choice"), so
        `_spice_level == 0` cannot mean "nothing picked yet" — and Pay is
        gated on something having been picked. One int cannot carry both
        answers.

        Caller holds `state_lock`.
        """
        if self.fsm.state is not fsm.State.SPICE:
            return
        if self.menu.spice(level) is None:
            _log.warning("core: unknown spice level %r ignored", level)
            return
        if self._spice_chosen and level == self._spice_level:
            return
        self._spice_level = level
        self._spice_chosen = True
        # Same "single tap" cue as broth — see `_choose_broth`'s own
        # comment.
        self._send_evt({"t": "evt", "kind": "sound", "id": "single_tap"})
        _log.info("core: spice level %d selected", level)

    def _write_order(self) -> None:
        """SPICE -> CHECKOUT: doc section 18.1's "order written to SQLite,
        a short code assigned, a QR code projected".

        Was the RECAP -> CHECKOUT edge until 2026-08-25; RECAP is deleted
        (see `fsm.py`'s module docstring) and this is unchanged apart from
        which state it leaves.

        **The cart is NOT re-baselined here.** That happens at
        `_finish_checkout`, when the diner is actually done — doc section
        9.1 lists checkout *completion* as the reset_session() caller, not
        checkout entry. Resetting now would empty the cart out from under
        the total the payment screen still shows beside the code.

        A failed write leaves the FSM on the spice screen rather than
        advancing to a payment screen with no order behind it: the diner
        sees Pay not take, which is honest, instead of a code that is not
        in the database when they try to pay with it.

        Caller holds `state_lock`.
        """
        lines = self._order_lines()
        if not lines:
            _log.warning("core: Confirm on an empty cart — no order written")
            return
        total = round(sum(l.line_total for l in lines), 2)
        try:
            # The URL is built BEFORE the row exists, which needs the code,
            # so the row is written first with an empty `qr_url` and the
            # URL is stamped on afterwards. One column update beats
            # allocating a code outside the transaction that guarantees it
            # is unique (see `OrderStore.create`).
            order = self.orders.create(
                lines=lines, total=total, broth=self._broth_id,
                spice=self._spice_level, locale=self.locale,
                currency=self.locales.currency_symbol(self.locale))
            url = self.receipt_url(order.code)
            self.orders.set_qr_url(order.code, url)
            order.qr_url = url
        except Exception as e:                      # noqa: BLE001
            _log.exception("core: could not write the order — staying on "
                           "the spice screen so the diner sees Pay not "
                           "take: %s", e)
            return
        if not self.fsm.confirm():
            return
        self._order = order
        self._order_qr = orders.qr_matrix(url)
        self._checkout_since = time.monotonic()
        self._send_evt({"t": "evt", "kind": "sound", "id": "order_code",
                        "code": order.code})
        self.web.broadcast({"t": "orders", "orders": self.orders.as_dicts()})
        _log.info("core: order %s written, total %.2f, QR %s",
                  order.code, total, url)

    def _order_lines(self) -> list:
        """The cart as order lines, at the numbers the diner just read.

        `name` is resolved here and stored on the row — see
        `OrderStore`'s own docstring for why a receipt must not be
        re-labelled by a later catalogue edit.

        Caller holds `state_lock`.
        """
        out = []
        for i in range(cart.NUM_BINS):
            if not self.binmap.resolved(i):
                continue
            item_id = self.binmap.bins[i].item_id
            item = self.catalogue.item(item_id)
            if item is None:
                continue
            # **`removed_grams`, not `shown_g`** — I5: the deadband never
            # enters price maths, and `pricing.total()` (what bills) is
            # built on this same number. They agree anyway by the time
            # this runs, because `_begin_checkout` called `finalize()`,
            # but reading the billed number directly means they cannot
            # come apart if that ordering is ever changed.
            grams = self.cart.removed_grams(i)
            # **Not `grams <= 0` — `is_billable`.** A true zero was never
            # the thing appearing on receipts with a 1-cent tag; a
            # sub-gram pick was, printed as "0 g" and priced anyway. Same
            # predicate `pricing._sum_resolved` now uses, so the lines
            # here and the total they sit under drop exactly the same
            # bins (2026-08-25).
            if not pricing.is_billable(grams):
                continue
            out.append(orders.OrderLine(
                bin=i, item_id=item_id, name=item.display_name(self.locale),
                grams=round(grams, 1), price_per_100g=item.price_per_100g,
                line_total=round(pricing.bin_price(grams, item.price_per_100g), 2)))
        return out

    def _finish_checkout(self) -> None:
        """CHECKOUT -> IDLE. Doc section 9.1's "[re-baseline, clear cart]".

        Reached three ways, all of them ending here so the cleanup cannot
        differ between them: the 90s timeout (doc section 18.3), the
        diner dwelling the one button on the screen, and the receipt page
        being paid.

        Caller holds `state_lock`.
        """
        self.fsm.checkout_complete()
        self._end_session()

    # -- doc section 18.2: the payment mock -------------------------------

    def _on_http(self, path: str):
        """Two routes, both doc section 18.2's: the receipt page and the
        Pay button behind it.

        Runs on the WEB SERVER's thread, not core's state thread, so
        everything it touches is either the order store (its own
        connection per call — see `OrderStore`) or taken under
        `state_lock`.

        **Pay is a GET.** It mutates, which a GET should not, and that is
        a deliberate trade for a demo: `websockets`' `process_request`
        hook is handed the request before any body has been read, so a
        POST body is not available here without running a second HTTP
        server on another port. The cost of the shortcut is bounded — the
        operation is idempotent (`mark_paid` does not move `paid_at` on a
        second call) and the worst a prefetching browser can do is mark
        an order paid that a diner was about to pay anyway.
        """
        if path.startswith("/r/"):
            code = path[3:].strip("/").upper()
            order = self.orders.get(code)
            if order is None:
                return (404, "text/html; charset=utf-8",
                        b"<!doctype html><meta charset=utf-8>"
                        b"<p style='font:16px system-ui;padding:2rem'>"
                        b"No such order.</p>")
            return (200, "text/html; charset=utf-8",
                    self._receipt_html(order).encode("utf-8"))
        if path.startswith("/pay/"):
            code = path[5:].strip("/").upper()
            order = self.orders.mark_paid(code)
            if order is None:
                return (404, "application/json", b'{"ok":false}')
            self._on_order_paid(order)
            return (200, "application/json",
                    b'{"ok":true,"paid":true}')
        return None

    def _on_order_paid(self, order: "orders.Order") -> None:
        """Doc section 18.2: "The table sees the payment land (via the
        WebSocket) and plays `order_done`."

        Runs on the web server's thread, so it takes `state_lock` before
        touching the FSM — this is the one place an outside event drives
        a state change.

        **The session does NOT end here any more (2026-08-25), and it
        cannot.** It used to: payment landed and `_finish_checkout()` put
        the table straight back to IDLE. That was survivable while the
        order code was shown throughout — but the developer's instruction
        is "the token number should be given only after sucessfull
        payment", so the token now appears *at* this moment. Resetting in
        the same breath would flash it for a frame or two and clear the
        table before the diner had read a single character of the one
        thing they are meant to carry to the counter.

        So the screen stays up, showing the token, until a person presses
        Done (`hover.checkout_widgets(paid=True)`). There is deliberately
        no timer behind it — same instruction, same reason as the QR's:
        "no time out. onc can cancell or go back, but not self
        disappear." **The cost, stated plainly: a diner who pays and
        walks away leaves the table on its thank-you screen until
        somebody presses Done.** That is visible and one dwell from
        clear, where the old behaviour was invisible and lost the token.
        If a paid screen should time out after all, that is one branch in
        `_apply_cursor` — but it is a product call, not a fix.

        **Only touches the table if this is the order currently on it.**
        A judge scanning a receipt from ten minutes ago must not disturb
        a table a different diner is halfway through.
        """
        self.web.broadcast({"t": "orders", "orders": self.orders.as_dicts()})
        with self.state_lock:
            current = self._order is not None and self._order.code == order.code
            if current:
                # So the payment screen reads `paid` and core starts
                # sending the token — see `_overlay_msg`.
                self._order = order
            if not current or self.fsm.state is not fsm.State.CHECKOUT:
                _log.info("core: %s paid (not the order on the table now)",
                          order.code)
                return
            self._send_evt({"t": "evt", "kind": "sound", "id": "order_done"})
            _log.info("core: %s paid — token %s is on the table, waiting for "
                      "Done", order.code, order.code)

    def _receipt_html(self, order: "orders.Order") -> str:
        """Doc section 18.2's "mobile-friendly receipt page — itemised, in
        the diner's chosen locale, with a Pay button".

        Self-contained: no external CSS, no fonts, no scripts from
        anywhere. A diner's phone on a contest floor may have no working
        internet at all — it only has to reach the table's own network to
        have loaded this — so anything fetched from outside would leave
        them with an unstyled page and a dead button.

        **A real UPI deep link is deliberately NOT here.** Doc section
        18.2: "A QR that opens a real payment app asking a judge for real
        money is not a demo, it is an incident." The Pay button posts to
        this server and nothing else.
        """
        sym = order.currency or self.locales.currency_symbol(order.locale)
        broth = self.menu.broth(order.broth)
        spice = self.menu.spice(order.spice)
        rows = "".join(
            "<tr><td>{name}</td><td class=n>{grams:.0f} g</td>"
            "<td class=n>{sym}{total:.2f}</td></tr>".format(
                name=_html_escape(l.name), grams=l.grams, sym=_html_escape(sym),
                total=l.line_total)
            for l in order.lines)
        chosen = []
        if broth is not None:
            chosen.append(_html_escape(broth.display_name(order.locale)))
        if spice is not None:
            chosen.append(_html_escape(spice.display_name(order.locale)))
        paid_banner = (
            "<div class=paid>Paid. Thank you.</div>" if order.paid else
            "<button id=pay onclick=\"pay()\">Pay {sym}{total:.2f}</button>"
            .format(sym=_html_escape(sym), total=order.total))
        return _RECEIPT_TEMPLATE.format(
            code=_html_escape(order.code),
            rows=rows,
            chosen=" &middot; ".join(chosen) or "&nbsp;",
            sym=_html_escape(sym),
            total=order.total,
            action=paid_banner,
        )

    def receipt_url(self, code: str) -> str:
        """Doc section 18.2: "The QR encodes a URL served by core:
        `http://<host>:8090/r/<order_code>`."

        Built from `camera_host` — the one hostname in this config that is
        already known to be reachable from a phone on the same network,
        because the Live tab's `<img>` has been loading from it since M3.3
        (doc section 8.6's `camera.host_for_browser`). `localhost` would
        produce a QR that only works on the machine nobody is holding.
        """
        return "http://%s:%d/r/%s" % (self.camera_host, self.web.port, code)

    def _cycle_locale(self) -> None:
        """Doc section 17.1: "locale switches via: a projected button
        (dwell)". Cycles rather than toggles so a third locale needs no
        change here.

        Unreachable while only one locale is loaded — `widgets_for` marks
        the button disabled and `DwellTracker` will not accumulate on a
        disabled widget — but written to be correct anyway, because the day
        `zh.json` lands the only thing that should have to change is that
        file.
        """
        names = self.locales.available()
        if len(names) < 2:
            return
        nxt = names[(names.index(self.locale) + 1) % len(names)]
        self.locale = nxt
        _log.info("core: locale switched to %s by dwell", nxt)

    def _widget_msgs(self) -> list:
        """Doc section 4.3's `widgets`, with labels **already resolved**
        (I2: "oF does no lookup") and `dwell` as a 0..1 fraction (doc
        section 9.4: "oF does not time anything").
        """
        out = []
        for w in self._widgets:
            # A menu option carries its own already-localised name from
            # `data/menu.json`; the fixed chrome (Cancel, Confirm) goes
            # through the locale table. See `hover.Widget.label`.
            label = w.label or self.locales.translate(w.label_key, self.locale)
            item = {
                "id": w.id,
                "kind": w.kind,
                "rect": [round(v, 1) for v in w.rect],
                "label": label,
                "dwell": round(self.dwell.fraction(w.id), 3),
                "enabled": w.enabled,
                "style": w.style,
                # Which one the pointer is actually inside, so oF knows
                # whose info box to show. Taken from `DwellTracker`
                # rather than re-hit-testing here: that class already
                # decides what "inside" means (it skips disabled
                # widgets), and two answers to that question would
                # eventually disagree.
                "hover": w.id == self.dwell.active_id,
                # 2026-08-25: which option is LOCKED IN, as opposed to
                # which one a hand happens to be over. The two are
                # independent now — a diner can hover a second broth to
                # read about it and leave without changing their choice —
                # so oF needs both on the wire, not one derived from the
                # other. See `hover.Widget.selected`.
                "selected": bool(w.selected),
            }
            if w.info:
                item["info"] = dict(w.info)
            if w.swatch:
                item["swatch"] = w.swatch
            if w.icon:
                item["icon"] = w.icon
                item["icon_count"] = int(w.icon_count)
                if w.max_icon_count:
                    item["max_icon_count"] = int(w.max_icon_count)
            out.append(item)
        return out

    def _hands_msg(self) -> Dict[str, Any]:
        """Doc section 12.3's "hand marker for each tracked hand, with the
        pointer drawn differently from ambient", for the staff view.

        Stage-space, exactly as it arrived — the tablet converts into its
        own rectified canvas, which is the same space (see
        `common/geometry.warp_frame_to_stage`), so no conversion happens on
        this side.
        """
        return {
            "t": "hands",
            "hands": [{"id": h.id, "role": h.role,
                       "x": round(h.x, 1), "y": round(h.y, 1),
                       "conf": round(h.conf, 2)} for h in self._hands],
            "hover_bin": self._hover_bin,
            "dwell": {"id": self.dwell.active_id,
                      "fraction": round(
                          self.dwell.fraction(self.dwell.active_id or ""), 3)},
        }

    def _overlay_msg(self) -> Dict[str, Any]:
        """Doc section 4.3's `overlay`, and the order it is decided in.

        Doc section 9.5's fault overlay: `error` when a bin that was
        billing from real weight can no longer be read — not merely "the
        scale has never been calibrated", which is the ordinary state of
        the M1 mock-only demo (doc section 12.8) and must not permanently
        cover the table in a fault screen. Only a bin that has crossed into
        `_scale_baselined` and then lost its reading counts: that is the
        "dead XIAO mid-session" case doc section 21's M2 acceptance test
        means, not "never plugged in".
        """
        if self.fsm.state is fsm.State.UNCALIBRATED:
            return {"kind": "uncalibrated"}
        # M6, and it outranks the fault overlay for the same reason doc
        # section 14.5 puts SETTING above `error`: a table in CHECKOUT is
        # not billing any more — the order is written and the numbers are
        # fixed — so "SCALES OFFLINE, NOT BILLING" would warn about a risk
        # that cannot occur while covering the code the diner is trying to
        # pay with.
        if self.fsm.state is fsm.State.CHECKOUT and self._order is not None:
            return {
                "kind": "qr",
                "code": self._order.code,
                "url": self._order.qr_url,
                "total": self._order.total,
                "total_text": self.locales.currency(
                    self._order.total, self.locale)["text"],
                "paid": self._order.paid,
                # **The token is only sent once the money has landed.**
                # Developer, 2026-08-25: "the token number should be given
                # only after sucessfull payment." `code` above is still on
                # the wire because the URL is built from it and the staff
                # view lists it — but oF draws THIS field, and an unpaid
                # order simply has no token to draw. A number shown beside
                # an unpaid QR is one a diner can walk to the counter with.
                "token": self._order.code if self._order.paid else "",
                # The QR as a square bool matrix, drawn by oF as filled
                # rects (I2 — core owns the data, oF owns the pixels).
                # Sent once per state tick like everything else; it is a
                # 29x29 array of bools, which is small enough not to earn
                # a second, event-shaped message of its own.
                "qr": [[1 if v else 0 for v in row] for row in self._order_qr],
            }
        reading = self.scale.read()
        lost = any(self._scale_baselined[i] and reading.grams[i] is None
                  for i in range(cart.NUM_BINS))
        return {"kind": "error"} if lost else {"kind": "none"}

    def _handle_manual_calibrate(self, msg: Dict[str, Any]) -> None:
        """Doc section 12.6's "Calibrate projector <-> camera", solved from
        the 4 table corners the operator placed on the live feed — the
        only calibration path; automated dot-projection calibration was
        removed (it needed a dark, room-light-free rig this project never
        achieved — see CLAUDE.md's M4h/M4i/M4j).

        **Setting mode required**, same rule as `_handle_set_grid`: a new
        homography moves the table crop the camera grid is drawn on, and
        that must not happen under a live diner.

        Synchronous — there is no pattern to project and no classifier
        round trip to wait on, so this never blocks the calling tablet's
        thread for more than a 4-point fit. `GeometryStore.fit_from_corners`
        pins each point to a fixed physical corner by its position in
        `points`, never by where it lands on screen — see that method's
        docstring for why the other way round is the exact bug this avoids.
        """
        if not self._in_setting():
            self.web.broadcast({"t": "manual_calibrate_result", "ok": False,
                                "message": NOT_IN_SETTING_MSG})
            return
        points = msg.get("points")
        if (not isinstance(points, list) or len(points) != 4
                or not all(isinstance(p, list) and len(p) == 2
                           and all(isinstance(v, (int, float))
                                   and not isinstance(v, bool)
                                   and math.isfinite(v) for v in p)
                           for p in points)):
            self.web.broadcast({
                "t": "manual_calibrate_result", "ok": False,
                "message": "Expected exactly 4 corner points."})
            return
        parsed = [(float(p[0]), float(p[1])) for p in points]
        try:
            fit = self.geometry.fit_from_corners(parsed)
            self.geometry.set_homography(
                fit.h, rms_px=fit.rms_px, n_points=fit.n_points,
                keystone_fingerprint=self._keystone_fingerprint,
                camera_size=self.geometry.camera_size,
                corner_points=parsed)
            self.geometry.save_homography()
        except geometry.GeometryError as e:
            self.web.broadcast({"t": "manual_calibrate_result", "ok": False,
                                "message": str(e)})
            return
        # A table that has just acquired its first homography and has no
        # camera grid yet gets the legacy measured layout put on screen —
        # pure line arithmetic now, no homography needed to seed it (unlike
        # the old rect version) — rather than an empty canvas to drag onto
        # from nothing. Not saved (doc section 12.6's "Save is explicit"),
        # and no call to `_check_calibration_complete()` for the same
        # reason — nobody has looked at a seed yet, so it must not be what
        # takes the table out of UNCALIBRATED.
        if not self.camera_grid.has_grid:
            self.camera_grid.seed_from_table()
        self.web.broadcast({
            "t": "manual_calibrate_result", "ok": True,
            "message": "Table corners saved."})
        self.web.broadcast(self._geometry_msg())
        # The tracker holds no config of its own (doc section 4.2) and was
        # told the OLD homography — or none at all — when it connected. A
        # new solve that did not reach it would leave every cursor being
        # converted through the previous table geometry, silently, until
        # the next restart.
        self._push_tracker_cfg()

    def _handle_set_view_rotation(self, msg: Dict[str, Any]) -> None:
        """The Setup tab's Rotate button (drag-corner rebuild step 4 — no
        UI sends this yet). Saves immediately rather than waiting on a
        Confirm — but it is still gated behind setting mode like every
        other Setup-tab action, since it is still something only staff
        should be changing.

        **No longer purely a display preference** (2026-08-12): the
        tracker now applies this same value to compensate MediaPipe's own
        detection for the camera's physical mount rotation
        (`backend_mediapipe.py`'s "180-degree mount compensation"), so a
        change here has to reach it immediately, the same way a new
        homography does in `_handle_manual_calibrate` — a rotation solved
        here but not pushed would leave the tracker detecting against the
        old orientation until its next restart.
        """
        if not self._in_setting():
            self.web.broadcast({"t": "set_view_rotation_result", "ok": False,
                                "message": NOT_IN_SETTING_MSG})
            return
        try:
            self.geometry.set_view_rotation(msg.get("deg"))
        except ValueError as e:
            self.web.broadcast({"t": "set_view_rotation_result", "ok": False,
                                "message": str(e)})
            return
        self.web.broadcast({"t": "set_view_rotation_result", "ok": True,
                            "message": "View rotation saved."})
        self.web.broadcast(self._geometry_msg())
        self._push_tracker_cfg()

    def _handle_set_grid(self, msg: Dict[str, Any]) -> None:
        """Doc section 12.6's "Adjust bin boundaries — drag the grid lines
        on the rectified live feed… Save is explicit."

        The tablet sends the whole grid (4 horizontal + 8 vertical lines),
        not a delta, and only when the operator taps Save — dragging is
        entirely local to the page (doc section 12.6 gives it Undo, which
        is a page-level idea). Sending the whole grid also means a dropped
        message cannot leave core holding some new lines and some old
        ones — which, for a grid, would be worse than the old rect
        version's equivalent gap: a mismatched line pair would not just be
        stale, it could cross and make a bin's own rect invalid.

        **Setting mode required**, same rule as everything else on this
        tab: moving the grid moves the light-pass cutout (once the
        projector grid exists) and moves what the classifier crops right
        now, so a save in serving mode would change what a diner is being
        billed and photographed against mid-order.
        """
        if not self._in_setting():
            self.web.broadcast({"t": "grid_result", "ok": False,
                                "message": NOT_IN_SETTING_MSG})
            return
        h_lines = msg.get("h_lines")
        v_lines = msg.get("v_lines")
        if (not isinstance(h_lines, list) or len(h_lines) != bin_grid.NUM_H_LINES
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                           and math.isfinite(v) for v in h_lines)):
            self.web.broadcast({
                "t": "grid_result", "ok": False,
                "message": f"Expected {bin_grid.NUM_H_LINES} horizontal "
                           "line positions."})
            return
        if (not isinstance(v_lines, list) or len(v_lines) != bin_grid.NUM_V_LINES
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                           and math.isfinite(v) for v in v_lines)):
            self.web.broadcast({
                "t": "grid_result", "ok": False,
                "message": f"Expected {bin_grid.NUM_V_LINES} vertical "
                           "line positions."})
            return
        try:
            self.camera_grid.set_grid([float(v) for v in h_lines],
                                      [float(v) for v in v_lines])
            self.camera_grid.save()
        except bin_grid.BinGridError as e:
            self.web.broadcast({"t": "grid_result", "ok": False,
                                "message": str(e)})
            return
        self.web.broadcast({"t": "grid_result", "ok": True,
                            "message": "Bin grid saved."})
        self.web.broadcast(self._geometry_msg())
        # A table that had a homography and no grid has just become
        # calibrated. Doc section 9.1's UNCALIBRATED -> IDLE transition
        # is M4 build item 6; it hooks in here.
        self._check_calibration_complete()

    def _handle_seed_grid(self) -> None:
        """Doc section 21 M4 build item 5's successor: put the legacy
        measured grid on screen as a starting position to drag from — pure
        line arithmetic now, no homography needed (`bin_grid.py`'s
        docstring on why the old rect version needed one and this does
        not).

        Not saved — doc section 12.6's "Save is explicit" applies to a
        seed more than to anything else, since nobody has looked at it
        yet.
        """
        if not self._in_setting():
            self.web.broadcast({"t": "grid_result", "ok": False,
                                "message": NOT_IN_SETTING_MSG})
            return
        self.camera_grid.seed_from_table()
        self.web.broadcast({
            "t": "grid_result", "ok": True,
            "message": ("Starting positions loaded from the measured table "
                        "layout. Drag the lines onto the trays, then Save.")})
        self.web.broadcast(self._geometry_msg())

    # -- the projector grid (M4n — bin_grid.py's second BinGridStore) ------
    #
    # The camera grid's two handlers above are the template these two
    # follow, deliberately — same message shape, same setting-mode gate,
    # same "Save is explicit" rule. What is genuinely different: there is
    # no rectified picture to drag a line on top of, because this grid
    # needs no camera at all (bin_grid.py's docstring). So `set_grid_
    # projector` doubles as both "drag" and "Save" — a tablet has no local
    # canvas to hold a pending edit in, so every line change it sends is
    # already final, and it reaches oF on the very next ~16ms state tick
    # (`_bin_msg` below), which is the only "preview" this grid can have:
    # watching the real light move on the real table. **Neither grid has a
    # separate Verify step any more (dropped 2026-08-12, same session as
    # this one, on the developer's own call)** — the camera grid's Verify
    # existed to check the REAL TABLE against the RECTIFIED PICTURE an
    # operator actually dragged on, which is a genuinely different space
    # and can diverge from it (doc §5.3's TRAP); the projector grid never
    # had a second space to diverge from in the first place, since the
    # operator is looking at the real table while nudging it, not a proxy
    # for it. Removing the camera grid's Verify step trades that TRAP
    # guard for one fewer tap — a deliberate call, not an oversight; if a
    # bad camera-to-table solve ever produces a plausible-looking rectified
    # picture again, this is the tradeoff to revisit first.

    def _handle_set_grid_projector(self, msg: Dict[str, Any]) -> None:
        """Doc section 12.6/12.7's future projector-space twin, per
        `bin_grid.py`'s docstring: "dragged by watching the actual light
        on the actual table, no camera, no homography, closing its own
        Verify loop independently." Validation, gating and the result
        message are byte-for-byte `_handle_set_grid`'s, aimed at
        `self.projector_grid` instead of `self.camera_grid`.
        """
        if not self._in_setting():
            self.web.broadcast({"t": "grid_projector_result", "ok": False,
                                "message": NOT_IN_SETTING_MSG})
            return
        h_lines = msg.get("h_lines")
        v_lines = msg.get("v_lines")
        if (not isinstance(h_lines, list) or len(h_lines) != bin_grid.NUM_H_LINES
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                           and math.isfinite(v) for v in h_lines)):
            self.web.broadcast({
                "t": "grid_projector_result", "ok": False,
                "message": f"Expected {bin_grid.NUM_H_LINES} horizontal "
                           "line positions."})
            return
        if (not isinstance(v_lines, list) or len(v_lines) != bin_grid.NUM_V_LINES
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                           and math.isfinite(v) for v in v_lines)):
            self.web.broadcast({
                "t": "grid_projector_result", "ok": False,
                "message": f"Expected {bin_grid.NUM_V_LINES} vertical "
                           "line positions."})
            return
        try:
            self.projector_grid.set_grid([float(v) for v in h_lines],
                                         [float(v) for v in v_lines])
            self.projector_grid.save()
        except bin_grid.BinGridError as e:
            self.web.broadcast({"t": "grid_projector_result", "ok": False,
                                "message": str(e)})
            return
        self.web.broadcast({"t": "grid_projector_result", "ok": True,
                            "message": "Projector grid saved."})
        self.web.broadcast(self._projector_grid_msg())

    def _handle_seed_grid_projector(self) -> None:
        """A starting position to nudge from — the same CAD/legacy line
        arithmetic `_handle_seed_grid` puts on the rectified feed, put on
        the real table instead (`_bin_msg` below sends it to oF the moment
        it lands in memory, saved or not, same as every other in-memory
        grid edit). Not saved, for the same reason `_handle_seed_grid`
        does not save.
        """
        if not self._in_setting():
            self.web.broadcast({"t": "grid_projector_result", "ok": False,
                                "message": NOT_IN_SETTING_MSG})
            return
        self.projector_grid.seed_from_table()
        self.web.broadcast({
            "t": "grid_projector_result", "ok": True,
            "message": ("Starting positions loaded from the measured table "
                        "layout. Select a line and nudge it with the arrow "
                        "keys, watching the real table.")})
        self.web.broadcast(self._projector_grid_msg())

    def _projector_grid_msg(self) -> Dict[str, Any]:
        """What a staff-view projector-grid editor needs on join and after
        every change. No homography fields — unlike `_geometry_msg`, this
        grid was never solved from anything.
        """
        pg = self.projector_grid
        return {
            "t": "projector_grid",
            "has_grid": pg.has_grid,
            "h_lines": (None if pg.grid is None
                       else [round(v, 1) for v in pg.grid.h_lines]),
            "v_lines": (None if pg.grid is None
                       else [round(v, 1) for v in pg.grid.v_lines]),
        }

    # -- the Capture tab (doc section 12.7 — M4 build item 7) --------------

    def _handle_capture(self, msg: Dict[str, Any]) -> None:
        """Doc section 12.7's "Capture all" and "Burst".

        **The lighting rule is the load-bearing part of this handler, and
        it is enforced by refusing, not by building a second path.** Doc
        section 12.7: "capture must run with the bin patches lit exactly
        as serving mode lights them… The Capture tab must therefore drive
        the same bin-patch path as serving mode, not its own." Doc section
        21's acceptance list restates it as a rule about *design*: "If the
        Capture tab has its own lighting path, that is a bug to fix before
        collecting a single image, not after."

        So there is no lighting code here at all. What there is instead:

        - **Setting mode is required.** Not for the lighting — doc section
          14.5 is explicit that "the field and the bin patches are
          identical to serving mode" in setting mode, so this changes
          nothing about the light. It is required because the operator is
          reaching over trays and swapping them, which in serving mode is
          a pick and would bill; the same reason Tare and Calibrate need
          it (doc section 12.4).
        - **The rects come from the camera grid store, not from the
          tablet.** The classifier crops the warped table frame (doc
          section 4.7), and the rects it should crop are the ones core
          owns. A tablet sending its own would let an un-saved drag reach
          the dataset.
        - **Core never touches a frame (hard invariant).** So core cannot
          do the table-crop warp itself — it sends the classifier the
          homography and stage size alongside the rects, and the
          classifier (which already handles frames) warps before it crops.
        """
        if not self._in_setting():
            self.web.broadcast({"t": "capture_result", "ok": False,
                                "message": NOT_IN_SETTING_MSG})
            return
        if not self.geometry.has_homography:
            self.web.broadcast({
                "t": "capture_result", "ok": False,
                "message": ("The table has not been calibrated yet — do that "
                            "on the Setup tab first.")})
            return
        if not self.camera_grid.has_grid:
            self.web.broadcast({
                "t": "capture_result", "ok": False,
                "message": ("The bin grid is not set yet — do that on "
                            "the Setup tab first.")})
            return

        labels = msg.get("labels")
        if (not isinstance(labels, list)
                or len(labels) != binmap.NUM_BINS
                or not all(isinstance(v, str) and v.strip() for v in labels)):
            self.web.broadcast({
                "t": "capture_result", "ok": False,
                "message": f"Every one of the {binmap.NUM_BINS} bins needs a "
                           "label before its photograph is worth keeping."})
            return
        burst = msg.get("burst", 1)
        # Seconds BETWEEN shots, not a total period divided across the
        # burst — see classifier/main.py's `_capture` for why that division
        # used to be the wrong knob. Core does no validation of its own
        # here; the classifier clamps both fields (`MAX_BURST`,
        # `MAX_INTERVAL_S`) and this is a straight pass-through.
        interval = msg.get("interval", 2.0)

        # `[x, y, w, h, bin_index]` — the fifth element is what puts the
        # bin number in the filename (doc section 12.7's
        # `<unixms>_bin<i>.jpg`). The classifier reads it and ignores it
        # otherwise; it never learns what a bin is.
        rects = [list(r) + [i] for i, r in enumerate(self.camera_grid.rects())]

        reply = self._send_classifier_cmd(
            "capture", CLASSIFIER_REPLY_TIMEOUT_S,
            rects=rects, labels=list(labels), burst=burst, interval=interval,
            h=self.geometry.h, stage_size=list(self.geometry.stage_size))
        if not reply or reply.get("ok") is False:
            self.web.broadcast({
                "t": "capture_result", "ok": False,
                "message": str((reply or {}).get("error")
                               or "the classifier is not answering")})
            return
        files = reply.get("files") or []
        self.web.broadcast({
            "t": "capture_result", "ok": True,
            "message": (f"Saved {len(files)} images."
                        + (" Cancelled part-way." if reply.get("cancelled")
                           else "")),
            "files": files,
            "counts": self._capture_counts(),
        })

    def _capture_counts(self) -> Dict[str, int]:
        """Doc section 12.7's "session counter per label, so the operator
        can see they have 40 mushrooms and 6 prawns and go collect more
        prawns."

        Counted off the filesystem rather than kept in memory: the number
        that matters is how many images exist, not how many this run of
        core happened to take, and an operator who restarts core mid-
        collection must not see the count reset to zero.
        """
        out: Dict[str, int] = {}
        try:
            if not CAPTURES_DIR.is_dir():
                return out
            for label_dir in CAPTURES_DIR.iterdir():
                if not label_dir.is_dir():
                    continue
                out[label_dir.name] = sum(
                    1 for f in label_dir.iterdir()
                    if f.suffix.lower() in (".jpg", ".jpeg", ".png"))
        except OSError:
            _log.exception("core: could not count the captured images")
        return out

    # -- Edge Impulse link/upload/download (doc sections 19.2, 19.5) -------

    def _ei_msg(self) -> Dict[str, Any]:
        """What the Capture tab's Edge Impulse panel needs on join: whether
        a project is linked yet, and which one -- `active` lets a tablet
        that (re)joins mid-upload/mid-download show the right disabled
        state instead of a stale "idle" one."""
        project = ei_store.load_project(self._ei_project_path)
        return {
            "t": "ei_status",
            "linked": project is not None,
            "project_id": project["project_id"] if project else None,
            "project_name": project["project_name"] if project else None,
            "active": self._ei_active,
        }

    def _handle_ei_link(self, msg: Dict[str, Any]) -> None:
        """Doc section 19.2's project link, done once per fresh clone --
        two ways in, both ending at the same ei_store.save_project() call:

        - `username`/`password`(/`totp`): login() then create_project(),
          which always makes a brand NEW, empty Studio project (see
          `_link_new`'s own docstring for why that's the wrong choice if
          `hotpot-ingredients`, id 1087506, already exists and is
          trained).
        - `project_id`/`api_key`: adopts an EXISTING project by pasting
          its own API key straight from Studio's Dashboard -> Keys —
          `_link_existing` below, added 2026-08-24 after `_link_new`
          created a second, empty "hotpot-ingredients" project (id
          1095239) alongside the real one the first time this ran, since
          nothing before this let a caller say "no, THAT one" instead.

        Idempotent either way: an already-linked project is a no-op
        reporting the existing link, same contract the ported-from
        project's `EIController.link()` gives per device_type — here
        there is only ever the one project. Whatever the tablet sent
        (password, or API key) is used for exactly this one call and
        never stored — see `ei_client.py`'s and `ei_store.py`'s module
        docstrings.
        """
        existing = ei_store.load_project(self._ei_project_path)
        if existing is not None:
            self.web.broadcast({
                "t": "ei_link_result", "ok": True, "linked": True,
                "project_id": existing["project_id"],
                "project_name": existing["project_name"],
                "message": f"Already linked to {existing['project_name']!r}."})
            return
        if self._ei_active is not None:
            self.web.broadcast({
                "t": "ei_link_result", "ok": False,
                "message": f"a {self._ei_active!r} job is already running"})
            return

        if msg.get("project_id") and msg.get("api_key"):
            self._link_existing(msg)
        else:
            self._link_new(msg)

    def _link_new(self, msg: Dict[str, Any]) -> None:
        """login() + create_project() -- ALWAYS makes a brand new, empty
        Studio project (EI's API has no "create if missing, else adopt"
        endpoint), which is the right call exactly once: the very first
        time this app is ever linked to Edge Impulse at all, before any
        project named `EI_PROJECT_NAME` exists yet. Any other time (e.g.
        `hotpot-ingredients` id 1087506 already exists and is trained),
        `_link_existing` is what a caller wants instead — see
        `_handle_ei_link`'s own docstring for how that project came to
        exist as a second, empty duplicate the first time this ran.
        """
        username = msg.get("username")
        password = msg.get("password")
        if not username or not password:
            self.web.broadcast({
                "t": "ei_link_result", "ok": False,
                "message": "Edge Impulse username and password are required."})
            return
        totp = msg.get("totp") or None
        project_name = msg.get("project_name") or EI_PROJECT_NAME

        self._ei_active = "link"
        try:
            jwt = self._ei_client.login(username, password, totp)
            project_id, api_key = self._ei_client.create_project(jwt, project_name)
        except ei_client.EITotpRequiredError:
            self.web.broadcast({
                "t": "ei_link_result", "ok": False, "totp_required": True,
                "message": "Edge Impulse needs a two-factor code — enter it "
                           "and try again."})
            return
        except ei_client.EIClientError as e:
            self.web.broadcast({"t": "ei_link_result", "ok": False,
                                "message": str(e)})
            return
        except Exception:      # noqa: BLE001 - see the module-level note
                                # in _handle_ei_link's docstring block for
                                # why every _handle_ei_*/_link_* method
                                # needs its own catch-all.
            _log.exception("core: ei_link (new) raised")
            self.web.broadcast({
                "t": "ei_link_result", "ok": False,
                "message": "linking to Edge Impulse hit an internal error — see the log"})
            return
        finally:
            self._ei_active = None

        ei_store.save_project(self._ei_project_path, project_id, api_key,
                              project_name)
        # Every _handle_ei_* path logs the project id it acted on, added
        # 2026-08-24 after a rig report ("pressed Download and the
        # training was gone") could not be reconstructed at all: this
        # whole flow used to log nothing but exceptions, so the log could
        # not even say WHICH of the three same-named `hotpot-ingredients`
        # projects on this account each click had gone to. The id is the
        # one fact that tells a duplicate project apart from the real one.
        _log.info("core: ei_link created NEW project %s (%r) — this is an "
                  "empty project, not an adopted one", project_id, project_name)
        self.web.broadcast({
            "t": "ei_link_result", "ok": True, "linked": True,
            "project_id": project_id, "project_name": project_name,
            "message": (f"Created and linked {project_name!r}. Open it in "
                        "Edge Impulse Studio to configure the impulse "
                        "(image input, image DSP block, MobileNetV2 "
                        "transfer learning — doc §19.2) before the first "
                        "upload.")})

    def _link_existing(self, msg: Dict[str, Any]) -> None:
        """Adopts an already-existing Studio project by its own
        project_id + api_key (Studio's Dashboard -> Keys page), rather
        than creating a new one -- the only way to point this app at
        `hotpot-ingredients` (id 1087506) instead of at a fresh empty
        project sharing its name. ei_client.get_project() both fetches
        the project's real name (for the "Linked to <name>" link) and
        validates the key actually belongs to that project id before
        anything is saved locally — a copy-paste mismatch fails loudly
        here instead of silently linking and only breaking on the first
        Upload.
        """
        api_key = msg.get("api_key")
        try:
            project_id = int(msg.get("project_id"))
        except (TypeError, ValueError):
            self.web.broadcast({
                "t": "ei_link_result", "ok": False,
                "message": "That project ID isn't a number."})
            return

        self._ei_active = "link"
        try:
            project = self._ei_client.get_project(api_key, project_id)
        except ei_client.EIClientError as e:
            self.web.broadcast({"t": "ei_link_result", "ok": False,
                                "message": str(e)})
            return
        except Exception:      # noqa: BLE001 - see _link_new's own note
            _log.exception("core: ei_link (existing) raised")
            self.web.broadcast({
                "t": "ei_link_result", "ok": False,
                "message": "linking to Edge Impulse hit an internal error — see the log"})
            return
        finally:
            self._ei_active = None

        project_name = project.get("name") or f"project-{project_id}"
        ei_store.save_project(self._ei_project_path, project_id, api_key,
                              project_name)
        _log.info("core: ei_link adopted existing project %s (%r)",
                  project_id, project_name)
        self.web.broadcast({
            "t": "ei_link_result", "ok": True, "linked": True,
            "project_id": project_id, "project_name": project_name,
            "message": f"Linked to the existing project {project_name!r}."})

    def _handle_ei_upload(self, msg: Dict[str, Any]) -> None:
        """Doc section 19.2's dataset push -- every image under
        `datasets/captures/<label>/` (doc section 12.7's Capture tab
        output), sent straight to Edge Impulse's ingestion API with
        `category="split"` (EI's own auto 80/20, the same split
        `tools/upload_edgeimpulse.ps1`'s CLI-based path already asks for).
        Progress is broadcast per batch so the tablet can show a running
        uploaded/total count, the same shape `capture_progress` gives a
        multi-shot capture burst.
        """
        project = ei_store.load_project(self._ei_project_path)
        if project is None:
            self.web.broadcast({
                "t": "ei_upload_result", "ok": False,
                "message": "Link to Edge Impulse first."})
            return
        if self._ei_active is not None:
            self.web.broadcast({
                "t": "ei_upload_result", "ok": False,
                "message": f"a {self._ei_active!r} job is already running"})
            return

        def progress(**fields) -> None:
            self.web.broadcast({"t": "ei_upload_progress", **fields})

        self._ei_active = "upload"
        _log.info("core: ei_upload -> project %s (%r) from %s",
                  project["project_id"], project["project_name"], CAPTURES_DIR)
        try:
            result = self._ei_client.upload_captures(
                project["api_key"], CAPTURES_DIR, on_progress=progress)
        except ei_client.EIClientError as e:
            self.web.broadcast({"t": "ei_upload_result", "ok": False,
                                "message": str(e)})
            return
        except Exception:      # noqa: BLE001 - see _handle_ei_link's own
                                # catch-all for why this must exist too.
            _log.exception("core: ei_upload raised")
            self.web.broadcast({
                "t": "ei_upload_result", "ok": False,
                "message": "uploading to Edge Impulse hit an internal error — see the log"})
            return
        finally:
            self._ei_active = None

        uploaded_total = sum(result["uploaded"].values())
        _log.info("core: ei_upload -> project %s finished: %d image(s), "
                  "per label %s, %d batch failure(s)%s",
                  project["project_id"], uploaded_total, result["uploaded"],
                  len(result["failures"]),
                  "" if not result["failures"] else f" — {result['failures']}")
        self.web.broadcast({
            "t": "ei_upload_result", "ok": True,
            "uploaded": result["uploaded"], "failures": result["failures"],
            "message": (f"Uploaded {uploaded_total} image(s) to "
                        f"{project['project_name']!r} (id "
                        f"{project['project_id']})."
                        + (f" {len(result['failures'])} batch(es) failed — "
                           "local files are untouched, re-run Upload to "
                           "retry." if result["failures"] else ""))})

    def _handle_ei_download(self, msg: Dict[str, Any]) -> None:
        """Doc section 19.5's model fetch: build the locked C++ library /
        EON / int8 deployment (`ei_client.DEPLOY_ENGINE`/`DEPLOY_MODEL_TYPE`
        -- models/README.md's "hotpot-ingredients" entry) for whatever is
        currently trained in Studio, then download the ZIP to
        `models/<project_name>.zip`, overwriting any previous download --
        same "single current file, nothing versioned locally" convention
        `models/README.md` already documents by hand.

        Training itself is NOT triggered here — doc section 19.2's transfer
        learning stays a manual Studio step (module docstrings on why), so
        this assumes the operator has already clicked Train there. It is
        just a button, not a poll for "has training finished yet": build
        job's own wait_for_job() below only waits on the BUILD, which only
        starts once this is clicked.

        Unzipping the download over `tools/eim_cpp/vendor/` and rebuilding
        `classify.exe` (`ei_deploy.py`) now happen right here too, not as a
        manual step after this returns — models/README.md's own provenance
        log caught the gap this used to leave: 2026-08-24's 99.69%-accuracy
        redeploy (project 1095598) sat downloaded and unzipped for hours
        while the running app kept classifying with the previous model,
        because "download" and "the running app actually uses it" were two
        separate steps a human had to remember to do both of. Pressing
        Download is now the whole redeploy; the very next live
        classification pass after this broadcasts `ok: true` uses the new
        model, no classifier process restart needed (backend_ei.py's
        `_InputDims` re-reads model_metadata.h by mtime on every call).

        Training itself is still NOT triggered here — doc section 19.2's
        transfer learning stays a manual Studio step.
        """
        project = ei_store.load_project(self._ei_project_path)
        if project is None:
            self.web.broadcast({
                "t": "ei_download_result", "ok": False,
                "message": "Link to Edge Impulse first."})
            return
        if self._ei_active is not None:
            self.web.broadcast({
                "t": "ei_download_result", "ok": False,
                "message": f"a {self._ei_active!r} job is already running"})
            return
        api_key, project_id = project["api_key"], project["project_id"]

        def progress(stage: str) -> None:
            self.web.broadcast({"t": "ei_download_progress", "stage": stage})

        self._ei_active = "download"
        _log.info("core: ei_download -> project %s (%r), building %s/%s/%s",
                  project_id, project["project_name"],
                  ei_client.DEPLOY_TYPE, ei_client.DEPLOY_ENGINE,
                  ei_client.DEPLOY_MODEL_TYPE)
        # _ei_active stays "download" through unzip+rebuild too (finally
        # below), not just the network half — a second Download click
        # landing mid-rebuild would race the same tools/eim_cpp/vendor/ and
        # build/ directories this one is writing into.
        try:
            progress("building")
            job_id = self._ei_client.build_model(api_key, project_id)
            _log.info("core: ei_download -> project %s build job %s started",
                      project_id, job_id)
            self._ei_client.wait_for_job(
                api_key, project_id, job_id,
                on_poll=lambda: progress("building"))

            progress("downloading")
            zip_bytes = self._ei_client.download_model(api_key, project_id)

            dest = self._models_dir / f"{project['project_name']}.zip"
            atomicio.write_bytes(dest, zip_bytes)
            _log.info("core: ei_download -> project %s wrote %s (%d bytes)",
                      project_id, dest, len(zip_bytes))

            progress("unzipping")
            eim_cpp_dir = self._models_dir.parent / "tools" / "eim_cpp"
            vendor_dir = eim_cpp_dir / "vendor"
            self._ei_deploy.unzip_over_vendor(zip_bytes, vendor_dir)
            _log.info("core: ei_download -> project %s unzipped over %s",
                      project_id, vendor_dir)

            progress("compiling")
            self._ei_deploy.rebuild(eim_cpp_dir,
                                    on_output=lambda line: _log.info(
                                        "core: ei_download -> rebuild.bat: %s",
                                        line.rstrip()))
            _log.info("core: ei_download -> project %s classify.exe rebuilt",
                      project_id)
        except ei_client.EIClientError as e:
            self.web.broadcast({"t": "ei_download_result", "ok": False,
                                "message": str(e)})
            return
        except ei_deploy.EiDeployError as e:
            _log.error("core: ei_download -> deploy step failed: %s", e)
            self.web.broadcast({
                "t": "ei_download_result", "ok": False,
                "message": (f"Downloaded the model but deploying it failed: "
                            f"{e}")})
            return
        except Exception:      # noqa: BLE001 - see _handle_ei_link's own
                                # catch-all for why this must exist too.
            _log.exception("core: ei_download raised")
            self.web.broadcast({
                "t": "ei_download_result", "ok": False,
                "message": "downloading from Edge Impulse hit an internal error — see the log"})
            return
        finally:
            self._ei_active = None

        self.web.broadcast({
            "t": "ei_download_result", "ok": True, "path": str(dest),
            "message": (f"Deployed {dest.name} ({len(zip_bytes)} bytes) "
                        f"from {project['project_name']!r} (id {project_id}) "
                        "— classify.exe rebuilt, live now.")})

    def _handle_ei_unlink(self, msg: Dict[str, Any]) -> None:
        """Drops the saved local project_id/api_key mapping
        (ei_store.remove_project()) so a fresh Link starts over -- e.g.
        the linked Studio project was deleted by hand, or the wrong
        project got linked and there's nothing to Upload/Download against
        that's still correct. Never calls Edge Impulse's own API: there is
        nothing left to delete there if the project genuinely is gone, and
        even if it isn't, deleting someone's Studio project as a side
        effect of a local "forget this" click would be a surprising blast
        radius -- same call ei_store.remove_project()'s own docstring
        already makes. Purely local and instant (no network call), but
        still gated on `_ei_active` -- unlinking out from under an
        in-flight Upload/Download would pull the api_key its background
        work is already using.
        """
        if self._ei_active is not None:
            self.web.broadcast({
                "t": "ei_unlink_result", "ok": False,
                "message": f"a {self._ei_active!r} job is already running"})
            return
        removed = ei_store.remove_project(self._ei_project_path)
        _log.info("core: ei_unlink dropped the local project link (%s) — "
                  "the Studio project itself is untouched",
                  "there was one" if removed else "there was none")
        self.web.broadcast({
            "t": "ei_unlink_result", "ok": True,
            "message": "Unlinked." if removed else "Nothing was linked."})

    def _check_calibration_complete(self) -> None:
        """Doc section 9.1's UNCALIBRATED -> IDLE, taken the moment both
        state files exist.

        `Fsm.calibration_complete()` re-checks the geometry itself and
        no-ops from every other state, so this is safe to call after any
        geometry write without asking where the FSM is first.
        """
        with self.state_lock:
            self.fsm.calibration_complete()

    def _capture_msg(self) -> Dict[str, Any]:
        """What the Capture tab needs on join: the camera-grid-derived
        rects to crop previews out of the RECTIFIED live feed, the label
        each bin defaults to, and the per-label counts.

        Doc section 12.7: "Each crop has a label selector defaulting to
        the current bin-map item." The default is the item's **`id`**, not
        its display name — doc section 8.1's hidden-label rule runs the
        other way here than it does on the table. `names` is what a diner
        reads; `class_name`/`id` is what the model emits, and a training
        folder named "Fish Ball" would be a folder the model can never
        produce a label for.
        """
        labels = []
        for i in range(binmap.NUM_BINS):
            item = self.catalogue.item(self.binmap.bins[i].item_id)
            labels.append(item.class_name if item is not None else "")
        return {
            "t": "capture_info",
            "rects": [None if r is None else [round(v, 1) for v in r]
                      for r in self.camera_grid.rects()],
            "labels": labels,
            "choices": sorted({self.catalogue.item(i).class_name
                               for i in self.catalogue.ids()
                               if self.catalogue.item(i) is not None}
                              | set(NON_FOOD_CAPTURE_LABELS)),
            "counts": self._capture_counts(),
        }

    def _geometry_msg(self) -> Dict[str, Any]:
        """What the Setup tab needs to render: whether the table is
        calibrated (homography AND camera grid — two stores now, see
        `core/bin_grid.py`), the last solve's numbers, the camera grid
        lines to drag, whether oF's keystone has moved under the solve
        (doc section 8.5), the last confirmed 4 corner points (drag-corner
        rebuild step 4's seed for its handles — `null` if none yet), and
        the display-only view rotation.
        """
        g = self.geometry
        cg = self.camera_grid
        return {
            "t": "geometry",
            "calibrated": g.has_homography and cg.has_grid,
            "has_homography": g.has_homography,
            "has_grid": cg.has_grid,
            "rms_px": None if g.rms_px is None else round(g.rms_px, 2),
            "n_points": g.n_points,
            "computed_at": g.computed_at,
            "camera_size": list(g.camera_size),
            "stage_size": list(g.stage_size),
            "keystone_stale": g.keystone_is_stale(self._keystone_fingerprint),
            "h_lines": (None if cg.grid is None
                       else [round(v, 1) for v in cg.grid.h_lines]),
            "v_lines": (None if cg.grid is None
                       else [round(v, 1) for v in cg.grid.v_lines]),
            "corner_points": (None if g.corner_points is None
                              else [list(p) for p in g.corner_points]),
            "view_rotation_deg": g.view_rotation_deg,
        }

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
                # `item_id`/`source` are what the Bins tab's manual
                # override select reads to preselect the current item and
                # to know whether it is looking at a classifier guess or
                # a standing human answer — added for the override
                # control, doc section 9.3's fallback for a bad classify.
                "item_id": b.item_id,
                "source": b.source,
                "grams": None if grams is None else round(grams),
                "calibrated": cal_bin.calibrated,
                "tared": cal_bin.tared,
                "noise_g": None if noise_g is None else round(noise_g, 1),
                "noisy": noise_g is not None and noise_g > settle_tol_g,
                "noise_dots": _noise_dots(noise_g, settle_tol_g),
            })
        return {
            "t": "bins",
            # **`port`/`age`/`bad_lines` are here because of 2026-08-25**:
            # the scales went offline mid-service and the three fields on
            # this message could not tell "the XIAO is unplugged" from
            # "the XIAO is plugged in, enumerated, and has stopped
            # sending" — which are different faults with different fixes,
            # and the second one is the one that happened. Diagnosing it
            # took a hand-written WebSocket probe; it should have taken a
            # glance at this line. `scale.status()` has carried all three
            # since M2.2, so this is wiring, not new data.
            "serial": {"open": status["open"], "stale": status["stale"],
                      "hz": status["hz"], "port": status["port"],
                      "age": status["age"], "bad_lines": status["bad_lines"]},
            # Every catalogue item, for the override select — doc §8.1's
            # SHOWN half (display_name), since this is a staff-facing
            # surface, not the Capture tab's hidden class_name.
            "choices": [{"id": iid, "name": self.catalogue.item(iid).display_name(self.locale)}
                       for iid in self.catalogue.ids()
                       if self.catalogue.item(iid) is not None],
            "bins": bins,
            # The Developer tab's window-size controls read their current
            # value off this — the same "arrives within 100ms of join, no
            # separate join message needed" reasoning this whole method's
            # docstring already gives for the Bins tab itself (2026-08-26).
            "median_window": self.scale.median_window,
            "avg_window": self.scale.avg_window,
        }

    def _scale_trace_msg(self) -> Dict[str, Any]:
        """The Developer tab's live plot: one raw sample and one
        filtered (median-then-average) sample per bin, per tick
        (2026-08-26).

        **GRAMS, not counts — changed the same day, on developer
        request.** The first version sent counts on purpose (a
        signal-level diagnostic that still works on an uncalibrated
        bin), but a jump that "looks small" in counts can still be
        several grams once divided by that bin's own counts_per_gram,
        and two bins' counts are never directly comparable to each other
        anyway (each has its own scale and sign). Grams is the unit the
        display and the bill actually use, so the plot now shows the
        real thing rather than a proxy for it. **Cost of the change,
        accepted deliberately:** an unresolved/uncalibrated bin now
        plots nothing — `grams`/`raw_grams` are `None` there, same as
        everywhere else in this file — where the counts version would
        have shown a signal with no unit attached. Watching an
        uncalibrated cell's raw counts is what `capture()`'s own tare/
        calibrate flow is for, not this card.

        Broadcast to every tablet on the same 10Hz tick as `bins`/`hands`
        regardless of whether anyone has the Developer tab open — unlike
        the camera process's own stats (polled cross-process, over HTTP,
        only while that tab is visible), this is core's own in-memory
        data going out over a socket that is already open, and eight
        bins' worth of two numbers each is a rounding error next to
        `bins`' own per-tick payload.
        """
        reading = self.scale.read()
        return {
            "t": "scale_trace",
            "ts": reading.ts,
            "raw": [None if g is None else round(g, 2) for g in reading.raw_grams],
            "filtered": [None if g is None else round(g, 2) for g in reading.grams],
        }

    def _handle_set_scale_filter(self, msg: Dict[str, Any]) -> None:
        """Developer tab's window-size controls (2026-08-26):
        `median_window`/`avg_window`, resized LIVE on the running reader.

        **No setting-mode gate.** This is a developer tuning knob, not a
        calibration — it cannot make a bin bill wrong, only change how
        smoothed the number is (`scale.DEFAULT_AVG_WINDOW`'s own comment
        in scale.py explains why these two have to be tunable at all: the
        right value is a rig measurement away, not a guess). Gating it on
        setting mode would make it untestable against a live diner's own
        pick, which is exactly the case a developer watching the plot
        would want to try.

        Either field may be omitted to leave it unchanged. Both are
        validated BEFORE either is applied, so a bad `avg_window` cannot
        leave `median_window` half-changed with no way to tell from the
        reply.
        """
        median_window = msg.get("median_window")
        avg_window = msg.get("avg_window")
        for name, v in (("median_window", median_window), ("avg_window", avg_window)):
            if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 1):
                self.web.broadcast({
                    "t": "scale_filter_result", "ok": False,
                    "message": f"{name} must be a whole number of 1 or more.",
                })
                return
        if median_window is not None:
            self.scale.set_median_window(median_window)
        if avg_window is not None:
            self.scale.set_avg_window(avg_window)
        try:
            scale.save_filter_window(self.scale.median_window,
                                     self.scale.avg_window,
                                     self.scale_filter_path)
        except Exception as e:                        # noqa: BLE001
            _log.warning("core: could not write %s (%s) — the filter "
                         "window is correct in memory but will not "
                         "survive a restart", self.scale_filter_path, e)
        self.web.broadcast({
            "t": "scale_filter_result", "ok": True,
            "median_window": self.scale.median_window,
            "avg_window": self.scale.avg_window,
        })

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
                # Doc section 12.3's hand markers, on the same divided
                # clock as the Bins tab and for the same reason: a tablet
                # over Wi-Fi does not need 60Hz to show where a hand is,
                # and the table already gets it at full rate over UDP.
                self.web.broadcast(self._hands_msg())
                # The Developer tab's live plot, same clock again — the
                # load cells themselves only sample at ~10.7Hz (scale.py's
                # module docstring), so this tick already matches the
                # fastest anything here can actually change.
                self.web.broadcast(self._scale_trace_msg())
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
            self._apply_cursor(time.monotonic())
            self._apply_scale_to_cart()
            msg = {
                "t": "state",
                "seq": self._state_seq,
                "ts": time.time(),
                "mode": (MODE_SETTING if self.fsm.state is fsm.State.SETTING
                          else MODE_SERVING),
                # **M6: which screen the table is on, alongside — not
                # inside — `mode`.** Doc section 4.3 fixes `mode` at
                # serving|setting and oF branches its banner on it, so
                # folding BROTH/SPICE/CHECKOUT in there would have made
                # every checkout screen read as a mode change. This is the
                # FSM state's own name, and oF uses it to decide whether
                # it is drawing a cart or a list of options.
                "phase": self.fsm.state.value,
                # `_apply_phantom`'s idle-table attract loop. oF uses this
                # to hide everything except the bin halos and the brand
                # mark — the hidden UI is itself the "this table is idle"
                # signal the developer asked for, 2026-08-26.
                "idle_attract": self._phantom_active,
                # 2026-08-26: whether a hand is currently inside a bin —
                # oF loops `fire_burning` for exactly as long as this is
                # true (`AudioBus::setFireBurningActive`), the same
                # bool-on-`state`-drives-a-loop shape `idle_attract`
                # already uses for `attract`. The one-shot catch/put-out
                # cues (`fire_start`/`fire_stop`) are `evt`s, sent where
                # `self._hover_bin` changes, just above.
                "fire_active": self._hover_bin is not None,
                "screen": self._screen_msg(),
                "locale": self.locale,
                # M8 hasn't built the fluid renderer yet; the shape is correct
                # per doc section 4.3, "mala" is the documented diner default,
                # and enabled:False is the honest statement that nothing is
                # rendering it yet.
                "fluid": {"style": "mala", "enabled": False, "intensity": 0.6},
                "bins": [self._bin_msg(i) for i in range(binmap.NUM_BINS)],
                "total": self._total_msg(),
                "widgets": self._widget_msgs(),
                "overlay": self._overlay_msg(),
            }
            self._state_seq += 1
            return msg

    def _screen_msg(self) -> Dict[str, Any]:
        """The page header: a title telling the diner what to do, and
        where they are in the sequence.

        2026-08-25. The developer's standard for this table is "any non
        techy person should be able to understand it", and the single
        cheapest thing that buys is a sentence naming the task — every
        restaurant kiosk a diner has already used leads its screen with
        one ("Choose your size", "Add a drink?"). Without it the broth
        page is four unlabelled plates and a Next button.

        `step`/`steps` drive the dots oF draws under the title: a diner
        who can see how many steps there are and which one they are on
        knows the table is not about to charge them, which is most of what
        makes a kiosk feel safe to poke at.

        **FIVE steps, not three** — developer, 2026-08-25: "the three dots
        showing which page is active, shouldnt it be 5 dots including the
        payment page and token number page." It was three because paying
        was read as the END of the sequence rather than a step in it; but
        a diner counting dots is counting SCREENS THEY WILL SEE, and they
        see five. Three dots on a table that then shows two more screens
        understates how far there is to go at exactly the moment being
        honest about it matters — which is the whole reason the dots are
        there.

            1 cart · 2 broth · 3 spice · 4 pay · 5 token

        Every string is resolved here, per I2 — oF does no lookup, so a
        second locale changes `data/locales/*.json` and no C++.

        Caller holds `state_lock` (via `_state_msg`).
        """
        st = self.fsm.state
        if st is fsm.State.BROTH:
            key, step = "step_broth", 2
        elif st is fsm.State.SPICE:
            key, step = "step_spice", 3
        elif st is fsm.State.CHECKOUT:
            paid = self._order is not None and self._order.paid
            key, step = ("paid", 5) if paid else ("step_pay", 4)
        elif st is fsm.State.SELECTING:
            key, step = "step_cart", 1
        else:
            # IDLE, BOOT, SETTING, UNCALIBRATED: no header. An empty title
            # draws nothing (oF's own rule for every optional string), and
            # a step counter on a table nobody is using would be furniture
            # claiming a transaction is in progress.
            return {"title": "", "step": 0, "steps": 0, "hint": "",
                    "hint2": ""}
        hint = ""
        hint2 = ""
        if st is fsm.State.CHECKOUT:
            paid = self._order is not None and self._order.paid
            # **The unpaid screen has no hint at all** — developer,
            # 2026-08-25: "in payment no need to say scan with ur phone
            # camera, it is very clear, remove that line." A QR code
            # under a title that already reads "Scan To Pay" does not
            # need a third line explaining what a QR code is; the locale
            # key `pay_hint` is deleted, not blanked, so nothing can put
            # it back by accident.
            #
            # **The PAID screen keeps its hint, and it is TWO lines.**
            # Developer, 2026-08-25: "at the payment recieved page it says
            # show this number at the counter. that doesnt make sense. we
            # first need to hand it over for cooking, then collect it when
            # the token number is called." The old single line named a
            # counter that does not exist and pointed at the wrong end of
            # the trip — a token here is not presented to anybody, it is
            # ANNOUNCED back at the diner. The flow the developer gave:
            # staff stand at the table, the bowl is handed to them, they
            # tie it to the token themselves (not this system's problem),
            # and the number is called when the food is cooked.
            #
            # So two lines, because they are two different moments and
            # collapsing them is what made the old line wrong: `hint` is
            # what to do NOW, `hint2` is what happens next. Both resolved
            # here per I2; `hint2` is empty on every other screen, and oF
            # draws nothing for an empty one.
            hint = (self.locales.translate("token_hint", self.locale)
                    if paid else "")
            hint2 = (self.locales.translate("token_hint2", self.locale)
                     if paid else "")
        return {
            "title": self.locales.translate(key, self.locale),
            "step": step,
            "steps": 5,
            "hint": hint,
            "hint2": hint2,
        }

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
            #
            # 2026-08-14: a `shortLabel`/`short_display_name()` detour
            # (VISUAL_LAYER.md step 2) lived here briefly, deleted the same
            # day on developer instruction — "remove the short label idea,
            # show the original label, max 2 lines." oF wraps `b.label` to
            # up to 2 lines now (UiLayer.cpp's drawBin) instead of core
            # pre-shortening it.
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
            # VISUAL_LAYER.md section 8's info box (build item 10). Two
            # already-resolved strings and one number, same rule as `label`
            # and `sub` above — oF looks nothing up (I2) and formats no
            # unit: "kcal" is a word, and the day a locale needs a
            # different one it changes here, not in C++.
            #
            # `fact` was a third string until 2026-08-24 and is gone, not
            # blanked: the developer read both lines on the table and cut
            # the trivia one outright. `desc` is the survivor and it
            # changed meaning at the same time — see `Item.description`.
            #
            # Sent on EVERY bin, not only the hovered one. The bin the box
            # is about is `hl == "hover"`, which is already on this same
            # message, so a separate "active item info" field would be a
            # second place for the same fact to be computed from and to
            # disagree with. It is a few hundred bytes at 60Hz on a
            # loopback socket.
            info = {
                "diet": item.diet,
                # `meta` is the info box's right-hand slot, NOT "kcal" —
                # M6's broth and spice options put how hot it is there,
                # which is the number a diner is choosing between on that
                # screen. One field name so `drawInfoBox` can take a bin's
                # info or a widget's without caring which it got.
                "meta": f"{round(item.kcal_per_100g)} "
                        f"{self.locales.translate('kcal_per_100g', self.locale)}",
                "desc": item.description,
            }
        else:
            label, sub, price = "", "", 0.0
            # An unresolved bin has no item, so it has nothing true to say
            # about what is in it — blank, never a placeholder. oF draws no
            # box at all for this (UiLayer::drawInfoBox), which is doc
            # section 8's "Idle: invisible. No fill, no border. Not an
            # empty bordered box."
            info = {"diet": "", "meta": "", "desc": ""}
        # Doc section 5.3: "core pushes … stage-space rects to oF" — from
        # `self.projector_grid` (M4n), never `self.camera_grid`: that one
        # feeds the classifier and core's own hand hit test, and the two
        # grids are never derived from each other (core/bin_grid.py's
        # docstring). `None` until a human has put something in the
        # projector grid, same as it always has been — oF falls back to
        # TableGeometry.h's CAD layout rather than drawing eight plates at
        # the origin.
        stage_rect = self.projector_grid.rects()[i]
        return {
            "i": i,
            "label": label,
            "sub": sub,
            "rect": (None if stage_rect is None
                     else [round(v, 1) for v in stage_rect]),
            "grams": round(self.cart.live_g[i]),
            "picked": picked,
            "price": price,
            # Doc section 4.3's `hl`. **Hover outranks picked while the
            # hand is actually there**, and that ordering is the point of
            # the field: `picked` is a fact about the whole session and
            # stays true for the rest of it, while `hover` is live feedback
            # that the table has seen this hand right now. A bin the diner
            # has already taken from would otherwise stop responding to
            # them for the rest of the order.
            #
            # Hover never bills (doc section 9.4: "hover on a bin is
            # feedback only. It never bills. Billing is weight, always").
            # Nothing in this branch touches Cart.
            #
            # lowstock still needs a threshold nothing sets yet (doc
            # section 22, P3).
            "hl": ("hover" if self._hover_bin == i
                   else "picked" if picked > 0 else "none"),
            "stock": "ok",
            "resolved": resolved,
            "info": info,
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
    # The one config.load() call in this process (module docstring's
    # "hardcoded to the doc section 4.1 defaults" is now true of every port
    # except this pair) — camera/main.py is the only other reader, and
    # config.py's own docstring says a live system.json seeded from the
    # committed default deep-merges over it, so an operator can repoint the
    # Live tab at a different host without touching code.
    cfg = config.load()
    cam_cfg = config.get(cfg, "camera", {})
    core = start(
        # Resolved, not read straight through — see
        # `config.resolve_browser_host`. This one string is both the Live
        # tab's `<img>` host and (via `Core.receipt_url`) the host inside
        # the projected QR, and a diner's phone is the strictest reader of
        # the two.
        camera_host=config.resolve_browser_host(
            cam_cfg.get("host_for_browser", CAMERA_HOST)),
        camera_port=cam_cfg.get("mjpeg_port", CAMERA_PORT),
        # Doc section 8.6's tracker block, read here rather than by the
        # tracker itself: doc section 4.2 makes core the one holder of
        # every client's configuration.
        mirror_handedness=bool(config.get(cfg, "tracker.mirror_handedness",
                                          False)),
        emit_hz=float(config.get(cfg, "tracker.emit_hz", TRACKER_EMIT_HZ)),
        cursor_port=int(config.get(cfg, "cursor.core_port",
                                   cursorbus.CORE_PORT)),
        phantom_idle_s=float(config.get(cfg, "phantom.idle_s",
                                        PHANTOM_IDLE_S)),
        dwell_ms=float(config.get(cfg, "core.dwell_ms",
                                  hover.DEFAULT_DWELL_MS)),
        deadband_g=float(config.get(cfg, "core.deadband_g",
                                    cart.DEFAULT_DEADBAND_G)),
        classify_hz=float(config.get(cfg, "classifier.live_hz",
                                     CLASSIFIER_LIVE_HZ)),
        classify_enabled=bool(config.get(cfg, "classifier.enabled", True)),
    )
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
