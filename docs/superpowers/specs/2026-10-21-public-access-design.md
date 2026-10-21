# Public access via Cloudflare Tunnel + Access

Status: design approved in chat (2026-10-21), pending spec review.

## Goal

Use remote-rider from any computer **without Tailscale**, over HTTPS, behind a
Google login that only `dh7777777@gmail.com` can pass. Success:

- On a non-tailnet laptop, `https://rr.sb39.xyz` → Google login → full UI,
  including a working ttyd shell on gx10 and the built-in Files view on hal5090.
- Without a valid login, **no request reaches netochka**, and even a request that
  somehow does is rejected by our own gateway.
- Tailnet access (`http://100.x.x.x:7000`) behaves exactly as today.

Non-goals: multi-user, per-user permissions, exposing anything other than the
hub UI and the panels it already embeds.

## Phase 1 — retire legacy `fileserver.py`

The built-in hub viewer (`workspace_builtins.py`, `/workspaces/<s>/files`)
becomes the only file server.

- Delete `fileserver.py`.
- `start-remote.sh`, `start.sh`: stop launching it on `FILES_PORT` (keep the
  `stop_*` lines one release so old processes are cleaned up on update).
- `services.py`: remove the `files` service entry, `fileserver` kind handling and
  the extra-fileserver spawner; `main.py`: remove `/services/files/start` and
  `/services/files/start/proxy`.
- `static/app.js`: remove the "Start fileserver" action (~L1303); the default
  panel list (~L702) uses the built-in Files tab.
- Migration: `_normalize_control_session` rewrites any legacy Files tab
  (port 8080, or `service: files` with a `/files` path) to the built-in Files
  tab for that workspace, deduplicating against an existing built-in one. It is
  a pure read-side transform (idempotent, nothing destroyed); the next save
  persists it, and the tracked `sessions.json` is normalized in the same commit.
  Service-registry rows of `kind: fileserver` are ignored and pruned.
- Defaults (`machines.json`, `servers.json`, `session_templates.json`,
  `storage.py`, `app.js`) simply drop the Files 8080 panel: new workspaces
  already get built-in Files/Notes/Skills from `DEFAULT_WORKSPACE_TABS`.
- `rr-files`: instead of asking the hub to spawn a fileserver, register the
  workspace's built-in Files tab (with `?path=<rel>` when PATH is inside the
  project that owns the `.rr` file) and print its URL.
- README / Agents.md: drop fileserver and port 8080.

## Phase 2 — public gateway

### Topology

```
browser ──HTTPS──▶ Cloudflare edge — Access app rr.sb39.xyz (Google, 1 email)
                       │ tunnel "remote-rider" (outbound-only from netochka)
                       ▼
            gateway.py  127.0.0.1:7010   (netochka only, never on tailnet IP)
              1. verify Cf-Access-Jwt-Assertion  (fail closed)
              2. /m/<machine>/<port>/…  → http://<tailnet-ip>:<port>/…
                 everything else        → http://<netochka-tailnet-ip>:7000/…
```

The hub is unchanged in how it listens. The gateway is a small separate ASGI
app (`gateway.py`, Starlette/uvicorn, `httpx` for HTTP + SSE streaming,
`websockets` for WS) with no state of its own.

### Authentication (gateway side)

- Every HTTP request and WS handshake must carry `Cf-Access-Jwt-Assertion`
  (header only; the `CF_Authorization` cookie is not accepted as a substitute).
- Verify with PyJWT: algorithm pinned to `RS256`, signature against
  `https://<team>.cloudflareaccess.com/cdn-cgi/access/certs` (cached, refetched
  on unknown `kid`, at most once/minute), `aud` == configured AUD tag,
  `iss` == team URL, `exp`/`nbf` enforced (30s leeway), `email` ==
  `ALLOWED_EMAIL`.
- Config from `~/.config/remote-rider/gateway.env` (mode 600, outside the repo):
  `CF_TEAM_DOMAIN`, `CF_ACCESS_AUD`, `ALLOWED_EMAIL`. Any missing → gateway
  refuses to start. JWKS unreachable and no cache → 403, never "allow".
- Failure response: bare `403` with no detail.

### Routing / no open proxy

- `/m/<host>/<port>/<rest>`: `<host>` must be a tailnet address
  (100.64.0.0/10) that appears in `machines.json` or as a session's machine
  host in `sessions.json`; `<port>` must be 1024–65535. Nothing outside the
  inventory (router, LAN, cloud metadata, other tailnet nodes) is reachable.
  A per-port allowlist is deliberately not added: anyone past the login already
  has a shell, so it would add breakage without adding protection. Anything
  else → 404.
- `<rest>` is forwarded as-is after rejecting `..` segments (raw and
  percent-encoded) and control characters.
- Everything not under `/m/` goes to the local hub (`<netochka-ip>:7000`).

### Header hygiene (no leaks)

Request → backend: drop `Cf-Access-Jwt-Assertion`, `Cookie` (CF_Authorization
lives there), `Cf-*`, `X-Forwarded-*` from the client, `Authorization`; set
`X-Forwarded-Prefix: /m/<machine>/<port>` (or empty for hub),
`X-Forwarded-Proto: https`, `Host: <target>`.

Response → browser: drop `Server`, `Set-Cookie` from backends; add
`Cache-Control: private, no-store` (so Cloudflare never caches panel content),
`Strict-Transport-Security: max-age=31536000`, `X-Content-Type-Options:
nosniff`, `Referrer-Policy: no-referrer`, and
`Content-Security-Policy: frame-ancestors 'self'`. `Location` headers with an
absolute backend URL (`http://<tailnet-ip>:<port>/…`) are rewritten to the
prefixed public path; path-only Locations are left alone because the apps
already emit them prefix-aware (no double prefixing). After phase 1 no backend
sets cookies, so dropping `Set-Cookie` loses nothing.

### CSRF / cross-site WebSocket

- Non-GET/HEAD requests and every WS upgrade must have `Origin ==
  https://rr.sb39.xyz`; otherwise 403. (Access cookie alone must not be enough
  for a foreign page to drive a terminal.)

### Prefix awareness in panel apps

- Built-in Files/Notes/Skills (`workspace_builtins.py`): `_file_base()` and the
  `/static/vendor/...` links read `X-Forwarded-Prefix` (validated against
  `^/m/<host>/<port>$`, default `""`) through a request-scoped contextvar.
- `logs.py`: `EventSource` URL becomes relative; also fix the reflected XSS
  in its `?path=` input (unescaped into HTML) — harmless on a private tailnet,
  a real hole once the UI has a public origin.
- `monitor.py`, ttyd: no change (relative URLs).
- Frontend `endpointUrl()` (`static/app.js`): when `/control/context` reports
  `via_gateway: true` (hub sees `X-RR-Via-Gateway: 1`, set only by the
  gateway), return `/m/<host>/<port><path>`, mapping local hosts to the hub's
  tailnet IP (`hub_host`); otherwise unchanged.

### Logging

Gateway logs method, path (query string stripped), status, latency. Never
headers, cookies, tokens, or bodies.

### Cloudflare side

- Access (dashboard, manual, one time): Google IdP; self-hosted app
  `rr.sb39.xyz`; single Allow rule `email == dh7777777@gmail.com`; session 24h;
  no Bypass / Service Auth rules; no email-OTP fallback.
- Tunnel `remote-rider` (`cloudflared tunnel create`), config in
  `~/.cloudflared/remote-rider.yml`: one ingress rule `rr.sb39.xyz →
  http://127.0.0.1:7010`, catch-all `http_status:404`, metrics on
  `127.0.0.1` only. Existing tunnels `sb38`/`secondbrain` untouched.
- Only DNS change: CNAME `rr.sb39.xyz` → tunnel.

### Processes

The gateway is started by `start-control.sh` (control host only) next to the
hub, on `127.0.0.1:7010`, only when `gateway.env` exists. `cloudflared` runs as
its own systemd user unit `rr-tunnel.service`. Kill switch:
`systemctl --user stop rr-tunnel`.

### Secrets — never in the repo

`gateway.env`, `~/.cloudflared/*.json`, `cert.pem` live outside the repo.
`.gitignore` gains `gateway.env` and `*.cloudflared*.json` as a backstop. Before
each commit: scan the diff for tokens/AUD/JWT-looking strings.

## Rollout order (nothing exposed without login, ever)

1. Phase 1 merged; verified on tailnet.
2. Gateway built; local tests pass (below).
3. User creates the Access app; sends team domain + AUD.
4. Tunnel started **without** DNS route.
5. DNS route created; immediately run the external leak checks.
6. User tests from a non-tailnet machine.

## Testing

Unit (pytest, gateway):
- missing / malformed / expired / wrong-aud / wrong-iss / wrong-email /
  HS256-forged / unknown-kid JWT → 403; valid (test-signed with a local key
  injected as JWKS) → proxied.
- `/m/` routing: unknown machine, port not in allowlist, `..` and `%2e%2e`,
  host smuggling attempts → 404/400.
- Header stripping both directions; `Cache-Control: no-store` present.
- Origin check on POST and WS upgrade.
- Prefix rewriting for built-in Files and logs SSE.

Live, before DNS (curl against 127.0.0.1:7010): no token → 403 on `/`,
`/openapi.json`, `/m/gx10/7681/`, WS upgrade.

Live, after DNS, from outside the tailnet (curl, no cookies): `/`,
`/openapi.json`, `/docs`, `/m/gx10/7681/ws`, `/sessions` → all 302 to
`*.cloudflareaccess.com`, never a 200 or a hub body. Check no other
`*.sb39.xyz` record was added.

Browser (real Google login, non-tailnet): UI loads, ttyd on gx10 types,
Files on hal5090 opens/saves, logs stream, monitor renders.

Phase 1: migration test on a copy of `sessions.json` (8080 tabs → built-in,
idempotent, backup written); tailnet UI check of Files on every machine.
