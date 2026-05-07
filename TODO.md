# TODO

## Next
- [ ] Improve Add Workspace further by turning the current single form into clearer sections or a stepper: type, machine, tabs, terminal.
- [ ] Decide whether Sandbox Workspace should reuse the Add Workspace shell fully instead of redirecting into the existing sandbox modal.
- [ ] Decide whether `/workspaces` API aliases are worth adding while keeping `/sessions` compatibility.

## Done
- [x] Rename the user-facing sidebar concept from session to workspace while keeping the `/sessions` API/storage names compatible.
- [x] Connect sandbox creation to Add Workspace through an explicit Workspace Type selector and tighten sandbox wording.
- [x] Remove existing workspaces from the Add Workspace target selector; workspaces are now only used as tab-copy sources, not targets.
- [x] Add an explicit Workspace Type selector so normal and sandbox workspaces are separate concepts.

## Skipped
- [ ] Remove gradients from workspace cards; current UI still shows a gradient, so leave this for a separate visual pass.
