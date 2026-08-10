"""Tests for core/pricing.py — M1 build item 2 (doc section 21).

Run from the repo root:

    python -m unittest discover -s python/tests -v

Includes one integration test against the real data/catalogue.json (doc
section 8.1, written by M1.1): the point of that file existing at all is
that Catalogue.load() can parse it, so a test that only ever builds its
own fixture would miss a drift between the two.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hotpot.core import binmap  # noqa: E402
from hotpot.core.cart import Cart  # noqa: E402
from hotpot.core.pricing import Catalogue, Item, bin_price, total  # noqa: E402

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CATALOGUE_PATH = os.path.join(REPO_ROOT, "data", "catalogue.json")


def make_catalogue():
    return Catalogue([
        Item(id="mushroom", price_per_100g=12.0,
             names={"en": "Shiitake mushroom", "zh": "香菇"},
             tags=["vegetarian", "vegan"], class_name="mushroom"),
        Item(id="dried_prawns", price_per_100g=55.0,
             names={"en": "Dried Prawns", "zh": "虾干"},
             tags=["seafood"], class_name="dried_prawns"),
    ])


class TestBinPrice(unittest.TestCase):

    def test_matches_doc_9_2_exactly(self):
        self.assertEqual(bin_price(200.0, 12.0), 24.0)

    def test_zero_removed_is_zero_price(self):
        self.assertEqual(bin_price(0.0, 55.0), 0.0)


class TestCatalogue(unittest.TestCase):

    def setUp(self):
        self.cat = make_catalogue()

    def test_item_lookup(self):
        it = self.cat.item("mushroom")
        self.assertEqual(it.price_per_100g, 12.0)
        self.assertEqual(it.names["zh"], "香菇")

    def test_unknown_id_returns_none(self):
        self.assertIsNone(self.cat.item("does_not_exist"))

    def test_none_id_returns_none(self):
        """An unresolved bin's item_id is None — callers should not have
        to special-case that before asking the catalogue.
        """
        self.assertIsNone(self.cat.item(None))

    def test_len(self):
        self.assertEqual(len(self.cat), 2)

    def test_ids_preserve_load_order(self):
        """core/main.py's mock bin seed (M1 build item 3) pairs bins with
        catalogue items by this order — it has to match the file, not an
        arbitrary dict iteration.
        """
        self.assertEqual(self.cat.ids(), ["mushroom", "dried_prawns"])


class TestCatalogueLoad(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "catalogue.json")

    def write(self, text):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_loads_a_well_formed_file(self):
        self.write('{"schema":3,"base_currency":"INR","items":['
                   '{"id":"tofu","pricePer100g":18.0,'
                   '"names":{"en":"Tofu","zh":"豆腐"},'
                   '"tags":["vegetarian"],"class_name":"tofu"}]}')
        cat = Catalogue.load(self.path)
        self.assertEqual(len(cat), 1)
        self.assertEqual(cat.item("tofu").price_per_100g, 18.0)

    def test_wrong_schema_raises(self):
        self.write('{"schema":2,"items":[]}')
        with self.assertRaises(ValueError):
            Catalogue.load(self.path)

    def test_real_catalogue_file_loads_and_has_eight_items(self):
        """Integration check against doc section 8.1's committed file."""
        cat = Catalogue.load(CATALOGUE_PATH)
        self.assertEqual(len(cat), 8)
        it = cat.item("mushroom")
        self.assertIsNotNone(it)
        self.assertIn("en", it.names)
        self.assertIn("zh", it.names)
        self.assertIsInstance(it.price_per_100g, float)

    def test_real_catalogue_has_exactly_eight_ids_for_the_mock_bin_seed(self):
        """core/main.py's mock bin seed needs one id per bin — see
        test_core_main.py's TestStateBroadcast for the pairing itself.
        """
        cat = Catalogue.load(CATALOGUE_PATH)
        self.assertEqual(len(cat.ids()), 8)
        self.assertEqual(len(set(cat.ids())), 8)   # no duplicate ids


class TestTotal(unittest.TestCase):
    """Doc section 9.2 line 3 and section 9.3's unresolved-bins rule."""

    def setUp(self):
        self.cart = Cart()
        self.binmap = binmap.BinMap()
        self.cat = make_catalogue()

    def test_sums_only_resolved_bins(self):
        self.binmap.set_bin(0, item_id="mushroom", conf=0.9, source="mock")
        self.cart.start_g[0] = 500.0
        self.cart.set_live_grams(0, 300.0)      # 200g removed @ 12/100g = 24.0

        self.binmap.set_bin(1, item_id="dried_prawns", conf=0.9, source="mock")
        self.cart.start_g[1] = 100.0
        self.cart.set_live_grams(1, 0.0)        # 100g removed @ 55/100g = 55.0

        self.assertEqual(total(self.cart, self.binmap, self.cat), 79.0)

    def test_unresolved_bin_bills_nothing_no_matter_how_much_mass_left(self):
        """Doc section 9.3, the exact scenario it calls out."""
        # bin 2 has no item_id at all, but real mass has left it.
        self.cart.start_g[2] = 300.0
        self.cart.set_live_grams(2, 0.0)
        self.assertEqual(total(self.cart, self.binmap, self.cat), 0.0)

    def test_low_confidence_bin_bills_nothing(self):
        self.binmap.set_bin(3, item_id="mushroom", conf=0.10, source="mock")
        self.cart.start_g[3] = 300.0
        self.cart.set_live_grams(3, 0.0)
        self.assertEqual(total(self.cart, self.binmap, self.cat, conf_floor=0.65), 0.0)

    def test_a_resolved_item_id_missing_from_the_catalogue_bills_nothing(self):
        """Belt and braces: a stale item_id (catalogue edited, bin_map.json
        not yet refreshed) must not crash pricing or bill a phantom price.
        """
        self.binmap.set_bin(4, item_id="no_longer_exists", conf=0.99, source="mock")
        self.cart.start_g[4] = 300.0
        self.cart.set_live_grams(4, 0.0)
        self.assertEqual(total(self.cart, self.binmap, self.cat), 0.0)

    def test_empty_binmap_totals_zero(self):
        self.assertEqual(total(self.cart, self.binmap, self.cat), 0.0)


if __name__ == "__main__":
    unittest.main()
