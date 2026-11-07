# Deploying OpenPath

The API and its database run on Render, and the web app runs on Vercel. Merges to `main` deploy through CI: the `Deploy to production` job calls the Render deploy hook after `CI passed`, then waits until `/health` reports the new commit. Vercel builds a preview for every pull request and deploys production on every merge. [docs/CI_CD.md](CI_CD.md) has the full pipeline and merge rules; this page is the one-time setup in detail.

Secrets live only in the Render, Vercel and GitHub settings, never in the repository. Every variable is listed in [`.env.example`](../.env.example).

## 1. Render: API and Postgres

1. In the Render dashboard, choose **New → Blueprint** and connect `tatsatshah05/NatHacks2026`.
2. Render reads [`render.yaml`](../render.yaml) and creates:
   - `openpath-api`, a Python web service run from the repository root (`uvicorn cloud.app.main:app`), with its health check on `/health` and **auto-deploy off**
   - `openpath-db`, a Postgres database whose `DATABASE_URL` is wired to the API automatically
3. When Render asks for the `sync: false` variables, fill them in:
   - `CORS_ORIGINS`: the Vercel production URL plus a preview pattern, for example `https://openpath.vercel.app,https://nat-hacks2026-*.vercel.app`. A `*` matches one part of a host name.
   - `SOCRATA_APP_TOKEN`: from the City of Edmonton open data portal
   - The others (`VAPID_*`, `WEATHER_*`) can stay empty until the stories that use them (OP-109, OP-304)
4. Render generates `ADMIN_KEY` and `WORKER_KEY` itself. Share them with the team privately, never in Git.
5. Open the `openpath-api` service, go to **Settings → Deploy Hook**, and copy the URL.
6. Check that `https://<service>.onrender.com/health` returns `{"status": "ok", "version": "<commit>"}`.

The free plan sleeps after a period without traffic. Decision D-2 (PLAN.md section 16) chooses between always-on instances and a keep-warm before judging.

## 2. Vercel: web app

1. In Vercel, choose **Add New → Project** and import `tatsatshah05/NatHacks2026`.
2. Set **Root Directory** to `web`. Vercel detects Next.js; keep the default build settings.
3. Add these environment variables for both Production and Preview:
   - `NEXT_PUBLIC_API_URL`: the Render API URL, for example `https://openpath-api.onrender.com`
   - `NEXT_PUBLIC_WS_URL`: the same host with `wss://` and `/ws`
   - `NEXT_PUBLIC_VAPID_PUBLIC_KEY`: empty until OP-304
4. Deploy. The home page shows "API: connected" when the web app can reach the API. If it shows "API: not reachable", check `CORS_ORIGINS` on Render.

## 3. GitHub: connect CI to the deploys (repository admin)

In **Settings → Secrets and variables → Actions**:

| Kind | Name | Value |
| --- | --- | --- |
| Secret | `RENDER_API_DEPLOY_HOOK` | The deploy hook URL from Render step 5 |
| Secret | `RENDER_WORKER_DEPLOY_HOOK` | Added later, when the vision worker exists (OP-204) |
| Variable | `API_URL` | The Render API URL, for example `https://openpath-api.onrender.com` |
| Variable | `WEB_URL` | The Vercel production URL |

Until these are set, the deploy job skips each step with a notice instead of failing.
