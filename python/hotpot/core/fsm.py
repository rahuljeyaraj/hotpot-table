"""core/fsm.py — the diner-facing state machine (doc section 9.1).

M1 build item 2 (doc section 21) scopes this to three states only: BOOT,
IDLE, SELECTING. Doc section 9.1's diagram has more — UNCALIBRATED lands
in M4 build item 6, once geometry_store.py exists to give BOOT something
to check for; STAFF, BROTH, SPICE, RECAP, CHECKOUT arrive with the
milestones that give them something to do (M2 and M7, per doc section
21). Adding a state means adding both a State member and a `_go(...)`
method below; nothing about the shape here is provisional scaffolding to
be replaced later.

Fsm does not own the cart. It calls into it, because doc section 9.1's
rule — "one shared function reset_session() is called from three places:
cancel, checkout completion, and staff-mode exit" — only holds if every
trigger that ends a diner session calls the *same* method rather than
each reimplementing the reset. Fsm takes a `Cart` in its constructor for
exactly that call; cancel() is the only one of the three that exists yet.
"""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from hotpot.core.cart import Cart

OnTransition = Callable[["State", "State"], None]


class State(enum.Enum):
    BOOT = "boot"
    IDLE = "idle"
    SELECTING = "selecting"


class Fsm:
    def __init__(self, cart: "Cart", *, on_transition: Optional[OnTransition] = None) -> None:
        self.cart = cart
        self.state = State.BOOT
        self._on_transition = on_transition

    def boot_complete(self) -> bool:
        """BOOT -> IDLE. Doc section 9.1: BOOT really goes to UNCALIBRATED
        first if homography.json or bin_rects.json is missing. That branch
        needs geometry_store.py (M4 build item 6) to check for those files
        and does not exist yet, so this always succeeds and always lands
        on IDLE until M4 replaces it.
        """
        return self._go(State.BOOT, State.IDLE)

    def hand_present(self) -> bool:
        """IDLE -> SELECTING: a hand arriving over the table."""
        return self._go(State.IDLE, State.SELECTING)

    def staff_start(self) -> bool:
        """IDLE -> SELECTING: the staff-view equivalent, doc section 9.1's
        'OR staff "start"'.
        """
        return self._go(State.IDLE, State.SELECTING)

    def cancel(self) -> bool:
        """SELECTING -> IDLE. Re-baselines and clears the cart (I6) through
        the one shared reset_session() — never inline that logic here, per
        doc section 9.1.
        """
        if self._go(State.SELECTING, State.IDLE):
            self.cart.reset_session()
            return True
        return False

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
        if self._on_transition is not None:
            self._on_transition(old, target)
        return True
