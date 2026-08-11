"""core/loadcell_cal.py — load-cell calibration maths and its state file.

Doc section 9.6 (the maths) and doc section 8.3 (`state/loadcell_cal.json`).

This module is deliberately free of the serial port. Everything here is
arithmetic over counts that somebody else captured, plus an atomic write —
which is what makes the one number in this system that can *silently
mis-bill* (atomicio.py's own docstring names it) testable without a XIAO
plugged in. `core/scale.py` owns the port and the 2s capture windows; it
hands the counts it collected to `tare()` and `calibrate()` here.

Why `counts_per_gram` is signed
-------------------------------
Doc section 9.6, and it is the whole reason this module refuses to expose
counts to anyone. Several cells are mounted upside down, so their counts
fall as mass is added. The sign falls out of the two-point subtraction on
its own:

    counts_per_gram = (loaded_counts - zero_counts) / ref_mass_g

and `grams()` divides by it, so an inverted cell reads correctly with no
special case anywhere. **The operator is never asked about sign, mounting
or orientation** (doc section 21, M2's "Do NOT"). There is no place in
this API to tell it, on purpose.

Tare is not I6's re-baseline
----------------------------
Two different zeroes, and conflating them would be expensive:

    tare() here      moves the *load cell's* zero — a setup action, run
                     against an empty bin, persisted to disk, survives
                     restarts. It is about the hardware.
    Cart.reset_session()  moves the *session's* baseline — I6's
                     "re-baseline, never re-tare", run between diners,
                     never touches this file. It is about the order.

A tare mid-session would silently zero out food a diner already has in
their bowl. Nothing in this module is reachable from the diner-facing
path; the Bins tab (doc section 12.4) is its only caller.

Missing file versus corrupt file
--------------------------------
`load()` on a missing file returns eight uncalibrated bins — that is a
first boot (doc section 9.1), and it is normal. A file that exists and
does not parse raises, per atomicio.py: an uncalibrated bin reads no
grams at all and is loudly wrong, whereas a half-read calibration prices
food incorrectly and nobody notices.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from hotpot.common import atomicio

log = logging.getLogger("hotpot.loadcell_cal")

CAL_SCHEMA = 3
NUM_BINS = 8

# Doc section 9.6's sanity check: "if abs(counts_per_gram) < 10 the cell is
# probably not connected or the reference mass was too light. Refuse and say
# so." For scale, the cells measured on this rig sit in the hundreds of
# counts per gram, so 10 is far below anything a working cell produces and
# is not at risk of rejecting a genuine calibration.
MIN_COUNTS_PER_GRAM = 10.0


class CalibrationError(Exception):
    """A calibration that failed doc section 9.6's sanity check.

    Carries a message written for the operator, not for the log: doc
    section 12.4 puts it straight on the Bins tab, and that screen is
    forbidden from mentioning counts, sign or orientation.
    """


@dataclass
class BinCal:
    """One cell's calibration. `counts_per_gram is None` ⇒ uncalibrated."""

    i: int
    zero_counts: float = 0.0
    counts_per_gram: Optional[float] = None
    calibrated_at: Optional[float] = None
    ref_mass_g: Optional[float] = None
    noise_counts_rms: float = 0.0

    @property
    def calibrated(self) -> bool:
        return self.counts_per_gram is not None and self.counts_per_gram != 0.0

    def grams(self, counts: Optional[float]) -> Optional[float]:
        """Doc section 9.6 line 3. None out when this cell has never been
        calibrated, or when the caller had no counts to give — never 0.0.

        The distinction matters at the till: 0.0 g is a real reading that
        bills nothing, and None is "this bin cannot be weighed", which
        doc section 21's M2 acceptance requires to block billing rather
        than quietly contribute zero.
        """
        if counts is None or not self.calibrated:
            return None
        assert self.counts_per_gram is not None  # narrowed by .calibrated
        return (counts - self.zero_counts) / self.counts_per_gram

    def noise_grams(self) -> Optional[float]:
        """The cell's own noise, in grams — doc section 12.4's indicator.

        Reported in grams rather than counts for the same reason `grams()`
        exists: counts are meaningless to the operator, and the useful
        question on that screen is whether the noise is small against the
        10 g deadband.
        """
        if not self.calibrated:
            return None
        assert self.counts_per_gram is not None
        return abs(self.noise_counts_rms / self.counts_per_gram)

    def to_json(self) -> Dict[str, Any]:
        return {
            "i": self.i,
            "zero_counts": self.zero_counts,
            "counts_per_gram": self.counts_per_gram,
            "calibrated_at": self.calibrated_at,
            "ref_mass_g": self.ref_mass_g,
            "noise_counts_rms": self.noise_counts_rms,
        }

    @classmethod
    def from_json(cls, raw: Dict[str, Any]) -> "BinCal":
        cpg = raw.get("counts_per_gram")
        return cls(
            i=int(raw["i"]),
            zero_counts=float(raw.get("zero_counts", 0.0)),
            counts_per_gram=None if cpg is None else float(cpg),
            calibrated_at=raw.get("calibrated_at"),
            ref_mass_g=raw.get("ref_mass_g"),
            noise_counts_rms=float(raw.get("noise_counts_rms", 0.0)),
        )


class Calibration:
    """All eight cells' calibration, and the two operations doc section
    12.4's wizard performs on them.

    Neither `tare()` nor `calibrate()` writes to disk. `save()` is a
    separate call so the Bins tab can persist once, after the sanity
    check has passed — a failed calibration must leave the file holding
    the *previous* good numbers, not a rejected one.
    """

    def __init__(self, bins: Optional[List[BinCal]] = None) -> None:
        self.bins: List[BinCal] = bins if bins is not None else [
            BinCal(i=i) for i in range(NUM_BINS)
        ]

    def _check_bin(self, i: int) -> None:
        if not (0 <= i < NUM_BINS):
            raise IndexError(f"bin {i} out of range 0..{NUM_BINS - 1}")

    # ---- persistence (doc section 8.3) ----------------------------------

    @classmethod
    def load(cls, path: Any) -> "Calibration":
        raw = atomicio.read_json(path, default=None)
        if raw is None:
            log.info("loadcell_cal: no %s — all 8 bins uncalibrated", path)
            return cls()
        schema = raw.get("schema")
        if schema != CAL_SCHEMA:
            raise ValueError(
                f"{path}: schema {schema!r}, expected {CAL_SCHEMA}")
        # Seeded with defaults first, then overwritten by whatever the file
        # holds: a file listing six bins leaves the other two uncalibrated
        # rather than short, so `bins[i]` is always indexable by bin number.
        bins = [BinCal(i=i) for i in range(NUM_BINS)]
        for entry in raw.get("bins", []):
            bc = BinCal.from_json(entry)
            if 0 <= bc.i < NUM_BINS:
                bins[bc.i] = bc
            else:
                log.warning("loadcell_cal: %s has bin %d, ignored", path, bc.i)
        return cls(bins)

    def save(self, path: Any) -> None:
        """Atomic, per doc section 20.4 — see atomicio.py on why a
        truncated calibration is the worst failure in this system.
        """
        atomicio.write_json(path, {
            "schema": CAL_SCHEMA,
            "bins": [b.to_json() for b in self.bins],
        })

    # ---- doc section 9.6 -------------------------------------------------

    def tare(self, i: int, zero_counts: float,
             noise_counts_rms: float = 0.0) -> None:
        """Doc section 9.6 line 1. `zero_counts` is the median of a 2s
        window captured while the bin was empty; core/scale.py collects it.

        Deliberately leaves `counts_per_gram` alone. Re-zeroing a drifted
        cell is the common operation and must not throw away a good
        two-point calibration — doc section 12.4's flow offers Tare and
        Calibrate as separate buttons for exactly this reason.
        """
        self._check_bin(i)
        b = self.bins[i]
        b.zero_counts = float(zero_counts)
        b.noise_counts_rms = float(noise_counts_rms)

    def calibrate(self, i: int, loaded_counts: float, ref_mass_g: float,
                  noise_counts_rms: Optional[float] = None,
                  now: Optional[float] = None) -> float:
        """Doc section 9.6 line 2. Returns the signed counts_per_gram.

        Raises CalibrationError — leaving this bin's existing calibration
        untouched — when the result cannot be trusted. The caller shows
        the message and does not save.
        """
        self._check_bin(i)
        if ref_mass_g <= 0:
            raise CalibrationError(
                "Enter the weight of the mass you placed in the bin.")
        b = self.bins[i]
        cpg = (float(loaded_counts) - b.zero_counts) / float(ref_mass_g)
        # Doc section 9.6's check, and it can genuinely fail: a disconnected
        # DT line reads a near-constant value, so cpg collapses toward 0.
        # Both causes the doc names are in the message, since the operator
        # cannot be told which one it was without being shown counts.
        if abs(cpg) < MIN_COUNTS_PER_GRAM:
            raise CalibrationError(
                f"That didn't work — check the wiring for bin {i}, "
                "or use a heavier weight.")
        b.counts_per_gram = cpg
        b.ref_mass_g = float(ref_mass_g)
        b.calibrated_at = time.time() if now is None else now
        if noise_counts_rms is not None:
            b.noise_counts_rms = float(noise_counts_rms)
        return cpg

    def clear(self, i: int) -> None:
        """Forget bin i entirely — back to first-boot state."""
        self._check_bin(i)
        self.bins[i] = BinCal(i=i)

    # ---- reading ---------------------------------------------------------

    def calibrated(self, i: int) -> bool:
        self._check_bin(i)
        return self.bins[i].calibrated

    def grams(self, i: int, counts: Optional[float]) -> Optional[float]:
        self._check_bin(i)
        return self.bins[i].grams(counts)

    def grams_all(self,
                  counts: Optional[Sequence[Optional[float]]]) -> List[Optional[float]]:
        """Eight bins' grams in one call, None where a bin cannot be
        weighed. `counts=None` (no serial data at all) gives eight Nones,
        so a dead XIAO and an uncalibrated cell reach pricing by the same
        route and neither can bill.
        """
        if counts is None:
            return [None] * NUM_BINS
        return [self.bins[i].grams(counts[i]) for i in range(NUM_BINS)]
