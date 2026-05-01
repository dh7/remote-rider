import json
import os
import shlex
import socket
import subprocess
from pathlib import Path
from typing import Any
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from config import HERE
from host_utils import _is_local_host, _normalize_host
from models import RemoteUpdateProxyRequest


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


def _fetch_remote_update_diagnostics(host: str, port: int, branch: str) -> dict[str, Any]:
    cleaned_host = _normalize_host(host)
    if _is_local_host(cleaned_host):
        return _local_update_diagnostics(branch)

    # Bug fix: only send `branch` — the remote endpoint accepts RemoteUpdateRequest which has only that field.
    body = json.dumps({"branch": branch}).encode("utf-8")
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
