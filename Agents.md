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

## CLI Tool Pattern (`rr-*`)

`remote-rider` ships a collection of small CLI tools that follow a "less is more" philosophy:
each tool does one thing, but unlike a plain shell script it can surface a UI tab in the control plane.

Design rules:
- Read `.rr` to find control URL + workspace/session name.
- Start a local service on a free port.
- Call `POST /sessions/{session}/tabs` on the control plane to register a tab.
- Use a **descriptive label** (e.g. `Files: /data/results`, `Preview: feature/xyz`) — label is the tab identity within a session. Same label = update existing tab. Different label = new tab.
- On exit or on receiving a kill signal, call `DELETE /sessions/{session}/tabs` to remove the tab.

Examples:
- `rr-files /path` — starts a file browser for that path, adds a Files tab
- `rr-files /path/a` + `rr-files /path/b` — two separate file tabs in the same workspace
- `rr-skill` — starts a Skills tab that manages project-scoped dh7skills access
- `rr-notes` — starts a Notes tab for `Agents.md`, `TODO.md`, and `STATUS.md`
- Agents can do the same: spawn any small HTTP server, register it as a tab, expose it to the operator

## Project Notes & Status

`rr-notes` exposes the project handoff surface in a Remote Rider tab:

- `Agents.md` / `AGENTS.md` / `CLAUDE.md` — durable project intent and agent rules.
- `TODO.md` — shared project work queue. The Notes UI can create it when missing.
- `STATUS.md` — current/last session handoff. The Notes UI can create it when missing.

Agents should update `STATUS.md` from time to time during meaningful work, especially before stopping, after commits, after launches, or when the current state would otherwise be hard to reconstruct. Keep `TODO.md` for durable work items and `STATUS.md` for current focus, recent changes, current state, next steps, and open questions.

## Skill Management Boundary

`remote-rider` does not own skill installation logic. The split is:

- `dh7skills` owns the canonical `dh7skill` CLI, skill discovery, project manifests, symlinks, env vars, and secrets.
- `remote-rider` owns the `rr-skill` adapter: an HTML/API server that reads `.rr`, registers a Skills tab, and shells out to `dh7skill`.

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
