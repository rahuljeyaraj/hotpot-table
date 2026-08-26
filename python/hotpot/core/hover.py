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

# 2026-08-25. The reverse edge, offered on every screen after the cart —
# `fsm.back()` is what it fires and `fsm._BACK_EDGES` is where it goes.
#
# It has an id of its own rather than being "Cancel with a different
# label" because the two do opposite things: Back keeps the order and
# moves one screen, Cancel throws the order away. A diner who mixed those
# up would lose a cart they spent two minutes filling.
BACK = "back"

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

    # --- 2026-08-25: selection is a STATE now, not a page turn -----------
    #
    # Developer, verbatim: "each option button doesnt select and move to
    # the next page, instead hovering over it shows the info and when the
    # progress fills the the selection is locked even without hover. then
    # the info als remains locked. the user can still switch his option by
    # hovering on a different button, only when the button progress
    # completes the previous button gets unselected and this button get
    # selected."
    #
    # So a completed dwell on an option no longer advances anything — it
    # sets core's scratch choice, and core marks the chosen widget here on
    # the next tick. oF draws a selected plate differently and, crucially,
    # keeps the info box pinned to it once the hand moves away, which is
    # what "the info als remains locked" asks for.
    selected: bool = False
    # A glyph oF could draw itself, repeated `icon_count` times — the
    # mechanism the spice screen's chilli gauge used to be built on (doc
    # section 18.1's "four plates, 0-3, with chilli glyphs"). Deleted
    # 2026-08-25, later still: developer, "no need chilli icon... follow
    # exactly what is done with broth... just 3 boxes" — `UiLayer::
    # drawOptionPlate` no longer reads any of these three fields, and no
    # producer in this module sets them any more. Left on the wire rather
    # than removed outright, since it is generic (a name and a count, not
    # "chilli" specifically) and nothing downstream currently depends on
    # its absence — but there is no live caller left to demonstrate it.
    icon: str = ""
    icon_count: int = 0
    max_icon_count: int = 0

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

# **100 -> 76 tall, 16 -> 28 apart (2026-08-25).** Developer: "also make
# the buttons smallaer and increase the sapce between them, it feels
# crammed." Both numbers moved together on purpose — shrinking the button
# without widening the gap just leaves the same crowded row with more
# background around it, and the row has to hold THREE buttons now (Back,
# Cancel, Next) where it held two.
#
# 76px is about 64mm of plywood, and the narrowest a three-button row gets
# is (520 - 2*28) / 3 = 154.7px, i.e. ~123 x 64 mm. That is still far
# above the "a hand is not a mouse" floor the sizes below were written
# against — a MediaPipe landmark's frame-to-frame wander is a few px — and
# it is roughly a credit card, which is the smallest thing anyone reaches
# for with confidence.
BUTTON_H_PX = 76.0
BUTTON_GAP_PX = 28.0


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


# **THREE FIXED SLOTS, on every screen, whether or not all three are
# used.** The grid never re-centres and never closes a gap: a row with
# two buttons leaves the middle slot EMPTY and fills the two ends, so
# every button on this table sits on one of three x positions and the
# row reads as the same row from screen to screen.
#
# Developer, 2026-08-25, giving the row per screen outright:
#
#     cart      Cancel · —      · Next
#     broth     Back   · Cancel · Next
#     spice     Back   · Cancel · Pay
#     payment   Back   · —      · Cancel
#     paid      —      · —      · Done
#
# **A two-button row filling the ends is the developer's call and it
# costs something real — read this before "simplifying" any row back.**
# Dwell selection means a hand is resting on a button at the instant that
# button fires, and firing is what changes the screen. The three slots
# used to be role-fixed as well as position-fixed (Back 0, Cancel 1,
# forward 2), which made the crossing safe by geometry: the spice
# screen's Pay sat in slot 2 and the payment screen left slot 2 empty, so
# the hand came to rest on nothing. With Cancel in slot 2 on the payment
# screen, that hand now rests on Cancel instead — 1.2s from voiding the
# order it just placed. The cart screen has the mirror of it: Back sits
# in slot 0 on broth, and pressing it lands the hand on the cart screen's
# Cancel, also slot 0.
#
# **What stops both is `DwellTracker.suppress_until_exit`, and it is the
# only thing that does.** `core/main.py` calls it whenever the widget
# SHAPE changes — ids and rects, not just on a transition — which is
# exactly what both crossings are, so whatever is under the hand is
# disarmed until the hand actually leaves and comes back. That call is
# load-bearing now in a way it was not when the geometry also covered
# this; do not weaken its trigger to "a transition fired".
BUTTON_SLOTS = 3
SLOT_LEFT, SLOT_MIDDLE, SLOT_RIGHT = 0, 1, 2
# The old role names, kept because most rows still use the roles they
# describe (Back left, Cancel middle, the forward action right) and every
# reader of this module already knows them. They are POSITIONS, not
# promises about which button lands there — see the table above.
SLOT_BACK, SLOT_CANCEL, SLOT_FORWARD = SLOT_LEFT, SLOT_MIDDLE, SLOT_RIGHT


def button_slot_rects() -> List[Rect]:
    """The three slots, left to right, filling the cart's width."""
    x0, col_w = centre_column_px()
    left = x0 + (col_w - CART_WIDTH_PX) * 0.5
    btn_w = (CART_WIDTH_PX
             - BUTTON_GAP_PX * (BUTTON_SLOTS - 1)) / BUTTON_SLOTS
    return [(left + i * (btn_w + BUTTON_GAP_PX), BUTTONS_TOP_PX,
             btn_w, BUTTON_H_PX) for i in range(BUTTON_SLOTS)]


def button_span_centre(slots: int = 2) -> Rect:
    """One button, centred on the row, `slots` slots wide.

    **The single deliberate exception to the fixed-slot grid, and it is
    for a row with exactly one button in it.** Developer, 2026-08-25:
    "the last done button shout be center aligned and double width." The
    grid's rule (see `BUTTON_SLOTS`) is that a row never re-centres — a
    two-button row leaves the middle EMPTY and fills the ends, so buttons
    stay on the same three x positions from screen to screen. That rule
    exists so a diner's eye does not have to re-find the row; it says
    nothing useful about a screen whose row is one button, where there is
    no second button for it to line up with and nothing left to confuse
    it with.

    Width is `slots` slots plus the gaps between them, so the button is
    exactly as wide as the slots it spans — the row's own rhythm, not a
    new size invented for one screen.

    **This overlaps the slot the payment screen's Cancel sits in**, which
    is the crossing `BUTTON_SLOTS` warns about: a hand resting on Cancel
    when the payment lands is inside the new Done. Nothing about that is
    new — `DwellTracker.suppress_until_exit` already covers it, because
    `core/main.py` fires it on any change of widget SHAPE and this is one.
    """
    rects = button_slot_rects()
    btn_w = rects[0][2]
    width = btn_w * slots + BUTTON_GAP_PX * (slots - 1)
    x0, col_w = centre_column_px()
    left = x0 + (col_w - CART_WIDTH_PX) * 0.5
    return (left + (CART_WIDTH_PX - width) * 0.5, BUTTONS_TOP_PX,
            width, BUTTON_H_PX)


def button_row(ids: Sequence[Optional[str]]) -> Dict[str, Rect]:
    """Assign widget ids to the fixed slots, `None` for an empty one.

    `ids` is positional and must be `BUTTON_SLOTS` long: the index IS the
    slot, so a caller cannot accidentally shuffle Cancel into the forward
    position by passing a shorter list.

    **Reading order is reversible-first, committing-last.** Developer,
    2026-08-24: "cancel button should come first." With Back added that
    reads Back | Cancel | Next: the two ways out on the left, the one way
    forward on the right, which is where every checkout a diner has
    already used puts it.

    A row with two buttons passes `None` for the MIDDLE slot, never for
    an end one — see `BUTTON_SLOTS` for the developer's own table and for
    what that costs.
    """
    if len(ids) != BUTTON_SLOTS:
        raise ValueError(
            f"hover: button_row needs exactly {BUTTON_SLOTS} slots "
            f"(use None for an empty one), got {len(ids)}")
    rects = button_slot_rects()
    return {wid: rects[i] for i, wid in enumerate(ids) if wid is not None}


def layout(include_language: bool = False) -> Dict[str, Rect]:
    """The cart screen's own button rects: Cancel, then Next — and, once a
    second locale is actually loaded, Language in the middle slot that
    used to sit empty between them.

    **The ENDS of the row, with the middle slot empty** (developer,
    2026-08-25: "the button should be always fill the left and right slot
    if there is only 2 buttons"). Cancel was in the middle slot until
    then, with the Back slot empty on the argument that Back appears
    there on the very next screen — that argument is gone: a two-button
    row that leaves a hole on one side reads as a row missing a button
    rather than as a row of two. A THREE-button row (the `zh.json` case)
    fills all three slots instead, which is the same rule applied to a
    row that now genuinely has three things in it rather than two and a
    gap.

    Kept as a named function (rather than callers reaching for
    `button_row` directly) because it is the pair `UiLayer::setup()`'s
    cross-file check and this module's own tests measure against —
    `include_language` defaults to `False` so every existing caller of
    the bare two-button row is untouched.

    Done is NOT here. It was removed outright in 2026-08-13 (RIG_FEEDBACK
    items 4-7) and has nowhere to go until M6; the band it used to sit in
    (`BAND_TOP_PX`..`BAND_BOTTOM_PX`) is the cart's now. Those two
    constants and the three sizes above are kept because they record what
    was measured on the rig about that band — see `BAND_BOTTOM_PX`'s own
    comment, which is a finding, not a leftover.

    **Language WAS removed alongside Done in the same 2026-08-13 pass**,
    then brought back 2026-08-26 once `data/locales/zh.json` gave it
    somewhere to switch to (see `widgets_for`'s own gate on
    `locales_available`) — unlike Done, it never needed a new FSM edge to
    mean something, so nothing else about its 2026-08-13 removal applied
    to it once a second locale existed.
    """
    return button_row([CANCEL, LANGUAGE if include_language else None, CONFIRM])


# --- M6: the option list the BROTH and SPICE screens share ----------------
#
# Both screens are "pick one of four", so they get one layout function
# rather than two that would drift. The options stack VERTICALLY in the
# centre column, which is the only span on the table with no bin and no
# bin label in it (see the module docstring) — doc section 18.1 says
# "large projected plates" without saying where, and the near/far margins
# are where the plate labels already are.
#
# **They sit in the CART's band now, not in the free band above it, and
# that is the whole of the "broth overlays the cart" report.** Developer,
# 2026-08-25: "the broth should come like a second page of the selection
# with an option to go back to cart. now it overlays the cart and it is
# teribble... the buttons should only take the space which was previously
# once consumedby the cart are, the top info area should be left to there
# for broth info and in spicy page, spice info."
#
# The old band (BAND_TOP_PX..BAND_BOTTOM_PX = 350..820) straddled BOTH the
# info box and the cart, so four option plates landed on top of a cart that
# oF was still drawing underneath them. Moving them into exactly the cart's
# own rows band makes broth a genuine second PAGE: oF stops drawing the cart
# there (see `UiLayer::draw`) and the info band above is left free for the
# hovered or selected option's own info, which is what makes the "hover to
# read, dwell to lock" interaction legible at all.
#
# The band below mirrors `UiLayer.cpp`'s cart chain term for term —
# kNearRowBottomPx, kCartBottomGapPx, kCartFooterHeightPx,
# kCartRowHeightPx * 8 — for the same reason CART_WIDTH_PX is mirrored:
# one is Python and one is C++, they cannot share a constant, and
# `UiLayer::setup()` carries the check that warns when they drift.
_CART_BOTTOM_GAP_PX = 16.0     # UiLayer kCartBottomGapPx
_CART_FOOTER_H_PX = 92.0       # UiLayer kCartFooterHeightPx
_CART_ROW_H_PX = 32.0          # UiLayer kCartRowHeightPx
_CART_ROWS = 8


def _cart_band_px() -> Tuple[float, float]:
    """`(top, bottom)` in stage px of the space the cart occupies.

    Derived from the near row's own bottom edge, exactly as
    `UiLayer.cpp` derives `kCartTopPx` — so moving a bin moves the option
    list with it, the same rule the buttons and the cart already follow.
    """
    near_bottom = gs.mm_to_stage(
        0.0, gs.BIN_ORIGINS_MM[4][1] + gs.BIN_H_MM)[1]
    bottom = near_bottom - _CART_BOTTOM_GAP_PX
    top = bottom - _CART_FOOTER_H_PX - _CART_ROW_H_PX * _CART_ROWS
    return top, bottom


OPTIONS_TOP_PX, OPTIONS_BOTTOM_PX = _cart_band_px()

# **Narrower than the cart, on purpose — this is the whole of the "broth
# overlaps the halo" report.** Developer, 2026-08-25: "the broth buttons
# are too long and it overlaps with the halo, need to be made smaller."
# `OPTION_W_PX` used to just be `CART_WIDTH_PX` (520px), which the cart
# and its buttons can afford because nothing else sits in their band —
# but the option row sits in the SAME centre column as bins 1 and 2, and
# each bin's idle halo (`UiLayer.cpp`'s `drawHalo`) reaches
# `kHaloMarginPx` (14px) + `kHaloRingCount`(24) * `kHaloRingPitchPx`(1.5)
# = 50px past the bin's own edge into that column. 520px leaves only
# (554 - 520)/2 =~ 17px clear on each side — well inside the halo's own
# 50px reach. Mirrored here as `_HALO_REACH_PX`, the same reason
# `CART_WIDTH_PX` itself is mirrored in `UiLayer.cpp` and cannot share a
# constant with it (one is Python, the other C++).
_HALO_REACH_PX = 50.0
# A bit of daylight beyond the halo's own outer edge, so the row clears
# it rather than just touching it.
_HALO_CLEARANCE_PX = 10.0


def _option_w_px() -> float:
    _, col_w = centre_column_px()
    return col_w - 2.0 * (_HALO_REACH_PX + _HALO_CLEARANCE_PX)


OPTION_W_PX = _option_w_px()
# Shared by the spice chili-strip's cell gaps and the broth cards' row
# gaps (`spice_cell_rects`/`broth_card_rects`) — the two no longer share a
# per-item height (`OPTION_H_PX` was that shared height, and is gone with
# the function that used it, `option_rects` — see this constant's own git
# history if that reasoning is ever needed again).
OPTION_GAP_PX = 16.0

# --- the spice screen's own layout, 2026-08-25, later still ----------------
#
# Was a horizontal chili-strip row, then (same day) a vertical slider — a
# narrow chilli-gauge stop paired with its own description card per level.
# Both are gone. Developer: "no need chilli icon, no need slider which was
# never implemented, instead a 2 button was implemented, remove that and
# follow exactly what is done with broth do the same for spice boxes as
# well. just 3 boxes." The slider was never actually draggable — it was
# always two more dwellable RECTS per level, same as the chili-strip it
# replaced, just laid out vertically — so "the slider" is what the
# developer saw drawn, not a mechanism this module ever built.
#
# `spice_widgets` below now calls `broth_card_rects` directly: one
# full-width, full-height card per level, the same layout function and
# the same card shape `broth_widgets` already uses. No spice-specific
# layout function is left to own.


# --- the broth screen's own full-height cards, 2026-08-25 ------------------
#
# Developer: "there is no info box, instead the whole button is inlarged to
# contain the info about respective brothes, so u can use the complete
# vertical space above the next button row." One card per broth, stacked
# full-width (not a row of narrow columns like the spice chili-strip — a
# broth's `note` is a real sentence, and 130px-wide columns would wrap it
# into a dozen barely-readable lines where a ~434px-wide, ~185px-tall card
# fits it in two or three).
#
# **These three constants mirror `UiLayer.cpp`'s brand-block geometry and
# cannot share a constant with it (one is Python, the other C++) — same
# reasoning as `CART_WIDTH_PX`/`kCartWidthPx`.** They give the band's TOP:
# the same point `kInfoBoxTopPx` marks in oF, i.e. immediately below the
# brand mark, which is exactly the space broth cards now reclaim from the
# (no longer drawn, on this screen) shared info box.
_BRAND_TOP_MARGIN_PX = 20.0    # UiLayer kBrandTopMarginPx
_BRAND_HEIGHT_PX = 170.0       # UiLayer kBrandHeightPx
_BRAND_BANNER_GAP_PX = 26.0    # UiLayer kBrandBannerGapPx
_INFO_BOX_TOP_PX = (_BRAND_TOP_MARGIN_PX + _BRAND_HEIGHT_PX
                    + _BRAND_BANNER_GAP_PX)
# The page header's height (title + step dots) is measured at RUNTIME from
# the loaded font in oF (`UiLayer::_pageHeaderPx`) — this module has no font
# metrics to measure it with. 82px is a rounded-up safety margin over the
# 73.70px this title/font/gap combination measured on a real boot
# (2026-08-25 session log, after the header's breathing-room pass).
#
# **Guessing SHORT is not free after all, 2026-08-25.** The comment here
# used to say a few px of slack costs nothing because the cards are
# bottom-anchored — that is true of the cards' height but not of their
# TOP, and when the header grew (kBrandTopMarginPx/kBrandBannerGapPx/
# kStepDotsRowGapPx all went up) this estimate stayed at 72 and the step
# dots ended up drawn on top of the first card. Developer, photographed on
# the rig: "now the 5 dots are on top of the first box, u need to make the
# boxes smaller to give better breathing space at the top." Every one of
# these four numbers mirrors a UiLayer constant, so all four move together
# or this happens again.
_PAGE_HEADER_PX_ESTIMATE = 82.0
# Visible air between the step dots and the first card, on top of the
# header's own measured height. Same instruction as above: the dots
# clearing the card by a hairline is not "breathing space".
_HEADER_CLEARANCE_PX = 14.0

# The shortest a broth card can be and still hold its own content: one
# name line plus one diet/meta line (`UiLayer::drawOptionPlate`'s
# broth-card branch), with no room left for the note that is the whole
# point of the redesign. Unlike the old `option_rects`, this function
# scales card height to whatever fits `count` into the band rather than
# using one fixed height — which means an unbounded `count` would not
# overflow, it would just shrink every card toward nothing, just as
# invisible-in-a-diff a failure as the overflow `option_rects` guarded
# against. This is that same guard, aimed at the new failure mode.
_BROTH_CARD_MIN_H_PX = 80.0


def broth_card_rects(count: int) -> List[Rect]:
    """`count` cards, stacked top to bottom, spanning from just below the
    page header down to `OPTIONS_BOTTOM_PX` — the info box's old band AND
    the option row's old band, combined, since broth no longer shares the
    former with anything.

    Raises if a card would come out shorter than `_BROTH_CARD_MIN_H_PX` —
    see that constant for why that is the right failure mode here, unlike
    `option_rects`'s own fixed-height overflow check.
    """
    if count <= 0:
        return []
    top = _INFO_BOX_TOP_PX + _PAGE_HEADER_PX_ESTIMATE + _HEADER_CLEARANCE_PX
    bottom = OPTIONS_BOTTOM_PX
    band_h = bottom - top
    total_gap = OPTION_GAP_PX * (count - 1)
    card_h = (band_h - total_gap) / count
    if card_h < _BROTH_CARD_MIN_H_PX:
        raise ValueError(
            f"hover: {count} broth cards would be {card_h:.0f}px tall, "
            f"under the {_BROTH_CARD_MIN_H_PX:.0f}px floor — shrink the menu")
    x0, col_w = centre_column_px()
    left = x0 + (col_w - OPTION_W_PX) * 0.5
    return [(left, top + i * (card_h + OPTION_GAP_PX), OPTION_W_PX, card_h)
            for i in range(count)]


def _nav_row(*, forward_key: str, forward_enabled: bool) -> List[Widget]:
    """The button row every screen after the cart shares.

    Back | Cancel | <forward>, in the fixed slots (see `BUTTON_SLOTS`).
    `forward_key` is the locale key for the primary button's label, which
    is the ONLY thing that differs between screens: "next" on broth,
    "pay" on spice. The id stays CONFIRM throughout so `_fire_confirm` can
    keep dispatching on the FSM state rather than on which label the
    button happened to be wearing.
    """
    rects = button_row([BACK, CANCEL, CONFIRM])
    return [
        Widget(id=BACK, rect=rects[BACK], label_key="back",
               style="secondary", enabled=True),
        Widget(id=CANCEL, rect=rects[CANCEL], label_key="cancel",
               style="danger", enabled=True),
        Widget(id=CONFIRM, rect=rects[CONFIRM], label_key=forward_key,
               style="primary", enabled=bool(forward_enabled)),
    ]


def broth_widgets(broths: Sequence[Any], *,
                  selected_id: str = "", locale: Optional[str] = None) -> List[Widget]:
    """Doc section 18.1's BROTH screen: one full-height card per broth,
    plus the nav row (Back, Cancel, Next).

    **Next is disabled until a broth is locked in.** That is the visible
    half of the new selection model: dwelling a card marks it and does
    nothing else, so the only thing that can move the diner forward is a
    button whose own label says so. A Next that fired on nothing chosen
    would either skip the question or need a silent default, and a broth
    nobody picked is not a broth a kitchen should cook.

    **No swatch, 2026-08-25** — developer: "also the coloured circle
    infront of the broth name has to be removed." Nothing is passed for
    it any more (`Widget.swatch` defaults to `""`); `UiLayer::
    drawOptionPlate`'s broth-card branch never reads `w.swatch` at all,
    so this is the one place that removal actually lives.

    `broths` are `menu.Broth`es; typed loosely so this module does not
    import `menu` (it imports nothing of core's but `geometry_store`, and
    keeping it that way is what lets `test_hover` run with no data files).

    `locale` (2026-08-26) is passed straight through to `display_name()`
    and the new `meta_text()`/`note_text()` — this module still does not
    look anything up itself (see the module docstring's "keeps knowing
    nothing about the session"), it just hands the caller's locale to the
    objects that already know how to resolve themselves in it. `None`
    (the default) is every existing caller: `display_name(None)` and
    `meta_text(None)`/`note_text(None)` all fall back to English, same as
    before this parameter existed.
    """
    rects = broth_card_rects(len(broths))
    out = [
        Widget(id=broth_widget_id(b.id), rect=rect, label_key="",
               label=b.display_name(locale), kind="option", style="option",
               enabled=True,
               selected=(b.id == selected_id),
               info={"diet": b.diet, "meta": b.meta_text(locale),
                     "desc": b.note_text(locale)})
        for b, rect in zip(broths, rects)
    ]
    out.extend(_nav_row(forward_key="next",
                        forward_enabled=bool(selected_id)))
    return out


def spice_widgets(levels: Sequence[Any], *,
                  selected_level: Optional[int] = None,
                  locale: Optional[str] = None) -> List[Widget]:
    """Doc section 18.1's SPICE screen. Redesigned twice on 2026-08-25 (a
    horizontal chili-strip, then a vertical slider) and, later the same
    day, reverted to `broth_widgets`' own shape. Developer: "no need
    chilli icon, no need slider which was never implemented, instead a 2
    button was implemented, remove that and follow exactly what is done
    with broth do the same for spice boxes as well. just 3 boxes." One
    dwellable card per level (level 0 excluded — see below), full-width
    and full-height via `broth_card_rects` — the exact layout function
    `broth_widgets` already uses — plus the nav row (Back, Cancel, Pay).

    **Level 0 ("No Spice") is filtered out here, not in the data.**
    `menu.Menu.load` still requires it to exist in `data/menu.json` (doc
    section 17's genuine-no-spice guarantee), so the underlying menu is
    untouched; this is the one place that keeps it off the picker —
    unchanged from every version of this picker so far.

    Hottest first, top to bottom — the module docstring's "primary
    action nearest the diner" rule: Mild sits at the BOTTOM of the
    stack, closest to the nav row (the diner's own edge), Hot at the
    TOP.

    No `diet` on a spice level — it is not food.

    **The chilli gauge is back, 2026-08-25 (later still).** Developer:
    "in the spicy box, put one chilli in the mil right alighedn in same
    line as that of mild. then 2 chilli in medium and three in hot, all
    right alighned. chili icon should be bigger than the one u used
    before as it was not clear." So `icon_count` is the level itself —
    Mild 1, Medium 2, Hot 3 — and `UiLayer::drawOptionPlate` draws that
    many peppers right-aligned on the name's own line. `max_icon_count`
    is deliberately NOT set: the earlier design drew empty outline
    peppers up to the maximum as a gauge, and the instruction here is a
    plain count, not a scale.
    """
    ordered = sorted((s for s in levels if int(s.level) > 0),
                     key=lambda s: int(s.level))
    hottest_first = list(reversed(ordered))
    rects = broth_card_rects(len(hottest_first))
    out = [
        Widget(id=spice_widget_id(s.level), rect=rect, label_key="",
               label=s.display_name(locale), kind="option", style="option",
               enabled=True,
               selected=(selected_level is not None
                         and int(s.level) == int(selected_level)),
               icon="chilli", icon_count=int(s.level),
               info={"diet": "", "meta": "", "desc": s.note_text(locale)})
        for s, rect in zip(hottest_first, rects)
    ]
    out.extend(_nav_row(forward_key="pay",
                        forward_enabled=selected_level is not None))
    return out


def checkout_widgets(*, paid: bool = False) -> List[Widget]:
    """CHECKOUT — the payment screen, which is two screens in one.

    UNPAID: **Back and Cancel, and no forward button.** A forward here
    would be a way to clear the table without paying — pressed by mistake
    it leaves an unpaid order in the kitchen's queue and a diner walking
    off with food. Back voids the written order and returns to SPICE;
    Cancel voids it and ends the session. Both of those are core's to do
    (`core/main.py._fire_back` and `_dispatch_widget`).

    **The two of them fill the ENDS, so Cancel sits where the spice
    screen's Pay just was** (developer, 2026-08-25 — see `BUTTON_SLOTS`
    for the row table). This is the one crossing the old role-fixed grid
    was explicitly designed to make impossible, and it is now handled by
    `DwellTracker.suppress_until_exit` instead of by geometry: the hand
    resting on Cancel at the instant this screen arrives is disarmed
    until it leaves and comes back. Nothing else stands between a
    stationary hand and a voided order here.

    PAID: **Done, alone — centred, two slots wide.** Back is meaningless (nobody re-chooses a spice
    level for an order they have paid for) and Cancel would be worse than
    meaningless — it would offer to cancel money that has already
    changed hands, which this table cannot do. What is on screen is the
    token, and the only thing left is to take it.

    **Neither half times out.** Developer, 2026-08-25: "i see the qr code
    dissaperared when it was left idel for sometime, that should not
    happen, no time out. onc can cancell or go back, but not self
    disappear." The screen ends when a person presses one of these.
    """
    if paid:
        # Centred and two slots wide (`button_span_centre`), not parked in
        # the right-hand slot — developer, 2026-08-25. It is the only
        # thing on the screen a hand can press, and it sat off to one side
        # under a token that is itself centred.
        return [Widget(id=CONFIRM, rect=button_span_centre(), label_key="done",
                       style="primary", enabled=True)]
    rects = button_row([BACK, None, CANCEL])
    return [
        Widget(id=BACK, rect=rects[BACK], label_key="back",
               style="secondary", enabled=True),
        Widget(id=CANCEL, rect=rects[CANCEL], label_key="cancel",
               style="danger", enabled=True),
    ]


def widgets_for(*, selecting: bool, locales_available: int,
                cart_active: bool = False) -> List[Widget]:
    """The cart screen's Cancel and Next, always both, drawn in every
    non-checkout state.

    **2026-08-24, developer: "the confirm and cancell button didnt work and
    no progress of hover was shown."** They did not work because this
    function returned `[]` — the buttons on the table were static paint in
    `UiLayer::drawCart`, hit-tested against nothing. This is the fix: they
    are real widgets, so core hit-tests them and `DwellTracker` fills them,
    through the same path that has been tested since M5.

    **The primary button is labelled "Next", not "Confirm" (2026-08-25).**
    Its id is still CONFIRM (see `_nav_row`), but what it does from the
    cart screen is open the broth page, and a button that says Confirm on
    a screen that confirms nothing is the kind of thing that makes a
    first-time diner hesitate. Nothing on this table commits an order
    until the spice screen's Pay.

    **No Back here**, deliberately: the cart is the first screen of the
    chain and there is nothing behind it. `fsm.back()` returns False from
    SELECTING for the same reason.

    **Always returned, never conditionally absent** — doc section 8's cart
    "never moves" and a button that vanishes when the cart empties is the
    same broken promise as a row that does. `enabled` carries the state
    instead: with nothing picked there is nothing to cancel or go forward
    to, so both are disabled, which `DwellTracker` already refuses to
    accumulate on and `UiLayer::drawWidget` already greys out.

    `selecting`/`locales_available` are unchanged in shape (callers and
    tests do not move); `cart_active` is new and defaulted, so a caller
    that has not been updated gets the disabled pair rather than a crash.

    **`locales_available` now actually does something (2026-08-26).**
    Doc section 17.1: the projected Language button "is offered only when
    there is somewhere to switch TO" (see `i18n.Locales.available`'s own
    docstring) — with one locale file loaded it stays absent, exactly as
    `test_locale_count_does_not_resurrect_language` pins; with two it
    takes the cart row's middle slot, which has sat empty since Cancel
    moved to the left end (2026-08-25). It is NOT gated by `cart_active`:
    which language the table speaks is not something to hold hostage to
    whether a diner has picked anything yet.
    """
    show_language = locales_available >= 2
    rects = layout(include_language=show_language)
    enabled = bool(cart_active)
    out = [
        Widget(id=CANCEL, rect=rects[CANCEL], label_key="cancel",
               style="danger", enabled=enabled),
    ]
    if show_language:
        # 2026-08-26, developer: put "EN | 中文" on the button itself,
        # both sides, rather than a translated word that only ever shows
        # the CURRENT language back at the diner. A literal `label`
        # (never `label_key="language"` now — see Widget's own docstring
        # on why `label` wins) so it reads the same in either locale;
        # `UiLayer::drawWidget` special-cases any mixed-script label to
        # draw the ASCII and CJK halves in their own fonts, since no
        # single locale-selected font this table loads carries both.
        out.append(Widget(id=LANGUAGE, rect=rects[LANGUAGE],
                          label_key="", label="EN | 中文",
                          style="secondary", enabled=True))
    out.append(Widget(id=CONFIRM, rect=rects[CONFIRM], label_key="next",
                      style="primary", enabled=enabled))
    return out


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

    def suppress_until_exit(self, widgets: Sequence[Widget],
                            hand: Optional[cursorbus.Hand]) -> None:
        """Arm the re-arm latch on whatever the pointer is inside RIGHT
        NOW, and clear the accumulator.

        **Called when the SCREEN changes, and it is the general form of
        the guarantee the fixed button grid gives by geometry.** A dwell
        fires with the hand still on the button — that is what dwell
        means — and firing is what changes the screen. So at the instant a
        new widget set arrives, the hand is sitting on top of it, having
        chosen none of it. Whatever is under that hand must not start
        filling until the diner has actually moved and come back.

        `update`'s ordinary latch covers this only while the id under the
        hand is unchanged. This covers the case where it is not — a
        different button now occupies that spot, or the same rect now
        carries a different meaning — which is precisely the case that can
        cost a diner their order (see `BUTTON_SLOTS` for the crossing
        that made it concrete).

        A no-op when the hand is outside everything, which is the common
        case: nothing to suppress.
        """
        self.active_id = None
        self.accumulated_ms = 0.0
        self._left_at = None
        self._fired_id = None
        if hand is None:
            return
        for widget in widgets:
            if widget.enabled and widget.contains(hand.x, hand.y):
                self._fired_id = widget.id
                return

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
