import json
import shutil
import socket
from typing import Any
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from config import (
    HERE,
    SERVERS_LOCK,
    SESSIONS_LOCK,
    _normalize_path,
    _normalize_protocol,
    _run_mode,
    _control_api_enabled,
    _runtime_api_enabled,
    _require_control_api,
    _require_runtime_api,
)
from host_utils import _is_local_host, _is_local_server, _normalize_host, _probe_panel_port
from models import (
    AgentStartProxyRequest,
    AgentStartRequest,
    AgentStopProxyRequest,
    AgentStopRequest,
    RemoteGitCheckProxyRequest,
    RemoteRequest,
    RemoteUpdateProxyRequest,
    RemoteUpdateRequest,
    ReorderRequest,
    SandboxBranchProxyRequest,
    SandboxBranchRequest,
    SandboxCloneProxyRequest,
    SandboxCloneRequest,
    SandboxCreateProxyRequest,
    SandboxCreateRequest,
    SandboxStopProxyRequest,
    SandboxStopRequest,
    SessionsPutRequest,
    SessionTabDeleteRequest,
    SessionTabUpsertRequest,
    StartFilesServiceProxyRequest,
    StartFilesServiceRequest,
    TabRequest,
    TmuxKillRequest,
    UpdateAllRemotesRequest,
)
from services import (
    _fetch_remote_services,
    _local_services_snapshot,
    _start_files_service_local,
    _start_remote_files_service,
)
from storage import (
    _apply_port_overrides,
    _clone_panels,
    _extract_terminal_session,
    _load_machine_inventory,
    _load_normalized_control_sessions,
    _load_templates,
    _normalize_control_session,
    _normalize_session_tab,
    _save_machine_inventory,
    _save_normalized_control_sessions,
    _set_terminal_session,
    _tab_slug,
)
from tmux import (
    _fetch_remote_agents,
    _fetch_remote_tmux_sessions,
    _kill_tmux_session,
    _list_local_agents,
    _list_tmux_sessions,
    _start_agent_local,
    _start_remote_agent,
    _stop_agent_local,
    _stop_remote_agent,
    _tmux_session_exists,
)
from sandbox import (
    branch_sandbox,
    branch_sandbox_proxy,
    clone_sandbox,
    clone_sandbox_proxy,
    create_sandbox,
    create_sandbox_proxy,
    list_sandboxes,
    list_sandboxes_proxy,
    stop_sandbox,
    stop_sandbox_proxy,
)
from updates import (
    _fetch_remote_update_diagnostics,
    _local_update_diagnostics,
    _machine_host_from_inventory,
    _schedule_remote_update_local,
    _schedule_remote_update_proxy,
)

app = FastAPI()
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")


# ── Remote machine proxy ──────────────────────────────────────────────────────

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


# ── Agents discovery text ─────────────────────────────────────────────────────

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
curl -X POST {base}/tab \\
  -H 'content-type: application/json' \\
  -d '{{"server":"netochka","label":"Agent","port":9001,"path":"/","ip":"100.119.43.10"}}'

curl -X POST {base}/remote \\
  -H 'content-type: application/json' \\
  -d '{{"action":"add","name":"netochka-job2","display":"netochka","ip":"100.119.43.10","base":"netochka","position":"top","terminal_session":"webterm"}}'

curl -X POST {base}/sessions/netochka-job1/tabs \\
  -H 'content-type: application/json' \\
  -d '{{"label":"Preview","service":"preview","port":8123,"path":"/","protocol":"http","activate":true}}'

curl -X POST {base}/agents/start/proxy \\
  -H 'content-type: application/json' \\
  -d '{{"host":"100.119.43.10","name":"agent-review","command":"codex --dangerously-bypass-approvals-and-sandbox","session_name":"demo-session"}}'

curl -X POST {base}/admin/update-remote/proxy \\
  -H 'content-type: application/json' \\
  -d '{{"host":"100.119.43.10","branch":"main"}}'

curl -X POST {base}/admin/update-all-remotes \\
  -H 'content-type: application/json' \\
  -d '{{"branch":"main"}}'

curl -X POST {base}/admin/update-diagnostics/proxy \\
  -H 'content-type: application/json' \\
  -d '{{"host":"100.119.43.10","branch":"main"}}'

Sandbox (Docker-based isolated Claude sessions):
- List local containers: GET {base}/sandbox/list
- List containers on remote hub: GET {base}/sandbox/list/proxy?host=<ip>&hub_port=7000
- Create sandbox: POST {base}/sandbox/create
- Create sandbox on remote hub: POST {base}/sandbox/create/proxy
- Stop & remove sandbox: POST {base}/sandbox/stop
- Stop & remove on remote hub: POST {base}/sandbox/stop/proxy
- Clone container to new branch: POST {base}/sandbox/clone
- Clone container on remote hub: POST {base}/sandbox/clone/proxy
- Spawn sibling from same source: POST {base}/sandbox/branch (callable from inside a container via $HUB_HOST/$HUB_PORT env vars)
- Spawn sibling on remote hub: POST {base}/sandbox/branch/proxy

Sandbox env vars injected into each container:
- HUB_HOST=host.docker.internal  (resolves to the host running remote-rider)
- HUB_PORT=<hub port>
- CONTAINER_NAME=<container name>
- BRANCH=<branch name>
- REPO_URL=<git url>  (if cloned from GitHub)

curl -X POST {base}/sandbox/create \\
  -H 'content-type: application/json' \\
  -d '{{"branch":"feature/xyz","repo_url":"https://github.com/user/repo"}}'

curl -X POST {base}/sandbox/create \\
  -H 'content-type: application/json' \\
  -d '{{"branch":"main","local_path":"/home/user/myproject"}}'

curl -X POST $HUB_HOST:$HUB_PORT/sandbox/branch \\
  -H 'content-type: application/json' \\
  -d '{{"container_id":"'"$CONTAINER_NAME"'","new_branch":"feature/review"}}'

curl -X POST {base}/sandbox/stop \\
  -H 'content-type: application/json' \\
  -d '{{"container_id":"abc123def456"}}'
"""


# ── Routes ────────────────────────────────────────────────────────────────────

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
            "storage": str(HERE / "sessions.json"),
        }


@app.put("/sessions")
def replace_sessions(payload: SessionsPutRequest) -> dict[str, Any]:
    _require_control_api()
    normalized = [s for s in (_normalize_control_session(row) for row in payload.sessions) if s]
    with SESSIONS_LOCK:
        _save_normalized_control_sessions(normalized)
    return {
        "status": "ok",
        "count": len(normalized),
        "storage": str(HERE / "sessions.json"),
    }


@app.get("/sessions/{session_name}")
def session_by_name(session_name: str) -> dict[str, Any]:
    _require_control_api()
    with SESSIONS_LOCK:
        all_sessions = _load_normalized_control_sessions()
    session = next((row for row in all_sessions if row.get("name") == session_name), None)
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
def admin_update_all_remotes(payload: UpdateAllRemotesRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    _require_control_api()
    inventory = _load_machine_inventory()
    selected = set(payload.machines or [])
    results: list[dict[str, Any]] = []
    self_update_branch: str | None = None

    # Merge session machines into the update target list so any machine with
    # a session appears here without needing a separate machines.json entry.
    seen_hosts: set[str] = set()
    merged: list[dict[str, Any]] = []
    for m in inventory:
        h = _machine_host_from_inventory(m)
        if h:
            seen_hosts.add(h)
        merged.append(m)
    with SESSIONS_LOCK:
        sessions = _load_normalized_control_sessions()
    for s in sessions:
        machine = s.get("machine", {})
        host = str(machine.get("host", "")).strip()
        name = str(machine.get("name", "") or s.get("name", "")).strip()
        if not host or not name or host in seen_hosts or host == "127.0.0.1":
            continue
        seen_hosts.add(host)
        merged.append({"name": name, "ip": host})

    for machine in merged:
        name = str(machine.get("name", "")).strip()
        host = _machine_host_from_inventory(machine)
        if not name or not host:
            continue
        if selected and name not in selected:
            continue
        cleaned = _normalize_host(host)
        if _is_local_host(cleaned):
            # Defer local machine update via background task so the response is sent first
            self_update_branch = payload.branch
            result = {"status": "scheduled", "branch": payload.branch, "machine": name, "host": host}
        else:
            result = _schedule_remote_update_proxy(
                RemoteUpdateProxyRequest(host=host, hub_port=7000, branch=payload.branch)
            )
            result.setdefault("machine", name)
            result.setdefault("host", host)
        results.append(result)

    if not selected or "controller" in selected:
        self_update_branch = payload.branch
        results.append({"status": "scheduled", "machine": "controller", "host": "127.0.0.1", "branch": payload.branch})

    if self_update_branch is not None:
        background_tasks.add_task(_schedule_remote_update_local, self_update_branch, 3)

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
        all_sessions = _load_normalized_control_sessions()
        session = next((row for row in all_sessions if row.get("name") == session_name), None)
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

        _save_normalized_control_sessions(all_sessions)

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
        all_sessions = _load_normalized_control_sessions()
        session = next((row for row in all_sessions if row.get("name") == session_name), None)
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

        _save_normalized_control_sessions(all_sessions)

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


@app.post("/sandbox/create")
def sandbox_create(payload: SandboxCreateRequest) -> dict[str, Any]:
    _require_runtime_api()
    return create_sandbox(branch=payload.branch, repo_url=payload.repo_url, local_path=payload.local_path, auth_path=payload.auth_path, image=payload.image)


@app.post("/sandbox/create/proxy")
def sandbox_create_proxy_route(payload: SandboxCreateProxyRequest) -> dict[str, Any]:
    _require_control_api()
    return create_sandbox_proxy(payload.host, payload.hub_port, branch=payload.branch, repo_url=payload.repo_url, local_path=payload.local_path, auth_path=payload.auth_path, image=payload.image)


@app.get("/sandbox/list")
def sandbox_list_local() -> dict[str, Any]:
    _require_runtime_api()
    return {"sandboxes": list_sandboxes(), "host": socket.gethostname()}


@app.get("/sandbox/list/proxy")
def sandbox_list_proxy_route(host: str = Query(...), hub_port: int = Query(7000, ge=1, le=65535)) -> dict[str, Any]:
    _require_control_api()
    return list_sandboxes_proxy(host, hub_port)


@app.post("/sandbox/stop")
def sandbox_stop_local(payload: SandboxStopRequest) -> dict[str, Any]:
    _require_runtime_api()
    return stop_sandbox(payload.container_id)


@app.post("/sandbox/stop/proxy")
def sandbox_stop_proxy_route(payload: SandboxStopProxyRequest) -> dict[str, Any]:
    _require_control_api()
    return stop_sandbox_proxy(payload.host, payload.hub_port, payload.container_id)


@app.post("/sandbox/clone")
def sandbox_clone_local(payload: SandboxCloneRequest) -> dict[str, Any]:
    _require_runtime_api()
    return clone_sandbox(payload.container_id, payload.new_branch)


@app.post("/sandbox/clone/proxy")
def sandbox_clone_proxy_route(payload: SandboxCloneProxyRequest) -> dict[str, Any]:
    _require_control_api()
    return clone_sandbox_proxy(payload.host, payload.hub_port, payload.container_id, payload.new_branch)


@app.post("/sandbox/branch")
def sandbox_branch_local(payload: SandboxBranchRequest) -> dict[str, Any]:
    _require_runtime_api()
    return branch_sandbox(payload.container_id, payload.new_branch)


@app.post("/sandbox/branch/proxy")
def sandbox_branch_proxy_route(payload: SandboxBranchProxyRequest) -> dict[str, Any]:
    _require_control_api()
    return branch_sandbox_proxy(payload.host, payload.hub_port, payload.container_id, payload.new_branch)


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
