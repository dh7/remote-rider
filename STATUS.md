# Status

## Current Focus

Understand and simplify the Add Workspace flow.

## Last Updated

2026-05-07 Europe/Paris

## What Changed

- Added `rr-notes`, a Remote Rider-compatible notes/status server.
- Added Notes guidance to `Agents.md`.
- Updated the `dh7skills` `remote-rider` skill with `rr-notes` and `STATUS.md` guidance.
- Launched `rr-notes` for this project.
- Updated `rr-notes` so each note shows either Markdown view or Code view, controlled by a toggle.
- Added `TODO.md` as the durable work queue.
- Renamed the user-facing sidebar concept from session to workspace in the main UI/docs while keeping `/sessions` API/storage compatibility.
- Tried to remove workspace-card gradients, but the running UI still shows a gradient; this is now marked skipped for a later visual pass.
- Connected sandbox creation to Add Workspace and updated sandbox copy.
- Reviewed the current Add Workspace implementation to document why it is confusing.
- Reworked Add Workspace so existing workspaces are no longer listed as targets. The form now starts with Workspace Type, then a machine-only selector for normal workspaces.

## Current State

- `rr-notes` is running on netochka at `http://127.0.0.1:8126`.
- The Remote Rider `remote-rider` session has a `Notes` tab.
- `TODO.md` now exists locally.
- Workspace naming is implemented.
- Workspace card gradient cleanup is skipped for now because the visible UI still has a gradient.
- Add Workspace no longer treats an existing workspace as a target. Existing workspaces are used only by Panel Source when copying tabs.
- The normal workspace path is now: Workspace Type = Normal, Machine, Panel Source, Workspace Label, Terminal tmux Session.
- The sandbox path is now: Workspace Type = Sandbox, then Configure Sandbox opens the sandbox modal.

## Next Steps

- Improve Add Workspace further by making the sections visually clearer or turning them into a stepper.
- Decide whether Sandbox Workspace should fully live inside Add Workspace or keep using the existing sandbox modal.
- Decide whether `/workspaces` API aliases are worth adding now or later.

## Open Questions

- Should `rr-init` start `rr-notes` automatically alongside `rr-skill`?
- Should `STATUS.md` be committed per project by default, or treated as local session state?
- Should the backend expose `/workspaces` aliases while keeping `/sessions` for compatibility?
- Should Add Workspace default to the active machine, a new machine, or an explicit "choose machine" empty state?
