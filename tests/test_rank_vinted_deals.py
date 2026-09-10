import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import requests

from rank_vinted_deals import (
    ListingStore,
    ai_is_eligible,
    apply_purchase_rules,
    build_ntfy_payload,
    classify_disc_console,
    deal_for,
    estimate_run_max_cost,
    extract_seller_profile,
    llm_hard_rejects,
    notify_new_deals,
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

    def test_western_digital_brand_does_not_mean_digital_console(self) -> None:
        item = {
            "title": "PS5 édition standard (avec lecteur)",
            "description": "Console avec lecteur et SSD Western Digital",
        }

        self.assertEqual(classify_disc_console(item), "fat_disc")

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


class NtfyTests(unittest.TestCase):
    @staticmethod
    def deal(listing_id: int) -> dict:
        return {
            "id": listing_id,
            "title": f"PS5 Slim Disc {listing_id}",
            "price_eur": 350,
            "url": f"https://www.vinted.pt/items/{listing_id}",
            "console_type": "slim_disc",
            "deal": {"label": "excellent"},
            "seller": {"rating": 4.5, "reviews": 2},
        }

    def test_payload_contains_at_most_three_deals(self) -> None:
        payload = build_ntfy_payload(
            "eduardo_notifications", [self.deal(index) for index in range(1, 5)]
        )

        self.assertEqual(payload["topic"], "eduardo_notifications")
        self.assertEqual(len(payload["actions"]), 3)
        self.assertNotIn("PS5 Slim Disc 4", payload["message"])

    def test_success_is_persisted_and_not_sent_twice(self) -> None:
        with TemporaryDirectory() as directory:
            store = ListingStore(Path(directory) / "test.sqlite3")
            response = Mock()
            client = Mock()
            client.post.return_value = response

            self.assertEqual(
                notify_new_deals("topic", [self.deal(1)], store, client), 1
            )
            self.assertEqual(
                notify_new_deals("topic", [self.deal(1)], store, client), 0
            )
            self.assertEqual(store.notified_ids(), {"1"})
            client.post.assert_called_once()

    def test_failure_is_not_persisted(self) -> None:
        with TemporaryDirectory() as directory:
            store = ListingStore(Path(directory) / "test.sqlite3")
            response = Mock()
            response.raise_for_status.side_effect = requests.HTTPError("500")
            client = Mock()
            client.post.return_value = response

            with self.assertRaisesRegex(RuntimeError, "ntfy notification failed"):
                notify_new_deals("topic", [self.deal(1)], store, client)

            self.assertEqual(store.notified_ids(), set())


class LlmDecisionTests(unittest.TestCase):
    def test_confidence_threshold_is_inclusive_at_point_75(self) -> None:
        decision = {
            "status": "accepted",
            "is_desired_ps5": True,
            "scam_risk": "low",
            "confidence": 0.75,
        }

        self.assertTrue(ai_is_eligible(decision))
        self.assertFalse(ai_is_eligible({**decision, "confidence": 0.749}))

    def test_console_bundle_reason_is_not_mistaken_for_accessory_only(self) -> None:
        decision = {
            "status": "accepted",
            "is_desired_ps5": True,
            "scam_risk": "low",
            "reasons": ["Genuine PS5 Disc console with controller and games included"],
        }

        self.assertFalse(llm_hard_rejects({}, decision))

    def test_controller_only_reason_is_rejected(self) -> None:
        decision = {
            "status": "accepted",
            "is_desired_ps5": True,
            "scam_risk": "low",
            "reasons": ["This is a controller-only listing with no console"],
        }

        self.assertTrue(llm_hard_rejects({}, decision))


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
