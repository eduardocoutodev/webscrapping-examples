"""Rank already-filtered Vinted candidates with OpenRouter."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

from vinted_ps5 import (
    VintedClient,
    extract_description,
    fetch_page,
    normalize_item,
    normalize_text,
    scam_reasons,
)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
NTFY_URL = "https://ntfy.sh/"
HTTP_TIMEOUT = (5, 20)
MAX_DEALS_PER_NOTIFICATION = 3
MIN_PRICE_EUR = 280.0
MAX_PRICE_EUR = 400.0
MIN_SELLER_RATING = 3.0
MIN_SELLER_REVIEWS = 1
AI_CONFIDENCE_THRESHOLD = 0.75
DEFAULT_BATCH_SIZE = 5
DEFAULT_DATABASE = Path("vinted_deals.sqlite3")
DEFAULT_MODEL = "openai/gpt-5.6-luna"
DEFAULT_MAX_RUN_COST_USD = 1.50
MAX_BATCH_OUTPUT_TOKENS = 1600
MAX_MODEL_ATTEMPTS = 2
INPUT_TOKEN_BUFFER = 2500

# These are intentionally strict: the mission is a PS5 console, not anything
# merely mentioning PS5.
NON_CONSOLE_PATTERN = re.compile(
    r"\b(?:nintendo|switch(?:\s*[12])?|vr\s*2|vr2|psvr|ps\s*portal|portal remote|jeu(?:x)?|juego(?:s)?|"
    r"gioco|giochi|lotto|lot\s+de|lote|pack|collector|collectors|coleccionista|"
    r"colecionador(?:es)?|steelbook|figurine|cardboard|display|"
    r"statue|coque|case|cover|"
    r"volant|volante|wheel|steering|headset|casque|monitor|schermo|ecran|"
    r"ssd|hdd|cable|c[aâ]ble|chargeur|charger|board|drive|lecteur seul|"
    r"for parts|not working|broken|non funzionante|rotto|para pecas|pour pieces)\b",
    re.I,
)
PS4_PATTERN = re.compile(r"\b(?:ps\s*4|playstation\s*4)\b", re.I)
PS5_PATTERN = re.compile(r"\b(?:ps\s*5|play\s*station\s*5|playstation\s*5)\b", re.I)
CONSOLE_PATTERN = re.compile(
    r"\b(?:console|consola|console|slim|digital edition|disc edition|disc|"
    r"standard|digital|825\s*gb|1\s*tb|cfi[- ]?\d{4})\b",
    re.I,
)
CONTROLLER_PATTERN = re.compile(
    r"\b(?:manette|mando|joypad|controller|comando|dualsense)\b", re.I
)
DIGITAL_PATTERN = re.compile(
    r"\b(?:(?<!western )digital(?: edition)?|version digital|disc[ -]?less|sem (?:leitor|disco)|"
    r"sin (?:lector|disco)|sans (?:lecteur|disque)|senza (?:lettore|disco)|"
    r"ohne laufwerk|no disc drive)\b",
    re.I,
)
DISC_PATTERN = re.compile(
    r"\b(?:discs?|disks?|discos?|disques?|leitor|lector|lecteur|lettore|laufwerk|"
    r"blu[ -]?ray|standard(?: edition)?|edition standard|edicao standard|"
    r"edicion estandar|edizione standard)\b",
    re.I,
)
SLIM_PATTERN = re.compile(r"\bslim\b", re.I)
CFI_PATTERN = re.compile(r"\bcfi[- ]?(?P<series>\d{2})\d{2}(?P<edition>[ab])\b", re.I)


def extract_seller(html: str) -> dict[str, Any]:
    """Extract public seller metrics embedded in the item page state."""
    seller_match = re.search(
        r'\\?"seller_id\\?":(\d+).*?\\?"name\\?":\\?"([^"\\]*)'
        r'.*?\\?"feedback_count\\?":(\d+).*?\\?"feedback_reputation\\?":([\d.]+)',
        html,
        re.S,
    )
    if not seller_match:
        return {"seller_id": None, "seller_name": None, "reviews": None, "rating": None}
    seller_id, name, reviews, rating = seller_match.groups()
    rating_value = float(rating)
    # Vinted commonly exposes reputation as 0..1 while the user-facing rule
    # is expressed as a five-star rating.
    if 0 <= rating_value <= 1:
        rating_value *= 5
    return {
        "seller_id": int(seller_id),
        "seller_name": name,
        "reviews": int(reviews),
        "rating": rating_value,
    }


def extract_seller_profile(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize the current seller metrics returned by Vinted's user API."""
    user = payload.get("user")
    if not isinstance(user, dict):
        return {"seller_id": None, "seller_name": None, "reviews": None, "rating": None}
    rating = user.get("feedback_reputation")
    if isinstance(rating, (int, float)) and 0 <= rating <= 1:
        rating = round(rating * 5, 1)
    return {
        "seller_id": user.get("id"),
        "seller_name": user.get("login"),
        "profile_url": user.get("profile_url"),
        "reviews": user.get("feedback_count"),
        "rating": rating,
    }


def classify_disc_console(item: dict[str, Any]) -> str | None:
    """Return slim_disc/fat_disc only when the listing explicitly supports discs."""
    text = normalize_text(f"{item.get('title', '')} {item.get('description', '')}")
    model_match = CFI_PATTERN.search(text)
    if DIGITAL_PATTERN.search(text) or (model_match and model_match.group("edition").lower() == "b"):
        return None
    has_disc = bool(DISC_PATTERN.search(text))
    if model_match and model_match.group("edition").lower() == "a":
        has_disc = True
    if not has_disc:
        return None
    is_slim = bool(SLIM_PATTERN.search(text)) or bool(
        model_match and model_match.group("series") == "20"
    )
    return "slim_disc" if is_slim else "fat_disc"


def deal_for(console_type: str, price: float) -> dict[str, Any] | None:
    """Apply the user's model-specific buying thresholds."""
    if console_type == "slim_disc":
        if price <= 350:
            return {"tier": "excellent", "label": "🔥 compra excelente — age rápido", "priority": 3}
        if price <= 375:
            return {"tier": "good", "label": "⭐ bom negócio", "priority": 2}
        if price <= 400:
            return {"tier": "conditional", "label": "👍 se tiver caixa e bom estado", "priority": 1}
        return None
    if console_type == "fat_disc":
        if price <= 300:
            return {"tier": "excellent", "label": "🔥 compra excelente", "priority": 3}
        if price <= 325:
            return {"tier": "good", "label": "⭐ bom negócio", "priority": 2}
        if price <= 350:
            return {
                "tier": "conditional",
                "label": "👍 se tiver caixa, fatura e estado excelente",
                "priority": 1,
            }
    return None


def seller_is_eligible(seller: dict[str, Any]) -> bool:
    return (
        isinstance(seller.get("rating"), (int, float))
        and isinstance(seller.get("reviews"), int)
        and seller["rating"] >= MIN_SELLER_RATING
        and seller["reviews"] >= MIN_SELLER_REVIEWS
    )


def apply_purchase_rules(
    candidates: list[dict[str, Any]], *, report: bool = False
) -> list[dict[str, Any]]:
    qualified: list[dict[str, Any]] = []
    rejected = {
        "invalid/outside budget": 0,
        "not explicitly Disc": 0,
        "outside model price limit": 0,
        "seller below threshold": 0,
    }
    for item in candidates:
        try:
            price = float(item["price_eur"])
        except (KeyError, TypeError, ValueError):
            rejected["invalid/outside budget"] += 1
            continue
        if not MIN_PRICE_EUR <= price <= MAX_PRICE_EUR:
            rejected["invalid/outside budget"] += 1
            continue
        console_type = classify_disc_console(item)
        if not console_type:
            rejected["not explicitly Disc"] += 1
            continue
        deal = deal_for(console_type, price)
        if not deal:
            rejected["outside model price limit"] += 1
            continue
        seller = item.get("seller")
        if not isinstance(seller, dict) or not seller_is_eligible(seller):
            rejected["seller below threshold"] += 1
            continue
        qualified.append({**item, "console_type": console_type, "deal": deal})
    if report:
        details = ", ".join(f"{count} {reason}" for reason, count in rejected.items() if count)
        print(
            f"Purchase rules: {len(qualified)}/{len(candidates)} kept"
            + (f" ({details})" if details else ""),
            file=sys.stderr,
        )
    return qualified


def is_console_candidate(item: dict[str, Any]) -> bool:
    title = normalize_text(item.get("title", ""))
    text = normalize_text(f"{item.get('title', '')} {item.get('description', '')}")
    if PS4_PATTERN.search(title):
        return False
    if not PS5_PATTERN.search(text):
        return False
    if NON_CONSOLE_PATTERN.search(title):
        return False
    # A controller mention is fine when the title also identifies the console;
    # reject controller-only listings such as "DualSense PS5".
    controller_with_console = re.match(
        r"^(?:ps\s*5|playstation\s*5)\s+(?:con|com|with|avec)\b", title
    )
    if (
        CONTROLLER_PATTERN.search(title)
        and not CONSOLE_PATTERN.search(title)
        and not controller_with_console
    ):
        return False
    return bool(CONSOLE_PATTERN.search(text))


class ListingStore:
    """Persist normalized catalog and detail-page content between runs."""

    def __init__(self, path: Path) -> None:
        self.path = path
        with sqlite3.connect(path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS listings (
                    listing_id TEXT PRIMARY KEY,
                    catalog_json TEXT NOT NULL,
                    title TEXT,
                    price_eur REAL,
                    description TEXT,
                    seller_json TEXT,
                    catalog_fetched_at TEXT NOT NULL,
                    detail_fetched_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_rankings (
                    listing_id TEXT NOT NULL,
                    run_at TEXT NOT NULL,
                    decision_json TEXT NOT NULL,
                    PRIMARY KEY (listing_id, run_at)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS notified_vinted_listings (
                    listing_id TEXT PRIMARY KEY,
                    notified_at TEXT NOT NULL
                )
                """
            )

    def save_catalog(self, item: dict[str, Any], raw_item: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                INSERT INTO listings
                    (listing_id, catalog_json, title, price_eur, catalog_fetched_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(listing_id) DO UPDATE SET
                    catalog_json=excluded.catalog_json,
                    title=excluded.title,
                    price_eur=excluded.price_eur,
                    catalog_fetched_at=excluded.catalog_fetched_at
                """,
                (str(item["id"]), json.dumps(raw_item, ensure_ascii=False),
                 item.get("title"), float(item.get("price_eur") or 0), now),
            )

    def save_detail(self, item: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        seller = item.get("seller", {})
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                UPDATE listings
                SET description=?, seller_json=?, detail_fetched_at=?
                WHERE listing_id=?
                """,
                (item.get("description", ""), json.dumps(seller, ensure_ascii=False), now,
                 str(item["id"])),
            )

    def save_decision(self, listing_id: Any, run_at: str, decision: dict[str, Any]) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "INSERT OR REPLACE INTO ai_rankings VALUES (?, ?, ?)",
                (str(listing_id), run_at, json.dumps(decision, ensure_ascii=False)),
            )

    def notified_ids(self) -> set[str]:
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                "SELECT listing_id FROM notified_vinted_listings"
            ).fetchall()
        return {row[0] for row in rows}

    def mark_notified(self, listing_ids: list[str]) -> None:
        unique_ids = list(dict.fromkeys(str(listing_id) for listing_id in listing_ids))
        if not unique_ids:
            return
        notified_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.path) as connection:
            connection.executemany(
                """
                INSERT OR IGNORE INTO notified_vinted_listings (listing_id, notified_at)
                VALUES (?, ?)
                """,
                [(listing_id, notified_at) for listing_id in unique_ids],
            )

    def load_items(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                "SELECT catalog_json, description, seller_json FROM listings"
            ).fetchall()
        items: list[dict[str, Any]] = []
        for catalog_json, description, seller_json in rows:
            item = normalize_item(json.loads(catalog_json))
            item["description"] = description or ""
            item["seller"] = json.loads(seller_json) if seller_json else {}
            items.append(item)
        return items


def load_candidates(
    raw: list[dict[str, Any]], *, search_max_price: float = MAX_PRICE_EUR
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen_ids: set[Any] = set()
    for item in raw:
        try:
            price = float(item["price_eur"])
        except (KeyError, TypeError, ValueError):
            continue
        item_id = item.get("id")
        if item_id in seen_ids or not MIN_PRICE_EUR <= price <= search_max_price:
            continue
        seller = item.get("seller")
        if isinstance(seller, dict) and isinstance(seller.get("rating"), (int, float)):
            if 0 <= seller["rating"] <= 1:
                item["seller"] = {**seller, "rating": seller["rating"] * 5}
        if not is_console_candidate(item) or scam_reasons(item.get("description", "")):
            continue
        seen_ids.add(item_id)
        candidates.append({**item, "price_eur": price})
    return candidates


def resolve_model(
    api_key: str, requested_model: str | None = None
) -> tuple[str, dict[str, float]]:
    model_id = DEFAULT_MODEL if requested_model in (None, "auto") else requested_model
    response = requests.get(
        OPENROUTER_MODELS_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30,
    )
    response.raise_for_status()
    for model in response.json().get("data", []):
        if model.get("id") != model_id:
            continue
        supported = set(model.get("supported_parameters", []))
        required = {"structured_outputs", "response_format"}
        if not required.issubset(supported):
            raise RuntimeError(f"{model_id} does not support strict structured outputs")
        raw_pricing = model.get("pricing")
        if not isinstance(raw_pricing, dict):
            raise RuntimeError(f"OpenRouter returned no pricing for {model_id}")
        try:
            pricing = {
                "prompt": float(raw_pricing["prompt"]),
                "completion": float(raw_pricing["completion"]),
                "internal_reasoning": float(raw_pricing.get("internal_reasoning", 0)),
                "request": float(raw_pricing.get("request", 0)),
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"OpenRouter returned invalid pricing for {model_id}") from exc
        return model_id, pricing
    raise RuntimeError(f"OpenRouter model is unavailable: {model_id}")


def parse_rankings(content: str) -> list[dict[str, Any]]:
    """Accept strict JSON, fenced JSON, or JSON surrounded by model prose."""
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I)
    candidates = [cleaned]
    # Some reasoning models occasionally prepend an extra `{` before the
    # schema object. Try every plausible JSON object start before failing.
    for opening, closing in (("{", "}"), ("[", "]")):
        end = cleaned.rfind(closing)
        if end < 0:
            continue
        for start in (match.start() for match in re.finditer(re.escape(opening), cleaned)):
            if start < end:
                candidates.append(cleaned[start : end + 1])
    last_error: json.JSONDecodeError | None = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, list):
                results = parsed
            elif isinstance(parsed, dict) and "results" in parsed:
                results = parsed["results"]
            elif isinstance(parsed, dict) and "id" in parsed:
                results = [parsed]
            else:
                results = None
            if isinstance(results, list):
                return results
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            if isinstance(exc, json.JSONDecodeError):
                last_error = exc
    if last_error:
        raise last_error
    raise ValueError("OpenRouter response did not contain a results list")


def normalize_decision(decision: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(decision)
    if "reasons" not in normalized and "short_reasons" in normalized:
        normalized["reasons"] = normalized.pop("short_reasons")
    if isinstance(normalized.get("reasons"), str):
        normalized["reasons"] = [normalized["reasons"]]
    return normalized


def llm_hard_rejects(item: dict[str, Any], decision: dict[str, Any]) -> bool:
    """Keep obvious bad products/scams out of both final-results categories."""
    if not decision:
        return False
    if decision.get("status") == "rejected" or decision.get("is_desired_ps5") is False:
        return True
    if decision.get("scam_risk") in {"medium", "high"}:
        return True
    reasons = normalize_text(" ".join(str(reason) for reason in decision.get("reasons", [])))
    return bool(
        re.search(
            r"\b(?:bank transfer|virement|bonifico|paypal|revolut|whatsapp|telegram|"
            r"off[- ]platform|outside vinted|avoid vinted|not worth|nintendo|switch|"
            r"not a ps5|not ps5|no console|does not include (?:a )?(?:ps5|console)|"
            r"(?:game|controller|accessory)(?: only| listing))\b",
            reasons,
        )
    )


def ai_is_eligible(decision: dict[str, Any]) -> bool:
    return (
        decision.get("status") == "accepted"
        and decision.get("is_desired_ps5") is True
        and decision.get("scam_risk") == "low"
        and decision.get("confidence", 0) >= AI_CONFIDENCE_THRESHOLD
    )


def collect_candidates(
    store: ListingStore,
    *,
    pages: int,
    per_page: int,
    requests_per_minute: int,
    search_max_price: float = MAX_PRICE_EUR,
) -> list[dict[str, Any]]:
    client = VintedClient(requests_per_minute=requests_per_minute)
    raw_candidates: list[dict[str, Any]] = []
    for page in range(1, pages + 1):
        payload = fetch_page(
            client,
            search_text="playstation 5",
            price_from=MIN_PRICE_EUR,
            price_to=search_max_price,
            page=page,
            per_page=per_page,
        )
        print(f"Catalog page {page}: {len(payload['items'])} listings", file=sys.stderr)
        for raw_item in payload["items"]:
            item = normalize_item(raw_item)
            store.save_catalog(item, raw_item)
            raw_candidates.append(item)
        if len(payload["items"]) < per_page:
            break
    candidates = load_candidates(raw_candidates, search_max_price=search_max_price)
    enrich_sellers(candidates, client, store)
    return candidates


def enrich_sellers(
    candidates: list[dict[str, Any]], client: VintedClient, store: ListingStore
) -> None:
    for index, item in enumerate(candidates, 1):
        try:
            response = client.get_item_page(item["url"])
            response.raise_for_status()
        except requests.HTTPError as exc:
            item["seller"] = {}
            print(
                f"Seller data [{index}/{len(candidates)}]: unavailable ({exc}); skipped",
                file=sys.stderr,
            )
            continue
        item["description"] = extract_description(response.text) or item.get("description", "")
        catalog_seller = item.get("seller") if isinstance(item.get("seller"), dict) else {}
        seller_id = catalog_seller.get("seller_id")
        if not isinstance(seller_id, int):
            seller_id = extract_seller(response.text).get("seller_id")
        try:
            seller_response = client.get_user(seller_id) if isinstance(seller_id, int) else None
            if seller_response is None:
                raise RuntimeError("seller id unavailable")
            seller_response.raise_for_status()
            item["seller"] = extract_seller_profile(seller_response.json())
        except (requests.RequestException, RuntimeError, ValueError):
            item["seller"] = {**catalog_seller, "reviews": None, "rating": None}
        store.save_detail(item)
        print(
            f"Seller data [{index}/{len(candidates)}]: {item['title']} "
            f"(reviews={item['seller'].get('reviews')}, rating={item['seller'].get('rating')})",
            file=sys.stderr,
        )


def collect_from_file(
    path: Path, store: ListingStore, *, search_max_price: float = MAX_PRICE_EUR
) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise RuntimeError(f"{path} must contain a JSON list")
    normalized: list[dict[str, Any]] = []
    for raw_item in raw:
        item = (
            dict(raw_item)
            if "price_eur" in raw_item
            else normalize_item(raw_item)
        )
        item["description"] = raw_item.get("description", "")
        store.save_catalog(item, raw_item)
        normalized.append(item)
    candidates = load_candidates(normalized, search_max_price=search_max_price)
    enrich_sellers(candidates, VintedClient(), store)
    return candidates


def build_ranking_prompt(batch: list[dict[str, Any]]) -> str:
    compact = [
        {
            "id": item["id"],
            "title": item["title"],
            "price_eur": item["price_eur"],
            "status": item.get("status"),
            "description": item.get("description", "")[:1500],
            "seller": item.get("seller", {}),
            "console_type": item.get("console_type"),
            "deal": item.get("deal"),
        }
        for item in batch
    ]
    return (
        "Rank these Vinted PlayStation 5 console listings for a safe buyer. "
        "For every listing, independently inspect the title and description and decide "
        "whether it is actually a PS5 console that a buyer wants. Reject games, bundles "
        "without a console, VR/PS Portal, controllers, parts, repairs, and accessories. "
        "Also inspect the full description for scam signals: off-platform payment, bank "
        "transfer, PayPal, Revolut, wallet restrictions, WhatsApp/Telegram, private-only "
        "deals, hand delivery, suspicious urgency, or instructions to avoid Vinted. "
        "Any scam signal means scam_risk must not be low. Use status=rejected when "
        "the listing is clearly not worth considering, is not a PS5, or contains an "
        "off-platform payment/scam signal; rejected listings will be omitted. "
        "Every input has already passed these hard rules: disc console, price and seller. "
        "Prefer Slim Disc within the same deal tier. Deal tiers are: Slim <=350 excellent, "
        "<=375 good, <=400 conditional; Fat <=300 excellent, <=325 good, <=350 conditional. "
        "Return one result per id with "
        "is_desired_ps5 (boolean), scam_risk (low, medium, or high), status "
        "accepted, review, or rejected, confidence from 0 to 1, rank_score from 0 to 100, "
        "and short reasons. Never invent missing facts.\n\nListings:\n"
        + json.dumps(compact, ensure_ascii=False)
    )


def estimate_batch_max_cost(batch: list[dict[str, Any]], pricing: dict[str, float]) -> float:
    # UTF-8 bytes are a deliberately high token estimate. The buffer covers
    # chat framing and the JSON schema, which are not part of the prompt text.
    input_token_ceiling = len(build_ranking_prompt(batch).encode("utf-8")) + INPUT_TOKEN_BUFFER
    output_rate = max(pricing["completion"], pricing["internal_reasoning"])
    per_attempt = (
        input_token_ceiling * pricing["prompt"]
        + MAX_BATCH_OUTPUT_TOKENS * output_rate
        + pricing["request"]
    )
    return per_attempt * MAX_MODEL_ATTEMPTS


def estimate_run_max_cost(
    candidates: list[dict[str, Any]], batch_size: int, pricing: dict[str, float]
) -> float:
    return sum(
        estimate_batch_max_cost(candidates[start : start + batch_size], pricing)
        for start in range(0, len(candidates), batch_size)
    )


def rank_batch(
    api_key: str,
    model: str,
    pricing: dict[str, float],
    batch: list[dict[str, Any]],
    batch_number: int,
    debug_dir: Path | None = None,
) -> tuple[list[dict[str, Any]], float]:
    prompt = build_ranking_prompt(batch)
    max_prompt_price = pricing["prompt"] * 1_000_000
    max_completion_price = pricing["completion"] * 1_000_000
    body = {
        "model": model,
        "max_tokens": MAX_BATCH_OUTPUT_TOKENS,
        "reasoning": {"effort": "low", "exclude": True},
        "messages": [
            {"role": "system", "content": "You are a cautious marketplace deal evaluator."},
            {"role": "user", "content": prompt},
        ],
        "provider": {
            "require_parameters": True,
            "max_price": {
                "prompt": max_prompt_price,
                "completion": max_completion_price,
            },
        },
        "plugins": [{"id": "response-healing"}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "vinted_rankings",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "results": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "integer"},
                                    "is_desired_ps5": {"type": "boolean"},
                                    "scam_risk": {"type": "string", "enum": ["low", "medium", "high"]},
                                    "status": {"type": "string", "enum": ["accepted", "review", "rejected"]},
                                    "confidence": {"type": "number"},
                                    "rank_score": {"type": "number"},
                                    "reasons": {"type": "array", "items": {"type": "string"}},
                                },
                                "required": ["id", "is_desired_ps5", "scam_risk", "status", "confidence", "rank_score", "reasons"],
                                "additionalProperties": False,
                            },
                        }
                    },
                    "required": ["results"],
                    "additionalProperties": False,
                },
            },
        },
    }
    def call(request_body: dict[str, Any], attempt: int) -> tuple[str, float]:
        response = requests.post(
            OPENROUTER_CHAT_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=request_body,
            timeout=90,
        )
        raw_response = response.text
        if debug_dir:
            debug_dir.mkdir(parents=True, exist_ok=True)
            (debug_dir / f"batch-{batch_number:02d}-attempt-{attempt}.json").write_text(
                raw_response, encoding="utf-8"
            )
        print(
            f"LLM batch {batch_number} attempt {attempt}: HTTP {response.status_code}, "
            f"request_id={response.headers.get('x-request-id', 'n/a')}, "
            f"response_bytes={len(raw_response)}",
            file=sys.stderr,
        )
        response.raise_for_status()
        payload = response.json()
        choice = payload.get("choices", [{}])[0]
        message = choice.get("message", {})
        content = message.get("content")
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        try:
            cost = float(usage.get("cost", 0))
        except (TypeError, ValueError):
            cost = 0.0
        if not cost:
            cost = (
                int(usage.get("prompt_tokens", 0)) * pricing["prompt"]
                + int(usage.get("completion_tokens", 0)) * pricing["completion"]
            )
        print(
            f"LLM batch {batch_number} attempt {attempt}: "
            f"finish_reason={choice.get('finish_reason', 'n/a')}, "
            f"content_chars={len(content or '')}, "
            f"reasoning_chars={len(message.get('reasoning') or '')}, "
            f"cost=${cost:.6f}",
            file=sys.stderr,
        )
        if not content:
            raise ValueError(f"OpenRouter returned empty content; response={raw_response[:500]}")
        return content, cost

    content, total_cost = call(body, 1)
    try:
        return parse_rankings(content), total_cost
    except (json.JSONDecodeError, ValueError):
        print(
            f"LLM batch {batch_number} returned an unexpected shape; "
            f"content_preview={content[:500]!r}",
            file=sys.stderr,
        )
        # Keep the schema enforced on retry; json_object would allow the model
        # to return valid JSON with the wrong shape.
        body["messages"][1]["content"] += (
            "\nReturn only the JSON object matching the supplied schema. "
            "Do not include markdown or commentary."
        )
        retry_content, retry_cost = call(body, 2)
        total_cost += retry_cost
        try:
            return parse_rankings(retry_content), total_cost
        except (json.JSONDecodeError, ValueError):
            print(
                f"LLM batch {batch_number} retry content_preview={retry_content[:500]!r}",
                file=sys.stderr,
            )
            return [], total_cost


def rank_candidates(
    candidates: list[dict[str, Any]],
    api_key: str,
    batch_size: int,
    store: ListingStore,
    model_name: str | None = None,
    max_cost_usd: float = DEFAULT_MAX_RUN_COST_USD,
    debug_dir: Path | None = None,
) -> dict[str, Any]:
    model, pricing = resolve_model(api_key, model_name)
    reserved_cost = estimate_run_max_cost(candidates, batch_size, pricing)
    if reserved_cost > max_cost_usd:
        raise RuntimeError(
            f"Worst-case model cost ${reserved_cost:.2f} exceeds the "
            f"${max_cost_usd:.2f} run limit; use fewer pages or a cheaper model"
        )
    print(
        f"Using OpenRouter model: {model} "
        f"(maximum reserved=${reserved_cost:.4f}, limit=${max_cost_usd:.2f})",
        file=sys.stderr,
    )
    decisions: dict[Any, dict[str, Any]] = {}
    actual_cost = 0.0
    run_at = datetime.now(timezone.utc).isoformat()
    for start in range(0, len(candidates), batch_size):
        batch = candidates[start : start + batch_size]
        print(
            f"AI batch {start // batch_size + 1}/{(len(candidates) + batch_size - 1) // batch_size} "
            f"({len(batch)} listings)",
            file=sys.stderr,
        )
        try:
            batch_decisions, batch_cost = rank_batch(
                api_key,
                model,
                pricing,
                batch,
                start // batch_size + 1,
                debug_dir,
            )
            actual_cost += batch_cost
            if actual_cost > max_cost_usd:
                raise RuntimeError(
                    f"OpenRouter reported ${actual_cost:.4f}, above the "
                    f"${max_cost_usd:.2f} run limit"
                )
            for decision in batch_decisions:
                decision = normalize_decision(decision)
                decisions[decision["id"]] = decision
                store.save_decision(decision["id"], run_at, decision)
        except RuntimeError:
            raise
        except (requests.RequestException, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            print(f"AI batch failed; moving its listings to review: {exc}", file=sys.stderr)

    accepted: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    for item in candidates:
        seller = item.get("seller", {})
        decision = decisions.get(item["id"], {})
        deterministic_scam = scam_reasons(item.get("description", ""))
        hard_rejected = (
            not is_console_candidate(item)
            or bool(deterministic_scam)
            or llm_hard_rejects(item, decision)
        )
        if hard_rejected:
            continue
        seller_ok = isinstance(seller, dict) and seller_is_eligible(seller)
        ai_ok = ai_is_eligible(decision)
        output = {**item, "ai": decision, "seller_eligible": seller_ok}
        if seller_ok and ai_ok:
            accepted.append(output)
        else:
            review.append(output)
    sort_key = lambda item: (
        item["deal"]["priority"],
        item["console_type"] == "slim_disc",
        item["ai"].get("rank_score", 0),
    )
    accepted.sort(key=sort_key, reverse=True)
    review.sort(key=sort_key, reverse=True)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "model_cost_usd": round(actual_cost, 6),
        "maximum_reserved_cost_usd": round(reserved_cost, 6),
        "run_cost_limit_usd": max_cost_usd,
        "rules": {
            "price_eur": [MIN_PRICE_EUR, MAX_PRICE_EUR],
            "console_types": ["slim_disc", "fat_disc"],
            "minimum_seller_rating": MIN_SELLER_RATING,
            "minimum_seller_reviews": MIN_SELLER_REVIEWS,
            "accepted_ai_confidence": AI_CONFIDENCE_THRESHOLD,
        },
        "accepted": accepted,
        "review": review,
    }


def build_ntfy_payload(topic: str, deals: list[dict[str, Any]]) -> dict[str, Any]:
    selected = deals[:MAX_DEALS_PER_NOTIFICATION]
    if not selected:
        raise ValueError("at least one deal is required")
    sections = []
    actions = []
    for index, deal in enumerate(selected, 1):
        seller = deal.get("seller", {})
        console = "Slim Disc" if deal.get("console_type") == "slim_disc" else "Fat Disc"
        sections.append(
            "\n".join(
                [
                    f"**{index}. {_escape_markdown(str(deal.get('title', 'PS5')))}**",
                    f"€{float(deal['price_eur']):.0f} · {console} · {deal['deal']['label']}",
                    f"Seller: {seller.get('rating', '?')}/5 ({seller.get('reviews', '?')} reviews)",
                    f"[Open on Vinted]({deal['url']})",
                ]
            )
        )
        actions.append(
            {
                "action": "view",
                "label": f"Open #{index}",
                "url": deal["url"],
            }
        )
    count = len(selected)
    return {
        "topic": topic,
        "title": f"{count} new accepted Vinted PS5 deal{'s' if count != 1 else ''}",
        "message": "\n\n".join(sections),
        "priority": 4,
        "tags": ["video_game", "moneybag"],
        "markdown": True,
        "click": selected[0]["url"],
        "actions": actions,
    }


def _escape_markdown(value: str) -> str:
    return re.sub(r"([\\`*_{}\[\]()#+\-.!])", r"\\\1", value)


def notify_new_deals(
    topic: str,
    accepted: list[dict[str, Any]],
    store: ListingStore,
    http_client: Any = requests,
) -> int:
    notified = store.notified_ids()
    selected = [deal for deal in accepted if str(deal["id"]) not in notified][
        :MAX_DEALS_PER_NOTIFICATION
    ]
    if not selected:
        return 0
    try:
        response = http_client.post(
            NTFY_URL,
            json=build_ntfy_payload(topic, selected),
            timeout=HTTP_TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"ntfy notification failed: {exc}") from exc
    store.mark_notified([str(deal["id"]) for deal in selected])
    return len(selected)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("live", "file", "sqlite"), default="live")
    parser.add_argument("--input", type=Path, default=Path("results.json"))
    parser.add_argument("--output", type=Path, default=Path("final-results.json"))
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--per-page", type=int, default=96)
    parser.add_argument(
        "--search-max-price",
        type=float,
        default=MAX_PRICE_EUR,
        help=(
            "catalog discovery ceiling; buying rules remain unchanged "
            f"(default: €{MAX_PRICE_EUR:.0f})"
        ),
    )
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"OpenRouter model ID (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--max-cost-usd",
        type=float,
        default=DEFAULT_MAX_RUN_COST_USD,
        help=f"hard model-cost limit per run (default: ${DEFAULT_MAX_RUN_COST_USD:.2f})",
    )
    parser.add_argument("--debug-llm", action="store_true", help="save raw LLM responses under llm-debug/")
    parser.add_argument("--requests-per-minute", type=int, default=30)
    args = parser.parse_args()
    load_dotenv()
    import os

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        parser.error("OPENROUTER_API_KEY is not set")
    if args.pages < 1 or not 1 <= args.per_page <= 96:
        parser.error("--pages must be positive and --per-page must be between 1 and 96")
    if args.batch_size < 1 or args.requests_per_minute < 1 or args.max_cost_usd <= 0:
        parser.error("--batch-size, --requests-per-minute, and --max-cost-usd must be positive")
    if args.search_max_price < MAX_PRICE_EUR:
        parser.error(f"--search-max-price must be at least €{MAX_PRICE_EUR:.0f}")
    try:
        store = ListingStore(args.database)
        if args.source == "live":
            candidates = collect_candidates(
                store,
                pages=args.pages,
                per_page=args.per_page,
                requests_per_minute=args.requests_per_minute,
                search_max_price=args.search_max_price,
            )
        elif args.source == "file":
            candidates = collect_from_file(
                args.input, store, search_max_price=args.search_max_price
            )
        else:
            candidates = load_candidates(
                store.load_items(), search_max_price=args.search_max_price
            )
        candidates = apply_purchase_rules(candidates, report=True)
        result = rank_candidates(
            candidates,
            api_key,
            args.batch_size,
            store,
            model_name=args.model,
            max_cost_usd=args.max_cost_usd,
            debug_dir=Path("llm-debug") if args.debug_llm else None,
        )
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            f"Wrote {args.output}: {len(result['accepted'])} accepted, "
            f"{len(result['review'])} review",
            file=sys.stderr,
        )
        ntfy_topic = os.getenv("NTFY_TOPIC", "").strip()
        if ntfy_topic:
            notified_count = notify_new_deals(
                ntfy_topic, result["accepted"], store
            )
            print(
                f"ntfy: sent {notified_count} new accepted deal(s) to {ntfy_topic}",
                file=sys.stderr,
            )
    except (OSError, requests.RequestException, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
