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

from hotpot.classifier import ei_client, ei_store
from hotpot.common import atomicio, config, cursorbus, geometry, health, log, wire
from hotpot.core import (bin_grid, binmap, calibrator, cart, fsm,
                         geometry_store, hover, i18n, loadcell_cal, pricing,
                         scale)
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

# How long a cursor may go without a NEW datagram before core treats the
# pointer as gone rather than merely between frames (doc section 21's M5
# build item 4, found while verifying it on the rig — see _apply_cursor's
# docstring). Matches oF's own CursorLink::kCursorHoldSeconds so the table
# and core agree about when a hand is "still here".
POINTER_STALE_S = 0.35

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
        camera_host: str = CAMERA_HOST,
        camera_port: int = CAMERA_PORT,
        homography_path: Path = geometry_store.HOMOGRAPHY_PATH,
        camera_grid_path: Path = bin_grid.CAMERA_GRID_PATH,
        projector_grid_path: Path = bin_grid.PROJECTOR_GRID_PATH,
        view_rotation_path: Path = geometry_store.VIEW_ROTATION_PATH,
        mirror_handedness: bool = False,
        emit_hz: float = TRACKER_EMIT_HZ,
        cursor_port: int = cursorbus.CORE_PORT,
        dwell_ms: float = hover.DEFAULT_DWELL_MS,
        classify_hz: float = CLASSIFIER_LIVE_HZ,
        classify_enabled: bool = True,
        ei_project_path: Path = EI_PROJECT_PATH,
        models_dir: Path = MODELS_DIR,
        ei_client=ei_client,
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
        self.scale = scale.ScaleReader(scale_port, cal=self.cal,
                                       open_port=scale_open_port)
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
        # `scale_open_port` gives ScaleReader.
        self._ei_project_path = Path(ei_project_path)
        self._models_dir = Path(models_dir)
        self._ei_client = ei_client
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
        self._widgets: list = []

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
        # `fsm.serving`, not "not SETTING": doc section 9.1 makes serving
        # unreachable in UNCALIBRATED too, and a table that does not know
        # which tray is which must not weigh food out of one and charge
        # for it. One predicate, so a state added later cannot start
        # billing by omission.
        if not self.fsm.serving:
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
            for entry in bins:
                i = entry.get("i")
                if not isinstance(i, int) or not (0 <= i < binmap.NUM_BINS):
                    continue
                self.binmap.set_bin(
                    i, item_id=entry.get("label"),
                    conf=float(entry.get("conf") or 0.0),
                    source="classifier")

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
            self._pointer = frame.pointer()
            self._pointer_at = now

        pointer = self._pointer
        if (pointer is not None and self._pointer_at is not None
                and now - self._pointer_at > POINTER_STALE_S):
            # The stream has gone properly silent for a while — not just
            # "no new datagram this tick" but long enough that the tracker
            # itself is plausibly dead or the camera has gone stale (doc
            # section 6.4). A hover/dwell frozen on a hand that is
            # provably no longer being reported is worse than clearing it.
            # Matches oF's own `CursorLink::kCursorHoldSeconds` so the
            # table and core's own idea of "is a hand still here" agree.
            pointer = None
            self._pointer = None

        # Doc section 9.1's IDLE -> SELECTING edge, which has had no driver
        # since M1 (`fsm.hand_present()` existed with nothing calling it —
        # CLAUDE.md's M2.6 notes say so outright, and `_handle_cancel_order`
        # carries a fallback that becomes unreachable the moment this line
        # lands). Only a POINTER starts a session: a bowl set down on the
        # table must not open an order.
        if pointer is not None:
            self.fsm.hand_present()

        self._widgets = hover.widgets_for(
            selecting=self.fsm.state is fsm.State.SELECTING,
            locales_available=len(self.locales.available()))

        # `hover.bin_under` already answers None for a None hand (its own
        # docstring), so this runs unconditionally rather than duplicating
        # that check here — one place decides what "no pointer" means for
        # a hit test.
        was = self._hover_bin
        self._hover_bin = hover.bin_under(self.camera_grid.rects(), pointer)
        if self._hover_bin is not None and self._hover_bin != was:
            # Doc section 15.2's `hover`, "very soft tick, -18 dB". Sent
            # as a one-shot `evt` rather than riding `state`, because
            # `state` repeats at 60Hz and a repeated sound would fire
            # sixty times a second (doc section 4.4's whole rationale).
            self._send_evt({"t": "evt", "kind": "sound", "id": "hover"})

        fired = self.dwell.update(self._widgets, pointer, now)
        if fired is not None:
            self._fire_widget(fired)

    def _send_evt(self, msg: Dict[str, Any]) -> None:
        """Doc section 4.4's one-shot events. Fire-and-forget: "if oF misses
        one because it just restarted, nothing breaks."
        """
        self.control.broadcast(msg, only=["of"])

    def _fire_widget(self, widget_id: str) -> None:
        """A dwell completed. One dispatch table, so a widget that fires
        and does nothing is visible as a missing entry rather than as
        silence.

        Caller holds `state_lock`.
        """
        self._send_evt({"t": "evt", "kind": "sound", "id": "dwell_fire"})
        if widget_id == hover.CANCEL:
            # The same path the staff view's Cancel order button takes, not
            # a second implementation — doc section 9.1 has exactly one
            # `reset_session()` and this is one of its three callers.
            if not self.fsm.cancel() and self.cart.is_active():
                self.cart.reset_session()
            return
        if widget_id == hover.LANGUAGE:
            self._cycle_locale()
            return
        if widget_id == hover.DONE:
            # **Doc section 9.1's SELECTING -> BROTH edge is M6 and is not
            # invented here.** Doc section 21's M5 acceptance test asks only
            # that "the ring fills over 1.2s and fires", which it now does:
            # the dwell completes, the ring resets, and the sound event
            # above goes out. M6 build item 1 adds the BROTH/SPICE/RECAP/
            # CHECKOUT states and attaches them at this line.
            _log.info("core: Done fired (dwell complete). Checkout is M6 — "
                      "no state change yet.")
            return
        _log.warning("core: widget %r fired with nothing bound to it",
                     widget_id)

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
            out.append({
                "id": w.id,
                "kind": w.kind,
                "rect": [round(v, 1) for v in w.rect],
                "label": self.locales.translate(w.label_key, self.locale),
                "dwell": round(self.dwell.fraction(w.id), 3),
                "enabled": w.enabled,
                "style": w.style,
            })
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
        """Doc section 19.2's project link, done once per fresh clone (or
        once ever, since the real `hotpot-ingredients` project already
        exists — see `_handle_ei_link`'s reply message for how to adopt an
        existing project instead of creating a new one).

        Idempotent: an already-linked project is a no-op reporting the
        existing link, same contract the ported-from project's
        `EIController.link()` gives per device_type — here there is only
        ever the one project. The username/password/TOTP the tablet sends
        are used for exactly this one login() call and never stored — see
        `ei_client.py`'s and `ei_store.py`'s module docstrings.
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
        except Exception:      # noqa: BLE001 - never leave the tablet stuck
            # web/server.py's own outer catch-all would swallow anything
            # not caught here and log it, but with NO reply ever sent to
            # the tablet -- the button stays disabled and the status stays
            # "Linking..." forever, indistinguishable from a real hang.
            # Every _handle_ei_* method needs its own catch-all for
            # exactly that reason (classifier/main.py's `_run` worker loop
            # already makes this same call for the same reason).
            _log.exception("core: ei_link raised")
            self.web.broadcast({
                "t": "ei_link_result", "ok": False,
                "message": "linking to Edge Impulse hit an internal error — see the log"})
            return
        finally:
            self._ei_active = None

        ei_store.save_project(self._ei_project_path, project_id, api_key,
                              project_name)
        self.web.broadcast({
            "t": "ei_link_result", "ok": True, "linked": True,
            "project_id": project_id, "project_name": project_name,
            "message": (f"Created and linked {project_name!r}. Open it in "
                        "Edge Impulse Studio to configure the impulse "
                        "(image input, image DSP block, MobileNetV2 "
                        "transfer learning — doc §19.2) before the first "
                        "upload.")})

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
        self.web.broadcast({
            "t": "ei_upload_result", "ok": True,
            "uploaded": result["uploaded"], "failures": result["failures"],
            "message": (f"Uploaded {uploaded_total} image(s) to "
                        f"{project['project_name']!r}."
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
        stays a manual step too, same as it already is today — this
        replaces the "log into Studio, click Deployment, wait, click
        Download" half of the workflow, not the "unzip and rebuild" half.
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
        try:
            progress("building")
            job_id = self._ei_client.build_model(api_key, project_id)
            self._ei_client.wait_for_job(
                api_key, project_id, job_id,
                on_poll=lambda: progress("building"))

            progress("downloading")
            zip_bytes = self._ei_client.download_model(api_key, project_id)
        except ei_client.EIClientError as e:
            self.web.broadcast({"t": "ei_download_result", "ok": False,
                                "message": str(e)})
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

        dest = self._models_dir / f"{project['project_name']}.zip"
        atomicio.write_bytes(dest, zip_bytes)

        self.web.broadcast({
            "t": "ei_download_result", "ok": True, "path": str(dest),
            "message": (f"Downloaded {dest.name} ({len(zip_bytes)} bytes). "
                        f"Unzip it over tools/eim_cpp/vendor/ and rebuild "
                        "(tools/eim_cpp/CMakeLists.txt) to deploy it.")})

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
                # Doc section 12.3's hand markers, on the same divided
                # clock as the Bins tab and for the same reason: a tablet
                # over Wi-Fi does not need 60Hz to show where a hand is,
                # and the table already gets it at full rate over UDP.
                self.web.broadcast(self._hands_msg())
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
        else:
            label, sub, price = "", "", 0.0
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
        camera_host=cam_cfg.get("host_for_browser", CAMERA_HOST),
        camera_port=cam_cfg.get("mjpeg_port", CAMERA_PORT),
        # Doc section 8.6's tracker block, read here rather than by the
        # tracker itself: doc section 4.2 makes core the one holder of
        # every client's configuration.
        mirror_handedness=bool(config.get(cfg, "tracker.mirror_handedness",
                                          False)),
        emit_hz=float(config.get(cfg, "tracker.emit_hz", TRACKER_EMIT_HZ)),
        cursor_port=int(config.get(cfg, "cursor.core_port",
                                   cursorbus.CORE_PORT)),
        dwell_ms=float(config.get(cfg, "core.dwell_ms",
                                  hover.DEFAULT_DWELL_MS)),
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
