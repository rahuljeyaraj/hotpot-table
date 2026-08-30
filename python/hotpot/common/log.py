"""Process logging, and the two lines the launcher parses (doc sections 10, 12.8).

Every process configures logging exactly once, at the top of its `main()`:

    ring = log.setup("tracker")
    ...
    log.ready("tracker")        # once it is genuinely serving

Where the logs go
-----------------
Nowhere, from this module's point of view. Logs go to stdout and stop
there. `run.py` owns the files: it tails every child's stdout and stderr,
prefixes each line with the process name and a colour, and writes
`logs/hotpot-<date>.log` with rotation (doc section 10.2). Six processes each
opening the same log file would need locking to avoid interleaved lines, and
the launcher already has every byte in front of it.

That is also why the process name is not in the line format. The launcher
adds it, and a merged log reading `tracker | 12:04:31.882 tracker I ...` is
worse than one that does not. The `who` field on the ring record carries it
for the staff view, which has no launcher prefix to lean on.

Buffering is load-bearing
-------------------------
A child's stdout is a pipe, and Python block-buffers pipes at 8 KB. Left
alone, a process that is killed loses its last few hundred lines — including,
reliably, the ones explaining why it was worth killing. The M0 acceptance
test is `kill -9` a child, so this module flushes every record and puts
stdout in line-buffered mode.

The encoding is forced to UTF-8 for a related reason: bin labels are Chinese
(doc section 8.1), and the Windows dev box defaults stdout to a codepage that
cannot represent them. A log line that raises inside the logging machinery is
a log line that is not there when it matters.

The ring
--------
Doc section 12.8 wants a live log tail with a level filter in the developer
panel. `setup` returns a bounded in-memory `Ring` holding the most recent
records, which core's web layer streams. It is deliberately a plain container
with an `add` that takes a `who`: the launcher already reads every other
process's stdout, so forwarding those lines into core's ring later is a
matter of calling `add`, not of inventing a second record shape.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, TextIO

# The readiness protocol of doc section 10.2. The launcher waits for this
# line before starting the next tier, so the exact bytes are a contract
# between this module and run.py, and they live in one place.
READY_PREFIX = "HOTPOT-READY"

# Doc section 10.2 again: the level a process starts at, overridable per run
# without editing code, because the first thing anyone does when chasing a
# reconnect storm is turn wire.py up to DEBUG.
LEVEL_ENV = "HOTPOT_LOG_LEVEL"
DEFAULT_LEVEL = logging.INFO

# Enough to cover a long bring-up session in the developer panel without
# being a memory decision anyone has to think about: ~2000 lines is a few
# hundred KB, on a box with 8 GB.
RING_SIZE = 2000

# Milliseconds, no date. The date is in the log filename (doc section 10.2),
# and every line of a merged six-process log repeating it would cost a fifth
# of the terminal width.
#
# The level is padded to 7 so that DEBUG, INFO, WARNING and ERROR all line
# up in a merged log — everything downstream of it is then scannable by eye.
# CRITICAL is 8 and breaks the column on purpose.
TIME_FORMAT = "%H:%M:%S"
LINE_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s: %(message)s"

# Name to number, for the staff view's level filter. Exposed so the web layer
# does not have to import `logging` to turn "warning" into 30.
LEVELS: Dict[str, int] = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}

log = logging.getLogger("hotpot.log")

# Set by setup(). Module state because there is exactly one logging config per
# process, and pretending otherwise would let a second call quietly double
# every line.
_configured: Optional[str] = None
_ring: Optional["Ring"] = None
_prev_hooks: Optional[tuple] = None


# ---------------------------------------------------------------------------
# The ring
# ---------------------------------------------------------------------------

class Ring:
    """The last N log records, for the doc section 12.8 tail.

    Records are plain dicts so they can go down a WebSocket unchanged:

        {"seq":41,"ts":1770...,"level":20,"levelname":"INFO",
         "who":"core","name":"hotpot.wire","msg":"..."}

    `seq` is monotonic from process start and never reused. It is what lets
    the staff view reconnect and ask for "everything after 41" instead of
    re-rendering the whole buffer or, worse, silently missing the lines it
    dropped while disconnected.

    Thread-safe: records arrive on wire read threads, heartbeat threads and
    the main loop, and the web layer reads from yet another.
    """

    def __init__(self, size: int = RING_SIZE) -> None:
        self._lock = threading.Lock()
        self._records: Deque[Dict[str, Any]] = deque(maxlen=size)
        self._seq = 0

    def add(self, who: str, level: int, name: str, msg: str,
            ts: Optional[float] = None) -> Dict[str, Any]:
        """Append one record. Returns it, already sequenced."""
        with self._lock:
            self._seq += 1
            rec = {
                "seq": self._seq,
                "ts": time.time() if ts is None else ts,
                "level": level,
                "levelname": logging.getLevelName(level),
                "who": who,
                "name": name,
                "msg": msg,
            }
            self._records.append(rec)
            return rec

    def tail(self, limit: int = 200, min_level: int = 0,
             after: Optional[int] = None) -> List[Dict[str, Any]]:
        """The newest records, oldest first.

        `limit` is applied after the level filter, so asking for 50
        errors gives 50 errors and not "whatever errors happen to be in the
        last 50 lines" — which is the same request the panel's filter makes
        and the answer a human expects from it.
        """
        with self._lock:
            out = [dict(r) for r in self._records
                   if r["level"] >= min_level
                   and (after is None or r["seq"] > after)]
        return out[-limit:] if limit else out

    @property
    def seq(self) -> int:
        with self._lock:
            return self._seq

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)


class RingHandler(logging.Handler):
    """Feeds a Ring from the logging module. Installed by setup()."""

    def __init__(self, ring: Ring, who: str) -> None:
        super().__init__()
        self.ring = ring
        self.who = who

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # format() rather than getMessage(): a traceback belongs in the
            # panel too, and the formatter is what turns exc_info into one.
            self.ring.add(self.who, record.levelno, record.name,
                          self.format(record), record.created)
        except Exception:       # pragma: no cover - logging must not raise
            self.handleError(record)


class _RingFormatter(logging.Formatter):
    """Message and traceback only. The ring stores the rest as fields."""

    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        if record.exc_info:
            msg = msg + "\n" + self.formatException(record.exc_info)
        return msg


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def setup(who: str, level: Optional[Any] = None, *,
          stream: Optional[TextIO] = None, ring_size: int = RING_SIZE) -> Ring:
    """Configure this process's logging. Idempotent.

    `level` may be a number or a name; None means read `HOTPOT_LOG_LEVEL`,
    defaulting to INFO.

    Returns the Ring, so core can hand it straight to the web layer. The
    other five processes ignore the return value and pay one deque for it.

    Calling twice is a no-op rather than an error. Tests and `python -m`
    entry points both manage it, and doubling every log line is a subtle
    enough symptom that it is worth simply not allowing.
    """
    global _configured, _ring

    if _configured is not None:
        if _configured != who:
            log.warning("log: already set up as %r, ignoring setup(%r)",
                        _configured, who)
        return _ring                                    # type: ignore[return-value]

    lvl = resolve_level(level)
    out = _prepare_stream(stream if stream is not None else sys.stdout)
    ring = Ring(ring_size)

    stream_handler = logging.StreamHandler(out)
    stream_handler.setFormatter(logging.Formatter(LINE_FORMAT, TIME_FORMAT))

    ring_handler = RingHandler(ring, who)
    ring_handler.setFormatter(_RingFormatter())

    root = logging.getLogger()
    # Own the root logger outright. A library that installed a handler at
    # import time — or a previous setup in the same interpreter, which is a
    # test — would otherwise duplicate every line.
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(stream_handler)
    root.addHandler(ring_handler)
    root.setLevel(lvl)

    _configured, _ring = who, ring
    _install_excepthooks(who)
    return ring


def resolve_level(level: Optional[Any] = None) -> int:
    """Number, name, or None-means-environment. Unknown names fall back.

    An unparsable level never raises. `HOTPOT_LOG_LEVEL=verbose` on the board
    at 1am should produce a working process that says the level was wrong,
    not a process that refuses to start.
    """
    if level is None:
        level = os.environ.get(LEVEL_ENV, "")
    if isinstance(level, int):
        return level
    text = str(level).strip()
    if not text:
        return DEFAULT_LEVEL
    if text.isdigit():
        return int(text)
    known = LEVELS.get(text.lower())
    if known is None:
        print(f"{LEVEL_ENV}={text!r} is not a level name, using INFO",
              file=sys.stderr, flush=True)
        return DEFAULT_LEVEL
    return known


def ring() -> Optional[Ring]:
    """The Ring created by setup(), or None if setup() has not run."""
    return _ring


def reset() -> None:
    """Undo setup(), hooks included. For tests only — the app never calls it."""
    global _configured, _ring, _prev_hooks
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    if _prev_hooks is not None:
        sys.excepthook, threading.excepthook = _prev_hooks
    _configured, _ring, _prev_hooks = None, None, None


# ---------------------------------------------------------------------------
# The readiness line (doc section 10.2)
# ---------------------------------------------------------------------------

def ready(who: str, stream: Optional[TextIO] = None) -> None:
    """Announce that this process is genuinely serving.

    Printed raw, with no timestamp and no level, because the launcher matches
    the line and a formatter change must not be able to break the start
    sequence. Say it *after* the port is bound or the device is open — the
    launcher starts the next tier on this line, and a process that says it
    early converts a clean start into a race that only shows up on the slow
    board.
    """
    out = stream if stream is not None else sys.stdout
    print(f"{READY_PREFIX} {who}", file=out, flush=True)


def is_ready_line(line: str) -> Optional[str]:
    """The process name if `line` is a readiness line, else None. For run.py.

    Tolerates a leading prefix so it still matches once the launcher's own
    tailing has coloured and labelled the line, and tolerates trailing
    whitespace because the line has crossed a pipe.
    """
    text = line.strip()
    at = text.find(READY_PREFIX)
    if at < 0:
        return None
    rest = text[at + len(READY_PREFIX):].strip()
    return rest.split()[0] if rest else None


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _prepare_stream(stream: TextIO) -> TextIO:
    """Line-buffer it and force UTF-8. See the module docstring."""
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return stream
    try:
        # backslashreplace rather than strict: a label this console cannot
        # draw should degrade to 香, never take the log line with it.
        reconfigure(line_buffering=True, encoding="utf-8",
                    errors="backslashreplace")
    except (ValueError, OSError):
        # Already detached, or not a real text stream. Nothing to fix.
        pass
    return stream


def _install_excepthooks(who: str) -> None:
    """Route uncaught exceptions into the log instead of onto bare stderr.

    Without this, a thread that dies takes its traceback to stderr where the
    ring never sees it, and the developer panel shows a process that simply
    went quiet. Doc section 12.8 exists to answer "what happened", and this
    is most of the answer.

    KeyboardInterrupt is passed through untouched: Ctrl-C is the documented
    shutdown (doc section 10.2), and a stack trace for a normal stop trains
    everyone to ignore tracebacks.
    """
    global _prev_hooks
    previous = sys.excepthook
    _prev_hooks = (sys.excepthook, threading.excepthook)

    def hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            previous(exc_type, exc, tb)
            return
        logging.getLogger(who).critical("uncaught exception",
                                        exc_info=(exc_type, exc, tb))

    sys.excepthook = hook

    def thread_hook(args):
        if issubclass(args.exc_type, SystemExit):
            return
        logging.getLogger(who).critical(
            "uncaught exception in thread %s", args.thread and args.thread.name,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    threading.excepthook = thread_hook
