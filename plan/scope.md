# Scope: Local PS5 Deal Monitor

## Summary

Build a standalone Python monitor for OLX Portugal that finds credible PlayStation 5 Slim Disc listings priced from €200 up to, but not including, €300 and located within approximately 80 km of Penafiel. Every 15 minutes it should send one well-formatted ntfy notification containing at most the three best unseen matches.

## Classification

Small feature — estimated one implementation session after API research.

## Target Outcome

Running the monitor continuously produces a notification on the `eduardo_notifications` ntfy topic when new qualifying listings appear. Restarting it does not notify again for listing IDs already stored locally.

## Key Decisions

- Notification channel: ntfy topic `eduardo_notifications`.
- Ranking: lowest price first, then shortest approximate distance.
- Duplicate handling: persist notified OLX listing IDs in a local SQLite database.
- Product match: listing text must mention `PlayStation 5` or `PS5`, must indicate `Slim`, and must indicate a disc/standard model; obvious digital-only, accessory, wanted, repair, or parts listings are rejected.
- Distance: calculate approximate straight-line distance from Penafiel using `map.lat` and `map.lon` from the listings response; skip results without usable coordinates.
- Authentication: use the anonymous public-page GraphQL transport. Do not collect, store, or send OLX tokens or cookies.
- Scheduling: perform one check and exit; the server-owned scheduler invokes the script every 15 minutes.

## In Scope

- Query OLX for recent PlayStation 5 listings using the configured price range.
- Apply stricter local filters for price, model, listing type, and distance.
- Send the top three unseen qualifying results in one readable ntfy message.
- Support execution every 15 minutes by an external scheduler and retain notified IDs in SQLite across runs.
- Provide installation, configuration, and run instructions.

## Out of Scope

- Contacting sellers or purchasing automatically.
- A graphical interface or hosted service.
- Exact driving-distance routing; the 80 km check is approximate.
- Models other than PS5 Slim with a disc drive.
- Scraping seller personal details.

## Dependencies

- OLX Portugal's undocumented public-page GraphQL schema and availability.
- ntfy HTTP publish API.
- Python 3 and a small HTTP client dependency, if justified by research.

## Open Questions

- The verified anonymous GraphQL schema is undocumented and may change.
- OLX coordinates are approximate; results missing usable coordinates will be skipped.
- OLX rate-limit behavior is unknown; the monitor will use conservative polling.

## Recommended Next Step

Write the PRD and execution tickets from the verified research, then implement the standalone one-shot monitor.
