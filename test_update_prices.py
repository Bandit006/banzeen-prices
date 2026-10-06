"""
Tests for update_prices.py. Run with:  python3 -m unittest test_update_prices

The sample sentences are copied from real 2026 announcements, because each
news site words the prices differently.
"""

import os
import unittest

import update_prices as up

SEPTEMBER = {"jayyid91": 0.225, "mumtaz95": 0.247, "super98": 0.380, "diesel": 0.229}
JULY = {"jayyid91": 0.222, "mumtaz95": 0.247, "super98": 0.362, "diesel": 0.229}
MAY = {"jayyid91": 0.233, "mumtaz95": 0.269, "super98": 0.362, "diesel": 0.229}
APRIL = {"jayyid91": 0.223, "mumtaz95": 0.253, "super98": 0.314, "diesel": 0.220}


def read(html_or_text):
    return up.extract_prices(up.page_text(html_or_text))


class ExtractPrices(unittest.TestCase):

    def test_price_after_name_with_colon(self):
        # GDN Online, September 2026
        text = ("<p>The Fuel Price Committee has approved fuel prices for September.</p>"
                "<ul><li>Jayyid (91): BD0.225 per litre</li>"
                "<li>Mumtaz (95): BD0.247 per litre</li>"
                "<li>Super (98): BD0.380 per litre</li>"
                "<li>Diesel: BD0.229 per litre</li></ul>")
        self.assertEqual(read(text), SEPTEMBER)

    def test_price_before_name(self):
        # Zawya, September 2026
        text = ("Prices are set at 0.225 ($0.60) Bahraini dinar per litre for Jayyid (91), "
                "0.247 dinar for Mumtaz (95), 0.380 dinar for Super (98), and 0.229 dinar "
                "for Diesel.")
        self.assertEqual(read(text), SEPTEMBER)

    def test_dollar_amount_is_ignored(self):
        # TradeArabia, July 2026
        text = ("Jayyid (91) petrol will cost BD0.222 ($0.59) per litre, Mumtaz (95) "
                "BD0.247, Super (98) BD0.362, and diesel BD0.229 per litre.")
        self.assertEqual(read(text), JULY)

    def test_sentence_with_words_between(self):
        # Oil and Gas News, August 2026
        text = ("The committee set the price of Jayyid (91) at 0.222 Bahraini dinar ($0.59) "
                "per litre, Mumtaz (95) at 0.247 dinar per litre, Super (98) at 0.362 dinar "
                "per litre, and Diesel at 0.229 dinar per litre.")
        self.assertEqual(read(text), JULY)

    def test_fils_first_then_dinar(self):
        # Lovin Bahrain, May 2026
        text = ("Diesel is now 229 fils (0.229 BHD/Litre), Jayyid (91) comes in at 233 fils "
                "(0.233 BHD/Litre), Mumtaz (95) is priced at 269 fils (0.269 BHD/Litre), "
                "and Super (98) tops the list at 362 fils (0.362 BHD/Litre).")
        self.assertEqual(read(text), MAY)

    def test_octane_number_first_and_fils(self):
        # Zawya, April 2026
        text = ("* Diesel - 220 fils per litre * 98 Super - 314 fils per litre "
                "* 95 Mumtaz - 253 fils per litre * 91 Jayyid - 223 fils per litre")
        self.assertEqual(read(text), APRIL)

    def test_short_form(self):
        # Gulf Insider, June 2026
        text = ("Jayyid 91 at BD 0.233/L, Mumtaz 95 at BD 0.269/L, "
                "Super 98 at BD 0.362/L, diesel at BD 0.229/L.")
        self.assertEqual(read(text), MAY)

    def test_missing_fuel_returns_none(self):
        text = "Jayyid (91): BD0.225 per litre. Mumtaz (95): BD0.247 per litre."
        self.assertIsNone(read(text))

    def test_homepage_without_prices_returns_none(self):
        text = "<h1>Bahrain Confidential</h1><p>Best brunches in Bahrain this September 2026</p>"
        self.assertIsNone(read(text))

    def test_script_and_style_are_ignored(self):
        text = ("<script>var p = 'Jayyid (91): BD0.999';</script>"
                "<style>.diesel{}</style>"
                "Jayyid (91): BD0.225, Mumtaz (95): BD0.247, Super (98): BD0.380, "
                "Diesel: BD0.229")
        self.assertEqual(read(text), SEPTEMBER)


class Checks(unittest.TestCase):

    def test_month_must_match(self):
        text = up.page_text("Bahrain fuel prices for September 2026")
        self.assertTrue(up.mentions_month(text, 9, 2026))
        self.assertFalse(up.mentions_month(text, 10, 2026))

    def test_large_jump_is_rejected(self):
        self.assertTrue(up.plausible_change(JULY, SEPTEMBER))
        doubled = {k: round(v * 2, 3) for k, v in SEPTEMBER.items()}
        self.assertFalse(up.plausible_change(SEPTEMBER, doubled))


class ManualForm(unittest.TestCase):

    def tearDown(self):
        for key in up.FUEL_KEYS + ["effective_date"]:
            os.environ.pop("IN_" + key.upper(), None)

    def fill(self, values, effective=""):
        for key, value in values.items():
            os.environ["IN_" + key.upper()] = value
        os.environ["IN_EFFECTIVE_DATE"] = effective

    def test_empty_form_means_automatic(self):
        self.assertIsNone(up.manual_input(up.date(2026, 10, 3)))

    def test_form_prices_and_default_date(self):
        self.fill({"jayyid91": "0.225", "mumtaz95": "0,247", "super98": "380", "diesel": "0.229"})
        prices, effective = up.manual_input(up.date(2026, 10, 3))
        self.assertEqual(prices, SEPTEMBER)
        self.assertEqual(effective, "2026-10-02")

    def test_form_rejects_text(self):
        self.fill({"jayyid91": "abc", "mumtaz95": "0.247", "super98": "0.380", "diesel": "0.229"})
        with self.assertRaises(SystemExit):
            up.manual_input(up.date(2026, 10, 3))


if __name__ == "__main__":
    unittest.main()
