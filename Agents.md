# Remote Rider — Agent Notes & System Intent

## Core Goal

`remote-rider` is a control plane for multiple remote coding agents where:

- The control host provides one shared operator webpage.
- Each left-sidebar workspace is a control-side workspace.
- Each workspace points at one machine and one remote agent context.
- Remote machines own runtime truth and keep running even if control goes away.
- Agents and CLI tools can expose new tabs/services back into the control workspace.

## Where Control Runs

The control hub runs permanently on **netochka** (fixed Tailscale IP), not the operator's Mac.
This gives every machine and every script a stable, known address to call without dynamic discovery.

`start-control.sh` on netochka. `start-remote.sh` on every other target machine.
Stop control with `./start-control.sh --stop` or `./stop-control.sh`.

When shipping changes that affect both control UI/API and remote runtime, push once and pull on both machines:
1. Pull on netochka, restart control hub.
2. Pull on each remote, restart remote stack.

## Hub Topology: One Per Machine (not per workspace)

Each machine runs exactly one hub process (port 7000). Workspaces are a control-plane concept only — the machine does not need to know which workspace it belongs to.

**Rationale for this choice:**
- One process, one port per machine — simple to operate and update.
- A machine hub can serve multiple workspaces (e.g. two repos on the same machine) without extra processes.
- Kill/restart/update applies to the whole machine in one operation.
- CLI tools only need machine IP + control IP — no workspace-level hub to find or start.
- In a single-user Tailscale network, the machine is already the natural isolation boundary. Per-workspace hubs would add process overhead and port management complexity without meaningful isolation gain.

Workspaces pointing at the same machine all go through the same hub. The control plane owns workspace identity; the hub owns runtime.

## Architectural Data Domains

### 1. Machine Inventory
Owned by the control host.
- Source of truth: `machines.json`
- Purpose: known machines, hostnames/IPs, bootstrap defaults
- APIs: `/machines`, `/control/context`, related control-side inventory routes

### 2. Workspaces
Owned by the control host.
- Source of truth: `sessions.json`
- Purpose: sidebar workspaces, ordering, labels, chosen tabs, machine binding
- Browser `localStorage` is only a cache/fallback, not the durable source
- APIs: `/sessions` and control-side session mutation routes

### 3. Remote Runtime / Services
Owned by each remote machine.
- Source of truth: live runtime on that machine
- Purpose: tmux sessions, health, live services, spawned fileservers, agent processes
- APIs: `/services`, `/tmux/sessions`, `/services/files/start`, `/agents/runtime`, `/agents/start`, `/agents/stop`, and related runtime routes

### 4. Agent/Tool-Initiated Workspace Mutation
Owned by the control host, called by remote agents or CLI tools.
- Purpose: let any process ask control to add/update/remove tabs in a session
- Example: agent starts a preview server remotely, calls control to add a tab pointing at it
- This is a control-side session mutation, not a remote runtime call

## Repo-Level Config: `.rr` File

Each repo or working directory can contain a `.rr` file with two things only:
- The control plane URL (e.g. `http://100.x.x.x:7000`)
- The workspace/session name this directory belongs to

If a tool needs anything beyond that it calls the control API. The `.rr` file is intentionally minimal — it is a pointer, not a configuration store.

## Built-In Workspace Tabs

The machine hub serves the standard workspace tabs directly:

- `/workspaces/{workspace}/notes` — `Agents.md`, `TODO.md`, and `STATUS.md`.
- `/workspaces/{workspace}/skills` — project-scoped dh7skill management.
- `/workspaces/{workspace}/files` — project file browser/editor.

These tabs do not require a separate `rr-files`, `rr-skill`, or `rr-notes` process per workspace.
They are routed through the one hub running on the workspace machine and use the workspace
`project` path stored in `sessions.json`.

## CLI Tool Pattern (`rr-*`)

`rr-init` writes `.rr`, creates or updates the control-side workspace, stores the project path,
and adds Terminal plus built-in Notes, Skills, and Files tabs.

Other `rr-*` tools may still expose ad hoc tabs in the control plane:
- Read `.rr` to find control URL + workspace/session name.
- Start a local service on a free port only when the tab needs custom runtime.
- Call `POST /sessions/{session}/tabs` on the control plane to register a tab.
- Use a **descriptive label** (e.g. `Preview: feature/xyz`) — label is the tab identity within a session. Same label = update existing tab. Different label = new tab.
- On exit or on receiving a kill signal, call `DELETE /sessions/{session}/tabs` to remove the tab.

Examples:
- `rr-init --control http://100.119.43.10:7000` — creates a workspace for the current project with built-in tabs.
- Agents can do the same: spawn any small HTTP server, register it as a tab, expose it to the operator

## Project Notes & Status

The built-in Notes view exposes the project handoff surface in a Remote Rider tab:

- `Agents.md` / `AGENTS.md` / `CLAUDE.md` — durable project intent and agent rules.
- `TODO.md` — shared project work queue. The Notes UI can create it when missing.
- `STATUS.md` — current/last session handoff. The Notes UI can create it when missing.

Agents should update `STATUS.md` from time to time during meaningful work, especially before stopping, after commits, after launches, or when the current state would otherwise be hard to reconstruct. Keep `TODO.md` for durable work items and `STATUS.md` for current focus, recent changes, current state, next steps, and open questions.

## Skill Management Boundary

`remote-rider` does not own skill installation logic. The split is:

- `dh7skills` owns the canonical `dh7skill` CLI, skill discovery, project manifests, symlinks, env vars, and secrets.
- `remote-rider` owns the built-in Skills tab: hub routes that shell out to `dh7skill` for the workspace project.

This keeps `dh7skills` usable without Remote Rider while letting every Remote Rider workspace get a UI for adding/removing project skills.

Project skill state lives in `.dh7skills.json`, not `.rr`. The `.rr` file stays a Remote Rider pointer only.

Access control is skill-scoped:
- Skills declare required env vars/secrets in `SKILL.md` frontmatter.
- Adding a skill installs only that skill's declared access into the project.
- Removing a skill recomputes the managed `.env` block from the remaining installed skills.
- Secrets/env remain invisible to a project unless a selected skill requires them.

## Tab Lifecycle & the X Button

Every tab except Terminal has a close button (X).

Clicking X:
1. Sends a kill request to the service's hub: `DELETE /services/{port}` (or equivalent) on the machine that owns the service.
2. The hub kills the process using its internal service registry (it holds the pids; the control plane does not need to).
3. Control removes the tab from the session.

Tabs track `machine_host` and `port` — enough to route the kill through the correct hub. No pid leakage to the control layer.

## Tab Persistence Rules

- **Terminal** — persistent. Stored in sessions.json. No X button. Its lifecycle is the tmux session, not a process.
- **Everything else** (Monitor, Logs, Files, agent-spawned tabs) — ephemeral. The X button is always shown. Source of truth is the hub's service registry. On control restart, ephemeral tabs that are still running can be recovered by rescanning machine hubs.
- `sessions.json` stores Terminal tabs and display overrides (label, tab order). Hub service registry stores what is actually running.
- Tabs carry an `ephemeral: true` flag when registered by remote tools/agents. This flag is used for future recovery logic.

## Agent Lifecycle

Remote nodes manage first-class agent processes.
- Preferred backend: `tmux` detached session
- Fallback backend: local background process
- Registry: `agent_registry.json`

Agents keep running independently of the control UI.

## Remote Updates

To avoid SSHing into every remote machine, the hub can schedule a self-update:
1. `git pull --ff-only origin <branch>`
2. `./start-remote.sh`

```bash
curl -X POST http://CONTROL_HOST:7000/admin/update-remote/proxy \
  -H 'content-type: application/json' \
  -d '{"host":"100.119.43.10","branch":"main"}'

curl -X POST http://CONTROL_HOST:7000/admin/update-all-remotes \
  -H 'content-type: application/json' \
  -d '{"branch":"main"}'
```

Update logs: `logs/update-remote.log`

## Git Rules

- Never clone GitHub repos over HTTPS on homelab machines.
- Never leave `origin` as `https://github.com/...` on homelab machines.
- Always use SSH remotes: `git@github.com:owner/repo.git`.

## Authentication

Intentionally deferred. The current environment is a single-user Tailscale network.
The API shape (session-scoped tab mutations, hub-proxied kills) is designed to make auth easy to add later.

## Clipboard in Terminal Tabs

ttyd's xterm.js auto-copies on selection via `document.execCommand('copy')` and flashes a ✂ overlay. Two gotchas affect this:

1. **Cross-origin iframe blocks the copy.** The dashboard hub and each ttyd run on different origins (different ports/hosts), so the terminal tab is a cross-origin iframe. Chromium-based browsers (incl. Arc) block `execCommand('copy')` there unless the parent delegates clipboard via Permissions-Policy AND the iframe carries `allow="clipboard-read; clipboard-write"`. Both are now in place (`main.py` middleware + `app.js` iframe creation). Each tab also has a ↗ popout button to open it as a top-level page if the in-iframe path ever breaks again.

2. **Claude Code (and any TUI in mouse-capture mode) preempts xterm.js selection.** When Claude Code is in fullscreen/alt-screen mode it requests mouse events, so plain mouse drags go to Claude, not to xterm.js's native selection — no selection means no copy and no ✂. **Workaround: hold Shift while dragging.** Shift is the X11/xterm convention for "ignore the app's mouse capture and do the terminal's native selection." Same fix applies to vim with `set mouse=a`, htop, btop, etc.

If a user reports "copy stopped working" in a Terminal tab, check whether a mouse-capturing TUI is running in the active pane before suspecting browser/server config — that's the most common cause and the Shift+drag workaround resolves it immediately.

## Post-Reboot Recovery on Remote Machines

The stack is under **systemd** as a per-user unit, so after a reboot it restarts automatically — no manual step. The unit runs `boot-stack.sh`, which waits for the Tailscale IP and then starts the right mode for the machine: **control + remote** on the control host (netochka), **remote-only** everywhere else.

Install / manage (see `deploy/remote-rider.service`, `install-service.sh`, `boot-stack.sh`):

```bash
ssh <machine>
cd ~/code/remote-rider
git pull --ff-only
./install-service.sh                  # idempotent: installs unit, enables linger + byobu mouse-off
systemctl --user restart remote-rider # bring the stack up now / restart it
systemctl --user status  remote-rider # ttyd + hub + monitor + logs + fileserver, one cgroup
journalctl --user -u remote-rider -e  # boot/start logs
```

Requires user **linger** (`loginctl enable-linger <user>` — the installer does this when permitted) so the unit starts at boot with no login session. The unit is `Type=oneshot` + `RemainAfterExit=yes`; `systemctl --user stop|restart` tears the whole cgroup down cleanly. Deployed on: **netochka** (control), **gx10**, **image-store**, **pi**.

Manual fallback (only if systemd is unavailable), detached from the launching SSH session:

```bash
cd ~/code/remote-rider
setsid nohup bash start-remote.sh > logs/start-remote.log 2>&1 < /dev/null &
disown
```

`setsid nohup ... < /dev/null &` + `disown` is the exact recipe — a plain `nohup &` still dies when the SSH ControlMaster tears down. Ubuntu's OS-default `ttyd.service` (binds `127.0.0.1:7681 -O login`) is *not* remote-rider's — ignore it. Verify:

```bash
tail -8 logs/start-remote.log
for f in logs/pids/*.pid; do
  pid=$(cat $f); printf "%-15s pid=%s %s\n" "$(basename $f .pid)" "$pid" \
    "$(kill -0 $pid 2>/dev/null && echo ALIVE || echo dead)"
done
```

The gx10 model manager (`~/code/vlmtest/model_manager.py` on port 8999) also dies with the SSH session that launched it; until it has its own unit, use the same detached recipe with `python3` (system, not the missing `~/venv/bin/python`).

## Terminal Tabs Should Load `byobu-tmux`, Not Bare `tmux`

`terminal-entry.sh` invokes `byobu-tmux new-session -A -s "$SESSION"` (falling back to bare `tmux` only where byobu is not installed) so the tmux server boots with byobu's status bar / keybindings loaded from the start. **The byobu profile is applied only when tmux starts the server**, so to convert an already-running plain-tmux server you must `tmux kill-server` (sessions are recreated on demand by ttyd) or reboot. Do *not* `tmux source-file /usr/share/byobu/profiles/tmux` into a live plain server — it works partially but throws "invalid style" warnings because byobu's color/statusrc environment is only fully wired when byobu launches the server itself.

Prerequisite on any machine using this: `~/.byobu/keybindings.tmux` must contain `set -g mouse off` — **`install-service.sh` adds this automatically**. Byobu enables tmux mouse mode by default, which then intercepts browser text selection in the ttyd iframe — you lose the ✂ copy path. The `mouse off` override plus the Shift+drag rule (see previous section) covers all cases.

If someone previously reverted this and switched back to plain `tmux` because of a copy/paste regression: it was almost certainly the mouse-mode issue above, not `byobu-tmux` itself. Re-apply the `byobu-tmux` swap along with the `~/.byobu/keybindings.tmux` override.

## NFS Mount Recovery (gx10 specifically)

gx10 mounts TrueNAS at `/mnt/truenas-shared` via fstab (`_netdev`). Post-reboot this occasionally fails to auto-mount — the model manager then shows `error` because model paths like `/mnt/truenas-shared/models/*` don't exist. Fix requires sudo and can't be done from a Claude session:

```bash
sudo mount /mnt/truenas-shared
curl -X POST http://localhost:8999/sync
```

Then start whatever config was intended: `curl -X POST http://localhost:8999/configs/<name>/start`.

Long-term: convert the fstab entry to `x-systemd.automount` or add a small `mount-truenas.service`.
