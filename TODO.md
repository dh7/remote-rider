# TODO

## Next
- [ ] Redesign Add Workspace: split "where it runs", "what tabs it starts with", and "which terminal/tmux session it opens" into clearer steps.
- [ ] Decide whether sandbox creation should be a first-class Add Workspace mode instead of a separate modal reached from one select option.
- [ ] Decide whether `/workspaces` API aliases are worth adding while keeping `/sessions` compatibility.

## Done
- [x] Rename the user-facing sidebar concept from session to workspace while keeping the `/sessions` API/storage names compatible.
- [x] Connect sandbox creation to Add Workspace by adding a Feature sandbox workspace option and tightening sandbox wording.

## Skipped
- [ ] Remove gradients from workspace cards; current UI still shows a gradient, so leave this for a separate visual pass.
