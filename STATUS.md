# Status

## Current Focus

Clean up Remote Rider workspace naming and sidebar card styling.

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
- Removed the gradient/color wash from workspace cards; only the selected workspace uses its color beyond the dot.
- Connected sandbox creation to Add Workspace with a `+ Feature sandbox workspace` target option and updated sandbox copy.

## Current State

- `rr-notes` is running on netochka at `http://127.0.0.1:8126`.
- The Remote Rider `remote-rider` session has a `Notes` tab.
- `TODO.md` now exists locally.
- Workspace naming/style changes are implemented and ready for visual review in the running control UI.

## Next Steps

- Decide whether `/workspaces` API aliases are worth adding now or later.
- Visually review the updated Add Workspace and Sandbox Workspace flows in the running control UI.

## Open Questions

- Should `rr-init` start `rr-notes` automatically alongside `rr-skill`?
- Should `STATUS.md` be committed per project by default, or treated as local session state?
- Should the backend expose `/workspaces` aliases while keeping `/sessions` for compatibility?
