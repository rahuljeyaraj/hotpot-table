"""core/hover.py — doc section 9.4's hover and dwell, and the widget layout
they hit-test against (doc section 21, M5 build item 4).

Doc section 9.4 in full:

    Core receives stage-space cursors from the tracker and hit-tests them
    against stage-space bin rects and widget rects.
    - Only `role == "pointer"` hands are hit-tested. Ambient hands are
      ignored entirely for selection.
    - Dwell: a widget accumulates dwell time while the pointer is
      continuously inside it. Leaving resets to 0 after a 150ms grace.
    - Default dwell to fire: 1200ms. Configurable.
    - Core sends `dwell` as a 0..1 fraction so oF can draw a filling ring.
      oF does not time anything.
    - Hover on a *bin* is feedback only. It never bills.

Pure: no sockets, no clock of its own, no `Core`. `now` is passed in, the
same discipline `tracker/tracking.py` follows and for the same reason —
"a dwell fires at 1200ms and not at 900ms" should be a test, not a
stopwatch on a rig.

**Ambient hands are dropped at the door, in `pick_pointer`, not filtered
at each hit test.** Doc section 11.4 wants the isolation "at the
consumer", and one function that no ambient hand gets past is a much
stronger guarantee than remembering to check the role at every call site.
There are three call sites today and there will be more at M6.

Where the widgets are, and why there
-------------------------------------
Doc section 4.3's example rect is `[1480,880,380,140]` — the near-right
margin. **That is not where they go, and the reason is measured rather
than aesthetic.** The near margin is where `UiLayer::drawBin` draws every
near-row bin's name and price, downward from the ring: a two-line name
puts ink from about y=890 to y=1010, straight through that example rect.
The doc's number predates any label ever having been measured in a bin —
the same class of thing as section 13.4's 36px, which M2.6g had to correct
after looking at the real table.

So widgets live in the **centre column**: the 440mm pot gap between bin 1's
right edge and bin 2's left edge, which is the one horizontal span on the
table with no bin and no label in it *by construction*. That is already
this codebase's own established answer — `UiLayer::drawBanner` moved there
for exactly this reason, and the brand mark and the running total are
there too. The column's top holds the brand and the banner and its bottom
holds the total; the widgets take the free band between them, with the
primary action nearest the diner.

Rects are derived from `TableGeometry.h`'s chain (mirrored in
`geometry_store`) rather than hardcoded, so moving a bin moves the buttons
with it — the same rule `drawBanner` follows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from hotpot.common import cursorbus
from hotpot.core import geometry_store as gs

Rect = Tuple[float, float, float, float]

# Doc section 9.4's two numbers. `DWELL_MS` is "long enough not to misfire
# and short enough not to feel broken"; `GRACE_MS` is what stops one
# jittery frame from throwing away a nearly-complete dwell.
DEFAULT_DWELL_MS = 1200.0
DEFAULT_GRACE_MS = 150.0

# Doc section 4.3's `widgets[].id` values that exist at M5 (build item 4:
# "Widgets: Done, Cancel, Language").
DONE = "done"
CANCEL = "cancel"
LANGUAGE = "language"

# VISUAL_LAYER.md section 8's own pair, 2026-08-24: "Two buttons below the
# cart: Confirm, Cancel." `CONFIRM` is deliberately a NEW id rather than a
# relabelled `DONE`: doc section 9.1's SELECTING -> BROTH edge is M6's, and
# `DONE` is the name that edge is written against everywhere in this repo.
# Confirm is the cart's own button and does what the cart can honestly do
# today (see `core/main.py._fire_widget`) — conflating the two would make
# M6's arrival a rename instead of an addition.
CONFIRM = "confirm"

# M6. Option widgets carry their choice in the id itself — `broth:mala`,
# `spice:2` — so `_fire_widget` reads the choice off the id it was handed
# rather than needing a parallel lookup of "which option was at index 3
# when this layout was built". The layout is rebuilt every tick from the
# menu, so an index would be a second thing to keep in step.
BROTH_PREFIX = "broth:"
SPICE_PREFIX = "spice:"


def broth_widget_id(broth_id: str) -> str:
    return BROTH_PREFIX + broth_id


def spice_widget_id(level: int) -> str:
    return SPICE_PREFIX + str(int(level))


def parse_broth_id(widget_id: str) -> Optional[str]:
    if widget_id.startswith(BROTH_PREFIX):
        return widget_id[len(BROTH_PREFIX):] or None
    return None


def parse_spice_level(widget_id: str) -> Optional[int]:
    if not widget_id.startswith(SPICE_PREFIX):
        return None
    try:
        return int(widget_id[len(SPICE_PREFIX):])
    except ValueError:
        return None


@dataclass
class Widget:
    """One dwellable target, in stage space.

    `label` is **already resolved** — doc section 4.3: "label and text are
    already resolved strings in the current locale. oF does no lookup."
    This dataclass carries the i18n *key* instead (`label_key`), and core
    resolves it on the way out, because the layout has to be describable
    without a locale table in scope.
    """

    id: str
    rect: Rect
    label_key: str
    kind: str = "button"
    style: str = "primary"
    enabled: bool = True
    # M6's option widgets carry their own label and info box content
    # instead of an i18n key, because a broth's name lives in
    # `data/menu.json` and is already localised there — resolving it
    # through the locale table would need every broth name copied into
    # every locale file, which is exactly the duplication `menu.json`'s
    # own `names` dict exists to avoid. `label_key` stays the route for
    # the fixed chrome (Cancel, Confirm), which genuinely is UI text.
    label: str = ""
    # {"diet","meta","desc"} — the info box's content while this widget is
    # hovered, same shape as a bin's. Empty for a widget with nothing to
    # say, and the info box then simply does not appear.
    info: Dict[str, str] = field(default_factory=dict)
    # Doc section 18.1's "colour swatch each", hex, "" for no swatch.
    swatch: str = ""

    def contains(self, x: float, y: float) -> bool:
        rx, ry, rw, rh = self.rect
        return rx <= x <= rx + rw and ry <= y <= ry + rh


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def centre_column_px() -> Tuple[float, float]:
    """`(x, width)` of the pot gap in stage pixels — bin 1's right edge to
    bin 2's left edge, derived rather than hardcoded (see the module
    docstring).
    """
    left_mm = gs.BIN_ORIGINS_MM[1][0] + gs.BIN_W_MM
    right_mm = gs.BIN_ORIGINS_MM[2][0]
    x0 = gs.mm_to_stage(left_mm, 0.0)[0]
    x1 = gs.mm_to_stage(right_mm, 0.0)[0]
    return x0, x1 - x0


# The free band between the banner block (brand + banner, which end around
# y=320 in UiLayer's own layout) and the running total. Stated as stage px
# because both neighbours are: the brand mark's height is developer-tuned
# in px and the total's baseline is a px offset from the near edge, so
# converting to mm here would only add a round trip that hides the
# adjacency this has to respect.
#
# **BAND_BOTTOM_PX was 900 and it was wrong — found on the rig, not by
# arithmetic.** `UiLayer::drawTotal`'s label sits at a baseline computed
# from `_totalNumFont.getAscenderHeight()`, a FreeType metric this module
# has no access to (it lives in oF, in C++, resolved at font-load time).
# The value used to derive 900 assumed a modest ascender; a real screenshot
# of the projected table (M5 build item 3's verification pass) showed the
# Done button's ring overlapping the word "Total" by several pixels — the
# same class of gap doc section 0 warns about generally: a number reasoned
# out in code and never checked against the thing it draws next to. 820
# leaves a wide margin instead of a tight, font-metric-dependent one, so
# this stays correct even if the total's font or its size changes later.
BAND_TOP_PX = 350.0
BAND_BOTTOM_PX = 820.0

# --- the cart's own two buttons (VISUAL_LAYER.md section 8) --------------
#
# **`CART_WIDTH_PX` and `BUTTON_H_PX` are mirrored in
# `of/hotpot-table/src/UiLayer.cpp` and cannot share a constant — one is
# Python, the other C++.** They are the seam between core, which owns the
# rect a hand is hit-tested against (doc section 9.4), and oF, which lays
# the cart out directly above them. `UiLayer::setup()` carries the matching
# check: it measures the cart's own bottom and logs a warning if it has
# grown down into `BUTTONS_TOP_PX`. Move one side without the other and the
# buttons collide with the total, or float away from the cart — visible on
# the table, invisible in a diff.
#
# **`BUTTONS_TOP_PX` is DERIVED now, not chosen.** Developer, 2026-08-24:
# "the buttons should be vertically center alligned in the space below the
# near row bottom edge and the bottom edge of the table." That band is the
# 177.4mm near margin, and both of its edges are already in
# `geometry_store`'s mm chain — so this follows the same rule the rest of
# this module does (see the module docstring: rects are derived from
# `TableGeometry.h`'s chain rather than hardcoded, so moving a bin moves
# the buttons with it).
# 500 -> 520 (2026-08-25), to stop the cart truncating long item names.
# **Mirrored in UiLayer.cpp's kCartWidthPx** — see the block comment above
# on why the two cannot share a constant, and UiLayer::setup()'s own check
# on what breaks if they drift.
CART_WIDTH_PX = 520.0
BUTTON_H_PX = 100.0
BUTTON_GAP_PX = 16.0


def _near_margin_px() -> Tuple[float, float]:
    """`(top, bottom)` in stage px of the band between the near row's
    bottom edge and the table's own — the diner's margin.
    """
    near_bottom_mm = gs.BIN_ORIGINS_MM[4][1] + gs.BIN_H_MM
    return (gs.mm_to_stage(0.0, near_bottom_mm)[1],
            gs.mm_to_stage(0.0, gs.TABLE_H_MM)[1])


def _buttons_top_px() -> float:
    top, bottom = _near_margin_px()
    return top + (bottom - top - BUTTON_H_PX) * 0.5


BUTTONS_TOP_PX = _buttons_top_px()

# Button sizes, largest for the primary action. Dwell targets have to be
# comfortably bigger than the cursor's own wander — a hand is not a mouse,
# and landmark 9 moves a few px per frame even on a still hand — so these
# are far larger than a tablet's 44px rule. `DONE` at 440x150 px is about
# 350x127 mm on the plywood.
DONE_SIZE = (440.0, 150.0)
SECONDARY_SIZE = (360.0, 110.0)
TERTIARY_SIZE = (260.0, 90.0)


def _centred(width: float, height: float, y: float) -> Rect:
    x0, col_w = centre_column_px()
    return (x0 + (col_w - width) * 0.5, y, width, height)


def layout() -> Dict[str, Rect]:
    """The cart's two button rects, side by side directly under the cart.

    **CANCEL is on the LEFT, CONFIRM on the right** — developer, 2026-08-24:
    "cancel button should come first." Read left-to-right that puts the
    reversible action first and the committing one last, which is the
    order every checkout a diner has already used puts them in.

    Done/Language are NOT here. They were removed outright in 2026-08-13
    (RIG_FEEDBACK items 4-7) and nothing has brought them back; the band
    they used to sit in (`BAND_TOP_PX`..`BAND_BOTTOM_PX`) is the cart's
    now. Those two constants and the three sizes above are kept because
    they record what was measured on the rig about that band — see
    `BAND_BOTTOM_PX`'s own comment, which is a finding, not a leftover.
    """
    x0, col_w = centre_column_px()
    left = x0 + (col_w - CART_WIDTH_PX) * 0.5
    btn_w = (CART_WIDTH_PX - BUTTON_GAP_PX) * 0.5
    return {
        CANCEL: (left, BUTTONS_TOP_PX, btn_w, BUTTON_H_PX),
        CONFIRM: (left + btn_w + BUTTON_GAP_PX, BUTTONS_TOP_PX, btn_w, BUTTON_H_PX),
    }


# --- M6: the option list the BROTH and SPICE screens share ----------------
#
# Both screens are "pick one of four", so they get one layout function
# rather than two that would drift. The options stack VERTICALLY in the
# centre column, which is the only span on the table with no bin and no
# bin label in it (see the module docstring) — doc section 18.1 says
# "large projected plates" without saying where, and the near/far margins
# are where the plate labels already are.
#
# Sized to fit: the free band is BAND_TOP_PX..BAND_BOTTOM_PX = 470px, and
# 4 * OPTION_H + 3 * OPTION_GAP = 448, leaving 22px of slack. A fifth
# broth would not fit, and `option_rects` raises rather than silently
# overflowing the band into the cart below it.
OPTION_W_PX = CART_WIDTH_PX
OPTION_H_PX = 100.0
OPTION_GAP_PX = 16.0


def option_rects(count: int) -> List[Rect]:
    """`count` stacked rects, centred in the free band.

    Raises if they will not fit. A silent overflow here would put a
    dwellable button on top of the cart, i.e. a hand reaching for the
    total would choose a broth — the kind of thing that is obvious on the
    table and invisible in a diff, so it fails loudly at layout time.
    """
    if count <= 0:
        return []
    total_h = count * OPTION_H_PX + (count - 1) * OPTION_GAP_PX
    band_h = BAND_BOTTOM_PX - BAND_TOP_PX
    if total_h > band_h:
        raise ValueError(
            f"hover: {count} options need {total_h:.0f}px but the centre "
            f"band is only {band_h:.0f}px — shrink OPTION_H_PX or the menu")
    x0, col_w = centre_column_px()
    left = x0 + (col_w - OPTION_W_PX) * 0.5
    top = BAND_TOP_PX + (band_h - total_h) * 0.5
    return [(left, top + i * (OPTION_H_PX + OPTION_GAP_PX),
             OPTION_W_PX, OPTION_H_PX) for i in range(count)]


def _cancel_only() -> Widget:
    """Cancel, alone, on the button row.

    The checkout screens put Cancel where Cancel has been the whole
    session rather than moving it — a diner who has already used it once
    on this table should not have to find it again.
    """
    return Widget(id=CANCEL, rect=layout()[CANCEL], label_key="cancel",
                  style="danger", enabled=True)


def broth_widgets(broths: Sequence[Any]) -> List[Widget]:
    """Doc section 18.1's BROTH screen: one plate per broth, plus Cancel.

    `broths` are `menu.Broth`es; typed loosely so this module does not
    import `menu` (it imports nothing of core's but `geometry_store`, and
    keeping it that way is what lets `test_hover` run with no data files).
    """
    rects = option_rects(len(broths))
    out = [
        Widget(id=broth_widget_id(b.id), rect=rect, label_key="",
               label=b.display_name(), kind="option", style="option",
               enabled=True, swatch=b.swatch,
               info={"diet": b.diet, "meta": b.meta, "desc": b.note})
        for b, rect in zip(broths, rects)
    ]
    out.append(_cancel_only())
    return out


def spice_widgets(levels: Sequence[Any]) -> List[Widget]:
    """Doc section 18.1's SPICE screen: one plate per level, plus Cancel.

    No `diet` on a spice level — it is not food. The info box draws the
    diet mark only when there is one rather than drawing a blank dot.
    """
    rects = option_rects(len(levels))
    out = [
        Widget(id=spice_widget_id(s.level), rect=rect, label_key="",
               label=s.display_name(), kind="option", style="option",
               enabled=True,
               info={"diet": "", "meta": s.meta, "desc": s.note})
        for s, rect in zip(levels, rects)
    ]
    out.append(_cancel_only())
    return out


def recap_widgets() -> List[Widget]:
    """Doc section 18.1's RECAP: "dwell confirm". Same two buttons and the
    same two rects as SELECTING — the diner has been looking at them all
    session and this is the screen where Confirm finally commits.
    """
    rects = layout()
    return [
        Widget(id=CANCEL, rect=rects[CANCEL], label_key="cancel",
               style="danger", enabled=True),
        Widget(id=CONFIRM, rect=rects[CONFIRM], label_key="confirm",
               style="primary", enabled=True),
    ]


def checkout_widgets() -> List[Widget]:
    """CHECKOUT. One button, and it is not Cancel.

    The order is written and the code is assigned by the time this screen
    is up, so there is nothing left to cancel — offering it would suggest
    the diner could still call the order off, which is not true. Doc
    section 18.3's 90s timeout ends this screen on its own; this is the
    "I have finished reading the code" shortcut, and it does exactly what
    the timeout does.
    """
    rects = layout()
    return [Widget(id=CONFIRM, rect=rects[CONFIRM], label_key="done",
                   style="primary", enabled=True)]


def widgets_for(*, selecting: bool, locales_available: int,
                cart_active: bool = False) -> List[Widget]:
    """The cart's Cancel and Confirm, always both, drawn in every state.

    **2026-08-24, developer: "the confirm and cancell button didnt work and
    no progress of hover was shown."** They did not work because this
    function returned `[]` — the buttons on the table were static paint in
    `UiLayer::drawCart`, hit-tested against nothing. This is the fix: they
    are real widgets, so core hit-tests them and `DwellTracker` fills them,
    through the same path that has been tested since M5.

    That reverses part of RIG_FEEDBACK items 4-7 (2026-08-13), and only
    that part, deliberately. Those three were removed because none had a
    product decision behind it — but VISUAL_LAYER.md section 8 makes this
    pair part of the cart itself, and the developer has now asked for them
    to work. `DONE` and `LANGUAGE` stay removed: Done still has nowhere to
    go until M6, and there is still only one locale file.

    **Always returned, never conditionally absent** — doc section 8's cart
    "never moves" and a button that vanishes when the cart empties is the
    same broken promise as a row that does. `enabled` carries the state
    instead: with nothing picked there is nothing to confirm or cancel, so
    both are disabled, which `DwellTracker` already refuses to accumulate
    on and `UiLayer::drawWidget` already greys out.

    `selecting`/`locales_available` are unchanged in shape (callers and
    tests do not move); `cart_active` is new and defaulted, so a caller
    that has not been updated gets the disabled pair rather than a crash.
    """
    rects = layout()
    enabled = bool(cart_active)
    return [
        Widget(id=CANCEL, rect=rects[CANCEL], label_key="cancel",
               style="danger", enabled=enabled),
        Widget(id=CONFIRM, rect=rects[CONFIRM], label_key="confirm",
               style="primary", enabled=enabled),
    ]


# ---------------------------------------------------------------------------
# Hit testing
# ---------------------------------------------------------------------------

def pick_pointer(frame: Optional[cursorbus.CursorFrame]
                 ) -> Optional[cursorbus.Hand]:
    """The one hand allowed to select, or None.

    Doc section 11.4: core "discards [ambient hands] entirely before
    hit-testing". This is that discard, and it is the ONLY route a hand
    takes into any hit test below — see the module docstring on why it is
    one door rather than a check repeated at each call site.
    """
    if frame is None:
        return None
    return frame.pointer()


def bin_under(rects: Sequence[Optional[Rect]], hand: Optional[cursorbus.Hand]
              ) -> Optional[int]:
    """Index of the bin the pointer is over, or None.

    `rects` are the **camera** grid's, not the projector grid's — see
    `core/bin_grid.py`: "This is the grid MediaPipe, the classifier's crop,
    and core's hand-entered-bin hit test all read." A consequence worth
    recognising rather than debugging: the ring that lights up is drawn on
    the *projector* grid, so if a human has set the two grids differently
    the highlight appears slightly off the hand. That is inherent in two
    independently-authored grids and is the price of neither being derived
    from the other.

    An unset bin (`None`) is skipped rather than treated as the whole
    table; first match wins, and the grid's own construction means two bins
    cannot overlap.
    """
    if hand is None:
        return None
    for i, rect in enumerate(rects):
        if rect is None:
            continue
        rx, ry, rw, rh = rect
        if rx <= hand.x <= rx + rw and ry <= hand.y <= ry + rh:
            return i
    return None


# ---------------------------------------------------------------------------
# Dwell
# ---------------------------------------------------------------------------

@dataclass
class DwellTracker:
    """One accumulating dwell at a time, because a pointer is one point.

    Deliberately not a per-widget dictionary of timers: with one cursor
    only one widget can ever be accumulating, and a dictionary of them
    invites a version where two rings fill at once because a stale entry
    was never cleared.
    """

    dwell_ms: float = DEFAULT_DWELL_MS
    grace_ms: float = DEFAULT_GRACE_MS

    active_id: Optional[str] = None
    accumulated_ms: float = 0.0
    # When the pointer left `active_id`. None while it is inside. This is
    # what the grace is measured from, and it is stamped ONCE on leaving
    # rather than refreshed per frame — refreshing it would make the grace
    # never expire and a dwell never reset.
    _left_at: Optional[float] = None
    _last_now: Optional[float] = None
    # The widget that just fired, held until the pointer LEAVES it.
    #
    # Resetting the accumulator on fire is not enough on its own, and a
    # test caught that: the hand is still inside the widget on the very
    # next tick, so the accumulator starts filling again and the widget
    # fires a second time 1200ms later, and again, for as long as a hand
    # rests there. Done leads to a state change, so a diner who simply did
    # not move their hand would be walked through the whole checkout flow.
    # Re-arming requires an actual exit.
    _fired_id: Optional[str] = None

    def update(self, widgets: Sequence[Widget],
               hand: Optional[cursorbus.Hand], now: float) -> Optional[str]:
        """Advance the clock. Returns the widget id that FIRED this tick,
        or None.

        Fires at most once per crossing: the accumulator resets on fire, so
        a hand left resting on Done does not re-fire every 1200ms. That is
        not a nicety — Done leads to a state change, and a diner whose hand
        has not moved would otherwise walk the whole checkout flow.
        """
        dt = 0.0 if self._last_now is None else max(0.0, now - self._last_now)
        self._last_now = now

        inside = None
        if hand is not None:
            for widget in widgets:
                # A disabled widget is not a dwell target. It still draws
                # (doc section 4.3's `enabled`), so the diner can see it
                # exists and is not available, but a ring that filled and
                # then did nothing would be worse than no ring at all.
                if widget.enabled and widget.contains(hand.x, hand.y):
                    inside = widget.id
                    break

        # The re-arm latch. Cleared the instant the pointer is anywhere
        # else — including nowhere at all — so leaving and coming back
        # works normally.
        if self._fired_id is not None:
            if inside == self._fired_id:
                self._left_at = None
                return None
            self._fired_id = None

        if inside is not None and inside == self.active_id:
            self._left_at = None
            self.accumulated_ms += dt * 1000.0
        elif inside is not None:
            # A different widget (or the first one). Start fresh — dwell
            # accumulated over Cancel must never count toward Done.
            self.active_id = inside
            self.accumulated_ms = dt * 1000.0
            self._left_at = None
        else:
            # Outside everything, or no pointer at all. Doc section 9.4:
            # "leaving resets to 0 after a 150ms grace (so a jittery frame
            # does not reset a nearly-complete dwell)". So the accumulator
            # is HELD, not cleared, until the grace expires.
            if self.active_id is not None:
                if self._left_at is None:
                    self._left_at = now
                elif (now - self._left_at) * 1000.0 >= self.grace_ms:
                    self.reset()

        if self.active_id is not None and self.accumulated_ms >= self.dwell_ms:
            fired = self.active_id
            self.reset()
            self._fired_id = fired
            return fired
        return None

    def fraction(self, widget_id: str) -> float:
        """Doc section 9.4's "0..1 fraction in `state.widgets[].dwell` so oF
        can draw a filling ring. oF does not time anything."
        """
        if widget_id != self.active_id or self.dwell_ms <= 0:
            return 0.0
        return max(0.0, min(1.0, self.accumulated_ms / self.dwell_ms))

    def reset(self) -> None:
        """Clears the accumulator, NOT the re-arm latch — `update` sets
        `_fired_id` immediately after calling this on a fire, and the latch
        is cleared only by the pointer actually leaving.
        """
        self.active_id = None
        self.accumulated_ms = 0.0
        self._left_at = None
