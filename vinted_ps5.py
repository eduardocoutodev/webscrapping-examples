"""Scrape public Vinted catalog results for PlayStation 5 listings."""

from __future__ import annotations

import argparse
from collections import deque
import json
import re
import sys
import time
import unicodedata
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup
import requests

CATALOG_ENDPOINT = "https://www.vinted.pt/api/v2/catalog/items"
DEFAULT_SEARCH_URL = (
    "https://www.vinted.pt/catalog?search_text=playstation%205"
    "&price_from=280.00&price_to=400.00&currency=EUR"
    "&order=newest_first"
)
HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8",
    "Referer": DEFAULT_SEARCH_URL,
    "User-Agent": "Mozilla/5.0 (compatible; VintedCatalogReader/1.0)",
}
CSRF_TOKEN_PATTERN = re.compile(
    r'\\?"CSRF_TOKEN\\?"\s*:\s*\\?"([^"\\]+)'
)
CSRF_REFRESH_SECONDS = 10 * 60
DEFAULT_REQUESTS_PER_MINUTE = 30
MAX_RATE_LIMIT_RETRIES = 3
PS5_PATTERN = re.compile(r"\b(?:ps\s*5|play\s*station\s*5|playstation\s*5)\b")
SCAM_PATTERNS = (
    re.compile(r"\b(?:procuro|procura|compro|busco|cerco|suche|recherche|je\s+cherche|looking\s+for|wanted)\b"),
    re.compile(r"\bpay\s*pal\b"),
    re.compile(r"\b(?:bank|wire)\s+transfer\b|\b(?:transferencia|transfer|virement|bonifico|ueberweisung)\s+(?:bancaria?|bancario|bancar|bancaire)\b|\b(?:virement|bonifico|ueberweisung)\b"),
    re.compile(r"\b(?:revolut|wise|western\s+union|bitcoin|crypto|cashapp|venmo|zelle)\b"),
    re.compile(r"\b(?:mb\s*-?\s*way|bizum)\b"),
    re.compile(r"\b(?:whatsapp|telegram|signal)\b"),
    re.compile(r"\b(?:envio|envio|shipping|expedition|spedizione)\s+(?:apenas|somente|only|uniquement|solo)\b"),
    re.compile(r"\b(?:accetto|solo|only|uniquement|vende)\b.{0,30}\b(?:privati|particulares|particuliers|private|privat)\b"),
    re.compile(r"\b(?:wallet|carteira|porte\s+monnaie)\b.{0,40}\bvinted\b"),
    re.compile(r"\b(?:experiencias|experiences|esperienze)\s+(?:ruins|mauvaises|negative|negativas)\b.{0,30}\bvinted\b"),
    re.compile(r"\b(?:outside|off|hors|fuori|fuera|fora|ausserhalb)\s+(?:of\s+)?(?:the\s+)?vinted\b"),
    re.compile(r"\b(?:outside|hors|fuori|fuera|fora|ausserhalb)\s+(?:of|the|da|de|la|von)?\s*vinted\b"),
    re.compile(r"\b(?:sem|sin|sans|senza|ohne)\s+vinted\b"),
    re.compile(r"\b(?:pay(?:ment|ement)|paiement|pagamento|pago|zahlung)\b.{0,35}\b(?:not|pas|non|no|ne|hors|fuori|fora|sem|sin|sans)\b.{0,35}\b(?:here|ici|qui|vinted)\b"),
    re.compile(r"\b(?:n['’]?achetez\s+pas|ne\s+pas\s+acheter|non\s+comprare|non\s+acquistare|no\s+comprar|no\s+compres|don['’]?t\s+buy)\b"),
    re.compile(r"\b(?:not|no|non|ne|hors|fuori|fora)\b.{0,25}\b(?:sell|vendo|vends|venda|acheter|comprare|comprar)\b.{0,30}\bvinted\b"),
    re.compile(r"\b(?:payment|paiement|pagamento|pago|zahlung)\s+(?:outside|off|hors|fuori|fuera|fora|ausserhalb|por|em|en)\b"),
    re.compile(r"\b(?:cash|dinheiro|efectivo|efectivo|especie|barzahlung)\b"),
    re.compile(r"\b(?:hand\s*[- ]?to\s*[- ]?hand|remise\s+en\s+main\s+propre|ritiro\s+a\s+mano|consegna\s+a\s+mano|entrega\s+(?:em|en)\s+m[aã]o|abholung)\b"),
)


class VintedClient:
    """Small session client that keeps Vinted cookies and CSRF state together."""

    def __init__(self, requests_per_minute: int = DEFAULT_REQUESTS_PER_MINUTE) -> None:
        if requests_per_minute < 1:
            raise ValueError("requests_per_minute must be positive")
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.csrf_token: str | None = None
        self.csrf_refreshed_at = 0.0
        self.request_interval = 60.0 / requests_per_minute
        self.request_times: deque[float] = deque()

    def _wait_for_request_slot(self) -> None:
        now = time.monotonic()
        while self.request_times and now - self.request_times[0] >= 60:
            self.request_times.popleft()
        if self.request_times:
            wait_seconds = self.request_interval - (now - self.request_times[-1])
            if wait_seconds > 0:
                time.sleep(wait_seconds)
        now = time.monotonic()
        while self.request_times and now - self.request_times[0] >= 60:
            self.request_times.popleft()
        self.request_times.append(now)

    def _backoff_after_rate_limit(
        self, response: requests.Response, retry_number: int
    ) -> None:
        retry_after = response.headers.get("Retry-After")
        try:
            wait_seconds = float(retry_after) if retry_after else 5 * (2**retry_number)
        except ValueError:
            wait_seconds = 5 * (2**retry_number)
        wait_seconds = min(max(wait_seconds, 1.0), 90.0)
        print(
            f"Rate limited (HTTP 429); retrying in {wait_seconds:.0f}s "
            f"({retry_number}/{MAX_RATE_LIMIT_RETRIES})...",
            file=sys.stderr,
        )
        time.sleep(wait_seconds)

    def refresh_csrf(self) -> None:
        self._wait_for_request_slot()
        response = self.session.get(DEFAULT_SEARCH_URL, timeout=30)
        response.raise_for_status()
        match = CSRF_TOKEN_PATTERN.search(response.text)
        if not match:
            raise RuntimeError("Vinted catalog did not provide a CSRF token")
        self.csrf_token = match.group(1)
        self.csrf_refreshed_at = time.monotonic()

    def get_catalog_page(self, params: dict[str, Any]) -> requests.Response:
        token_expired = (
            time.monotonic() - self.csrf_refreshed_at >= CSRF_REFRESH_SECONDS
        )
        if self.csrf_token is None or token_expired:
            self.refresh_csrf()

        auth_retried = False
        rate_limit_retries = 0
        while True:
            self._wait_for_request_slot()
            response = self.session.get(
                CATALOG_ENDPOINT,
                params=params,
                headers={"X-CSRF-Token": self.csrf_token or ""},
                timeout=30,
            )
            if response.status_code == 429 and rate_limit_retries < MAX_RATE_LIMIT_RETRIES:
                rate_limit_retries += 1
                self._backoff_after_rate_limit(response, rate_limit_retries)
                continue
            if response.status_code not in (401, 403) or auth_retried:
                return response
            if not auth_retried:
                self.refresh_csrf()
                auth_retried = True

    def get_item_page(self, url: str) -> requests.Response:
        detail_url = urljoin("https://www.vinted.pt", url)
        auth_retried = False
        rate_limit_retries = 0
        while True:
            self._wait_for_request_slot()
            response = self.session.get(detail_url, headers={
                "Accept": "text/html,application/xhtml+xml",
            }, timeout=30)
            if response.status_code == 429 and rate_limit_retries < MAX_RATE_LIMIT_RETRIES:
                rate_limit_retries += 1
                self._backoff_after_rate_limit(response, rate_limit_retries)
                continue
            if response.status_code not in (401, 403) or auth_retried:
                return response
            if not auth_retried:
                auth_retried = True
                self.refresh_csrf()

    def get_user(self, user_id: int) -> requests.Response:
        """Fetch current public seller metrics from Vinted's user endpoint."""
        auth_retried = False
        rate_limit_retries = 0
        while True:
            self._wait_for_request_slot()
            response = self.session.get(
                f"https://www.vinted.pt/api/v2/users/{user_id}",
                headers={"X-CSRF-Token": self.csrf_token or ""},
                timeout=30,
            )
            if response.status_code == 429 and rate_limit_retries < MAX_RATE_LIMIT_RETRIES:
                rate_limit_retries += 1
                self._backoff_after_rate_limit(response, rate_limit_retries)
                continue
            if response.status_code not in (401, 403) or auth_retried:
                return response
            self.refresh_csrf()
            auth_retried = True
        


def fetch_page(
    client: VintedClient,
    *,
    search_text: str,
    price_from: float,
    price_to: float,
    page: int,
    per_page: int,
) -> dict[str, Any]:
    response = client.get_catalog_page(
        {
            "search_text": search_text,
            "price_from": f"{price_from:.2f}",
            "price_to": f"{price_to:.2f}",
            "currency": "EUR",
            "order": "newest_first",
            "page": page,
            "per_page": per_page,
        }
    )
    if response.status_code in (401, 403):
        raise RuntimeError(
            f"Vinted rejected the session (HTTP {response.status_code}) "
            "after refreshing the CSRF token and cookies."
        )
    if response.status_code == 429:
        raise RuntimeError("Vinted rate-limited the request (HTTP 429)")
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("Vinted returned non-JSON content") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise RuntimeError("Unexpected Vinted catalog response shape")
    return payload


def normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    price = item.get("price") or {}
    item_id = item.get("id")
    user = item.get("user") if isinstance(item.get("user"), dict) else {}
    return {
        "id": item_id,
        "title": item.get("title"),
        "price_eur": price.get("amount") if isinstance(price, dict) else price,
        "brand": item.get("brand_title"),
        "size": item.get("size_title"),
        "status": item.get("status"),
        "url": item.get("url") or f"https://www.vinted.pt/items/{item_id}",
        "seller": {
            "seller_id": user.get("id"),
            "seller_name": user.get("login"),
            "profile_url": user.get("profile_url"),
        },
    }


def extract_description(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    description = soup.find("meta", attrs={"name": "description"})
    return str(description.get("content", "")) if description else ""


def scam_reasons(description: str) -> list[str]:
    normalized = normalize_text(description)
    return [pattern.pattern for pattern in SCAM_PATTERNS if pattern.search(normalized)]


def normalize_text(value: str) -> str:
    """Make seller text comparable across accents, casing, and punctuation."""
    decomposed = unicodedata.normalize("NFKD", value)
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", without_accents.casefold().replace("’", "'")).strip()


def is_ps5_listing(item: dict[str, Any]) -> bool:
    text = f"{item.get('title', '')} {item.get('description', '')}"
    return bool(PS5_PATTERN.search(normalize_text(text)))


def scrape(
    *,
    search_text: str = "playstation 5",
    price_from: float = 280.0,
    price_to: float = 400.0,
    pages: int = 1,
    per_page: int = 96,
    delay_seconds: float = 1.0,
    detail_delay_seconds: float = 0.35,
    requests_per_minute: int = DEFAULT_REQUESTS_PER_MINUTE,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    client = VintedClient(requests_per_minute=requests_per_minute)
    for page in range(1, pages + 1):
        payload = fetch_page(
            client,
            search_text=search_text,
            price_from=price_from,
            price_to=price_to,
            page=page,
            per_page=per_page,
        )
        print(
            f"Catalog page {page}: {len(payload['items'])} candidates found",
            file=sys.stderr,
        )
        results.extend(normalize_item(item) for item in payload["items"])
        if len(payload["items"]) < per_page:
            break
        if page < pages:
            time.sleep(delay_seconds)

    safe_results: list[dict[str, Any]] = []
    print(f"Checking {len(results)} catalog candidates...", file=sys.stderr)
    for index, item in enumerate(results, 1):
        prefix = f"[{index}/{len(results)}] {item.get('title', 'Untitled')}"
        try:
            price = float(item["price_eur"])
        except (TypeError, ValueError):
            print(f"{prefix} -> skipped (invalid price)", file=sys.stderr)
            continue
        if not price_from <= price <= price_to or not is_ps5_listing(item):
            print(f"{prefix} -> skipped (price or PS5 filter)", file=sys.stderr)
            continue
        detail_response = client.get_item_page(item["url"])
        detail_response.raise_for_status()
        item["description"] = extract_description(detail_response.text)
        reasons = scam_reasons(item["description"])
        if reasons:
            print(f"{prefix} -> filtered (off-platform/wanted wording)", file=sys.stderr)
        else:
            safe_results.append(item)
            print(f"{prefix} -> accepted (€{price:.2f})", file=sys.stderr)
        if index < len(results):
            time.sleep(detail_delay_seconds)
    return safe_results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--per-page", type=int, default=96)
    parser.add_argument("--price-from", type=float, default=280.0)
    parser.add_argument("--price-to", type=float, default=400.0)
    parser.add_argument(
        "--requests-per-minute",
        type=int,
        default=DEFAULT_REQUESTS_PER_MINUTE,
        help=f"maximum Vinted requests per minute (default: {DEFAULT_REQUESTS_PER_MINUTE})",
    )
    parser.add_argument("--json", action="store_true", help="print JSON output")
    args = parser.parse_args()
    if args.pages < 1 or not 1 <= args.per_page <= 96 or args.requests_per_minute < 1:
        parser.error("--pages must be positive, --per-page 1-96, and --requests-per-minute positive")

    try:
        items = scrape(
            pages=args.pages,
            per_page=args.per_page,
            price_from=args.price_from,
            price_to=args.price_to,
            requests_per_minute=args.requests_per_minute,
        )
    except (requests.RequestException, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
    else:
        for item in items:
            print(f"{item['price_eur']} EUR | {item['title']} | {item['url']}")
        print(f"\nFound {len(items)} items.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
