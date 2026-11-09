# Security and privacy (OP-401)

What OpenPath protects, how, what the dependency audits found, and what is left to do.
Written on November 8, 2026 for the Phase 4 review (issue #68). The privacy page
(`web/src/app/privacy/page.tsx`) is the user-facing version; this page is for the team.
When a store, a route or a header changes, update both in the same pull request.

## What is protected and how

### No accounts, one anonymous id

Version 1 has no accounts (decided November 7). The only identity is `client_id`: a
random, URL-safe string of 16 to 64 characters the browser generates and keeps
(`web/src/lib/client-id.ts`). It is the key to a browser's saved routes and push
subscriptions, so whoever holds it can list and delete them (`GET` and `DELETE
/api/saved-routes`). It is therefore never placed in a URL people share, and the
privacy page tells people to remove their routes before clearing the id. A report is
stored without it: the obstruction gets a random `source_ref`, and the id goes only
into the 24-hour rate-limit log.

### Keys on the keyed routes

| Route | Header | Check |
| --- | --- | --- |
| `POST /api/ingest/camera` | `X-Worker-Key` | `ingest_api.require_worker_key`: `secrets.compare_digest` on UTF-8 bytes (a non-ASCII header is a plain 401, never a 500). An unset or empty `WORKER_KEY` answers 503: the route is off, never open. |
| `POST /api/admin/override`, `DELETE /api/admin/override[/{source_ref}]` | `X-Admin-Key` | `admin_api.require_admin_key` (OP-406): the same pattern as the worker key (UTF-8 bytes, constant time; an unset or empty `ADMIN_KEY` answers 503, so the route is off, never open), followed by the keyed limit. Testing only: it stores one labelled test obstruction (`Test override: ...`, source `admin_override`, attribution `Test override, not real data`) that expires within an hour. Render generates `ADMIN_KEY`; the smoke tests never call it. |

`scripts/dev.py` (`make dev`) removes both keys and every other secret from the
environment, so a local run can never post to a live source.

### CORS allowlist

`CORS_ORIGINS` (comma-separated; `*` matches part of one host-name label for Vercel
previews) decides which web pages may read API responses in a browser. It is not a
security boundary: the data is public, the API never allows credentials (no cookies),
and writes are protected by the keys and the limits below. Allowed methods are `GET`,
`POST` and `DELETE`. The security headers middleware sits outside the CORS middleware,
so preflight answers carry the headers too (`test_security_headers.py`).

### Rate limits

Two layers. The per-`client_id` caps came with the stories; the per-connection limits
are OP-401's (`cloud/app/rate_limit.py`, a fixed 60 s window per client, in memory).

| Route | Per `client_id` | Per connection and minute |
| --- | --- | --- |
| `POST /api/reports` | 5 an hour (200 an hour in total), in the database, survives restarts | `RATE_LIMIT_PER_MIN` (default 30) |
| `POST /api/push/subscribe` | 5 subscriptions | same |
| `POST /api/push/test` | none (503 without VAPID keys) | same |
| `POST /api/saved-routes` | 10 saved routes | same |
| `DELETE /api/saved-routes/{id}` | only the owner's | same |
| `POST /api/ingest/camera` | n/a (the key) | 10 times `RATE_LIMIT_PER_MIN`, checked after the key, so a request without the key never uses the worker's share |
| every `GET`, `WS /ws` | none | never limited |

The client is the first address in `X-Forwarded-For` when the header is present
(Render's proxy puts the caller there), else the socket address. A caller can forge
that header, so the limit is protection against mistakes and casual floods, not against
a determined attacker. A refused request gets `429`, a `Retry-After` header (seconds
until the window ends) and one plain sentence. Invalid bodies count too, because the
dependency runs before validation. `RATE_LIMIT_PER_MIN=0` turns both limits off; the
tests do that in `cloud/tests/conftest.py` and turn them on only in
`test_rate_limit.py`. `test_every_write_route_is_keyed_or_limited` fails when a new
`POST` or `DELETE` route has neither a key nor the public limit.

### Request size and shape

`POST /api/reports` reads its body itself, capped at 7 MB, and parses multipart in
memory (`reports_api.parse_multipart`): at most 8 parts, 1 KB per text field, a 120
character note, a 5 MB JPEG or PNG. Starlette's form parser is never used, because it
spools uploads over 1 MB to a temporary file. Every JSON body is a Pydantic model with
`extra="forbid"`. A report outside Edmonton is refused.

### Security headers

**API** (`cloud/app/security.py`, on every HTTP response, including 404s, validation
errors and CORS preflights; WebSockets pass through untouched):

| Header | Value | Note |
| --- | --- | --- |
| `X-Content-Type-Options` | `nosniff` | |
| `Referrer-Policy` | `no-referrer` | |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=()` | |
| `Content-Security-Policy` | `default-src 'none'; frame-ancestors 'none'` | Not on `/docs` and `/redoc`, which load Swagger UI and ReDoc from a CDN. The exemption reads the raw `scope["path"]`, never `request.url` (see PYSEC-2026-161 below). |
| `X-Frame-Options` | `DENY` | |
| `Cache-Control` | `no-store` | Only on answers to `POST` and `DELETE`; reads keep what the route set. |
| `Strict-Transport-Security` | `max-age=31536000` | Only when the request came over HTTPS (`X-Forwarded-Proto: https` from Render, or a TLS socket), so a local `http://` run never sets it. |

A header a route set itself wins.

**Web** (`web/next.config.mjs`, `headers()` for every path):

| Header | Value |
| --- | --- |
| `Content-Security-Policy` | `default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https://tiles.openfreemap.org; font-src 'self'; connect-src 'self' https://tiles.openfreemap.org <API origin> <WebSocket origin>; worker-src 'self' blob:; child-src 'self' blob:; manifest-src 'self'` |
| `X-Content-Type-Options` | `nosniff` |
| `Referrer-Policy` | `no-referrer` |
| `Permissions-Policy` | `camera=(self), microphone=(), geolocation=(self)` (the camera guide, OP-418, opens the rear camera on this origin only; the frame goes to our API and is never stored) (the planner and the Report button offer "Use my location"; the report photo is a file input, which needs no camera permission) |
| `X-Frame-Options` | `DENY` |

The API and WebSocket origins come from `NEXT_PUBLIC_API_URL`,
`NEXT_PUBLIC_API_BASE_URL` and `NEXT_PUBLIC_WS_URL` at build time (Vercel sets them for
production and previews; CI sets localhost). `next dev` adds `'unsafe-eval'` and
`ws:`/`wss:` for source maps and reloads. MapLibre needs `blob:` workers and
`data:`/`blob:` images; tiles, glyphs and sprites are fetched from OpenFreeMap, so it
is in `connect-src` (and `img-src`). `next/font` serves Atkinson Hyperlegible from
this host. Vercel adds `Strict-Transport-Security` itself.

`script-src` allows inline scripts on purpose: in production Next.js writes each
page's data into inline `<script>` tags (`self.__next_f.push(...)`) whose content
differs per request, so they cannot be allowed by hash, and the only strict option is a
per-request nonce from a middleware, which would turn every static page into a
server-rendered one. The settings boot script in `layout.tsx` is a constant and could
be allowed by hash once a nonce covers the rest. The policy was checked in a headless
Chromium against the production build with the API in replay mode: every page loads
with no CSP violation and no console error, the maps draw (OpenFreeMap tiles answer),
the service worker registers and the push subscribe flow posts to the API (the
Playwright run is described in the OP-401 pull request).

### No stored images

- Report photos are parsed in memory, classified (the ONNX model on our server, and
  Claude through the Anthropic API) and dropped with the request. They never reach the
  disk, the database or a log. `cloud/tests/test_reports.py` and
  `cloud/tests/test_privacy_inventory.py` watch every file, temporary file and spool
  call during a report with a photo, scan every table for a copy, and check the logs.
- `test_privacy_inventory.py` also pins the seven tables and their columns
  (`obstruction`, `camera_reading`, `camera_incident`, `community_report_log`,
  `saved_route`, `source_count_daily`, `push_subscription`), refuses any column named
  or typed like image storage, and the
  session fixture in `cloud/tests/conftest.py` fails the run if a `.jpg`, `.png`,
  `.webp` or similar file appears anywhere under the repository while the tests run.
- The camera worker holds each frame in memory and posts a label, never pixels
  (`POST /api/ingest/camera` carries no image field). CI's hygiene job rejects images
  outside `web/public/`, `web/src/`, `docs/` and `vision/tests/fixtures/`.
- Third-party processing: report photos and camera frames are sent to the Anthropic
  API in memory for the Claude check. The privacy page says so, and so do
  `docs/DATA_SOURCES.md` and PLAN.md section 16.

### Secrets

Secrets live only in the Render, Vercel and GitHub environment settings
(`docs/DEPLOY.md`). `.env.example` lists every variable with a blank value, and
nothing loads a `.env` file. CI's hygiene job fails on any committed `.env` or
`secrets.h`, model weights or large files, scans every commit with gitleaks, and
reviews new dependencies for high-severity advisories. `make dev` sets its own local
values and removes every key from the environment.

### What the server logs

One line per state change (`CHANGE`, `SAVED`, `PUSH`, `RATE_LIMIT`, `WARN`) plus the
ASGI server's access log. The access log carries the request line, so a `client_id` in
a query string (`GET /api/saved-routes?client_id=`) and search terms appear there; the
privacy page says so. Photos, notes and push keys never go into a log; a report logs
only its size and the class a check saw. The rate-limit log line names the scope,
method and path, not the address.

## Dependency audits

Run on November 8, 2026 on commit `3967064` (the Phase 4 base; the OP-401 branch
changed no Python requirement and added one npm override).

### Python: `pip-audit` 2.10.1

| What was audited | Result |
| --- | --- |
| `cloud/requirements.txt` + `cloud/requirements-photo-check.txt`, resolved fresh (what Render installs: FastAPI 0.162.2, starlette 1.10.0, uvicorn 0.54.0) | No known vulnerabilities |
| `vision/requirements.txt`, resolved fresh | No known vulnerabilities |
| `cloud/requirements-dev.txt` (pytest, ruff), resolved fresh | No known vulnerabilities |
| The CI and `make setup` environment: every `requirements*.txt` installed one after another, 67 packages frozen | 1 package flagged: **starlette 0.52.1** (five advisories, below) |

CI and local development end up on FastAPI 0.128.8 and starlette 0.52.1 because
`cloud/requirements-weather.txt` (an OP-109 file) caps `fastapi<0.129`, while Render
resolves fresh and runs starlette 1.10.0. None of the five advisories applies to
OpenPath's code:

| Advisory | Fixed in | Why it does not apply here |
| --- | --- | --- |
| PYSEC-2026-161 (GHSA-86qp-5c8j-p5mr): `request.url.path` rebuilt from an unvalidated `Host` header | 1.0.1 | No security decision reads `request.url`. Routing uses the raw path, and the security middleware's docs exemption reads `scope["path"]`. |
| PYSEC-2026-248: a path without a leading `/` moves the URL authority | 1.3.0 | Nothing reads `request.url.hostname` or `netloc`; uvicorn rejects such request targets before the app. |
| PYSEC-2026-249: `max_fields` and `max_part_size` ignored for url-encoded forms | 1.3.1 | The API never calls `request.form()`; the one multipart route parses the body itself with its own caps. |
| PYSEC-2026-2280: `HTTPEndpoint` dispatches non-standard methods to attributes | 1.1.0 | No `HTTPEndpoint` subclass; every route is a FastAPI function with explicit methods. |
| PYSEC-2026-2281: `StaticFiles` on Windows resolves UNC paths over SMB | 1.1.0 | No `StaticFiles`; the API runs on Linux (Render) and macOS. |

Revisit: lifting the cap to `fastapi>=0.115,<1` makes CI resolve the same versions as
Render. The full suite was run on that fresh set: 1,570 passed and one test failed,
`cloud/tests/test_ws.py::test_attach_wires_the_scheduler_and_the_matcher`, which looks
for the `/ws` route by `path` on `app.routes` and does not find it under starlette
1.10.0 (the WebSocket itself connects and works in the other tests). Fix that test,
then lift the cap, so CI tests what production runs. Not done in OP-401, because it
touches OP-109's and OP-303's files.

### Web: `npm audit` (npm 11.5.1, Node 24.5.0 locally; CI builds on Node 22)

Before OP-401: 6 moderate findings, all one advisory,
[GHSA-g3hv-75wg-m249](https://github.com/advisories/GHSA-g3hv-75wg-m249) (minimatch 7.0.0
to 11.0.0, denial of service through globparts amplification), reached only through
`@typescript-eslint/typescript-estree` 8.72.1 (`minimatch ^10.2.2`) under
`eslint-config-next`. This is the medium Dependabot alert on `minimatch`. It is a
development dependency used at lint time on our own patterns; nothing from it ships to
the browser or the server. `npm audit fix` could not move it (the fix is a new major).

Fixed on this branch: `web/package.json` gained
`"overrides": {"@typescript-eslint/typescript-estree": {"minimatch": "^11.0.5"}}`
(minimatch 11.0.5 supports Node 20 and 22 or later), `package-lock.json` follows, `npm
audit` reports 0 vulnerabilities and `npm run lint` passes. Revisit: drop the override
once `npm ls minimatch` shows typescript-estree on minimatch 11 by itself.

### How to run the audits again

```bash
python3.12 -m venv /tmp/audit && /tmp/audit/bin/pip install pip-audit
/tmp/audit/bin/pip-audit -r cloud/requirements.txt -r cloud/requirements-photo-check.txt   # Render
/tmp/audit/bin/pip-audit -r vision/requirements.txt
.venv/bin/python -m pip freeze > /tmp/frozen.txt && /tmp/audit/bin/pip-audit -r /tmp/frozen.txt --no-deps   # CI and dev
cd web && npm audit
```

Add the date, the commit and the results to this page.

## Follow-ups

1. **Script nonces.** Replace `'unsafe-inline'` in the web `script-src` with a
   per-request nonce (a Next.js middleware that sets the header and passes the nonce
   to the settings boot script). It makes every page dynamic, so measure the cost on
   Vercel first. A `Content-Security-Policy-Report-Only` header with the strict policy
   can collect violations before switching.
2. **One FastAPI for tests and production.** Fix
   `test_attach_wires_the_scheduler_and_the_matcher` for starlette 1.x and lift the
   `fastapi<0.129` cap in `cloud/requirements-weather.txt` (above).
3. **Proxy hops.** If Render documents the exact number of hops it adds, key the
   limiter by the last trusted hop instead of the first address, which a client can
   forge.
4. **One process.** The limiter counts per process. If the API ever runs on more than
   one instance, each instance has its own counts; that is acceptable for a limit
   against floods, but say so here.
5. **Error tracking and uptime** (decision D-1 in PLAN.md section 16) are still open.
