"""Security headers on every API response (OP-401).

One ASGI middleware, the outermost in ``create_app``, so it also decorates CORS
preflight answers, 404s and validation errors:

- ``X-Content-Type-Options: nosniff``: a browser never guesses a type.
- ``Referrer-Policy: no-referrer``: nothing served here sends a referrer anywhere.
- ``Permissions-Policy: camera=(), microphone=(), geolocation=()``: nothing on this
  origin may use a camera, a microphone or the location.
- ``Content-Security-Policy: default-src 'none'; frame-ancestors 'none'``: a response
  is data, never a page, so nothing loads and no site may frame it. The interactive
  docs (``/docs``, ``/redoc``) are the one exception: they are pages that load
  Swagger UI and ReDoc from a CDN, so they get every header but this one.
- ``X-Frame-Options: DENY``: the same, for browsers before CSP level 2.
- ``Cache-Control: no-store`` on the answer to every write (anything but GET, HEAD
  and OPTIONS), so a report or a saved route never sits in a shared cache. Reads
  keep whatever the route set.
- ``Strict-Transport-Security``, only when the request arrived over HTTPS (Render
  terminates TLS and says so in ``X-Forwarded-Proto``), so a local ``http://`` run
  never tells a browser to insist on HTTPS for localhost.

A header a route set itself is kept. WebSocket connections pass through untouched.
"""

from __future__ import annotations

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

HSTS = "max-age=31536000"  # one year
CSP = "default-src 'none'; frame-ancestors 'none'"
PERMISSIONS = "camera=(), microphone=(), geolocation=()"
DOC_PAGES = frozenset({"/docs", "/redoc", "/docs/oauth2-redirect"})
READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

BASE_HEADERS: tuple[tuple[str, str], ...] = (
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("Permissions-Policy", PERMISSIONS),
    ("X-Frame-Options", "DENY"),
)


def is_https(scope: Scope) -> bool:
    """Whether the client used HTTPS: the socket says so, or the proxy in front does."""
    if scope.get("scheme") == "https":
        return True
    proto = Headers(scope=scope).get("x-forwarded-proto", "")
    return proto.split(",", 1)[0].strip().lower() == "https"


def response_headers(method: str, path: str, https: bool) -> list[tuple[str, str]]:
    """The headers to add to one response (those the route did not set itself)."""
    headers = list(BASE_HEADERS)
    if path not in DOC_PAGES:
        headers.append(("Content-Security-Policy", CSP))
    if method.upper() not in READ_METHODS:
        headers.append(("Cache-Control", "no-store"))
    if https:
        headers.append(("Strict-Transport-Security", HSTS))
    return headers


class SecurityHeadersMiddleware:
    """Adds ``response_headers`` to every HTTP response; leaves WebSockets alone."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        extra = response_headers(scope["method"], scope["path"], is_https(scope))

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                raw = list(message.get("headers") or [])
                message["headers"] = raw
                headers = MutableHeaders(raw=raw)
                for name, value in extra:
                    if name not in headers:
                        headers.append(name, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)
