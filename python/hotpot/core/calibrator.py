"""core/calibrator.py — the Bins tab's Tare and Calibrate flows, wired.

M2 build item 3's second half (doc section 21). M2.1 built the maths
(`core/loadcell_cal.py`) and M2.2 built the port (`core/scale.py`), and
the two were deliberately kept apart: the maths has no serial port in it
so the one number that can *silently mis-bill* stays testable with no
XIAO attached, and the port does not write to disk. This module is the
seam — it is the only place a captured window becomes a saved
calibration:

    scale.capture(2s)  ->  loadcell_cal.tare/calibrate  ->  state/loadcell_cal.json

and it is what doc section 12.4's two buttons call. Nothing else in the
tree may write that file.

The wiring order, for build item 5
----------------------------------
One `Calibration` object, shared. `core/main.py` will do:

    cal    = loadcell_cal.Calibration.load(calibrator.CAL_PATH)
    reader = scale.ScaleReader(port, cal=cal); reader.start()
    bins   = calibrator.Calibrator(reader)

There is deliberately **no `cal` parameter** here: the calibrator takes
`reader.cal`, which makes it impossible to calibrate a copy. A copy would
save a perfectly good file that the live reading never picks up — the
table would go on showing the pre-calibration grams and nothing would
look broken until someone weighed a plate by hand.

Why the verification reading is a second measurement
----------------------------------------------------
Doc section 12.4 ends each flow with "Done. Bin 3 reads 500 g." The
number in that sentence comes from `reader.read()` — a **fresh** look at
the live slot through the calibration just saved — and not from the
capture the fit was computed from. Reading it back out of that capture is
a **TRAP** in the doc section 21 sense: `(loaded - zero) / cpg` is
`ref_mass_g` by construction, to the last decimal place, so it would
print "500 g" for a cell that is disconnected, mis-wired, or drifting,
and confirm nothing. A second measurement can disagree, which is the
whole point of showing it.

What is not saved from the loaded capture
-----------------------------------------
`noise_counts_rms` (doc section 8.3) is taken from the **tare** capture
only. Noise measured with a mass sitting in the bin includes the mass
settling and the tray rocking; the empty-bin number is the channel's own
noise, which is what CLAUDE.md's per-channel table measured and what doc
section 12.4's indicator is asking about. So `calibrate()` never
overwrites it.

Failure leaves the previous good numbers
----------------------------------------
Three separate refusals, none of which writes the file:

- the capture is too short to be a measurement (`scale.ScaleError`),
- the fit fails doc section 9.6's sanity check (`CalibrationError`),
- the bin was never tared (`CalibrationError`, see `calibrate`).

And if the *write* fails, the in-memory bin is rolled back to what the
file still holds. Memory ahead of disk is the quiet version of this
failure: the table would bill correctly all evening and come back after a
restart weighing food against a calibration that was never saved.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterator, Optional

from hotpot.core import loadcell_cal, scale

log = logging.getLogger("hotpot.calibrator")

NUM_BINS = loadcell_cal.NUM_BINS

# core/calibrator.py -> core -> hotpot -> python -> repo root. Same
# hardcoded-until-something-loads-config rationale as core/main.py's
# DATA_DIR; doc section 8.3 fixes the name.
CAL_PATH = Path(__file__).resolve().parents[3] / "state" / "loadcell_cal.json"

# Doc section 12.4: "2s capture", twice. A parameter on the constructor
# because doc section 4.9's rate was wrong by 7x once already — at the
# rig's 10.7Hz this window is ~21 samples, and if a future firmware
# changes that, the window and not the code should move.
DEFAULT_CAPTURE_S = 2.0

# Doc section 12.4's keypad default. Lives here rather than in the browser
# so the number the operator is offered and the number the maths defaults
# to cannot drift apart.
DEFAULT_REF_MASS_G = 500.0


class BusyError(Exception):
    """A second tare or calibrate arrived while one was still running.

    Refused rather than queued, on purpose: the operator is standing at
    the table with a weight in their hand, and a flow that silently waits
    2s and then captures — possibly after they have picked the weight
    back up — photographs the wrong bin state. Message is written for the
    Bins tab, like the other two.
    """


# Everything doc section 12.4's screen has to be able to show. All three
# carry a message written for an untrained operator and none of them
# mentions counts, sign or orientation (test_calibrator.py checks that).
OPERATOR_ERRORS = (scale.ScaleError, loadcell_cal.CalibrationError, BusyError)


@dataclass(frozen=True)
class Result:
    """What one completed flow puts on the screen.

    `grams` is the verification reading (see the module docstring) and is
    `None` when the link went stale between the capture and the read —
    the calibration is saved either way, but the sentence must not claim
    a number nobody measured.

    `noise_g` is doc section 8.3's `noise_counts_rms` in grams, which is
    the form doc section 12.4's indicator wants, and `noisy` compares it
    to the settle band the reader is actually using: a cell whose noise
    exceeds ±`settle_tol_g` can never satisfy doc section 9.5's settle
    test, so the classifier would never be triggered for that bin. That
    is the risk CLAUDE.md flags for four channels on this rig, and this
    is the field that turns it into a number.
    """

    bin: int
    op: str                       # "tare" | "calibrate"
    grams: Optional[float]
    noise_g: Optional[float]
    noisy: bool
    samples: int
    message: str


class Calibrator:
    """Doc section 12.4's two buttons, and the only writer of
    `state/loadcell_cal.json`.

    Blocking — a capture window is a duration — so it is called from the
    staff view's thread, never from core's 60Hz loop.
    """

    def __init__(self, reader: scale.ScaleReader, *,
                 path: Any = CAL_PATH,
                 capture_s: float = DEFAULT_CAPTURE_S) -> None:
        self.reader = reader
        # The reader's own object, never a copy — see the module docstring.
        self.cal = reader.cal
        self.path = Path(path)
        self.capture_s = capture_s
        self._busy = threading.Lock()

    # -- doc section 12.4's flows ------------------------------------------

    def tare(self, i: int, seconds: Optional[float] = None) -> Result:
        """Doc section 12.4 step 1: bin empty → 2s capture → "reads 0 g".

        Leaves `counts_per_gram` alone (loadcell_cal.tare's own contract),
        so re-zeroing a drifted cell does not throw away a good two-point
        calibration. This is the load cell's zero and **not** I6's
        re-baseline; nothing on the diner path can reach it.
        """
        self._check_bin(i)
        with self._one_at_a_time():
            cap = self.reader.capture(self._seconds(seconds))
            self._apply_and_save(
                i, lambda: self.cal.tare(i, cap.counts[i], cap.noise_rms[i]))
            log.info("calibrator: bin %d tared over %d samples", i, cap.n)
            return self._result(i, "tare", cap)

    def calibrate(self, i: int, ref_mass_g: float = DEFAULT_REF_MASS_G,
                  seconds: Optional[float] = None) -> Result:
        """Doc section 12.4 step 2: known mass in the bin → 2s capture →
        "reads 500 g", or step 3's refusal with nothing saved.

        Refuses a bin that has never been tared, which doc section 12.4's
        flow implies by ordering but does not enforce. It matters more
        than it looks: an untared bin has `zero_counts` 0, and on this rig
        an empty cell sits at -287,000 counts, so the fit comes out
        roughly 4x too steep — a number that sails through doc section
        9.6's `abs(cpg) < 10` check and then under-reads every gram taken
        out of that bin for the rest of the evening.

        "Never tared" is the bin still being byte-for-byte its first-boot
        default rather than a new `tared_at` field, because doc section
        8.3 fixes the shape of that file and a schema bump is a poor
        trade for a fact already visible in the data.
        """
        self._check_bin(i)
        if self.cal.bins[i] == loadcell_cal.BinCal(i=i):
            raise loadcell_cal.CalibrationError(
                f"Tare bin {i} first, with the bin empty.")
        with self._one_at_a_time():
            cap = self.reader.capture(self._seconds(seconds))
            # ref_mass_g is validated inside loadcell_cal.calibrate, which
            # raises before it mutates anything — so a fat-fingered keypad
            # entry cannot half-apply a calibration.
            self._apply_and_save(
                i, lambda: self.cal.calibrate(i, cap.counts[i], ref_mass_g))
            log.info("calibrator: bin %d calibrated against %.1fg over %d "
                     "samples", i, ref_mass_g, cap.n)
            return self._result(i, "calibrate", cap)

    # -- internals ---------------------------------------------------------

    def _check_bin(self, i: int) -> None:
        # IndexError, matching loadcell_cal: a bin number out of range is a
        # caller bug, not something to word for an operator. The staff
        # view validates the incoming index the way core/main.py's mock
        # handler does, before it ever gets here.
        if not isinstance(i, int) or isinstance(i, bool) or not (0 <= i < NUM_BINS):
            raise IndexError(f"bin {i!r} out of range 0..{NUM_BINS - 1}")

    def _seconds(self, seconds: Optional[float]) -> float:
        return self.capture_s if seconds is None else seconds

    @contextlib.contextmanager
    def _one_at_a_time(self) -> Iterator[None]:
        if not self._busy.acquire(blocking=False):
            raise BusyError(
                "One bin is already being set up — wait for it to finish.")
        try:
            yield
        finally:
            self._busy.release()

    def _apply_and_save(self, i: int, apply: Any) -> None:
        """Mutate one bin and persist all eight, atomically, or leave both
        the file and this object as they were.
        """
        before = replace(self.cal.bins[i])
        apply()
        try:
            self.cal.save(self.path)
        except Exception:
            # Rolled back so memory and disk cannot disagree — see the
            # module docstring. The exception carries on to the Bins tab,
            # which shows it: a calibration that could not be written is a
            # full-stop failure, not a warning.
            self.cal.bins[i] = before
            log.exception("calibrator: cannot write %s — bin %d rolled back",
                          self.path, i)
            raise

    def _result(self, i: int, op: str, cap: scale.Capture) -> Result:
        """Build doc section 12.4's closing sentence.

        Doc section 12.4 step 1 ends "Done. Bin 3 reads 0 g." **That
        sentence is not available on a first-ever tare**, and the doc's
        flow does not notice: a bin with no `counts_per_gram` yet cannot
        be read in grams at all, so there is no measurement to quote. The
        honest reply is to send the operator to step 2, which is where
        that flow was going anyway. Printing "0 g" from `zero - zero` for
        a cell nobody has weighed anything on would be the TRAP this
        module's docstring is about, one step earlier.
        """
        reading = self.reader.read()
        grams = reading.grams[i]
        noise_g = self.cal.bins[i].noise_grams()
        noisy = noise_g is not None and noise_g > self.reader.settle_tol_g
        if grams is not None:
            message = f"Done. Bin {i} reads {round(grams)} g."
        elif not self.cal.bins[i].calibrated:
            message = (f"Bin {i} is set as empty. Now place a known weight "
                       "in it and tap Calibrate.")
        else:
            # Saved, but unverified. Says so, in those terms.
            message = (f"Saved bin {i}, but the load cells stopped "
                       "reporting — check the cable, then try again.")
        if noisy:
            log.warning("calibrator: bin %d noise is +/-%.1fg, wider than the "
                        "%.1fg settle band — it may never settle",
                        i, noise_g, self.reader.settle_tol_g)
        return Result(bin=i, op=op, grams=grams, noise_g=noise_g, noisy=noisy,
                      samples=cap.n, message=message)
