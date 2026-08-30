# Execution Tickets: Local PS5 Deal Monitor

## F-1 Add configuration and SQLite notification state

**Phase**: Foundation
**Blocked by**: None
**Estimated scope**: M

**Description**:
Create the importable one-shot monitor module with validated environment configuration and a minimal SQLite repository for notified listing IDs. Establish the standard-library unit-test structure used by later tickets.

**Requirements**:

1. Load `NTFY_TOPIC`, `MIN_PRICE_EUR`, `MAX_PRICE_EUR`, `MAX_DISTANCE_KM`, and `STATE_DB_PATH` with the PRD defaults.
2. Reject non-finite or negative numeric values and reject a minimum price that is not lower than the maximum.
3. Create the `notified_listings` table on first use with `listing_id` as its primary key and a non-null UTC `notified_at` value.
4. Read notified IDs and persist a collection of newly notified IDs in one transaction.
5. Fail without recreating or deleting an unreadable or corrupt database.
6. Ignore `.env`, SQLite database files, SQLite sidecar files, Python caches, and macOS metadata without changing unrelated ignore behavior.

**Technical Notes**:

- Keep the implementation in an importable `ps5_monitor.py` script to match the repository's small standalone-script style while allowing direct unit tests.
- Use `os.environ`, `sqlite3`, `dataclasses`, and `unittest`; do not add a framework or ORM.
- Follow the state model and configuration table in `plan/PRD.md`.

**Acceptance Criteria**:

- [ ] Default and overridden configuration values are covered by unit tests.
- [ ] Invalid numeric configuration fails before external work and is covered by unit tests.
- [ ] SQLite schema creation, persistence across reopened connections, idempotent listing IDs, and corrupt-database failure are covered by unit tests.
- [ ] `python3 -m unittest discover -s tests -v` passes.

**Files likely affected**:

- `ps5_monitor.py`
- `tests/test_ps5_monitor.py`
- `.gitignore`

## CB-1 Implement strict listing classification, distance, and ranking

**Phase**: Core Backend
**Blocked by**: F-1
**Estimated scope**: M

**Description**:
Add pure domain logic that converts parsed OLX listing values into qualifying deal candidates. Apply strict PS5 Slim Disc classification, price and distance rules, deterministic ranking, and unseen-ID filtering without performing network or database I/O inside the classification functions.

**Requirements**:

1. Normalize case, whitespace, and Portuguese diacritics across title and description.
2. Require a PS5 identity term, a Slim term, and a disc-edition term.
3. Reject digital-only, accessory/game-only, wanted, repair/damaged, and parts indicators.
4. Require EUR, active status, `MIN_PRICE_EUR <= price < MAX_PRICE_EUR`, and Haversine distance at most `MAX_DISTANCE_KM` from Penafiel.
5. Skip listings with missing or malformed required values and expose a warning message for each skip.
6. Exclude notified IDs and rank remaining matches by price, distance, and listing ID.

**Technical Notes**:

- Use an immutable listing/deal value object and pure helper functions in `ps5_monitor.py`.
- Use a fixed, documented Penafiel reference coordinate and the Haversine formula described in `plan/research.md`.
- Strict matching should prefer false negatives over false positives.

**Acceptance Criteria**:

- [ ] Positive PS5 Slim Disc wording variants, including Portuguese text with diacritics, are covered by unit tests.
- [ ] Every rejection category has at least one focused unit test.
- [ ] Inclusive/exclusive price boundaries and the inclusive distance boundary are covered by unit tests.
- [ ] Missing data, notified-ID exclusion, and deterministic ordering are covered by unit tests.
- [ ] `python3 -m unittest discover -s tests -v` passes.

**Files likely affected**:

- `ps5_monitor.py`
- `tests/test_ps5_monitor.py`

## I-1 Add the anonymous OLX GraphQL client and response parser

**Phase**: Integration
**Blocked by**: CB-1
**Estimated scope**: M

**Description**:
Retrieve the newest OLX search results with the verified minimal anonymous GraphQL request and convert a valid response into the domain values consumed by classification. Keep seller, account, contact, cookie, and authentication data out of both the query and logs.

**Requirements**:

1. Send one JSON `POST` to `https://www.olx.pt/apigateway/graphql` with explicit timeout, JSON content type, stable user agent, and no authentication or cookies.
2. Request 40 results per page with successive offsets until the configured unique-result limit, using the configured price bounds and deduplicating listing IDs.
3. Limit the GraphQL selection set to the fields required by the PRD.
4. Accept only HTTP `200`, valid JSON without top-level GraphQL errors, `ListingSuccess`, and a list-valued listing collection.
5. Parse price and other typed parameters by key and GraphQL typename.
6. Skip and warn for malformed individual listings while treating a malformed response envelope as a fatal retrieval error.

**Technical Notes**:

- Use the existing `requests` dependency and dependency injection or mocks for tests; automated tests must not contact OLX.
- Follow the exact operation, variables, and response paths documented in `plan/research.md`.
- Do not reuse any content from the credential-bearing captured curl except the public query schema.

**Acceptance Criteria**:

- [ ] Representative single-page and paginated `ListingSuccess` responses parse and deduplicate correctly in unit tests.
- [ ] Tests assert that the request contains no authorization or cookie headers and excludes seller/contact fields.
- [ ] Timeout, non-`200`, invalid JSON, GraphQL errors, `ListingError`, and malformed collection cases fail closed in unit tests.
- [ ] One malformed item is skipped without discarding valid siblings.
- [ ] `python3 -m unittest discover -s tests -v` passes without live network access.

**Files likely affected**:

- `ps5_monitor.py`
- `tests/test_ps5_monitor.py`

## I-2 Add ntfy delivery, one-shot orchestration, and server instructions

**Phase**: Integration
**Blocked by**: I-1
**Estimated scope**: L

**Description**:
Complete the one-shot command by combining configuration, SQLite state, OLX retrieval, filtering, ranking, ntfy publishing, and process exit behavior. Document local setup and a non-overlapping 15-minute server schedule.

**Requirements**:

1. Build one ntfy JSON request containing at most three deals with title, price, approximate distance, locality, URL, high priority, Markdown, and one view action per deal.
2. Do not call ntfy when there are no qualifying unseen matches.
3. Persist only the IDs included in a successful ntfy response, after publication succeeds, in one SQLite transaction.
4. Leave excess unseen matches eligible for subsequent runs.
5. Write concise success summaries to stdout; write warnings and operational failures to stderr.
6. Exit `0` after a completed check, including no-match checks, and exit non-zero after configuration, SQLite, OLX, or ntfy failures.
7. Document installation, configuration, manual execution, SQLite state location, public-topic warning, tests, and a scheduler example that prevents overlapping runs.

**Technical Notes**:

- Publish JSON to `https://ntfy.sh/` through the existing `requests` dependency with an explicit timeout.
- Keep external clients injectable or mockable so orchestration and exit behavior are fully testable without network access.
- Preserve at-least-once semantics: successful ntfy publication followed by a failed SQLite write can produce a later duplicate and must exit non-zero.

**Acceptance Criteria**:

- [ ] ntfy payload formatting and the three-item limit are covered by unit tests.
- [ ] No-match, all-notified, excess-match, first-run, ntfy-failure, and post-publish SQLite-failure flows are covered by unit tests.
- [ ] Tests prove IDs are absent before successful publication and present afterward.
- [ ] CLI success and error exit paths are covered without live network calls.
- [ ] README instructions are sufficient to install, test, run once, and schedule the monitor every 15 minutes without overlap.
- [ ] `python3 -m unittest discover -s tests -v` passes.

**Files likely affected**:

- `ps5_monitor.py`
- `tests/test_ps5_monitor.py`
- `README.md`
- `requirements.txt`

## Dependency Graph

### Critical Path

F-1 → CB-1 → I-1 → I-2 → Done

### Parallelizable Work

None. Each ticket extends the same small script and test module, so serial execution avoids overlapping edits.

### Blocking Relationships

| Ticket | Blocked By |
|---|---|
| F-1 | None |
| CB-1 | F-1 |
| I-1 | CB-1 |
| I-2 | I-1 |

## Effort Summary

- Total tickets: 4
- Foundation: 1
- Core Backend: 1
- Integration: 2
- Estimated agent sessions: 4
- Critical path length: 4 tickets
