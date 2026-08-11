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

from typing import List

NUM_BINS = 8

# Doc section 8.6 default. A constructor default rather than read from
# config/system.json for the same reason as binmap.DEFAULT_CONF_FLOOR:
# config loading is not built yet (see common/stub.py's docstring).
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
        function called from cancel, checkout completion, and staff-mode
        exit, once each of those exists to call it. Never re-derive this
        logic at a call site.
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

    def set_live_grams(self, i: int, grams: float) -> None:
        """The one entry point for 'the scale (or its mock) now reads
        this'. Applies the doc section 9.2 display deadband: shown_g
        SNAPS to the true removed grams once the gap reaches deadband_g —
        it does not creep toward it, and it does not move at all below
        that gap. Small picks accumulate silently and are shown in one
        jump. This is I5's snap version; do not shrink it to "ignore
        events under the deadband", which throws picks away.
        """
        self._check_bin(i)
        self.live_g[i] = max(0.0, grams)
        removed = self.removed_grams(i)
        if abs(removed - self.shown_g[i]) >= self.deadband_g:
            self.shown_g[i] = removed

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

    def mock_pick(self, i: int, grams: float) -> None:
        """Developer-panel mock (doc section 12.8): `grams` leave bin i."""
        self._check_bin(i)
        self.set_live_grams(i, self.live_g[i] - grams)

    def mock_putback(self, i: int, grams: float) -> None:
        """Developer-panel mock, reversed. No refund branch anywhere (I4)
        — this raises live_g back, and the lower price falls out of the
        same subtraction that produced the higher one.
        """
        self._check_bin(i)
        self.set_live_grams(i, self.live_g[i] + grams)

    def finalize(self) -> None:
        """Order finalisation: shown_g snaps to the true removed grams for
        every bin, unconditionally, regardless of the deadband — doc
        section 9.2's fix for open debt #5 (a diner must never be shown a
        recap that disagrees with the arithmetic they were actually
        charged from).
        """
        for i in range(NUM_BINS):
            self.shown_g[i] = self.removed_grams(i)
