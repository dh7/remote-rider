# Launch & Queue: start Claude tasks from the Sessions view, or queue them for agents

Date: 2026-11-05. Status: design, awaiting approval.

## Intent

dh7 runs many Claude Code sessions across homelab machines (netochka, gx10, hal5090, ...),
each living in a byobu/tmux session that RemoteRider (RR) calls a *workspace*. The
Sessions view (`/?view=latest`, the "history" view) already lists every running Claude
session with its state and priority.

Two things are missing:

1. **Starting a task without leaving the view.** Today, to give Claude a new job dh7 opens
   the workspace, presses F2 in byobu, `cd`s, types `claude`, then types the prompt. The view
   should do that from a form.
2. **A queue of prompts that agents pull from.** The motivating case: dh7 sees a new image
   model somewhere and wants to paste "model X, link, notes" into a queue. On gx10 a Claude
   session runs in a loop; whenever it finishes a job it pulls the next task meant for gx10
   (and its folder) and implements the model (builds the capsule). Later, agents may
   chain work (capsule on gx10 -> catalog -> batch server -> website) by writing new tasks
   into the queue themselves. How chaining works is explicitly undecided; the queue just has
   to make it possible.

The queue is an **API first**. RR already has a FastAPI API (56 endpoints in `main.py`,
agent-facing docs at `/agents` and `/.well-known/agent-api`). The queue extends it: agents
must be able to read *and* write the queue. The UI is one client of that API.

## Goals

- A **+** button in the Sessions view header opens a popup ("New task") with:
  prompt (multi-line), computer, workspace, folder, priority (none/P0/P1/P2).
- Two actions in the popup:
  - **Start**: run it now. Equivalent of byobu F2 in the chosen workspace's tmux session:
    new window, in the chosen folder, running `claude` with the prompt.
    dh7 **stays on the Sessions view** (no workspace switch); the new session appears in
    the list with the chosen priority.
  - **Queue**: save it as a queued task instead of starting it.
- Queue API (read + write) usable by any agent: add, take next, finish, list, edit, delete.
- **Take is exclusive**: two agents asking at once never get the same task.
- **A task whose taker dies or gives up goes back to waiting automatically.**
- A small `rr-queue` CLI next to `rr-files`/`rr-notes` so an agent can do
  `rr-queue next` / `rr-queue done "<note>"` without writing curl; it reads the nearest
  `.rr` like the other `rr-*` tools.
- The Sessions view shows the queue: waiting, taken (by whom), recently done (with note).
  Waiting tasks can be deleted or re-prioritized from there.
- Agent docs (`/agents`, `/.well-known/agent-api`) describe the queue so a Claude session
  can learn how to use it.

## Non-goals

- RR does not orchestrate multi-step pipelines (capsule -> catalog -> website). Agents can
  enqueue follow-up tasks themselves; RR does not understand the chain.
- RR does not write or run the gx10 "loop" agent. That is a Claude session dh7 instructs
  ("when done, take the next queue task for this folder"). RR only provides the queue and
  the CLI. (A one-paragraph usage recipe in the agent docs is in scope.)
- RR does not auto-start queued tasks. Queue = "an agent will pull this"; Start = "run now".
- No new auth model. Same-origin / tailnet rules that protect existing endpoints apply.

## Anti-goals (would be a failure even if it "works")

- The same task done twice by two agents.
- A task silently stuck "taken" forever after its agent died.
- Start typing the prompt as keystrokes that get mangled (long prompts, quotes, newlines).
- Start yanking dh7's attached terminal to the new window (the view must not switch the
  user's visible tmux window; see the existing care in `focusWorkspace`, which only selects
  windows on explicit click).
- Losing queued tasks on a hub restart (queue must be persisted on disk, atomically, like
  `claude_priorities.json` / `sessions.json`).

## Constraints and facts from the codebase

- Control hub runs on netochka port 7000 (`RUN_MODE=all`); it owns `sessions.json`
  (workspaces) and `machines.json`. Every machine runs its own hub on port 7000;
  control forwards to them via `/.../proxy` endpoints with `host` + `hub_port`.
- A workspace record has `name`, `machine {name, host}`, `display`, `color`, optional
  `project` (folder; most workspaces don't set it), optional `priority` (P0-P2), and a
  Terminal tab whose path `/?arg=<tmux session>` names its tmux session
  (`claude_sessions._workspace_tmux`).
- Existing `/agents/start` (`tmux.py:_start_agent_local`) starts a command in a **new tmux
  session**, not a window in an existing one. Start needs a "new window in existing session"
  variant (`tmux new-window -d -t =<session>: -c <folder> ...`), like
  `claude_sessions.attach_window` already does. `-d` keeps the user's current window.
- Claude accepts the initial prompt as an argument: `claude "<prompt>"`. Pass it as an argv
  element (no shell interpolation of the prompt; e.g. write it to a temp file and use
  `claude "$(cat file)"`, or exec via an argv list). Window ends with
  `exec "${SHELL:-bash}"` so it stays open after Claude exits (same pattern as attach).
- Session priorities live in `claude_priorities.json` keyed by Claude **session id**, which
  is unknown until Claude starts. Start must link the chosen priority to the new session
  once it appears (e.g. pass `--session-id <uuid>` that RR generates, if supported by the
  installed claude; otherwise match by the new window's pane tty -> pid -> session file).
- **The Sessions view only lists Claude sessions on the control hub's machine**
  (`/claude/sessions` reads local `~/.claude/sessions`). gx10's hub currently returns 404 on
  `/claude/sessions` (it runs older code). See "Remote machines" below.
- Folder browsing exists for the Files tab (`workspace_builtins.py`, `iterdir`); the folder
  picker can reuse it, served by the hub of the chosen machine.
- Tests: `tests/` with pytest (99 passing). New code needs tests in the same style
  (`tests/test_claude_sessions.py` fakes `/proc`, tmux and `~/.claude`).

## How it works (decided parts)

### The form

- Computer list = machines in `machines.json`. Workspace list = workspaces on that computer.
  Choosing a workspace sets the computer (a workspace belongs to one machine); choosing a
  computer filters workspaces.
- Folder defaults to the workspace's `project` if set, otherwise the most common pane folder
  of that workspace's tmux session (same heuristic as `_workspace_for_folder`), otherwise
  `~`. dh7 can browse to another folder on that computer.
- Priority: none/P0/P1/P2. On Start it becomes the new session's priority; on Queue it is
  the task's priority.
- **Permissions (dh7 decided):** Start launches `claude` with dh7's normal settings, so a
  started task stops for approval like any session and shows as "needs you". The form has
  an **"Autonomous"** checkbox, **off by default**; when on, Start adds
  `--dangerously-skip-permissions`. Queued tasks carry the same flag, but it is only a
  request: the pulling agent's own permissions decide (RR does not launch queued tasks).
  An agent adding a task via the API cannot make Start run autonomously on its behalf
  unless the request comes through the UI (same-origin) path; my call, to avoid
  agents escalating each other.
- After Start: popup closes, a toast "Started in <workspace>", the view stays; the new row
  appears on the next refresh.

### The queue model

A task:

```
id, prompt, machine, workspace (optional), folder (optional), priority (P0/P1/P2/none),
autonomous (bool, default false),
status: waiting | taken | done | cancelled,
created_at, created_by (free text: "dh7 via UI", or the agent's session/name),
taken_by {machine, session_id, pid, proc_start}, taken_at, lease_until (only when no pid),
release_count, done_at, note
```

Stored on the **control hub** (single source of truth), persisted to a JSON file next to
`sessions.json`, written atomically under a lock, git-ignored.

### Queue API (on the control hub)

- `POST /queue` add a task. Returns it.
- `GET /queue?status=&machine=&workspace=&folder=` list.
- `POST /queue/next` take the most urgent waiting task matching
  `{machine, workspace?, folder?, taker}`. Order: P0, P1, P2, none; oldest first within a
  priority. Atomic: marks it `taken` with a lease. Returns the task or 204/empty.
- `POST /queue/{id}/done {note?}`, `POST /queue/{id}/release {note?}` (give up: back to
  waiting), `POST /queue/{id}/heartbeat` (extend the lease).
- `PATCH /queue/{id}` edit prompt/priority/target while waiting; `DELETE /queue/{id}`.
- **Targeting (dh7 decided):** every task has a `machine` (required). `workspace` and
  `folder` are optional and **empty by default when queuing from the form**: the form's
  Queue action only saves a workspace/folder if dh7 explicitly sets one (the defaults the
  form shows for Start are not carried into a queued task). So "model X, link, notes"
  queued for gx10 goes to *any* agent on gx10.
- Matching for `next`: machine must match. If the task has a workspace, it is a hard filter
  (the taker must send that workspace; `rr-queue` sends the workspace from `.rr`). If the
  task has a folder, the taker's folder must be that folder or inside it. A task with
  neither matches any taker on its machine.

### Auto re-queue (dh7 decided: liveness, not a timer)

Tasks can run for a very long time (hours or days when they involve training), so a fixed
time lease is wrong: it would hand a still-running task to a second agent.

Rule: **a task stays taken as long as the Claude session that took it is alive**, even if
that session is blocked waiting on dh7 (it then shows as "needs you" in the Sessions view,
which is the right signal). It returns to waiting when that session's process is gone, or
when the agent calls release.

- `next` records the taker: machine + Claude session id + pid (+ process start time to
  defeat pid reuse, as `claude_sessions` already does via `procStart`). `rr-queue` finds
  these by walking up its parent processes to the `claude` process and reading
  `~/.claude/sessions/<pid>.json`. A plain-curl agent passes them itself; if it passes no
  pid, the task falls back to a long lease (my call: 24 h, extendable with heartbeat).
- Each machine's hub exposes a small runtime endpoint "are these (pid, procStart) alive?".
  The control hub checks taken tasks against the taker's machine hub lazily (on queue
  reads/writes and on the Sessions view refresh, rate-limited, e.g. at most once per 30 s).
  If the taker's machine is **unreachable**, the task stays taken (never re-queue on a
  network blip); it is shown as "taker unreachable" in the view.
- Requires updating every machine that takes tasks (gx10 first); fleet update exists.

### `rr-queue` CLI

`rr-queue add "<prompt>" [--machine --workspace --folder --priority]`,
`rr-queue next` (prints the prompt + id; machine/folder default to this host and cwd;
control URL from `.rr`), `rr-queue done [id] [note]`, `rr-queue release [id]`,
`rr-queue list`. Remembers the current task id per folder so `done` needs no id.

### Remote machines

- **Queue**: works for every machine immediately, because it lives on the control hub and
  agents reach it over the tailnet (`.rr` has the control URL).
- **Start on another computer**: control proxies to that machine's hub, which opens the
  window (new runtime endpoint + `/proxy`, like `/agents/start`). Requires that machine's
  hub to be updated (fleet update exists: `/admin/update-all-remotes`).
- **Seeing remote sessions in the view**: out of scope for this design (the view stays
  netochka-only). A Start on gx10 therefore won't show a row; the toast says where it
  started. Aggregating remote sessions is a separate follow-up.

## Left to the builder

- Exact endpoint payload shapes, status codes, file name of the queue store.
- How the new session's id is linked to the chosen priority (see constraint above).
- Popup layout and styling (match the existing Sessions view and workspace settings dialog;
  must work at phone width).
- Where the Queue section sits in the view (my call: a collapsible section above the
  session list, showing waiting + taken, with done in the last 24 h collapsed).
- Lease length and heartbeat cadence for the no-pid fallback.

## Calls made by Claude (not discussed with dh7; check these)

- **Priority link on Start:** RR generates a session UUID and launches
  `claude --session-id <uuid>` if the installed claude supports it, writing the priority
  under that id up front; otherwise it matches the new window's pane tty to the session file.
- **Remote Start priority:** priorities stay on the control hub keyed by session id, even for
  sessions on other machines (they will matter once remote sessions are shown).
- **Missing tmux session:** Start first calls the existing tmux-ensure API so a workspace
  whose byobu session isn't running still works.
- **Folder browsing** may leave the workspace folder (any folder under the user's home on
  that machine), via the chosen machine's hub.
- **Delete** marks a task `cancelled` (kept in history) rather than erasing it; done and
  cancelled tasks are kept 14 days, then pruned.
- **Release loop guard:** a task released or re-queued 3 times stops being handed out
  and is flagged "stuck" in the view for dh7 to look at.
- **Autonomous via API:** only the UI path can request an autonomous Start (see Permissions).
