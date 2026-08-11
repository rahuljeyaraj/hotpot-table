"""common/framebus.py — the shared-memory frame ring (doc section 6).

M3 build item 1. Single producer (`camera`, doc section 3), multiple
readers (`tracker`, `classifier`). Lock-free, seqlock-verified: the
writer publishes a frame by writing pixels, then the slot header, then
incrementing `write_counter` **last** (doc section 6.2) — that last
write is what a reader treats as "the frame exists" (doc section 6.3's
`c1 = write_counter`), so it has to happen after everything it
publishes, never before. A reader reads the slot's `frame_id` before and
after copying pixels and retries if the two disagree: the writer lapped
it mid-copy. With `slot_count` slots at 30fps a reader has ~260ms before
it can be lapped (doc section 6.3), so tearing is expected to be
unobservable in practice — the retry exists for the case it happens
anyway, and `FrameReader._torn_read_hook` exists so a test can force
that case rather than trust it never occurs (the same TRAP doc section
5.3 warns about for the homography: a check that cannot fail proves
nothing).

Nobody but camera writes and nobody but tracker/classifier reads.
`core` never imports this module — I3 ("core never touches a frame") is
enforced by that omission, not by a runtime check, the same way I2 keeps
pricing logic out of `of/`.

Everything here works with no camera attached and no other process
running: a `FrameWriter` and a `FrameReader` can share a ring inside one
process and one test, the same way `core/scale.py`'s `feed()` needs no
XIAO.
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass
from multiprocessing import shared_memory
from typing import Callable, Optional

# doc section 4.1
SHM_NAME = "hotpot_frames"

# doc section 6.1
MAGIC = 0x48505446          # 'HPTF'
VERSION = 3
DEFAULT_SLOT_COUNT = 8
CHANNELS = 3                 # BGR

# doc section 6.4: "if now - ts_ns > 500ms, the camera is dead or stalled".
DEFAULT_STALE_S = 0.5

# How many times `FrameReader.read()` retries a torn read before giving
# up. Doc section 6.3 puts the real budget at ~260ms of slack at 8
# slots/30fps; a handful of in-process retries is far more than that
# situation should ever need, and giving up rather than looping forever
# is what keeps a reader that is somehow being lapped every single frame
# from hanging the tracker/classifier main loop.
DEFAULT_MAX_RETRIES = 4

# doc section 6.1's header, up to (not including) the slot header array:
#   magic, version, width, height, channels, slot_count : u32 x6
#   write_counter                                        : u64
#   reserved                                              : 32 bytes
_HEADER_FMT = "<6IQ32s"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)
assert _HEADER_SIZE == 64, _HEADER_SIZE

# write_counter's own offset within the header, so it can be read/written
# on its own without repacking the whole 64 bytes on every frame.
_WRITE_COUNTER_FMT = "<Q"
_WRITE_COUNTER_OFFSET = 24

# [frame_id:u64][ts_ns:u64][ready:u32][pad:u32], one per slot.
_SLOT_HEADER_FMT = "<QQII"
_SLOT_HEADER_SIZE = struct.calcsize(_SLOT_HEADER_FMT)
assert _SLOT_HEADER_SIZE == 24, _SLOT_HEADER_SIZE


def _frame_size(width: int, height: int, channels: int) -> int:
    return width * height * channels


def _total_size(width: int, height: int, slot_count: int, channels: int) -> int:
    return (_HEADER_SIZE
            + slot_count * _SLOT_HEADER_SIZE
            + slot_count * _frame_size(width, height, channels))


@dataclass(frozen=True)
class Frame:
    """One frame as a reader sees it.

    `data` is always a fresh `bytes` copy, never a view into the ring —
    the writer is free to overwrite that slot the instant `read()`
    returns, and a caller holding a view into shared memory across that
    would be reading someone else's frame without knowing it.
    """

    frame_id: int
    ts_ns: int
    data: bytes


class FrameWriter:
    """The producer. Doc section 6.2.

    Creating a `FrameWriter` for a `name` that already has a live ring
    raises `FileExistsError` — `multiprocessing.shared_memory`'s own
    guard, and the correct one: doc section 3 makes `camera` the single
    owner of the ring, so two writers racing for the same name is a
    startup bug (two camera processes), not a case to paper over.
    """

    def __init__(self, width: int, height: int, *, name: str = SHM_NAME,
                 slot_count: int = DEFAULT_SLOT_COUNT,
                 channels: int = CHANNELS) -> None:
        if width <= 0 or height <= 0:
            raise ValueError("width and height must be positive")
        if channels <= 0:
            raise ValueError("channels must be positive")
        if slot_count < 1:
            raise ValueError("slot_count must be at least 1")
        self.name = name
        self.width = width
        self.height = height
        self.channels = channels
        self.slot_count = slot_count
        self.frame_size = _frame_size(width, height, channels)
        self._next_frame_id = 0

        self._shm = shared_memory.SharedMemory(
            name=name, create=True,
            size=_total_size(width, height, slot_count, channels))
        self._buf = self._shm.buf
        struct.pack_into(_HEADER_FMT, self._buf, 0,
                          MAGIC, VERSION, width, height, channels,
                          slot_count, 0, bytes(32))

    def _slot_header_offset(self, slot: int) -> int:
        return _HEADER_SIZE + slot * _SLOT_HEADER_SIZE

    def _slot_pixel_offset(self, slot: int) -> int:
        return (_HEADER_SIZE + self.slot_count * _SLOT_HEADER_SIZE
                + slot * self.frame_size)

    def write(self, pixels: bytes, ts_ns: Optional[int] = None) -> int:
        """Publish one frame. Returns its `frame_id`.

        Doc section 6.2, in order — pixels, then the slot header, then
        `write_counter` — because `write_counter` being visible to a
        reader is the definition of "published" (doc section 6.3), so
        everything the frame needs must already be in place before it
        moves.
        """
        if len(pixels) != self.frame_size:
            raise ValueError(
                f"expected {self.frame_size} bytes "
                f"({self.width}x{self.height}x{self.channels}), "
                f"got {len(pixels)}")
        ts_ns = time.time_ns() if ts_ns is None else ts_ns
        frame_id = self._next_frame_id
        slot = frame_id % self.slot_count

        px_off = self._slot_pixel_offset(slot)
        self._buf[px_off:px_off + self.frame_size] = pixels

        hdr_off = self._slot_header_offset(slot)
        struct.pack_into(_SLOT_HEADER_FMT, self._buf, hdr_off,
                          frame_id, ts_ns, 1, 0)

        self._next_frame_id = frame_id + 1
        struct.pack_into(_WRITE_COUNTER_FMT, self._buf,
                          _WRITE_COUNTER_OFFSET, self._next_frame_id)
        return frame_id

    def close(self) -> None:
        self._shm.close()

    def unlink(self) -> None:
        """Remove the segment. POSIX only removes the name here; Windows
        frees it once every handle (this writer's and every reader's) is
        closed and this call is a no-op. Call it unconditionally either
        way — that is what makes camera's shutdown path portable.
        """
        self._shm.unlink()


class FrameReader:
    """A consumer: `tracker` or `classifier` (doc section 6.3).

    Attaches to a ring `FrameWriter` already created. Raises
    `FileNotFoundError` if none exists yet — the same "nothing is
    listening" case as a control-link client connecting before core is
    up, and the caller's job, not this constructor's, to retry.
    """

    def __init__(self, name: str = SHM_NAME, *,
                 max_retries: int = DEFAULT_MAX_RETRIES) -> None:
        if max_retries < 1:
            raise ValueError("max_retries must be at least 1")
        try:
            self._shm = shared_memory.SharedMemory(name=name, create=False)
        except FileNotFoundError as e:
            raise FileNotFoundError(
                f"no frame ring named {name!r} — "
                f"is the camera process running?") from e
        self._buf = self._shm.buf
        self.name = name
        self.max_retries = max_retries

        (magic, version, width, height, channels, slot_count,
         _write_counter, _reserved) = struct.unpack_from(
            _HEADER_FMT, self._buf, 0)
        if magic != MAGIC:
            self._shm.close()
            raise ValueError(
                f"{name!r} is not a hotpot frame ring "
                f"(bad magic {magic:#x}, expected {MAGIC:#x})")
        if version != VERSION:
            self._shm.close()
            raise ValueError(
                f"{name!r} is frame-ring version {version}, "
                f"this code speaks version {VERSION}")
        self.width = width
        self.height = height
        self.channels = channels
        self.slot_count = slot_count
        self.frame_size = _frame_size(width, height, channels)

        # Testing seam, not a production feature: called between the
        # pixel copy and the second frame_id check in `read()`, so a
        # test can inject a concurrent write and prove the retry catches
        # a torn read rather than merely trust that it would (the TRAP
        # doc section 5.3 warns about generally: a check that passes
        # regardless of correctness is not a check).
        self._torn_read_hook: Optional[Callable[[], None]] = None

    def _slot_header_offset(self, slot: int) -> int:
        return _HEADER_SIZE + slot * _SLOT_HEADER_SIZE

    def _slot_pixel_offset(self, slot: int) -> int:
        return (_HEADER_SIZE + self.slot_count * _SLOT_HEADER_SIZE
                + slot * self.frame_size)

    def _write_counter(self) -> int:
        return struct.unpack_from(
            _WRITE_COUNTER_FMT, self._buf, _WRITE_COUNTER_OFFSET)[0]

    def _read_slot_header(self, slot: int):
        off = self._slot_header_offset(slot)
        frame_id, ts_ns, _ready, _pad = struct.unpack_from(
            _SLOT_HEADER_FMT, self._buf, off)
        return frame_id, ts_ns

    def read(self) -> Optional[Frame]:
        """Doc section 6.3. Never blocks.

        Returns `None` if nothing has been published yet, or if every
        retry lost the race to the writer — at 30fps and `slot_count`
        slots that means something is very wrong upstream, and the
        correct response is to say so, not to spin until it resolves.
        """
        for _ in range(self.max_retries):
            c1 = self._write_counter()
            if c1 == 0:
                return None
            slot = (c1 - 1) % self.slot_count
            frame_id1, ts_ns = self._read_slot_header(slot)
            px_off = self._slot_pixel_offset(slot)
            data = bytes(self._buf[px_off:px_off + self.frame_size])
            if self._torn_read_hook is not None:
                self._torn_read_hook()
            frame_id2, _ts_ns2 = self._read_slot_header(slot)
            if frame_id1 == frame_id2:
                return Frame(frame_id=frame_id1, ts_ns=ts_ns, data=data)
            # Torn: the writer published into this same slot while the
            # copy was in flight. Retry — doc section 6.3's "discarded
            # rather than silently corrupted".
        return None

    def peek_ts_ns(self) -> Optional[int]:
        """The newest published frame's timestamp, with no pixel copy.

        Doc section 6.4's staleness check is meant to run every tick of
        a consumer's main loop; copying a full frame just to read one
        timestamp would be the tail wagging the dog.
        """
        c1 = self._write_counter()
        if c1 == 0:
            return None
        slot = (c1 - 1) % self.slot_count
        _frame_id, ts_ns = self._read_slot_header(slot)
        return ts_ns

    def is_stale(self, *, timeout_s: float = DEFAULT_STALE_S,
                 now_ns: Optional[int] = None) -> bool:
        """Doc section 6.4: true if nothing has ever been published, or
        the newest frame is older than `timeout_s`.

        This function only answers the question; doc section 6.4's three
        consumer duties (stop emitting, report `frames_stale`, keep
        polling) belong to `tracker`/`classifier`, not here.
        """
        ts_ns = self.peek_ts_ns()
        if ts_ns is None:
            return True
        now_ns = time.time_ns() if now_ns is None else now_ns
        return (now_ns - ts_ns) > timeout_s * 1e9

    def close(self) -> None:
        self._shm.close()
