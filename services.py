import json
import os
import socket
import subprocess
import time
from typing import Any
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from config import HERE, SERVICE_LOCK
from host_utils import _is_local_host, _normalize_host, _pick_free_port, _service_entry
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
        _service_entry("files", int(os.getenv("FILES_PORT", "8080"))),
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
            if kind == "fileserver":
                pid = row.get("pid") if isinstance(row.get("pid"), int) else None
                entry = _service_entry(
                    str(row.get("name", "files-extra")),
                    port,
                    enabled=True,
                    pid=pid,
                )
                entry["label"] = "Files"
                entry["path"] = "/files"
                entry["protocol"] = "http"
                entry["launchable"] = True
                entry["kind"] = "fileserver"
                services.append(entry)
            elif kind == "sandbox":
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


def _start_files_service_local(preferred_port: int | None, root_path: str | None = None, session_name: str | None = None) -> dict[str, Any]:
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

    env = os.environ.copy()
    if root_path:
        env["RR_FILES_ROOT"] = root_path

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
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        close_fds=True,
    )
    log_handle.close()

    row: dict[str, Any] = {
        "kind": "fileserver",
        "name": f"files-{port}",
        "port": port,
        "pid": proc.pid,
        "started_at": int(time.time()),
    }
    if root_path:
        row["root_path"] = root_path
    if session_name:
        row["session_name"] = session_name

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


def _start_remote_files_service(host: str, hub_port: int, preferred_port: int | None, root_path: str | None = None, session_name: str | None = None) -> dict[str, Any]:
    cleaned_host = _normalize_host(host)
    if _is_local_host(cleaned_host):
        return _start_files_service_local(preferred_port, root_path=root_path, session_name=session_name)

    url = f"http://{cleaned_host}:{hub_port}/services/files/start"
    body_dict: dict[str, Any] = {"port": preferred_port}
    if root_path:
        body_dict["root_path"] = root_path
    if session_name:
        body_dict["session_name"] = session_name
    body = json.dumps(body_dict).encode("utf-8")
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
