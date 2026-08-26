"""core/menu.py — the broths and spice levels the checkout flow offers.

Doc section 18.1: "BROTH: large projected plates with names and a colour
swatch each. Dwell to choose." / "SPICE: four plates, 0-3, with chilli
glyphs."

Separate from `pricing.Catalogue` on purpose. A broth is not an item in a
bin: nothing weighs it, nothing prices it per 100g, and `Catalogue.load`
requires a price on every entry it holds (see its own docstring). Putting
them in one file would mean either giving a broth a fake price or
loosening the check that stops an unpriced ingredient from reaching a
plate.

Both lists are DATA, not code, for the same reason the catalogue is: the
strings reach a diner's eyes, and doc section 17.3 says Chinese judges
will read them. `zh` is carried here already even though nothing renders
it yet (M1.4's English-only scope, unchanged) so the file's shape does
not have to change when a locale lands.

**`diet` on a broth is inferred from its NAME and is not confirmed.**
Classic Mala is traditionally a beef-tallow base and Collagen Bone Broth
is bone, so both are marked nonveg; the two vegetarian ones say so in
their own names. This is the one field here where being wrong is not
cosmetic, so it is flagged in data/menu.json as well as here. Confirm it
with the restaurant before a real diner reads it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[3]
MENU_PATH = _ROOT / "data" / "menu.json"

MENU_SCHEMA = 1

# `pricing.VALID_DIETS` is the same three values and the same rule (see
# `Item.diet`). Imported rather than restated so a fourth diet cannot be
# added to one file and not the other.
from hotpot.core.pricing import VALID_DIETS  # noqa: E402


@dataclass(frozen=True)
class Broth:
    """One broth option.

    `note` and `meta` are the info box's two strings, and follow exactly
    the rules `pricing.Item.description` sets out: ASCII only (UiLayer
    loads Latin1Supplement + CurrencySymbols, so an em-dash silently does
    not render on the table), and never phrased as an instruction — the
    kitchen cooks this, the diner only chooses it.

    `meta` fills the info box's right-hand slot, where a bin puts its
    kcal figure. For a broth the useful number there is not calories but
    how hot it is, which is the thing a diner is actually deciding
    between — see `core/main.py`'s `info` payload, where the field is
    called `meta` for exactly that reason.

    `swatch` is doc section 18.1's "colour swatch each". Held as a hex
    string and parsed on the oF side, the same shape every other colour
    that crosses this wire uses.
    """

    id: str
    names: Dict[str, str]
    diet: str
    meta: str
    note: str
    swatch: str
    pinyin: str = ""
    # 2026-08-26: `zh` siblings of `meta`/`note`, same reasoning as
    # `pricing.Item.description_zh` — `meta`/`note` predate the locale
    # switch as plain strings, so their translations ride beside them
    # rather than turning both into `names`-style dicts. Optional; see
    # `meta_text()`/`note_text()`.
    meta_zh: str = ""
    note_zh: str = ""

    def display_name(self, locale: Optional[str] = None) -> str:
        if locale:
            name = self.names.get(locale, "").strip()
            if name:
                return name
        return self.names["en"]

    def meta_text(self, locale: Optional[str] = None) -> str:
        if locale == "zh" and self.meta_zh.strip():
            return self.meta_zh
        return self.meta

    def note_text(self, locale: Optional[str] = None) -> str:
        if locale == "zh" and self.note_zh.strip():
            return self.note_zh
        return self.note


@dataclass(frozen=True)
class SpiceLevel:
    """One spice level, 0-3.

    Doc section 17: "Spice level 0-3, with 0 explicitly available as
    plain broth. Many shops offer a level 0 with no numbing at all, and
    this is a normal, expected choice." So level 0 is a first-class
    option with its own note, never a greyed-out "none".

    No `diet` — a spice level is not food and has nothing to say about
    it. The info box draws the diet mark only when there is one, rather
    than drawing a blank dot (see `UiLayer::drawInfoBox`).
    """

    level: int
    names: Dict[str, str]
    meta: str
    note: str
    pinyin: str = ""
    meta_zh: str = ""
    note_zh: str = ""

    def display_name(self, locale: Optional[str] = None) -> str:
        if locale:
            name = self.names.get(locale, "").strip()
            if name:
                return name
        return self.names["en"]

    def meta_text(self, locale: Optional[str] = None) -> str:
        if locale == "zh" and self.meta_zh.strip():
            return self.meta_zh
        return self.meta

    def note_text(self, locale: Optional[str] = None) -> str:
        if locale == "zh" and self.note_zh.strip():
            return self.note_zh
        return self.note


@dataclass
class Menu:
    broths: List[Broth] = field(default_factory=list)
    spice_levels: List[SpiceLevel] = field(default_factory=list)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Menu":
        """Load and VALIDATE. A bad file stops core on the bench.

        Same argument `Catalogue.load` makes: this is committed data, so
        a missing field is an editing mistake, and the alternative is a
        broth plate that projects a blank name mid-service. Every check
        below names the offending id so the message is actionable.
        """
        p = Path(path) if path is not None else MENU_PATH
        with open(p, "r", encoding="utf-8") as f:
            doc = json.load(f)
        if doc.get("schema") != MENU_SCHEMA:
            raise ValueError(
                f"{p}: schema {doc.get('schema')!r}, expected {MENU_SCHEMA}")

        broths: List[Broth] = []
        for raw in doc.get("broths", []):
            bid = str(raw.get("id", "")).strip()
            if not bid:
                raise ValueError(f"{p}: a broth has no id")
            names = raw.get("names") or {}
            if not str(names.get("en", "")).strip():
                raise ValueError(f"{p}: broth {bid!r} has no English name")
            diet = str(raw.get("diet", "")).strip()
            if diet not in VALID_DIETS:
                raise ValueError(
                    f"{p}: broth {bid!r} has diet {diet!r}, expected one of "
                    f"{sorted(VALID_DIETS)} — a diner may act on this one.")
            note = str(raw.get("note", "")).strip()
            meta = str(raw.get("meta", "")).strip()
            if not note or not meta:
                raise ValueError(
                    f"{p}: broth {bid!r} needs a note and a meta — both show "
                    f"in the info box and a blank one reads as broken.")
            broths.append(Broth(
                id=bid, names={k: str(v) for k, v in names.items()},
                diet=diet, meta=meta, note=note,
                swatch=str(raw.get("swatch", "#CCCCCC")),
                pinyin=str(raw.get("pinyin", "")),
                meta_zh=str(raw.get("meta_zh", "")).strip(),
                note_zh=str(raw.get("note_zh", "")).strip(),
            ))
        if not broths:
            raise ValueError(f"{p}: no broths — BROTH would be a dead end")

        levels: List[SpiceLevel] = []
        for raw in doc.get("spice_levels", []):
            if "level" not in raw:
                raise ValueError(f"{p}: a spice level has no level number")
            lvl = int(raw["level"])
            names = raw.get("names") or {}
            if not str(names.get("en", "")).strip():
                raise ValueError(f"{p}: spice level {lvl} has no English name")
            note = str(raw.get("note", "")).strip()
            meta = str(raw.get("meta", "")).strip()
            if not note or not meta:
                raise ValueError(
                    f"{p}: spice level {lvl} needs a note and a meta.")
            levels.append(SpiceLevel(
                level=lvl, names={k: str(v) for k, v in names.items()},
                meta=meta, note=note, pinyin=str(raw.get("pinyin", "")),
                meta_zh=str(raw.get("meta_zh", "")).strip(),
                note_zh=str(raw.get("note_zh", "")).strip(),
            ))
        if not levels:
            raise ValueError(f"{p}: no spice levels — SPICE would be a dead end")
        # Doc section 17: level 0 must genuinely exist. Checked rather than
        # assumed, because "we offer a no-spice option" is a claim about
        # the restaurant, not about this file.
        if not any(l.level == 0 for l in levels):
            raise ValueError(
                f"{p}: no level 0 — doc section 17 requires a genuine "
                f"no-spice option, not a greyed-out one.")
        levels.sort(key=lambda l: l.level)
        return cls(broths=broths, spice_levels=levels)

    def broth(self, broth_id: str) -> Optional[Broth]:
        for b in self.broths:
            if b.id == broth_id:
                return b
        return None

    def spice(self, level: int) -> Optional[SpiceLevel]:
        for s in self.spice_levels:
            if s.level == level:
                return s
        return None

    def as_dict(self) -> Dict[str, Any]:
        """For the staff view's Orders tab, which shows what was chosen."""
        return {
            "broths": [{"id": b.id, "name": b.display_name()} for b in self.broths],
            "spice_levels": [{"level": s.level, "name": s.display_name()}
                             for s in self.spice_levels],
        }
