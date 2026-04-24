import json
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

app = FastAPI()
HERE = Path(__file__).parent
SERVERS_FILE = HERE / "servers.json"
SERVERS_LOCK = threading.Lock()


class TabRequest(BaseModel):
    server: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=80)
    port: int = Field(ge=1, le=65535)
    path: str = "/"
    ip: str | None = None
    protocol: str = "http"


def _normalize_path(path: str) -> str:
    cleaned = (path or "/").strip()
    if not cleaned:
        return "/"
    if not cleaned.startswith("/"):
        cleaned = "/" + cleaned
    return cleaned


def _normalize_protocol(protocol: str) -> str:
    cleaned = (protocol or "http").strip().lower()
    if cleaned not in {"http", "https"}:
        raise HTTPException(status_code=400, detail="protocol must be http or https")
    return cleaned


def _load_servers() -> list[dict[str, Any]]:
    if not SERVERS_FILE.exists():
        return []
    data = json.loads(SERVERS_FILE.read_text())
    if not isinstance(data, list):
        raise HTTPException(status_code=500, detail="servers.json must contain a JSON list")
    return data


def _save_servers(servers: list[dict[str, Any]]) -> None:
    SERVERS_FILE.write_text(json.dumps(servers, indent=2) + "\n")


def _agents_text(request: Request) -> str:
    base = str(request.base_url).rstrip("/")
    return f"""SlashAgents-Version: 0.1
API: Web Terminal Hub

Context:
- This API manages a local web-terminal dashboard and panel tabs.
- State is persisted in servers.json next to the hub app.
- Tabs are rendered by the UI from GET /servers responses.

Auth:
- None (local/private network use).

Base URLs:
- Active base: {base}

Limits:
- Designed for low-frequency control operations (human-driven updates).

Retries:
- Retry 5xx and network failures with exponential backoff.
- Do not retry 4xx validation errors without changing the request.

Idempotency:
- POST /tab is label-upsert within a server (same label updates existing panel).

Errors:
- 400 invalid protocol/path/payload
- 404 server not found (when creating tab without ip)
- 500 malformed servers.json

Specs:
- OpenAPI: {base}/openapi.json
- Docs: {base}/docs

Runtime:
- Health: GET {base}/servers
- Add/update tab: POST {base}/tab

Example:
curl -X POST {base}/tab \
  -H 'content-type: application/json' \
  -d '{{"server":"netochka","label":"Agent","port":9001,"path":"/","ip":"100.119.43.10"}}'
"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (HERE / "index.html").read_text()


@app.get("/servers")
def servers() -> list[dict]:
    return _load_servers()


@app.get("/agents", response_class=PlainTextResponse)
def agents(request: Request) -> str:
    return _agents_text(request)


@app.get("/.well-known/agents", response_class=PlainTextResponse)
def well_known_agents(request: Request) -> str:
    return _agents_text(request)


@app.get("/.well-known/agent-api", response_class=PlainTextResponse)
def well_known_agent_api(request: Request) -> str:
    return _agents_text(request)


@app.post("/tab")
def add_or_update_tab(payload: TabRequest) -> dict[str, Any]:
    panel_path = _normalize_path(payload.path)
    protocol = _normalize_protocol(payload.protocol)

    with SERVERS_LOCK:
        data = _load_servers()

        server = next((s for s in data if s.get("name") == payload.server), None)
        if server is None:
            if not payload.ip:
                raise HTTPException(
                    status_code=404,
                    detail="server not found; include 'ip' to create it",
                )
            server = {
                "name": payload.server,
                "ip": payload.ip,
                "panels": [],
            }
            data.append(server)
        elif payload.ip:
            server["ip"] = payload.ip

        panels = server.setdefault("panels", [])

        panel: dict[str, Any] = {
            "label": payload.label,
            "port": payload.port,
        }
        if panel_path != "/":
            panel["path"] = panel_path
        if protocol != "http":
            panel["protocol"] = protocol

        existing = next((p for p in panels if p.get("label") == payload.label), None)
        if existing is None:
            panels.append(panel)
            action = "created"
        else:
            existing.clear()
            existing.update(panel)
            action = "updated"

        _save_servers(data)

    return {
        "status": "ok",
        "action": action,
        "server": server,
    }
