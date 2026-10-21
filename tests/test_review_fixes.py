import asyncio
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

import gateway
from gateway_auth import AccessVerifier
from storage import _normalize_control_session
from tests.jwt_helpers import AUD, EMAIL, TEAM, FakeJwks, make_token

ORIGIN = "https://rr.sb39.xyz"
HOST = "100.118.187.64"


def _gw(handler, jwks=None):
    app = gateway.create_app(AccessVerifier(TEAM, AUD, EMAIL, jwk_client=jwks or FakeJwks()),
                             public_origin=ORIGIN, hub_url="http://100.119.43.10:7000",
                             inventory=lambda: {HOST}, transport=httpx.MockTransport(handler))
    return TestClient(app, base_url=ORIGIN)


# I1: backend CSP must be kept, frame-ancestors added
def test_backend_csp_is_preserved():
    def h(request):
        return httpx.Response(200, headers={"content-security-policy": "sandbox"}, stream=httpx.ByteStream(b"x"))
    r = _gw(h).get(f"/m/{HOST}/8999/", headers={"Cf-Access-Jwt-Assertion": make_token()})
    csp = r.headers["content-security-policy"]
    assert "sandbox" in csp and "frame-ancestors 'self'" in csp


# I1: workspace files must never execute on the hub origin
@pytest.mark.parametrize("name,inline", [("x.html", False), ("x.svg", False), ("x.png", True), ("x.mp4", True)])
def test_files_raw_is_sandboxed(tmp_path, monkeypatch, name, inline):
    import main
    import workspace_builtins as wb
    (tmp_path / name).write_bytes(b"<script>alert(1)</script>")
    monkeypatch.setattr(wb, "_project_for", lambda s: tmp_path)
    r = TestClient(main.app).get(f"/workspaces/s/files/raw?path={name}")
    assert r.status_code == 200
    assert "sandbox" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers.get("content-disposition", "").startswith("attachment") is (not inline)


# M6: request bodies keep their Content-Length (no forced chunked encoding)
def test_post_keeps_content_length():
    seen = {}

    def h(request):
        seen["cl"] = request.headers.get("content-length")
        seen["te"] = request.headers.get("transfer-encoding")
        return httpx.Response(200, stream=httpx.ByteStream(b""))
    _gw(h).post("/sessions", content=b'{"a":1}',
                headers={"Cf-Access-Jwt-Assertion": make_token(), "Origin": ORIGIN})
    assert seen == {"cl": "7", "te": None}


# M5: only Files-like tabs on 8080 are migrated
def test_non_files_8080_tab_untouched():
    s = _normalize_control_session({"name": "w", "machine": {"host": "100.118.187.64"},
                                    "tabs": [{"label": "Jenkins", "port": 8080, "path": "/"}]})
    assert s["tabs"] == [{"id": "tab-jenkins", "label": "Jenkins", "port": 8080, "path": "/"}]


# M2: a slow JWKS fetch must not freeze other connections (verify runs off the event loop)
def test_slow_jwks_does_not_block_event_loop():
    class SlowJwks(FakeJwks):
        def get_signing_key_from_jwt(self, token):
            if jwt_slow.is_set():
                time.sleep(1.0)
            return super().get_signing_key_from_jwt(token)

    jwt_slow = threading.Event()

    def h(request):
        return httpx.Response(200, stream=httpx.ByteStream(b"ok"))

    app = gateway.create_app(AccessVerifier(TEAM, AUD, EMAIL, jwk_client=SlowJwks()),
                             public_origin=ORIGIN, hub_url="http://100.119.43.10:7000",
                             inventory=lambda: {HOST}, transport=httpx.MockTransport(h))

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as c:
            jwt_slow.set()
            slow = asyncio.create_task(c.get("/a", headers={"Cf-Access-Jwt-Assertion": make_token()}))
            await asyncio.sleep(0.05)
            ticks = 0
            start = time.monotonic()
            while not slow.done():  # the loop must keep ticking while verify sleeps
                await asyncio.sleep(0.01)
                ticks += 1
            await slow
            return ticks, time.monotonic() - start

    ticks, elapsed = asyncio.run(run())
    assert ticks > 20, (ticks, elapsed)
