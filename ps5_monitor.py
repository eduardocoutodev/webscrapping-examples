"""One-shot OLX Portugal monitor for nearby PS5 Slim Disc deals."""

from __future__ import annotations

import math
import os
import re
import sqlite3
import sys
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

import requests
from dotenv import load_dotenv

OLX_GRAPHQL_URL = "https://www.olx.pt/apigateway/graphql"
NTFY_URL = "https://ntfy.sh/"
HTTP_TIMEOUT = (5, 20)
PENAFIEL_LATITUDE = 41.2083
PENAFIEL_LONGITUDE = -8.2829

OLX_QUERY = """
query ListingSearchQuery($searchParameters: [SearchParameter!] = []) {
  clientCompatibleListings(searchParameters: $searchParameters) {
    __typename
    ... on ListingSuccess {
      data {
        id
        title
        description
        url
        created_time
        status
        location {
          city { name }
          region { name }
        }
        map {
          lat
          lon
          radius
          show_detailed
        }
        params {
          key
          value {
            __typename
            ... on GenericParam { key label }
            ... on PriceParam { value currency negotiable arranged }
          }
        }
      }
    }
    ... on ListingError {
      error { code detail status title }
    }
  }
}
""".strip()

OLX_PAGE_SIZE = 40
MAX_OLX_RESULTS_LIMIT = 300

PS5_PATTERN = re.compile(r"\b(?:ps\s*5|play\s*station\s*5|playstation\s*5)\b")
SLIM_PATTERN = re.compile(r"\bslim\b")
DISC_PATTERN = re.compile(r"\b(?:disco|disc|leitor|standard|blu\s*-?\s*ray)\b")
DIGITAL_ONLY_PATTERNS = (
    re.compile(r"\bdigital(?:\s+edition)?\b"),
    re.compile(r"\bsem\s+(?:disco|leitor)\b"),
    re.compile(r"\bnao\s+(?:tem|inclui|aceita)\s+(?:disco|leitor)\b"),
)
DISQUALIFYING_TEXT_PATTERNS = (
    re.compile(r"\b(?:procuro|compro|queria\s+comprar)\b"),
    re.compile(
        r"\b(?:avariad[ao]|danificad[ao]|nao\s+funciona|para\s+reparar|reparacao|pecas)\b"
    ),
)
ACCESSORY_TITLE_PATTERNS = (
    re.compile(
        r"^(?:comando|controller|dualsense|jogo|jogos|headset|auscultadores|volante|vr)\b"
    ),
    re.compile(
        r"\b(?:comando|controller|dualsense|jogo|capa|tampa|cover|base|stand|suporte|headset|auscultadores|volante|vr)\s+(?:para|compativel\s+com)\s+(?:a\s+)?(?:ps\s*5|playstation\s*5)\b"
    ),
    re.compile(r"^(?:leitor|drive)\b.*\b(?:para|compativel\s+com)\b"),
)


class MonitorError(Exception):
    """Base class for expected operational failures."""


class ConfigurationError(MonitorError):
    pass


class StateError(MonitorError):
    pass


class RetrievalError(MonitorError):
    pass


class PublishError(MonitorError):
    pass


@dataclass(frozen=True)
class Config:
    ntfy_topic: str
    min_price_eur: float
    max_price_eur: float
    max_distance_km: float
    state_db_path: Path
    olx_max_results: int = 200

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Config:
        values = os.environ if environ is None else environ
        topic = values.get("NTFY_TOPIC", "eduardo_notifications").strip()
        state_path = values.get("STATE_DB_PATH", "ps5_monitor.sqlite3").strip()

        if not topic:
            raise ConfigurationError("NTFY_TOPIC cannot be empty")
        if not state_path:
            raise ConfigurationError("STATE_DB_PATH cannot be empty")

        min_price = _read_number(values, "MIN_PRICE_EUR", 200.0)
        max_price = _read_number(values, "MAX_PRICE_EUR", 300.0)
        max_distance = _read_number(values, "MAX_DISTANCE_KM", 80.0)
        max_results = _read_integer(values, "OLX_MAX_RESULTS", 200)

        if min_price >= max_price:
            raise ConfigurationError("MIN_PRICE_EUR must be lower than MAX_PRICE_EUR")
        if not 1 <= max_results <= MAX_OLX_RESULTS_LIMIT:
            raise ConfigurationError(
                f"OLX_MAX_RESULTS must be between 1 and {MAX_OLX_RESULTS_LIMIT}"
            )

        return cls(
            ntfy_topic=topic,
            min_price_eur=min_price,
            max_price_eur=max_price,
            max_distance_km=max_distance,
            state_db_path=Path(state_path),
            olx_max_results=max_results,
        )


@dataclass(frozen=True)
class Listing:
    listing_id: str
    title: str
    description: str
    url: str
    created_time: str
    status: str
    locality: str
    latitude: float
    longitude: float
    price_eur: float
    currency: str


@dataclass(frozen=True)
class Deal:
    listing: Listing
    distance_km: float


class NotificationStore:
    def __init__(self, path: Path):
        self.path = path

    def initialize(self) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS notified_listings (
                        listing_id TEXT PRIMARY KEY,
                        notified_at TEXT NOT NULL
                    )
                    """
                )
        except sqlite3.Error as exc:
            raise StateError(
                f"Cannot initialize state database {self.path}: {exc}"
            ) from exc

    def notified_ids(self) -> set[str]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT listing_id FROM notified_listings"
                ).fetchall()
        except sqlite3.Error as exc:
            raise StateError(f"Cannot read state database {self.path}: {exc}") from exc
        return {row[0] for row in rows}

    def mark_notified(self, listing_ids: Iterable[str]) -> None:
        unique_ids = list(dict.fromkeys(str(listing_id) for listing_id in listing_ids))
        if not unique_ids:
            return

        notified_at = datetime.now(timezone.utc).isoformat()
        try:
            with self._connect() as connection:
                connection.executemany(
                    """
                    INSERT OR IGNORE INTO notified_listings (listing_id, notified_at)
                    VALUES (?, ?)
                    """,
                    [(listing_id, notified_at) for listing_id in unique_ids],
                )
        except sqlite3.Error as exc:
            raise StateError(
                f"Cannot update state database {self.path}: {exc}"
            ) from exc

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.path), timeout=5)


def _read_number(values: Mapping[str, str], name: str, default: float) -> float:
    raw_value = values.get(name)
    try:
        value = default if raw_value is None else float(raw_value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{name} must be a number") from exc
    if not math.isfinite(value) or value < 0:
        raise ConfigurationError(f"{name} must be a finite non-negative number")
    return value


def _read_integer(values: Mapping[str, str], name: str, default: int) -> int:
    raw_value = values.get(name)
    if raw_value is None:
        return default
    try:
        return int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc


def normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    without_diacritics = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(without_diacritics.lower().split())


def haversine_km(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    earth_radius_km = 6371.0088
    latitude_a_radians = math.radians(latitude_a)
    latitude_b_radians = math.radians(latitude_b)
    latitude_delta = math.radians(latitude_b - latitude_a)
    longitude_delta = math.radians(longitude_b - longitude_a)
    haversine = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(latitude_a_radians)
        * math.cos(latitude_b_radians)
        * math.sin(longitude_delta / 2) ** 2
    )
    return (
        earth_radius_km * 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))
    )


def classify_listing(listing: Listing, config: Config) -> Deal | None:
    if listing.status.lower() != "active" or listing.currency.upper() != "EUR":
        return None
    if not (config.min_price_eur <= listing.price_eur < config.max_price_eur):
        return None

    text = normalize_text(f"{listing.title}\n{listing.description}")
    title = normalize_text(listing.title)
    if (
        not PS5_PATTERN.search(text)
        or not SLIM_PATTERN.search(text)
        or not DISC_PATTERN.search(text)
    ):
        return None
    if any(pattern.search(text) for pattern in DIGITAL_ONLY_PATTERNS):
        return None
    if any(pattern.search(text) for pattern in DISQUALIFYING_TEXT_PATTERNS):
        return None
    if any(pattern.search(title) for pattern in ACCESSORY_TITLE_PATTERNS):
        return None

    distance = haversine_km(
        PENAFIEL_LATITUDE,
        PENAFIEL_LONGITUDE,
        listing.latitude,
        listing.longitude,
    )
    if distance > config.max_distance_km:
        return None
    return Deal(listing=listing, distance_km=distance)


def select_deals(
    listings: Iterable[Listing],
    config: Config,
    notified_ids: set[str],
) -> list[Deal]:
    deals = []
    for listing in listings:
        if listing.listing_id in notified_ids:
            continue
        deal = classify_listing(listing, config)
        if deal is not None:
            deals.append(deal)
    return sorted(
        deals,
        key=lambda deal: (
            deal.listing.price_eur,
            deal.distance_km,
            deal.listing.listing_id,
        ),
    )


def fetch_olx_listings(
    http_client: Any = requests,
    *,
    max_results: int = 200,
    min_price_eur: float = 200,
    max_price_eur: float = 300,
) -> tuple[list[Listing], list[str]]:
    if not 1 <= max_results <= MAX_OLX_RESULTS_LIMIT:
        raise ConfigurationError(
            f"OLX_MAX_RESULTS must be between 1 and {MAX_OLX_RESULTS_LIMIT}"
        )

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (compatible; LocalPS5DealMonitor/1.0)",
    }
    listings: list[Listing] = []
    warnings: list[str] = []
    seen_ids: set[str] = set()

    for offset in range(0, max_results, OLX_PAGE_SIZE):
        payload = {
            "query": OLX_QUERY,
            "variables": {
                "searchParameters": _olx_search_parameters(
                    offset, min_price_eur, max_price_eur
                )
            },
        }
        raw_listings = _fetch_olx_page(http_client, payload, headers)
        for index, raw_listing in enumerate(raw_listings):
            try:
                listing = _parse_listing(raw_listing)
            except (TypeError, ValueError) as exc:
                identifier = (
                    raw_listing.get("id") if isinstance(raw_listing, dict) else None
                )
                label = (
                    f"listing {identifier}"
                    if identifier is not None
                    else f"item {offset + index}"
                )
                warnings.append(f"Skipping malformed OLX {label}: {exc}")
                continue
            if listing.listing_id in seen_ids:
                continue
            seen_ids.add(listing.listing_id)
            listings.append(listing)
            if len(listings) == max_results:
                break

        if len(listings) == max_results or len(raw_listings) < OLX_PAGE_SIZE:
            break
    return listings, warnings


def _olx_search_parameters(
    offset: int, min_price_eur: float, max_price_eur: float
) -> list[dict[str, str]]:
    return [
        {"key": "offset", "value": str(offset)},
        {"key": "limit", "value": str(OLX_PAGE_SIZE)},
        {"key": "query", "value": "playstation 5"},
        {"key": "sort_by", "value": "created_at:desc"},
        {"key": "filter_float_price:from", "value": f"{min_price_eur:g}"},
        {"key": "filter_float_price:to", "value": f"{max_price_eur:g}"},
    ]


def _fetch_olx_page(
    http_client: Any, payload: dict[str, Any], headers: dict[str, str]
) -> list[dict[str, Any]]:
    try:
        response = http_client.post(
            OLX_GRAPHQL_URL,
            json=payload,
            headers=headers,
            timeout=HTTP_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise RetrievalError(f"OLX request failed: {exc}") from exc

    if response.status_code != 200:
        raise RetrievalError(f"OLX returned HTTP {response.status_code}")
    try:
        body = response.json()
    except (TypeError, ValueError) as exc:
        raise RetrievalError("OLX returned invalid JSON") from exc
    return _validate_olx_response(body)


def _validate_olx_response(body: Any) -> list[dict[str, Any]]:
    if not isinstance(body, dict):
        raise RetrievalError("OLX response is not an object")
    if body.get("errors"):
        raise RetrievalError("OLX returned GraphQL errors")
    try:
        result = body["data"]["clientCompatibleListings"]
    except (KeyError, TypeError) as exc:
        raise RetrievalError("OLX response envelope is malformed") from exc
    if not isinstance(result, dict):
        raise RetrievalError("OLX listing result is malformed")
    if result.get("__typename") == "ListingError":
        raise RetrievalError("OLX returned ListingError")
    if result.get("__typename") != "ListingSuccess":
        raise RetrievalError("OLX returned an unexpected listing result")
    raw_listings = result.get("data")
    if not isinstance(raw_listings, list):
        raise RetrievalError("OLX listing collection is malformed")
    return raw_listings


def _parse_listing(raw_listing: Any) -> Listing:
    if not isinstance(raw_listing, dict):
        raise TypeError("listing is not an object")

    listing_id = raw_listing.get("id")
    if isinstance(listing_id, bool) or not isinstance(listing_id, (str, int)):
        raise TypeError("id is missing")
    listing_id = str(listing_id).strip()
    if not listing_id:
        raise ValueError("id is empty")

    title = _required_string(raw_listing, "title")
    description = _required_string(raw_listing, "description", allow_empty=True)
    url = _required_string(raw_listing, "url")
    created_time = _required_string(raw_listing, "created_time")
    status = _required_string(raw_listing, "status")

    location = raw_listing.get("location")
    if not isinstance(location, dict):
        raise TypeError("location is missing")
    locality = _nested_name(location.get("city")) or _nested_name(
        location.get("region")
    )
    if locality is None:
        raise ValueError("locality is missing")

    map_data = raw_listing.get("map")
    if not isinstance(map_data, dict):
        raise TypeError("map is missing")
    latitude = _finite_listing_number(map_data.get("lat"), "latitude")
    longitude = _finite_listing_number(map_data.get("lon"), "longitude")
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError("coordinates are outside valid ranges")

    price_value = None
    params = raw_listing.get("params")
    if not isinstance(params, list):
        raise TypeError("params are missing")
    for parameter in params:
        if not isinstance(parameter, dict) or parameter.get("key") != "price":
            continue
        candidate = parameter.get("value")
        if isinstance(candidate, dict) and candidate.get("__typename") == "PriceParam":
            price_value = candidate
            break
    if price_value is None:
        raise ValueError("price parameter is missing")

    price_eur = _finite_listing_number(price_value.get("value"), "price")
    currency = price_value.get("currency")
    if not isinstance(currency, str) or not currency.strip():
        raise ValueError("currency is missing")

    return Listing(
        listing_id=listing_id,
        title=title,
        description=description,
        url=url,
        created_time=created_time,
        status=status,
        locality=locality,
        latitude=latitude,
        longitude=longitude,
        price_eur=price_eur,
        currency=currency.strip(),
    )


def _required_string(
    mapping: dict[str, Any], key: str, allow_empty: bool = False
) -> str:
    value = mapping.get(key)
    if not isinstance(value, str):
        raise TypeError(f"{key} is missing")
    value = value.strip()
    if not value and not allow_empty:
        raise ValueError(f"{key} is empty")
    return value


def _nested_name(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    name = value.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    return name.strip()


def _finite_listing_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} is missing or not numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} is not finite")
    return number


def build_ntfy_payload(topic: str, deals: Iterable[Deal]) -> dict[str, Any]:
    selected = list(deals)[:3]
    if not selected:
        raise ValueError("At least one deal is required")

    message_sections = []
    actions = []
    for index, deal in enumerate(selected, start=1):
        listing = deal.listing
        message_sections.append(
            "\n".join(
                [
                    f"### {index}. {_escape_markdown(listing.title)}",
                    f"**€{listing.price_eur:g} · {deal.distance_km:.1f} km · {_escape_markdown(listing.locality)}**",
                    f"[Open on OLX]({listing.url})",
                ]
            )
        )
        actions.append(
            {
                "action": "view",
                "label": f"Open #{index}",
                "url": listing.url,
            }
        )

    count = len(selected)
    return {
        "topic": topic,
        "title": f"{count} new PS5 Slim Disc deal{'s' if count != 1 else ''}",
        "message": "\n\n".join(message_sections),
        "priority": 4,
        "tags": ["video_game", "moneybag"],
        "markdown": True,
        "click": selected[0].listing.url,
        "actions": actions,
    }


def _escape_markdown(value: str) -> str:
    return re.sub(r"([\\`*_{}\[\]()#+\-.!])", r"\\\1", value)


def publish_deals(
    topic: str, deals: Iterable[Deal], http_client: Any = requests
) -> None:
    payload = build_ntfy_payload(topic, deals)
    try:
        response = http_client.post(NTFY_URL, json=payload, timeout=HTTP_TIMEOUT)
    except requests.RequestException as exc:
        raise PublishError(f"ntfy request failed: {exc}") from exc
    if not 200 <= response.status_code < 300:
        raise PublishError(f"ntfy returned HTTP {response.status_code}")


def run(
    config: Config | None = None,
    http_client: Any = requests,
    store: NotificationStore | None = None,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    try:
        active_config = Config.from_env() if config is None else config
        active_store = (
            NotificationStore(active_config.state_db_path) if store is None else store
        )
        active_store.initialize()

        notified_ids = active_store.notified_ids()
        listings, warnings = fetch_olx_listings(
            http_client,
            max_results=active_config.olx_max_results,
            min_price_eur=active_config.min_price_eur,
            max_price_eur=active_config.max_price_eur,
        )
        for warning in warnings:
            print(f"Warning: {warning}", file=stderr)

        deals = select_deals(listings, active_config, notified_ids)
        selected = deals[:3]
        if not selected:
            print(
                f"Check complete: {len(listings)} listings inspected, 0 unseen matches.",
                file=stdout,
            )
            return 0

        publish_deals(active_config.ntfy_topic, selected, http_client)
        active_store.mark_notified(deal.listing.listing_id for deal in selected)
        print(
            f"Check complete: notified {len(selected)} deal(s); {len(deals) - len(selected)} remain eligible.",
            file=stdout,
        )
        return 0
    except MonitorError as exc:
        print(f"Error: {exc}", file=stderr)
        return 1


def main() -> int:
    load_dotenv()
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
