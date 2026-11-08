# Deploying OpenPath

The API and its database run on Render, and the web app runs on Vercel. Merges to `main` deploy through CI: the `Deploy to production` job calls the Render deploy hook after `CI passed`, then waits until `/health` reports the new commit. Vercel builds a preview for every pull request and deploys production on every merge. [docs/CI_CD.md](CI_CD.md) has the full pipeline and merge rules; this page is the one-time setup in detail.

Secrets live only in the Render, Vercel and GitHub settings, never in the repository. Every variable is listed in [`.env.example`](../.env.example). Nothing reads a `.env` file.

## 1. Render: API and Postgres

1. In the Render dashboard, choose **New → Blueprint** and connect `tatsatshah05/NatHacks2026`.
2. Render reads [`render.yaml`](../render.yaml) and creates:
   - `openpath-api`, a Python web service run from the repository root (`uvicorn cloud.app.main:app`), with its health check on `/health` and **auto-deploy off**
   - `openpath-db`, a Postgres database whose `DATABASE_URL` is wired to the API automatically
3. When Render asks for the `sync: false` variables, fill them in:
   - `SOCRATA_APP_TOKEN`: from the City of Edmonton open data portal
   - `CORS_ORIGINS`: leave it empty for now and set it after step 2 below
   - The others (`VAPID_*`, `WEATHER_*`) can stay empty until the stories that use them (OP-109, OP-304)
4. Render generates `ADMIN_KEY` and `WORKER_KEY` itself. Share them with the team privately, never in Git.
5. Open the `openpath-api` service, go to **Settings → Deploy Hook**, and copy the URL.
6. Check that `https://<service>.onrender.com/health` returns `{"status": "ok", "version": "<commit>", "mode": "live"}`.

The free plan sleeps after a period without traffic. Decision D-2 (PLAN.md section 16) chooses between always-on instances and a keep-warm before judging.

## 2. Vercel: web app

1. In Vercel, choose **Add New → Project** and import `tatsatshah05/NatHacks2026`.
2. Name the project `openpath`, and set **Root Directory** to `web`. Vercel detects Next.js; keep the default build settings.
3. Add these environment variables for both Production and Preview. URLs have no trailing slash.
   - `NEXT_PUBLIC_API_URL`: the Render API URL, for example `https://openpath-api.onrender.com`
   - `NEXT_PUBLIC_API_BASE_URL`: the same URL. The OP-113 web app (#94) reads this name, while AGENTS.md names `NEXT_PUBLIC_API_URL`; set both until the two agree.
   - `NEXT_PUBLIC_WS_URL`: the same host with `wss://` and `/ws`
   - `NEXT_PUBLIC_VAPID_PUBLIC_KEY`: empty until OP-304
4. Deploy, and note the production URL (the project's **Domains** page) and your team slug (the `<team-slug>` at the end of every preview URL).
5. On Render, set `CORS_ORIGINS` on `openpath-api` to the production URL plus the preview pattern, for example `https://openpath.vercel.app,https://openpath-*-<team-slug>.vercel.app`. Vercel preview URLs look like `openpath-<hash>-<team-slug>.vercel.app` and `openpath-git-<branch>-<team-slug>.vercel.app`, and a `*` matches part of one host-name label.
6. Open the site. If the browser console shows a CORS error, check `CORS_ORIGINS` on Render.

`CORS_ORIGINS` is not a security boundary. The API serves public data, never allows credentials (no cookies), and protects writes with the `ADMIN_KEY` and `WORKER_KEY` headers. The allowlist only decides which web pages can read API responses in a browser. The team slug keeps the wildcard to our own previews in normal use, but Vercel project names are chosen by their owners, so do not rely on it to keep anyone out.

## 3. GitHub: connect CI to the deploys (repository admin)

In **Settings → Secrets and variables → Actions**:

| Kind | Name | Value |
| --- | --- | --- |
| Secret | `RENDER_API_DEPLOY_HOOK` | The deploy hook URL from Render step 5 |
| Secret | `RENDER_WORKER_DEPLOY_HOOK` | Not used: the camera worker runs on a laptop (section 4) |
| Variable | `API_URL` | The Render API URL, for example `https://openpath-api.onrender.com` |
| Variable | `WEB_URL` | The Vercel production URL |

Until these are set, the deploy job skips each step with a notice instead of failing.

## 4. Camera worker on demo day

Render's free plan has no background workers, so the camera worker (OP-204) runs on one team laptop during the demo and posts to the live API (decided November 8; Estella runs it). Run it on one laptop only: the 511 key allows 10 calls per 60 seconds for the whole team, so stop any frame collection while it runs.

1. **Install:** `make setup` in the repository.
2. **Set the environment** in the terminal (values by direct message from Tatsat; never commit them):

   ```bash
   export ALBERTA_511_KEY="..."   # the team's 511 Alberta key
   export WORKER_KEY="..."        # the same value as WORKER_KEY on the openpath-api service
   export CLOUD_URL="https://openpath-api-w6ql.onrender.com"
   export MODEL_URL="..."         # the model release asset from OP-202
   ```

3. **Get the model details** from the OP-202/OP-203 release: `labels.json`, the model's SHA-256 and the validated minimum score in `vision/MODEL_CARD.md`.
4. **Start it** and keep the laptop awake and on power (`caffeinate -i` on macOS):

   ```bash
   caffeinate -i python -m vision.worker --live --publish \
     --model-labels /path/to/labels.json --model-sha256 <sha256> --min-score <validated score>
   ```

5. **Check** that `https://openpath-api-w6ql.onrender.com/api/cameras` lists readings with `age_s` under 600 within about 10 minutes.

If the worker stops, its readings expire after 15 minutes and the weather watch keeps running (OP-412 adds the fallback notice in the UI). Its posts every 5 minutes also keep the free API awake, so start it at least 10 minutes before judging.

## Current deployment (November 8, 2026)

| Part | Where |
| --- | --- |
| API | `https://openpath-api-w6ql.onrender.com` (Render web service `openpath-api`, free plan, Blueprint managed from `main`) |
| Web app | `https://openpath-lyart.vercel.app` (Vercel project `openpath`, root directory `web`, production branch `main`) |
| GitHub `production` environment | secret `RENDER_API_DEPLOY_HOOK`; variables `API_URL`, `WEB_URL` |
| Vercel environment variables | `NEXT_PUBLIC_API_BASE_URL`, `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_WS_URL` (production and preview) |

`CORS_ORIGINS` on the API is `https://openpath-lyart.vercel.app,https://openpath-*-tatsats-projects-997c1533.vercel.app`. The free Render instance sleeps after inactivity; decision D-2 covers always-on instances for judging.
