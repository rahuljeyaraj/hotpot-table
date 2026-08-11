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


def display_grams(shown_g: float) -> float:
    """The gram figure the table actually prints, as a number.

    The projected plate has no room for a decimal and a diner reading
    "45.4g" learns nothing "45g" did not tell them, so the display rounds.
    It is a function rather than a round() at the call site because the
    *price* printed beside those grams has to be computed from this exact
    value — see shown_total(). Doc section 21's M1 acceptance test asks a
    human to check the table "by arithmetic, not by watching", and that
    only works if the grams they read and the money they read came from
    one number.
    """
    return float(round(shown_g))


def _sum_resolved(cart: "Cart", binmap: BinMap, catalogue: Catalogue,
                  conf_floor: float, grams_of: Any) -> float:
    """Doc section 9.2, line 3: sum bin_price() over every *resolved* bin.

    Unresolved (doc section 9.3: no item_id, or conf below conf_floor)
    contributes 0.00 no matter how much mass has left it. That is checked
    here via BinMap.resolved() — not by skipping only bins with
    item_id is None — so a bin that was resolved and then fails
    reclassification also stops billing rather than billing on stale data.

    `grams_of` is what separates the two public callers below, and it is
    the only thing that separates them.
    """
    grand = 0.0
    for i, b in enumerate(binmap.bins):
        if not binmap.resolved(i, conf_floor):
            continue
        item = catalogue.item(b.item_id)
        if item is None:
            continue
        grand += bin_price(grams_of(i), item.price_per_100g)
    return grand


def total(cart: "Cart", binmap: BinMap, catalogue: Catalogue,
          *, conf_floor: float = DEFAULT_CONF_FLOOR) -> float:
    """**The billed number.** Doc section 9.2 exactly: true removed grams,
    the deadband nowhere near it (I5: "it never enters price maths").

    This is what an order is written to SQLite from (M6). Nothing that
    only gets looked at may use it — see shown_total() for that.
    """
    return _sum_resolved(cart, binmap, catalogue, conf_floor,
                          cart.removed_grams)


def shown_total(cart: "Cart", binmap: BinMap, catalogue: Catalogue,
                *, conf_floor: float = DEFAULT_CONF_FLOOR) -> float:
    """**The number on the table.** Same formula, fed the deadbanded grams.

    Why this exists rather than displaying total(): the deadband (I5) is
    there so the projected number stops twitching, and a number is not
    just the grams — the running total is the largest thing on the table
    (doc section 13.4: 80px). Printing deadbanded grams beside a price
    computed from true grams gives a plate that contradicts itself (45g
    next to a 51g price), and once real load cells replace the mock at M2
    their noise would move that price continuously while the grams beside
    it sat still. Both failures are the deadband not doing its one job.

    I5 is not weakened by this: the deadband still never enters *price*
    maths — total() above is untouched and is what bills. The two
    converge at order finalisation, because Cart.finalize() sets
    shown_g[i] = removed_grams(i) for every bin unconditionally (doc
    section 9.2's fix for open debt #5). So the diner is never shown less
    than they are charged for; they are shown it slightly later.
    """
    return _sum_resolved(cart, binmap, catalogue, conf_floor,
                          lambda i: display_grams(cart.shown_g[i]))
