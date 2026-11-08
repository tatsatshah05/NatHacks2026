# ADR 0004: No user accounts in version 1

- **Status:** Accepted
- **Date:** November 7, 2026
- **Recorded in:** [PLAN.md](../../PLAN.md) section 16 ("Decided: version 1 has no user accounts") and section 5 (accounts are listed under "Won't"); [AGENTS.md](../../AGENTS.md#decisions-already-made); pull request [#82](https://github.com/tatsatshah05/NatHacks2026/pull/82), which removed the empty login placeholder files

## Context

Saved routes, push alerts and the per-device report limit all need to know which browser a request comes from. The sources record the decision itself; the only reason they give is scope, because PLAN.md section 5 lists user accounts under "Won't (this weekend)".

## Decision

Version 1 has no accounts. Settings and saved routes are tied to the device, and push alerts use an anonymous id.

## Consequences

- The browser generates an anonymous `client_id` (16 to 64 URL-safe characters) and keeps it in `localStorage` as `openpath.client_id` (`web/src/lib/client-id.ts`). It is not tied to a name, an email address or a phone number.
- The server keeps saved routes and push subscriptions per `client_id`: at most 10 saved routes and 5 push subscriptions per client (`cloud/app/saved_routes.py`, `cloud/app/push.py`). Whoever holds the id can list and delete that browser's routes (PLAN.md section 16, the OP-304 decision).
- Accessibility settings (`openpath.settings`, `openpath.speak`) never leave the device.
- If a person clears the site's data before removing their saved routes, they can no longer remove them themselves. The privacy page tells people to remove routes first.
- An app added to the iPhone home screen keeps its own storage, so it has its own id and its own saved routes.
- The API has no unsubscribe endpoint. A subscription is dropped when the push service answers 404 or 410, or after 5 failed deliveries in a row.
