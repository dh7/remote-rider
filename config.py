import os
import threading
from pathlib import Path

from fastapi import HTTPException

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

SERVICE_PANEL_DEFAULTS: dict[str, dict] = {
    "terminal": {"label": "Terminal", "path": "/", "protocol": "http", "launchable": False, "embeddable": True},
    "monitor": {"label": "Monitor", "path": "/", "protocol": "http", "launchable": False, "embeddable": True},
    "logs": {"label": "Logs", "path": "/", "protocol": "http", "launchable": False, "embeddable": True},
    "files": {"label": "Files", "path": "/files", "protocol": "http", "launchable": True, "embeddable": True},
    "hub": {"label": "Hub", "path": "/", "protocol": "http", "launchable": False, "embeddable": False},
}


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
