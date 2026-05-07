# Status

## Current Focus

Build and launch `rr-notes` so Remote Rider sessions have a visible project handoff tab.

## Last Updated

2026-05-07 Europe/Paris

## What Changed

- Added `rr-notes`, a Remote Rider-compatible notes/status server.
- Added Notes guidance to `Agents.md`.
- Updated the `dh7skills` `remote-rider` skill with `rr-notes` and `STATUS.md` guidance.
- Launched `rr-notes` for this project.
- Updated `rr-notes` so each note shows either Markdown view or Code view, controlled by a toggle.

## Current State

- `rr-notes` is running on netochka at `http://127.0.0.1:8126`.
- The Remote Rider `remote-rider` session has a `Notes` tab.
- `TODO.md` now exists locally.
- The worktree has pre-existing/live dirty state from Remote Rider session updates and related local project setup.

## Next Steps

- Decide which live `sessions.json` changes should remain local runtime state versus committed defaults.
- Decide whether the current local `rr-init`/`rr-skill` service startup changes should be committed separately.

## Open Questions

- Should `rr-init` start `rr-notes` automatically alongside `rr-skill`?
- Should `STATUS.md` be committed per project by default, or treated as local session state?
