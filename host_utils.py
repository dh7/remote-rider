import os
import socket
import time
from typing import Any

from fastapi import HTTPException

from config import SERVICE_PANEL_DEFAULTS


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


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
