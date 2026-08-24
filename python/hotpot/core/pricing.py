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

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from hotpot.common import atomicio
from hotpot.core.binmap import DEFAULT_CONF_FLOOR, BinMap
from hotpot.core.i18n import DEFAULT_LOCALE as FALLBACK_LOCALE

if TYPE_CHECKING:
    from hotpot.core.cart import Cart

log = logging.getLogger("hotpot.pricing")

# 4 -> 5, 2026-08-24: every item gained `diet`/`kcalPer100g`/`description`
# for VISUAL_LAYER.md section 8's info box. Bumped because the file's
# shape changed, this repo's standing practice on any catalogue change.
CATALOGUE_SCHEMA = 5

# The three answers `Item.diet` may hold. Three rather than two: an egg is
# neither veg nor non-veg in most of the world this table is aimed at, and
# collapsing it into either one would be stating something false to the
# one person who cares about the distinction.
VALID_DIETS = {"veg", "nonveg", "egg"}


@dataclass(frozen=True)
class Item:
    """One catalogue entry. Two of these five fields are **hidden** and
    two are shown, and the split is the whole point of doc section 8.1.

    HIDDEN — never reaches a diner-facing surface, in any language:
        id          the catalogue's own key, and what BinMap stores.
        class_name  the label string the ML model emits.

    SHOWN — the only strings a diner ever reads:
        names       display name per locale.
        pinyin      romanisation of the `zh` name, en-locale only (doc
                    section 8.1) — a pronunciation aid, not a translation,
                    so it rides beside `names["zh"]` rather than inside it.

    `names` is **not** a translation of `id`. The label names a thing that
    is easy to photograph and train on; the display name is the hot pot
    ingredient it stands in for on the table. In this catalogue the two
    usually coincide (2026-08-13's real ingredient photos), same as `egg`
    always has — but that is still a coincidence of what was photographed,
    not a rule this class enforces, and nothing here derives one from the
    other.

    So there is no derivation from `id` to a display name, no
    prettifier, and no fallback that reaches for one. See display_name().
    """

    id: str
    price_per_100g: float
    names: Dict[str, str]
    tags: List[str]
    class_name: str
    pinyin: str = ""
    # VISUAL_LAYER.md section 8's info box: "Shows veg/non-veg, kcal, short
    # description for the active bin." SHOWN, all three — they are read by
    # a diner off the projected table, so they belong on the same side of
    # doc section 8.1's split as `names`, and nothing here may be derived
    # from `id`/`class_name` either.
    #
    # **`diet` is an explicit field, deliberately NOT derived from
    # `tags`.** The tags are a loose editorial list ("vegetarian", "vegan",
    # "seafood", "noodle") that nothing validates; deriving from them would
    # make "is this safe for me to eat" a side effect of whether somebody
    # remembered a tag. It is also the one field here where a wrong answer
    # is not a cosmetic bug — `chicken_eggs` carries the tag "vegetarian"
    # and a derivation would have projected "VEG" onto an egg.
    # "veg" | "nonveg" | "egg" — three, not two, because egg is neither in
    # most of the world this table is aimed at.
    #
    # `kcal_per_100g` is a per-100g figure to match `price_per_100g`, and
    # is an approximate published value for the REAL ingredient the plate
    # names, not a lab measurement of what is in the bin — see
    # data/catalogue.json's own entries and CLAUDE.md for the sourcing.
    diet: str = ""
    kcal_per_100g: float = 0.0
    description: str = ""

    def display_name(self, locale: Optional[str] = None) -> str:
        """The label the table prints. **Cannot return `id` or
        `class_name`** — that is this method's entire reason for existing.

        Falling back to `id` is what core/main.py used to do, and it put
        the hidden training label onto the projected surface the moment a
        locale was missing one name: a diner reading "soya_chunks" off a
        plate. The chain here ends at the default locale instead, which
        `Catalogue.load()` guarantees is present for every item, so it is
        total and never has to reach past the `names` dict.

        Mirrors Locales.translate()'s policy deliberately — try the
        locale, fall back to the default, log once — because a bin label
        and a UI string degrading differently under the same missing
        locale would be its own bug.
        """
        loc = locale or FALLBACK_LOCALE
        name = self.names.get(loc)
        if name:
            return name
        fallback = self.names.get(FALLBACK_LOCALE)
        if fallback:
            log.warning(
                "catalogue: item %r has no %r name, showed the %r one",
                self.id, loc, FALLBACK_LOCALE)
            return fallback
        # Unreachable via Catalogue.load(), which refuses an item without
        # a FALLBACK_LOCALE name. Hand-built Items in tests can still get
        # here, and even they do not get to leak the label.
        raise ValueError(
            f"item {self.id!r} has no {FALLBACK_LOCALE!r} display name")


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
        items = []
        for it in raw["items"]:
            names = dict(it["names"])
            # The one thing that makes Item.display_name() total, and so
            # the one thing standing between a missing translation and the
            # hidden training label being projected onto a plate. Checked
            # here, at startup, because catalogue.json is committed data
            # (doc section 8): a missing display name is an editing
            # mistake, and it should stop core on the bench rather than
            # surface mid-service as a plate reading "soya_chunks".
            if not names.get(FALLBACK_LOCALE):
                raise ValueError(
                    f"{path}: item {it['id']!r} has no {FALLBACK_LOCALE!r} "
                    f"display name. Every item needs one — it is the "
                    f"fallback every other locale degrades to, and without "
                    f"it there is no name to show but the hidden label.")
            # The info box's three fields, required — same argument the
            # `en` name check above makes: catalogue.json is committed
            # data, so a missing one is an editing mistake that should
            # stop core on the bench rather than project a blank box, or
            # (worse, for `diet`) nothing at all where a diner is looking
            # for whether they can eat it. `VALID_DIETS` is checked for
            # the same reason: a typo'd "vegetarian" would silently draw
            # neither veg nor non-veg.
            diet = it.get("diet")
            if diet not in VALID_DIETS:
                raise ValueError(
                    f"{path}: item {it['id']!r} has diet {diet!r}; expected "
                    f"one of {sorted(VALID_DIETS)}. This is the one field a "
                    f"diner may act on, so it is never guessed or derived "
                    f"from `tags`.")
            if "kcalPer100g" not in it or not str(it.get("description", "")).strip():
                raise ValueError(
                    f"{path}: item {it['id']!r} needs kcalPer100g and a "
                    f"description — VISUAL_LAYER.md section 8's info box "
                    f"shows both, and a blank one reads as a broken table.")
            items.append(Item(
                id=it["id"],
                price_per_100g=float(it["pricePer100g"]),
                names=names,
                tags=list(it.get("tags", [])),
                class_name=it["class_name"],
                pinyin=it.get("pinyin", ""),
                diet=diet,
                kcal_per_100g=float(it["kcalPer100g"]),
                description=str(it["description"]).strip(),
            ))
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
