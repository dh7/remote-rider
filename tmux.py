import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from fastapi import HTTPException

from config import HERE, AGENT_LOCK
from host_utils import _is_local_host, _normalize_host, _pid_alive
from models import AgentStartRequest, AgentStartProxyRequest, AgentStopRequest, AgentStopProxyRequest
from storage import _load_agent_registry, _save_agent_registry, _tab_slug


# ── Tmux helpers ──────────────────────────────────────────────────────────────

def _tmux_session_exists(session: str) -> bool:
    if not shutil.which("tmux"):
        return False
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


# Keys the mobile keypad may send. Client token -> tmux send-keys argument.
# Restricting to this fixed set keeps clients from injecting arbitrary keystrokes.
SEND_KEY_ALLOWLIST: dict[str, str] = {
    "F1": "F1", "F2": "F2", "F3": "F3", "F4": "F4",
    "F5": "F5", "F6": "F6", "F7": "F7", "F8": "F8",
    "F9": "F9", "F10": "F10", "F11": "F11", "F12": "F12",
    "Escape": "Escape", "Tab": "Tab", "Enter": "Enter", "C-c": "C-c",
    "Up": "Up", "Down": "Down", "Left": "Left", "Right": "Right",
}


def _send_keys_tmux(session: str, tmux_key: str) -> bool:
    if not _tmux_session_exists(session):
        return False
    result = subprocess.run(
        ["tmux", "send-keys", "-t", session, tmux_key],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


# byobu/tmux navigation the mobile keypad can drive (the byobu F-key functions),
# run as real tmux commands targeted at the session so they act like a byobu keypress.
# Each value is (tmux subcommand args, target flag).
TMUX_ACTIONS: dict[str, tuple[list[str], str]] = {
    "win-prev": (["previous-window"], "-t"),
    "win-next": (["next-window"], "-t"),
    "win-new": (["new-window"], "-t"),
    "scroll": (["copy-mode"], "-t"),
    "detach": (["detach-client"], "-s"),
}


def _run_tmux_action(session: str, action: str) -> bool:
    spec = TMUX_ACTIONS.get(action)
    if spec is None or not _tmux_session_exists(session):
        return False
    cmd, flag = spec
    result = subprocess.run(
        ["tmux", *cmd, flag, session],
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


# ── Agent lifecycle ───────────────────────────────────────────────────────────

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


# ── Remote agent proxy ────────────────────────────────────────────────────────

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
