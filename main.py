import json
import os
import shutil
import socket
import subprocess
import threading
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote
from urllib.parse import parse_qs, urlparse
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from fastapi import FastAPI, HTTPException, Query, Request
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
    action: Literal["add", "remove", "remove_kill"] = "add"
    name: str = Field(min_length=1, max_length=80)
    display: str | None = None
    ip: str | None = None
    base: str | None = "netochka"
    position: Literal["top", "bottom"] | None = None
    terminal_session: str | None = None


class ReorderRequest(BaseModel):
    order: list[str]


class TmuxKillRequest(BaseModel):
    host: str
    session: str = Field(min_length=1, max_length=120)
    port: int = Field(default=7000, ge=1, le=65535)


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


def _terminal_session_path(session: str) -> str:
    return f"/?arg={quote(session, safe='')}"


def _set_terminal_session(panels: list[dict[str, Any]], session: str) -> None:
    for panel in panels:
        if str(panel.get("label", "")) == "Terminal":
            panel["path"] = _terminal_session_path(session)


def _extract_terminal_session(server: dict[str, Any]) -> str | None:
    for panel in server.get("panels", []):
        if str(panel.get("label", "")) != "Terminal":
            continue
        raw_path = str(panel.get("path", "/"))
        query = parse_qs(urlparse(raw_path).query)
        arg = query.get("arg", [""])[0].strip()
        return arg or "1"
    return None


def _local_host_values() -> set[str]:
    values = {
        "127.0.0.1",
        "::1",
        "localhost",
        os.getenv("BIND_HOST", ""),
        os.getenv("PUBLIC_HOST", ""),
        socket.gethostname(),
        socket.getfqdn(),
    }
    return {v for v in values if v}


def _normalize_host(host: str) -> str:
    cleaned = host.strip().lower()
    if not cleaned:
        raise HTTPException(status_code=400, detail="host is required")
    if any(ch in cleaned for ch in ["/", "?", "#", "@"]):
        raise HTTPException(status_code=400, detail="invalid host")
    return cleaned


def _is_local_server(server: dict[str, Any]) -> bool:
    ip = str(server.get("ip", "")).strip()
    if not ip:
        return True
    return ip in _local_host_values()


def _is_local_host(host: str) -> bool:
    return host in {v.lower() for v in _local_host_values()}


def _tmux_session_exists(session: str) -> bool:
    result = subprocess.run(
        ["tmux", "has-session", "-t", session],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _kill_tmux_session(session: str) -> bool:
    if not _tmux_session_exists(session):
        return False
    result = subprocess.run(
        ["tmux", "kill-session", "-t", session],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _list_tmux_sessions() -> list[str]:
    if not shutil.which("tmux"):
        return []

    result = subprocess.run(
        ["tmux", "ls"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []

    sessions: list[str] = []
    for line in result.stdout.splitlines():
        if ":" not in line:
            continue
        name = line.split(":", 1)[0].strip()
        if name:
            sessions.append(name)
    return sessions


def _fetch_remote_tmux_sessions(host: str, port: int) -> dict[str, Any]:
    cleaned_host = _normalize_host(host)
    if _is_local_host(cleaned_host):
        return {
            "available": bool(shutil.which("tmux")),
            "sessions": _list_tmux_sessions(),
            "host": socket.gethostname(),
            "source": "local",
        }

    url = f"http://{cleaned_host}:{port}/tmux/sessions"
    req = UrlRequest(url, method="GET")
    try:
        with urlopen(req, timeout=2.5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("invalid response shape")
            return {
                "available": bool(payload.get("available", False)),
                "sessions": list(payload.get("sessions", [])),
                "host": str(payload.get("host", cleaned_host)),
                "source": "proxy",
            }
    except Exception as exc:
        return {
            "available": False,
            "sessions": [],
            "host": cleaned_host,
            "source": "proxy",
            "error": str(exc),
        }


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
- UI profile state is persisted in client localStorage.
- GET /servers returns bootstrap defaults from servers.json.

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
- tmux sessions: GET {base}/tmux/sessions
- tmux sessions by host: GET {base}/tmux/sessions/proxy?host=<ip>&port=7000
- kill tmux session: POST {base}/tmux/kill
- Add/update tab: POST {base}/tab
- Add/remove remote: POST {base}/remote
- Reorder remotes: POST {base}/remote/reorder
- Remove + kill tmux session: POST {base}/remote with action=remove_kill

Example:
curl -X POST {base}/tab \
  -H 'content-type: application/json' \
  -d '{{"server":"netochka","label":"Agent","port":9001,"path":"/","ip":"100.119.43.10"}}'

curl -X POST {base}/remote \
  -H 'content-type: application/json' \
  -d '{{"action":"add","name":"netochka-job2","display":"netochka","ip":"100.119.43.10","base":"netochka","position":"top","terminal_session":"webterm"}}'
"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (HERE / "index.html").read_text()


@app.get("/servers")
def servers() -> list[dict]:
    return _apply_port_overrides(_load_servers())


@app.get("/tmux/sessions")
def tmux_sessions() -> dict[str, Any]:
    return {
        "available": bool(shutil.which("tmux")),
        "sessions": _list_tmux_sessions(),
        "host": socket.gethostname(),
    }


@app.get("/tmux/sessions/proxy")
def tmux_sessions_proxy(host: str = Query(...), port: int = Query(7000, ge=1, le=65535)) -> dict[str, Any]:
    return _fetch_remote_tmux_sessions(host, port)


@app.post("/tmux/kill")
def tmux_kill(payload: TmuxKillRequest) -> dict[str, Any]:
    host = _normalize_host(payload.host)
    session = payload.session.strip()
    if not session:
        raise HTTPException(status_code=400, detail="session is required")

    if _is_local_host(host):
        if not shutil.which("tmux"):
            return {"status": "skipped", "reason": "tmux not available", "host": host, "session": session}
        if _kill_tmux_session(session):
            return {"status": "killed", "host": host, "session": session}
        if _tmux_session_exists(session):
            return {"status": "error", "reason": "unable to kill session", "host": host, "session": session}
        return {"status": "not_found", "host": host, "session": session}

    url = f"http://{host}:{payload.port}/tmux/kill"
    req = UrlRequest(
        url,
        data=json.dumps({"host": "127.0.0.1", "session": session, "port": payload.port}).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=3) as resp:
            remote_payload = json.loads(resp.read().decode("utf-8"))
            if isinstance(remote_payload, dict):
                remote_payload.setdefault("source", "proxy")
                return remote_payload
    except Exception as exc:
        return {"status": "error", "reason": str(exc), "host": host, "session": session, "source": "proxy"}

    return {"status": "error", "reason": "invalid response", "host": host, "session": session, "source": "proxy"}


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

        if payload.action in {"remove", "remove_kill"}:
            if remote_index is None:
                raise HTTPException(status_code=404, detail="remote not found")
            removed = data.pop(remote_index)

            kill_result: dict[str, Any] | None = None
            if payload.action == "remove_kill":
                session = _extract_terminal_session(removed)
                kill_result = {
                    "requested": True,
                    "session": session,
                    "status": "skipped",
                    "reason": "",
                }

                if not session:
                    kill_result["reason"] = "no terminal panel/session found"
                elif not _is_local_server(removed):
                    kill_result["reason"] = "remote profile does not target this host"
                elif not shutil.which("tmux"):
                    kill_result["reason"] = "tmux not available on host"
                else:
                    shared = [
                        s
                        for s in data
                        if _is_local_server(s) and _extract_terminal_session(s) == session
                    ]
                    if shared:
                        kill_result["reason"] = "session still used by other profiles"
                        kill_result["shared_by"] = [str(s.get("name", "")) for s in shared]
                    elif _kill_tmux_session(session):
                        kill_result["status"] = "killed"
                        kill_result["reason"] = ""
                    elif _tmux_session_exists(session):
                        kill_result["reason"] = "unable to kill tmux session"
                    else:
                        kill_result["status"] = "not_found"
                        kill_result["reason"] = "tmux session not found"

            _save_servers(data)
            response = {
                "status": "ok",
                "action": "removed",
                "remote": removed,
                "servers": data,
            }
            if kill_result is not None:
                response["kill"] = kill_result
            return response

        if remote_index is None:
            if not payload.ip:
                raise HTTPException(status_code=400, detail="ip is required when creating a remote")

            panels: list[dict[str, Any]] = []
            if payload.base:
                base_server = next((s for s in data if s.get("name") == payload.base), None)
                if base_server is None:
                    raise HTTPException(status_code=404, detail="base server not found")
                panels = _clone_panels(base_server)

            if payload.terminal_session:
                _set_terminal_session(panels, payload.terminal_session)

            remote = {
                "name": payload.name,
                "ip": payload.ip,
                "panels": panels,
            }
            if payload.display:
                remote["display"] = payload.display
            if payload.position == "bottom":
                data.append(remote)
            else:
                data.insert(0, remote)
            action = "added"
        else:
            remote = data[remote_index]
            if payload.ip:
                remote["ip"] = payload.ip
            if payload.display is not None:
                if payload.display:
                    remote["display"] = payload.display
                else:
                    remote.pop("display", None)

            if payload.terminal_session:
                _set_terminal_session(remote.setdefault("panels", []), payload.terminal_session)

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


@app.post("/remote/reorder")
def reorder_remotes(payload: ReorderRequest) -> dict[str, Any]:
    with SERVERS_LOCK:
        data = _load_servers()
        existing_names = [str(s.get("name", "")) for s in data]

        if len(payload.order) != len(existing_names):
            raise HTTPException(status_code=400, detail="order length mismatch")
        if set(payload.order) != set(existing_names):
            raise HTTPException(status_code=400, detail="order must contain the same remote names")

        by_name = {str(s.get("name", "")): s for s in data}
        reordered = [by_name[name] for name in payload.order]
        _save_servers(reordered)

    return {
        "status": "ok",
        "servers": reordered,
    }
