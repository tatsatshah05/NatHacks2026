"""Demo-day check (OP-402): one line per dependency, ``ok`` or ``FAIL``.

    python scripts/demo_check.py                    # production, live data (make demo-check)
    python scripts/demo_check.py --mode replay --api http://localhost:8000 --web http://localhost:3000
    python scripts/demo_check.py --only health,status,web   # what .github/workflows/uptime.yml runs

Every line starts with ``ok``, ``warn`` or ``FAIL`` (green, yellow or red on a
terminal; the words alone carry the meaning), and the script exits with 1 if any
check failed. Only read-only requests are sent: GET, one CORS preflight (OPTIONS)
and one WebSocket connection. Nothing is posted.

The checks, in order (names for ``--only``):

``health``   ``GET /health`` answers 200 with a 40-character commit and the expected
             mode. The first call waits up to ``--wake-s`` seconds, because a free
             Render instance takes about a minute to wake.
``status``   ``GET /api/status``: the scheduler is running and no source is stale.
``cameras``  ``GET /api/cameras`` has a reading less than 600 s old (a warning, not a
             failure, with ``--mode replay``).
``route``    ``GET /api/route`` for one fixed downtown trip, once per profile: at
             least one route, and the best one carries its report.
``search``   ``GET /api/search?q=churchill`` returns results.
``ws``       ``WS /ws`` connects and sends its first event (``hello``) within 5 s.
``web``      the web app answers 200 with "OpenPath" in its HTML.
``webapi``   the web app's scripts name this API, so the browser calls it. The Replay
             badge is drawn in the browser from that API's ``/health``, so it is not
             in the HTML; with ``--mode replay`` this line and ``health`` together
             are the badge check.
``cors``     a preflight from the web app's origin is accepted.

Defaults: ``--api`` from ``CLOUD_URL``, else ``NEXT_PUBLIC_API_URL``, else the
production API; ``--web`` is the production web app (docs/DEPLOY.md).
Needs only httpx, plus the websockets package (installed with uvicorn[standard]
from cloud/requirements.txt) for the ``ws`` check.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_API = "https://openpath-api-w6ql.onrender.com"
PRODUCTION_WEB = "https://openpath-lyart.vercel.app"

# One fixed downtown trip: the examples on GET /api/route (cloud/app/routing_api.py).
TRIP_FROM = "53.5444,-113.4909"
TRIP_TO = "53.5461,-113.4938"
# config/profiles.json is the shared contract; these are its keys, if it is missing.
DEFAULT_PROFILES = ("wheelchair", "walker_cane", "low_vision", "standard")
SEARCH_QUERY = "churchill"
# docs/DEPLOY.md section 4: a running camera worker keeps readings under 600 s old.
CAMERA_MAX_AGE_S = 600.0
WS_FIRST_EVENT_S = 5.0
# Render: a free instance "takes about one minute" to wake (render.com/docs/free).
DEFAULT_WAKE_S = 150.0
REQUEST_TIMEOUT_S = 30.0
ROUTE_TIMEOUT_S = 60.0  # the first route request may build the hazard map
MAX_SCRIPTS = 40  # web app scripts read by the webapi check
MAX_SCRIPT_BYTES = 3_000_000
COMMIT = re.compile(r"[0-9a-f]{40}")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}

Status = Literal["ok", "warn", "FAIL"]
Mode = Literal["live", "replay"]
CHECKS = (
    "health",
    "status",
    "cameras",
    "route",
    "search",
    "ws",
    "web",
    "webapi",
    "cors",
)


@dataclass
class Result:
    status: Status
    name: str
    detail: str


@dataclass
class Context:
    api: str
    web: str
    mode: Mode
    client: httpx.Client
    # (url, timeout_s) -> the first message's text; raises on failure.
    ws_first_message: Callable[[str, float], str]
    wake_s: float = DEFAULT_WAKE_S
    profiles: Sequence[str] = DEFAULT_PROFILES
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    notes: dict[str, Any] = field(default_factory=dict)


# ------------------------------------------------------------------- helpers


def short(value: object, limit: int = 160) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def is_local(url: str) -> bool:
    return (urlsplit(url).hostname or "") in LOCAL_HOSTS


def origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def ws_url(api: str) -> str:
    parts = urlsplit(api)
    scheme = "wss" if parts.scheme == "https" else "ws"
    return f"{scheme}://{parts.netloc}{parts.path.rstrip('/')}/ws"


def load_profiles(path: Path = ROOT / "config" / "profiles.json") -> tuple[str, ...]:
    try:
        return tuple(json.loads(path.read_text(encoding="utf-8"))["profiles"])
    except (OSError, ValueError, KeyError, TypeError):
        return DEFAULT_PROFILES


def get_json(
    ctx: Context, path: str, timeout_s: float = REQUEST_TIMEOUT_S, **params: str
) -> tuple[httpx.Response, Any]:
    """GET ``path`` on the API; raises ``ValueError`` with a short reason on failure."""
    response = ctx.client.get(f"{ctx.api}{path}", params=params, timeout=timeout_s)
    if response.status_code != 200:
        raise ValueError(f"HTTP {response.status_code}: {short(response.text, 120)}")
    try:
        return response, response.json()
    except ValueError:
        raise ValueError("the answer is not JSON") from None


def failure(name: str, error: Exception) -> Result:
    if isinstance(error, httpx.TimeoutException):
        reason = "timed out"
    elif isinstance(error, httpx.HTTPError):
        reason = f"{type(error).__name__}: {short(error, 120)}"
    else:
        reason = short(error)
    return Result("FAIL", name, reason)


# -------------------------------------------------------------------- checks


def check_health(ctx: Context) -> list[Result]:
    """``/health``, waiting up to ``wake_s`` for a sleeping free instance."""
    name = "API /health"
    started = ctx.clock()
    attempts = 0
    last_error = "no answer"
    body: Any = None
    while True:
        attempts += 1
        try:
            response = ctx.client.get(f"{ctx.api}/health", timeout=ctx.wake_s)
            if response.status_code == 200:
                body = response.json()
                break
            last_error = f"HTTP {response.status_code}"
        except (httpx.HTTPError, ValueError) as error:
            last_error = failure(name, error).detail
        if ctx.clock() - started >= ctx.wake_s:
            return [
                Result(
                    "FAIL",
                    name,
                    f"{last_error} after {attempts} tries in "
                    f"{ctx.clock() - started:.0f} s",
                )
            ]
        ctx.sleep(5)
    elapsed_s = ctx.clock() - started
    ctx.notes["health_s"] = elapsed_s
    ctx.notes["health_attempts"] = attempts
    waited = f"200 in {elapsed_s:.1f} s" + (
        f" after {attempts} tries" if attempts > 1 else ""
    )
    if not isinstance(body, dict) or body.get("status") != "ok":
        return [Result("FAIL", name, f"{waited}, but the body is {short(body, 80)}")]
    version = str(body.get("version", ""))
    mode = body.get("mode")
    problems: list[str] = []
    warnings: list[str] = []
    if not COMMIT.fullmatch(version):
        if version == "dev" and is_local(ctx.api):
            warnings.append("version is 'dev' (a local run; Render sets the commit)")
        else:
            problems.append(f"version {version!r} is not a 40-character commit")
    if mode != ctx.mode:
        problems.append(f"mode is {mode!r}, expected {ctx.mode!r}")
    detail = f"{waited}; version {version}; mode {mode}"
    if problems:
        return [Result("FAIL", name, f"{detail}; " + "; ".join(problems))]
    if warnings:
        return [Result("warn", name, f"{detail}; " + "; ".join(warnings))]
    return [Result("ok", name, detail)]


def check_status(ctx: Context) -> list[Result]:
    name = "API /api/status"
    try:
        _, body = get_json(ctx, "/api/status")
    except (httpx.HTTPError, ValueError) as error:
        return [failure(name, error)]
    sources = body.get("sources") or []
    running = bool(body.get("scheduler_running"))
    stale = [s for s in sources if s.get("stale")]
    problems: list[str] = []
    if not running:
        problems.append("the scheduler is not running")
    if not sources:
        problems.append("no sources are scheduled")
    for source in stale:
        error = source.get("last_error")
        problems.append(
            f"{source.get('source')} stale ({source.get('consecutive_failures', 0)} "
            f"failures" + (f", last error {short(error, 80)}" if error else "") + ")"
        )
    ages = [s["age_s"] for s in sources if isinstance(s.get("age_s"), int | float)]
    oldest = f"; oldest data {max(ages):.0f} s" if ages else ""
    if problems:
        return [Result("FAIL", name, "; ".join(problems))]
    return [
        Result(
            "ok",
            name,
            f"scheduler running; {len(sources)} sources, none stale{oldest}",
        )
    ]


def check_cameras(ctx: Context) -> list[Result]:
    name = "API /api/cameras"
    try:
        _, body = get_json(ctx, "/api/cameras")
    except (httpx.HTTPError, ValueError) as error:
        return [failure(name, error)]
    features = body.get("features") or []
    ages = [
        float(f["properties"]["age_s"])
        for f in features
        if isinstance(f, dict)
        and isinstance(f.get("properties", {}).get("age_s"), int | float)
    ]
    fresh = [age for age in ages if age < CAMERA_MAX_AGE_S]
    if fresh:
        return [
            Result(
                "ok",
                name,
                f"{len(fresh)} of {len(features)} readings under "
                f"{CAMERA_MAX_AGE_S:.0f} s old; newest {min(fresh):.0f} s",
            )
        ]
    if ages:
        detail = (
            f"{len(features)} readings, none under {CAMERA_MAX_AGE_S:.0f} s old "
            f"(newest {min(ages):.0f} s); is the camera worker running?"
        )
    else:
        detail = "no camera readings; is the camera worker running?"
    if ctx.mode == "replay":
        return [Result("warn", name, f"{detail} (replay mode: a warning only)")]
    return [Result("FAIL", name, detail)]


def check_route(ctx: Context) -> list[Result]:
    results: list[Result] = []
    for profile in ctx.profiles:
        name = f"API /api/route {profile}"
        started = ctx.clock()
        try:
            _, body = get_json(
                ctx,
                "/api/route",
                timeout_s=ROUTE_TIMEOUT_S,
                profile=profile,
                to=TRIP_TO,
                **{"from": TRIP_FROM},
            )
        except (httpx.HTTPError, ValueError) as error:
            results.append(failure(name, error))
            continue
        elapsed_s = ctx.clock() - started
        routes = body.get("routes") or []
        if not routes:
            warnings = "; ".join(body.get("warnings") or []) or "no warnings"
            results.append(Result("FAIL", name, f"no route ({short(warnings, 100)})"))
            continue
        report = routes[0].get("report")
        if not isinstance(report, dict) or not isinstance(report.get("groups"), list):
            results.append(Result("FAIL", name, "the best route has no report"))
            continue
        results.append(
            Result(
                "ok",
                name,
                f"{len(routes)} routes in {elapsed_s:.1f} s; best "
                f"{routes[0].get('distance_m', 0):.0f} m, "
                f"{routes[0].get('eta_min', 0):.1f} min; report "
                f"{report.get('on_route', 0)} on route, "
                f"{report.get('nearby', 0)} nearby, "
                f"{report.get('avoided', 0)} avoided",
            )
        )
    return results


def check_search(ctx: Context) -> list[Result]:
    name = f"API /api/search?q={SEARCH_QUERY}"
    try:
        _, body = get_json(ctx, "/api/search", q=SEARCH_QUERY)
    except (httpx.HTTPError, ValueError) as error:
        return [failure(name, error)]
    if not isinstance(body, list) or not body:
        return [Result("FAIL", name, "no results")]
    first = body[0] if isinstance(body[0], dict) else {}
    return [Result("ok", name, f"{len(body)} results; first {first.get('label')!r}")]


def check_ws(ctx: Context) -> list[Result]:
    url = ws_url(ctx.api)
    name = "API WS /ws"
    started = ctx.clock()
    try:
        text = ctx.ws_first_message(url, WS_FIRST_EVENT_S)
    except Exception as error:  # noqa: BLE001 - any library error is a failed check
        return [
            Result(
                "FAIL",
                name,
                f"no event within {WS_FIRST_EVENT_S:.0f} s "
                f"({type(error).__name__}: {short(error, 100)})",
            )
        ]
    elapsed_s = ctx.clock() - started
    try:
        kind = json.loads(text).get("type")
    except (ValueError, AttributeError):
        return [Result("FAIL", name, f"first message is not JSON: {short(text, 80)}")]
    if kind != "hello":
        return [Result("FAIL", name, f"first event is {kind!r}, expected 'hello'")]
    return [Result("ok", name, f"connected; 'hello' in {elapsed_s:.1f} s")]


def check_web(ctx: Context) -> list[Result]:
    name = "web app"
    try:
        response = ctx.client.get(ctx.web, timeout=REQUEST_TIMEOUT_S)
    except httpx.HTTPError as error:
        return [failure(name, error)]
    ctx.notes["web_html"] = response.text if response.status_code == 200 else None
    if response.status_code != 200:
        return [Result("FAIL", name, f"HTTP {response.status_code}")]
    if "OpenPath" not in response.text:
        return [Result("FAIL", name, "200, but the page does not say OpenPath")]
    return [Result("ok", name, f"200; {len(response.content)} bytes; says OpenPath")]


def script_urls(html: str, web: str) -> list[str]:
    """The web app's own Next.js scripts in the page, as absolute URLs."""
    urls: list[str] = []
    for src in re.findall(r"""<script[^>]+src=["']([^"']+)["']""", html):
        if src.startswith("/_next/"):
            urls.append(f"{origin(web)}{src}")
        elif src.startswith(f"{origin(web)}/_next/"):
            urls.append(src)
    return list(dict.fromkeys(urls))[:MAX_SCRIPTS]


def check_webapi(ctx: Context) -> list[Result]:
    name = "web app uses this API"
    html = ctx.notes.get("web_html")
    if html is None:
        try:
            response = ctx.client.get(ctx.web, timeout=REQUEST_TIMEOUT_S)
        except httpx.HTTPError as error:
            return [failure(name, error)]
        if response.status_code != 200:
            return [Result("FAIL", name, f"web app HTTP {response.status_code}")]
        html = response.text
    needle = origin(ctx.api)
    urls = script_urls(html, ctx.web)
    for url in urls:
        try:
            response = ctx.client.get(url, timeout=REQUEST_TIMEOUT_S)
        except httpx.HTTPError:
            continue
        if response.status_code == 200 and needle in response.text[:MAX_SCRIPT_BYTES]:
            badge = (
                "; the Replay badge reads mode replay from it"
                if ctx.mode == "replay"
                else ""
            )
            return [
                Result("ok", name, f"{needle} is in {url.rsplit('/', 1)[-1]}{badge}")
            ]
    return [
        Result(
            "FAIL",
            name,
            f"none of the page's {len(urls)} scripts names {needle}; check "
            "NEXT_PUBLIC_API_BASE_URL and NEXT_PUBLIC_API_URL on Vercel",
        )
    ]


def check_cors(ctx: Context) -> list[Result]:
    web_origin = origin(ctx.web)
    name = "CORS preflight"
    try:
        response = ctx.client.options(
            f"{ctx.api}/api/route",
            headers={
                "Origin": web_origin,
                "Access-Control-Request-Method": "GET",
            },
            timeout=REQUEST_TIMEOUT_S,
        )
    except httpx.HTTPError as error:
        return [failure(name, error)]
    allowed = response.headers.get("access-control-allow-origin")
    if response.status_code == 200 and allowed in (web_origin, "*"):
        return [Result("ok", name, f"{web_origin} allowed")]
    return [
        Result(
            "FAIL",
            name,
            f"{web_origin} refused (HTTP {response.status_code}, allow-origin "
            f"{allowed!r}); add it to CORS_ORIGINS on Render",
        )
    ]


CHECK_FUNCTIONS: dict[str, Callable[[Context], list[Result]]] = {
    "health": check_health,
    "status": check_status,
    "cameras": check_cameras,
    "route": check_route,
    "search": check_search,
    "ws": check_ws,
    "web": check_web,
    "webapi": check_webapi,
    "cors": check_cors,
}


def run_checks(ctx: Context, names: Sequence[str] = CHECKS) -> list[Result]:
    results: list[Result] = []
    for name in CHECKS:
        if name not in names:
            continue
        try:
            results.extend(CHECK_FUNCTIONS[name](ctx))
        except Exception as error:  # noqa: BLE001 - one broken check must not hide the rest
            results.append(Result("FAIL", name, f"check crashed: {error!r}"))
    return results


# --------------------------------------------------------------------- output

COLOURS = {"ok": "\033[32m", "warn": "\033[33m", "FAIL": "\033[31m"}
RESET = "\033[0m"


def format_line(result: Result, colour: bool) -> str:
    word = f"{result.status:<4}"
    if colour:
        word = f"{COLOURS[result.status]}{word}{RESET}"
    return f"{word}  {result.name}: {result.detail}"


def summary(results: Sequence[Result]) -> str:
    counts = {status: 0 for status in ("ok", "warn", "FAIL")}
    for result in results:
        counts[result.status] += 1
    return f"{counts['ok']} ok, {counts['warn']} warn, {counts['FAIL']} FAIL"


def websocket_first_message(url: str, timeout_s: float) -> str:
    """Connect to ``url`` and return the first text message within ``timeout_s``."""
    from websockets.sync.client import connect

    with connect(url, open_timeout=timeout_s, close_timeout=2) as socket:
        message = socket.recv(timeout=timeout_s)
    return message if isinstance(message, str) else message.decode("utf-8")


def default_api(environ: dict[str, str] | os._Environ[str]) -> str:
    return (
        environ.get("CLOUD_URL") or environ.get("NEXT_PUBLIC_API_URL") or PRODUCTION_API
    )


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check every OpenPath dependency and print ok or FAIL for each."
    )
    parser.add_argument(
        "--api", default=default_api(os.environ), help="API base URL (no slash)"
    )
    parser.add_argument("--web", default=PRODUCTION_WEB, help="web app URL")
    parser.add_argument("--mode", choices=("live", "replay"), default="live")
    parser.add_argument(
        "--only",
        default=",".join(CHECKS),
        help=f"comma-separated checks to run: {','.join(CHECKS)}",
    )
    parser.add_argument(
        "--wake-s",
        type=float,
        default=DEFAULT_WAKE_S,
        help="how long the first /health call may wait for a sleeping instance",
    )
    parser.add_argument("--no-colour", action="store_true", help="plain text only")
    args = parser.parse_args(argv)
    names = [n.strip() for n in args.only.split(",") if n.strip()]
    unknown = sorted(set(names) - set(CHECKS))
    if unknown:
        parser.error(f"unknown checks: {', '.join(unknown)}")
    args.names = names
    args.api = args.api.rstrip("/")
    args.web = args.web.rstrip("/")
    return args


def main(
    argv: Sequence[str] | None = None,
    client: httpx.Client | None = None,
    ws_first_message: Callable[[str, float], str] = websocket_first_message,
) -> int:
    args = parse_args(argv)
    colour = not args.no_colour and sys.stdout.isatty() and "NO_COLOR" not in os.environ
    in_actions = os.environ.get("GITHUB_ACTIONS") == "true"
    own_client = client is None
    http = client or httpx.Client(
        follow_redirects=True, headers={"User-Agent": "openpath-demo-check"}
    )
    started = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    print(
        f"OpenPath demo check {started}: API {args.api}, web {args.web}, "
        f"expected mode {args.mode}"
    )
    try:
        ctx = Context(
            api=args.api,
            web=args.web,
            mode=args.mode,
            client=http,
            ws_first_message=ws_first_message,
            wake_s=args.wake_s,
            profiles=load_profiles(),
        )
        results = run_checks(ctx, args.names)
    finally:
        if own_client:
            http.close()
    for result in results:
        print(format_line(result, colour))
        if in_actions and result.status == "FAIL":
            print(f"::error::{result.name}: {result.detail}")
    print(summary(results))
    return 1 if any(r.status == "FAIL" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
