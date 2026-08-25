"""core/fsm.py — the table's state machine (doc section 9.1).

BOOT, IDLE, SELECTING (M1 build item 2), SETTING (M2.6), UNCALIBRATED
(M4 build item 6) and, since M6, the checkout chain: BROTH, SPICE,
CHECKOUT. Adding a state means adding both a State member and a
transition method below; nothing about the shape here is provisional
scaffolding to be replaced later.

**RECAP is gone, 2026-08-25.** It was a fifth state between SPICE and
CHECKOUT whose whole screen was "the cart again, with Confirm". The
developer's own page list for the table is four screens — cart, broth,
spice, payment — and a diner who has just been shown the cart on screen
one does not need it re-shown on screen four before the QR. What RECAP
actually carried was the *commit*, and that moved onto `confirm()`'s
SPICE -> CHECKOUT edge unchanged: the order is still written on exactly
one transition, and `weighing` still freezes the cart for every screen
after SELECTING. Deleted outright rather than left unreachable, this
codebase's usual rule.

**Every screen in the chain can now go BACKWARD as well as out**, which
is `back()` below and is new in the same change. Cancel was the only
edge out before, so a diner who picked the wrong broth had to throw the
whole order away to fix it.

**Two predicates, not one, since M6: `serving` and `weighing`.** They had
the same answer while IDLE and SELECTING were the only serving states,
and they stopped having it the moment a diner could be mid-checkout. Read
both before gating anything new on either.

The old docstring here scheduled the mode state for "M2 and M7". That was
never true of M2's build items — none of them mention it — and M7 build
items 3-4 were the first that functionally needed it, five milestones
after `core/main.py` started hardcoding `"mode": "diner"` on the wire.
M2.6 is the milestone that corrects that, and it is also where the mode
stopped being called `STAFF`: `SERVING`/`SETTING` name what the table is
*doing* (billing, or being changed) rather than who is standing at it —
staff are present in both — and "staff mode" collided with "staff view",
the tablet UI used in both modes.

Fsm does not own the cart, the bin map, or the scale. It calls into them,
because doc section 9.1's rules — "one shared function reset_session() is
called from three places: cancel, checkout completion, and setting-mode
exit" and "setting-mode exit additionally locks the bin map" — only hold
if every trigger that ends a session calls the *same* code rather than
each reimplementing it. Fsm takes a `Cart` and a `BinMap` in its
constructor for exactly that, so no caller can do two of exit's three
steps and forget the third. `refresh_weights` is the third of them and is
a callback rather than a `ScaleReader`, so this module still knows
nothing about serial ports — see exit_setting() for why the order of
those three steps is the load-bearing part.
"""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from hotpot.core.binmap import BinMap
    from hotpot.core.cart import Cart

OnTransition = Callable[["State", "State"], None]
RefreshWeights = Callable[[], None]
IsCalibrated = Callable[[], bool]


class State(enum.Enum):
    BOOT = "boot"
    UNCALIBRATED = "uncalibrated"
    IDLE = "idle"
    SELECTING = "selecting"
    # Doc section 9.1's checkout chain, M6, minus RECAP (see the module
    # docstring). SELECTING -> BROTH -> SPICE -> CHECKOUT -> IDLE, with a
    # `back()` edge along every arrow in it.
    BROTH = "broth"
    SPICE = "spice"
    CHECKOUT = "checkout"
    SETTING = "setting"


# The states between "the diner stopped picking" and "the order is
# done". Grouped because three separate places need exactly this set and
# spelling it out at each would let them drift: the scale gate below, the
# widget layout in `core/hover.py`, and the cart-freeze in
# `core/main.py._apply_scale_to_cart`.
CHECKOUT_STATES = (State.BROTH, State.SPICE, State.CHECKOUT)

# Where `back()` goes from each screen — one table, so a screen cannot
# end up with a Back button that does nothing, and so the reverse chain
# is readable in one place next to the forward one. CHECKOUT goes back to
# SPICE rather than to a screen of its own: the diner's last decision was
# the spice level, and that is where "no, wait" should land them.
#
# **CHECKOUT -> SPICE voids the order that was already written.** That is
# core's job, not this module's (fsm.py owns no database, the same reason
# it owns neither the cart nor the bin map) — see
# `core/main.py._fire_back`, which is the only caller allowed to take
# this particular edge.
_BACK_EDGES = {
    State.BROTH: State.SELECTING,
    State.SPICE: State.BROTH,
    State.CHECKOUT: State.SPICE,
}


class Fsm:
    def __init__(self, cart: "Cart", binmap: "BinMap", *,
                 on_transition: Optional[OnTransition] = None,
                 refresh_weights: Optional[RefreshWeights] = None,
                 is_calibrated: Optional[IsCalibrated] = None) -> None:
        self.cart = cart
        self.binmap = binmap
        self.state = State.BOOT
        self._on_transition = on_transition
        self._refresh_weights = refresh_weights
        # A callable, not a bool, and not a GeometryStore. Not a bool
        # because the answer has to be asked again at every exit from
        # setting mode rather than sampled once at construction — an
        # operator who calibrates during setting mode changes it. Not a
        # GeometryStore because this module knows nothing about state
        # files, the same reason `refresh_weights` is a callback rather
        # than a ScaleReader.
        #
        # `None` means "nothing to check", which is M1 through M3's
        # behaviour and is kept so this class stays constructible on its
        # own. Core always passes one.
        self._is_calibrated = is_calibrated

    def calibrated(self) -> bool:
        return self._is_calibrated is None or bool(self._is_calibrated())

    @property
    def serving(self) -> bool:
        """Whether the table is open for business — the mode predicate.

        Doc section 9.1: "In UNCALIBRATED, serving mode is unreachable." A
        table with no homography has no idea which tray is which. BOOT is
        excluded for the reason it always was — nothing is loaded yet.

        **This is no longer the scale gate.** It was, up to M6, when IDLE
        and SELECTING were the only serving states and the two questions
        had the same answer. They do not any more: a table on the broth
        screen is very much serving a diner, and must not be weighing.
        See `weighing`.
        """
        return self.state in (State.IDLE, State.SELECTING) or \
            self.state in CHECKOUT_STATES

    @property
    def weighing(self) -> bool:
        """Whether the scale may still move the cart.

        **The cart freezes the moment the diner presses Next on the cart
        screen, and this is the predicate that freezes it.** Everything
        from BROTH onward shows the diner numbers they are being asked to
        approve — a hand brushing a tray while they choose a broth, or
        the load cells drifting a gram while the QR is up, must not
        change what they already agreed to.

        **Going BACK to SELECTING un-freezes it, deliberately.** `back()`
        from BROTH lands on SELECTING, which is in this tuple, so the
        scale drives the cart again — that is the whole point of the edge:
        the diner returned to the cart to change what is in it.

        A predicate rather than `state is SELECTING` for the same reason
        `serving` is one: a state added later cannot start billing by
        omission.
        """
        return self.state in (State.IDLE, State.SELECTING)

    def boot_complete(self) -> bool:
        """BOOT -> IDLE, or BOOT -> UNCALIBRATED.

        Doc section 9.1: "BOOT always goes to UNCALIBRATED if
        `homography.json` or `bin_rects.json` is missing… This is the
        first-boot path and it must work on a fresh clone with an empty
        `state/`." Both files, and both complete — `GeometryStore` counts
        seven rects and a hole as uncalibrated, because the eighth bin
        would otherwise render from a fallback nobody chose.
        """
        if not self.calibrated():
            return self._go(State.BOOT, State.UNCALIBRATED)
        return self._go(State.BOOT, State.IDLE)

    def calibration_complete(self) -> bool:
        """UNCALIBRATED -> IDLE, doc section 9.1's "(calibration
        complete)" edge.

        Refuses if the geometry still is not there, rather than trusting
        the caller: core calls this after any geometry write, and a write
        that saved a homography but no rects must not open the table.
        No-ops from every other state, so it is safe to call after every
        save without asking where the FSM is first.
        """
        if self.state is not State.UNCALIBRATED:
            return False
        if not self.calibrated():
            return False
        return self._go(State.UNCALIBRATED, State.IDLE)

    def hand_present(self) -> bool:
        """IDLE -> SELECTING: a hand arriving over the table.

        Unreachable from UNCALIBRATED by construction — `_go` refuses any
        source state but IDLE — which is what "serving mode is
        unreachable" means in practice: a diner can wave at an
        uncalibrated table all day and nothing starts.
        """
        return self._go(State.IDLE, State.SELECTING)

    def staff_start(self) -> bool:
        """IDLE -> SELECTING: the staff-view equivalent, doc section 9.1's
        'OR staff "start"'.

        `staff` here means a *person* pressing Start on the tablet, and
        has nothing to do with the mode — which is why this name survived
        M2.6's rename untouched. Naming the mode SETTING is what makes
        this unambiguous; do not re-litigate it (M2.6 plan, section 4).
        """
        return self._go(State.IDLE, State.SELECTING)

    def cancel(self) -> bool:
        """-> IDLE from SELECTING or from anywhere in the checkout chain.
        Re-baselines and clears the cart (I6) through the one shared
        reset_session() — never inline that logic here, per doc 9.1.

        **Reachable from BROTH/SPICE/CHECKOUT too, which doc section
        9.1's diagram does not draw.** The diagram has no edge out of
        those but the last one, and that cannot be right in a restaurant:
        a diner three screens into a checkout they did not mean to start
        would have no way out at all now that CHECKOUT no longer times
        itself out (see `core/main.py` — the developer asked for the QR
        to stay up until a person acts). Cancel is offered on every one
        of those screens (`core/hover.py`), so the FSM has to accept it
        there.
        """
        if self.state is not State.SELECTING and self.state not in CHECKOUT_STATES:
            return False
        old = self.state
        self.state = State.IDLE
        self.cart.reset_session()
        self._fire(old, State.IDLE)
        return True

    # -- the checkout chain (doc section 9.1, section 18.1 — M6) -----------

    def done(self) -> bool:
        """SELECTING -> BROTH, doc section 9.1's `dwell "done"` edge —
        the cart screen's own Next button.

        Refuses on an empty cart. Nothing else in the chain checks it, so
        this is the one gate between "a hand rested on Next" and a
        zero-total order written to the database with a code a diner
        would then be asked to pay.
        """
        if not self.cart.is_active():
            return False
        return self._go(State.SELECTING, State.BROTH)

    def broth_chosen(self) -> bool:
        """BROTH -> SPICE — the broth screen's Next button.

        **Named for the fact, not for the button, and that distinction
        moved in 2026-08-25's redesign.** Choosing a broth used to BE this
        transition: dwelling a plate both recorded the choice and jumped
        to the next screen, so a diner could not see what they had picked
        or change their mind. Selection is now core's own scratch state
        (`core/main.py._choose_broth`) and this edge fires only when the
        diner presses Next, which is what makes "hover a different plate
        to switch" possible at all.

        Which broth was chosen stays core's to remember — this module
        owns the state, not the order's contents, the same way it owns
        neither the cart nor the bin map.
        """
        return self._go(State.BROTH, State.SPICE)

    def confirm(self) -> bool:
        """SPICE -> CHECKOUT, doc section 9.1's `dwell "confirm"` edge —
        the spice screen's Pay button, and the commit.

        Was RECAP -> CHECKOUT until 2026-08-25; RECAP is deleted and this
        edge absorbed it whole (see the module docstring). The order is
        still written by core on exactly this one transition.

        Writing that row is deliberately NOT done here: writing to SQLite
        from inside a state machine that is unit-tested with no
        filesystem would drag a database into every FSM test, the same
        reason `refresh_weights` is a callback.
        """
        return self._go(State.SPICE, State.CHECKOUT)

    def back(self) -> bool:
        """One screen backward along the chain, per `_BACK_EDGES`.

        A single method with a table rather than three `back_to_x()`
        methods, for the reason the reverse chain exists at all: a Back
        button is offered on every screen after the first, so the set of
        legal reverse edges has to be readable in one place or a screen
        will eventually get a button with no edge behind it.

        No-ops (returns False) from SELECTING, IDLE and everywhere else —
        there is nothing behind the cart screen, and the cart screen's own
        button row offers no Back for exactly that reason.
        """
        target = _BACK_EDGES.get(self.state)
        if target is None:
            return False
        old = self.state
        self.state = target
        self._fire(old, target)
        return True

    def checkout_complete(self) -> bool:
        """CHECKOUT -> IDLE, doc section 9.1's "(receipt fetched)" edge,
        with its "[re-baseline, clear cart]".

        **The "OR timeout 90s" half of that edge is gone.** Developer,
        2026-08-25: "i see the qr code dissaperared when it was left idel
        for sometime, that should not happen, no time out. onc can cancell
        or go back, but not self disappear." A diner reaching for their
        phone is exactly the person that timer was firing on.

        This is the third of doc section 9.1's three `reset_session()`
        callers ("cancel, checkout completion, and setting-mode exit") and
        the only one that had no caller until M6.
        """
        if self._go(State.CHECKOUT, State.IDLE):
            self.cart.reset_session()
            return True
        return False

    # -- setting mode (doc section 9.1, M2.6) ------------------------------

    def can_enter_setting(self) -> Optional[str]:
        """None if allowed; otherwise a plain-language reason (doc 12.1).

        A reason string rather than a bare False because doc section 9.1
        requires it: "The staff view shows *why* it is refused and offers
        'cancel the order first.'" `_go()`'s bool cannot carry that, so
        the refusal check is its own method and the caller can put the
        answer straight on the wire.

        The one refusal doc section 9.1 names is an active cart — "one
        wrong keypress must not destroy a diner's order." Active means
        `cart.is_active()`, which reads the *deadbanded* shown grams, not
        raw removed grams; see cart.py for why the raw version would make
        this mode unreachable on a noisy load cell.
        """
        if self.cart.is_active():
            return "An order is in progress — food has already been taken from the table."
        return None

    def enter_setting(self) -> bool:
        """any -> SETTING, per doc section 9.1's "any (staff enter, cart
        empty)". Not `_go()`: this one legal source state is *every*
        state, so there is nothing to compare against.

        Returns False without a reason when already in SETTING — that is
        an ordinary no-op (two tablets tapping at once), not a refusal.
        Callers that need to tell the two apart ask can_enter_setting()
        first, which is what core/main.py's set_mode handler does.
        """
        if self.state is State.SETTING:
            return False
        if self.can_enter_setting() is not None:
            return False
        old = self.state
        self.state = State.SETTING
        self._fire(old, State.SETTING)
        return True

    def exit_setting(self) -> bool:
        """SETTING -> IDLE, doing all three of doc section 9.1's exit
        steps in this order, inside Fsm so no caller can do two of them
        and forget the third:

        1. **Refresh live_g from the scale for every bin.**
        2. `cart.reset_session()` — I6's re-baseline.
        3. Lock the bin map (doc section 8.2: `locked` is true in serving
           mode, false while setting mode is live-updating).

        **Step 1 is not optional, and leaving it out mis-bills silently.**
        core/main.py's `_apply_scale_to_cart()` does nothing at all while
        this state is live, which is the entire point of the mode — so by
        the time exit runs, `live_g` still holds whatever the bins weighed
        when setting mode was *entered*. `reset_session()` does
        `start_g[i] = live_g[i]`. Swap a full tray for an empty one during
        setting mode — again, the entire point of the mode — and without
        step 1, exit baselines `start_g` to the old full tray's weight
        while the next state tick sets `live_g` to the new empty one.
        `removed_g` becomes the whole tray and the next diner is billed
        for the swap.

        This is the eight-bin version of what the (now deleted) per-bin
        `cal_end` freeze did with `cart.seed_live_grams()`.

        The state change and the transition callback come last, after all
        three steps, so nothing observing the transition can see a
        half-exited table.
        """
        if self.state is not State.SETTING:
            return False
        if self._refresh_weights is not None:
            self._refresh_weights()
        self.cart.reset_session()
        self.binmap.locked = True
        # **Back to UNCALIBRATED, not IDLE, on a table that still has no
        # geometry.** Doc section 9.1's diagram writes this edge as
        # SETTING -> IDLE, which is right for the ordinary case and wrong
        # for the first-boot one: calibration is a setting-mode activity,
        # so the operator is IN setting mode while doing it, and an exit
        # that always landed on IDLE would open a table that has no idea
        # which tray is which. Asked again here rather than remembered
        # from boot, because the whole point of the mode being left is
        # that the operator may have just fixed it.
        nxt = State.IDLE if self.calibrated() else State.UNCALIBRATED
        self.state = nxt
        self._fire(State.SETTING, nxt)
        return True

    def _go(self, expected: State, target: State) -> bool:
        """Apply the transition only if `expected` is the current state.

        Returns False rather than raising when it is not: a hand arriving
        while core is still in BOOT is ordinary traffic racing startup,
        not a bug, and every trigger method above is a no-op from any
        state doc section 9.1 does not name for it.
        """
        if self.state is not expected:
            return False
        old = self.state
        self.state = target
        self._fire(old, target)
        return True

    def _fire(self, old: State, new: State) -> None:
        if self._on_transition is not None:
            self._on_transition(old, new)
