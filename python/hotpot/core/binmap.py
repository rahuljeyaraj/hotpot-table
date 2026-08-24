"""core/binmap.py — which catalogue item is in which bin (doc sections 8.2, 9.3).

BinMap holds exactly 8 `Bin` entries: item_id, conf, source. It knows
nothing about grams (that's cart.py) or prices (pricing.py) — its only
job is answering "is bin i billable right now", per doc section 9.3:

    unresolved  <=>  item_id is None  or  conf < conf_floor

`conf_floor` is a parameter of resolved(), not a field of BinMap: it lives
in config/system.json (doc section 8.6) and can change without a
bin_map.json write, and config loading is not built yet (see
common/stub.py's docstring) — DEFAULT_CONF_FLOOR is that doc default,
hardcoded here the same way core/main.py hardcodes its ports until a
config reader exists that needs more than one key.

M1 has no classifier (that arrives in M6) and no Setup-tab wizard (M4), so
every BinMap in this milestone is built by hand — a fixed seed for the
mock demo. Persistence (load/save against state/bin_map.json, doc section
8.2) is here now because the on-disk shape needs to be right from the
first write, even though M1's mock controls never call save().
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from hotpot.common import atomicio

NUM_BINS = 8
SCHEMA = 3

# core/binmap.py -> core -> hotpot -> python -> repo root. Same shape as
# `bin_grid.py`'s own grid paths and `geometry_store.py`'s homography.
# Doc section 8.2's file. **`save`/`load` existed from M1 and nothing
# called either until 2026-08-24** — see `core/main.py`'s own
# `_load_binmap`/`_save_binmap`, and this module's docstring above, which
# said outright that persistence was here early so the on-disk shape
# would be right from the first write. This is that first write.
_ROOT = Path(__file__).resolve().parents[3]
BIN_MAP_PATH = _ROOT / "state" / "bin_map.json"

# Doc section 8.6 default. Passed explicitly rather than stored so a
# BinMap never goes stale relative to a config file it does not read.
DEFAULT_CONF_FLOOR = 0.65


@dataclass
class Bin:
    """One row of doc section 8.2's bin_map.json."""

    i: int
    item_id: Optional[str] = None
    conf: float = 0.0
    source: str = "unset"          # "classifier" | "mock" | "manual"

    def to_json(self) -> Dict[str, Any]:
        return {"i": self.i, "item_id": self.item_id, "conf": self.conf,
                "source": self.source}

    @classmethod
    def from_json(cls, raw: Dict[str, Any]) -> "Bin":
        return cls(i=int(raw["i"]), item_id=raw.get("item_id"),
                    conf=float(raw.get("conf", 0.0)),
                    source=raw.get("source", "unset"))


class BinMap:
    """8 bins, always. A short BinMap is a bug, not a partially-set table —
    doc section 4.3: 'bins always has exactly 8 entries.'
    """

    def __init__(self, bins: Optional[List[Bin]] = None, *, locked: bool = False) -> None:
        self.bins: List[Bin] = bins if bins is not None else [Bin(i=i) for i in range(NUM_BINS)]
        if len(self.bins) != NUM_BINS:
            raise ValueError(f"BinMap needs exactly {NUM_BINS} bins, got {len(self.bins)}")
        self.locked = locked

    def resolved(self, i: int, conf_floor: float = DEFAULT_CONF_FLOOR) -> bool:
        """Doc section 9.3, unchanged."""
        b = self.bins[i]
        return b.item_id is not None and b.conf >= conf_floor

    def set_bin(self, i: int, item_id: Optional[str], conf: float, source: str) -> None:
        """Replace bin i wholesale — never patch a field in place, so a
        caller can never leave a Bin's conf and source disagreeing about
        which assignment they came from.
        """
        self.bins[i] = Bin(i=i, item_id=item_id, conf=conf, source=source)

    # -- persistence (doc section 8.2) --------------------------------------

    def to_json(self) -> Dict[str, Any]:
        return {"schema": SCHEMA, "written": time.time(), "locked": self.locked,
                "bins": [b.to_json() for b in self.bins]}

    @classmethod
    def from_json(cls, raw: Dict[str, Any]) -> "BinMap":
        bins = [Bin(i=i) for i in range(NUM_BINS)]
        for row in raw.get("bins", []):
            b = Bin.from_json(row)
            if 0 <= b.i < NUM_BINS:
                bins[b.i] = b
        return cls(bins, locked=bool(raw.get("locked", False)))

    def save(self, path: Any) -> None:
        atomicio.write_json(path, self.to_json())

    @classmethod
    def load(cls, path: Any) -> "BinMap":
        """A missing state/bin_map.json is a fresh clone (doc section 9.1),
        not an error: it reads back as 8 unresolved bins, same as a table
        that has never been calibrated.
        """
        raw = atomicio.read_json(path, default=None)
        if raw is None:
            return cls()
        return cls.from_json(raw)
