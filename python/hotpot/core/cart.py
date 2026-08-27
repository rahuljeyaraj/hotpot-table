"""core/cart.py — per-bin session weights (doc sections 2/I6, 9.1, 9.2).

Cart owns exactly three arrays of 8 floats: start_g, live_g, shown_g.
Every other pricing number — removed grams, per-bin price, the running
total — is *derived* from these by pricing.py and is never stored, per
I4: price is cumulative and absolute, computed fresh from two absolute
weights, never accumulated from individual pick events. There is no
running total anywhere in this file; call pricing.total() again whenever
a fresh number is needed.

Where live_g comes from is not this module's business. M1 sets it from
the developer panel's mock pick/put-back controls (doc section 12.8); M2
build item 5 adds core/scale.py's median-of-5 serial reading as a second
source. Ordinary updates from either source go through set_live_grams()
— the one entry point that also drives the display deadband (doc section
9.2), so neither caller has to know the snap rule exists.

The one thing set_live_grams() cannot do is *introduce* a source: the
first real scale reading for a bin arrives while start_g is still
wherever the M1 mock seed (or a previous mock pick) left it, and running
that gap through the ordinary removed_grams subtraction would price the
distance between a fictional mock weight and a real one as one enormous
phantom pick. seed_live_grams() exists for exactly that one moment —
main.py calls it once per bin, the first time core/scale.py reports a
real, non-None weight for it, and set_live_grams() for every reading
after that.
"""

from __future__ import annotations

from typing import List, Optional

NUM_BINS = 8

# Doc section 8.6's default was 10.0. Dropped to 5.0 on 2026-08-25 (see
# git history) after a rig report that a first sub-10g scoop wasn't
# showing up in the cart at all. Back to **10.0 on 2026-08-26**, on the
# developer's own explicit instruction — no new rig report behind this
# one, just a direct "make it 10".
#
# The floor is still load-cell noise: CLAUDE.md's per-channel table has
# four bins at ~750-1500 counts rms, and `is_active()` below is the
# predicate that goes permanently true if the deadband ever falls under
# that noise — which would make setting mode unreachable, the exact
# failure M2.6 chose 10 g to avoid in the first place. 10 g is comfortably
# clear of it.
#
# A real config key (`core.deadband_g`, read in `main()` and threaded
# through `Core.__init__`), not just a constant — config/system.json and
# config/system.default.json both carry the same value.
DEFAULT_DEADBAND_G = 10.0


class Cart:
    def __init__(self, *, deadband_g: float = DEFAULT_DEADBAND_G) -> None:
        self.deadband_g = deadband_g
        self.start_g: List[float] = [0.0] * NUM_BINS
        self.live_g: List[float] = [0.0] * NUM_BINS
        self.shown_g: List[float] = [0.0] * NUM_BINS

    def _check_bin(self, i: int) -> None:
        if not (0 <= i < NUM_BINS):
            raise IndexError(f"bin {i} out of range 0..{NUM_BINS - 1}")

    def reset_session(self) -> None:
        """I6: re-baseline, never re-tare — `start_g[i] = live_g[i]`.
        Nothing becomes zero. Doc section 9.1: this is the one shared
        function called from cancel, checkout completion, and setting-mode
        exit, once each of those exists to call it. Never re-derive this
        logic at a call site.

        Setting-mode exit has an extra requirement this method cannot
        enforce on its own: live_g must be refreshed from the scale
        *first*, or a tray swapped during setting mode gets baselined at
        its old weight. fsm.exit_setting() owns that ordering — read its
        docstring before calling this from any new place.
        """
        for i in range(NUM_BINS):
            self.start_g[i] = self.live_g[i]
            self.shown_g[i] = 0.0

    def removed_grams(self, i: int) -> float:
        """I4's absolute-weight difference. Never negative — a bin that
        gained weight (put-back, or food added) has removed nothing, per
        'no put-back branch' (I4): the refund is this subtraction, not a
        separate code path.
        """
        self._check_bin(i)
        return max(0.0, self.start_g[i] - self.live_g[i])

    def is_active(self) -> bool:
        """"Is a diner part-way through an order?" — doc section 9.1's
        "staff enter, cart empty" precondition, which fsm.py's
        can_enter_setting() is the only caller of so far.

        **shown_g, NOT removed_grams().** This is the single easiest thing
        to get wrong here and the failure is not subtle. `removed_g =
        max(0, start_g - live_g)` is raw and moves with load-cell noise —
        CLAUDE.md's per-channel table has four bins sitting at 500-1500
        counts rms, which at a plausible counts/gram is several grams of
        permanent wobble. Reading that raw number would make this true
        essentially always on a noisy channel and **setting mode would be
        permanently unreachable**, since an active cart is the one thing
        that refuses entry.

        shown_g is deadband-gated (set_live_grams above), so it is 0 until
        a real pick of `deadband_g` or more lands, and it is also exactly
        what the diner can see on the table — refusing to enter setting
        mode because of something visible is explicable to an operator;
        refusing because of invisible noise is not.

        Accepted cost: a sub-deadband pick (under `deadband_g`) reads as
        inactive and is discarded by exit's re-baseline. That is a few
        cents and invisible on the table, against a mode that could not
        be entered at all.

        **This is the predicate that bounds how far `deadband_g` can
        fall** — see DEFAULT_DEADBAND_G's own block. Below the load
        cells' own noise this is true on an untouched table and setting
        mode becomes unreachable.
        """
        return any(g > 0.0 for g in self.shown_g)

    def set_live_grams(self, i: int, grams: float) -> Optional[float]:
        """The one entry point for 'the scale (or its mock) now reads
        this'. Applies the doc section 9.2 display deadband: shown_g
        SNAPS to the true removed grams once the gap reaches deadband_g —
        it does not creep toward it, and it does not move at all below
        that gap. Small picks accumulate silently and are shown in one
        jump. This is I5's snap version; do not shrink it to "ignore
        events under the deadband", which throws picks away.

        Returns the signed change in `shown_g` (positive: more removed,
        i.e. a pick; negative: less removed, i.e. a put-back) the instant
        it snaps, or None on a tick that moved nothing visible.
        """
        self._check_bin(i)
        self.live_g[i] = max(0.0, grams)
        removed = self.removed_grams(i)
        prev_shown = self.shown_g[i]
        if abs(removed - prev_shown) >= self.deadband_g:
            self.shown_g[i] = removed
            return removed - prev_shown
        return None

    def seed_live_grams(self, i: int, grams: float) -> None:
        """M2 build item 5: the one-time hand-off from a mock/placeholder
        weight to a bin's first real scale reading.

        Sets start_g, live_g AND shown_g around `grams`, as if this bin's
        session had just begun — the same three-field shape as
        reset_session(), but for one bin instead of all eight, and driven
        by a real weight instead of whatever live_g already held. Call
        this exactly once per bin, the first time it has one (main.py
        tracks that per bin); every reading after that is an ordinary
        set_live_grams() call, same as a mock pick.
        """
        self._check_bin(i)
        g = max(0.0, grams)
        self.start_g[i] = g
        self.live_g[i] = g
        self.shown_g[i] = 0.0

    def mock_pick(self, i: int, grams: float) -> Optional[float]:
        """Developer-panel mock (doc section 12.8): `grams` leave bin i."""
        self._check_bin(i)
        return self.set_live_grams(i, self.live_g[i] - grams)

    def mock_putback(self, i: int, grams: float) -> Optional[float]:
        """Developer-panel mock, reversed. No refund branch anywhere (I4)
        — this raises live_g back, and the lower price falls out of the
        same subtraction that produced the higher one.
        """
        self._check_bin(i)
        return self.set_live_grams(i, self.live_g[i] + grams)

    def finalize(self) -> None:
        """Order finalisation: shown_g snaps to the true removed grams for
        every bin, unconditionally, regardless of the deadband — doc
        section 9.2's fix for open debt #5 (a diner must never be shown a
        recap that disagrees with the arithmetic they were actually
        charged from).
        """
        for i in range(NUM_BINS):
            self.shown_g[i] = self.removed_grams(i)
