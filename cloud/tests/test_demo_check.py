"""OP-402: scripts/demo_check.py. No network: a mock transport plays the API and web app.

The last test runs the read-only API checks against the real app in replay mode
(``make dev``'s fixtures) through FastAPI's TestClient.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
API = "https://api.example.test"
WEB = "https://web.example.test"
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def load_check() -> ModuleType:
    """Import scripts/demo_check.py by path (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location(
        "openpath_demo_check", ROOT / "scripts" / "demo_check.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


demo = load_check()


def route_body(routes: int = 2, report: bool = True) -> dict[str, Any]:
    route: dict[str, Any] = {"rank": 1, "distance_m": 412.0, "eta_min": 6.9}
    if report:
        route["report"] = {"on_route": 1, "nearby": 2, "avoided": 0, "groups": []}
    return {"routes": [route] * routes, "warnings": []}


class FakeSite:
    """The API and the web app, answering from ``self.bodies``; records each request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.bodies: dict[str, Any] = {
            "/health": {"status": "ok", "version": COMMIT, "mode": "live"},
            "/api/status": {
                "mode": "live",
                "scheduler_running": True,
                "sources": [
                    {"source": "lrt", "stale": False, "age_s": 30.0},
                    {"source": "weather", "stale": False, "age_s": 290.0},
                ],
            },
            "/api/cameras": {
                "features": [
                    {"properties": {"age_s": 120.0}},
                    {"properties": {"age_s": 4000.0}},
                ]
            },
            "/api/route": route_body(),
            "/api/search": [{"label": "Churchill Station", "kind": "station"}],
        }
        self.status: dict[str, int] = {}
        self.html = (
            '<html><head><title>OpenPath</title><script src="/_next/static/chunks/'
            'main-1.js"></script><script src="/_next/static/chunks/app-2.js">'
            "</script></head><body>OpenPath</body></html>"
        )
        self.scripts = {
            "/_next/static/chunks/main-1.js": "console.log(1)",
            "/_next/static/chunks/app-2.js": f'const apiBase="{API}";',
        }
        self.allow_origin: str | None = WEB

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.url.host == "web.example.test":
            if path in self.scripts:
                return httpx.Response(200, text=self.scripts[path])
            return httpx.Response(self.status.get("web", 200), text=self.html)
        if request.method == "OPTIONS":
            if self.allow_origin is None:
                return httpx.Response(400, text="Disallowed CORS origin")
            return httpx.Response(
                200, headers={"access-control-allow-origin": self.allow_origin}
            )
        if path in self.status:
            return httpx.Response(self.status[path], text="broken")
        return httpx.Response(200, json=self.bodies[path])


def hello(url: str, timeout_s: float) -> str:
    return json.dumps({"type": "hello", "heartbeat_s": 25.0})


def run(
    site: FakeSite,
    *args: str,
    ws: Callable[[str, float], str] = hello,
) -> int:
    client = httpx.Client(transport=httpx.MockTransport(site))
    argv = ["--api", API, "--web", WEB, *args]
    return demo.main(argv, client=client, ws_first_message=ws)


def lines(out: str) -> list[str]:
    return [line for line in out.splitlines() if line[:4] in ("ok  ", "warn", "FAIL")]


@pytest.fixture(autouse=True)
def plain_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


# ------------------------------------------------------------------ all good


def test_every_check_passes(capsys: pytest.CaptureFixture[str]) -> None:
    site = FakeSite()
    assert run(site) == 0
    out = capsys.readouterr().out
    checks = lines(out)
    names = [line.split(":", 1)[0][6:] for line in checks]
    assert names == [
        "API /health",
        "API /api/status",
        "API /api/cameras",
        "API /api/route wheelchair",
        "API /api/route walker_cane",
        "API /api/route low_vision",
        "API /api/route standard",
        "API /api/search?q=churchill",
        "API WS /ws",
        "web app",
        "web app uses this API",
        "CORS preflight",
    ]
    assert all(line.startswith("ok  ") for line in checks)
    assert "\033[" not in out  # not a terminal: plain text
    assert out.splitlines()[-1] == "12 ok, 0 warn, 0 FAIL"
    assert "1 of 2 readings under 600 s old; newest 120 s" in out
    assert "app-2.js" in out


def test_only_read_only_requests_are_sent() -> None:
    site = FakeSite()
    run(site)
    assert {r.method for r in site.requests} == {"GET", "OPTIONS"}
    preflight = next(r for r in site.requests if r.method == "OPTIONS")
    assert preflight.headers["origin"] == WEB
    assert preflight.headers["access-control-request-method"] == "GET"
    route = next(r for r in site.requests if r.url.path == "/api/route")
    assert route.url.params["from"] == demo.TRIP_FROM
    assert route.url.params["to"] == demo.TRIP_TO


def test_only_runs_the_named_checks(capsys: pytest.CaptureFixture[str]) -> None:
    site = FakeSite()
    assert run(site, "--only", "health,status,web") == 0
    checks = lines(capsys.readouterr().out)
    assert [line.split(":", 1)[0][6:] for line in checks] == [
        "API /health",
        "API /api/status",
        "web app",
    ]
    assert {r.url.path for r in site.requests} == {"/health", "/api/status", "/"}


def test_unknown_check_name_is_an_error() -> None:
    with pytest.raises(SystemExit):
        demo.parse_args(["--only", "health,teapot"])


# ------------------------------------------------------------------- health


def test_dev_version_fails_on_a_deployed_api(
    capsys: pytest.CaptureFixture[str],
) -> None:
    site = FakeSite()
    site.bodies["/health"]["version"] = "dev"
    assert run(site, "--only", "health") == 1
    assert "'dev' is not a 40-character commit" in capsys.readouterr().out


def test_dev_version_is_a_warning_on_localhost(
    capsys: pytest.CaptureFixture[str],
) -> None:
    site = FakeSite()
    site.bodies["/health"] = {"status": "ok", "version": "dev", "mode": "replay"}
    client = httpx.Client(transport=httpx.MockTransport(site))
    argv = ["--api", "http://localhost:8741", "--mode", "replay", "--only", "health"]
    assert demo.main(argv, client=client) == 0
    [line] = lines(capsys.readouterr().out)
    assert line.startswith("warn") and "a local run" in line


def test_wrong_mode_fails(capsys: pytest.CaptureFixture[str]) -> None:
    site = FakeSite()
    assert run(site, "--mode", "replay", "--only", "health") == 1
    assert "mode is 'live', expected 'replay'" in capsys.readouterr().out


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def context(site: Callable[[httpx.Request], httpx.Response], **kw: Any) -> Any:
    clock = FakeClock()
    return demo.Context(
        api=API,
        web=WEB,
        mode="live",
        client=httpx.Client(transport=httpx.MockTransport(site)),
        ws_first_message=hello,
        sleep=clock.sleep,
        clock=clock,
        **kw,
    )


def test_health_waits_for_a_sleeping_instance() -> None:
    calls = {"n": 0}

    def waking(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectTimeout("asleep", request=request)
        return httpx.Response(
            200, json={"status": "ok", "version": COMMIT, "mode": "live"}
        )

    ctx = context(waking, wake_s=150)
    [result] = demo.check_health(ctx)
    assert result.status == "ok"
    assert "after 3 tries" in result.detail
    assert ctx.notes["health_attempts"] == 3


def test_health_gives_up_after_the_wake_time() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable")

    [result] = demo.check_health(context(down, wake_s=20))
    assert result.status == "FAIL"
    assert result.detail.startswith("HTTP 503 after 5 tries in 20 s")


# ---------------------------------------------------------------- the others


def test_stale_source_and_stopped_scheduler_fail(
    capsys: pytest.CaptureFixture[str],
) -> None:
    site = FakeSite()
    site.bodies["/api/status"]["scheduler_running"] = False
    site.bodies["/api/status"]["sources"][1] = {
        "source": "weather",
        "stale": True,
        "consecutive_failures": 3,
        "last_error": "ReadTimeout",
    }
    assert run(site, "--only", "status") == 1
    [line] = lines(capsys.readouterr().out)
    assert line.startswith("FAIL")
    assert "the scheduler is not running" in line
    assert "weather stale (3 failures, last error ReadTimeout)" in line


def test_no_sources_fails() -> None:
    site = FakeSite()
    site.bodies["/api/status"]["sources"] = []
    assert run(site, "--only", "status") == 1


def test_old_camera_readings_fail_live_and_warn_in_replay(
    capsys: pytest.CaptureFixture[str],
) -> None:
    site = FakeSite()
    site.bodies["/api/cameras"] = {"features": [{"properties": {"age_s": 900.0}}]}
    assert run(site, "--only", "cameras") == 1
    assert "none under 600 s old (newest 900 s)" in capsys.readouterr().out
    site.bodies["/api/cameras"] = {"features": []}
    assert run(site, "--mode", "replay", "--only", "cameras") == 0
    [line] = lines(capsys.readouterr().out)
    assert line.startswith("warn") and "no camera readings" in line


def test_route_without_routes_or_report_fails(
    capsys: pytest.CaptureFixture[str],
) -> None:
    site = FakeSite()
    site.bodies["/api/route"] = route_body(routes=0)
    assert run(site, "--only", "route") == 1
    assert all(line.startswith("FAIL") for line in lines(capsys.readouterr().out))
    site.bodies["/api/route"] = route_body(report=False)
    assert run(site, "--only", "route") == 1
    assert "the best route has no report" in capsys.readouterr().out
    site.status["/api/route"] = 503
    assert run(site, "--only", "route") == 1
    assert "HTTP 503" in capsys.readouterr().out


def test_empty_search_fails() -> None:
    site = FakeSite()
    site.bodies["/api/search"] = []
    assert run(site, "--only", "search") == 1


def test_websocket_without_hello_fails(capsys: pytest.CaptureFixture[str]) -> None:
    def timeout(url: str, timeout_s: float) -> str:
        raise TimeoutError("timed out during opening handshake")

    assert run(FakeSite(), "--only", "ws", ws=timeout) == 1
    assert "no event within 5 s (TimeoutError" in capsys.readouterr().out
    assert run(FakeSite(), "--only", "ws", ws=lambda u, t: '{"type": "heartbeat"}') == 1
    assert "expected 'hello'" in capsys.readouterr().out


def test_websocket_url_follows_the_api_scheme() -> None:
    assert demo.ws_url("https://openpath-api-w6ql.onrender.com") == (
        "wss://openpath-api-w6ql.onrender.com/ws"
    )
    assert demo.ws_url("http://localhost:8741") == "ws://localhost:8741/ws"


def test_web_app_must_answer_and_say_openpath(
    capsys: pytest.CaptureFixture[str],
) -> None:
    site = FakeSite()
    site.html = "<html>Something else</html>"
    assert run(site, "--only", "web") == 1
    assert "does not say OpenPath" in capsys.readouterr().out
    site.status["web"] = 500
    assert run(site, "--only", "web") == 1


def test_web_app_must_name_this_api(capsys: pytest.CaptureFixture[str]) -> None:
    site = FakeSite()
    site.scripts["/_next/static/chunks/app-2.js"] = (
        'const apiBase="https://other-api.example.test";'
    )
    assert run(site, "--only", "web,webapi") == 1
    out = capsys.readouterr().out
    assert "none of the page's 2 scripts names https://api.example.test" in out


def test_replay_badge_line_in_replay_mode(capsys: pytest.CaptureFixture[str]) -> None:
    site = FakeSite()
    site.bodies["/health"]["mode"] = "replay"
    assert run(site, "--mode", "replay", "--only", "health,webapi") == 0
    assert "the Replay badge reads mode replay from it" in capsys.readouterr().out


def test_refused_cors_preflight_fails(capsys: pytest.CaptureFixture[str]) -> None:
    site = FakeSite()
    site.allow_origin = None
    assert run(site, "--only", "cors") == 1
    assert "add it to CORS_ORIGINS on Render" in capsys.readouterr().out


def test_actions_get_error_annotations(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    site = FakeSite()
    site.bodies["/api/search"] = []
    assert run(site, "--only", "search") == 1
    assert "::error::API /api/search?q=churchill: no results" in capsys.readouterr().out


def test_colour_only_wraps_the_word() -> None:
    result = demo.Result("FAIL", "API /health", "timed out")
    assert demo.format_line(result, colour=False) == "FAIL  API /health: timed out"
    coloured = demo.format_line(result, colour=True)
    assert coloured.startswith("\033[31mFAIL\033[0m")
    assert coloured.endswith("API /health: timed out")


def test_api_default_comes_from_the_environment() -> None:
    assert demo.default_api({}) == demo.PRODUCTION_API
    assert demo.default_api({"NEXT_PUBLIC_API_URL": "http://b"}) == "http://b"
    assert demo.default_api({"CLOUD_URL": "http://a", "NEXT_PUBLIC_API_URL": "b"}) == (
        "http://a"
    )


def test_profiles_come_from_the_shared_config() -> None:
    profiles = json.loads((ROOT / "config" / "profiles.json").read_text())["profiles"]
    assert demo.load_profiles() == tuple(profiles)
    assert demo.load_profiles(ROOT / "missing.json") == demo.DEFAULT_PROFILES


# ----------------------------------------------------- against the real app


# The checks pass a timeout per request, which TestClient ignores with a warning.
@pytest.mark.filterwarnings("ignore:You should not use the 'timeout' argument")
def test_api_checks_pass_on_the_replay_app(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``make dev``'s API (replay fixtures, scheduler on), checked in-process."""
    from cloud.app.main import create_app

    monkeypatch.setenv("DATA_MODE", "replay")
    monkeypatch.setenv("REPLAY_SNAPSHOT", str(FIXTURES))
    monkeypatch.setenv("SCHEDULER", "on")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'demo.db'}")
    for name in ("REPLAY_AT", "REPLAY_CLOCK", "WEATHER_REPLAY", "RENDER_GIT_COMMIT"):
        monkeypatch.delenv(name, raising=False)
    app = create_app()
    with TestClient(app) as client:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            sources = client.get("/api/status").json()["sources"]
            if sources and not any(s["stale"] for s in sources):
                break
            time.sleep(0.2)

        def first_message(url: str, timeout_s: float) -> str:
            assert url == "ws://testserver/ws"
            with client.websocket_connect("/ws") as socket:
                return socket.receive_text()

        ctx = demo.Context(
            api="http://testserver",
            web=WEB,
            mode="replay",
            client=client,
            ws_first_message=first_message,
            profiles=demo.load_profiles(),
        )
        results = demo.run_checks(
            ctx, ("health", "status", "cameras", "route", "search", "ws")
        )
    by_name = {r.name: r for r in results}
    # TestClient is not localhost and sets no commit, so only the version is off.
    assert by_name["API /health"].status == "FAIL"
    assert by_name["API /health"].detail.endswith(
        "version 'dev' is not a 40-character commit"
    )
    assert by_name["API /api/cameras"].status == "warn"  # no readings in the fixtures
    others = [r for r in results if r.name not in ("API /health", "API /api/cameras")]
    assert len(others) == 7
    assert all(r.status == "ok" for r in others), others
