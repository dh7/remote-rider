"""Live view of the Claude Code sessions on this machine, located in tmux.

Reads the per-process files Claude Code keeps in ~/.claude/sessions and joins them
to tmux panes by tty, so each one can be linked to its Remote Rider workspace.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import threading
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

CLAUDE_HOME = Path.home() / ".claude"
PRIORITIES_FILE = Path(__file__).parent / "claude_priorities.json"
PRIORITY_VALUES = ("P0", "P1", "P2")
_PRIORITIES_LOCK = threading.Lock()
WINDOW_ID_RE = re.compile(r"^@[0-9]+$")
JOB_ID_RE = re.compile(r"^[0-9a-z][0-9a-z-]{0,63}$")
UNSTARTED_JOB_NEEDS = "send a prompt to start"
_TAIL_BYTES = 1024 * 1024
_ENTRY_MARKERS = (b'"custom-title"', b'"ai-title"', b'"last-prompt"', b'"user"', b'"assistant"')

# transcript path -> (mtime_ns, parsed summary); transcripts reach many MB and the
# page polls, so each one is only re-read after it changes.
_summary_cache: dict[str, tuple[int, dict[str, Any]]] = {}


def _proc_info(pid: int) -> tuple[str, str] | None:
    """(start time, tty path) for a running pid, or None if it is gone."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        tty = os.readlink(f"/proc/{pid}/fd/0")
    except OSError:
        return None
    # Fields after the parenthesised command name; starttime is field 22 of stat.
    return stat.rsplit(")", 1)[1].split()[19], tty


def _tmux_panes() -> dict[str, dict[str, str]]:
    """Pane location by tty. `on_screen`: its window is the one an attached client shows."""
    if not shutil.which("tmux"):
        return {}
    fmt = "\t".join([
        "#{pane_tty}", "#{session_attached}", "#{window_active}", "#{session_name}",
        "#{window_id}", "#{pane_current_path}", "#{window_name}",
    ])
    result = subprocess.run(
        ["tmux", "list-panes", "-a", "-F", fmt], capture_output=True, text=True, check=False
    )
    panes: dict[str, dict[str, Any]] = {}
    for line in result.stdout.splitlines():
        parts = line.split("\t", 6)
        if len(parts) == 7:
            panes[parts[0]] = {
                "on_screen": parts[1] not in ("", "0") and parts[2] == "1",
                "session": parts[3],
                "window": parts[4],
                "path": parts[5],
                "window_name": parts[6],
            }
    return panes


def _workspace_for_folder(
    cwd: str,
    panes: dict[str, dict[str, Any]],
    by_tmux: dict[str, dict[str, Any]],
    workspaces: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """The workspace a session with no terminal of its own belongs to, by folder.

    First the workspace whose tmux panes sit in that folder most often, then one
    whose project folder contains it.
    """
    votes = Counter(
        by_tmux[pane["session"]]["name"]
        for pane in panes.values()
        if pane.get("path") == cwd and pane["session"] in by_tmux
    )
    if votes:
        name = votes.most_common(1)[0][0]
        return next(w for w in by_tmux.values() if w["name"] == name)
    for workspace in workspaces:
        project = (workspace.get("project") or "").rstrip("/")
        if project and (cwd == project or cwd.startswith(project + "/")):
            return workspace
    return None


def _claude_bin() -> str:
    return shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude")


def _job_state(claude_home: Path, job_id: str) -> dict[str, Any]:
    """Claude Code's record for a background job (state, tempo, needs, ...)."""
    if not JOB_ID_RE.match(job_id):
        return {}
    try:
        state = json.loads((claude_home / "jobs" / job_id / "state.json").read_text())
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def attach_window(job_id: str, tmux_session: str, cwd: str, label: str) -> str | None:
    """Window id of a tmux window showing background job `job_id`.

    Reuses a window already running `claude attach <job>`; otherwise opens one in
    `tmux_session` without switching to it. The window keeps a shell after the
    attach ends, and the background session keeps running either way.
    """
    if not JOB_ID_RE.match(job_id) or not shutil.which("tmux"):
        return None
    listing = subprocess.run(
        ["tmux", "list-panes", "-s", "-t", f"={tmux_session}", "-F", "#{window_id}\t#{pane_start_command}"],
        capture_output=True, text=True, check=False,
    )
    for line in listing.stdout.splitlines():
        window, _, command = line.partition("\t")
        if f"attach {job_id};" in command:
            return window
    command = f"{shlex.quote(_claude_bin())} attach {job_id}; exec \"${{SHELL:-bash}}\""
    result = subprocess.run(
        ["tmux", "new-window", "-d", "-P", "-F", "#{window_id}", "-t", f"={tmux_session}:",
         "-n", label, "-c", cwd if os.path.isdir(cwd) else str(Path.home()), command],
        capture_output=True, text=True, check=False,
    )
    window = result.stdout.strip()
    return window if result.returncode == 0 and WINDOW_ID_RE.match(window) else None


def select_window(session: str, window: str) -> bool:
    if not WINDOW_ID_RE.match(window) or not shutil.which("tmux"):
        return False
    result = subprocess.run(
        ["tmux", "select-window", "-t", f"={session}:{window}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


# Per Claude session. A workspace's own priority lives on the workspace (sessions.json).
def load_priorities(path: Path = PRIORITIES_FILE) -> dict[str, str]:
    """{claude session id: "P0" | "P1" | "P2"}"""
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in raw.items() if v in PRIORITY_VALUES}


def set_priority(session_id: str, priority: str | None, path: Path = PRIORITIES_FILE) -> dict[str, str]:
    """Set (or clear, with None) one session's priority and return them all."""
    if priority is not None and priority not in PRIORITY_VALUES:
        raise ValueError("bad priority")
    with _PRIORITIES_LOCK:
        table = load_priorities(path)
        if priority is None:
            table.pop(session_id, None)
        else:
            table[session_id] = priority
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(table, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, path)
    return table


def _transcript(session_id: str, claude_home: Path) -> Path | None:
    return next((claude_home / "projects").glob(f"*/{session_id}.jsonl"), None)


def _timestamp_ms(value: Any) -> int:
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return 0


def _summarize(path: Path, mtime_ns: int) -> dict[str, Any]:
    """Title, last prompt and last message time from the end of a transcript.

    The time comes from the last user/assistant entry, never the file's mtime: idle
    Claude processes keep appending metadata, so the mtime of a weeks-old session is
    recent.
    """
    key = str(path)
    cached = _summary_cache.get(key)
    if cached and cached[0] == mtime_ns:
        return cached[1]
    with path.open("rb") as handle:
        handle.seek(max(0, path.stat().st_size - _TAIL_BYTES))
        lines = handle.read().splitlines()
    custom = ai = last_prompt = ""
    last_message = 0
    for raw in lines:
        if not any(marker in raw for marker in _ENTRY_MARKERS):
            continue
        try:
            entry = json.loads(raw)
        except ValueError:
            continue  # the tail read can start mid-line
        if entry.get("type") in ("user", "assistant"):
            last_message = max(last_message, _timestamp_ms(entry.get("timestamp")))
        custom = entry.get("customTitle") or custom
        ai = entry.get("aiTitle") or ai
        last_prompt = entry.get("lastPrompt") or last_prompt
    summary = {"title": custom or ai, "last_prompt": last_prompt, "last_message": last_message}
    _summary_cache[key] = (mtime_ns, summary)
    return summary


def _workspace_tmux(workspace: dict[str, Any]) -> str | None:
    for tab in workspace.get("tabs") or []:
        if tab.get("service") == "terminal":
            arg = parse_qs(urlsplit(tab.get("path") or "").query).get("arg")
            if arg:
                return arg[0]
    return None


def _tmux_session_to_workspace(workspaces: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for workspace in workspaces:
        tmux_session = _workspace_tmux(workspace)
        if tmux_session:
            mapping[tmux_session] = workspace
    return mapping


def list_sessions(
    workspaces: list[dict[str, Any]],
    claude_home: Path = CLAUDE_HOME,
) -> list[dict[str, Any]]:
    """Running Claude sessions, most recently active first.

    `workspaces` are the Remote Rider sessions whose terminals live on this machine.
    """
    panes = _tmux_panes()
    by_tmux = _tmux_session_to_workspace(workspaces)
    rows: list[dict[str, Any]] = []
    for path in (claude_home / "sessions").glob("*.json"):
        try:
            info = json.loads(path.read_text())
            pid = int(info["pid"])
            session_id = str(info["sessionId"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        proc = _proc_info(pid)
        # A leftover file can name a pid since reused by another process.
        if proc is None or str(info.get("procStart", proc[0])) != proc[0]:
            continue
        if info.get("spare"):
            continue  # a pre-warmed process waiting to become the next background job

        last_active = int(info.get("updatedAt") or info.get("startedAt") or 0)
        summary: dict[str, Any] = {"title": "", "last_prompt": "", "last_message": 0}
        transcript = _transcript(session_id, claude_home)
        if transcript is not None:
            try:
                summary = _summarize(transcript, transcript.stat().st_mtime_ns)
            except OSError:
                pass
        last_active = max(last_active, summary["last_message"])

        pane = panes.get(proc[1])
        workspace = by_tmux.get(pane["session"]) if pane else None
        # Background sessions (`claude --bg`, forks) run in Claude's own daemon, not
        # tmux; they open via `claude attach` in their folder's workspace.
        background = info.get("kind") == "bg" and pane is None
        status = info.get("status") or ""
        waiting_for = info.get("waitingFor") or ""
        job_id = ""
        if background:
            workspace = _workspace_for_folder(str(info.get("cwd") or ""), panes, by_tmux, workspaces)
            job_id = str(info.get("jobId") or session_id[:8])
            # The session file can say idle while the job waits on you; the job
            # record knows. A fork that was never given a prompt is also "blocked"
            # ("send a prompt to start"), but nothing is waiting there: it's idle.
            job = _job_state(claude_home, job_id)
            needs = str(job.get("needs") or "")
            if job.get("tempo") == "blocked" and needs != UNSTARTED_JOB_NEEDS:
                status, waiting_for = "waiting", needs or "input needed"
            if not summary["title"]:
                summary["title"] = str(job.get("intent") or "")[:120]
        rows.append({
            "pid": pid,
            "session_id": session_id,
            "name": info.get("name") or "",
            "cwd": info.get("cwd") or "",
            # busy | idle | waiting (needs the user; waiting_for says why)
            "background": background,
            "job_id": job_id,
            "status": status,
            "waiting_for": waiting_for,
            "status_since": int(info.get("statusUpdatedAt") or info.get("startedAt") or 0),
            "on_screen": bool(pane and pane["on_screen"]),
            "last_active": last_active,
            "title": summary["title"],
            "last_prompt": summary["last_prompt"],
            "tmux_session": pane["session"] if pane else None,
            "tmux_window": pane["window"] if pane else None,
            "tmux_window_name": pane["window_name"] if pane else None,
            "workspace": workspace["name"] if workspace else None,
            "workspace_display": (workspace.get("display") or workspace["name"]) if workspace else None,
            "workspace_priority": workspace.get("priority") if workspace else None,
            "workspace_tmux": _workspace_tmux(workspace) if workspace else None,
        })
    rows.sort(key=lambda row: row["last_active"], reverse=True)
    return rows
