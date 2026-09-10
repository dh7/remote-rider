# Sessions: control server as the single source of truth

## Problem

Session list + order lives in three places (browser memory, `localStorage`,
control-hub `sessions.json`) synced **both directions with field-merging** on
every read (`refreshSessionsFromControlIfChanged`) and every write
(`flushSessionsToControl`). Two competing "authorities" had to be reconciled,
and the write-merge rebuilt order from the **remote** copy — so any local
reorder was silently discarded ~150ms later. (Fixed symptomatically by making
the merge iterate local order; this spec removes the dual-authority design.)

## Decision

**The control server (`sessions.json`) is the single source of truth** for the
session list, order, and content. The browser is a *view + editor* that writes
through. `localStorage` is demoted to an **offline fallback** (read only when
the server is unreachable). This is required because the server copy is also
written by non-browser actors (agents/CLI via `POST /sessions/{name}/tabs`,
sandbox creation, skill tools) and by other devices.

## Model

```
sessions.json  =  authority (list + order + content)
   │ GET (load + 4s poll) → adopt verbatim, no merge
   ▼
state.sessions (in-memory view) ── every mutation ──▶ PUT full list (verbatim)
   │                                                   guarded by version
   ▼
localStorage = last-known-good cache, read ONLY if server unreachable
```

- **Reads** (`loadProfilesFromBootstrap`, poll): take server sessions + version
  verbatim into state. No field-merge. `localStorage` only on fetch failure.
- **Writes** (reorder / add / remove / panel-edit): optimistic local update +
  render, then debounced `PUT /sessions {sessions, base_version}`.
- **Concurrency**: optimistic version guard. `GET` returns a content `version`
  (hash of normalized sessions). `PUT` sends `base_version`; if it != the
  server's current version → **409** with the current `{sessions, version}`.
  The client **rebases** (reorders server membership into local intent, keeps
  local-new sessions, keeps local content for sessions it holds, appends
  server-only sessions) and retries once. Common path (no conflict) stores the
  browser's list **verbatim** — order preserved, no merge.

Merge logic (`mergeSessionForControlSave`) survives only inside the
conflict-rebase path, not the steady state.

## Changes

**Server**
- `storage.py`: `_sessions_version(normalized) -> str` (sha256[:16] of
  `json.dumps(sorted)`).
- `main.py` `GET /sessions`: add `"version"`.
- `models.py` `SessionsPutRequest`: add `base_version: str | None = None`.
- `main.py` `PUT /sessions`: under `SESSIONS_LOCK`, compare `base_version` to
  current version; `409 {sessions, version}` on mismatch; else save, return new
  `version`.

**Client (`static/app.js`)**
- `state.controlSessionsSignature` → `state.sessionsVersion` (null default).
- `loadSessionsFromControl()` returns `{sessions, version}` | null.
- `adoptServerSessions(sessions, version)` helper (sets state + version +
  localStorage cache).
- `loadProfilesFromBootstrap`: use `loadSessionsFromControl()` for sessions +
  version; fall back to localStorage / bootstrap machines only if server down.
- `refreshSessionsFromControlIfChanged`: compare `version`; adopt verbatim.
- `flushSessionsToControl`: PUT `{sessions, base_version}`; on 409 rebase +
  retry (≤2); on ok update `version` + cache.
- `rebaseOntoServer(serverSessions)`: local-order-first merge (the only merge).
- `closeTab`: drop the signature bookkeeping; next poll reconciles.

## Out of scope

- Moving tab add/remove off `PUT /sessions` onto the `/tabs` endpoints (would
  let `PUT` ignore tabs entirely). Larger change; not needed for truth-ownership.

## Verification

1. Node simulation of flush/rebase: reorder then concurrent server change →
   order preserved, server-only session kept.
2. Live server: `GET` returns version; stale `base_version` PUT → 409 with
   current state; correct `base_version` → 200 + new version.
3. Manual: reorder hal5090 up, move imagebench — hal5090 stays; reload keeps
   order; second device/agent tab-add still appears within one poll.
