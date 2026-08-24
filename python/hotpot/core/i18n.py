"""core/i18n.py — locale strings and currency (doc section 17).

Two jobs, both existing because of I2 — core resolves every string before
it leaves core; oF and the staff view never do a lookup themselves:

    Locales.translate()   data/locales/<locale>.json, a flat key -> string
                           map (doc section 17.1).
    Locales.currency()    doc section 17.2: currency is a property of the
                           *locale*, not the catalogue. catalogue.json
                           prices are always the base currency (INR); a
                           locale's `_currency` block says how to show
                           that number in the diner's language.

M1 build item 1 (doc section 21) only wrote data/locales/en.json — English
only, per M1 build item 4. load() tolerates a missing locale file: it
falls back to `default` and logs once, rather than raising, because the
alternative (a missing zh.json crashing core outright) would make every
locale after the first a single point of failure for the whole table.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from hotpot.common import atomicio

log = logging.getLogger("hotpot.i18n")

DEFAULT_LOCALE = "en"


class Locales:
    """Every locale file loaded once at startup. data/locales/ is
    committed, not machine-written (doc section 8), so — like
    pricing.Catalogue — there is no watch-for-changes machinery: a
    wording change ships with a restart.
    """

    def __init__(self, tables: Dict[str, Dict[str, Any]], *,
                 default: str = DEFAULT_LOCALE) -> None:
        if default not in tables:
            raise ValueError(
                f"no {default!r} locale loaded — i18n needs its fallback present")
        self._tables = tables
        self.default = default

    @classmethod
    def load(cls, locales_dir: Any, locales: Sequence[str] = ("en", "zh"),
              *, default: str = DEFAULT_LOCALE) -> "Locales":
        tables: Dict[str, Dict[str, Any]] = {}
        for loc in locales:
            path = Path(locales_dir) / f"{loc}.json"
            raw = atomicio.read_json(path, default=None)
            if raw is None:
                log.warning("i18n: no %s.json in %s — falls back to %r where needed",
                            loc, locales_dir, default)
                continue
            tables[loc] = raw
        return cls(tables, default=default)

    def has(self, locale: str) -> bool:
        return locale in self._tables

    def available(self) -> List[str]:
        """Every locale actually loaded, sorted.

        Added at M5 for doc section 17.1's projected language button: it is
        offered only when there is somewhere to switch TO, and the honest
        answer to "how many locales are there" is "how many files loaded",
        not "how many the doc names". `zh.json` does not exist yet, so this
        returns one entry and the button renders disabled — and lights up
        by itself the day that file lands, with no code change.
        """
        return sorted(self._tables)

    def translate(self, key: str, locale: Optional[str] = None) -> str:
        """Doc section 17.1. Falls back to the default locale, then to the
        key itself, so a missing string is visibly wrong on the projected
        table rather than a blank label nobody notices.
        """
        loc = locale or self.default
        table = self._tables.get(loc)
        if table is not None and key in table:
            return table[key]
        if loc != self.default:
            fallback = self._tables.get(self.default)
            if fallback is not None and key in fallback:
                log.warning("i18n: locale %r missing key %r, used %r",
                            loc, key, self.default)
                return fallback[key]
        log.warning("i18n: key %r missing from every loaded locale", key)
        return key

    def currency_symbol(self, locale: Optional[str] = None) -> str:
        """Just the symbol. M6's order rows store which currency a total
        was taken in (doc section 9.7's `currency` column), and that is a
        different question from formatting an amount — recovering it by
        stripping the digits back off `currency()["text"]` would break the
        day a locale puts its symbol after the number.
        """
        loc = locale or self.default
        table = self._tables.get(loc) or self._tables.get(self.default) or {}
        return str(table.get("_currency", {}).get("symbol", ""))

    def currency(self, amount_base: float, locale: Optional[str] = None) -> Dict[str, Any]:
        """Doc section 17.2. `amount_base` is in the catalogue's base
        currency (INR). Returns the shape doc section 4.3's `total` field
        uses: `{"amount": <float>, "text": "<symbol><formatted>"}`.
        """
        loc = locale or self.default
        table = self._tables.get(loc) or self._tables.get(self.default) or {}
        cur = table.get("_currency", {"symbol": "", "rate": 1.0, "decimals": 2})
        rate = float(cur.get("rate", 1.0))
        decimals = int(cur.get("decimals", 2))
        symbol = cur.get("symbol", "")
        amount = round(amount_base * rate, decimals)
        return {"amount": amount, "text": f"{symbol}{amount:.{decimals}f}"}
