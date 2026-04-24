import json
import os
import threading
from pathlib import Path
from typing import Any, Literal

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


class RemoteRequest(BaseModel):
    action: Literal["add", "remove"] = "add"
    name: str = Field(min_length=1, max_length=80)
    ip: str | None = None
    base: str | None = "netochka"
    position: Literal["top", "bottom"] = "top"


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


def _clone_panels(server: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(panel) for panel in server.get("panels", [])]


def _apply_port_overrides(servers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    env_map = {
        "Terminal": "TERM_PORT",
        "Monitor": "MONITOR_PORT",
        "Logs": "LOGS_PORT",
        "Files": "FILES_PORT",
    }
    overridden: list[dict[str, Any]] = []
    for server in servers:
        cloned_server = dict(server)
        panels = []
        for panel in server.get("panels", []):
            cloned_panel = dict(panel)
            if os.getenv("DISABLE_TERMINAL") == "1" and str(cloned_panel.get("label", "")) == "Terminal":
                continue
            if cloned_panel.get("api") is True:
                panels.append(cloned_panel)
                continue
            env_key = env_map.get(str(cloned_panel.get("label", "")))
            if env_key:
                port_val = os.getenv(env_key)
                if port_val and port_val.isdigit():
                    cloned_panel["port"] = int(port_val)
            panels.append(cloned_panel)
        cloned_server["panels"] = panels
        overridden.append(cloned_server)
    return overridden


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
- Add/remove remote: POST {base}/remote

Example:
curl -X POST {base}/tab \
  -H 'content-type: application/json' \
  -d '{{"server":"netochka","label":"Agent","port":9001,"path":"/","ip":"100.119.43.10"}}'

curl -X POST {base}/remote \
  -H 'content-type: application/json' \
  -d '{{"action":"add","name":"gx10","ip":"100.118.187.64","base":"netochka","position":"top"}}'
"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (HERE / "index.html").read_text()


@app.get("/servers")
def servers() -> list[dict]:
    return _apply_port_overrides(_load_servers())


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
            "api": True,
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


@app.post("/remote")
def manage_remote(payload: RemoteRequest) -> dict[str, Any]:
    with SERVERS_LOCK:
        data = _load_servers()
        remote_index = next((i for i, s in enumerate(data) if s.get("name") == payload.name), None)

        if payload.action == "remove":
            if remote_index is None:
                raise HTTPException(status_code=404, detail="remote not found")
            removed = data.pop(remote_index)
            _save_servers(data)
            return {
                "status": "ok",
                "action": "removed",
                "remote": removed,
                "servers": data,
            }

        if not payload.ip:
            raise HTTPException(status_code=400, detail="ip is required when action is add")

        if remote_index is None:
            panels: list[dict[str, Any]] = []
            if payload.base:
                base_server = next((s for s in data if s.get("name") == payload.base), None)
                if base_server is None:
                    raise HTTPException(status_code=404, detail="base server not found")
                panels = _clone_panels(base_server)

            remote = {
                "name": payload.name,
                "ip": payload.ip,
                "panels": panels,
            }
            if payload.position == "top":
                data.insert(0, remote)
            else:
                data.append(remote)
            action = "added"
        else:
            remote = data[remote_index]
            remote["ip"] = payload.ip
            if payload.position == "top" and remote_index != 0:
                data.insert(0, data.pop(remote_index))
            elif payload.position == "bottom" and remote_index != len(data) - 1:
                data.append(data.pop(remote_index))
            action = "updated"

        _save_servers(data)

    return {
        "status": "ok",
        "action": action,
        "remote": remote,
        "servers": data,
    }
