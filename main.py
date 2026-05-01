import json
import os
import shlex
import shutil
import socket
import subprocess
import threading
import time
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
MACHINES_FILE = HERE / "machines.json"
LEGACY_SERVERS_FILE = HERE / "servers.json"
SESSIONS_FILE = HERE / "sessions.json"
TEMPLATES_FILE = HERE / "session_templates.json"
SERVICE_REGISTRY_FILE = HERE / "service_registry.json"
AGENT_REGISTRY_FILE = HERE / "agent_registry.json"
SERVERS_LOCK = threading.Lock()
SESSIONS_LOCK = threading.Lock()
SERVICE_LOCK = threading.Lock()
AGENT_LOCK = threading.Lock()
SERVICE_PANEL_DEFAULTS = {
    "terminal": {"label": "Terminal", "path": "/", "protocol": "http", "launchable": False, "embeddable": True},
    "monitor": {"label": "Monitor", "path": "/", "protocol": "http", "launchable": False, "embeddable": True},
    "logs": {"label": "Logs", "path": "/", "protocol": "http", "launchable": False, "embeddable": True},
    "files": {"label": "Files", "path": "/files", "protocol": "http", "launchable": True, "embeddable": True},
    "hub": {"label": "Hub", "path": "/", "protocol": "http", "launchable": False, "embeddable": False},
}


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


class StartFilesServiceRequest(BaseModel):
    port: int | None = Field(default=None, ge=1, le=65535)


class StartFilesServiceProxyRequest(BaseModel):
    host: str
    hub_port: int = Field(default=7000, ge=1, le=65535)
    port: int | None = Field(default=None, ge=1, le=65535)


class SessionsPutRequest(BaseModel):
    sessions: list[dict[str, Any]]


class SessionTabUpsertRequest(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    tab_id: str | None = Field(default=None, min_length=1, max_length=120)
    service: str | None = Field(default=None, min_length=1, max_length=80)
    port: int | None = Field(default=None, ge=1, le=65535)
    path: str = "/"
    protocol: str = "http"
    machine_name: str | None = Field(default=None, min_length=1, max_length=120)
    machine_host: str | None = Field(default=None, min_length=1, max_length=255)
    activate: bool = False


class SessionTabDeleteRequest(BaseModel):
    tab_id: str | None = Field(default=None, min_length=1, max_length=120)
    label: str | None = Field(default=None, min_length=1, max_length=120)


class AgentStartRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    command: str = Field(min_length=1, max_length=4000)
    cwd: str | None = Field(default=None, min_length=1, max_length=2000)
    tmux_session: str | None = Field(default=None, min_length=1, max_length=120)
    session_name: str | None = Field(default=None, min_length=1, max_length=120)
    machine_host: str | None = Field(default=None, min_length=1, max_length=255)


class AgentStopRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class AgentStartProxyRequest(AgentStartRequest):
    host: str
    hub_port: int = Field(default=7000, ge=1, le=65535)


class AgentStopProxyRequest(AgentStopRequest):
    host: str
    hub_port: int = Field(default=7000, ge=1, le=65535)


class RemoteUpdateRequest(BaseModel):
    branch: str = Field(default="main", min_length=1, max_length=120)


class RemoteUpdateProxyRequest(RemoteUpdateRequest):
    host: str
    hub_port: int = Field(default=7000, ge=1, le=65535)


class UpdateAllRemotesRequest(RemoteUpdateRequest):
    machines: list[str] | None = None


class RemoteGitCheckProxyRequest(BaseModel):
    host: str
    hub_port: int = Field(default=7000, ge=1, le=65535)
    branch: str = Field(default="main", min_length=1, max_length=120)


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


def _run_mode() -> str:
    raw = os.getenv("RUN_MODE", "all").strip().lower()
    if raw in {"control", "remote", "all"}:
        return raw
    return "all"


def _control_api_enabled() -> bool:
    return _run_mode() in {"control", "all"}


def _runtime_api_enabled() -> bool:
    return _run_mode() in {"remote", "all"}


def _require_control_api() -> None:
    if not _control_api_enabled():
        raise HTTPException(status_code=404, detail="control API not enabled in this mode")


def _require_runtime_api() -> None:
    if not _runtime_api_enabled():
        raise HTTPException(status_code=404, detail="runtime API not enabled in this mode")


def _load_machine_inventory() -> list[dict[str, Any]]:
    source_file = MACHINES_FILE if MACHINES_FILE.exists() else LEGACY_SERVERS_FILE
    if not source_file.exists():
        return []
    data = json.loads(source_file.read_text())
    if not isinstance(data, list):
        raise HTTPException(status_code=500, detail=f"{source_file.name} must contain a JSON list")
    return data


def _save_machine_inventory(machines: list[dict[str, Any]]) -> None:
    MACHINES_FILE.write_text(json.dumps(machines, indent=2) + "\n")


def _load_control_sessions() -> list[dict[str, Any]]:
    if not SESSIONS_FILE.exists():
        return []
    try:
        data = json.loads(SESSIONS_FILE.read_text())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{SESSIONS_FILE.name} is invalid JSON") from exc
    if not isinstance(data, list):
        raise HTTPException(status_code=500, detail=f"{SESSIONS_FILE.name} must contain a JSON list")
    return [row for row in data if isinstance(row, dict)]


def _save_control_sessions(sessions: list[dict[str, Any]]) -> None:
    SESSIONS_FILE.write_text(json.dumps(sessions, indent=2) + "\n")


def _tab_slug(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    collapsed = "-".join(part for part in cleaned.split("-") if part)
    return collapsed or "tab"


def _normalize_session_tab(payload: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    label = str(payload.get("label", "")).strip()
    if not label:
        return None
    tab_id_raw = str(payload.get("id") or payload.get("tab_id") or "").strip()
    tab: dict[str, Any] = {
        "id": tab_id_raw or f"tab-{_tab_slug(label)}",
        "label": label,
    }
    service = str(payload.get("service", "")).strip()
    if service:
        tab["service"] = service
    port = payload.get("port")
    if isinstance(port, int) and port > 0:
        tab["port"] = port
    path = str(payload.get("path", "")).strip()
    if path:
        tab["path"] = _normalize_path(path)
    protocol = str(payload.get("protocol", "")).strip().lower()
    if protocol:
        tab["protocol"] = _normalize_protocol(protocol)
    return tab


def _normalize_control_session(payload: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    name = str(payload.get("name", "")).strip()
    if not name:
        return None
    display = str(payload.get("display", "")).strip()
    machine_raw = payload.get("machine")
    machine_host = ""
    machine_name = ""
    if isinstance(machine_raw, dict):
        machine_host = str(machine_raw.get("host", "")).strip()
        machine_name = str(machine_raw.get("name", "")).strip()
    if not machine_host:
        machine_host = str(payload.get("ip", "")).strip()
    if not machine_name:
        machine_name = name
    raw_tabs = payload.get("tabs", payload.get("panels", []))
    tabs = []
    if isinstance(raw_tabs, list):
        for row in raw_tabs:
            tab = _normalize_session_tab(row)
            if tab:
                tabs.append(tab)
    session = {
        "name": name,
        "machine": {
            "name": machine_name,
            "host": machine_host or "127.0.0.1",
        },
        "tabs": tabs,
    }
    if display:
        session["display"] = display
    return session


def _load_normalized_control_sessions() -> list[dict[str, Any]]:
    return [session for session in (_normalize_control_session(row) for row in _load_control_sessions()) if session]


def _save_normalized_control_sessions(sessions: list[dict[str, Any]]) -> None:
    _save_control_sessions(sessions)


def _load_agent_registry() -> list[dict[str, Any]]:
    if not AGENT_REGISTRY_FILE.exists():
        return []
    try:
        data = json.loads(AGENT_REGISTRY_FILE.read_text())
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def _save_agent_registry(rows: list[dict[str, Any]]) -> None:
    AGENT_REGISTRY_FILE.write_text(json.dumps(rows, indent=2) + "\n")


def _default_templates() -> list[dict[str, Any]]:
    return [
        {
            "id": "standard",
            "label": "Standard",
            "panels": [
                {"label": "Terminal", "port": 7681},
                {"label": "Monitor", "port": 8001},
                {"label": "Logs", "port": 8002},
                {"label": "Files", "port": 8080, "path": "/files"},
            ],
        },
        {
            "id": "ops",
            "label": "Ops (no terminal)",
            "panels": [
                {"label": "Monitor", "port": 8001},
                {"label": "Logs", "port": 8002},
                {"label": "Files", "port": 8080, "path": "/files"},
            ],
        },
    ]


def _load_templates() -> list[dict[str, Any]]:
    if not TEMPLATES_FILE.exists():
        return _default_templates()
    try:
        data = json.loads(TEMPLATES_FILE.read_text())
    except Exception:
        return _default_templates()
    if not isinstance(data, list):
        return _default_templates()

    out: list[dict[str, Any]] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        tid = str(raw.get("id", "")).strip()
        label = str(raw.get("label", tid)).strip()
        panels = raw.get("panels")
        if not tid or not isinstance(panels, list):
            continue
        normalized = []
        for panel in panels:
            if not isinstance(panel, dict):
                continue
            p_label = str(panel.get("label", "")).strip()
            p_port = panel.get("port")
            if not p_label or not isinstance(p_port, int):
                continue
            p_obj: dict[str, Any] = {"label": p_label, "port": p_port}
            if panel.get("path"):
                p_obj["path"] = str(panel.get("path"))
            if panel.get("protocol"):
                p_obj["protocol"] = str(panel.get("protocol"))
            normalized.append(p_obj)
        if normalized:
            out.append({"id": tid, "label": label or tid, "panels": normalized})

    return out or _default_templates()


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


def _fetch_remote_servers(host: str, port: int) -> dict[str, Any]:
    cleaned_host = _normalize_host(host)
    if _is_local_host(cleaned_host):
        return {
            "ok": True,
            "servers": _apply_port_overrides(_load_machine_inventory()),
            "host": socket.gethostname(),
            "source": "local",
        }

    url = f"http://{cleaned_host}:{port}/servers"
    req = UrlRequest(url, method="GET")
    try:
        with urlopen(req, timeout=2.5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            if not isinstance(payload, list):
                raise ValueError("invalid response shape")
            return {
                "ok": True,
                "servers": payload,
                "host": cleaned_host,
                "source": "proxy",
            }
    except Exception as exc:
        return {
            "ok": False,
            "servers": [],
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


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _load_service_registry() -> list[dict[str, Any]]:
    if not SERVICE_REGISTRY_FILE.exists():
        return []
    try:
        data = json.loads(SERVICE_REGISTRY_FILE.read_text())
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def _save_service_registry(rows: list[dict[str, Any]]) -> None:
    SERVICE_REGISTRY_FILE.write_text(json.dumps(rows, indent=2) + "\n")


def _cleanup_service_registry(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    alive: list[dict[str, Any]] = []
    for row in rows:
        pid = row.get("pid")
        if isinstance(pid, int) and _pid_alive(pid):
            alive.append(row)
    return alive


def _agent_entry_alive(row: dict[str, Any]) -> bool:
    tmux_session = str(row.get("tmux_session", "")).strip()
    if tmux_session:
        return bool(shutil.which("tmux")) and _tmux_session_exists(tmux_session)
    pid = row.get("pid")
    return isinstance(pid, int) and _pid_alive(pid)


def _cleanup_agent_registry(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if _agent_entry_alive(row)]


def _list_local_agents() -> list[dict[str, Any]]:
    with AGENT_LOCK:
        rows = _cleanup_agent_registry(_load_agent_registry())
        _save_agent_registry(rows)

    agents: list[dict[str, Any]] = []
    for row in rows:
        entry = dict(row)
        entry["status"] = "running" if _agent_entry_alive(row) else "stopped"
        agents.append(entry)
    return agents


def _start_agent_local(payload: AgentStartRequest) -> dict[str, Any]:
    name = payload.name.strip()
    command = payload.command.strip()
    if not name or not command:
        raise HTTPException(status_code=400, detail="name and command are required")

    cwd = str((Path(payload.cwd).expanduser().resolve() if payload.cwd else HERE))
    tmux_session = (payload.tmux_session or name).strip()

    with AGENT_LOCK:
        rows = _cleanup_agent_registry(_load_agent_registry())
        existing = next((row for row in rows if str(row.get("name")) == name), None)
        if existing is not None:
            return {"status": "exists", "agent": existing}

        log_dir = HERE / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"agent-{_tab_slug(name)}.log"

        entry: dict[str, Any] = {
            "name": name,
            "command": command,
            "cwd": cwd,
            "session_name": payload.session_name,
            "machine_host": payload.machine_host or os.getenv("PUBLIC_HOST") or socket.gethostname(),
            "started_at": int(time.time()),
        }

        if shutil.which("tmux"):
            escaped_command = command.replace("'", "'\"'\"'")
            shell_line = f"cd '{cwd}' && {escaped_command}"
            result = subprocess.run(
                ["tmux", "new-session", "-d", "-s", tmux_session, shell_line],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                raise HTTPException(status_code=500, detail=result.stderr.strip() or "failed to start tmux agent")
            entry["tmux_session"] = tmux_session
            entry["backend"] = "tmux"
        else:
            log_handle = log_path.open("a")
            proc = subprocess.Popen(
                command,
                cwd=cwd,
                shell=True,
                executable="/bin/bash",
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                close_fds=True,
            )
            log_handle.close()
            entry["pid"] = proc.pid
            entry["backend"] = "process"

        entry["log_path"] = str(log_path)
        rows.append(entry)
        _save_agent_registry(rows)

    return {"status": "ok", "agent": entry}


def _stop_agent_local(payload: AgentStopRequest) -> dict[str, Any]:
    with AGENT_LOCK:
        rows = _cleanup_agent_registry(_load_agent_registry())
        target = next((row for row in rows if str(row.get("name")) == payload.name), None)
        if target is None:
            return {"status": "not_found", "name": payload.name}

        backend = str(target.get("backend", ""))
        stopped = False
        if backend == "tmux" and target.get("tmux_session"):
            stopped = _kill_tmux_session(str(target["tmux_session"]))
        elif isinstance(target.get("pid"), int):
            pid = int(target["pid"])
            try:
                os.kill(pid, 15)
                time.sleep(0.2)
                if _pid_alive(pid):
                    os.kill(pid, 9)
                stopped = True
            except OSError:
                stopped = False

        rows = [row for row in rows if str(row.get("name")) != payload.name]
        _save_agent_registry(rows)

    return {"status": "stopped" if stopped else "not_found", "name": payload.name}


def _fetch_remote_agents(host: str, port: int) -> dict[str, Any]:
    cleaned_host = _normalize_host(host)
    if _is_local_host(cleaned_host):
        return {
            "host": socket.gethostname(),
            "source": "local",
            "agents": _list_local_agents(),
        }

    url = f"http://{cleaned_host}:{port}/agents/runtime"
    req = UrlRequest(url, method="GET")
    try:
        with urlopen(req, timeout=3) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("invalid response shape")
            payload.setdefault("source", "proxy")
            payload.setdefault("host", cleaned_host)
            return payload
    except Exception as exc:
        return {
            "host": cleaned_host,
            "source": "proxy",
            "agents": [],
            "error": str(exc),
        }


def _start_remote_agent(payload: AgentStartProxyRequest) -> dict[str, Any]:
    cleaned_host = _normalize_host(payload.host)
    if _is_local_host(cleaned_host):
        return _start_agent_local(
            AgentStartRequest(
                name=payload.name,
                command=payload.command,
                cwd=payload.cwd,
                tmux_session=payload.tmux_session,
                session_name=payload.session_name,
                machine_host=payload.machine_host or cleaned_host,
            )
        )

    body = json.dumps(
        {
            "name": payload.name,
            "command": payload.command,
            "cwd": payload.cwd,
            "tmux_session": payload.tmux_session,
            "session_name": payload.session_name,
            "machine_host": payload.machine_host or cleaned_host,
        }
    ).encode("utf-8")
    req = UrlRequest(
        f"http://{cleaned_host}:{payload.hub_port}/agents/start",
        data=body,
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=4) as resp:
            payload_out = json.loads(resp.read().decode("utf-8"))
            if not isinstance(payload_out, dict):
                raise ValueError("invalid response shape")
            payload_out.setdefault("source", "proxy")
            payload_out.setdefault("host", cleaned_host)
            return payload_out
    except Exception as exc:
        return {"status": "error", "host": cleaned_host, "source": "proxy", "reason": str(exc)}


def _stop_remote_agent(payload: AgentStopProxyRequest) -> dict[str, Any]:
    cleaned_host = _normalize_host(payload.host)
    if _is_local_host(cleaned_host):
        return _stop_agent_local(AgentStopRequest(name=payload.name))

    body = json.dumps({"name": payload.name}).encode("utf-8")
    req = UrlRequest(
        f"http://{cleaned_host}:{payload.hub_port}/agents/stop",
        data=body,
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=4) as resp:
            payload_out = json.loads(resp.read().decode("utf-8"))
            if not isinstance(payload_out, dict):
                raise ValueError("invalid response shape")
            payload_out.setdefault("source", "proxy")
            payload_out.setdefault("host", cleaned_host)
            return payload_out
    except Exception as exc:
        return {"status": "error", "host": cleaned_host, "source": "proxy", "reason": str(exc)}


def _schedule_remote_update_local(branch: str) -> dict[str, Any]:
    branch_name = branch.strip() or "main"
    script_path = HERE / "update-remote.sh"
    if not script_path.exists():
        return {
            "status": "error",
            "reason": f"missing {script_path.name}",
        }

    command = f"sleep 1; {shlex.quote(str(script_path))} {shlex.quote(branch_name)}"
    with open(os.devnull, "wb") as devnull:
        proc = subprocess.Popen(
            ["/bin/bash", "-lc", command],
            cwd=str(HERE),
            stdout=devnull,
            stderr=devnull,
            close_fds=True,
            start_new_session=True,
        )

    return {
        "status": "scheduled",
        "branch": branch_name,
        "pid": proc.pid,
        "log_path": str(HERE / "logs" / "update-remote.log"),
        "script": str(script_path.name),
    }


def _schedule_remote_update_proxy(payload: RemoteUpdateProxyRequest) -> dict[str, Any]:
    cleaned_host = _normalize_host(payload.host)
    if _is_local_host(cleaned_host):
        return _schedule_remote_update_local(payload.branch)

    body = json.dumps({"branch": payload.branch}).encode("utf-8")
    req = UrlRequest(
        f"http://{cleaned_host}:{payload.hub_port}/admin/update-remote",
        data=body,
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=4) as resp:
            payload_out = json.loads(resp.read().decode("utf-8"))
            if not isinstance(payload_out, dict):
                raise ValueError("invalid response shape")
            payload_out.setdefault("source", "proxy")
            payload_out.setdefault("host", cleaned_host)
            return payload_out
    except Exception as exc:
        return {
            "status": "error",
            "host": cleaned_host,
            "source": "proxy",
            "reason": str(exc),
        }


def _machine_host_from_inventory(machine: dict[str, Any]) -> str:
    return str(machine.get("ip", "")).strip()


def _tail_text_file(path: Path, max_lines: int = 80) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(errors="replace").splitlines()
    return "\n".join(lines[-max_lines:])


def _git_remote_summary(branch: str) -> dict[str, Any]:
    remote_url = ""
    try:
        remote_url = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(HERE),
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
    except Exception:
        remote_url = ""

    result = subprocess.run(
        ["git", "ls-remote", "--heads", "origin", branch],
        cwd=str(HERE),
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )

    return {
        "ok": result.returncode == 0,
        "branch": branch,
        "remote_url": remote_url,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "returncode": result.returncode,
    }


def _local_update_diagnostics(branch: str) -> dict[str, Any]:
    return {
        "host": socket.gethostname(),
        "branch": branch,
        "git": _git_remote_summary(branch),
        "update_log_tail": _tail_text_file(HERE / "logs" / "update-remote.log"),
        "head": subprocess.run(
            ["git", "log", "-1", "--oneline"],
            cwd=str(HERE),
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip(),
    }


def _fetch_remote_update_diagnostics(host: str, port: int, branch: str) -> dict[str, Any]:
    cleaned_host = _normalize_host(host)
    if _is_local_host(cleaned_host):
        return _local_update_diagnostics(branch)

    body = json.dumps({"host": cleaned_host, "hub_port": port, "branch": branch}).encode("utf-8")
    req = UrlRequest(
        f"http://{cleaned_host}:{port}/admin/update-diagnostics",
        data=body,
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=8) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("invalid response shape")
            payload.setdefault("host", cleaned_host)
            payload.setdefault("source", "proxy")
            return payload
    except Exception as exc:
        return {
            "host": cleaned_host,
            "source": "proxy",
            "branch": branch,
            "error": str(exc),
        }


def _is_port_busy_for_bind(port: int, bind_host: str = "0.0.0.0") -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((bind_host, port))
        return False
    except OSError:
        return True
    finally:
        sock.close()


def _pick_free_port(preferred: int, bind_host: str = "0.0.0.0") -> int:
    port = preferred
    while _is_port_busy_for_bind(port, bind_host=bind_host):
        port += 1
    return port


def _probe_panel_port(host: str, port: int, timeout_ms: int = 650) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout_ms / 1000):
            pass
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {"up": True, "latency_ms": elapsed_ms}
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {"up": False, "latency_ms": elapsed_ms, "error": str(exc)}


def _service_entry(name: str, port: int, *, enabled: bool = True, pid: int | None = None) -> dict[str, Any]:
    host_for_probe = os.getenv("BIND_HOST", "127.0.0.1")
    if enabled:
        probe = _probe_panel_port(host_for_probe, port)
    else:
        probe = {"up": False, "error": "disabled"}
    service_defaults = SERVICE_PANEL_DEFAULTS.get(name, {})
    return {
        "name": name,
        "label": service_defaults.get("label", name.title()),
        "port": port,
        "enabled": enabled,
        "pid": pid,
        "up": bool(probe.get("up", False)),
        "latency_ms": probe.get("latency_ms"),
        "error": probe.get("error"),
        "path": service_defaults.get("path", "/"),
        "protocol": service_defaults.get("protocol", "http"),
        "launchable": bool(service_defaults.get("launchable", False)),
        "embeddable": bool(service_defaults.get("embeddable", True)),
    }


def _local_services_snapshot() -> dict[str, Any]:
    term_enabled = os.getenv("DISABLE_TERMINAL") != "1"
    services = [
        _service_entry("terminal", int(os.getenv("TERM_PORT", "7681")), enabled=term_enabled),
        _service_entry("monitor", int(os.getenv("MONITOR_PORT", "8001"))),
        _service_entry("logs", int(os.getenv("LOGS_PORT", "8002"))),
        _service_entry("files", int(os.getenv("FILES_PORT", "8080"))),
        _service_entry("hub", int(os.getenv("HUB_PORT", "7000"))),
    ]

    with SERVICE_LOCK:
        rows = _cleanup_service_registry(_load_service_registry())
        _save_service_registry(rows)
        for row in rows:
            if str(row.get("kind")) != "fileserver":
                continue
            try:
                port = int(row.get("port", 0))
            except Exception:
                continue
            if port <= 0:
                continue
            pid = row.get("pid") if isinstance(row.get("pid"), int) else None
            services.append(
                _service_entry(
                    str(row.get("name", "files-extra")),
                    port,
                    enabled=True,
                    pid=pid,
                )
            )
            services[-1]["label"] = "Files"
            services[-1]["path"] = "/files"
            services[-1]["protocol"] = "http"
            services[-1]["launchable"] = True
            services[-1]["kind"] = "fileserver"

    return {
        "host": socket.gethostname(),
        "source": "local",
        "services": services,
    }


def _start_files_service_local(preferred_port: int | None) -> dict[str, Any]:
    venv_uvicorn = HERE / ".venv" / "bin" / "uvicorn"
    if not venv_uvicorn.exists():
        return {
            "status": "error",
            "reason": f"missing {venv_uvicorn}",
        }

    bind_host = os.getenv("BIND_HOST", "0.0.0.0")
    wanted = preferred_port if preferred_port is not None else int(os.getenv("FILES_PORT", "8080")) + 1
    port = _pick_free_port(wanted, bind_host=bind_host)

    log_dir = HERE / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"fileserver-{port}.log"
    log_handle = log_path.open("a")

    proc = subprocess.Popen(
        [
            str(venv_uvicorn),
            "fileserver:app",
            "--host",
            bind_host,
            "--port",
            str(port),
        ],
        cwd=str(HERE),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        close_fds=True,
    )
    log_handle.close()

    row = {
        "kind": "fileserver",
        "name": f"files-{port}",
        "port": port,
        "pid": proc.pid,
        "started_at": int(time.time()),
    }

    with SERVICE_LOCK:
        rows = _cleanup_service_registry(_load_service_registry())
        rows.append(row)
        _save_service_registry(rows)

    return {
        "status": "ok",
        "service": row,
        "bind_host": bind_host,
        "url": f"http://{bind_host}:{port}/files",
    }


def _fetch_remote_services(host: str, port: int) -> dict[str, Any]:
    cleaned_host = _normalize_host(host)
    if _is_local_host(cleaned_host):
        return _local_services_snapshot()

    url = f"http://{cleaned_host}:{port}/services"
    req = UrlRequest(url, method="GET")
    try:
        with urlopen(req, timeout=3) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("invalid response shape")
            payload.setdefault("source", "proxy")
            payload.setdefault("host", cleaned_host)
            return payload
    except Exception as exc:
        return {
            "host": cleaned_host,
            "source": "proxy",
            "services": [],
            "error": str(exc),
        }


def _start_remote_files_service(host: str, hub_port: int, preferred_port: int | None) -> dict[str, Any]:
    cleaned_host = _normalize_host(host)
    if _is_local_host(cleaned_host):
        return _start_files_service_local(preferred_port)

    url = f"http://{cleaned_host}:{hub_port}/services/files/start"
    body = json.dumps({"port": preferred_port}).encode("utf-8")
    req = UrlRequest(url, data=body, headers={"content-type": "application/json"}, method="POST")
    try:
        with urlopen(req, timeout=4) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("invalid response shape")
            payload.setdefault("source", "proxy")
            payload.setdefault("host", cleaned_host)
            return payload
    except Exception as exc:
        return {
            "status": "error",
            "source": "proxy",
            "host": cleaned_host,
            "reason": str(exc),
        }


def _agents_text(request: Request) -> str:
    base = str(request.base_url).rstrip("/")
    return f"""SlashAgents-Version: 0.1
API: Web Terminal Hub

Context:
- This API manages a local web-terminal dashboard and panel tabs.
- UI profile state is persisted in client localStorage.
- GET /servers returns bootstrap defaults from machines.json (or legacy servers.json).

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
- 500 malformed machines.json

Specs:
- OpenAPI: {base}/openapi.json
- Docs: {base}/docs

Runtime:
- Mode: GET {base}/api/info
- Machine inventory: GET {base}/machines
- Legacy inventory alias: GET {base}/servers
- Control context: GET {base}/control/context
- Control-side sessions: GET/PUT {base}/sessions
- Session detail: GET {base}/sessions/<session_name>
- Agent tab upsert: POST {base}/sessions/<session_name>/tabs
- Agent tab delete: DELETE {base}/sessions/<session_name>/tabs
- panel health probe: GET {base}/panel/status?host=<ip>&port=<port>
- local services: GET {base}/services
- remote services: GET {base}/services/proxy?host=<ip>&port=7000
- start files service: POST {base}/services/files/start
- start remote files service: POST {base}/services/files/start/proxy
- tmux sessions: GET {base}/tmux/sessions
- tmux sessions by host: GET {base}/tmux/sessions/proxy?host=<ip>&port=7000
- local agents: GET {base}/agents/runtime
- remote agents: GET {base}/agents/runtime/proxy?host=<ip>&port=7000
- start local agent: POST {base}/agents/start
- start remote agent: POST {base}/agents/start/proxy
- stop local agent: POST {base}/agents/stop
- stop remote agent: POST {base}/agents/stop/proxy
- schedule local remote-stack update: POST {base}/admin/update-remote
- schedule proxied remote-stack update: POST {base}/admin/update-remote/proxy
- schedule fleet remote-stack update: POST {base}/admin/update-all-remotes
- inspect local update diagnostics: POST {base}/admin/update-diagnostics
- inspect proxied update diagnostics: POST {base}/admin/update-diagnostics/proxy
- machine panels by host: GET {base}/machines/proxy?host=<ip>&port=7000
- legacy machine-panels alias: GET {base}/servers/proxy?host=<ip>&port=7000
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

curl -X POST {base}/sessions/netochka-job1/tabs \
  -H 'content-type: application/json' \
  -d '{{"label":"Preview","service":"preview","port":8123,"path":"/","protocol":"http","activate":true}}'

curl -X POST {base}/agents/start/proxy \
  -H 'content-type: application/json' \
  -d '{{"host":"100.119.43.10","name":"agent-review","command":"codex --dangerously-bypass-approvals-and-sandbox","session_name":"demo-session"}}'

curl -X POST {base}/admin/update-remote/proxy \
  -H 'content-type: application/json' \
  -d '{{"host":"100.119.43.10","branch":"main"}}'

curl -X POST {base}/admin/update-all-remotes \
  -H 'content-type: application/json' \
  -d '{{"branch":"main"}}'

curl -X POST {base}/admin/update-diagnostics/proxy \
  -H 'content-type: application/json' \
  -d '{{"host":"100.119.43.10","branch":"main"}}'
"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (HERE / "index.html").read_text()


@app.get("/api/info")
def api_info() -> dict[str, Any]:
    mode = _run_mode()
    return {
        "mode": mode,
        "control_api": _control_api_enabled(),
        "runtime_api": _runtime_api_enabled(),
    }


@app.get("/control/context")
def control_context() -> dict[str, Any]:
    _require_control_api()
    with SESSIONS_LOCK:
        sessions = _load_normalized_control_sessions()
    return {
        "mode": _run_mode(),
        "machines": servers(),
        "sessions": sessions,
        "session_templates": _load_templates(),
    }


@app.get("/servers")
def servers() -> list[dict]:
    return _apply_port_overrides(_load_machine_inventory())


@app.get("/machines")
def machines() -> list[dict]:
    return servers()


@app.get("/session-templates")
def session_templates() -> list[dict[str, Any]]:
    return _load_templates()


@app.get("/sessions")
def sessions() -> dict[str, Any]:
    _require_control_api()
    with SESSIONS_LOCK:
        return {
            "sessions": _load_normalized_control_sessions(),
            "storage": str(SESSIONS_FILE.name),
        }


@app.put("/sessions")
def replace_sessions(payload: SessionsPutRequest) -> dict[str, Any]:
    _require_control_api()
    normalized = [session for session in (_normalize_control_session(row) for row in payload.sessions) if session]
    with SESSIONS_LOCK:
        _save_normalized_control_sessions(normalized)
    return {
        "status": "ok",
        "count": len(normalized),
        "storage": str(SESSIONS_FILE.name),
    }


@app.get("/sessions/{session_name}")
def session_by_name(session_name: str) -> dict[str, Any]:
    _require_control_api()
    with SESSIONS_LOCK:
        sessions = _load_normalized_control_sessions()
    session = next((row for row in sessions if row.get("name") == session_name), None)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return {"session": session}


@app.get("/agents/runtime")
def agents_runtime() -> dict[str, Any]:
    _require_runtime_api()
    return {
        "host": socket.gethostname(),
        "source": "local",
        "agents": _list_local_agents(),
    }


@app.get("/agents/runtime/proxy")
def agents_runtime_proxy(host: str = Query(...), port: int = Query(7000, ge=1, le=65535)) -> dict[str, Any]:
    return _fetch_remote_agents(host, port)


@app.post("/agents/start")
def agents_start(payload: AgentStartRequest) -> dict[str, Any]:
    _require_runtime_api()
    return _start_agent_local(payload)


@app.post("/agents/start/proxy")
def agents_start_proxy(payload: AgentStartProxyRequest) -> dict[str, Any]:
    return _start_remote_agent(payload)


@app.post("/agents/stop")
def agents_stop(payload: AgentStopRequest) -> dict[str, Any]:
    _require_runtime_api()
    return _stop_agent_local(payload)


@app.post("/agents/stop/proxy")
def agents_stop_proxy(payload: AgentStopProxyRequest) -> dict[str, Any]:
    return _stop_remote_agent(payload)


@app.post("/admin/update-remote")
def admin_update_remote(payload: RemoteUpdateRequest) -> dict[str, Any]:
    _require_runtime_api()
    return _schedule_remote_update_local(payload.branch)


@app.post("/admin/update-remote/proxy")
def admin_update_remote_proxy(payload: RemoteUpdateProxyRequest) -> dict[str, Any]:
    return _schedule_remote_update_proxy(payload)


@app.post("/admin/update-all-remotes")
def admin_update_all_remotes(payload: UpdateAllRemotesRequest) -> dict[str, Any]:
    _require_control_api()
    machines = _load_machine_inventory()
    selected = set(payload.machines or [])
    results: list[dict[str, Any]] = []

    for machine in machines:
      name = str(machine.get("name", "")).strip()
      host = _machine_host_from_inventory(machine)
      if not name or not host:
          continue
      if selected and name not in selected:
          continue
      result = _schedule_remote_update_proxy(
          RemoteUpdateProxyRequest(
              host=host,
              hub_port=7000,
              branch=payload.branch,
          )
      )
      result.setdefault("machine", name)
      result.setdefault("host", host)
      results.append(result)

    return {
        "status": "ok",
        "branch": payload.branch,
        "results": results,
    }


@app.post("/admin/update-diagnostics")
def admin_update_diagnostics(payload: RemoteUpdateRequest) -> dict[str, Any]:
    _require_runtime_api()
    return _local_update_diagnostics(payload.branch)


@app.post("/admin/update-diagnostics/proxy")
def admin_update_diagnostics_proxy(payload: RemoteGitCheckProxyRequest) -> dict[str, Any]:
    return _fetch_remote_update_diagnostics(payload.host, payload.hub_port, payload.branch)


@app.post("/sessions/{session_name}/tabs")
def upsert_session_tab(session_name: str, payload: SessionTabUpsertRequest) -> dict[str, Any]:
    _require_control_api()
    normalized_tab = _normalize_session_tab(
        {
            "id": payload.tab_id or "",
            "label": payload.label,
            "service": payload.service,
            "port": payload.port,
            "path": payload.path,
            "protocol": payload.protocol,
        }
    )
    if normalized_tab is None:
        raise HTTPException(status_code=400, detail="invalid tab payload")

    with SESSIONS_LOCK:
        sessions = _load_normalized_control_sessions()
        session = next((row for row in sessions if row.get("name") == session_name), None)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")

        machine = session.setdefault("machine", {})
        if payload.machine_name:
            machine["name"] = payload.machine_name
        if payload.machine_host:
            machine["host"] = payload.machine_host

        tabs = session.setdefault("tabs", [])
        match = None
        requested_id = normalized_tab.get("id")
        for tab in tabs:
            if requested_id and tab.get("id") == requested_id:
                match = tab
                break
            if tab.get("label") == normalized_tab.get("label"):
                match = tab
                break

        action = "created"
        if match is None:
            tabs.append(normalized_tab)
            match = normalized_tab
        else:
            match.update(normalized_tab)
            action = "updated"

        if payload.activate:
            session["active_tab"] = str(match.get("id") or match.get("label"))

        _save_normalized_control_sessions(sessions)

    return {
        "status": "ok",
        "action": action,
        "session": session,
        "tab": match,
    }


@app.delete("/sessions/{session_name}/tabs")
def delete_session_tab(session_name: str, payload: SessionTabDeleteRequest) -> dict[str, Any]:
    _require_control_api()
    if not payload.tab_id and not payload.label:
        raise HTTPException(status_code=400, detail="tab_id or label is required")

    with SESSIONS_LOCK:
        sessions = _load_normalized_control_sessions()
        session = next((row for row in sessions if row.get("name") == session_name), None)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")

        tabs = session.setdefault("tabs", [])
        target_index = next(
            (
                idx
                for idx, tab in enumerate(tabs)
                if (payload.tab_id and tab.get("id") == payload.tab_id)
                or (payload.label and tab.get("label") == payload.label)
            ),
            None,
        )
        if target_index is None:
            raise HTTPException(status_code=404, detail="tab not found")

        removed = tabs.pop(target_index)
        if session.get("active_tab") in {removed.get("id"), removed.get("label")}:
            session.pop("active_tab", None)

        _save_normalized_control_sessions(sessions)

    return {
        "status": "ok",
        "removed": removed,
        "session": session,
    }


@app.get("/panel/status")
def panel_status(
    host: str = Query("127.0.0.1"),
    port: int = Query(..., ge=1, le=65535),
    timeout_ms: int = Query(650, ge=100, le=5000),
) -> dict[str, Any]:
    cleaned_host = _normalize_host(host)
    probe_host = "127.0.0.1" if _is_local_host(cleaned_host) else cleaned_host
    result = _probe_panel_port(probe_host, port, timeout_ms=timeout_ms)
    result["host"] = cleaned_host
    result["port"] = port
    return result


@app.get("/services")
def services_status() -> dict[str, Any]:
    _require_runtime_api()
    return _local_services_snapshot()


@app.get("/services/proxy")
def services_proxy(host: str = Query(...), port: int = Query(7000, ge=1, le=65535)) -> dict[str, Any]:
    return _fetch_remote_services(host, port)


@app.post("/services/files/start")
def start_files_service(payload: StartFilesServiceRequest) -> dict[str, Any]:
    _require_runtime_api()
    return _start_files_service_local(payload.port)


@app.post("/services/files/start/proxy")
def start_files_service_proxy(payload: StartFilesServiceProxyRequest) -> dict[str, Any]:
    return _start_remote_files_service(payload.host, payload.hub_port, payload.port)


@app.get("/tmux/sessions")
def tmux_sessions() -> dict[str, Any]:
    _require_runtime_api()
    return {
        "available": bool(shutil.which("tmux")),
        "sessions": _list_tmux_sessions(),
        "host": socket.gethostname(),
    }


@app.get("/tmux/sessions/proxy")
def tmux_sessions_proxy(host: str = Query(...), port: int = Query(7000, ge=1, le=65535)) -> dict[str, Any]:
    return _fetch_remote_tmux_sessions(host, port)


@app.get("/servers/proxy")
def servers_proxy(host: str = Query(...), port: int = Query(7000, ge=1, le=65535)) -> dict[str, Any]:
    return _fetch_remote_servers(host, port)


@app.get("/machines/proxy")
def machines_proxy(host: str = Query(...), port: int = Query(7000, ge=1, le=65535)) -> dict[str, Any]:
    return _fetch_remote_servers(host, port)


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
        data = _load_machine_inventory()

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

        _save_machine_inventory(data)

    return {
        "status": "ok",
        "action": action,
        "server": server,
    }


@app.post("/remote")
def manage_remote(payload: RemoteRequest) -> dict[str, Any]:
    with SERVERS_LOCK:
        data = _load_machine_inventory()
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

            _save_machine_inventory(data)
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

        _save_machine_inventory(data)

    return {
        "status": "ok",
        "action": action,
        "remote": remote,
        "servers": data,
    }


@app.post("/remote/reorder")
def reorder_remotes(payload: ReorderRequest) -> dict[str, Any]:
    with SERVERS_LOCK:
        data = _load_machine_inventory()
        existing_names = [str(s.get("name", "")) for s in data]

        if len(payload.order) != len(existing_names):
            raise HTTPException(status_code=400, detail="order length mismatch")
        if set(payload.order) != set(existing_names):
            raise HTTPException(status_code=400, detail="order must contain the same remote names")

        by_name = {str(s.get("name", "")): s for s in data}
        reordered = [by_name[name] for name in payload.order]
        _save_machine_inventory(reordered)

    return {
        "status": "ok",
        "servers": reordered,
    }
