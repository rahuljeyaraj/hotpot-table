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
from hotpot.core import pricing  # noqa: E402
from hotpot.core.cart import Cart  # noqa: E402
from hotpot.core.pricing import (  # noqa: E402
    Catalogue, Item, bin_price, display_grams, shown_total, total)

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
        self.write('{"schema":7,"base_currency":"INR","items":['
                   '{"id":"tofu","pricePer100g":18.0,'
                   '"names":{"en":"Tofu","zh":"豆腐"},'
                   '"tags":["vegetarian"],"class_name":"tofu",'
                   '"diet":"veg","kcalPer100g":76,'
                   '"description":"A test item."}]}')
        cat = Catalogue.load(self.path)
        self.assertEqual(len(cat), 1)
        self.assertEqual(cat.item("tofu").price_per_100g, 18.0)

    def test_wrong_schema_raises(self):
        self.write('{"schema":2,"items":[]}')
        with self.assertRaises(ValueError):
            Catalogue.load(self.path)

    def test_real_catalogue_file_loads(self):
        """Integration check against doc section 8.1's committed file."""
        cat = Catalogue.load(CATALOGUE_PATH)
        self.assertGreaterEqual(len(cat), binmap.NUM_BINS)
        it = cat.item("chicken_eggs")
        self.assertIsNotNone(it)
        self.assertIn("en", it.names)
        self.assertIn("zh", it.names)
        self.assertIsInstance(it.price_per_100g, float)

    def test_the_info_box_fields_are_required(self):
        """VISUAL_LAYER.md section 8's info box shows all three, and a
        blank one reads as a broken table rather than as a missing field —
        so a missing one stops core on the bench, exactly like a missing
        `en` name does.
        """
        for missing in ('"diet":"veg",', '"kcalPer100g":76,',
                        '"description":"A test item."'):
            full = ('{"schema":7,"base_currency":"INR","items":['
                    '{"id":"tofu","pricePer100g":18.0,'
                    '"names":{"en":"Tofu"},"tags":[],"class_name":"tofu",'
                    '"diet":"veg","kcalPer100g":76,'
                    '"description":"A test item."}]}')
            with self.subTest(missing=missing):
                self.write(full.replace(missing, "").replace(",}", "}"))
                with self.assertRaises(ValueError):
                    Catalogue.load(self.path)

    def test_a_diet_outside_the_three_valid_values_is_refused(self):
        # `diet` is the one field a diner may act on. A typo'd
        # "vegetarian" would otherwise draw neither veg nor non-veg — a
        # silently missing answer where somebody is looking for one.
        self.write('{"schema":7,"base_currency":"INR","items":['
                   '{"id":"tofu","pricePer100g":18.0,'
                   '"names":{"en":"Tofu"},"tags":[],"class_name":"tofu",'
                   '"diet":"vegetarian","kcalPer100g":76,'
                   '"description":"A test item."}]}')
        with self.assertRaises(ValueError):
            Catalogue.load(self.path)

    def test_the_real_catalogue_has_info_for_every_item(self):
        # The committed file, not a fixture: every one of these reaches a
        # diner's eyes through the info box.
        cat = Catalogue.load(CATALOGUE_PATH)
        for item_id in cat.ids():
            it = cat.item(item_id)
            with self.subTest(item=item_id):
                self.assertIn(it.diet, pricing.VALID_DIETS)
                self.assertGreater(it.kcal_per_100g, 0.0)
                self.assertTrue(it.description.strip())
                # ASCII only: UiLayer loads Latin1Supplement +
                # CurrencySymbols, so an em-dash silently does not
                # render on the table.
                self.assertTrue(it.description.isascii())

    def test_diet_is_not_derived_from_tags(self):
        """The egg case, which is why `diet` is its own field.

        `chicken_eggs` carries the tag "vegetarian" (it always has), so
        any version that derived this from `tags` would project "VEG"
        onto an egg. This test is that derivation's tombstone.
        """
        it = Catalogue.load(CATALOGUE_PATH).item("chicken_eggs")
        self.assertIn("vegetarian", it.tags)
        self.assertEqual(it.diet, "egg")

    def test_real_catalogue_has_at_least_one_id_per_bin_and_no_duplicates(self):
        """core/main.py's mock bin seed (`_seed_binmap`) needs one id per
        bin, taken off the front of `cat.ids()` — see
        test_core_main.py's TestStateBroadcast for the pairing itself.

        The catalogue is doc section 8.1's "every item that could ever be
        in a bin", not "one entry per physical bin" — pricing.Catalogue's
        own docstring says the two are deliberately different questions,
        BinMap's job is which bin an item is actually in. So this only
        checks there are enough ids to seed every bin and none repeat; it
        does NOT check the count is exactly `binmap.NUM_BINS` any more —
        the real file may legitimately hold more classes than the table
        has physical bins (2026-08-13: 12 catalogue items, 8 bins).
        """
        cat = Catalogue.load(CATALOGUE_PATH)
        ids = cat.ids()
        self.assertGreaterEqual(len(ids), binmap.NUM_BINS)
        self.assertEqual(len(set(ids)), len(ids))   # no duplicate ids


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


class TestShownTotal(unittest.TestCase):
    """The displayed total (I5's deadband) against the billed one (I4).

    These are the checks that fail if shown_total() is ever collapsed back
    into total(): every one of them puts the two deliberately out of step
    and asserts on the gap, so a test that passed by both functions being
    the same function would have to assert the gap is zero.
    """

    def setUp(self):
        # **Deadband pinned at 10 g, not left on the default**, the same
        # way test_cart.py's own deadband class pins it: these cases are
        # about the MECHANISM (shown and billed diverge under the
        # deadband, and converge at finalize) and are written around doc
        # section 21's 45g-then-6g M1 example with its own currency
        # figures. The rig's chosen value moved 10 -> 5 on 2026-08-25 and
        # would have turned "6 g is under it" into "6 g crosses it",
        # quietly making three of these assert nothing.
        self.cart = Cart(deadband_g=10.0)
        self.binmap = binmap.BinMap()
        self.binmap.set_bin(0, item_id="mushroom", conf=0.9, source="mock")
        self.cart.start_g[0] = 500.0
        self.cat = make_catalogue()

    def test_deadband_holds_the_displayed_total_back(self):
        """A sub-deadband pick moves the billed total and not the shown one.

        45g then 6g, doc section 21's own M1 example: shown_g snapped to 45
        on the first pick and cannot move again until the gap reaches 10g,
        so the table still says 45g — and must still say the price OF 45g.
        """
        self.cart.set_live_grams(0, 455.0)       # 45g removed, snaps shown to 45
        self.cart.set_live_grams(0, 449.0)       # 51g removed, gap is 6g — no snap

        self.assertEqual(self.cart.shown_g[0], 45.0)
        self.assertEqual(shown_total(self.cart, self.binmap, self.cat), 5.40)
        self.assertEqual(total(self.cart, self.binmap, self.cat), 6.12)

    def test_they_agree_once_the_deadband_snaps(self):
        """The third pick of the acceptance test's cycle crosses 10g."""
        self.cart.set_live_grams(0, 455.0)
        self.cart.set_live_grams(0, 449.0)
        self.cart.set_live_grams(0, 329.0)       # 171g removed — snaps

        self.assertEqual(self.cart.shown_g[0], 171.0)
        self.assertEqual(shown_total(self.cart, self.binmap, self.cat),
                         total(self.cart, self.binmap, self.cat))

    def test_finalize_makes_the_two_converge(self):
        """I5's guarantee: the diner is never shown less than they are
        charged for, only shown it later. Cart.finalize() is what closes
        the gap, so this is the check that the promise in
        pricing.shown_total's docstring is actually kept.
        """
        self.cart.set_live_grams(0, 455.0)
        self.cart.set_live_grams(0, 449.0)
        self.assertNotEqual(shown_total(self.cart, self.binmap, self.cat),
                            total(self.cart, self.binmap, self.cat))

        self.cart.finalize()
        self.assertEqual(shown_total(self.cart, self.binmap, self.cat),
                         total(self.cart, self.binmap, self.cat))

    def test_shown_total_respects_the_unresolved_rule_too(self):
        """Doc section 9.3 governs what is displayed as well as what is
        billed — an unresolved bin renders empty and adds nothing.
        """
        self.binmap.set_bin(0, item_id="mushroom", conf=0.10, source="mock")
        self.cart.set_live_grams(0, 300.0)       # 200g gone, well past the deadband
        self.assertEqual(self.cart.shown_g[0], 200.0)
        self.assertEqual(shown_total(self.cart, self.binmap, self.cat,
                                     conf_floor=0.65), 0.0)


class TestDisplayGrams(unittest.TestCase):

    def test_rounds_to_a_whole_gram(self):
        self.assertEqual(display_grams(45.4), 45.0)
        self.assertEqual(display_grams(45.6), 46.0)

    def test_the_shown_total_is_priced_from_the_rounded_figure(self):
        """The grams the diner reads and the money beside them come from
        one number, so the line checks out by hand (doc section 21's "verify
        by arithmetic, not by watching"). Pricing 45.4g rather than the 45g
        on the plate would show 5.45 against a plate reading 45g.
        """
        cart = Cart()
        bm = binmap.BinMap()
        bm.set_bin(0, item_id="mushroom", conf=0.9, source="mock")
        cart.start_g[0] = 500.0
        cart.set_live_grams(0, 454.6)            # 45.4g removed, snaps shown
        self.assertAlmostEqual(cart.shown_g[0], 45.4, places=6)
        self.assertEqual(shown_total(cart, bm, make_catalogue()), 5.40)


class TestDisplayName(unittest.TestCase):
    """The hidden-label rule (pricing.Item's docstring, doc section 8.1).

    `id` and `class_name` are the training labels and are never shown;
    `names` are the hot pot ingredients they stand in for. The fixture
    below is the real shape of that: a bin trained on soya chunks that
    sells as a fish ball.
    """

    def setUp(self):
        self.stand_in = Item(
            id="soya_chunks", price_per_100g=10.0,
            names={"en": "Fish Ball", "zh": "鱼丸"},
            tags=["seafood"], class_name="soya_chunks")
        # The case the user called out: label and English display name
        # coincide, but zh is a translation of the display name.
        self.same_in_english = Item(
            id="egg", price_per_100g=11.0,
            names={"en": "Egg", "zh": "鸡蛋"},
            tags=["vegetarian"], class_name="egg")

    def test_shows_the_requested_locale(self):
        self.assertEqual(self.stand_in.display_name("en"), "Fish Ball")
        self.assertEqual(self.stand_in.display_name("zh"), "鱼丸")

    def test_display_name_is_unrelated_to_the_hidden_label(self):
        """A stand-in item shows the food it represents, in every locale
        — not a prettified version of what the model was trained on.
        """
        for loc in ("en", "zh"):
            shown = self.stand_in.display_name(loc)
            self.assertNotIn("soya", shown.lower())
            self.assertNotIn("chunk", shown.lower())

    def test_label_matching_english_still_translates(self):
        self.assertEqual(self.same_in_english.display_name("en"), "Egg")
        self.assertEqual(self.same_in_english.display_name("zh"), "鸡蛋")

    def test_missing_locale_falls_back_to_english_not_the_id(self):
        """**The leak this method exists to close.** core/main.py used to
        do `names.get(locale, item.id)`, which put the training label onto
        the projected surface for any item a locale had not translated.
        """
        no_zh = Item(id="soya_chunks", price_per_100g=10.0,
                     names={"en": "Fish Ball"},
                     tags=[], class_name="soya_chunks")
        got = no_zh.display_name("zh")
        self.assertEqual(got, "Fish Ball")
        self.assertNotEqual(got, no_zh.id)
        self.assertNotEqual(got, no_zh.class_name)

    def test_no_locale_at_all_falls_back_rather_than_leaking(self):
        no_zh = Item(id="soya_chunks", price_per_100g=10.0,
                     names={"en": "Fish Ball"},
                     tags=[], class_name="soya_chunks")
        for loc in ("zh", "ja", "", None):
            self.assertEqual(no_zh.display_name(loc), "Fish Ball")

    def test_an_item_with_no_english_name_raises_rather_than_leaking(self):
        """Hand-built Items bypass Catalogue.load()'s guard. Even then the
        failure is an exception, never the label.
        """
        broken = Item(id="soya_chunks", price_per_100g=10.0, names={},
                      tags=[], class_name="soya_chunks")
        with self.assertRaises(ValueError):
            broken.display_name("en")

    def test_every_real_catalogue_item_names_itself_in_both_locales(self):
        """Integration check against the committed file. Guards the
        promise Catalogue.load() makes to display_name(): en is always
        there, so the fallback chain is total.
        """
        cat = Catalogue.load(CATALOGUE_PATH)
        for item_id in cat.ids():
            it = cat.item(item_id)
            for loc in ("en", "zh"):
                shown = it.display_name(loc)
                self.assertTrue(shown, f"{item_id} has no {loc} name")
                self.assertNotEqual(
                    shown, it.id,
                    f"{item_id}'s {loc} name is its hidden id")
                self.assertNotEqual(
                    shown, it.class_name,
                    f"{item_id}'s {loc} name is its hidden class_name")


class TestCatalogueLoadRejectsUnnameableItems(unittest.TestCase):
    """Catalogue.load()'s guarantee: no item survives loading unless it
    can be named without reaching for the hidden label.
    """

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "catalogue.json")

    def write(self, text):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_item_with_no_english_name_is_refused_at_load(self):
        self.write('{"schema":7,"base_currency":"INR","items":['
                   '{"id":"soya_chunks","pricePer100g":10.0,'
                   '"names":{"zh":"鱼丸"},'
                   '"tags":[],"class_name":"soya_chunks",'
                   '"diet":"veg","kcalPer100g":76,'
                   '"description":"A test item."}]}')
        with self.assertRaises(ValueError) as ctx:
            Catalogue.load(self.path)
        self.assertIn("soya_chunks", str(ctx.exception))

    def test_item_with_empty_names_is_refused_at_load(self):
        self.write('{"schema":7,"base_currency":"INR","items":['
                   '{"id":"tofu","pricePer100g":18.0,"names":{},'
                   '"tags":[],"class_name":"tofu",'
                   '"diet":"veg","kcalPer100g":76,'
                   '"description":"A test item."}]}')
        with self.assertRaises(ValueError):
            Catalogue.load(self.path)

    def test_item_with_a_blank_english_name_is_refused_at_load(self):
        """An empty string is not a name. It would render a blank plate
        that still bills — worse than refusing to start.
        """
        self.write('{"schema":7,"base_currency":"INR","items":['
                   '{"id":"tofu","pricePer100g":18.0,'
                   '"names":{"en":"","zh":"豆腐"},'
                   '"tags":[],"class_name":"tofu",'
                   '"diet":"veg","kcalPer100g":76,'
                   '"description":"A test item."}]}')
        with self.assertRaises(ValueError):
            Catalogue.load(self.path)

    def test_a_zh_only_gap_is_allowed_because_it_degrades_safely(self):
        """Missing translations are tolerated — they fall back to English.
        Missing *English* is not, because nothing is below it.
        """
        self.write('{"schema":7,"base_currency":"INR","items":['
                   '{"id":"soya_chunks","pricePer100g":10.0,'
                   '"names":{"en":"Fish Ball"},'
                   '"tags":[],"class_name":"soya_chunks",'
                   '"diet":"veg","kcalPer100g":76,'
                   '"description":"A test item."}]}')
        cat = Catalogue.load(self.path)
        self.assertEqual(cat.item("soya_chunks").display_name("zh"),
                         "Fish Ball")


if __name__ == "__main__":
    unittest.main()
