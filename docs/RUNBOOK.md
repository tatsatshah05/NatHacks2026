# OpenPath Runbook

What to do when something breaks, above all on demo day. Every failure below has its symptom, how to confirm it, and the fix. Hosting and one-time setup are in [DEPLOY.md](DEPLOY.md); CI and merge rules are in [CI_CD.md](CI_CD.md).

| Part | Where |
| --- | --- |
| API | `https://openpath-api-w6ql.onrender.com` (Render service `openpath-api`, free plan) |
| Database | Render Postgres `openpath-db` (free plan) |
| Web app | `https://openpath-lyart.vercel.app` (Vercel project `openpath`, Hobby plan) |
| Camera worker | Tatsat's laptop ([DEPLOY.md section 4](DEPLOY.md#4-camera-worker-on-demo-day)) |

## 1. Start with the demo check

Run it from the repository root after `make setup`. It sends only GET requests, one CORS preflight and one WebSocket connection; it never posts.

```bash
make demo-check                       # production, live data
.venv/bin/python scripts/demo_check.py --mode replay \
  --api http://localhost:8000 --web http://localhost:3000   # make dev
```

Each line starts with `ok`, `warn` or `FAIL` and names one dependency; the script exits with 1 if any line is `FAIL`. `--only health,status,web` runs a subset, and `make demo-check DEMO_CHECK_FLAGS="--mode replay"` passes options through make. The first `/health` call waits up to 150 s (`--wake-s`), because a sleeping free instance takes about a minute to wake; its line says how long it took.

| Line | It checks | If it fails, read |
| --- | --- | --- |
| `API /health` | 200, a 40-character commit, the expected mode | 2.1, 2.10 |
| `API /api/status` | the scheduler is running and no source is stale | 2.2, 2.8, 2.12 |
| `API /api/cameras` | a reading less than 600 s old (a warning in replay mode) | 2.3 |
| `API /api/route <profile>` | one fixed downtown trip per profile gives a route with its report | 2.2, 2.8 |
| `API /api/search?q=churchill` | place search returns results | 2.10 |
| `API WS /ws` | the live-update socket sends `hello` within 5 s | 2.7 |
| `web app` | the page answers 200 and says OpenPath | 2.9, 2.10 |
| `web app uses this API` | the web app's scripts name this API, so the browser calls it. The Replay badge is drawn in the browser from that API's `/health`, so in replay mode this line and `API /health` are the badge check | 2.7 |
| `CORS preflight` | the API accepts requests from the web app's origin | 2.7 |

### Uptime checks (decision D-1)

`.github/workflows/uptime.yml` runs `demo_check.py --only health,status,web` against production every 10 minutes. A red run means something is wrong, and GitHub emails "the user who last modified the cron syntax in the workflow file" ([GitHub docs](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)). To make sure the email arrives, open `https://github.com/settings/notifications` and, under Actions, choose email for failed workflows.

Things to know about scheduled workflows, from the same page:

- "Scheduled workflows run on the latest commit on the default branch." The check starts only after this file is merged into the default branch.
- "The `schedule` event can be delayed during periods of high loads of GitHub Actions workflow runs." A run can come late or, under very high load, be dropped.
- "In a public repository, scheduled workflows are automatically disabled when no repository activity has occurred in 60 days."

Each uptime run also wakes the free API. OP-404's `keep-warm.yml` calls `/health` on the same 10-minute cadence; the two can be merged into one workflow. While Render runs the replay fallback (2.2), set the repository variable `DEMO_CHECK_MODE` to `replay` (**Settings > Secrets and variables > Actions > Variables**), or every run fails on the mode; delete it when the API is live again.

**Phone alerts with UptimeRobot (optional).** GitHub only emails. For a push alert on a phone, the lead can add UptimeRobot. Its pricing page lists the free plan with "50 monitors" and a "5 min. monitoring interval", and keyword monitors are included; check there whether mobile push alerts are part of the free plan before relying on them.

1. Create a free account at `https://uptimerobot.com` and install the UptimeRobot app on the phone.
2. Add a monitor of type **HTTP(s)**, URL `https://openpath-api-w6ql.onrender.com/health`, interval 5 minutes.
3. Add a monitor of type **Keyword**, URL `https://openpath-api-w6ql.onrender.com/api/status`, keyword `"stale":true`, alert when the keyword **exists**. The API writes compact JSON, so the text is exactly `"stale":true` with no space.
4. Add a monitor of type **Keyword**, URL `https://openpath-lyart.vercel.app`, keyword `OpenPath`, alert when the keyword **does not exist**.
5. Under **Alert contacts**, add the lead's email and the mobile app, and attach them to all three monitors.

### Reading the logs

Open Render, then `openpath-api`, then **Logs**. Every record is one line: `<UTC time> <LEVEL> <logger> <message>`, for example:

```
2026-11-08T18:27:25.482Z INFO openpath.store CHANGE city_trail_closures:1 new trail_closed 'Trail closed: ...'
2026-11-08T18:30:24.715Z INFO uvicorn.access 127.0.0.1:59574 - "GET /health HTTP/1.1" 200
```

A traceback, when there is one, follows its line. With `LOG_FORMAT=json` on the service, each record is one JSON object instead (keys `time`, `level`, `logger`, `message`; a state change adds `event`, `source` and `id`; a traceback goes in `exc`):

```
{"time": "2026-11-08T18:32:21.991Z", "level": "INFO", "logger": "cloud.app.adapters.city_advisories", "message": "CHANGE city_trail_closures:1 new trail_closed", "event": "change", "source": "city_trail_closures", "id": "1"}
```

Useful searches: `CHANGE` (every state change), `SOURCE` (scheduler runs: `SOURCE lrt ok items=7 skipped=0`, `SOURCE lrt failed (2/3): ...`, `SOURCE lrt recovered`), `WARNING` and `ERROR`, `PUSH`, `REPORT`, `LIVE`, `MATCH`, and in JSON mode `"source": "city_lrt_devices"`. The API's own loggers log at `LOG_LEVEL`, `INFO` by default; `LOG_LEVEL=WARNING` gives back the warnings-only output the API had before OP-402. Changing a variable on Render redeploys the service.

## 2. Known failures

### 2.1 Render instance asleep or slow

**Symptom.** The first page load or route takes about a minute, then everything is normal. The demo check's `API /health` line says `200 in 40.0 s` or `after 3 tries`.

**Confirm.** `curl -s -o /dev/null -w '%{time_total} s\n' https://openpath-api-w6ql.onrender.com/health`. Render's free plan documentation says: "Render spins down a Free web service that goes 15 minutes without receiving any inbound traffic", and waking "takes about one minute" ([render.com/docs/free](https://render.com/docs/free)).

**Fix.**

1. Wake it: open `https://openpath-api-w6ql.onrender.com/health` and wait until it answers.
2. Keep it awake: the uptime workflow and OP-404's keep-warm workflow call it every 10 minutes, and the camera worker posts every 5 minutes. Start the worker and run `make demo-check` at least 10 minutes before judging.
3. If it is slow while awake (route lines over a few seconds), check **Metrics** on the Render service for memory near the free instance's 512 MB; restart the service from its Render page, or redeploy the current commit (2.10). Decision D-2 (PLAN.md section 16) covers an always-on paid instance.

### 2.2 A City source stale or failing

**Symptom.** `/sources` or the city dashboard shows a source as stale, or the demo check says `API /api/status: lrt stale (3 failures, last error ...)`.

**Confirm.** `curl -s https://openpath-api-w6ql.onrender.com/api/status | python3 -m json.tool` shows `stale`, `consecutive_failures` and `last_error` per source. The log has `SOURCE <name> failed (<n>/3): <error>` lines.

**What the app does.** Each attempt has a 10 s or 20 s timeout and a run has 3 retries (`config/sources.json`). A failed run never clears data: the items from the last good run stay until their own expiry time, and routing keeps using them. After 3 failed runs in a row the source is marked stale. The next good run logs `SOURCE <name> recovered`.

**Fix.**

1. One failure: wait for the next run (each source's `cadence_s` is on `/api/status`).
2. Check the City portal itself: open the source's `url` from `/api/status` (for example `https://data.edmonton.ca/d/snws-u3zx`). If the portal is down, there is nothing to fix on our side.
3. If it stays down through the demo, switch the API to the recorded data. On Render, open `openpath-api`, then **Environment**, set these and save (the service redeploys):

   | Key | Value |
   | --- | --- |
   | `DATA_MODE` | `replay` |
   | `REPLAY_SNAPSHOT` | `cloud/tests/fixtures` |
   | `REPLAY_AT` | optional: freezes the clock so dated closures stay in force, for example `2026-11-07T20:10:00Z` (the time the tests use). Without it the clock is the real one, as in `make dev` |

4. Check that `/health` says `"mode": "replay"` and the Replay badge shows on every page, and set the GitHub variable `DEMO_CHECK_MODE=replay` (section 1). The camera worker refuses to post to a replay API (HTTP 409), so stop it. Never present replayed data as live.
5. To go back, set `DATA_MODE=live`, delete `REPLAY_SNAPSHOT` and `REPLAY_AT`, save, and delete `DEMO_CHECK_MODE`.

### 2.3 Camera worker down

**Symptom.** The demo check says `API /api/cameras: no camera readings` or `none under 600 s old`; the city dashboard's "Road cameras" list is empty or old.

**Confirm.** `curl -s https://openpath-api-w6ql.onrender.com/api/cameras | python3 -m json.tool | grep age_s`. Look at the worker's terminal on Tatsat's laptop.

**What the app shows.** Readings expire 15 minutes after the frame, then the camera markers disappear and camera ice risks stop; the weather watch keeps running and routing carries on without cameras. The fallback notice in the UI is OP-412.

**Fix.** Restart the worker with the steps in [DEPLOY.md section 4](DEPLOY.md#4-camera-worker-on-demo-day). Keep the laptop awake and on power (`caffeinate -i`). Run it on one laptop only: the 511 key allows 10 calls per 60 s for the whole team, and after an HTTP 429 the worker pauses for one minute. Within about 10 minutes, `age_s` should be under 600 again.

### 2.4 Model file missing or hash mismatch

**Symptom.** The worker stops at startup, or the API logs `REPORT photo check: the ONNX model is not available (ValueError)`.

**Confirm.** The worker prints `Model checksum does not match its manifest` when `model.onnx` does not hash to the `sha256` in `labels.json`, and stops with a usage error when `--model-labels`, `--model-sha256`, `--min-score` or the model (`MODEL_URL` or `--model-file`) is missing. On the API, `(ModuleNotFoundError)` instead means the build did not install `cloud/requirements-photo-check.txt`.

**Fix.**

1. Compare the hashes: `shasum -a 256 /path/to/model.onnx` and the `sha256` in `labels.json` and in `vision/MODEL_CARD.md` must be the same 64 characters. Never edit `labels.json` to make them match: download the release asset again ([DEPLOY.md section 6, step 1](DEPLOY.md#6-report-photo-check-on-render-op-308)).
2. On Render, check `MODEL_URL`, `MODEL_LABELS` and `MODEL_SHA256` (DEPLOY.md section 6, step 2), and that the build command installs `cloud/requirements-photo-check.txt` (step 3).
3. While the model is missing, report photos get low confidence and the API keeps accepting reports; the worker posts nothing, so cameras fall back as in 2.3.

### 2.5 Claude check unavailable (`ANTHROPIC_API_KEY`)

**Symptom.** The worker with `--ai-check` stops at startup with `--ai-check requires ANTHROPIC_API_KEY`, posts only `unknown` readings, or the API logs `REPORT photo check: only ... is running`.

**Confirm.** On the laptop, `echo ${ANTHROPIC_API_KEY:+set}` prints `set`. On Render, the key is under **Environment**. After 3 Claude API failures in a row, the check pauses for 5 minutes; those camera views post `unknown` and report photos get low confidence.

**Fix.** Set or renew the key (on the laptop with `export ANTHROPIC_API_KEY=...`, then restart the worker; on Render in **Environment**). If the Anthropic API itself is down, nothing else is needed: the camera rule falls back to `unknown` and the weather watch, and reports keep working at low confidence.

### 2.6 Push notifications not arriving

**Symptom.** "Send me a test alert" on `/alerts` shows nothing, or saved-route alerts never come.

**Confirm.**

1. `curl -s https://openpath-api-w6ql.onrender.com/api/push/public-key` must say `"push_enabled": true`. If it is `false`, the log has one `PUSH disabled` line with the reason (a key missing, no subject, or a public key that does not belong to the private key).
2. On iPhone and iPad, alerts work only from the home-screen app ("Add to Home Screen"), and that app has its own saved routes.
3. The browser or system may block notifications for the site; check its notification settings.

**Fix.**

1. Set `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY` and `VAPID_SUBJECT` on Render as in [DEPLOY.md section 5](DEPLOY.md#5-web-push-keys-op-304). Changing the keys invalidates every existing subscription, so generate them once.
2. An expired subscription: when the push service answers 404 or 410, the API deletes the subscription (`PUSH subscription ... gone`). Open `/alerts` again: it posts the browser's current subscription, then send a test alert.
3. Timing is by design: at most one push per saved route every 120 s, and after a restart or wake-up the watcher waits 120 s before it rechecks routes.

### 2.7 WebSocket not connecting

**Symptom.** The planner's live status says "Trying to reconnect. Your routes stay as last checked." or "Live updates are paused.", or the demo check says `API WS /ws: no event within 5 s`.

**Confirm.** The demo check's `API WS /ws` line names the error: `HTTP 403` means the API serving that URL has no `/ws` route (an older release); a timeout means the API is asleep or down (2.1). In the browser, open the developer console on the planner and look for the `wss://` URL it tries.

**Fix.**

1. On Vercel, `NEXT_PUBLIC_WS_URL` must be `wss://openpath-api-w6ql.onrender.com/ws` for Production and Preview. `NEXT_PUBLIC_*` values are built into the app, so redeploy after changing one (**Deployments**, the latest production deployment, **Redeploy**).
2. CORS does not apply to `/ws`: the API accepts WebSocket connections from any page. A CORS error in the console is about the HTTP calls; `CORS_ORIGINS` on Render must list the web app's origin (`https://openpath-lyart.vercel.app` plus the preview pattern, DEPLOY.md section 2, step 5). The demo check's `CORS preflight` line tests it.
3. If the `web app uses this API` line fails, the web app calls another API or none: check `NEXT_PUBLIC_API_BASE_URL` and `NEXT_PUBLIC_API_URL` on Vercel, then redeploy.

### 2.8 Database down or full

**Symptom.** Saving a route, reporting a barrier or subscribing to alerts fails; the map has no barriers; `SOURCE ... failed` lines name a database error (`OperationalError`).

**What fails and what keeps working.** Every write fails: reports, saved routes, push subscriptions and camera readings. The scheduler cannot store new items, so sources turn stale (2.2), and `/api/obstructions` fails. Routing keeps working while the API keeps running: a failed hazard rebuild keeps the matcher's last good map and a failed camera read keeps the unexpired camera risks, so `/api/route` answers with the barriers it last knew. `/health` and `/api/status` do not use the database and keep answering.

**Confirm.** In Render, open `openpath-db`: its status, and its storage under **Info** or **Metrics**. Render's free plan documentation says: "Free Render Postgres databases have a fixed storage capacity of 1 GB." It also says they "expire 30 days after creation", that "An expired Free database is inaccessible unless you upgrade it to a paid compute plan", and that after a 14-day grace period "Render deletes the database (along with all of its data)". Check the expiry date on the database page now.

**Fix.**

1. Database expired: upgrade it to a paid plan in the Render dashboard; that keeps its data.
2. Database lost or unusable: the API needs only an empty database to run. It creates its tables at startup and the scheduler refills the City items within one run of each source; what is lost without a backup is saved routes, push subscriptions and community reports. Point `DATABASE_URL` on `openpath-api` at the new database, then restore the last backup (section 4).
3. Full: free storage is fixed at 1 GB. Upgrade the plan, or restore the last backup into a new database.

### 2.9 Vercel deployment limit reached

**Symptom.** Vercel refuses a new deployment, or a merged change never reaches `https://openpath-lyart.vercel.app`.

**Confirm.** Vercel's limits page lists "Deployments Created per Day" as 100 on Hobby and 6000 on Pro, and says: "Should you hit the rate limit, you will need to wait another day before you can deploy again" ([vercel.com/docs/limits](https://vercel.com/docs/limits)). The project's **Deployments** page shows the refused deployment.

**Fix.**

1. Stop spending deployments. Since PR #169, `web/vercel.json` creates deployments only for `main` (`git.deploymentEnabled` with `"*": false` and `"main": true`), so pull requests and branch pushes no longer build previews. Check that the file still says so, and merge into `main` less often.
2. The current production deployment keeps serving; a limit never takes the site down.
3. If a fix must ship today, start a Pro trial: Vercel's trial page says to select the team switcher, choose **Create Team**, name it and pick **Pro Trial**, that the trial "finishes after 14 days", and that there is "a limit of one Pro plan trial per user account" ([vercel.com/docs/plans/pro-plan/trials](https://vercel.com/docs/plans/pro-plan/trials)). The trial is a new team, so the `openpath` project has to move into it (**Project Settings > General > Transfer Project**); afterwards check the production URL, the environment variables and the preview pattern in `CORS_ORIGINS`.

### 2.10 Rolling back a bad deploy

**Render (API).**

1. Open `openpath-api`, then its **Deploys** page. Render's documentation says: "Find a recent successful deploy and click Rollback", then "Rollback to this deploy" ([render.com/docs/rollbacks](https://render.com/docs/rollbacks)). A dashboard rollback also turns auto-deploy off, which `openpath-api` already has off.
2. Or deploy a chosen commit with the deploy hook. Render: "You can specify a commit SHA to deploy by appending the `ref` query parameter to your deploy hook URL." The hook URL already has a `?key=` query, so append `&ref=`:

   ```bash
   curl -fsS -X POST "$RENDER_API_DEPLOY_HOOK&ref=<good commit SHA>"
   ```

3. Confirm with `make demo-check`: the `API /health` line shows the commit that is now live.
4. The next merge into `main` deploys `main` again through CI, so fix `main` (2.11) before the next merge.

**Vercel (web app).**

1. Open the project's overview page and click **Instant Rollback** on the production deployment, or use the context menu of a production deployment on the **Deployments** page. Vercel: "Hobby users can roll back to the immediately previous deployment"; Pro can pick any earlier production deployment ([vercel.com/docs/instant-rollback](https://vercel.com/docs/instant-rollback)).
2. "After a rollback, Vercel turns off auto-assignment of production domains", so new merges stop going live. To undo it, click **Manage** on the production deployment tile and promote a deployment, or run `vercel promote <deployment URL>`.

### 2.11 CI red on `main`

**Symptom.** The `CI` run on `main` is red, so the deploy job did not run.

**What it means.** Production keeps running the last commit that passed: the deploy job runs only after `CI passed` (CI_CD.md).

**Fix.**

1. Open the failed job in **Actions** and read the first error.
2. If it is a flaky test or a network hiccup in a setup step, click **Re-run failed jobs** once. If it fails again, it is real.
3. Lint: run `make lint`, then `make format`; CI pins ruff 0.17.4, so use `pip install ruff==0.17.4`.
4. Fix it with a `fix:` pull request into `main` (CI_CD.md allows urgent `fix:` work there), reviewed like any other. Never weaken a test to make it pass. The admin bypass is only for emergencies and only after `CI passed` is green.
5. If production itself is broken meanwhile, roll back (2.10) while the fix goes through.

### 2.12 Weather watch unknown or Open-Meteo 429

**Symptom.** `/api/weather` answers `"active": null` with "Weather data is not available yet.", or `/api/status` shows `weather_watch` with a `last_error` that starts `open_meteo: HTTP 429`. While the watch is unknown, a wet camera reading cannot become ice risk and the planner says the weather watch is off.

**Confirm.** `curl -s https://openpath-api-w6ql.onrender.com/api/weather` and `curl -s https://openpath-api-w6ql.onrender.com/api/status | python3 -m json.tool | grep -A 12 weather_watch`. In the log, `WARN weather open_meteo: 429, next try at <UTC time> (<Open-Meteo's reason>)` marks each backoff, `CHANGE weather open_meteo: back` the recovery, and `SOURCE weather_watch failed (<n>/3)` lines mean Environment Canada itself failed. On November 8, 2026 the adapter's exact request got 200 from a laptop (`X-RateLimit-Limit: 600` per 60 s window) while Render got 429 on every attempt, so the limit applies to Render's shared outbound address, not to our request.

**What the app does (since the fix of November 8).** Open-Meteo is optional. With `stale: false` and an `open_meteo:` message in `last_error`, the watch is running on Environment Canada alone: air and dew point are current, `ground_c` is null, and the `reason` says how many of the last 12 hours the history covers. After a 429 the adapter waits `Retry-After`, or 10 minutes doubling to 60, then tries again; the next 200 restores the full rule. `weather_watch` goes stale only when Environment Canada fails 3 times in a row, as for every source (2.2).

**Fix.**

1. `stale: false` with an `open_meteo:` message: nothing to do. The message names the next try; wait for the `CHANGE weather open_meteo: back` line.
2. Hours of 429 without recovery: Open-Meteo's free tier is limiting Render's address. Its commercial API needs a key and another host, which is a code change and a decision for the lead; the watch keeps running without it.
3. `"active": null` with `stale: true`: Environment Canada is failing. Open `https://api.weather.gc.ca/collections/swob-realtime/items?f=json&msc_id-value=3012209&limit=1` in a browser; if it is down there is nothing to fix on our side. The last good watch is kept until a refresh succeeds, and the replay fallback (2.2) serves the recorded weather.

## 3. Backups

**Render does not back up our database.** Render's free plan page says: "Free Render Postgres databases don't support any form of backups" ([render.com/docs/free](https://render.com/docs/free)). Its backup page adds: "Render does not provide recovery capabilities for databases on the Free compute plan", "Render does not create logical backups for databases on the Free compute plan", and "Use the `pg_dump` utility from your local machine" ([render.com/docs/postgresql-backups](https://render.com/docs/postgresql-backups)). Also, "Only one Free Render Postgres database can be active for any given workspace" (render.com/docs/free), so a second free database for a restore test is not possible.

### The nightly workflow

`.github/workflows/backup.yml` runs every day at 09:17 UTC (02:17 in Edmonton) and on demand (**Actions > Backup > Run workflow**). Like every scheduled workflow, it runs only from the default branch. Each run:

1. Zips the replay snapshot, `cloud/tests/fixtures` (what `make dev` and the replay fallback in 2.2 read), with a SHA-256 list of its files, as the artifact `replay-snapshot-<run id>`. With the copy in git, the snapshot is in two places. Measured on November 8 at commit 3967064: 40 files, 528 KB on disk, a 69,365-byte zip.
2. If the repository secret `DATABASE_URL` is set, connects to the database, installs the PostgreSQL client of the server's major version, and runs `scripts/db_backup.py`, which writes `openpath-<UTC time>.dump` (`pg_dump --format=custom`) and its manifest `openpath-<UTC time>.dump.json` (size, SHA-256, rows per table).
3. Encrypts the dump with gpg (AES-256, passphrase `BACKUP_PASSPHRASE`) and uploads the encrypted dump and its manifest as `openpath-db-<run id>`. GitHub's page on downloading artifacts says "Read access to the repository is required" ([GitHub docs](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts)), and every signed-in user has read access to a public repository. The dump holds saved routes and push subscriptions, so it is never uploaded unencrypted; the manifest holds no URL or host name.
4. Restores the dump into a throwaway Postgres container of the same major version (the restore drill) and compares the row counts with the manifest. A failed drill turns the run red after the backup is already uploaded.

Artifacts are kept 14 days. Without `DATABASE_URL` the database part is skipped with a notice; with `DATABASE_URL` and no `BACKUP_PASSPHRASE` the run fails, so the gap is noticed.

**Turn the database backup on (lead, once).**

1. In Render, open `openpath-db`, then **Info** (or **Connect**), and copy the **External Database URL**.
2. Make a passphrase and keep it in the team's password manager: `openssl rand -base64 32`.
3. In GitHub, open **Settings > Secrets and variables > Actions**, choose **New repository secret**, and add `DATABASE_URL` (the URL from step 1) and `BACKUP_PASSPHRASE` (step 2).
4. Once `backup.yml` is on the default branch, run it once: `gh workflow run backup.yml --repo tatsatshah05/NatHacks2026`, then `gh run watch --repo tatsatshah05/NatHacks2026`. If `openpath-db` limits inbound connections by IP (its **Networking** or access control settings), GitHub's runners need to be allowed.
5. Check the run: the **Back up the database** step prints `BACKUP postgres ... (<bytes> bytes, <n> tables, <rows> rows, ...)` and the drill step prints `RESTORE postgres ... done`. Record the run in section 4.3.

**A backup from a laptop.** `pg_dump` must have the server's major version. Find it with `.venv/bin/python -c "import os, psycopg; print(psycopg.connect(os.environ['DATABASE_URL']).info.server_version)"` (170005 means 17), install that client (on macOS, `brew install postgresql@17`, then put `$(brew --prefix postgresql@17)/bin` first on `PATH`), and run:

```bash
export DATABASE_URL='<External Database URL>'
.venv/bin/python scripts/db_backup.py --yes --out backups
```

`backups/` is ignored by git. Without `--yes` the script only says what it would do. The password is never printed or put on a command line; `pg_dump` reads it from `PGPASSWORD`.

## 4. Restore

`scripts/db_restore.py` checks the backup's SHA-256 against its manifest and that its kind (SQLite or Postgres) matches the target, restores it, then counts every table and compares the counts with the manifest. Without `--yes` it changes nothing.

- **Postgres:** `pg_restore --clean --if-exists --no-owner --no-privileges --single-transaction`. Each table is dropped and recreated, the whole restore is one transaction (it completes or changes nothing), and owners and grants are left out so a backup can go into a database with another user. Render's advice is "Only restore exports into an empty database"; prefer a new, empty database when there is a choice. Count differences are warnings, because the manifest counted rows while the source was live.
- **SQLite:** the `sqlite3` backup API copies the backup over the target file. Stop the API first. Any count difference is a failure.

### 4.1 Restore from the nightly backup (Postgres)

1. Find the run and download its artifact (needs `gh` signed in):

   ```bash
   gh run list --repo tatsatshah05/NatHacks2026 --workflow backup.yml --limit 5
   gh run download <run id> --repo tatsatshah05/NatHacks2026 --name openpath-db-<run id> --dir backups
   ```

2. Decrypt it (on macOS, `brew install gnupg` first; gpg asks for `BACKUP_PASSPHRASE`):

   ```bash
   gpg --output backups/openpath-<time>.dump --decrypt backups/openpath-<time>.dump.gpg
   ```

3. Pick the target. For a drill, start a throwaway Postgres of the server's major version (needs Docker) and restore into it:

   ```bash
   docker run -d --name restore-drill -e POSTGRES_PASSWORD=drill -p 127.0.0.1:55432:5432 postgres:<major>
   .venv/bin/python scripts/db_restore.py --yes backups/openpath-<time>.dump \
     --database-url postgresql://postgres:drill@127.0.0.1:55432/postgres
   docker rm -f restore-drill
   ```

   For a real restore, use the new database's External Database URL as `--database-url` (or `DATABASE_URL`), with `pg_restore` of the same major version on `PATH` (section 3).

4. The script ends with `RESTORE postgres ... done in <s> s; <n> tables: public.obstruction <rows>, ...`. A `RESTORE problem:` line or exit code 1 means a table is missing; stop and keep the old database.
5. After a real restore, make sure `DATABASE_URL` on `openpath-api` names the restored database, redeploy or restart the service, and run `make demo-check`.

### 4.2 Restore a SQLite backup (`make dev`)

```bash
export DATABASE_URL=sqlite:///./openpath.db
.venv/bin/python scripts/db_backup.py --yes --out backups
# stop make dev, then:
.venv/bin/python scripts/db_restore.py --yes backups/openpath-<time>.sqlite3
```

### 4.3 Restore test record

| When (UTC) | Where | What | Result |
| --- | --- | --- | --- |
| 2026-11-08 18:31 | Builder's Mac, branch `op-402-monitoring-runbook` | SQLite, by the CLI: backed up a replay API's database while it ran, stopped the API, deleted every `obstruction` row, restored | Backup 102,400 bytes, 4 tables, 61 rows; after the wipe 0 obstruction rows; after the restore 61, and the restore's row check passed |
| every test run | `cloud/tests/test_backup_restore.py` | SQLite on the app's real tables: seed, back up, wipe and add a row, restore, compare every row | Same rows; the app reads the restored obstructions |
| not run yet | Render `openpath-db` | Postgres: the first nightly run's restore drill (3, step 4 of "Turn the database backup on"), or 4.1 by hand | The lead fills this in. `pg_dump` and `pg_restore` were not installed on the builder's machine, so no Postgres backup or restore has been run yet |
