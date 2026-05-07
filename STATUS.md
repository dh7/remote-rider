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
- Grouped Add Workspace into clearer sections: Type, Machine, Tabs, and Terminal.
- Replaced the Tabs section with service checkboxes. Terminal is mandatory; Notes, Skills, and Files are selected by default.
- Moved Workspace Label, Machine, and Workspace Type into one Workspace config section, with label first.
- Add Workspace Tabs now includes additional live services from the selected machine as unchecked options.
- Removed the small tmux session summary text below the terminal session selector.

## Current State

- `rr-notes` is running on netochka at `http://127.0.0.1:8126`.
- The Remote Rider `remote-rider` session has a `Notes` tab.
- `TODO.md` now exists locally.
- Workspace naming is implemented.
- Workspace card gradient cleanup is skipped for now because the visible UI still has a gradient.
- Add Workspace no longer treats an existing workspace as a target.
- The normal workspace path is now: Workspace config, service checkboxes, Terminal tmux Session.
- The Tabs section now creates tabs from checked services instead of templates or copied workspaces.
- The sandbox path is now: Workspace Type = Sandbox, then Configure Sandbox opens the sandbox modal.
- Add Workspace is still a single modal, but it is no longer one undifferentiated form.

## Next Steps

- Decide whether Sandbox Workspace should fully live inside Add Workspace or keep using the existing sandbox modal.
- Decide whether `/workspaces` API aliases are worth adding now or later.

## Open Questions

- Should `rr-init` start `rr-notes` automatically alongside `rr-skill`?
- Should `STATUS.md` be committed per project by default, or treated as local session state?
- Should the backend expose `/workspaces` aliases while keeping `/sessions` for compatibility?
- Should Add Workspace default to the active machine, a new machine, or an explicit "choose machine" empty state?
