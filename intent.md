# Remote Rider Intent

## Core Goal

Build `remote-rider` into a control plane for multiple remote coding agents where:

- the control host provides one shared operator webpage
- each left-sidebar session is a control-side workspace
- each workspace points at one machine and one remote agent context
- remote machines own runtime truth and keep running even if control goes away
- agents can expose new tabs/services back into the control workspace

## Architectural Split

There are three primary data domains and one extension point:

### 1. Machine Inventory

Owned by the control host.

- source of truth: `machines.json`
- purpose: known machines, hostnames/IPs, bootstrap defaults
- APIs: `/machines`, `/control/context`, and related control-side inventory routes

### 2. Sessions / Workspaces

Owned by the control host.

- source of truth: `sessions.json`
- purpose: sidebar sessions, ordering, labels, chosen tabs, machine binding
- browser `localStorage` is only a cache/fallback, not the intended durable source
- APIs: `/sessions` and control-side session mutation routes

### 3. Remote Runtime / Services

Owned by each remote machine.

- source of truth: live runtime on that machine
- purpose: tmux sessions, health, live services, spawned fileservers, agent processes
- APIs: `/services`, `/tmux/sessions`, `/services/files/start`, `/agents/runtime`, `/agents/start`, `/agents/stop`, and related runtime routes

### 4. Agent-Initiated Workspace Mutation

Owned by the control host, called by remote agents.

- purpose: let an agent ask control to add/update/remove tabs in a session
- example: agent starts a preview server remotely, then asks control to show it as a new tab
- this is a control-side session mutation, not a remote runtime API

## Run Modes

- `start-control.sh` runs `main.py` with `RUN_MODE=control`
- `start-control.sh --stop` or `stop-control.sh` stops the control hub only
- `start-remote.sh` runs `main.py` with `RUN_MODE=remote`
- `start.sh` keeps `RUN_MODE=all` for legacy/local combined use

Control mode should own machine inventory and saved sessions.
Remote mode should own local runtime and service control.

## Current Product Behavior

### Session UX

- Sidebar session management: rename/delete/delete+kill/reorder
- Add-session flow supports:
  - existing session or new machine/session
  - tabs cloned from an existing session or from a template
  - existing/new tmux session selection
- Setup modal per session supports:
  - add/remove/edit tabs
  - sync tabs from remote machine state
  - discover remote services
  - launch a remote files service and wire it into the session

### Runtime Discovery

- Tab-level health dots
- Remote service discovery from the setup modal
- Live service resolution preferred over saved fallback endpoints

### Persistence

- `machines.json` for machine inventory
- `sessions.json` for control-side workspace persistence
- `service_registry.json` for extra local runtime services spawned on a remote node

## Required Long-Term Workflow

1. Start control on the MacBook Pro.
2. Start remote runtime on each target machine.
3. Open one webpage and manage multiple sessions/workspaces.
4. Let remote agents keep coding even if the control host disappears.
5. Allow agents to surface their own tabs back to the operator through the control API.
6. Keep the control/runtime boundary explicit so port drift and restarts do not corrupt workspace intent.

## Immediate Next Capability

Implement control-side session mutation APIs so a remote agent can:

- target a specific session
- add or update a tab in that session
- point that tab at a live remote service endpoint
- have the operator UI pick up that change without manual editing

Authentication is intentionally deferred for now because the current environment is a single-user Tailscale network, but the API shape should make auth easy to add later.
