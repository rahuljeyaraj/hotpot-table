"""Durable writes: temp file, fsync, rename (doc sections 8, 19.3, 20.4).

Every machine write in this system goes through here. The rule from doc
section 8 is one sentence long — *a power cut mid-write must never produce a
half-written homography* — and the reason it is a rule rather than a nicety
is doc section 20.4: a corrupt calibration does not crash the table, it
silently mis-bills. A file that is missing is a first boot. A file that
is truncated is a wrong price nobody notices.

The sequence, and why each step is there
----------------------------------------
    1. serialise to bytes in memory   nothing touches the disk until the
                                      whole object is known to be encodable
    2. write to <name>.tmp            the destination is never opened for
                                      writing, so it cannot be truncated
    3. flush + fsync the temp         the bytes are on the platter, not in
                                      the page cache
    4. os.replace(tmp, dest)          atomic on POSIX and on NTFS: a reader
                                      sees the old file or the new one
    5. fsync the directory            makes the *rename* durable, POSIX only

Step 1 is not decoration. `json.dump(obj, open(path, "w"))` on an object
containing something unserialisable writes half a file and then raises, which
is exactly the corruption this module exists to prevent, arriving by way of a
typo instead of a power cut.

Step 5 is the one people leave out. Without it, ext4 can come back after a
power cut with the rename undone and the file gone entirely. That is the
failure we want: gone is loud, and doc section 9.1 already routes a missing
`homography.json` to UNCALIBRATED. Truncated is the failure we do not.

Concurrency
-----------
The temp name is fixed at `<name>.tmp`, per doc section 8, which assumes
one writer process per file. That holds by design: core writes everything
in `state/` except `camera_settings.json`, which the camera process owns
(doc sections 6.6, 8.5, M2). Two processes writing one path would race on the
temp file, and no naming scheme fixes that properly — they would also be
racing on the meaning of the file. If that ever becomes necessary, it needs a
lock, not a longer suffix.

A crash between steps 2 and 4 leaves a stale `<name>.tmp`. That is harmless:
nothing reads it, and the next write overwrites it.

Missing versus corrupt
----------------------
`read_json` returns the caller's default when the file does not exist,
and raises when it exists and does not parse. First boot with an empty
`state/` is normal and expected (doc section 9.1). A file that is present but
unreadable is the thing this module was built to make impossible, so if one
ever appears it must stop the process it was read by, not quietly become a
default that prices a bowl of food.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Union

PathLike = Union[str, "os.PathLike[str]"]

log = logging.getLogger("hotpot.atomicio")

# Doc section 8. Kept as a constant because run.py may one day want to sweep
# stale ones at startup, and because a test should not spell it twice.
TEMP_SUFFIX = ".tmp"

# Sentinel for "no default given", so that None can be a legitimate default.
_MISSING = object()


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def write_bytes(path: PathLike, data: bytes) -> None:
    """Write `data` to `path` atomically and durably.

    The primitive the rest of this module is built on. On return the bytes
    are on the disk under the real name, or an exception was raised and the
    previous contents of `path` are untouched. There is no third outcome.

    Parent directories are created. `state/` is gitignored (doc section 8),
    so a fresh clone has nowhere to put the first calibration, and failing
    that write would be a confusing way to learn it.
    """
    dest = os.fspath(path)
    parent = os.path.dirname(os.path.abspath(dest))
    os.makedirs(parent, exist_ok=True)

    # Sibling, not the system temp dir: os.replace cannot cross a filesystem
    # boundary, and on this box /tmp is very often a different one.
    tmp = dest + TEMP_SUFFIX

    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dest)
    except BaseException:
        # Leave the destination as it was. Removing the temp is tidiness, and
        # it must never mask the real error, hence the bare except around it.
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise

    _fsync_dir(parent)


def write_text(path: PathLike, text: str) -> None:
    """UTF-8 text, atomically. Newlines are written as-is, no translation."""
    write_bytes(path, text.encode("utf-8"))


def write_json(path: PathLike, obj: Any, *, indent: int = 2,
               sort_keys: bool = False) -> None:
    """One JSON object to one file, atomically.

    Indented and newline-terminated on purpose. These files are read by a
    human at 2am during bring-up far more often than they are read by the
    program, and `git diff` on `config/` wants line granularity.

    `ensure_ascii` is off for the same reason as in wire.py: bin labels are
    Chinese, and `\\u9999\\u83c7` in `bin_map.json` helps nobody.
    """
    text = json.dumps(obj, indent=indent, sort_keys=sort_keys,
                      ensure_ascii=False) + "\n"
    write_text(path, text)


def append_json_line(path: PathLike, obj: Any) -> None:
    """Append one JSONL line and fsync it (doc section 19.3).

    The write-ahead journal is the one file that is *not* rewritten
    atomically, because rewriting it would defeat its purpose. Appending a
    single line to a file that was consistent leaves it consistent: the line
    is either wholly there or wholly absent, and the reader in doc section
    19.3 discards a trailing partial line.

    The fsync is per line and deliberate. At the doc section 19.3 rate — one
    snapshot every 2s — that is a rounding error of I/O, and it is what makes
    "a core crash mid-order loses at most 2 seconds" true rather than
    aspirational.
    """
    dest = os.fspath(path)
    parent = os.path.dirname(os.path.abspath(dest))
    os.makedirs(parent, exist_ok=True)

    line = json.dumps(obj, separators=(",", ":"), ensure_ascii=False) + "\n"
    with open(dest, "ab") as f:
        f.write(line.encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def read_json(path: PathLike, default: Any = _MISSING) -> Any:
    """Read one JSON file.

    Missing file: returns `default`, or raises FileNotFoundError if no
    default was given. Present but unparsable: always raises. See the module
    docstring for why those two cases are not allowed to look the same.
    """
    dest = os.fspath(path)
    try:
        with open(dest, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        if default is _MISSING:
            raise
        return default

    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        # Loud on the way past. Whoever gets the exception may well be a web
        # handler that turns it into a 500, and this line is the one that
        # says which file to go and look at.
        log.error("atomicio: %s exists but is not valid JSON", dest)
        raise


def read_json_lines(path: PathLike) -> list:
    """Every complete JSONL line in `path`. Missing file reads as empty.

    A trailing partial line is dropped, not raised on: it is the expected
    result of the process dying mid-append, which is precisely the case the
    journal exists to survive (doc section 19.3). A *non-final* line that
    does not parse is corruption of a different kind and does raise.
    """
    dest = os.fspath(path)
    try:
        with open(dest, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        return []

    text = raw.decode("utf-8", errors="replace")
    lines = text.split("\n")
    # A well-formed file ends with a newline, so the final element is "".
    # Anything else there is a torn write and is discarded.
    tail = lines.pop()
    if tail.strip():
        log.warning("atomicio: discarding a torn final line of %s", dest)

    out = []
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            log.error("atomicio: %s line %d is not valid JSON", dest, i + 1)
            raise
    return out


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _fsync_dir(path: str) -> None:
    """fsync a directory so a rename inside it survives a power cut.

    POSIX only. Windows has no handle you can open on a directory this way,
    and the deploy target is Linux (doc section 1.4) — Windows is the dev
    box, where the consequence of a power cut is a rebuild, not a wrong bill.

    A filesystem that refuses the fsync is logged and tolerated. Losing the
    durability of the rename is bad; refusing to save the calibration at all
    because the filesystem is unusual is worse.
    """
    if os.name != "posix":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError as e:
        log.debug("atomicio: cannot open %s to fsync it: %s", path, e)
        return
    try:
        os.fsync(fd)
    except OSError as e:
        log.debug("atomicio: cannot fsync %s: %s", path, e)
    finally:
        os.close(fd)
