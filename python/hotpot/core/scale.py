"""core/scale.py — the load-cell serial thread (doc section 9.5).

M2 build item 2. This module owns the serial port and nothing else owns
it: the parse, the median filter, staleness, settle detection, and the
2s capture windows `core/loadcell_cal.py` promised would live here. It
converts counts to grams through a `Calibration` and stops there — it
does not know what a cart, a price or a bin map is.

The format is VERIFIED, the rate in the doc is WRONG
----------------------------------------------------
Doc section 4.9 says "read `firmware/loadcells/src/main.cpp` and match
its actual print format. Do not assume." Both halves were checked
against the rig on COM5 on 2026-08-11:

- **Format is right.** `raw <c0> ... <c7>\\r\\n` at 115200 — nine
  whitespace-separated tokens, the literal `raw` and eight signed
  integers, printed once per conversion cycle by `main.cpp`'s `loop()`.
- **Rate is wrong. Doc section 4.9 says ~78Hz; the rig delivers 10.7Hz**
  — the HX711 at its default 10 SPS, RATE pin low. Every derived timing
  in this file follows from 10.7Hz and not from the doc's number:

      one sample          ~93 ms
      median-of-5         spans ~465 ms, and takes ~280 ms (3 samples)
                          to cross to a new value after a pick
      settle_ms 300       is ~4 samples, not ~24

That is why `median_window` is a **constructor parameter and not a
constant**: if the ~280ms lag is visible on the projected surface, the
first move is median-of-3 here, not a board mod (see CLAUDE.md, decided
2026-08-11). Nothing in this file may hardcode 5.

One slot, no queue
------------------
Doc section 9.5: the thread writes the latest sample into a single slot
under a lock; the 60Hz main loop reads it. A queue would let the main
loop fall behind and then bill from weights that are seconds old. The
main loop reading the same sample nine times in a row is correct
behaviour at 10.7Hz, not a bug to smooth over.

Staleness cannot bill
---------------------
Doc section 9.5: older than `stale_s` (0.5s) means the XIAO is gone. A
stale reader reports `counts=None`, which reaches `Calibration.grams_all`
and comes back as eight `None`s — the same route an uncalibrated cell
takes, and doc section 21's M2 acceptance requires exactly that: "no
billing occurs from the frozen reading". `None`, never `0.0`. A frozen
0.0 g would look like a bin emptied by a diner.

The thread never dies
---------------------
Unplugging the XIAO raises from inside `readline()`, and a dead reader
thread is worse than a dead device: staleness is what makes the fault
visible, and it only keeps working if something is still trying. So the
loop reopens on the doc section 20.2 ladder (1s doubling to 10s), the
same one `common/wire.py`'s client uses, and catches broadly on purpose
— see `_read_forever`. A missing port at startup is the ordinary first
case, not an error.
"""

from __future__ import annotations

import logging
import statistics
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, List, Optional, Sequence

from hotpot.core import loadcell_cal

log = logging.getLogger("hotpot.scale")

NUM_BINS = loadcell_cal.NUM_BINS

# firmware/loadcells/src/main.cpp: `Serial.begin(115200)` and one
# `Serial.print("raw")` per cycle. Both read off that file, not doc 4.9.
DEFAULT_BAUD = 115200
LINE_PREFIX = "raw"

# Doc section 9.5. A parameter everywhere below, never a constant — see
# the rate finding in the module docstring.
DEFAULT_MEDIAN_WINDOW = 5

# Doc section 9.5 / 8.6: "settled when its gram value has stayed within
# ±2g for settle_ms (default 300ms)".
DEFAULT_SETTLE_MS = 300.0
DEFAULT_SETTLE_TOL_G = 2.0

# Doc section 9.5's "if time.time() - ts > 0.5, the XIAO is gone".
DEFAULT_STALE_S = 0.5

# Bounds `readline()` so stop() is prompt and so a device that has gone
# quiet without raising is noticed by the staleness clock rather than by
# a thread parked forever in a blocking read. Comfortably longer than the
# 93ms sample period, so a healthy line is never cut in half by it.
READ_TIMEOUT_S = 0.2

# Doc section 20.2's reconnect ladder, identical to wire.py's.
BACKOFF_START = 1.0
BACKOFF_MAX = 10.0

# How many sample timestamps the measured-rate estimate averages over.
# 32 samples is ~3s at 10.7Hz — long enough to be steady on the staff
# view, short enough to fall visibly when the link degrades. The rate is
# reported at all because the doc's 78Hz went unchallenged for months.
RATE_WINDOW = 32

# Fewest samples a capture window may return and still be used for a
# tare or a calibration. A 2s window at 10.7Hz gives ~21; anything near
# 5 means the device is barely talking, and a median of two samples is
# not a measurement to bill from.
MIN_CAPTURE_SAMPLES = 5


class ScaleError(Exception):
    """A capture that cannot be used for a tare or a calibration.

    Like `loadcell_cal.CalibrationError`, the message is written for the
    operator on the Bins tab (doc section 12.4) and mentions no counts.
    """


def parse_line(line: Any) -> Optional[List[int]]:
    """`raw <c0> ... <c7>` → eight ints, or None for anything else.

    Doc section 4.9's three requirements, all of them satisfied by being
    strict rather than clever:

    - **Partial lines at startup.** The first line after opening the port
      is usually truncated. Its head (`raw 8123 -47`) has too few tokens;
      its tail (`39 812 ... 4471`) has no `raw`. Both fail the same test
      and are dropped. There is deliberately no attempt to salvage half a
      line — a plausible-looking half would mis-weigh a bin.
    - **Junk is discarded, never raised on.** Line noise at 115200 shows
      up as non-ASCII bytes or a token that is not an integer.
    - Floats are junk too: `main.cpp` prints `long`s, so a token with a
      decimal point did not come from this firmware.
    """
    if isinstance(line, (bytes, bytearray)):
        try:
            text = bytes(line).decode("ascii")
        except UnicodeDecodeError:
            return None
    else:
        text = str(line)
    # split() with no argument handles the \r\n, leading/trailing space
    # and any run of spaces at once.
    parts = text.split()
    if len(parts) != NUM_BINS + 1 or parts[0] != LINE_PREFIX:
        return None
    try:
        return [int(p) for p in parts[1:]]
    except ValueError:
        return None


@dataclass(frozen=True)
class Reading:
    """One snapshot of the slot, as the main loop sees it.

    `counts` are median-filtered, not raw. `grams[i] is None` means bin i
    cannot be weighed — stale link, or an uncalibrated cell — and must
    contribute nothing to a price rather than 0.0 g (doc section 21, M2).
    """

    counts: Optional[List[float]]
    grams: List[Optional[float]]
    settled: List[bool]
    ts: float
    age: float
    stale: bool


@dataclass(frozen=True)
class Capture:
    """A 2s window of raw counts, reduced to what doc section 9.6 needs.

    `counts` is the per-bin median the doc's two-point maths consumes;
    `noise_rms` is doc section 8.3's `noise_counts_rms`, which the Bins
    tab turns into grams for its noise indicator.
    """

    counts: List[float]
    noise_rms: List[float]
    n: int
    seconds: float


@dataclass
class _Settle:
    """Per-bin settle state. `ref` anchors the window, and that is the
    whole point of the type — see `_update_settle`.
    """

    ref: Optional[float] = None
    since: float = 0.0
    settled: bool = False


class ScaleReader:
    """The serial thread of doc section 9.5.

    Lifecycle matches `wire.Client` — `start()` returns immediately and
    the link comes up whenever the XIAO does, `stop()` is idempotent —
    rather than the doc's `threading.Thread` subclass, so every
    long-running link in this codebase is driven the same way. The doc's
    snippet is the body of `_read_forever`, unchanged in substance.

    `open_port` exists so the whole module is testable with no XIAO
    attached, for the reason `loadcell_cal.py`'s docstring gives: the
    numbers in here can silently mis-bill, so they have to be reachable
    from a test.
    """

    def __init__(
        self,
        port: str,
        *,
        cal: Optional[loadcell_cal.Calibration] = None,
        baud: int = DEFAULT_BAUD,
        median_window: int = DEFAULT_MEDIAN_WINDOW,
        settle_ms: float = DEFAULT_SETTLE_MS,
        settle_tol_g: float = DEFAULT_SETTLE_TOL_G,
        stale_s: float = DEFAULT_STALE_S,
        open_port: Optional[Callable[[], Any]] = None,
    ) -> None:
        if median_window < 1:
            raise ValueError("median_window must be at least 1")
        self.port = port
        self.baud = baud
        # An all-uncalibrated Calibration is the correct default and not a
        # placeholder: on first boot nothing is calibrated, every bin reads
        # None grams, and doc section 9.1 sends that boot to UNCALIBRATED.
        self.cal = cal if cal is not None else loadcell_cal.Calibration()
        self.median_window = median_window
        self.settle_ms = settle_ms
        self.settle_tol_g = settle_tol_g
        self.stale_s = stale_s
        self._open_port = open_port

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # The slot (doc section 9.5). Never a queue.
        self._counts: Optional[List[float]] = None
        self._ts: float = 0.0
        self._settle: List[_Settle] = [_Settle() for _ in range(NUM_BINS)]

        self._window: Deque[List[int]] = deque(maxlen=median_window)
        self._rate: Deque[float] = deque(maxlen=RATE_WINDOW)
        self._collectors: List[List[List[int]]] = []

        self._port_open = False
        self._error: Optional[str] = None
        self.samples = 0
        self.bad_lines = 0
        self.opens = 0

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("ScaleReader already started")
        self._thread = threading.Thread(
            target=self._run, name="scale-reader", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop reading and stop reopening. Idempotent."""
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            # Longer than READ_TIMEOUT_S so a thread parked in a healthy
            # blocking read is given time to come back round on its own.
            t.join(3.0)

    # -- reading -----------------------------------------------------------

    def read(self, now: Optional[float] = None) -> Reading:
        """The main loop's one call. Never blocks, never raises."""
        now = time.time() if now is None else now
        with self._lock:
            counts = None if self._counts is None else list(self._counts)
            ts = self._ts
            settled = [s.settled for s in self._settle]
        age = (now - ts) if ts > 0.0 else float("inf")
        if counts is None or age > self.stale_s:
            # Doc section 9.5: do not silently bill from a frozen reading.
            # grams_all(None) is eight Nones — the same value an
            # uncalibrated cell produces, on purpose, so pricing needs one
            # rule and not two.
            return Reading(counts=None, grams=self.cal.grams_all(None),
                           settled=[False] * NUM_BINS, ts=ts, age=age,
                           stale=True)
        # Converted outside the lock: the Calibration belongs to the Bins
        # tab, not to this thread. An edit landing mid-conversion can only
        # skew one frame's grams, and doc section 9.1 refuses staff mode
        # while a cart is active, so no such frame can reach a bill.
        return Reading(counts=counts, grams=self.cal.grams_all(counts),
                       settled=settled, ts=ts, age=age, stale=False)

    @property
    def stale(self) -> bool:
        return self.read().stale

    @property
    def hz(self) -> float:
        """Measured sample rate. ~10.7 on this rig, not doc 4.9's ~78."""
        with self._lock:
            stamps = list(self._rate)
        if len(stamps) < 2:
            return 0.0
        span = stamps[-1] - stamps[0]
        return (len(stamps) - 1) / span if span > 0 else 0.0

    def status(self, now: Optional[float] = None) -> dict:
        """What the staff view's serial pip and Bins tab need to draw."""
        r = self.read(now)
        with self._lock:
            port_open, error = self._port_open, self._error
        return {
            "port": self.port,
            "open": port_open,
            "stale": r.stale,
            "age": None if r.age == float("inf") else round(r.age, 3),
            "hz": round(self.hz, 2),
            "samples": self.samples,
            "bad_lines": self.bad_lines,
            "opens": self.opens,
            "error": error,
        }

    # -- the sample path ---------------------------------------------------

    def feed(self, counts: Sequence[int], now: Optional[float] = None) -> None:
        """Take one parsed sample. The reader thread's own entry point.

        Public so the median, the settle window and staleness can be
        driven from a test at chosen timestamps rather than at whatever
        the wall clock happened to do — the maths in here mis-bills
        silently when it is wrong, which is the same argument that keeps
        the serial port out of `loadcell_cal.py`.
        """
        if len(counts) != NUM_BINS:
            raise ValueError(f"expected {NUM_BINS} counts, got {len(counts)}")
        now = time.time() if now is None else now
        sample = [int(c) for c in counts]
        with self._lock:
            self._window.append(sample)
            self._rate.append(now)
            for collector in self._collectors:
                collector.append(sample)
            # Doc section 9.5: median, not mean. A single bad HX711 read is
            # an outlier, and a mean would smear it across the whole window
            # instead of discarding it.
            median = [float(statistics.median([s[i] for s in self._window]))
                      for i in range(NUM_BINS)]
            self._counts = median
            self._ts = now
            self.samples += 1
            self._update_settle(self.cal.grams_all(median), now)

    def _update_settle(self, grams: Sequence[Optional[float]],
                       now: float) -> None:
        """Doc section 9.5's classifier trigger: settled when the gram
        value has stayed within ±settle_tol_g for settle_ms.

        **Compared against the value the window opened at, never against
        the previous sample.** Sample-to-sample comparison passes on a
        slow ramp — food being poured in at 1g per sample sits inside a
        ±2g step forever — and would tell the classifier to photograph a
        bin that is still moving. Anchoring to `ref` bounds the whole
        window instead, which is what "stayed within ±2g" means.

        A bin with no grams (stale, or uncalibrated) is never settled: it
        is not steady, it is unmeasurable, and the classifier must not be
        triggered by the absence of a reading.

        Caller holds the lock.
        """
        for i in range(NUM_BINS):
            g = grams[i]
            st = self._settle[i]
            if g is None:
                st.ref, st.since, st.settled = None, now, False
                continue
            if st.ref is None or abs(g - st.ref) > self.settle_tol_g:
                st.ref, st.since, st.settled = g, now, False
                continue
            st.settled = (now - st.since) * 1000.0 >= self.settle_ms

    # -- capture windows (doc section 9.6) ---------------------------------

    def capture(self, seconds: float = 2.0) -> Capture:
        """Collect `seconds` of RAW samples and reduce them per doc 9.6.

        Blocking, and called from the Bins tab's thread — never from the
        60Hz main loop.

        Raw, not median-filtered, and the distinction is load-bearing in
        both directions: doc section 9.6's `median(counts, 2s window)` is
        already a filter and does not want a second one in front of it,
        and `noise_counts_rms` measured through the smoother would report
        the smoother's noise rather than the cell's — understating it by
        roughly the window size and hiding exactly the kind of bad channel
        (bin 4, 40x its neighbour on this rig) the indicator exists to
        find.
        """
        collector: List[List[int]] = []
        with self._lock:
            self._collectors.append(collector)
        try:
            self._stop.wait(seconds)
        finally:
            with self._lock:
                self._collectors.remove(collector)
        samples = list(collector)
        if len(samples) < MIN_CAPTURE_SAMPLES:
            raise ScaleError(
                "No steady reading from the load cells — check that the "
                "cable is plugged in, then try again.")
        counts = [float(statistics.median([s[i] for s in samples]))
                  for i in range(NUM_BINS)]
        noise = [float(statistics.pstdev([s[i] for s in samples]))
                 for i in range(NUM_BINS)]
        return Capture(counts=counts, noise_rms=noise, n=len(samples),
                       seconds=seconds)

    # -- the thread --------------------------------------------------------

    def _run(self) -> None:
        backoff = BACKOFF_START
        while not self._stop.is_set():
            port = self._try_open()
            if port is None:
                if self._stop.wait(backoff):
                    return
                backoff = min(backoff * 2, BACKOFF_MAX)
                continue
            backoff = BACKOFF_START
            self._read_forever(port)

    def _try_open(self) -> Optional[Any]:
        opener = self._open_port
        try:
            port = opener() if opener is not None else _open_serial(
                self.port, self.baud)
        except Exception as e:      # noqa: BLE001 — see _read_forever
            with self._lock:
                self._port_open = False
                self._error = str(e)
            # debug, not warning: "the XIAO is not plugged in yet" is the
            # ordinary state at boot and would otherwise print every
            # second forever.
            log.debug("scale: cannot open %s: %s", self.port, e)
            return None
        with self._lock:
            self._port_open = True
            self._error = None
            self.opens += 1
        log.info("scale: %s open at %d baud", self.port, self.baud)
        return port

    def _read_forever(self, port: Any) -> None:
        """Doc section 9.5's loop. Returns when the port dies or on stop.

        The bare `except Exception` is deliberate. Unplugging a USB serial
        device raises different types on different platforms and pyserial
        versions — SerialException, OSError, and on Windows occasionally
        neither — and every one of them means the same thing here: the
        port is gone, reopen it. A reader thread that dies on an
        unforeseen type takes staleness detection down with it, and
        staleness is the mechanism that stops a frozen weight from
        billing.
        """
        try:
            while not self._stop.is_set():
                line = port.readline()
                if not line:
                    # Read timeout. The device has gone quiet without
                    # raising; the staleness clock is what notices.
                    continue
                counts = parse_line(line)
                if counts is None:
                    self.bad_lines += 1
                    continue
                self.feed(counts)
        except Exception as e:      # noqa: BLE001 — see the docstring
            with self._lock:
                self._error = str(e)
            log.warning("scale: %s read failed: %s", self.port, e)
        finally:
            with self._lock:
                self._port_open = False
            try:
                port.close()
            except Exception:       # noqa: BLE001
                pass


def _open_serial(device: str, baud: int, timeout: float = READ_TIMEOUT_S) -> Any:
    """The real port. Imported here rather than at module scope so that
    everything above — the parse, the median, the settle window — can be
    imported and tested on a machine with no pyserial and no XIAO.
    """
    import serial      # pyserial; see python/requirements.txt
    return serial.Serial(port=device, baudrate=baud, timeout=timeout)
