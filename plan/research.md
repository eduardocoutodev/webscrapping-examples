# Research: OLX PS5 Monitoring and ntfy Publishing

> Researched on 2026-08-30 against the live OLX Portugal site, the public page's GraphQL request, OLX `robots.txt`, ntfy documentation, Requests documentation, and Playwright Python documentation.

## TL;DR

The public search page obtains listings from `POST https://www.olx.pt/apigateway/graphql` with the `ListingSearchQuery` operation. On 2026-08-30, a reduced query returned `200` and current listings without authentication, cookies, the captured `sl` value, `Origin`, or `x-client`; only JSON content headers and a normal user agent were needed. The response includes listing IDs, descriptions, prices, URLs, locality names, and approximate `map.lat`/`map.lon` coordinates, so a small `requests`-based monitor can retrieve and distance-filter results without Playwright or Nominatim. The endpoint is undocumented and may change, so the implementation must fail closed and keep the public-page Playwright path as a documented fallback. ntfy accepts a small JSON POST, but the requested topic name is public and guessable.

## OLX Retrieval

### Access behavior

- An earlier capture targeted `GET https://www.olx.pt/api/v1/offers/metadata/filters/`, which returned `403` and is not the listings transport used by the current page.
- The new capture targets `POST https://www.olx.pt/apigateway/graphql` and contains a complete `ListingSearchQuery` document.
- A minimized anonymous request to that endpoint returned `200` with live listing data on 2026-08-30.
- Authentication was not required. The verified request omitted the OLX access token, all cookies, `sl`, `Origin`, `Referer`, and `x-client`.
- The pasted browser capture contains account cookies and an identity-bearing token. They are unnecessary and must not be copied into source, configuration, logs, fixtures, or tests.
- A plain request to the rendered public search URL still returned `403`, while Chromium loaded it successfully. This does not prevent direct anonymous use of the GraphQL transport.
- OLX's current `robots.txt` disallows `/api/`; `/apigateway/graphql` is not explicitly matched by that rule. This observation is not a stability guarantee or an official API contract.

### Anonymous GraphQL listings query

Endpoint:

```text
POST https://www.olx.pt/apigateway/graphql
Content-Type: application/json
```

The JSON body contains:

- `query`: a GraphQL document named `ListingSearchQuery`.
- `variables.searchParameters`: a list of `{key, value}` strings.

Verified search parameters:

| Key | Value for this monitor |
|---|---|
| `offset` | `0` |
| `limit` | `40` |
| `query` | `playstation 5` |
| `sort_by` | `created_at:desc` |
| `filter_float_price:from` | `200` |
| `filter_float_price:to` | `350` |

The captured `suggest_filters=true` and `sl=<browser value>` parameters are not needed for listing retrieval and should be omitted. The query selection set should also omit seller contact, account, photo, promotion, delivery, and filter-facet fields because the monitor does not use them.

Required response fields:

| Purpose | Response path |
|---|---|
| Result type | `data.clientCompatibleListings.__typename` |
| Listings | `data.clientCompatibleListings.data[]` |
| Stable ID | `data[].id` |
| Classification text | `data[].title`, `data[].description` |
| URL | `data[].url` |
| Recency/status | `data[].created_time`, `data[].status` |
| Locality | `data[].location.city.name`, `data[].location.region.name` |
| Approximate coordinates | `data[].map.lat`, `data[].map.lon` |
| Location precision hints | `data[].map.radius`, `data[].map.show_detailed` |
| Price/model/state | `data[].params[]`, selected by each entry's `key` |

`params` is polymorphic. For `key == "price"`, select the `PriceParam` fragment and read numeric `value`, `currency`, `negotiable`, and `arranged`. The verified model and condition entries use `GenericParam`, with values such as `playstation5` / `PlayStation 5` and `used` / `Usado`.

A successful HTTP response can still represent either GraphQL-level `errors` or the `ListingError` union member. The monitor should accept only HTTP `200`, no top-level `errors`, `__typename == "ListingSuccess"`, and a list-valued `data` field. Every other shape is a failed check, and notified state must remain unchanged.

### Public search page

Search URL:

```text
https://www.olx.pt/ads/q-playstation-5/?search%5Border%5D=created_at%3Adesc&search%5Bfilter_float_price%3Afrom%5D=200&search%5Bfilter_float_price%3Ato%5D=350
```

Verified rendered selectors:

| Field | Selector |
|---|---|
| Results container | `[data-testid="listing-grid"]` |
| One result | `[data-testid="l-card"]` |
| Title and URL | `[data-testid="card-title-link"]` |
| Price | `[data-testid="ad-price"]` |
| Locality and date | `[data-testid="location-date"]` |
| Total count | `[data-testid="total-count"]` |

The search is noisy. Current results include PS4 consoles, games, VR headsets, steering wheels, rooms, and other products merely mentioning PS5. Search terms alone are not sufficient; local classification is required.

### Public detail page

Each detail page exposes a schema.org `Product` object in:

```text
script[type="application/ld+json"]
```

Verified fields:

- `name`: listing title.
- `description`: seller-provided description.
- `sku`: numeric OLX listing ID.
- `url`: canonical listing URL.
- `offers.price`: numeric asking price.
- `offers.priceCurrency`: `EUR`.
- `offers.itemCondition`: schema.org condition URL.
- `offers.areaServed.name`: locality name.

The rendered page also exposes human-readable fields such as `Estado: Usado`, `Modelo: PlayStation 5`, and the district. No coordinates were found in the public JSON-LD, but the search GraphQL response provides the approximate coordinates needed by the monitor.

### Classification implications

The detail title and description should be normalized to lowercase and diacritics removed. A strict candidate needs all of:

- A PS5 identity term: `ps5`, `playstation 5`, or `play station 5`.
- A Slim term: `slim`.
- A disc-edition term such as `disco`, `disc`, `leitor`, `standard`, or an explicit statement that games on disc are supported.
- No digital-only term such as `digital`, `sem disco`, `sem leitor`, or `digital edition`.
- No accessory/parts/wanted indicators such as controller-only, cover, stand, headset, VR, steering wheel, game-only, damaged/repair, `procuro`, or `compro`.

Text classification cannot be perfect. Strict matching favors missing an ambiguously described deal over notifying a false positive, which matches the requested behavior.

### Error handling

- Set explicit connect/read timeouts on the GraphQL POST.
- Treat timeouts, non-`200` responses, invalid JSON, top-level GraphQL errors, `ListingError`, or a malformed listing collection as a failed check.
- Do not update notified state after a failed retrieval or failed ntfy publish.
- Do not attempt to bypass access-denied responses, CAPTCHAs, or other access controls.
- One request every 15 minutes from the server-owned scheduler is conservative; the script should perform one check and exit rather than maintaining its own infinite polling loop.
- If the Playwright fallback is added later, wait for the results container rather than using a fixed sleep, and always close the browser in a `finally` block.

## Python HTTP Client

The repository already depends on `requests`, which is sufficient for the anonymous JSON POST and ntfy publishing. Use an explicit request timeout and send a stable user agent. Do not add Playwright or a browser installation unless the GraphQL transport stops working and the fallback is deliberately activated.

## Distance Calculation

### OLX approximate coordinates

The anonymous listings response contains `map.lat`, `map.lon`, `map.radius`, `map.show_detailed`, and `map.zoom`. Verified results used `show_detailed: false` and small non-zero radius values, so these coordinates represent an approximate listing area rather than a seller's precise position.

Compute straight-line distance from a fixed Penafiel coordinate with the Haversine formula. This is neither driving distance nor exact seller distance. Listings with missing or non-numeric coordinates should be skipped rather than geocoded through a second external service; the available response makes Nominatim unnecessary and avoids its recurring-job policy constraints.

## ntfy Publishing

### Endpoint and request

ntfy accepts `POST https://ntfy.sh/` with a JSON body. The fields relevant here are:

- `topic`: requested topic name.
- `title`: short deal summary.
- `message`: multi-line listing details.
- `priority`: integer 1 through 5.
- `tags`: strings that may render as emojis.
- `markdown`: enables Markdown formatting.
- `click`: URL opened when the notification is selected.
- `actions`: optional view buttons for individual listings.

A single JSON request can contain the top three deals in the message and up to three `view` actions, one per listing. Priority `4` (`high`) is suitable for a confirmed buy-trigger match.

### Topic privacy

On the public `ntfy.sh` service, unreserved topic names are public. ntfy states that the topic name effectively acts as a password and should be hard to guess. `eduardo_notifications` is easy to guess, so anyone who knows it can read or publish messages unless the topic is reserved with access controls. Listing titles, prices, approximate locations, and OLX URLs sent there should be considered public.

### Delivery state

Only mark listing IDs as notified in SQLite after ntfy returns success. If ntfy fails, retain them as unseen so the next check retries. Retain notified IDs indefinitely; expected volume is small and deletion could re-enable duplicate notifications.

## Suggested Configuration

No OLX token, login, cookie, or browser profile is required. Configuration can remain small:

```text
NTFY_TOPIC=eduardo_notifications
MAX_PRICE_EUR=300
MIN_PRICE_EUR=200
MAX_DISTANCE_KM=80
```

The server scheduler owns the 15-minute interval, so the first implementation does not need `POLL_MINUTES` or `HEADLESS`. The topic is not a secret, but `.env` and local state files should still be git-ignored. No seller names, phone numbers, account data, cookies, OLX credentials, or location cache should be collected.

## Alternatives Considered

### OLX `/api/v1` endpoints

Advantages: structured JSON. Disadvantages: explicitly disallowed by OLX `robots.txt`, returned `403` in testing, and are unnecessary because the public page's GraphQL transport supplies the required fields. They should not be used.

### Anonymous GraphQL transport

Advantages: one small JSON request, structured fields, approximate coordinates, no account, and no browser runtime. Disadvantages: undocumented schema, no published rate-limit contract, and possible anti-automation or schema changes. This is the recommended first implementation for a single-user 15-minute poller, with strict failure handling and a minimal selection set.

### Plain HTTP HTML parsing

Advantages: small dependency footprint. Disadvantages: the edge currently rejects non-browser requests and the page is JavaScript-driven. It is not viable in the tested environment.

### Playwright public-page parsing

Advantages: uses the rendered public pages, matches normal browser behavior, exposes stable test IDs, and needs no account. Disadvantages: Chromium is a large dependency and rendered selectors may change. Keep it as a fallback if the undocumented GraphQL transport stops working; it is no longer required for the first implementation.

### Nominatim or offline coordinates

Both add dependencies and operational complexity that are no longer justified because OLX supplies approximate coordinates directly. Reconsider them only if future GraphQL results routinely omit `map.lat` or `map.lon`.

## References

- OLX Portugal robots rules: https://www.olx.pt/robots.txt
- OLX search page: https://www.olx.pt/ads/q-playstation-5/
- OLX search transport observed from the public page: https://www.olx.pt/apigateway/graphql
- ntfy publishing API: https://docs.ntfy.sh/publish/
- ntfy Terms of Service: https://docs.ntfy.sh/terms/
- Requests documentation: https://requests.readthedocs.io/
- Playwright Python library (fallback): https://playwright.dev/python/docs/library
