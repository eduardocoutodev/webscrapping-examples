import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from rank_vinted_deals import (
    ListingStore,
    apply_purchase_rules,
    classify_disc_console,
    deal_for,
    estimate_run_max_cost,
    extract_seller_profile,
    rank_candidates,
    seller_is_eligible,
)


class DiscConsoleTests(unittest.TestCase):
    def test_classifies_slim_disc(self) -> None:
        item = {"title": "PS5 Slim Standard Edition", "description": "com leitor"}

        self.assertEqual(classify_disc_console(item), "slim_disc")

    def test_classifies_original_disc(self) -> None:
        item = {"title": "PlayStation 5", "description": "Consola com leitor de discos"}

        self.assertEqual(classify_disc_console(item), "fat_disc")

    def test_rejects_digital_even_if_disc_is_mentioned(self) -> None:
        item = {"title": "PS5 Slim Digital", "description": "sem leitor de disco"}

        self.assertIsNone(classify_disc_console(item))
        self.assertIsNone(classify_disc_console({"title": "PS5 sem disco"}))

    def test_recognizes_cfi_model_codes(self) -> None:
        self.assertEqual(classify_disc_console({"title": "PS5 CFI-2016A"}), "slim_disc")
        self.assertEqual(classify_disc_console({"title": "PS5 CFI-1216A"}), "fat_disc")
        self.assertIsNone(classify_disc_console({"title": "PS5 CFI-2016B"}))


class PurchaseRuleTests(unittest.TestCase):
    def test_slim_price_boundaries(self) -> None:
        self.assertEqual(deal_for("slim_disc", 350)["tier"], "excellent")
        self.assertEqual(deal_for("slim_disc", 375)["tier"], "good")
        self.assertEqual(deal_for("slim_disc", 400)["tier"], "conditional")
        self.assertIsNone(deal_for("slim_disc", 400.01))

    def test_fat_price_boundaries(self) -> None:
        self.assertEqual(deal_for("fat_disc", 300)["tier"], "excellent")
        self.assertEqual(deal_for("fat_disc", 325)["tier"], "good")
        self.assertEqual(deal_for("fat_disc", 350)["tier"], "conditional")
        self.assertIsNone(deal_for("fat_disc", 350.01))

    def test_seller_thresholds_are_inclusive(self) -> None:
        self.assertTrue(seller_is_eligible({"reviews": 1, "rating": 3.0}))
        self.assertFalse(seller_is_eligible({"reviews": 0, "rating": 5.0}))
        self.assertFalse(seller_is_eligible({"reviews": 10, "rating": 2.99}))

    def test_only_qualified_listing_survives(self) -> None:
        base = {
            "title": "PS5 Slim com leitor",
            "description": "Excelente estado",
            "seller": {"reviews": 1, "rating": 3.0},
        }
        candidates = [
            {**base, "id": 1, "price_eur": 350},
            {**base, "id": 2, "price_eur": 279.99},
            {**base, "id": 3, "price_eur": 350, "seller": {"reviews": 0, "rating": 5}},
            {**base, "id": 4, "price_eur": 350, "title": "PS5 Slim Digital"},
        ]

        result = apply_purchase_rules(candidates)

        self.assertEqual([item["id"] for item in result], [1])
        self.assertEqual(result[0]["console_type"], "slim_disc")
        self.assertEqual(result[0]["deal"]["tier"], "excellent")


class SellerProfileTests(unittest.TestCase):
    def test_converts_vinted_reputation_to_five_stars(self) -> None:
        result = extract_seller_profile(
            {
                "user": {
                    "id": 42,
                    "login": "seller",
                    "profile_url": "https://example.test/member/42",
                    "feedback_count": 8,
                    "feedback_reputation": 0.8,
                }
            }
        )

        self.assertEqual(result["reviews"], 8)
        self.assertEqual(result["rating"], 4.0)


class ModelBudgetTests(unittest.TestCase):
    def test_more_batches_reserve_more_cost(self) -> None:
        pricing = {
            "prompt": 0.000001,
            "completion": 0.000002,
            "internal_reasoning": 0.0,
            "request": 0.0,
        }
        listing = {
            "id": 1,
            "title": "PS5 Slim Disc",
            "price_eur": 350,
            "description": "Excellent condition",
        }

        one_batch = estimate_run_max_cost([listing] * 5, 5, pricing)
        two_batches = estimate_run_max_cost([listing] * 6, 5, pricing)

        self.assertGreater(one_batch, 0)
        self.assertGreater(two_batches, one_batch)

    @patch("rank_vinted_deals.rank_batch")
    @patch("rank_vinted_deals.resolve_model")
    def test_refuses_run_before_model_call_when_budget_is_too_low(
        self, resolve_model_mock, rank_batch_mock
    ) -> None:
        pricing = {
            "prompt": 0.000001,
            "completion": 0.000002,
            "internal_reasoning": 0.0,
            "request": 0.0,
        }
        resolve_model_mock.return_value = ("test/model", pricing)
        listing = {
            "id": 1,
            "title": "PS5 Slim Disc",
            "price_eur": 350,
            "description": "Excellent condition",
        }

        with TemporaryDirectory() as directory:
            store = ListingStore(Path(directory) / "test.sqlite3")
            with self.assertRaisesRegex(RuntimeError, "exceeds"):
                rank_candidates(
                    [listing],
                    "test-key",
                    5,
                    store,
                    max_cost_usd=0.000001,
                )

        rank_batch_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
