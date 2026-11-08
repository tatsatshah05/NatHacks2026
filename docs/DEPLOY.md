# Deploying OpenPath

The API and its database run on Render, and the web app runs on Vercel. Merges to `main` deploy through CI: the `Deploy to production` job calls the Render deploy hook after `CI passed`, then waits until `/health` reports the new commit. Vercel deploys production on every merge to `main` and nothing else: preview deployments are off in `web/vercel.json` (see [docs/CI_CD.md](CI_CD.md)). [docs/CI_CD.md](CI_CD.md) has the full pipeline and merge rules; this page is the one-time setup in detail.

Secrets live only in the Render, Vercel and GitHub settings, never in the repository. Every variable is listed in [`.env.example`](../.env.example). Nothing reads a `.env` file.

## 1. Render: API and Postgres

1. In the Render dashboard, choose **New → Blueprint** and connect `tatsatshah05/NatHacks2026`.
2. Render reads [`render.yaml`](../render.yaml) and creates:
   - `openpath-api`, a Python web service run from the repository root (`uvicorn cloud.app.main:app`), with its health check on `/health` and **auto-deploy off**
   - `openpath-db`, a Postgres database whose `DATABASE_URL` is wired to the API automatically
3. When Render asks for the `sync: false` variables, fill them in:
   - `SOCRATA_APP_TOKEN`: from the City of Edmonton open data portal
   - `CORS_ORIGINS`: leave it empty for now and set it after step 2 below
   - `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`: generate them as in section 5. Left empty, the API runs and saves routes, but sends no push alerts
   - `WEATHER_*` can stay empty until OP-109 uses them
   - `ANTHROPIC_API_KEY`, `MODEL_URL`, `MODEL_LABELS`, `MODEL_SHA256`: the report photo check, set as in section 6. Left empty, the API runs and every report gets low confidence
4. Render generates `ADMIN_KEY` and `WORKER_KEY` itself. Share them with the team privately, never in Git.
5. Open the `openpath-api` service, go to **Settings → Deploy Hook**, and copy the URL.
6. Check that `https://<service>.onrender.com/health` returns `{"status": "ok", "version": "<commit>", "mode": "live"}`.

Render spins down a free web service after 15 minutes without inbound traffic, and the first request afterwards waits about a minute while it starts again ([render.com/docs/free](https://render.com/docs/free), read on November 8, 2026). Since OP-404, [`.github/workflows/keep-warm.yml`](../.github/workflows/keep-warm.yml) calls `/health` and `/api/status` every 10 minutes from the default branch and fails loudly when either is not 200, and [docs/PERFORMANCE.md](PERFORMANCE.md) has the judging plan. Decision D-2 (PLAN.md section 16) still covers an always-on instance.

## 2. Vercel: web app

1. In Vercel, choose **Add New → Project** and import `tatsatshah05/NatHacks2026`.
2. Name the project `openpath`, and set **Root Directory** to `web`. Vercel detects Next.js; keep the default build settings.
3. Add these environment variables for both Production and Preview. URLs have no trailing slash.
   - `NEXT_PUBLIC_API_URL`: the Render API URL, for example `https://openpath-api.onrender.com`
   - `NEXT_PUBLIC_API_BASE_URL`: the same URL. The OP-113 web app (#94) reads this name, while AGENTS.md names `NEXT_PUBLIC_API_URL`; set both until the two agree.
   - `NEXT_PUBLIC_WS_URL`: the same host with `wss://` and `/ws`
   - `NEXT_PUBLIC_VAPID_PUBLIC_KEY`: the same value as `VAPID_PUBLIC_KEY` (section 5). Optional: the web app can read it from `GET /api/push/public-key` instead
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

Render's free plan has no background workers, so the camera worker (OP-204) runs on one team laptop during the demo and posts to the live API (decided November 8; it runs on Tatsat's laptop, including on demo day). Run it on one laptop only: the 511 key allows 10 calls per 60 seconds for the whole team, so stop any frame collection while it runs.

1. **Install:** `make setup` in the repository.
2. **Set the environment** in the terminal (the values stay on Tatsat's laptop; never commit them):

   ```bash
   export ALBERTA_511_KEY="..."   # the team's 511 Alberta key
   export WORKER_KEY="..."        # the same value as WORKER_KEY on the openpath-api service
   export CLOUD_URL="https://openpath-api-w6ql.onrender.com"
   export ANTHROPIC_API_KEY="..." # the Claude agree check (Render has its own for report photos, section 6)
   export MODEL_URL="..."         # the model release asset, once it is published (section 6, step 1)
   ```

3. **Get the model details:** `labels.json`, the model's SHA-256 and the validated minimum score in `vision/MODEL_CARD.md`. Until the weights are published as a release (section 6, step 1), use the local `model.onnx` with `--model-file` instead of setting `MODEL_URL`; the SHA-256 is checked either way.
4. **Start it** and keep the laptop awake and on power (`caffeinate -i` on macOS):

   ```bash
   caffeinate -i python -m vision.worker --live --publish \
     --model-file /path/to/model.onnx --model-labels /path/to/labels.json \
     --model-sha256 <sha256> --min-score <validated score> --ai-check claude-haiku-5-5 \
     --snow-model-file /path/to/snow/model.onnx --snow-model-labels /path/to/snow/labels.json \
     --snow-model-sha256 <snow sha256>
   ```

   The three `--snow-model-*` options add the snow and ice model from `vision/MODEL_CARD.md` (`v2-rscd`); its answer is used only when it is snow or ice, and the main model decides everything else.

   `--ai-check` posts a reading only when the model and Claude agree; anything else posts `unknown` (lead decision, November 8). It stops at startup without `ANTHROPIC_API_KEY`. If the Claude API fails 3 times in a row, the check pauses for 5 minutes and those views post `unknown`, so a pass is never held up. Leave out `--model-file` once `MODEL_URL` is set.

5. **Check** that `https://openpath-api-w6ql.onrender.com/api/cameras` lists readings with `age_s` under 600 within about 10 minutes.

If the worker stops, its readings expire after 15 minutes and the weather watch keeps running (OP-412 adds the fallback notice in the UI). Its posts every 5 minutes also keep the free API awake, so start it at least 10 minutes before judging.

**No cold starts during judging (OP-404).** Three things keep the API warm; any one of them is enough, and together they cover a laptop that drops off the Wi-Fi:

1. The camera worker above, posting every 5 minutes.
2. The `Keep warm` workflow (`.github/workflows/keep-warm.yml`), which calls `/health` and `/api/status` every 10 minutes from the default branch. Check that its last run is green in the Actions tab before judging; a red run means the API is down or reports no commit, and GitHub emails the person who last changed the workflow file.
3. A warm-up visit 10 minutes before the slot: open the web app, plan one route, open the alerts page. This also fills the matcher's hazard map and the routing caches, so the first route in front of the judges is not the first route of the day.

[docs/PERFORMANCE.md](PERFORMANCE.md) has the measured route latency on this instance.

## 5. Web Push keys (OP-304)

Push alerts for saved routes are signed with a VAPID key pair. Generate one once, on a laptop, from the repository root after `make setup`:

```bash
.venv/bin/python -m cloud.app.push --generate-keys --subject mailto:<team address>
```

It prints four lines. Put `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY` and `VAPID_SUBJECT` on the `openpath-api` service in Render, and `NEXT_PUBLIC_VAPID_PUBLIC_KEY` (the same public key) in Vercel for Production and Preview. Share the private key only through the dashboards, never in Git or chat. Both keys are base64url strings: the public key is the browser's `applicationServerKey`, and the private key is its raw 32 bytes, which pywebpush reads directly.

Check that `https://<service>.onrender.com/api/push/public-key` returns `"push_enabled": true`. If it says `false`, the API log has one `PUSH disabled` line with the reason (a key missing, no subject, or a public key that does not belong to the private key). Changing the keys invalidates every existing browser subscription, so generate them once and keep them.

## 6. Report photo check on Render (OP-308)

Community report photos are checked by both the ONNX road-condition model and Claude (lead decision, November 8). A report gets medium confidence only when both agree with the reported type; anything else is low, and the response says why (`photo.note`) and which checks answered (`photo.checked_by`). For a blocked path, which none of the model's six classes can show, Claude answers "Is the walking path in this photo blocked?" and the model only has to be able to read the photo, so **blocked confirmation rests on Claude alone**. The model was trained and tested on road-camera frames; its accuracy on phone photos is not measured.

`render.yaml` installs `cloud/requirements-photo-check.txt` on top of `cloud/requirements.txt`: only `onnxruntime`, `Pillow` and `anthropic`, never PyTorch or OpenCV. Measured on November 8 for Linux x86_64 and Python 3.12: the `onnxruntime` 1.31.1 wheel is 23.8 MB (67 MB installed), and the three packages with their new dependencies add about 126 MiB installed. With the `v1-night` model and the Claude client loaded, the API's peak memory after startup and one model check was 321 MiB, against 255 MiB without them (measured on a Mac, so treat it as an estimate for the 512 MB free instance).

The lead does these steps once.

1. **Publish the model as a GitHub release** (as in [vision/README.md](../vision/README.md#publishing-the-weights)), from the laptop that holds the `v1-night` run. The repository is public, so Render downloads the assets without a token.

   ```bash
   RUN=/path/to/runs/v1-night   # the folder with model.onnx, labels.json and metrics.json
   SHA=$(python -c "import json, sys; print(json.load(open(sys.argv[1]))['model_sha256'])" "$RUN/metrics.json")
   shasum -a 256 "$RUN/model.onnx"; echo "$SHA"   # both must be 5b54b705...304b96
   ```

   Only if both lines show `5b54b705219138edea5b26c13f9c2a99390dde4f6058c6a822da879f59304b96`, publish:

   ```bash
   gh release create camera-model-night-v1 "$RUN/model.onnx" "$RUN/labels.json" \
     --repo tatsatshah05/NatHacks2026 \
     --title "Road-condition model v1-night" \
     --notes "Trained with vision/train.py, seed 202. model.onnx SHA-256: $SHA. Metrics: vision/MODEL_CARD.md."
   ```

   Check the published asset: `curl -sL https://github.com/tatsatshah05/NatHacks2026/releases/download/camera-model-night-v1/model.onnx | shasum -a 256` prints the same SHA-256.

2. **Set four variables** on the `openpath-api` service (Render dashboard, **Environment**). Never commit them.

   | Key | Value |
   | --- | --- |
   | `ANTHROPIC_API_KEY` | An Anthropic API key for the team |
   | `MODEL_URL` | `https://github.com/tatsatshah05/NatHacks2026/releases/download/camera-model-night-v1/model.onnx` |
   | `MODEL_LABELS` | `https://github.com/tatsatshah05/NatHacks2026/releases/download/camera-model-night-v1/labels.json` |
   | `MODEL_SHA256` | `5b54b705219138edea5b26c13f9c2a99390dde4f6058c6a822da879f59304b96` |

   Optional: `REPORTS_AI_MODEL` (default `claude-haiku-5-5`) and `MODEL_MIN_SCORE` (default 0.5, the score at which the model card's agree-check results were measured). Do not set `MODEL_FILE` on Render.

3. **Deploy.** The new build command reaches Render with `render.yaml` when this change is released into `main`. Check that **Settings → Build Command** reads `pip install -r cloud/requirements.txt -r cloud/requirements-photo-check.txt`; if it does not, run **Manual Sync** on the Blueprint. The order of steps 2 and 3 does not matter: without the packages, the API logs that a check is not available and keeps accepting reports at low confidence.

4. **Check it.** The API configures no INFO logging, so Render's log shows only warnings. A correct setup starts with no `REPORT photo check` warning; each problem has its own line:
   - `REPORT photo check: only ... is running; the decided setup is the ONNX model and Claude together (check ...)`: one check is missing, and the line names what to set.
   - `REPORT photo check: the ONNX model is not available (ValueError)`: usually `MODEL_SHA256` does not match `model.onnx` or the `sha256` inside `labels.json`. The API refuses both rather than use the wrong model.
   - `... not available (ModuleNotFoundError)`: the build did not install `cloud/requirements-photo-check.txt` (step 3).

   To see the success line before deploying, run this on a laptop after `make setup`, with the same four values exported in the shell. It downloads the release assets and checks the SHA-256 but makes no Claude call:

   ```bash
   .venv/bin/python -c "import logging; logging.basicConfig(level=logging.INFO); from cloud.app.reports import build_photo_checker; build_photo_checker()"
   # REPORT photo check: onnx:5b54b7052191 + claude:claude-haiku-5-5; medium confidence needs both to agree
   ```

**Privacy.** Each report photo is sent to the Anthropic API in memory for the Claude check and never stored by OpenPath (docs/DATA_SOURCES.md, Community Reports). Claude is sent only the photo and the question, never the note, the location or the device id.

**Cost and speed.** Each report photo makes at most one Claude call, and none when the model already rules it out, with a 10 s timeout and one retry. After 3 API failures in a row the Claude check pauses for 5 minutes, and photos in that time get low confidence. `vision/ai_check.py` prices Haiku 5.5 at $0.10 per million input tokens and $0.50 per million output tokens; the cost per photo has not been measured yet.

## Current deployment (November 8, 2026)

| Part | Where |
| --- | --- |
| API | `https://openpath-api-w6ql.onrender.com` (Render web service `openpath-api`, free plan, Blueprint managed from `main`) |
| Web app | `https://openpath-lyart.vercel.app` (Vercel project `openpath`, root directory `web`, production branch `main`) |
| GitHub `production` environment | secret `RENDER_API_DEPLOY_HOOK`; variables `API_URL`, `WEB_URL` |
| Vercel environment variables | `NEXT_PUBLIC_API_BASE_URL`, `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_WS_URL` (production and preview) |

`CORS_ORIGINS` on the API is `https://openpath-lyart.vercel.app,https://openpath-*-tatsats-projects-997c1533.vercel.app`. The free Render instance spins down after 15 idle minutes; the `Keep warm` workflow and the camera worker keep it awake (section 4), and decision D-2 covers an always-on instance for judging.
