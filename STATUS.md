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
- Connected sandbox creation to Add Workspace with a `+ Feature sandbox workspace` target option and updated sandbox copy.
- Reviewed the current Add Workspace implementation to document why it is confusing.

## Current State

- `rr-notes` is running on netochka at `http://127.0.0.1:8126`.
- The Remote Rider `remote-rider` session has a `Notes` tab.
- `TODO.md` now exists locally.
- Workspace naming is implemented.
- Workspace card gradient cleanup is skipped for now because the visible UI still has a gradient.
- Add Workspace currently mixes host selection, tab-source selection, tmux session selection, and sandbox redirection in one panel.

## Next Steps

- Redesign Add Workspace around clearer steps: target, workspace type, tabs, terminal.
- Decide whether sandbox belongs inside Add Workspace as a workspace type or remains a separate shortcut.
- Decide whether `/workspaces` API aliases are worth adding now or later.

## Open Questions

- Should `rr-init` start `rr-notes` automatically alongside `rr-skill`?
- Should `STATUS.md` be committed per project by default, or treated as local session state?
- Should the backend expose `/workspaces` aliases while keeping `/sessions` for compatibility?
- Should Add Workspace default to the active machine, a new machine, or an explicit "choose target" empty state?
