# Web Terminal Hub

Browser-based terminal dashboard with tabs for:

- Terminal (`ttyd`)
- Process monitor (`monitor.py`)
- Log viewer (`logs.py`)
- File browser/editor (`fileserver.py`)

## Key Architecture

- **Client-side session config**: left sidebar profiles/order/labels are saved in browser `localStorage`.
- **Server-side runtime**: each machine runs its own services and exposes ports.
- `servers.json` is now only a **bootstrap/default profile list** for first page load.
- `session_templates.json` defines control-side default panel templates for new sessions.

This avoids cross-machine profile drift and lets each client keep its own view layout.

## Ports

- Hub/API: `7000`
- Terminal: `7681`
- Monitor: `8001`
- Logs: `8002`
- Files: `8080`

## Start Modes

### 1) Remote node mode (run on each target server)

Starts terminal + monitor + logs + files + API/hub endpoint on that server.

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

### 3) Legacy all-in-one

`start.sh` remains available and starts a full local stack.

## Add Session Flow

Click `+` in sidebar:

1. Select an existing profile or `+ New server profile`
2. Choose server host/IP (includes homelab Tailscale presets)
3. Choose panel source:
   - existing server profile (clone panel layout)
   - panel template (from control-side `session_templates.json`)
4. If using template source, choose a panel template
5. Choose terminal session source:
   - existing tmux session (queried from selected host)
   - new tmux session name
6. Create (the UI will also try to pull live panel ports from the target host)

## Session Setup Flow

Each session row includes `S` (setup). Use it to maintain panel mappings after creation:

- Add panel
- Remove panel
- Edit panel label/port/path/protocol
- Sync panel list/ports from remote (`/servers/proxy`)
- Save updated panel config to browser localStorage

This is the fastest way to fix broken Monitor/Logs/Files/Terminal port mappings after restarts or port shifts.

The tabs bar also includes `+` to open setup quickly and add a new panel/tab to the active session.

## Template Configuration

Default templates are loaded from:

- `session_templates.json`

Each template entry uses:

- `id` (unique)
- `label` (UI label)
- `panels` (list of `{ label, port, path?, protocol? }`)

## API Notes

- `GET /servers` -> bootstrap profiles
- `GET /session-templates` -> panel templates for add-session modal
- `GET /tmux/sessions` -> local host tmux sessions
- `GET /tmux/sessions/proxy?host=<ip>&port=7000` -> tmux sessions from a selected server
- `GET /servers/proxy?host=<ip>&port=7000` -> panel config from selected server
- `POST /tmux/kill` -> kill a tmux session on local host or proxied host

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install fastapi uvicorn aiofiles psutil
```

Install `ttyd` and `tmux` on each remote node for terminal persistence.
