import json
import threading

import httpx
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from websockets.sync.server import serve

import gateway
from gateway_auth import AccessVerifier
from tests.jwt_helpers import AUD, EMAIL, TEAM, FakeJwks, make_token

ORIGIN = "https://rr.sb39.xyz"
HOST = "100.118.187.64"
seen = {}


def upstream(request: httpx.Request) -> httpx.Response:
    seen["req"] = request
    seen["body"] = request.read()
    if request.url.path.endswith("/redirect"):
        return httpx.Response(302, headers={"location": f"http://{request.url.host}:{request.url.port}/next"},
                              stream=httpx.ByteStream(b""))
    body = f"{request.url.host}:{request.url.port}{request.url.raw_path.decode()}".encode()
    # stream= (not text=) so the response arrives unread, like a real upstream
    return httpx.Response(200, headers={"set-cookie": "a=b", "server": "uvicorn", "content-type": "text/plain"},
                          stream=httpx.ByteStream(body))


def client(inventory=frozenset({HOST}), transport=None):
    app = gateway.create_app(
        AccessVerifier(TEAM, AUD, EMAIL, jwk_client=FakeJwks()),
        public_origin=ORIGIN, hub_url="http://100.119.43.10:7000",
        inventory=lambda: set(inventory), transport=transport or httpx.MockTransport(upstream),
    )
    return TestClient(app, base_url=ORIGIN)


def auth(**extra):
    return {"Cf-Access-Jwt-Assertion": make_token(), **extra}


@pytest.mark.parametrize("path", ["/", "/openapi.json", f"/m/{HOST}/7681/", "/sessions"])
def test_no_token_403(path):
    seen.clear()
    r = client().get(path)
    assert r.status_code == 403 and r.text == ""
    assert "req" not in seen


def test_cookie_alone_is_not_enough():
    c = client()
    c.cookies.set("CF_Authorization", make_token())
    r = c.get("/")
    assert r.status_code == 403


def test_hub_route():
    r = client().get("/sessions?x=1", headers=auth())
    assert r.status_code == 200
    assert r.text == "100.119.43.10:7000/sessions?x=1"
    assert seen["req"].headers["x-rr-via-gateway"] == "1"
    assert "x-forwarded-prefix" not in seen["req"].headers


def test_machine_route_and_prefix():
    r = client().get(f"/m/{HOST}/7000/workspaces/a%20b/files?path=x", headers=auth())
    assert r.text == f"{HOST}:7000/workspaces/a%20b/files?path=x"
    assert seen["req"].headers["x-forwarded-prefix"] == f"/m/{HOST}/7000"


def test_machine_root_redirects_to_trailing_slash():
    r = client().get(f"/m/{HOST}/7681", headers=auth(), follow_redirects=False)
    assert r.status_code in (301, 307, 308) and r.headers["location"] == f"/m/{HOST}/7681/"


@pytest.mark.parametrize("path", [
    "/m/10.0.0.1/8080/", "/m/169.254.169.254/1024/", "/m/100.64.0.9/7681/",
    f"/m/{HOST}/22/", f"/m/{HOST}/70000/", f"/m/{HOST}/abc/",
    f"/m/{HOST}/7000/%2e%2e/x", f"/m/{HOST}/7000/a/%2E%2E/b", f"/m/{HOST}/7000/a/%00",
    f"/m/{HOST}@evil.com/7000/", "/m/evil.com/7000/",
])
def test_routing_rejections(path):
    seen.clear()
    assert client().get(path, headers=auth()).status_code in (400, 404)
    assert "req" not in seen


def test_header_hygiene():
    r = client().get(f"/m/{HOST}/7681/", headers=auth(**{
        "Cookie": "CF_Authorization=t; other=1", "Authorization": "Bearer x",
        "X-Forwarded-For": "1.2.3.4", "Cf-Connecting-Ip": "1.2.3.4", "X-Forwarded-Prefix": "/m/evil/1"}))
    sent = seen["req"].headers
    for h in ("cookie", "authorization", "cf-access-jwt-assertion", "x-forwarded-for", "cf-connecting-ip"):
        assert h not in sent, h
    assert sent["x-forwarded-prefix"] == f"/m/{HOST}/7681"
    assert "set-cookie" not in r.headers
    assert r.headers.get("server", "") == ""
    assert r.headers["cache-control"] == "private, no-store"
    assert "max-age=31536000" in r.headers["strict-transport-security"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["content-security-policy"] == "frame-ancestors 'self'"


def test_location_rewritten():
    r = client().get(f"/m/{HOST}/7000/redirect", headers=auth(), follow_redirects=False)
    assert r.headers["location"] == f"/m/{HOST}/7000/next"


def test_post_requires_same_origin_and_forwards_body():
    c = client()
    assert c.post("/sessions", headers=auth(), content=b"{}").status_code == 403
    assert c.post("/sessions", headers=auth(Origin="https://evil.com"), content=b"{}").status_code == 403
    r = c.post("/sessions", headers=auth(Origin=ORIGIN), content=b'{"a":1}')
    assert r.status_code == 200 and seen["body"] == b'{"a":1}'


def test_upstream_down_is_502():
    def boom(request):
        raise httpx.ConnectError("down")
    assert client(transport=httpx.MockTransport(boom)).get("/", headers=auth()).status_code == 502


def test_streaming_response_is_not_buffered():
    async def chunks():
        for i in range(3):
            yield f"data: {i}\n\n".encode()

    def sse(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=chunks())

    with client(transport=httpx.MockTransport(sse)).stream("GET", f"/m/{HOST}/8002/stream", headers=auth()) as r:
        body = b"".join(r.iter_raw())
    assert body == b"data: 0\n\ndata: 1\n\ndata: 2\n\n"
    assert r.headers["content-type"].startswith("text/event-stream")


@pytest.fixture
def ws_upstream():
    def handler(conn):
        seen["ws_headers"] = {k.lower(): v for k, v in conn.request.headers.raw_items()}
        for msg in conn:
            conn.send(msg)  # echo, preserving bytes vs text

    server = serve(handler, "127.0.0.1", 0, subprotocols=["tty"])
    port = server.socket.getsockname()[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield port
    server.shutdown()


def test_ws_proxies_binary_and_subprotocol(ws_upstream):
    c = client(inventory={"127.0.0.1"})
    with c.websocket_connect(f"/m/127.0.0.1/{ws_upstream}/ws", subprotocols=["tty"],
                             headers=auth(Origin=ORIGIN)) as ws:
        assert ws.accepted_subprotocol == "tty"
        ws.send_bytes(b"\x00ls\r")
        assert ws.receive_bytes() == b"\x00ls\r"
        ws.send_text('{"AuthToken":""}')
        assert ws.receive_text() == '{"AuthToken":""}'
    assert "cf-access-jwt-assertion" not in seen["ws_headers"]
    assert seen["ws_headers"]["x-forwarded-prefix"] == f"/m/127.0.0.1/{ws_upstream}"


@pytest.mark.parametrize("headers", [{"Origin": ORIGIN}, auth(Origin="https://evil.com"), auth()],
                         ids=["no-token", "bad-origin", "no-origin"])
def test_ws_rejected_without_auth_or_origin(ws_upstream, headers):
    c = client(inventory={"127.0.0.1"})
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect(f"/m/127.0.0.1/{ws_upstream}/ws", headers=headers):
            pass


def test_load_inventory(tmp_path):
    (tmp_path / "machines.json").write_text(json.dumps([{"ip": "100.118.187.64"}, {"ip": "192.168.1.1"}]))
    (tmp_path / "sessions.json").write_text(json.dumps([{"machine": {"host": "100.94.124.42"}}, {"machine": "x"}]))
    assert gateway.load_inventory(tmp_path) == {"100.118.187.64", "100.94.124.42"}


def test_env_factory_refuses_without_config(monkeypatch):
    for k in ("CF_TEAM_DOMAIN", "CF_ACCESS_AUD", "ALLOWED_EMAIL"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(SystemExit):
        gateway.create_app_from_env()
