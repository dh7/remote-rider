"""Task queue: prompts waiting for an agent to pull them.

One JSON file on the control hub, rewritten atomically under a lock. A task is
addressed to a machine, optionally narrowed to a workspace and/or folder. Taking
a task is exclusive; it stays taken while the Claude session that took it is
alive, and goes back to waiting when that session dies (see `reap`).
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

QUEUE_FILE = Path(__file__).parent / "queue.json"
PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}
KEEP_FINISHED_S = 14 * 24 * 3600
MAX_REQUEUES = 3            # after this many give-ups/deaths a task is flagged stuck
FALLBACK_LEASE_S = 24 * 3600  # takers that report no pid hold a task this long per heartbeat
EDITABLE = ("prompt", "name", "machine", "workspace", "folder", "priority", "autonomous", "stuck")

_LOCK = threading.Lock()


class QueueError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _now() -> float:
    return time.time()


def _load(path: Path | None) -> list[dict[str, Any]]:
    try:
        raw = json.loads((path or QUEUE_FILE).read_text())
    except (OSError, ValueError):
        return []
    return [t for t in raw if isinstance(t, dict)] if isinstance(raw, list) else []


def _save(path: Path | None, tasks: list[dict[str, Any]]) -> None:
    path = path or QUEUE_FILE
    cutoff = _now() - KEEP_FINISHED_S
    tasks = [
        t for t in tasks
        if t.get("status") not in ("done", "cancelled") or (t.get("done_at") or 0) >= cutoff
    ]
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(tasks, indent=1))
    os.replace(tmp, path)


def _clean_folder(folder: Any) -> str:
    folder = str(folder or "").strip()
    return folder.rstrip("/") or ("/" if folder else "")


def _find(tasks: list[dict[str, Any]], task_id: str) -> dict[str, Any]:
    for task in tasks:
        if task.get("id") == task_id:
            return task
    raise QueueError(404, f"no task {task_id}")


def _to_waiting(task: dict[str, Any], note: str | None, now: float) -> None:
    task.update(status="waiting", taken_by=None, taken_at=None, lease_until=None)
    task["requeues"] = int(task.get("requeues") or 0) + 1
    if note:
        task["note"] = note
    if task["requeues"] >= MAX_REQUEUES:
        task["stuck"] = True
    task["updated_at"] = now


def add(fields: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    prompt = str(fields.get("prompt") or "").strip()
    machine = str(fields.get("machine") or "").strip()
    if not prompt:
        raise QueueError(400, "prompt is required")
    if not machine:
        raise QueueError(400, "machine is required")
    now = _now()
    task = {
        "id": uuid.uuid4().hex[:12],
        "prompt": prompt,
        "name": str(fields.get("name") or "").strip()[:60] or None,
        "machine": machine,
        "workspace": str(fields.get("workspace") or "").strip() or None,
        "folder": _clean_folder(fields.get("folder")) or None,
        "priority": fields.get("priority") if fields.get("priority") in PRIORITY_ORDER else None,
        "autonomous": bool(fields.get("autonomous")),
        "status": "waiting",
        "stuck": False,
        "requeues": 0,
        "created_at": now,
        "created_by": str(fields.get("created_by") or "").strip()[:200] or None,
        "updated_at": now,
        "taken_by": None,
        "taken_at": None,
        "lease_until": None,
        "done_at": None,
        "note": None,
    }
    with _LOCK:
        tasks = _load(path)
        tasks.append(task)
        _save(path, tasks)
    return task


def list_tasks(path: Path | None = None, status: str | None = None, machine: str | None = None) -> list[dict[str, Any]]:
    tasks = _load(path)
    if status:
        wanted = set(status.split(","))
        tasks = [t for t in tasks if t.get("status") in wanted]
    if machine:
        tasks = [t for t in tasks if t.get("machine") == machine]
    return sorted(tasks, key=_order)


def _order(task: dict[str, Any]) -> tuple:
    return (PRIORITY_ORDER.get(task.get("priority") or "", 3), task.get("created_at") or 0)


def matches(task: dict[str, Any], machine: str, workspace: str | None, folder: str | None) -> bool:
    """Whether a taker at (machine, workspace, folder) may take `task`."""
    if task.get("machine") != machine:
        return False
    if task.get("workspace") and task["workspace"] != (workspace or ""):
        return False
    want = task.get("folder")
    if want:
        have = _clean_folder(folder)
        if not have or not (have == want or have.startswith(want.rstrip("/") + "/")):
            return False
    return True


def take_next(
    machine: str,
    taker: dict[str, Any],
    workspace: str | None = None,
    folder: str | None = None,
    path: Path | None = None,
) -> dict[str, Any] | None:
    """Atomically take the most urgent waiting task for this taker (P0 first, then oldest)."""
    now = _now()
    with _LOCK:
        tasks = _load(path)
        candidates = sorted(
            (t for t in tasks
             if t.get("status") == "waiting" and not t.get("stuck") and matches(t, machine, workspace, folder)),
            key=_order,
        )
        if not candidates:
            return None
        task = candidates[0]
        has_pid = bool(taker.get("pid"))
        task.update(
            status="taken",
            taken_by={
                "machine": machine,
                "session_id": taker.get("session_id") or None,
                "pid": int(taker["pid"]) if has_pid else None,
                "proc_start": str(taker.get("proc_start") or "") or None,
                "name": str(taker.get("name") or "")[:200] or None,
                "folder": _clean_folder(folder) or None,
            },
            taken_at=now,
            lease_until=None if has_pid else now + FALLBACK_LEASE_S,
            updated_at=now,
        )
        _save(path, tasks)
        return task


def finish(task_id: str, note: str | None = None, path: Path | None = None) -> dict[str, Any]:
    now = _now()
    with _LOCK:
        tasks = _load(path)
        task = _find(tasks, task_id)
        if task.get("status") in ("done", "cancelled"):
            raise QueueError(409, f"task is already {task['status']}")
        task.update(status="done", done_at=now, updated_at=now, lease_until=None)
        if note:
            task["note"] = note
        _save(path, tasks)
        return task


def release(task_id: str, note: str | None = None, path: Path | None = None) -> dict[str, Any]:
    """The taker gives up: back to waiting for someone else."""
    now = _now()
    with _LOCK:
        tasks = _load(path)
        task = _find(tasks, task_id)
        if task.get("status") != "taken":
            raise QueueError(409, "task is not taken")
        _to_waiting(task, note, now)
        _save(path, tasks)
        return task


def heartbeat(task_id: str, path: Path | None = None) -> dict[str, Any]:
    now = _now()
    with _LOCK:
        tasks = _load(path)
        task = _find(tasks, task_id)
        if task.get("status") != "taken":
            raise QueueError(409, "task is not taken")
        if task.get("lease_until"):
            task["lease_until"] = now + FALLBACK_LEASE_S
        task["updated_at"] = now
        _save(path, tasks)
        return task


def update(task_id: str, fields: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    now = _now()
    with _LOCK:
        tasks = _load(path)
        task = _find(tasks, task_id)
        if task.get("status") != "waiting":
            raise QueueError(409, "only waiting tasks can be edited")
        for key in EDITABLE:
            if key not in fields:
                continue
            value = fields[key]
            if key == "priority":
                value = value if value in PRIORITY_ORDER else None
            elif key in ("autonomous", "stuck"):
                value = bool(value)
            elif key == "folder":
                value = _clean_folder(value) or None
            elif key in ("prompt", "machine"):
                value = str(value or "").strip()
                if not value:
                    raise QueueError(400, f"{key} cannot be empty")
            else:
                value = str(value or "").strip() or None
            task[key] = value
        if fields.get("stuck") is False:
            task["requeues"] = 0
        task["updated_at"] = now
        _save(path, tasks)
        return task


def cancel(task_id: str, path: Path | None = None) -> dict[str, Any]:
    now = _now()
    with _LOCK:
        tasks = _load(path)
        task = _find(tasks, task_id)
        if task.get("status") in ("done", "cancelled"):
            return task
        task.update(status="cancelled", done_at=now, updated_at=now, lease_until=None)
        _save(path, tasks)
        return task


AliveFn = Callable[[str, list[tuple[int, str | None]]], "list[bool] | None"]


def reap(alive: AliveFn, path: Path | None = None) -> int:
    """Return taken tasks whose taker is gone to waiting. Returns how many.

    `alive(machine, [(pid, proc_start), ...])` answers per process, or None when the
    machine can't be reached; then its tasks stay taken (never re-queue on a blip).
    Takers without a pid hold their task until the lease runs out.
    """
    now = _now()
    tasks = _load(path)
    by_machine: dict[str, list[dict[str, Any]]] = {}
    expired: set[str] = set()
    for task in tasks:
        if task.get("status") != "taken":
            continue
        taker = task.get("taken_by") or {}
        if taker.get("pid"):
            by_machine.setdefault(taker.get("machine") or task["machine"], []).append(task)
        elif (task.get("lease_until") or 0) < now:
            expired.add(task["id"])
    dead: set[str] = set(expired)
    for machine, group in by_machine.items():
        answers = alive(machine, [(int(t["taken_by"]["pid"]), t["taken_by"].get("proc_start")) for t in group])
        if answers is None:
            continue
        for task, is_alive in zip(group, answers):
            if not is_alive:
                dead.add(task["id"])
    if not dead:
        return 0
    with _LOCK:
        tasks = _load(path)
        count = 0
        for task in tasks:
            # Re-check under the lock: it may have finished meanwhile.
            if task.get("id") in dead and task.get("status") == "taken":
                _to_waiting(task, None, now)
                count += 1
        if count:
            _save(path, tasks)
        return count
