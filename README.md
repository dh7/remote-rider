# Web Terminal Hub

Browser-based terminal dashboard with tabs for:

- Terminal (`ttyd`)
- Process monitor (`monitor.py`)
- Log viewer (`logs.py`)
- File browser/editor (`fileserver.py`)

## Key Architecture

- **Client-side workspace config**: left sidebar workspaces/order/labels are saved in browser `localStorage`.
- **Control-side workspace store**: workspaces are persisted to `sessions.json` on the control host, with browser localStorage kept as a fallback cache.
- **Server-side runtime**: each machine runs its own services and exposes ports.
- `machines.json` is the machine inventory/bootstrap list for first page load.
- `servers.json` remains as a legacy fallback during migration.
- `session_templates.json` defines control-side default panel templates for new workspaces.

This avoids cross-machine profile drift and lets each client keep its own view layout.

## Ports

- Hub/API: `7000`
- Terminal: `7681`
- Monitor: `8001`
- Logs: `8002`
- Files: `8080`

## CLI Install

Install the `rr-*` helper commands into `~/.local/bin` so they work from any repo:

```bash
~/code/remote-rider/install.sh
```

This links `rr-init` plus the legacy helper commands `rr-files`, `rr-skill`, and `rr-notes`.
Make sure `~/.local/bin` is in `PATH`.

## Start Modes

### 1) Remote node mode (run on each target server)

Starts terminal + monitor + logs + API/hub endpoint on that server. The hub also serves built-in
workspace Files, Skills, and Notes views.

```bash
./start-remote.sh
```

Use this on `netochka`, `gx10`, etc.

### 2) Control mode (optional centralized UI host)

Starts only the UI/API process on this machine.

```bash
./start-control.sh
```

Use this on the machine you open in browser (can be localhost).
Stop it with:

```bash
./start-control.sh --stop
```

or:

```bash
./stop-control.sh
```

### 3) Legacy all-in-one

`start.sh` remains available and starts a full local stack.

## Add Workspace Flow

Click `+` in sidebar:

1. Set the workspace label, machine, and workspace type in `Workspace config`.
2. Set the project path for the workspace. Built-in Files, Skills, and Notes use this path.
3. Choose tabs from service checkboxes. `Terminal` is mandatory; `Notes`, `Skills`, and `Files` are selected by default, and other live services appear unchecked.
4. Choose terminal tmux session source:
   - existing tmux session (queried from selected host)
   - new tmux session name
5. Create the workspace.

## Workspace Setup Flow

Each workspace row includes setup. Use it to maintain tab mappings after creation:

- Add custom tab
- Remove tab
- Edit tab label/port/path/protocol
- Sync tab list/ports from remote (`/machines/proxy`)
- Discover remote services and adopt them as tabs
- Add built-in or custom service tabs
- Save updated workspace config to browser localStorage

This is the fastest way to fix broken Monitor/Logs/Files/Terminal mappings after restarts or port shifts.

The tabs bar also includes `+` to open setup quickly and add a new panel/tab to the active workspace.
Each tab shows a status dot:

- green = reachable
- red = unreachable
- gray = checking

## Template Configuration

Default templates are loaded from:

- `session_templates.json`

Each template entry uses:

- `id` (unique)
- `label` (UI label)
- `panels` (list of `{ label, port, path?, protocol? }`)

## API Notes

- `GET /machines` -> bootstrap machine inventory
- `GET /servers` -> legacy-compatible bootstrap alias
- `GET /sessions` -> control-side saved workspaces
- `PUT /sessions` -> replace control-side saved workspaces
- `GET /sessions/<name>` -> one saved workspace
- `POST /sessions/<name>/tabs` -> add/update a tab in a saved workspace
- `DELETE /sessions/<name>/tabs` -> remove a tab from a saved workspace
- `GET /workspaces/<name>/notes` -> built-in Notes UI for the workspace project
- `GET /workspaces/<name>/skills` -> built-in Skills UI for the workspace project
- `GET /workspaces/<name>/files` -> built-in Files browser/editor for the workspace project
- `GET /control/context` -> control-side view of known machines, sessions, and templates
- `GET /session-templates` -> panel templates for add-session modal
- `GET /panel/status?host=<ip>&port=<n>` -> single panel port health probe
- `GET /services` -> services running on this host (including extra fileserver instances)
- `GET /services/proxy?host=<ip>&port=7000` -> service snapshot from selected server
- `GET /tmux/sessions` -> local host tmux sessions
- `GET /tmux/sessions/proxy?host=<ip>&port=7000` -> tmux sessions from a selected server
- `GET /agents/runtime` -> agents running on this host
- `GET /agents/runtime/proxy?host=<ip>&port=7000` -> agents running on a selected server
- `POST /agents/start` -> start an agent on this host
- `POST /agents/start/proxy` -> start an agent on a selected server
- `POST /agents/stop` -> stop an agent on this host
- `POST /agents/stop/proxy` -> stop an agent on a selected server
- `POST /admin/update-remote` -> schedule `git pull` + remote stack restart on this host
- `POST /admin/update-remote/proxy` -> schedule `git pull` + remote stack restart on a selected server
- `GET /machines/proxy?host=<ip>&port=7000` -> machine panel config from selected server
- `GET /servers/proxy?host=<ip>&port=7000` -> legacy-compatible alias
- `POST /services/files/start` -> legacy additional fileserver starter
- `POST /services/files/start/proxy` -> legacy proxied fileserver starter
- `POST /tmux/kill` -> kill a tmux session on local host or proxied host

## Run Modes

- `start-control.sh` runs the hub with `RUN_MODE=control`
- `start-remote.sh` runs the hub with `RUN_MODE=remote`
- `start.sh` defaults to `RUN_MODE=all`

Control mode is where machine inventory and saved sessions belong.
Remote mode is where local runtime/service APIs belong.

## Agent-Driven Tabs

Remote agents can call the control host to add or update tabs in a specific session.

Typical flow:

1. Agent starts or discovers a service on its remote machine.
2. Agent calls `POST /sessions/<session>/tabs` on the control host.
3. Control persists the tab in `sessions.json`.
4. The operator UI polls control-side session state and picks up the new tab.

Authentication is not implemented yet.

## Agent Lifecycle

Remote nodes can now manage first-class agent processes.

- Preferred backend: `tmux` detached session
- Fallback backend: local background process
- Registry: `agent_registry.json`

This is meant to keep remote coding agents alive independently of the control UI.

## Remote Updates

To avoid SSHing into every remote machine after each change, the remote hub can schedule a detached self-update job:

1. `git pull --ff-only origin <branch>`
2. `./start-remote.sh`

Use:

```bash
curl -X POST http://CONTROL_OR_REMOTE_HOST:7000/admin/update-remote/proxy \
  -H 'content-type: application/json' \
  -d '{"host":"100.119.43.10","branch":"main"}'
```

Remote update logs are written to:

- `logs/update-remote.log`

Current limitation:

- This updates remote nodes only.
- The control host still needs its own pull/restart workflow.

## Why Files Panel Breaks Sometimes

Most breakage comes from port drift on remote nodes. If `8080` is occupied, `start-remote.sh` can shift Files to another free port.
When a saved session still points to the old port, iframe loads fail. Fix it by opening session setup (`S`) and pressing `Sync From Remote`.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install fastapi uvicorn aiofiles psutil
```

Install `ttyd` and `tmux` on each remote node for terminal persistence.
