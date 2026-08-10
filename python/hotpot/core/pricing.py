"""core/pricing.py — the arithmetic in doc section 9.2, and nothing else.

bin_price() and total() are pure: no I/O, no state held between calls.
That is the point of keeping this module separate from cart.py — I4,
*price is cumulative and absolute*, means the total is always recomputed
fresh from Cart.start_g/live_g through a Catalogue lookup, never
accumulated from individual pick events. There is no running total stored
anywhere; call total() again whenever a fresh number is needed.

Catalogue also lives here rather than in its own module, because pricing
is the one M1 consumer that needs prices out of data/catalogue.json (doc
section 8.1) — binmap.py only ever needs item ids, never pricePer100g.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from hotpot.common import atomicio
from hotpot.core.binmap import DEFAULT_CONF_FLOOR, BinMap

if TYPE_CHECKING:
    from hotpot.core.cart import Cart

CATALOGUE_SCHEMA = 3


@dataclass(frozen=True)
class Item:
    id: str
    price_per_100g: float
    names: Dict[str, str]
    tags: List[str]
    class_name: str


class Catalogue:
    """Every item that could ever be in a bin (doc section 8.1) — not
    which bin it is in; that is BinMap's job. data/catalogue.json is
    committed, not machine-written (doc section 8), so there is no
    watch-for-changes machinery here: a catalogue edit ships with a
    restart, same as every other file under config/ and data/.
    """

    def __init__(self, items: List[Item]) -> None:
        self._by_id = {it.id: it for it in items}

    @classmethod
    def load(cls, path: Any) -> "Catalogue":
        raw = atomicio.read_json(path)
        schema = raw.get("schema")
        if schema != CATALOGUE_SCHEMA:
            raise ValueError(
                f"{path}: schema {schema!r}, expected {CATALOGUE_SCHEMA}")
        items = [
            Item(
                id=it["id"],
                price_per_100g=float(it["pricePer100g"]),
                names=dict(it["names"]),
                tags=list(it.get("tags", [])),
                class_name=it["class_name"],
            )
            for it in raw["items"]
        ]
        return cls(items)

    def item(self, item_id: Optional[str]) -> Optional[Item]:
        """None in, None out — an unresolved bin's item_id is None, and
        callers should not have to special-case that before asking.
        """
        if item_id is None:
            return None
        return self._by_id.get(item_id)

    def __len__(self) -> int:
        return len(self._by_id)

    def ids(self) -> List[str]:
        """Every item id, in the order catalogue.json listed them.

        Doc section 21 build item 2 didn't need this — build item 3's mock
        bin seed (core/main.py) does: it pairs bins with catalogue items
        one-to-one and needs a stable order to do it from, and dict
        insertion order (preserved since `_by_id` is built from the raw
        item list) is the least surprising source of one.
        """
        return list(self._by_id.keys())


def bin_price(removed_g: float, price_per_100g: float) -> float:
    """Doc section 9.2, line 2, exactly."""
    return (removed_g / 100.0) * price_per_100g


def total(cart: "Cart", binmap: BinMap, catalogue: Catalogue,
          *, conf_floor: float = DEFAULT_CONF_FLOOR) -> float:
    """Doc section 9.2, line 3: sum bin_price() over every *resolved* bin.

    Unresolved (doc section 9.3: no item_id, or conf below conf_floor)
    contributes 0.00 no matter how much mass has left it. That is checked
    here via BinMap.resolved() — not by skipping only bins with
    item_id is None — so a bin that was resolved and then fails
    reclassification also stops billing rather than billing on stale data.
    """
    grand = 0.0
    for i, b in enumerate(binmap.bins):
        if not binmap.resolved(i, conf_floor):
            continue
        item = catalogue.item(b.item_id)
        if item is None:
            continue
        grand += bin_price(cart.removed_grams(i), item.price_per_100g)
    return grand
