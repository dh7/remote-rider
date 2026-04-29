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
3. Choose terminal session source:
   - existing tmux session (queried from selected host)
   - new tmux session name
4. Create

## API Notes

- `GET /servers` -> bootstrap profiles
- `GET /tmux/sessions` -> local host tmux sessions
- `GET /tmux/sessions/proxy?host=<ip>&port=7000` -> tmux sessions from a selected server
- `POST /tmux/kill` -> kill a tmux session on local host or proxied host

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install fastapi uvicorn aiofiles psutil
```

Install `ttyd` and `tmux` on each remote node for terminal persistence.
