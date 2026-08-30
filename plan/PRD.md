# PRD: Local PS5 Deal Monitor

## Metadata

- **Author**: Eduardo Couto
- **Date**: 2026-08-30
- **Status**: Approved
- **Stack**: Standalone Python 3, Requests, SQLite

## 1. Overview

Build a one-shot Python monitor that finds credible used PlayStation 5 Slim Disc listings on OLX Portugal. A server-owned scheduler will invoke it every 15 minutes. Each invocation retrieves recent listings anonymously through the public search page's GraphQL transport, filters and ranks them, publishes at most three unseen matches to ntfy, records successfully notified OLX IDs in SQLite, and exits.

A qualifying listing costs at least €200 and less than €300, is approximately within 80 km of Penafiel, and contains enough text to identify a PS5 Slim with a disc drive while rejecting digital-only consoles, accessories, games, wanted ads, repair listings, and parts. The monitor is intentionally strict: an ambiguous listing is skipped rather than notified.

The selected ntfy topic is `eduardo_notifications`. The owner accepts that this guessable topic and its listing information are public unless separately protected through ntfy.

## 2. Goals and Success Criteria

- A scheduled run retrieves OLX listings without an OLX account, token, cookie, browser, or seller personal data.
- A run publishes one ntfy message containing no more than the three best unseen qualifying listings, ordered by price and then approximate distance.
- A run with no qualifying unseen listings publishes nothing and exits successfully.
- Successfully notified listing IDs survive process and server restarts in a local SQLite database.
- Retrieval, configuration, state, or publishing failures are visible on stderr and produce a non-zero exit status without incorrectly marking listings as notified.

### Non-goals

- Contacting sellers, negotiating, purchasing, or reserving listings.
- A graphical interface, web service, or multi-user deployment.
- Exact driving distance or seller location.
- Monitoring other PS5 models or other marketplaces.
- Collecting seller names, phone numbers, account details, OLX credentials, or cookies.
- Automatically bypassing OLX access controls or adding a browser fallback in the first release.

## 3. User Stories

### US-1: Run the monitor from a scheduler

**As the server owner, I want** one command to perform one complete check and exit **so that** cron, systemd, or an equivalent scheduler owns the 15-minute interval.

**Acceptance Criteria:**

1. Given valid configuration and an available SQLite database, when the command starts, it performs one retrieval-and-notification cycle and terminates.
2. Given a successful cycle with no unseen matches, when the command terminates, it exits with status `0` and no ntfy request was made.
3. Given invalid configuration or a failed external dependency, when the command terminates, it writes a useful error to stderr and exits non-zero.

### US-2: Identify credible deals

**As a prospective buyer, I want** only credible nearby PS5 Slim Disc listings below €300 **so that** irrelevant OLX results do not create notification noise.

**Acceptance Criteria:**

1. A candidate is accepted only when `200 <= price < 300`, the currency is EUR, its approximate Haversine distance from Penafiel is at most 80 km, and its status is active.
2. The normalized title and description must contain a PS5 identity term, a Slim term, and a disc-edition term.
3. A candidate is rejected when its text indicates digital-only, an accessory or game without a console, a wanted ad, repair/damage, or parts.
4. A listing with missing or malformed required fields is skipped and reported as a warning without failing other valid listings in the same response.

### US-3: Receive concise ntfy notifications

**As a prospective buyer, I want** the best unseen deals in one readable notification **so that** I can open the listings quickly.

**Acceptance Criteria:**

1. On the first run with matches, the notification contains the best three existing matches.
2. Matches are ordered by lowest price, then shortest approximate distance, with listing ID as a deterministic final tie-breaker.
3. The notification contains each listing's title, price, approximate distance, locality, and OLX URL, plus one view action per included listing.
4. When more than three unseen matches exist, only the three published listings are marked notified; remaining matches stay eligible for later runs while OLX continues returning them.
5. When every qualifying result has already been notified, no ntfy request is made.

### US-4: Prevent repeat notifications across runs

**As the server owner, I want** successful notifications recorded in SQLite **so that** restarts do not repeat already delivered listings.

**Acceptance Criteria:**

1. The first successful startup creates the required SQLite schema automatically.
2. A listing ID has at most one notified record.
3. Included listing IDs are persisted only after ntfy confirms successful publication.
4. An unreadable, corrupt, or incompatible state database causes a non-zero exit; the monitor does not reset it automatically or publish a notification.

## 4. Technical Design

### 4.1 Architecture

Each invocation follows this flow:

1. Load and validate environment configuration.
2. Open the local SQLite state database and ensure its schema exists.
3. Send one anonymous GraphQL search request to OLX.
4. Validate the response, parse listings, apply strict classification and numeric filters, and exclude notified IDs.
5. Rank unseen matches and select at most three.
6. If the selection is empty, log a short success result and exit `0` without contacting ntfy.
7. Publish one ntfy message for the selected listings.
8. After successful publication, store the selected IDs in one SQLite transaction and exit `0`.

The process is single-user and is expected to have at most one active invocation. External request timeouts must be far shorter than the 15-minute schedule interval so ordinary scheduled runs do not overlap.

### 4.2 Configuration

| Name | Required | Default | Meaning |
|---|---|---|---|
| `NTFY_TOPIC` | No | `eduardo_notifications` | Public ntfy topic used for deal notifications |
| `MIN_PRICE_EUR` | No | `200` | Inclusive minimum listing price |
| `MAX_PRICE_EUR` | No | `300` | Exclusive maximum listing price |
| `MAX_DISTANCE_KM` | No | `80` | Inclusive approximate straight-line distance |
| `STATE_DB_PATH` | No | Local application data path | SQLite state database location |

Numeric configuration must be finite and non-negative, and the minimum price must be lower than the maximum price. Configuration errors fail before any external request.

### 4.3 Data Model

SQLite stores one table of successful deliveries:

| Field | Type | Constraint | Purpose |
|---|---|---|---|
| `listing_id` | Text | Primary key | Stable OLX listing ID |
| `notified_at` | UTC timestamp text | Not null | Delivery audit timestamp |

No seller details, listing descriptions, URLs, coordinates, cookies, or credentials are persisted. Records are retained indefinitely because expected volume is small and removing them could re-enable duplicate notifications.

### 4.4 External Contracts

#### OLX listings

- Method: `POST`
- Endpoint: `https://www.olx.pt/apigateway/graphql`
- Authentication: none
- Search: newest `playstation 5` listings in the €200–€350 server-side range
- Required fields: ID, title, description, URL, creation time, status, locality, approximate map coordinates, and typed parameters for price/model/state

The client accepts only HTTP `200`, valid JSON without top-level GraphQL errors, the `ListingSuccess` union member, and a list-valued listing collection. The GraphQL selection set excludes seller contact and account fields.

#### ntfy

- Method: `POST`
- Endpoint: `https://ntfy.sh/`
- Authentication: none for the selected public topic
- Topic: configured by `NTFY_TOPIC`, defaulting to `eduardo_notifications`
- One successful request represents delivery of every listing included in that request

### 4.5 State and Delivery Semantics

Delivery is at least once. The monitor publishes before recording IDs so an ntfy failure remains retryable. If ntfy accepts the message but the subsequent SQLite transaction fails, the next run can repeat that notification; an external HTTP service and local SQLite cannot be committed atomically.

There are no database migrations or backfills for the first release. An absent database is created; an existing valid database is reused.

## 5. Edge Cases and Error Handling

| Scenario | Expected Behavior |
|---|---|
| No OLX listings or no qualifying unseen listings | Make no ntfy request, log the count, exit `0` |
| More than three unseen matches | Publish the best three; leave the rest eligible |
| OLX timeout, non-`200`, access denial, or invalid JSON | Write an error to stderr, do not publish or change state, exit non-zero |
| Top-level GraphQL errors, `ListingError`, or malformed collection | Write an error to stderr, do not publish or change state, exit non-zero |
| One malformed listing in an otherwise valid collection | Skip it, write a warning, continue with other listings |
| Missing or invalid coordinates | Skip the listing; do not call a geocoder |
| Ambiguous console edition | Skip the listing |
| ntfy timeout or non-success response | Write an error to stderr, do not change state, exit non-zero |
| SQLite cannot be opened, read, or initialized | Write an error to stderr, do not retrieve or publish, exit non-zero |
| SQLite write fails after ntfy success | Write an error to stderr and exit non-zero; a later run may repeat the notification |
| Two runs overlap unexpectedly | Deployment is unsupported; the server scheduler must prevent concurrent invocations |

## 6. Non-Functional Requirements

- **Performance:** One invocation should normally finish within 30 seconds; all external calls use explicit timeouts.
- **Scale:** One user, one request every 15 minutes, at most 40 OLX results evaluated per run, and at most three listings per notification.
- **Privacy:** Do not store or log OLX tokens, cookies, seller details, or precise personal information. The owner explicitly accepts the public `eduardo_notifications` topic.
- **Observability:** Log one concise summary to stdout on success. Send warnings and failures to stderr. Exit `0` only for completed checks, including checks with no matches.
- **Resilience:** Never update notified state after failed OLX retrieval or failed ntfy publication. Never silently recreate a corrupt database.
- **Rollback:** Stop the scheduler and revert the code. Preserve the SQLite database so a rollback does not cause duplicate notifications.
- **Compatibility:** Support a currently maintained Python 3 release on the owner's server.

## 7. Dependencies

- OLX Portugal's undocumented public-page GraphQL transport and schema.
- Public `ntfy.sh` publishing availability.
- Python 3 standard library, including SQLite.
- Requests for bounded HTTP calls.
- A server-owned scheduler configured for one run every 15 minutes without overlap.

## 8. Open Questions

None. The PRD is ready for approval and ticket generation.
