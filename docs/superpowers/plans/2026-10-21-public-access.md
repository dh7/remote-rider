# Public Access (Cloudflare Tunnel + Access) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reach the full remote-rider UI at `https://rr.sb39.xyz` from any computer, behind a Google login that only `dh7777777@gmail.com` passes, with legacy `fileserver.py` retired.

**Architecture:** Cloudflare Access authenticates at the edge; a `cloudflared` tunnel on netochka forwards only to `gateway.py` on `127.0.0.1:7010`. The gateway re-verifies the Access JWT on every request, then proxies `/m/<tailnet-ip>/<port>/…` to inventory hosts (HTTP, SSE, WebSocket) and everything else to the local hub. Panel apps emit prefix-aware URLs from `X-Forwarded-Prefix`.

**Tech Stack:** Python 3 / FastAPI + Starlette, uvicorn, httpx (async proxy), websockets ≥ 13 (WS proxy), PyJWT[crypto] (RS256 verify), pytest; vanilla JS frontend; cloudflared; systemd user units.

**Spec:** `docs/superpowers/specs/2026-10-21-public-access-design.md`

## Global Constraints

- Public hostname: `rr.sb39.xyz`; public origin string: `https://rr.sb39.xyz`.
- Allowed email: `dh7777777@gmail.com` (config, not hard-coded in code).
- Gateway listens on `127.0.0.1:7010` only; never on a tailnet or public interface.
- JWT: `RS256` only, `aud` = configured AUD tag, `iss` = `https://<team>.cloudflareaccess.com`, `exp`/`nbf` enforced with 30 s leeway.
- Gateway config file: `~/.config/remote-rider/gateway.env` (mode 600) with `CF_TEAM_DOMAIN`, `CF_ACCESS_AUD`, `ALLOWED_EMAIL`; missing value → refuse to start.
- Proxy targets: tailnet addresses (100.64.0.0/10) present in `machines.json` / `sessions.json`; ports 1024–65535.
- Never log headers, cookies, tokens, bodies or query strings.
- Secrets never enter the repo; scan each diff before committing.
- Tailnet behaviour (`http://<tailnet-ip>:7000`) must be unchanged except for the fileserver retirement.
- Work happens in the worktree `~/code/worktrees/rr-public-access`; the live checkout `~/code/remote-rider` is only updated at go-live.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. A request that reaches the gateway without (or with a stale) Cf-Access JWT — e.g. JWKS endpoint unreachable at boot — must get 403, never be proxied. → Task 5 test `test_jwks_failure_denies`.
2. ttyd through the gateway: the browser offers subprotocol `tty` and sends binary frames; the proxy must negotiate the same subprotocol and pass binary both ways, or the terminal is blank. → Task 6 test `test_ws_proxies_binary_and_subprotocol`.
3. Logs SSE stream through the gateway must not be buffered (lines appear live). → Task 6 test `test_streaming_response_is_not_buffered`.
4. A legacy Files tab saved in `sessions.json` on an un-updated checkout (port 8080) must open the built-in viewer, not a dead iframe. → Task 2 test `test_legacy_files_tab_becomes_builtin`.
5. A forged `X-Forwarded-Prefix` from a tailnet client must not inject markup into built-in pages. → Task 4 test `test_invalid_prefix_is_ignored`.

---

### Task 1: Test harness + retire the fileserver backend

**Files:**
- Create: `requirements-gateway.txt`, `requirements-dev.txt`, `tests/conftest.py`, `tests/test_services.py`
- Modify: `services.py` (remove `files` entry, `fileserver` branch, `_start_files_service_local`, `_start_files_service_proxy`), `main.py` (remove `/services/files/start`, `/services/files/start/proxy`, their imports and doc lines), `start-remote.sh`, `start.sh` (stop launching fileserver; keep the stop lines)
- Delete: `fileserver.py`

**Interfaces:**
- Produces: `services._local_services_snapshot()` no longer returns a `files` service nor `kind == "fileserver"` rows; registry rows with `kind == "fileserver"` are pruned by `_cleanup_service_registry`.

- [ ] **Step 1: Dependencies and harness**

`requirements-gateway.txt`:
```
httpx>=0.27
websockets>=13
PyJWT[crypto]>=2.8
```
`requirements-dev.txt`:
```
-r requirements-gateway.txt
pytest>=8
```
`tests/conftest.py`:
```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
```
Run: `.venv/bin/pip install -r requirements-dev.txt` (create `.venv` in the worktree first with `python3 -m venv .venv && .venv/bin/pip install fastapi uvicorn aiofiles psutil`).

- [ ] **Step 2: Failing test**

`tests/test_services.py`:
```python
import services


def test_snapshot_has_no_fileserver(monkeypatch):
    rows = [
        {"kind": "fileserver", "name": "files-8081", "port": 8081, "pid": 999999},
        {"kind": "service", "name": "app", "port": 8999, "label": "App"},
    ]
    saved = {}
    monkeypatch.setattr(services, "_load_service_registry", lambda: list(rows))
    monkeypatch.setattr(services, "_save_service_registry", lambda r: saved.setdefault("rows", r))
    monkeypatch.setattr(services, "_cleanup_service_registry", services._cleanup_service_registry)
    snap = services._local_services_snapshot()
    names = {s["name"] for s in snap["services"]}
    kinds = {s.get("kind") for s in snap["services"]}
    assert "files" not in names
    assert "fileserver" not in kinds
    assert all(r.get("kind") != "fileserver" for r in saved["rows"])


def test_files_start_helpers_removed():
    assert not hasattr(services, "_start_files_service_local")
```
Run: `.venv/bin/pytest tests/test_services.py -v` → FAIL (files entry present).

- [ ] **Step 3: Implement**

In `services.py`: delete the `_service_entry("files", …)` line; in `_cleanup_service_registry` drop rows whose `kind == "fileserver"` (add `rows = [r for r in rows if r.get("kind") != "fileserver"]` at its start); delete the `if kind == "fileserver":` branch; delete `_start_files_service_local` and the proxy starter that follows it. In `main.py`: remove those imports, the two `/services/files/start*` routes and the two API-doc lines. In `start-remote.sh` / `start.sh`: delete the `uvicorn fileserver:app` launch, its pidfile write and the `FILES_PORT`/echo lines; keep `stop_pidfile fileserver` and the `stop_matching … fileserver:app` line (cleans old processes on update). `git rm fileserver.py`. Then `grep -rn "fileserver\|FILES_PORT\|files/start" --include=*.py --include=*.sh .` must only show the stop lines.

- [ ] **Step 4: Verify** — `.venv/bin/pytest tests -v` PASS; `.venv/bin/python -c "import main"` OK.

- [ ] **Step 5: Commit** — `git commit -m "retire legacy fileserver backend (built-in Files is the only file server)"`

---

### Task 2: Defaults + legacy Files tab migration

**Files:**
- Modify: `storage.py` (`_normalize_control_session`, `_default_templates`), `machines.json`, `servers.json`, `session_templates.json`, `sessions.json` (normalized once)
- Test: `tests/test_storage_migration.py`

**Interfaces:**
- Produces: `storage._builtin_files_tab(session_name: str) -> dict`; `_normalize_control_session` output never contains a tab with `port == 8080`.

- [ ] **Step 1: Failing tests**
```python
from storage import _normalize_control_session, _default_templates


def _session(tabs):
    return {"name": "gx10-proj", "machine": {"name": "gx10", "host": "100.118.187.64"}, "tabs": tabs}


def test_legacy_files_tab_becomes_builtin():
    s = _normalize_control_session(_session([
        {"id": "t1", "label": "Terminal", "service": "terminal", "port": 7681},
        {"id": "t2", "label": "Files", "service": "files", "port": 8080, "path": "/files"},
    ]))
    files = [t for t in s["tabs"] if t["label"] == "Files"]
    assert files == [{"id": "tab-files", "label": "Files", "service": "builtin", "port": 7000,
                      "path": "/workspaces/gx10-proj/files", "protocol": "http"}]


def test_legacy_files_tab_deduped_against_builtin():
    builtin = {"id": "tab-files", "label": "Files", "service": "builtin", "port": 7000,
               "path": "/workspaces/gx10-proj/files", "protocol": "http"}
    s = _normalize_control_session(_session([builtin, {"label": "Files", "port": 8080, "path": "/files"}]))
    assert [t for t in s["tabs"] if t["label"] == "Files"] == [builtin]


def test_migration_is_idempotent():
    once = _normalize_control_session(_session([{"label": "Files", "port": 8080, "path": "/files"}]))
    assert _normalize_control_session(once) == once


def test_templates_have_no_fileserver():
    for t in _default_templates():
        assert all(p.get("port") != 8080 for p in t["panels"])
```
Run → FAIL.

- [ ] **Step 2: Implement** — in `storage.py`:
```python
def _builtin_files_tab(session_name: str) -> dict[str, Any]:
    return {
        "id": "tab-files",
        "label": "Files",
        "service": "builtin",
        "port": 7000,
        "path": f"/workspaces/{quote(session_name, safe='')}/files",
        "protocol": "http",
    }


def _is_legacy_files_tab(tab: dict[str, Any]) -> bool:
    if tab.get("port") == 8080:
        return True
    return tab.get("service") == "files" and str(tab.get("path", "")).startswith("/files")
```
In `_normalize_control_session`, after tabs are normalized: replace each legacy tab with `_builtin_files_tab(name)` unless a tab with that same `path` already exists (then drop it). Add `from urllib.parse import quote`. Remove the Files 8080 panel from `_default_templates()`, `session_templates.json`, and every machine in `machines.json` / `servers.json`. Normalize the tracked `sessions.json` once:
```bash
.venv/bin/python -c "from storage import _load_normalized_control_sessions as l, _save_control_sessions as s; s(l())"
git diff --stat sessions.json   # expect only the 3 legacy Files tabs to change
```

- [ ] **Step 3: Verify** — `.venv/bin/pytest tests -v` PASS; `grep -n 8080 sessions.json machines.json servers.json session_templates.json storage.py` empty.

- [ ] **Step 4: Commit** — `git commit -m "migrate legacy Files tabs to the built-in viewer; drop 8080 from defaults"`

---

### Task 3: Frontend + rr-files + docs for the retirement

**Files:**
- Modify: `static/app.js` (`defaultTemplates`, remove `startFilesServiceForEditor`, `launchFilesServiceFromPanelEditor` and their wiring), `index.html` (remove the launch button if present), `rr-files`, `README.md`, `Agents.md`

- [ ] **Step 1:** In `app.js` delete the Files 8080 line from `defaultTemplates()`, both fileserver functions and every reference (`grep -n "launchFilesService\|startFilesService\|8080" static/app.js index.html` → empty afterwards). `node --check static/app.js` passes.

- [ ] **Step 2:** Rewrite `rr-files` main flow (keep arg parsing / `.rr` discovery):
```python
    project_root = rr_path.parent if rr_path else None
    base = f"/workspaces/{urllib.parse.quote(session_name, safe='')}/files"
    tab_path = base
    if project_root and (path == project_root or project_root in path.parents):
        rel = path.relative_to(project_root).as_posix()
        if rel != ".":
            tab_path = f"{base}?path={urllib.parse.quote(rel)}"
    elif project_root:
        print(f"note: {path} is outside the workspace project {project_root}; opening the project root", file=sys.stderr)
    tab_result = _post(
        f"{control_url}/sessions/{urllib.parse.quote(session_name, safe='')}/tabs",
        {"label": label, "service": "builtin", "port": 7000, "path": tab_path, "protocol": "http"},
    )
    print(f"Tab '{label}' {tab_result.get('action', 'registered')} → {control_url.rsplit(':', 1)[0]}:7000{tab_path}")
```
Remove `--hub` and the `/services/files/start` call; update the docstring. Smoke: `./rr-files --help` exits 0.

- [ ] **Step 3:** README/Agents.md: drop `fileserver.py`, port 8080, `/services/files/start*`; Files = built-in viewer.

- [ ] **Step 4: Verify in a preview hub** — start the worktree hub on the tailnet at a spare port: `BIND_HOST=100.119.43.10 RUN_MODE=all .venv/bin/uvicorn main:app --host 100.119.43.10 --port 7100`; open a workspace, Files tab loads the built-in viewer on netochka and gx10.

- [ ] **Step 5: Commit** — `git commit -m "frontend/rr-files: built-in Files only"`

---

### Task 4: Prefix awareness in the hub, built-ins and logs (+ logs XSS fix)

**Files:**
- Create: `forwarded.py`, `tests/test_forwarded.py`
- Modify: `main.py` (middleware, `/control/context`), `workspace_builtins.py` (`_file_base`, CodeMirror asset links, `STATIC_BASE` prelude), `logs.py` (escape + relative EventSource + same middleware not needed)

**Interfaces:**
- Produces: `forwarded.prefix() -> str` (validated, `""` by default), `forwarded.via_gateway() -> bool`, `forwarded.ForwardedMiddleware` (pure ASGI). `/control/context` gains `"via_gateway": bool` and `"hub_host": str` (`PUBLIC_HOST` env).

- [ ] **Step 1: Failing tests** (`tests/test_forwarded.py`):
```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

import forwarded


def _app():
    app = FastAPI()
    app.add_middleware(forwarded.ForwardedMiddleware)

    @app.get("/p")
    def p():  # sync → runs in threadpool; contextvar must propagate
        return {"prefix": forwarded.prefix(), "via": forwarded.via_gateway()}

    return app


def test_prefix_from_header():
    c = TestClient(_app())
    r = c.get("/p", headers={"X-Forwarded-Prefix": "/m/100.118.187.64/7000", "X-RR-Via-Gateway": "1"})
    assert r.json() == {"prefix": "/m/100.118.187.64/7000", "via": True}


def test_no_header_means_no_prefix():
    assert TestClient(_app()).get("/p").json() == {"prefix": "", "via": False}


def test_invalid_prefix_is_ignored():
    for bad in ['/m/x"><script>/1', "/evil", "/m/1.2.3.4/99999999", "//attacker.com"]:
        r = TestClient(_app()).get("/p", headers={"X-Forwarded-Prefix": bad})
        assert r.json()["prefix"] == ""


def test_file_base_uses_prefix(monkeypatch):
    import workspace_builtins as wb
    monkeypatch.setattr(forwarded, "prefix", lambda: "/m/100.118.187.64/7000")
    assert wb._file_base("a b") == "/m/100.118.187.64/7000/workspaces/a%20b/files"


def test_logs_index_escapes_and_is_relative():
    import logs
    r = TestClient(logs.app).get("/", params={"path": '"><script>alert(1)</script>'})
    assert "<script>alert(1)" not in r.text
    assert "EventSource('stream?path='" in r.text
```
Run → FAIL (no module `forwarded`).

- [ ] **Step 2: Implement `forwarded.py`**
```python
"""Request-scoped view of headers set by gateway.py (see public-access spec)."""
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
```
`main.py`: `app.add_middleware(ForwardedMiddleware)` after `app = FastAPI()`; in `control_context()` add `"via_gateway": via_gateway(), "hub_host": os.getenv("PUBLIC_HOST", "")`.
`workspace_builtins.py`: `import forwarded`; `_file_base` returns `f"{forwarded.prefix()}/workspaces/{quote(session_name, safe='')}/files"`; turn `cm_assets` into an f-string using `st = forwarded.prefix() + "/static"`; add `f"const STATIC_BASE = {json.dumps(forwarded.prefix() + '/static')};\n"` to `prelude`; replace `'/static/vendor/codemirror/mode/%N/%N.min.js'` with `STATIC_BASE+'/vendor/codemirror/mode/%N/%N.min.js'`. Grep for any other `"/workspaces/` or `/static/` literal and route it the same way.
`logs.py`: `from html import escape`; render `value="{escape(path, quote=True)}"`; `new EventSource('stream?path=' + …)`.

- [ ] **Step 3: Verify** — `.venv/bin/pytest tests -v` PASS (if `test_prefix_from_header` fails for the sync endpoint, the contextvar is not reaching the threadpool: fix by keeping the pure-ASGI middleware — never `BaseHTTPMiddleware`).

- [ ] **Step 4: Commit** — `git commit -m "hub: honour X-Forwarded-Prefix in built-ins; logs: escape path, relative SSE"`

---

### Task 5: Gateway authentication (`gateway_auth.py`)

**Files:**
- Create: `gateway_auth.py`, `tests/test_gateway_auth.py`, `tests/jwt_helpers.py`

**Interfaces:**
- Produces: `class AuthError(Exception)`; `class AccessVerifier(team_domain: str, audience: str, allowed_email: str, jwk_client=None, leeway: int = 30)` with `.verify(token: str | None) -> dict` (raises `AuthError`). `jwk_client` needs `.get_signing_key_from_jwt(token) -> obj with .key`.
- Test helper `tests/jwt_helpers.py`: `TEAM = "rrtest.cloudflareaccess.com"`, `AUD = "aud-123"`, `EMAIL = "dh7777777@gmail.com"`, `make_token(**overrides) -> str`, `FakeJwks()`.

- [ ] **Step 1: Helpers + failing tests**

`tests/jwt_helpers.py`:
```python
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

TEAM = "rrtest.cloudflareaccess.com"
AUD = "aud-123"
EMAIL = "dh7777777@gmail.com"
KID = "k1"
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _Signing:
    def __init__(self, key):
        self.key = key


class FakeJwks:
    def __init__(self, fail=False):
        self.fail = fail

    def get_signing_key_from_jwt(self, token):
        if self.fail:
            raise jwt.PyJWKClientError("jwks unreachable")
        kid = jwt.get_unverified_header(token).get("kid")
        if kid != KID:
            raise jwt.PyJWKClientError("unknown kid")
        return _Signing(_KEY.public_key())


def make_token(key=None, kid=KID, alg="RS256", **claims):
    now = int(time.time())
    body = {"iss": f"https://{TEAM}", "aud": [AUD], "email": EMAIL, "sub": "u1",
            "iat": now, "nbf": now, "exp": now + 600}
    body.update(claims)
    body = {k: v for k, v in body.items() if v is not None}
    return jwt.encode(body, key or _KEY, algorithm=alg, headers={"kid": kid})


def other_key():
    return _OTHER
```
`tests/test_gateway_auth.py`:
```python
import time

import pytest

from gateway_auth import AccessVerifier, AuthError
from tests.jwt_helpers import AUD, EMAIL, TEAM, FakeJwks, make_token, other_key


def v(jwks=None):
    return AccessVerifier(TEAM, AUD, EMAIL, jwk_client=jwks or FakeJwks())


def test_valid_token():
    assert v().verify(make_token())["email"] == EMAIL


@pytest.mark.parametrize("token", [
    None, "", "garbage",
    make_token(exp=int(time.time()) - 120),
    make_token(aud=["other"]),
    make_token(iss="https://evil.cloudflareaccess.com"),
    make_token(email="someone@else.com"),
    make_token(email=None),
    make_token(key=other_key()),
    make_token(kid="unknown"),
    make_token(key="shared-secret", alg="HS256"),
])
def test_rejected(token):
    with pytest.raises(AuthError):
        v().verify(token)


def test_email_case_insensitive():
    assert v().verify(make_token(email=EMAIL.upper()))


def test_jwks_failure_denies():
    with pytest.raises(AuthError):
        v(FakeJwks(fail=True)).verify(make_token())


def test_bad_team_domain_rejected():
    with pytest.raises(ValueError):
        AccessVerifier("evil.com", AUD, EMAIL, jwk_client=FakeJwks())
```
Run → FAIL.

- [ ] **Step 2: Implement `gateway_auth.py`**
```python
"""Cloudflare Access JWT verification for gateway.py (fail closed)."""
from __future__ import annotations

import re
from typing import Any

import jwt

_TEAM_RE = re.compile(r"^[a-z0-9-]+\.cloudflareaccess\.com$")


class AuthError(Exception):
    pass


class AccessVerifier:
    def __init__(self, team_domain: str, audience: str, allowed_email: str, jwk_client=None, leeway: int = 30):
        if not _TEAM_RE.match(team_domain or ""):
            raise ValueError("CF_TEAM_DOMAIN must look like <team>.cloudflareaccess.com")
        if not audience or not allowed_email:
            raise ValueError("audience and allowed_email are required")
        self.issuer = f"https://{team_domain}"
        self.audience = audience
        self.allowed_email = allowed_email.strip().lower()
        self.leeway = leeway
        self._jwk = jwk_client or jwt.PyJWKClient(
            f"{self.issuer}/cdn-cgi/access/certs", cache_jwk_set=True, lifespan=300, timeout=5
        )

    def verify(self, token: str | None) -> dict[str, Any]:
        if not token:
            raise AuthError("missing token")
        try:
            key = self._jwk.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                leeway=self.leeway,
                options={"require": ["exp", "iat", "iss", "aud"]},
            )
        except Exception as exc:  # any failure, including JWKS fetch, denies
            raise AuthError("invalid token") from exc
        if str(claims.get("email", "")).strip().lower() != self.allowed_email:
            raise AuthError("email not allowed")
        return claims
```

- [ ] **Step 3: Verify** — `.venv/bin/pytest tests/test_gateway_auth.py -v` PASS.
- [ ] **Step 4: Commit** — `git commit -m "gateway: Cloudflare Access JWT verifier"`

---

### Task 6: Gateway proxy (`gateway.py`)

**Files:**
- Create: `gateway.py`, `tests/test_gateway.py`

**Interfaces:**
- Consumes: `gateway_auth.AccessVerifier`, `AuthError`; `tests.jwt_helpers`.
- Produces: `create_app(verifier, public_origin: str, hub_url: str, inventory: Callable[[], set[str]], transport: httpx.AsyncBaseTransport | None = None) -> Starlette`; `load_inventory(root: Path) -> set[str]`; `create_app_from_env() -> Starlette` (uvicorn `--factory`).

- [ ] **Step 1: Failing tests** (`tests/test_gateway.py`):
```python
import json
import threading

import httpx
import pytest
from starlette.testclient import TestClient
from websockets.sync.server import serve

import gateway
from gateway_auth import AccessVerifier
from tests.jwt_helpers import AUD, EMAIL, TEAM, FakeJwks, make_token

ORIGIN = "https://rr.sb39.xyz"
HOST = "100.118.187.64"
seen = {}


def upstream(request: httpx.Request) -> httpx.Response:
    seen["req"] = request
    if request.url.path.endswith("/redirect"):
        return httpx.Response(302, headers={"location": f"http://{request.url.host}:{request.url.port}/next"})
    return httpx.Response(200, headers={"set-cookie": "a=b", "server": "uvicorn", "content-type": "text/plain"},
                          text=f"{request.url.host}:{request.url.port}{request.url.raw_path.decode()}")


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
    r = client().get(path)
    assert r.status_code == 403 and r.text == ""


def test_cookie_alone_is_not_enough():
    r = client().get("/", cookies={"CF_Authorization": make_token()})
    assert r.status_code == 403


def test_hub_route():
    r = client().get("/sessions?x=1", headers=auth())
    assert r.status_code == 200
    assert r.text == "100.119.43.10:7000/sessions?x=1"
    assert seen["req"].headers["x-rr-via-gateway"] == "1"


def test_machine_route_and_prefix():
    r = client().get(f"/m/{HOST}/7000/workspaces/a%20b/files?path=x", headers=auth())
    assert r.text == f"{HOST}:7000/workspaces/a%20b/files?path=x"
    assert seen["req"].headers["x-forwarded-prefix"] == f"/m/{HOST}/7000"


@pytest.mark.parametrize("path", [
    "/m/10.0.0.1/80/", "/m/169.254.169.254/1024/", "/m/100.64.0.9/7681/",  # not in inventory
    f"/m/{HOST}/22/", f"/m/{HOST}/70000/", f"/m/{HOST}/abc/",
    f"/m/{HOST}/7000/../../etc", f"/m/{HOST}/7000/%2e%2e/x", f"/m/{HOST}/7000/a/%2E%2E/b",
    f"/m/{HOST}@evil.com/7000/", "/m/evil.com/7000/",
])
def test_routing_rejections(path):
    assert client().get(path, headers=auth()).status_code in (400, 404)


def test_header_hygiene():
    r = client().get(f"/m/{HOST}/7681/", headers=auth(Cookie="CF_Authorization=t; other=1",
                                                     Authorization="Bearer x", **{"X-Forwarded-For": "1.2.3.4",
                                                     "Cf-Connecting-Ip": "1.2.3.4"}))
    sent = seen["req"].headers
    for h in ("cookie", "authorization", "cf-access-jwt-assertion", "x-forwarded-for", "cf-connecting-ip"):
        assert h not in sent
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


def test_post_requires_same_origin():
    c = client()
    assert c.post("/sessions", headers=auth(), content=b"{}").status_code == 403
    assert c.post("/sessions", headers=auth(Origin="https://evil.com"), content=b"{}").status_code == 403
    assert c.post("/sessions", headers=auth(Origin=ORIGIN), content=b"{}").status_code == 200


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
        got = [c for c in r.iter_raw()]
    assert b"".join(got) == b"data: 0\n\ndata: 1\n\ndata: 2\n\n"
    assert r.headers["content-type"].startswith("text/event-stream")


@pytest.fixture
def ws_upstream():
    def handler(conn):
        seen["ws_headers"] = dict(conn.request.headers)
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
    assert "cf-access-jwt-assertion" not in {k.lower() for k in seen["ws_headers"]}


@pytest.mark.parametrize("headers", [{"Origin": ORIGIN}, {"Origin": "https://evil.com", **auth()}, auth()])
def test_ws_rejected_without_auth_or_origin(ws_upstream, headers):
    from starlette.websockets import WebSocketDisconnect
    c = client(inventory={"127.0.0.1"})
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect(f"/m/127.0.0.1/{ws_upstream}/ws", headers=headers):
            pass


def test_load_inventory(tmp_path):
    (tmp_path / "machines.json").write_text(json.dumps([{"ip": "100.118.187.64"}, {"ip": "192.168.1.1"}]))
    (tmp_path / "sessions.json").write_text(json.dumps([{"machine": {"host": "100.94.124.42"}}]))
    assert gateway.load_inventory(tmp_path) == {"100.118.187.64", "100.94.124.42"}
```
(The `127.0.0.1` inventory in WS tests is injected; `load_inventory` itself only ever admits 100.64.0.0/10.) Run → FAIL.

- [ ] **Step 2: Implement `gateway.py`**
```python
"""Public gateway for rr.sb39.xyz: verify the Cloudflare Access JWT, then proxy.

See docs/superpowers/specs/2026-10-21-public-access-design.md. Listens on
127.0.0.1 only; cloudflared is its only client.
"""
from __future__ import annotations

import asyncio
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
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from gateway_auth import AccessVerifier, AuthError

log = logging.getLogger("rr.gateway")
HERE = Path(__file__).resolve().parent
TAILNET = ipaddress.ip_network("100.64.0.0/10")
METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_M_RE = re.compile(rb"^/m/([^/]+)/([^/]+)(/.*)?$")
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
       "transfer-encoding", "upgrade", "host", "content-length"}
DROP_REQ = HOP | {"cookie", "authorization", "cf-access-jwt-assertion", "cf-access-client-id",
                  "cf-access-client-secret", "x-real-ip", "true-client-ip", "cdn-loop", "forwarded",
                  "x-forwarded-prefix", "x-rr-via-gateway", "origin", "referer"}
DROP_RESP = (HOP - {"content-length"}) | {"server", "set-cookie", "x-powered-by"}
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


def _clean_request_headers(headers, prefix: str) -> list[tuple[str, str]]:
    out = [(k, v) for k, v in headers.items()
           if k.lower() not in DROP_REQ and not k.lower().startswith(("cf-", "x-forwarded-"))]
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
        """Return (base_url, prefix, upstream_raw_path) or None if not allowed."""
        m = _M_RE.match(raw_path)
        if not m:
            return f"{hub.scheme}://{hub.netloc}", "", raw_path
        host, port_raw, rest = m.group(1).decode("latin-1"), m.group(2), m.group(3) or b"/"
        if not port_raw.isdigit() or not (1024 <= int(port_raw) <= 65535):
            return None
        if host not in inventory():
            return None
        segments = unquote(rest.decode("latin-1")).split("/")
        if any(s in ("..", ".") for s in segments) or any(ord(ch) < 32 for ch in "".join(segments)):
            return None
        port = int(port_raw)
        return f"http://{host}:{port}", f"/m/{host}/{port}", rest

    def rewrite_location(value: str, base: str, prefix: str) -> str:
        if prefix and value.startswith(base):
            return prefix + (value[len(base):] or "/")
        return value

    async def http_proxy(request: Request) -> Response:
        started = time.monotonic()
        if not authorized(request.headers):
            return _deny()
        if request.method not in SAFE_METHODS and request.headers.get("origin") != public_origin:
            return _deny()
        raw = request.scope.get("raw_path") or request.url.path.encode()
        target = resolve(raw)
        if target is None:
            return _deny(404)
        base, prefix, path = target
        query = request.scope.get("query_string", b"")
        url = base + path.decode("latin-1") + (("?" + query.decode("latin-1")) if query else "")
        upstream_req = client.build_request(request.method, httpx.URL(url),
                                            headers=_clean_request_headers(request.headers, prefix),
                                            content=request.stream())
        try:
            upstream = await client.send(upstream_req, stream=True)
        except httpx.HTTPError:
            log.info("%s %s 502 %.0fms", request.method, request.url.path, (time.monotonic() - started) * 1000)
            return _deny(502)
        headers = {k: v for k, v in upstream.headers.items() if k.lower() not in DROP_RESP}
        if "location" in headers:
            headers["location"] = rewrite_location(headers["location"], base, prefix)
        headers.update(SECURITY_HEADERS)
        log.info("%s %s %s %.0fms", request.method, request.url.path, upstream.status_code,
                 (time.monotonic() - started) * 1000)
        return StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code,
                                 headers=headers, background=_closer(upstream))

    async def ws_proxy(websocket: WebSocket) -> None:
        if not authorized(websocket.headers) or websocket.headers.get("origin") != public_origin:
            await websocket.close(code=1008)
            return
        target = resolve(websocket.scope.get("raw_path") or websocket.url.path.encode())
        if target is None:
            await websocket.close(code=1008)
            return
        base, prefix, path = target
        query = websocket.scope.get("query_string", b"").decode("latin-1")
        url = "ws" + base[len("http"):] + path.decode("latin-1") + (f"?{query}" if query else "")
        wanted = websocket.scope.get("subprotocols") or None
        extra = [(k, v) for k, v in _clean_request_headers(websocket.headers, prefix)
                 if k.lower() not in {"sec-websocket-key", "sec-websocket-version",
                                      "sec-websocket-extensions", "sec-websocket-protocol"}]
        try:
            upstream = await websockets.connect(url, subprotocols=wanted, additional_headers=extra,
                                                open_timeout=10, max_size=None, compression=None)
        except Exception:
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
            for t in tasks:
                t.cancel()
            await upstream.close()
            try:
                await websocket.close()
            except Exception:
                pass

    routes = [WebSocketRoute("/{rest:path}", ws_proxy), Route("/{rest:path}", http_proxy, methods=METHODS)]
    app = Starlette(routes=routes, on_shutdown=[client.aclose])
    return app


def _closer(upstream: httpx.Response):
    from starlette.background import BackgroundTask
    return BackgroundTask(upstream.aclose)


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
```
Notes: if `upstream.subprotocol` is unavailable on the installed websockets version, use `upstream.subprotocol` from `websockets.asyncio.client.connect` (≥ 13 exposes it); `additional_headers` is the ≥ 13 name.

- [ ] **Step 2b:** Startup smoke: `env -u CF_ACCESS_AUD .venv/bin/uvicorn --factory gateway:create_app_from_env --host 127.0.0.1 --port 7010` must exit with the "missing" message.
- [ ] **Step 3: Verify** — `.venv/bin/pytest tests -v` all PASS.
- [ ] **Step 4: Commit** — `git commit -m "gateway: authenticated reverse proxy for public access"`

---

### Task 7: Frontend uses gateway URLs

**Files:**
- Modify: `static/app.js` (`state`, context loader ~L184, `endpointUrl`, any other place that builds `${protocol}://${host}:${port}` URLs)

**Interfaces:**
- Consumes: `/control/context` → `via_gateway: bool`, `hub_host: str` (Task 4).

- [ ] **Step 1:** In the `/control/context` loader store `state.viaGateway = Boolean(payload.via_gateway); state.hubHost = payload.hub_host || '';`.
- [ ] **Step 2:** Add and use:
```js
function gatewayUrl(host, port, path) {
  const target = isLocalHost(host) ? state.hubHost : host;
  const p = path && path.startsWith('/') ? path : `/${path || ''}`;
  return `/m/${encodeURIComponent(target)}/${port}${p}`;
}
```
In `endpointUrl`, before the existing return: `if (state.viaGateway && port) return gatewayUrl(endpoint?.host || sessionMachineHost(server), port, path);`. Then `grep -n '://\${' static/app.js` and route every other direct-panel URL (open-in-new-tab links etc.) through `endpointUrl`. `node --check static/app.js`.
- [ ] **Step 3: Verify** — tailnet preview hub (Task 3 step 4 command) still renders all panels with the old URLs; then run the gateway locally against the preview hub with a test verifier (see Task 8 step 3) and confirm iframe `src` values start with `/m/`.
- [ ] **Step 4: Commit** — `git commit -m "frontend: route panels through /m/ when served via the gateway"`

---

### Task 8: Ops — gateway launch, tunnel unit, docs, secrets backstop

**Files:**
- Modify: `start-control.sh`, `.gitignore`, `README.md`, `install-service.sh`
- Create: `deploy/rr-tunnel.service`, `deploy/cloudflared-remote-rider.yml.example`, `deploy/gateway.env.example`, `install-tunnel.sh`

- [ ] **Step 1:** `start-control.sh`: after the hub start, if `$HOME/.config/remote-rider/gateway.env` exists: stop any `uvicorn --factory gateway:create_app_from_env`, then
```bash
( set -a; . "$GATEWAY_ENV"; set +a
  exec setsid "$VENV/uvicorn" --factory gateway:create_app_from_env \
    --host 127.0.0.1 --port 7010 --no-access-log --no-server-header ) \
  > "$LOG_DIR/gateway.log" 2>&1 < /dev/null &
echo "$!" > "$PID_DIR/gateway.pid"
```
Also stop it in `stop_hub`. Refuse to start (warn) if `gateway.env` is not mode 600.
- [ ] **Step 2:** `deploy/cloudflared-remote-rider.yml.example`:
```yaml
tunnel: remote-rider
credentials-file: /home/dh/.cloudflared/<TUNNEL_ID>.json
metrics: 127.0.0.1:0
no-autoupdate: true
ingress:
  - hostname: rr.sb39.xyz
    service: http://127.0.0.1:7010
  - service: http_status:404
```
`deploy/rr-tunnel.service`:
```ini
[Unit]
Description=Remote Rider public tunnel (cloudflared -> 127.0.0.1:7010)
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/local/bin/cloudflared --config %h/.cloudflared/remote-rider.yml tunnel run
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```
`deploy/gateway.env.example` (placeholders only). `install-tunnel.sh`: copies the unit to `~/.config/systemd/user/`, `daemon-reload`, `enable` (does **not** start, does **not** create DNS).
- [ ] **Step 3:** Local end-to-end without Cloudflare: a throwaway script in the session scratchpad mints a token with `tests/jwt_helpers.py`'s key and runs `gateway.create_app` with `FakeJwks` against the preview hub; curl with/without token. Not committed.
- [ ] **Step 4:** `.gitignore`: `gateway.env`, `*.cloudflared*.json`, `deploy/*.json`. README "Public access" section: architecture, setup, kill switch.
- [ ] **Step 5:** Secret scan, then commit:
```bash
git diff --cached | grep -inE 'eyJ[a-z0-9_-]{10,}|AccountTag|TunnelSecret|aud-[0-9a-f]{20,}|BEGIN (RSA )?PRIVATE' && echo "STOP: secret" || git commit -m "ops: gateway launch, rr-tunnel unit, public-access docs"
```

---

### Task 9: Go-live (with the user)

- [ ] **Step 1:** Merge `feat/public-access` → `main`, `git pull` in `~/code/remote-rider`, `pip install -r requirements-gateway.txt` in its venv, restart: `systemctl --user restart remote-rider`. Check the tailnet UI (all machines; Files = built-in). Update remotes via the existing fleet update.
- [ ] **Step 2 (user):** Zero Trust dashboard: Settings → Authentication → add Google; Access → Applications → Self-hosted `rr.sb39.xyz`, session 24h, policy *Allow* / *Emails* = `dh7777777@gmail.com`, no other rules. Send back team domain + AUD.
- [ ] **Step 3:** Write `~/.config/remote-rider/gateway.env` (chmod 600), restart control; `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:7010/` → `403` for `/`, `/openapi.json`, `/m/100.118.187.64/7681/`.
- [ ] **Step 4:** `cloudflared tunnel create remote-rider`; write `~/.cloudflared/remote-rider.yml` from the example; `./install-tunnel.sh`; `systemctl --user start rr-tunnel`; confirm connected (`cloudflared tunnel info remote-rider`). Still no DNS.
- [ ] **Step 5:** `cloudflared tunnel route dns remote-rider rr.sb39.xyz`. Immediately, from outside the tailnet (e.g. `curl` with `--resolve` is not needed — the request goes through Cloudflare anyway):
```bash
for p in / /openapi.json /docs /sessions /m/100.118.187.64/7681/ /m/100.118.187.64/7681/ws; do
  curl -s -o /dev/null -w "$p %{http_code} %{redirect_url}\n" "https://rr.sb39.xyz$p"
done
```
Every line must be `302 https://<team>.cloudflareaccess.com/...`. Any 200/403-from-us → `systemctl --user stop rr-tunnel` and investigate. Confirm no other new DNS record.
- [ ] **Step 6 (user):** From a non-tailnet browser: login, terminal on gx10, Files on hal5090 (open/save), logs stream, monitor.
