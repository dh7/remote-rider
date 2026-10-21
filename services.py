import json
import os
import socket
from typing import Any
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from config import HERE, SERVICE_LOCK
from host_utils import _is_local_host, _normalize_host, _service_entry
from storage import _load_service_registry, _save_service_registry


def _kill_service_by_port(port: int) -> dict[str, Any]:
    import psutil
    killed_pid: int | None = None

    with SERVICE_LOCK:
        rows = _cleanup_service_registry(_load_service_registry())
        target = next((r for r in rows if int(r.get("port", 0)) == port), None)
        if target:
            rows = [r for r in rows if int(r.get("port", 0)) != port]
            _save_service_registry(rows)
            if isinstance(target.get("pid"), int):
                killed_pid = target["pid"]

    if killed_pid is not None:
        try:
            proc = psutil.Process(killed_pid)
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except psutil.TimeoutExpired:
                proc.kill()
            return {"status": "killed", "port": port, "pid": killed_pid, "source": "registry"}
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    try:
        for conn in psutil.net_connections(kind="inet"):
            if (conn.laddr and conn.laddr.port == port
                    and conn.status == "LISTEN" and conn.pid):
                try:
                    proc = psutil.Process(conn.pid)
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except psutil.TimeoutExpired:
                        proc.kill()
                    return {"status": "killed", "port": port, "pid": conn.pid, "source": "port_scan"}
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
    except (psutil.AccessDenied, PermissionError):
        pass

    if killed_pid is not None:
        return {"status": "removed_from_registry", "port": port}
    return {"status": "not_found", "port": port}


def _kill_remote_service(host: str, hub_port: int, port: int) -> dict[str, Any]:
    cleaned_host = _normalize_host(host)
    if _is_local_host(cleaned_host):
        return _kill_service_by_port(port)

    url = f"http://{cleaned_host}:{hub_port}/services/stop"
    body = json.dumps({"port": port}).encode("utf-8")
    req = UrlRequest(url, data=body, headers={"content-type": "application/json"}, method="POST")
    try:
        with urlopen(req, timeout=4) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("invalid response")
            payload.setdefault("source", "proxy")
            payload.setdefault("host", cleaned_host)
            return payload
    except Exception as exc:
        return {"status": "error", "port": port, "host": cleaned_host, "reason": str(exc)}


def _cleanup_service_registry(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from host_utils import _pid_alive
    from sandbox import _container_running
    alive: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("kind")) == "fileserver":
            continue  # legacy fileserver.py is retired; drop stale rows
        if str(row.get("kind")) == "sandbox":
            cid = str(row.get("container_id") or row.get("container_name", ""))
            if cid and _container_running(cid):
                alive.append(row)
        else:
            pid = row.get("pid")
            if isinstance(pid, int) and _pid_alive(pid):
                alive.append(row)
    return alive


def _local_services_snapshot() -> dict[str, Any]:
    term_enabled = os.getenv("DISABLE_TERMINAL") != "1"
    services = [
        _service_entry("terminal", int(os.getenv("TERM_PORT", "7681")), enabled=term_enabled),
        _service_entry("monitor", int(os.getenv("MONITOR_PORT", "8001"))),
        _service_entry("logs", int(os.getenv("LOGS_PORT", "8002"))),
        _service_entry("hub", int(os.getenv("HUB_PORT", "7000"))),
    ]

    with SERVICE_LOCK:
        rows = _cleanup_service_registry(_load_service_registry())
        _save_service_registry(rows)
        for row in rows:
            kind = str(row.get("kind", ""))
            try:
                port = int(row.get("port", 0))
            except Exception:
                continue
            if port <= 0:
                continue
            if kind == "sandbox":
                name = str(row.get("name", f"sandbox-{port}"))
                entry = _service_entry(name, port, enabled=True)
                entry["label"] = f"Sandbox: {row.get('branch', name)}"
                entry["kind"] = "sandbox"
                entry["container_id"] = str(row.get("container_id", ""))
                entry["branch"] = str(row.get("branch", ""))
                entry["repo"] = str(row.get("repo", ""))
                entry["launchable"] = True
                entry["embeddable"] = True
                services.append(entry)
            else:
                name = str(row.get("name", f"service-{port}"))
                entry = _service_entry(name, port, enabled=True)
                entry["label"] = str(row.get("label", name))
                entry["path"] = str(row.get("path", "/"))
                entry["protocol"] = str(row.get("protocol", "http"))
                entry["kind"] = kind or "service"
                entry["launchable"] = True
                entry["embeddable"] = row.get("embeddable") is not False
                if row.get("session_name"):
                    entry["session_name"] = str(row.get("session_name"))
                services.append(entry)

    return {
        "host": socket.gethostname(),
        "source": "local",
        "services": services,
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
