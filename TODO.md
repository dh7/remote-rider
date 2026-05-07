# TODO

## Next
- [ ] Decide whether Sandbox Workspace should fully live inside Add Workspace or keep using the existing sandbox modal.
- [ ] Decide whether `/workspaces` API aliases are worth adding while keeping `/sessions` compatibility.

## Done
- [x] Add `install.sh` to link `rr-init`, `rr-files`, `rr-skill`, and `rr-notes` into `~/.local/bin`; ran it so `rr-init --help` works from other repos.
- [x] Match the left workspace panel background color to the tab panel background color.
- [x] Rename the user-facing sidebar concept from session to workspace while keeping the `/sessions` API/storage names compatible.
- [x] Connect sandbox creation to Add Workspace through an explicit Workspace Type selector and tighten sandbox wording.
- [x] Remove existing workspaces from the Add Workspace target selector.
- [x] Add an explicit Workspace Type selector so normal and sandbox workspaces are separate concepts.
- [x] Group Add Workspace into clearer sections: Type, Machine, Tabs, and Terminal.
- [x] Replace Add Workspace tab source/template controls with default service checkboxes: Terminal mandatory, Notes/Skills/Files selected by default.
- [x] Move Workspace Label, Machine, and Workspace Type into one Workspace config section.
- [x] List additional live services in Add Workspace Tabs as unchecked options.
- [x] Remove the small tmux session summary text from Add Workspace.

## Skipped
- [ ] Remove gradients from workspace cards; current UI still shows a gradient, so leave this for a separate visual pass.
