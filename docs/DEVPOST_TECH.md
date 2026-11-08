# OpenPath: the technical section of the Devpost

This is the technical part of the Devpost submission (OP-413). The story, the inspiration and the pitch belong to OP-409. Every number here comes from a file in this repository, named beside it, or was measured in this repository and says how; the [Numbers](#numbers) list at the end has each one with its source. Where a number does not exist yet, it says "to be measured" and names the story that will measure it. The full design is in [docs/ARCHITECTURE.md](ARCHITECTURE.md).

## How we built it

OpenPath has three parts: an API, a web app and a camera worker.

**The API** is Python 3.12 with FastAPI, Pydantic v2 and SQLModel, on Render's free plan with Postgres. A scheduler inside the API process polls each City of Edmonton dataset and the weather services on its own cadence, with a timeout, retries and a last-good cache. Every adapter turns its rows into one shared `Obstruction` contract (source, category, kind, effect, geometry, confidence, times, link, attribution), and the store upserts them by id and keeps only what has not expired.

**Routing** runs on the whole city's OpenStreetMap walking network. A networkx graph of Edmonton would need about 2.8 GB (recorded in `cloud/app/network.py`), far more than a free instance, so we convert it once on a laptop into compact NumPy arrays (`cloud/data/network.npz`) with flags for stairs, elevators, crossings and sidewalks. At run time the API loads those arrays and a KD-tree and never imports OSMnx. A matcher ties each barrier to the path edges it affects (points within 30 m, lines inside a 15 m buffer, neighbourhood advisories as areas), and SciPy's Dijkstra finds the best route for each mobility profile with weights from `config/profiles.json`. Alternates come from penalising the edges already used; one is kept only if it shares less than 70% of its length with the routes already kept. Each route carries a grouped report of every barrier on it, near it or avoided by it, with its source and confidence, plus Google Maps and Apple Maps links.

**The camera worker** runs on the lead's laptop, because Render's free plan has no background workers. It reads 511 Alberta cameras inside Edmonton through the official API, classifies each frame in memory with a MobileNetV3-Small model exported to ONNX, and posts a reading only when Claude Haiku 5.5 agrees with the model. A second model trained with RSCD road-surface images adds snow and ice readings. A reading changes only after two agreeing frames in a row. On the API, an `ice` reading, or a `wet` reading while the weather watch is active, becomes ice risk on the crossings within 50 m of the camera. We trained with PyTorch on a laptop and published the weights as GitHub release assets, never in Git.

**The web app** is Next.js 15.5 with React 19 and MapLibre GL 6 on Vercel. It draws each route as a transit-style line diagram where every barrier is a stop with its own shape, fill pattern and words, so it never relies on colour alone. Route changes arrive over a WebSocket and are announced in an `aria-live` region, spoken if the person turns speech on, and vibrated where the device supports it. Saved routes get Web Push alerts through a service worker that caches nothing. There are no accounts: the browser keeps an anonymous id.

**Replay mode** serves a recorded snapshot of every source through a fake HTTP transport and can freeze the clock, so `make dev` runs the full app with no keys and the demo works on a day without snow. Every page shows a Replay badge when it is on.

**How we worked.** Six people moved through four phases with one branch per phase, squash-only merges, a required `CI passed` check (ruff, pytest, lint, typecheck, the web build, and checks for secrets, weights and images) and one approval per pull request. The decisions are recorded as ADRs in [docs/adr/](adr/).

**Built with:** Python, FastAPI, Pydantic, SQLModel, Postgres, SQLite, NumPy, SciPy, OSMnx (build time only), ONNX Runtime, PyTorch and torchvision (training only), the Anthropic API (Claude Haiku 5.5), Next.js, React, TypeScript, Tailwind CSS, MapLibre GL, Web Push (pywebpush), Render, Vercel, GitHub Actions, OpenStreetMap, City of Edmonton Open Data, 511 Alberta, Environment and Climate Change Canada, Open-Meteo and RSCD.

## Challenges we ran into

- **No snow and no ice.** No 511 Alberta camera showed snow or ice on November 8: 2 `snow_partial` frames out of 1,372 collected (`vision/MODEL_CARD.md`). The main model has never seen snow or ice. We added a second model trained with licensed RSCD images (CC BY 4.0) that decides only snow and ice, and Claude must still agree. Snow and ice accuracy on 511 cameras stays unmeasured until real frames exist.
- **Black ice is invisible.** A clear layer can look like a dry or wet road, so we never claim to detect it. We report "ice risk" from a weather rule and from cameras, and only local signals change routes.
- **Class imbalance.** With one `snow_partial` frame in training, inverse-frequency weighting gave that image about 60% of the loss and a 4% validation score, so the main model trains on dry, wet and unusable only (`vision/MODEL_CARD.md`).
- **Elevators the City gives as station points.** The outage feed says which station, not where on the map the elevator is, and a wrong match would send a wheelchair user to a broken elevator. We never snap an outage to a nearby path; without a reviewed match it stays a station warning. None of the 32 City elevators has a reviewed match yet (`cloud/app/lrt.py`), so elevator outages do not reroute today.
- **Free hosting shaped the design.** Render's free plan has no background workers, so the camera worker runs on a laptop. Vercel's Hobby plan allows 100 deployments per rolling 24 hours, and previews for every push used them up on November 8, which blocked production, so Vercel now deploys `main` only (pull request #169). The free API also sleeps without traffic.
- **Threads and in-memory SQLite.** CI crashed with a segmentation fault when the app closed SQLite's one shared in-memory connection while scheduler threads were still writing. We now stop every thread that uses the store, in order and with timeouts, before closing the engine, and lend the shared connection to one thread at a time (pull requests #159 and #165).
- **Real City data is messy.** Two recorded trail notices were longer than the 500-character `detail` limit, so the contract check skipped them and those closures never reached the map; we now shorten text at a word instead (pull request #163). A replay clock that froze only the scheduler made the map empty (pull request #136). A wheelchair route could snap into a pocket of sidewalk cut off by stairs and a closure (pull request #135).

## Accomplishments that we're proud of

- **Whole-city routing on a free instance.** The full Edmonton walking network (134,948 nodes and 194,643 edges) loads from a 6.5 MB file into 20.5 MB of arrays, measured in this repository.
- **One honest report per route.** Every route lists every known barrier from every source, grouped, with its confidence, its source link and when OpenPath last confirmed it, and says when an item cannot change routes.
- **Measured results on cameras the model never saw.** The main model scores 89.9% on 306 test images from 26 unseen cameras, and with Claude's agreement it posts fewer false `dry` readings: 4 instead of 19 on 308 test frames (`vision/MODEL_CARD.md`). The card also says what the numbers do not show.
- **Reliability as a rule.** A failed source never clears data, a failed rebuild keeps the last good map, a failed report falls back to the hazards the route met, and missing keys turn features off instead of crashing the API.
- **Privacy by construction.** No accounts, no cookies, and no stored images. A test with a disk-write spy checks that a report photo is never written anywhere.
- **A test suite that runs without keys or network:** 1,537 tests passed in our run.

## What we learned

- Graph libraries are for building, not serving: precomputed arrays and a sparse matrix made city-wide routing fit in a free instance.
- Labels made with AI help are not ground truth. Our test labels are AI-assisted, so the model card reports agreement with Claude-made labels and calls labeller agreement "AI-to-AI", not human.
- A second opinion can matter more than a higher score: requiring the model and Claude to agree took false `dry` readings from 19 to 4, while correct readings went from 218 to 207 (`vision/MODEL_CARD.md`).
- Free tiers are architecture decisions. Background workers, deployment limits and sleeping instances each changed where code runs.
- Treating failure as normal is cheaper than debugging it live: a last-good value everywhere, and one log line per state change.

## What's next for OpenPath

- **Measure snow and ice on real 511 frames** as soon as a camera shows them, and publish the result in the model card.
- **Reviewed elevator matches,** so an elevator outage removes the right path for wheelchair and walker users instead of only warning.
- **Show the weather watch in the route report.** Today it is served on `GET /api/weather` and feeds the camera rule, but it is not yet an item in the report (see [docs/ARCHITECTURE.md](ARCHITECTURE.md#where-the-code-differs-from-the-plan)).
- **City intersection cameras,** where crosswalks and curb ramps are, once the City gives written permission.
- **Phase 4 measurements:** performance and load (OP-404), the accessibility audit (OP-408), monitoring and the runbook (OP-402), model monitoring and the fallback notice (OP-412).
- **From the plan's "Could" list:** lit routes at night, the nearest sandbox, 311 filing through Open311, 511 road weather stations to calibrate the ice rule, SMS alerts and French (PLAN.md section 5).

**Honest limits.** OpenPath covers Edmonton only. Its camera coverage is limited to the 511 Alberta views in and around the city: 19 cameras with 47 enabled views inside an approximate Edmonton bounding box ([docs/DATA_SOURCES.md](DATA_SOURCES.md)), mostly on highways rather than sidewalks. The model was trained on a small dataset collected on one day, with AI-assisted labels. It runs on free hosting, so the API can sleep and the camera worker depends on one laptop.

## Numbers

Each number, with where it comes from. "Measured here" means measured in this repository on commit `3967064` (the Phase 4 base; this branch changes only Markdown), on the lead's laptop (macOS), on November 8, 2026.

**Tests**

- 1,537 passed, 4 skipped, 1 warning, 11 subtests passed. Measured here with `PYTHONPATH=.:cloud:vision python -m pytest -q cloud vision`. Web lint, typecheck and build run in CI and are not counted here.

**Walking network** (measured here with `cloud.app.network.WalkNetwork.load()` on `cloud/data/network.npz`)

- 134,948 nodes and 194,643 edges.
- 418 stair edges and 22,469 crossing edges (`edge_flags`).
- 6.5 MB on disk and 20.5 MB of arrays in memory (`WalkNetwork.nbytes`).
- 0.051 s to 0.056 s to load, in three runs of `cloud.app.network.measure_load()` on the lead's laptop. Not measured on Render.
- About 2.8 GB for the same city as a networkx graph, as recorded in the `cloud/app/network.py` docstring. Not measured here.

**Search** (measured here with `cloud.app.search.load_index()`, from the manifest of `cloud/data/search.npz`)

- 346,922 addresses, 29 LRT stations and 54 named places.

**Data sources**

- 7 scheduled sources in `config/sources.json`, 6 of which produce items; the seventh is the weather watch.
- 392 cameras in 511 Alberta's metadata, 19 inside the approximate Edmonton bounding box, with 47 enabled views on those 19 ([docs/DATA_SOURCES.md](DATA_SOURCES.md), recorded November 8, 2026).

**Road-condition model** (all from [vision/MODEL_CARD.md](../vision/MODEL_CARD.md), run `v2-ours`, evaluated 2026-11-08T17:07:42+00:00)

- Dataset: 1,372 frames from 129 provincial 511 Alberta cameras, all taken on November 8, 2026.
- Training: 830 frames from 90 cameras; validation: 123 frames from 13 cameras.
- Test: 306 images from 26 cameras never used in training or validation. Accuracy 89.9%.
- Recall on the test cameras: `dry` 95.5% (224 images), `wet` 56.2% (16 images), `unusable` 78.8% (66 images). `snow_partial`, `snow_covered` and `ice`: no test images, so not measured.
- With the Claude check, on 308 test frames: 211 readings posted and 207 correct (98.1%), 4 false `dry`. The model alone: 242 posted and 218 correct (90.1%), 19 false `dry`. Reference labels are AI-assisted.
- Labeller agreement: 93.2%, Cohen's kappa 0.83, on 103 images labelled twice. Both passes were made with Claude, so this is AI-to-AI agreement, not human.
- Speed: median 2.98 ms and p95 3.09 ms for one image on the CPU with one thread, over 50 runs on the training machine (Darwin arm64). Not measured on Render.
- Cost: US$0.04 for the Claude check on the 240 test frames the model would have posted.

**To be measured**

- Route request latency and API memory on Render: to be measured by OP-404 (performance and load); `docs/PERFORMANCE.md` does not exist yet. [docs/DEPLOY.md](DEPLOY.md#6-report-photo-check-on-render-op-308) records 321 MiB peak memory measured on a Mac with the photo check loaded and says to treat it as an estimate for Render's 512 MB instance.
- Accessibility results (axe issues, keyboard and screen-reader walkthroughs, 200% zoom): to be measured by OP-408 (accessibility audit).
- The model's accuracy on snow and ice on 511 cameras, and on phone photos from community reports: not measured; no story owns it yet, because it needs real frames of snow and ice.
- The cost of the Claude check per report photo: not measured yet ([docs/DEPLOY.md](DEPLOY.md#6-report-photo-check-on-render-op-308)).
