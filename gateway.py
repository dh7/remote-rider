"""Public gateway for rr.sb39.xyz: verify the Cloudflare Access JWT, then proxy.

See docs/superpowers/specs/2026-10-21-public-access-design.md. It listens on
127.0.0.1 only and cloudflared is its only client:

  /m/<tailnet-ip>/<port>/...  -> http://<tailnet-ip>:<port>/...  (HTTP, SSE, WebSocket)
  everything else             -> the local hub (RR_HUB_URL)

Run with: uvicorn --factory gateway:create_app_from_env --host 127.0.0.1 --port 7010
"""
from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlsplit

import httpx
import websockets
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket

from gateway_auth import AccessVerifier, AuthError

log = logging.getLogger("rr.gateway")
HERE = Path(__file__).resolve().parent
TAILNET = ipaddress.ip_network("100.64.0.0/10")
METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_M_RE = re.compile(rb"^/m/([^/]+)/([^/]+)(/.*)?$")
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
       "transfer-encoding", "upgrade", "host", "content-length"}
# Never forwarded to backends: credentials, client-supplied forwarding info, our own markers.
DROP_REQ = HOP | {"cookie", "authorization", "x-real-ip", "true-client-ip", "cdn-loop", "forwarded",
                  "x-rr-via-gateway", "origin", "referer"}
DROP_RESP = (HOP - {"content-length", "transfer-encoding"}) | {"server", "set-cookie", "x-powered-by"}
WS_HANDSHAKE = {"sec-websocket-key", "sec-websocket-version", "sec-websocket-extensions", "sec-websocket-protocol"}
SECURITY_HEADERS = {
    "cache-control": "private, no-store",
    "strict-transport-security": "max-age=31536000",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "content-security-policy": "frame-ancestors 'self'",
}


def _deny(status: int = 403) -> Response:
    return Response(b"", status_code=status, headers=SECURITY_HEADERS)


def load_inventory(root: Path = HERE) -> set[str]:
    """Tailnet addresses the gateway may reach: machines.json IPs + session machine hosts."""
    hosts: set[str] = set()
    for name in ("machines.json", "sessions.json"):
        try:
            rows = json.loads((root / name).read_text())
        except Exception:
            continue
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            machine = row.get("machine") if isinstance(row.get("machine"), dict) else {}
            for value in (row.get("ip"), machine.get("host"), row.get("machine_host")):
                try:
                    if value and ipaddress.ip_address(str(value)) in TAILNET:
                        hosts.add(str(value))
                except ValueError:
                    continue
    return hosts


def _upstream_headers(headers, prefix: str, drop: set[str] = frozenset()) -> list[tuple[str, str]]:
    out = []
    for key, value in headers.items():
        k = key.lower()
        if k in DROP_REQ or k in drop or k.startswith(("cf-", "x-forwarded-")):
            continue
        out.append((key, value))
    out += [("x-forwarded-proto", "https"), ("x-rr-via-gateway", "1")]
    if prefix:
        out.append(("x-forwarded-prefix", prefix))
    return out


def create_app(verifier: AccessVerifier, public_origin: str, hub_url: str,
               inventory: Callable[[], set[str]], transport: httpx.AsyncBaseTransport | None = None) -> Starlette:
    client = httpx.AsyncClient(transport=transport, follow_redirects=False,
                               timeout=httpx.Timeout(30.0, read=None))
    hub = urlsplit(hub_url)

    def authorized(headers) -> bool:
        try:
            verifier.verify(headers.get("cf-access-jwt-assertion"))
            return True
        except AuthError:
            return False

    def resolve(raw_path: bytes):
        """(base_url, prefix, upstream_path | None) for an allowed target, else None.

        upstream_path is None for a bare /m/<host>/<port> (caller redirects to add '/').
        """
        m = _M_RE.match(raw_path)
        if not m:
            if not raw_path.startswith(b"/m/"):
                return f"{hub.scheme}://{hub.netloc}", "", raw_path
            m = re.match(rb"^/m/([^/]+)/([^/]+)$", raw_path)
            if not m:
                return None
        host, port_raw, rest = m.group(1).decode("latin-1"), m.group(2), m.group(3) if m.lastindex == 3 else None
        if not port_raw.isdigit() or not (1024 <= int(port_raw) <= 65535):
            return None
        if host not in inventory():
            return None
        if rest is not None:
            decoded = unquote(rest.decode("latin-1"))
            if any(seg in ("..", ".") for seg in decoded.split("/")) or any(ord(ch) < 32 for ch in decoded):
                return None
        port = int(port_raw)
        return f"http://{host}:{port}", f"/m/{host}/{port}", rest

    async def http_proxy(request: Request) -> Response:
        started = time.monotonic()
        path_for_log = request.url.path
        if not authorized(request.headers):
            return _deny()
        if request.method not in SAFE_METHODS and request.headers.get("origin") != public_origin:
            return _deny()
        target = resolve(request.scope.get("raw_path") or request.url.path.encode())
        if target is None:
            return _deny(404)
        base, prefix, path = target
        if path is None:
            return Response(b"", status_code=308, headers={**SECURITY_HEADERS, "location": f"{prefix}/"})
        query = request.scope.get("query_string", b"")
        url = base + path.decode("latin-1") + ("?" + query.decode("latin-1") if query else "")
        has_body = "content-length" in request.headers or "transfer-encoding" in request.headers
        upstream_req = client.build_request(
            request.method, httpx.URL(url),
            headers=_upstream_headers(request.headers, prefix),
            content=request.stream() if has_body else None,
        )
        try:
            upstream = await client.send(upstream_req, stream=True)
        except httpx.HTTPError:
            log.info("%s %s 502 %.0fms", request.method, path_for_log, (time.monotonic() - started) * 1000)
            return _deny(502)
        headers = [(k, v) for k, v in upstream.headers.multi_items() if k.lower() not in DROP_RESP]
        location = upstream.headers.get("location")
        if location and prefix and location.startswith(base):
            headers = [(k, v) for k, v in headers if k.lower() != "location"]
            headers.append(("location", prefix + (location[len(base):] or "/")))
        headers = [(k, v) for k, v in headers if k.lower() not in SECURITY_HEADERS] + list(SECURITY_HEADERS.items())
        log.info("%s %s %s %.0fms", request.method, path_for_log, upstream.status_code,
                 (time.monotonic() - started) * 1000)
        response = StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code,
                                     background=BackgroundTask(upstream.aclose))
        response.raw_headers = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers]
        return response

    async def ws_proxy(websocket: WebSocket) -> None:
        if not authorized(websocket.headers) or websocket.headers.get("origin") != public_origin:
            await websocket.close(code=1008)
            return
        target = resolve(websocket.scope.get("raw_path") or websocket.url.path.encode())
        if target is None or target[2] is None:
            await websocket.close(code=1008)
            return
        base, prefix, path = target
        query = websocket.scope.get("query_string", b"").decode("latin-1")
        url = "ws" + base[len("http"):] + path.decode("latin-1") + (f"?{query}" if query else "")
        try:
            upstream = await websockets.connect(
                url,
                subprotocols=websocket.scope.get("subprotocols") or None,
                additional_headers=_upstream_headers(websocket.headers, prefix, drop=WS_HANDSHAKE),
                open_timeout=10, max_size=None, compression=None,
            )
        except Exception:
            log.info("WS %s upstream failed", websocket.url.path)
            await websocket.close(code=1011)
            return
        await websocket.accept(subprotocol=upstream.subprotocol)
        log.info("WS %s open", websocket.url.path)

        async def client_to_upstream():
            while True:
                msg = await websocket.receive()
                if msg["type"] == "websocket.disconnect":
                    return
                if msg.get("bytes") is not None:
                    await upstream.send(msg["bytes"])
                elif msg.get("text") is not None:
                    await upstream.send(msg["text"])

        async def upstream_to_client():
            async for data in upstream:
                if isinstance(data, bytes):
                    await websocket.send_bytes(data)
                else:
                    await websocket.send_text(data)

        tasks = [asyncio.create_task(client_to_upstream()), asyncio.create_task(upstream_to_client())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await upstream.close()
            try:
                await websocket.close()
            except Exception:
                pass
            log.info("WS %s closed", websocket.url.path)

    routes = [
        WebSocketRoute("/{rest:path}", ws_proxy),
        Route("/{rest:path}", http_proxy, methods=METHODS),
    ]
    @contextlib.asynccontextmanager
    async def lifespan(app):
        yield
        await client.aclose()

    return Starlette(routes=routes, lifespan=lifespan)


def create_app_from_env() -> Starlette:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    missing = [k for k in ("CF_TEAM_DOMAIN", "CF_ACCESS_AUD", "ALLOWED_EMAIL") if not os.getenv(k)]
    if missing:
        raise SystemExit(f"gateway: missing {', '.join(missing)} (see ~/.config/remote-rider/gateway.env)")
    verifier = AccessVerifier(os.environ["CF_TEAM_DOMAIN"], os.environ["CF_ACCESS_AUD"], os.environ["ALLOWED_EMAIL"])
    return create_app(
        verifier,
        public_origin=os.getenv("RR_PUBLIC_ORIGIN", "https://rr.sb39.xyz"),
        hub_url=os.getenv("RR_HUB_URL", "http://100.119.43.10:7000"),
        inventory=load_inventory,
    )
