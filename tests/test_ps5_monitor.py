import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

import ps5_monitor


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, json_error=None):
        self.status_code = status_code
        self.json_data = json_data
        self.json_error = json_error

    def json(self):
        if self.json_error is not None:
            raise self.json_error
        return self.json_data


class FakeHTTPClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def raw_listing(
    listing_id=101,
    title="PS5 Slim Standard com leitor",
    description="PlayStation 5 Slim com leitor de disco, caixa e comando.",
    price=250,
    currency="EUR",
    status="active",
    latitude=ps5_monitor.PENAFIEL_LATITUDE,
    longitude=ps5_monitor.PENAFIEL_LONGITUDE,
    locality="Penafiel",
):
    return {
        "id": listing_id,
        "title": title,
        "description": description,
        "url": f"https://www.olx.pt/d/anuncio/{listing_id}.html",
        "created_time": "2026-08-30T12:00:00+01:00",
        "status": status,
        "location": {"city": {"name": locality}, "region": {"name": "Porto"}},
        "map": {
            "lat": latitude,
            "lon": longitude,
            "radius": 2,
            "show_detailed": False,
        },
        "params": [
            {
                "key": "price",
                "value": {
                    "__typename": "PriceParam",
                    "value": price,
                    "currency": currency,
                    "negotiable": False,
                    "arranged": False,
                },
            },
            {
                "key": "type",
                "value": {
                    "__typename": "GenericParam",
                    "key": "playstation5",
                    "label": "PlayStation 5",
                },
            },
        ],
    }


def success_body(items):
    return {
        "data": {
            "clientCompatibleListings": {
                "__typename": "ListingSuccess",
                "data": items,
            }
        }
    }


def config(state_path=Path("unused.sqlite3"), **overrides):
    values = {
        "ntfy_topic": "eduardo_notifications",
        "min_price_eur": 200.0,
        "max_price_eur": 300.0,
        "max_distance_km": 80.0,
        "state_db_path": state_path,
    }
    values.update(overrides)
    return ps5_monitor.Config(**values)


def listing(**overrides):
    values = {
        "listing_id": "101",
        "title": "PS5 Slim Standard com leitor",
        "description": "PlayStation 5 Slim com leitor de disco, caixa e comando.",
        "url": "https://www.olx.pt/d/anuncio/101.html",
        "created_time": "2026-08-30T12:00:00+01:00",
        "status": "active",
        "locality": "Penafiel",
        "latitude": ps5_monitor.PENAFIEL_LATITUDE,
        "longitude": ps5_monitor.PENAFIEL_LONGITUDE,
        "price_eur": 250.0,
        "currency": "EUR",
    }
    values.update(overrides)
    return ps5_monitor.Listing(**values)


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        parsed = ps5_monitor.Config.from_env({})

        self.assertEqual(parsed.ntfy_topic, "eduardo_notifications")
        self.assertEqual(parsed.min_price_eur, 200.0)
        self.assertEqual(parsed.max_price_eur, 300.0)
        self.assertEqual(parsed.max_distance_km, 80.0)
        self.assertEqual(parsed.state_db_path, Path("ps5_monitor.sqlite3"))
        self.assertEqual(parsed.olx_max_results, 200)

    def test_overrides(self):
        parsed = ps5_monitor.Config.from_env(
            {
                "NTFY_TOPIC": "my_topic",
                "MIN_PRICE_EUR": "210.5",
                "MAX_PRICE_EUR": "299",
                "MAX_DISTANCE_KM": "42",
                "STATE_DB_PATH": "/tmp/monitor.sqlite3",
                "OLX_MAX_RESULTS": "250",
            }
        )

        self.assertEqual(parsed.ntfy_topic, "my_topic")
        self.assertEqual(parsed.min_price_eur, 210.5)
        self.assertEqual(parsed.max_price_eur, 299.0)
        self.assertEqual(parsed.max_distance_km, 42.0)
        self.assertEqual(parsed.state_db_path, Path("/tmp/monitor.sqlite3"))
        self.assertEqual(parsed.olx_max_results, 250)

    def test_invalid_values(self):
        invalid_environments = [
            {"NTFY_TOPIC": ""},
            {"STATE_DB_PATH": ""},
            {"MIN_PRICE_EUR": "nope"},
            {"MIN_PRICE_EUR": "nan"},
            {"MAX_PRICE_EUR": "-1"},
            {"MAX_DISTANCE_KM": "inf"},
            {"OLX_MAX_RESULTS": "not-an-integer"},
            {"OLX_MAX_RESULTS": "0"},
            {"OLX_MAX_RESULTS": "301"},
            {"MIN_PRICE_EUR": "300", "MAX_PRICE_EUR": "300"},
        ]
        for environment in invalid_environments:
            with (
                self.subTest(environment=environment),
                self.assertRaises(ps5_monitor.ConfigurationError),
            ):
                ps5_monitor.Config.from_env(environment)


class NotificationStoreTests(unittest.TestCase):
    def test_schema_and_ids_persist_across_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            store = ps5_monitor.NotificationStore(path)
            store.initialize()
            store.mark_notified(["10", "20", "10"])

            reopened = ps5_monitor.NotificationStore(path)
            reopened.initialize()
            self.assertEqual(reopened.notified_ids(), {"10", "20"})

    def test_corrupt_database_fails_without_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            original = b"not a sqlite database"
            path.write_bytes(original)

            with self.assertRaises(ps5_monitor.StateError):
                ps5_monitor.NotificationStore(path).initialize()
            self.assertEqual(path.read_bytes(), original)


class ClassificationTests(unittest.TestCase):
    def test_normalization_accepts_portuguese_wording(self):
        candidate = listing(
            title="PlayStation 5 SLIM edição Standard",
            description="Consola com lêitor de dísco.",
        )

        deal = ps5_monitor.classify_listing(candidate, config())

        self.assertIsNotNone(deal)
        self.assertAlmostEqual(deal.distance_km, 0.0)

    def test_rejects_digital_accessory_wanted_repair_and_parts_listings(self):
        rejected = [
            listing(description="PS5 Slim Digital Edition sem leitor"),
            listing(title="Jogo para PS5 Slim Disc", description="Jogo novo em caixa"),
            listing(
                title="Comando para PS5 Slim Disc", description="Acessório compatível"
            ),
            listing(title="Leitor para PS5 Slim Disc", description="Drive amovível"),
            listing(title="Procuro PS5 Slim com leitor de disco"),
            listing(title="PS5 Slim Disc avariada para reparar"),
            listing(title="PS5 Slim Disc para peças"),
            listing(description="PlayStation 5 Slim com disco, mas não funciona"),
        ]

        for candidate in rejected:
            with self.subTest(title=candidate.title):
                self.assertIsNone(ps5_monitor.classify_listing(candidate, config()))

    def test_rejects_missing_required_identity_terms(self):
        rejected = [
            listing(title="Consola Slim com leitor", description="Como nova"),
            listing(title="PS5 Standard", description="Com leitor de disco"),
            listing(title="PS5 Slim", description="Consola como nova"),
        ]
        for candidate in rejected:
            with self.subTest(title=candidate.title):
                self.assertIsNone(ps5_monitor.classify_listing(candidate, config()))

    def test_price_boundaries_are_inclusive_then_exclusive(self):
        self.assertIsNotNone(
            ps5_monitor.classify_listing(listing(price_eur=200.0), config())
        )
        self.assertIsNone(
            ps5_monitor.classify_listing(listing(price_eur=300.0), config())
        )

    def test_status_currency_and_distance_filters(self):
        self.assertIsNone(
            ps5_monitor.classify_listing(listing(status="inactive"), config())
        )
        self.assertIsNone(
            ps5_monitor.classify_listing(listing(currency="USD"), config())
        )
        self.assertIsNotNone(
            ps5_monitor.classify_listing(listing(), config(max_distance_km=0.0))
        )
        self.assertIsNone(
            ps5_monitor.classify_listing(
                listing(latitude=38.7223, longitude=-9.1393), config()
            )
        )

    def test_notified_filter_and_deterministic_ranking(self):
        candidates = [
            listing(listing_id="30", price_eur=250),
            listing(listing_id="20", price_eur=220),
            listing(listing_id="10", price_eur=220),
            listing(listing_id="05", price_eur=210),
        ]

        deals = ps5_monitor.select_deals(candidates, config(), {"05"})

        self.assertEqual(
            [deal.listing.listing_id for deal in deals], ["10", "20", "30"]
        )


class OlxClientTests(unittest.TestCase):
    def test_success_response_is_parsed_with_minimal_anonymous_request(self):
        client = FakeHTTPClient(FakeResponse(json_data=success_body([raw_listing()])))

        listings, warnings = ps5_monitor.fetch_olx_listings(client)

        self.assertEqual(warnings, [])
        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0].listing_id, "101")
        self.assertEqual(listings[0].price_eur, 250.0)
        self.assertEqual(listings[0].locality, "Penafiel")
        url, kwargs = client.calls[0]
        self.assertEqual(url, ps5_monitor.OLX_GRAPHQL_URL)
        self.assertEqual(kwargs["timeout"], ps5_monitor.HTTP_TIMEOUT)
        lowered_headers = {key.lower() for key in kwargs["headers"]}
        self.assertNotIn("authorization", lowered_headers)
        self.assertNotIn("cookie", lowered_headers)
        query = kwargs["json"]["query"]
        self.assertNotIn("contact", query)
        self.assertNotIn("user {", query)
        self.assertNotIn("phone", query)
        parameters = {
            item["key"]: item["value"]
            for item in kwargs["json"]["variables"]["searchParameters"]
        }
        self.assertEqual(parameters["limit"], "40")
        self.assertEqual(parameters["filter_float_price:from"], "200")
        self.assertEqual(parameters["filter_float_price:to"], "300")

    def test_malformed_item_is_skipped_but_valid_sibling_remains(self):
        client = FakeHTTPClient(
            FakeResponse(json_data=success_body([{"id": 1}, raw_listing(listing_id=2)]))
        )

        listings, warnings = ps5_monitor.fetch_olx_listings(client)

        self.assertEqual([item.listing_id for item in listings], ["2"])
        self.assertEqual(len(warnings), 1)
        self.assertIn("listing 1", warnings[0])

    def test_city_falls_back_to_region(self):
        item = raw_listing()
        item["location"] = {"city": None, "region": {"name": "Porto"}}
        client = FakeHTTPClient(FakeResponse(json_data=success_body([item])))

        listings, _ = ps5_monitor.fetch_olx_listings(client)

        self.assertEqual(listings[0].locality, "Porto")

    def test_response_is_capped_at_forty_listings(self):
        items = [raw_listing(listing_id=index) for index in range(45)]
        client = FakeHTTPClient(FakeResponse(json_data=success_body(items)))

        listings, warnings = ps5_monitor.fetch_olx_listings(client, max_results=40)

        self.assertEqual(warnings, [])
        self.assertEqual(len(listings), 40)
        self.assertEqual(listings[-1].listing_id, "39")

    def test_paginates_in_forty_item_requests_and_deduplicates(self):
        first_page = [raw_listing(listing_id=index) for index in range(45)]
        second_page = [raw_listing(listing_id=index) for index in range(40, 85)]
        client = FakeHTTPClient(
            FakeResponse(json_data=success_body(first_page)),
            FakeResponse(json_data=success_body(second_page)),
        )

        listings, warnings = ps5_monitor.fetch_olx_listings(
            client,
            max_results=80,
            min_price_eur=225,
            max_price_eur=425,
        )

        self.assertEqual(warnings, [])
        self.assertEqual(len(listings), 80)
        offsets = []
        for _, kwargs in client.calls:
            parameters = {
                item["key"]: item["value"]
                for item in kwargs["json"]["variables"]["searchParameters"]
            }
            offsets.append(parameters["offset"])
            self.assertEqual(parameters["limit"], "40")
            self.assertEqual(parameters["filter_float_price:from"], "225")
            self.assertEqual(parameters["filter_float_price:to"], "425")
        self.assertEqual(offsets, ["0", "40"])

    def test_transport_and_envelope_failures_fail_closed(self):
        cases = [
            FakeHTTPClient(requests.Timeout("slow")),
            FakeHTTPClient(FakeResponse(status_code=403, json_data={})),
            FakeHTTPClient(FakeResponse(json_error=ValueError("bad json"))),
            FakeHTTPClient(FakeResponse(json_data={"errors": [{"message": "bad"}]})),
            FakeHTTPClient(
                FakeResponse(
                    json_data={
                        "data": {
                            "clientCompatibleListings": {
                                "__typename": "ListingError",
                                "error": {"code": "bad"},
                            }
                        }
                    }
                )
            ),
            FakeHTTPClient(
                FakeResponse(
                    json_data={
                        "data": {
                            "clientCompatibleListings": {
                                "__typename": "ListingSuccess",
                                "data": {},
                            }
                        }
                    }
                )
            ),
        ]

        for client in cases:
            with (
                self.subTest(client=client),
                self.assertRaises(ps5_monitor.RetrievalError),
            ):
                ps5_monitor.fetch_olx_listings(client)


class NtfyTests(unittest.TestCase):
    def test_payload_is_readable_and_limited_to_three_deals(self):
        deals = [
            ps5_monitor.Deal(
                listing(listing_id=str(index), price_eur=200 + index), index
            )
            for index in range(1, 5)
        ]

        payload = ps5_monitor.build_ntfy_payload("eduardo_notifications", deals)

        self.assertEqual(payload["topic"], "eduardo_notifications")
        self.assertEqual(payload["priority"], 4)
        self.assertTrue(payload["markdown"])
        self.assertEqual(len(payload["actions"]), 3)
        self.assertIn("€201", payload["message"])
        self.assertNotIn("€204", payload["message"])

    def test_publish_success_and_failure(self):
        deal = ps5_monitor.Deal(listing(), 1.25)
        success_client = FakeHTTPClient(FakeResponse(status_code=200))
        ps5_monitor.publish_deals("topic", [deal], success_client)
        self.assertEqual(success_client.calls[0][0], ps5_monitor.NTFY_URL)

        failure_client = FakeHTTPClient(FakeResponse(status_code=500))
        with self.assertRaises(ps5_monitor.PublishError):
            ps5_monitor.publish_deals("topic", [deal], failure_client)


class OrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.path = Path(self.temp_directory.name) / "state.sqlite3"
        self.config = config(self.path)

    def test_no_match_is_silent_and_successful(self):
        client = FakeHTTPClient(
            FakeResponse(
                json_data=success_body(
                    [raw_listing(description="PS5 Slim Digital Edition sem leitor")]
                )
            )
        )
        stdout = io.StringIO()

        result = ps5_monitor.run(self.config, client, stdout=stdout)

        self.assertEqual(result, 0)
        self.assertEqual(len(client.calls), 1)
        self.assertIn("0 unseen matches", stdout.getvalue())

    def test_success_publishes_then_persists(self):
        client = FakeHTTPClient(
            FakeResponse(json_data=success_body([raw_listing()])),
            FakeResponse(status_code=200),
        )

        result = ps5_monitor.run(self.config, client)

        self.assertEqual(result, 0)
        self.assertEqual(len(client.calls), 2)
        store = ps5_monitor.NotificationStore(self.path)
        self.assertEqual(store.notified_ids(), {"101"})

    def test_already_notified_match_does_not_publish(self):
        store = ps5_monitor.NotificationStore(self.path)
        store.initialize()
        store.mark_notified(["101"])
        client = FakeHTTPClient(FakeResponse(json_data=success_body([raw_listing()])))

        result = ps5_monitor.run(self.config, client, store=store)

        self.assertEqual(result, 0)
        self.assertEqual(len(client.calls), 1)

    def test_only_three_of_four_matches_are_published_and_persisted(self):
        items = [
            raw_listing(listing_id=index, price=210 + index) for index in range(1, 5)
        ]
        client = FakeHTTPClient(
            FakeResponse(json_data=success_body(items)),
            FakeResponse(status_code=200),
        )

        result = ps5_monitor.run(self.config, client)

        self.assertEqual(result, 0)
        self.assertEqual(
            ps5_monitor.NotificationStore(self.path).notified_ids(), {"1", "2", "3"}
        )
        self.assertEqual(len(client.calls[1][1]["json"]["actions"]), 3)

    def test_ntfy_failure_does_not_persist(self):
        client = FakeHTTPClient(
            FakeResponse(json_data=success_body([raw_listing()])),
            FakeResponse(status_code=500),
        )
        stderr = io.StringIO()

        result = ps5_monitor.run(self.config, client, stderr=stderr)

        self.assertEqual(result, 1)
        self.assertEqual(ps5_monitor.NotificationStore(self.path).notified_ids(), set())
        self.assertIn("ntfy returned HTTP 500", stderr.getvalue())

    def test_retrieval_failure_exits_nonzero(self):
        client = FakeHTTPClient(FakeResponse(status_code=403, json_data={}))
        stderr = io.StringIO()

        result = ps5_monitor.run(self.config, client, stderr=stderr)

        self.assertEqual(result, 1)
        self.assertIn("OLX returned HTTP 403", stderr.getvalue())

    def test_state_failure_after_publish_exits_nonzero(self):
        class FailingStore:
            def initialize(self):
                pass

            def notified_ids(self):
                return set()

            def mark_notified(self, listing_ids):
                list(listing_ids)
                raise ps5_monitor.StateError("disk full")

        client = FakeHTTPClient(
            FakeResponse(json_data=success_body([raw_listing()])),
            FakeResponse(status_code=200),
        )
        stderr = io.StringIO()

        result = ps5_monitor.run(
            self.config, client, store=FailingStore(), stderr=stderr
        )

        self.assertEqual(result, 1)
        self.assertEqual(len(client.calls), 2)
        self.assertIn("disk full", stderr.getvalue())

    def test_invalid_environment_fails_before_http(self):
        client = FakeHTTPClient()
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, {"MIN_PRICE_EUR": "bad"}, clear=True):
            result = ps5_monitor.run(http_client=client, stderr=stderr)

        self.assertEqual(result, 1)
        self.assertEqual(client.calls, [])
        self.assertIn("MIN_PRICE_EUR must be a number", stderr.getvalue())

    def test_main_returns_run_exit_code(self):
        with (
            mock.patch("ps5_monitor.load_dotenv") as load_dotenv,
            mock.patch("ps5_monitor.run", return_value=7),
        ):
            self.assertEqual(ps5_monitor.main(), 7)
        load_dotenv.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
