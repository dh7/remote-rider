"""Request-scoped view of the headers gateway.py sets (see the public-access spec).

Only values matching the gateway's own format are honoured, so a tailnet client
sending a forged X-Forwarded-Prefix cannot inject markup into built-in pages.
"""
from __future__ import annotations

import contextvars
import re

_PREFIX_RE = re.compile(r"^/m/[0-9A-Za-z.:-]{1,64}/[0-9]{1,5}$")
_prefix: contextvars.ContextVar[str] = contextvars.ContextVar("rr_prefix", default="")
_via: contextvars.ContextVar[bool] = contextvars.ContextVar("rr_via_gateway", default=False)


def prefix() -> str:
    return _prefix.get()


def via_gateway() -> bool:
    return _via.get()


class ForwardedMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        raw = headers.get("x-forwarded-prefix", "")
        t1 = _prefix.set(raw if _PREFIX_RE.match(raw) else "")
        t2 = _via.set(headers.get("x-rr-via-gateway") == "1")
        try:
            await self.app(scope, receive, send)
        finally:
            _prefix.reset(t1)
            _via.reset(t2)
