# TODO

## Next
- [ ] Pull/restart updated hub code on remote machines before migrating their existing Files/Skills/Notes tabs to built-in `/workspaces/*` routes.
- [ ] in the UI remove all CSS "transition" I like when things are just instant.
- [ ] in rr-notes, be sure the server push update to the client if some file changed
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
- [x] Add hub-hosted built-in workspace routes for Notes, Skills, and Files.
- [x] Update Add Workspace and `rr-init` so new workspaces use built-in Notes, Skills, and Files tabs instead of starting per-workspace services.
- [x] Store project paths on workspaces so built-in tabs know which repo they operate on.
- [x] Migrate local netochka workspaces with known project paths to built-in Files/Skills/Notes tab URLs.

## Skipped
- [ ] Remove gradients from workspace cards; current UI still shows a gradient, so leave this for a separate visual pass.
