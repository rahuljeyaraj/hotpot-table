"""Tests for core/i18n.py — M1 build item 2 (doc section 21).

Run from the repo root:

    python -m unittest discover -s python/tests -v

Includes one integration test against the real data/locales/en.json (doc
section 17.1, written by M1.1) — English only for M1 (build item 4), and
the point of this file is that a missing zh.json must degrade, not crash.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hotpot.core.i18n import Locales  # noqa: E402

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
LOCALES_DIR = os.path.join(REPO_ROOT, "data", "locales")


def write_locale(dir_, name, text):
    with open(os.path.join(dir_, f"{name}.json"), "w", encoding="utf-8") as f:
        f.write(text)


class LocaleDirCase(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)


class TestLoadWithMissingLocale(LocaleDirCase):
    """The exact situation M1 is in: en.json exists, zh.json does not."""

    def setUp(self):
        super().setUp()
        write_locale(self.dir.name, "en",
                     '{"_currency":{"symbol":"₹","rate":1.0,"decimals":2},'
                     '"total":"Total","done":"Done"}')
        self.locales = Locales.load(self.dir.name)

    def test_missing_locale_does_not_raise(self):
        self.assertTrue(self.locales.has("en"))
        self.assertFalse(self.locales.has("zh"))

    def test_translate_on_the_missing_locale_falls_back_to_default(self):
        self.assertEqual(self.locales.translate("total", "zh"), "Total")


class TestConstructorRequiresTheDefault(unittest.TestCase):

    def test_raises_if_default_locale_never_loaded(self):
        with self.assertRaises(ValueError):
            Locales({"zh": {"total": "总计"}}, default="en")


class TestTranslate(LocaleDirCase):

    def setUp(self):
        super().setUp()
        write_locale(self.dir.name, "en",
                     '{"_currency":{"symbol":"₹","rate":1.0,"decimals":2},'
                     '"total":"Total","done":"Done"}')
        write_locale(self.dir.name, "zh",
                     '{"_currency":{"symbol":"¥","rate":0.085,"decimals":2},'
                     '"total":"总计"}')
        self.locales = Locales.load(self.dir.name)

    def test_resolves_in_the_requested_locale(self):
        self.assertEqual(self.locales.translate("total", "zh"), "总计")

    def test_defaults_to_the_default_locale_when_none_given(self):
        self.assertEqual(self.locales.translate("total"), "Total")

    def test_falls_back_to_default_locale_for_a_key_missing_there(self):
        """zh.json has no 'done' key in this fixture (a real gap, not a
        missing file) — must fall back, not raise or return blank.
        """
        self.assertEqual(self.locales.translate("done", "zh"), "Done")

    def test_key_missing_everywhere_returns_the_key_itself(self):
        """Doc section 21 rationale: a missing string must be visibly
        wrong on the table, never a blank label nobody notices.
        """
        self.assertEqual(self.locales.translate("no_such_key", "en"), "no_such_key")


class TestCurrency(LocaleDirCase):
    """Doc section 17.2: currency is a property of the locale."""

    def setUp(self):
        super().setUp()
        write_locale(self.dir.name, "en",
                     '{"_currency":{"symbol":"₹","rate":1.0,"decimals":2}}')
        write_locale(self.dir.name, "zh",
                     '{"_currency":{"symbol":"¥","rate":0.085,"decimals":2}}')
        self.locales = Locales.load(self.dir.name)

    def test_base_currency_passes_through_at_rate_one(self):
        self.assertEqual(self.locales.currency(41.20, "en"),
                          {"amount": 41.20, "text": "₹41.20"})

    def test_converts_by_the_locale_rate(self):
        result = self.locales.currency(41.20, "zh")
        self.assertAlmostEqual(result["amount"], round(41.20 * 0.085, 2))
        self.assertTrue(result["text"].startswith("¥"))

    def test_a_locale_with_no_currency_block_falls_back_to_a_bare_number(self):
        write_locale(self.dir.name, "en", '{}')
        locales = Locales.load(self.dir.name)
        result = locales.currency(10.0, "en")
        self.assertEqual(result["text"], "10.00")


class TestRealLocaleFile(unittest.TestCase):
    """Integration check against doc section 17's committed data/locales/."""

    def test_en_json_loads_and_has_the_currency_block(self):
        locales = Locales.load(LOCALES_DIR)
        self.assertTrue(locales.has("en"))
        result = locales.currency(41.20, "en")
        self.assertIn("text", result)
        self.assertEqual(locales.translate("total", "en"), "Total")


if __name__ == "__main__":
    unittest.main()
