import json
import os
from typing import Any
from urllib.parse import parse_qs, urlparse, quote

from fastapi import HTTPException

from config import (
    MACHINES_FILE,
    LEGACY_SERVERS_FILE,
    SESSIONS_FILE,
    TEMPLATES_FILE,
    AGENT_REGISTRY_FILE,
    SERVICE_REGISTRY_FILE,
    _normalize_path,
    _normalize_protocol,
)


# ── Machine inventory ────────────────────────────────────────────────────────

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


# ── Sessions ─────────────────────────────────────────────────────────────────

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
    session: dict[str, Any] = {
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


def _load_normalized_control_sessions() -> list[dict[str, Any]]:
    return [s for s in (_normalize_control_session(row) for row in _load_control_sessions()) if s]


def _save_normalized_control_sessions(sessions: list[dict[str, Any]]) -> None:
    _save_control_sessions(sessions)


# ── Session templates ─────────────────────────────────────────────────────────

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


# ── Panel helpers ─────────────────────────────────────────────────────────────

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


# ── Agent registry ────────────────────────────────────────────────────────────

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


# ── Service registry ──────────────────────────────────────────────────────────

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
