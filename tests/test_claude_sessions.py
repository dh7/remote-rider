import json
import os

import claude_sessions

WORKSPACES = [{
    "name": "imgbench",
    "display": "imgbench.ai",
    "tabs": [
        {"label": "Files", "service": "builtin", "path": "/workspaces/imgbench/files"},
        {"label": "Terminal", "service": "terminal", "path": "/?arg=img-tmux"},
    ],
}]
PANES = {
    "/dev/pts/1": {"session": "img-tmux", "window": "@7", "window_name": "vote", "on_screen": True, "path": "/code/img"},
    "/dev/pts/2": {"session": "stray-tmux", "window": "@8", "window_name": "bash", "on_screen": False, "path": "/code/x"},
}


def _write_session(home, pid, sid, updated, **extra):
    (home / "sessions").mkdir(exist_ok=True)
    (home / "sessions" / f"{pid}.json").write_text(json.dumps({
        "pid": pid, "sessionId": sid, "procStart": str(pid), "cwd": "/code/x",
        "name": f"x-{pid}", "status": "idle", "updatedAt": updated, **extra,
    }))


def _write_transcript(home, sid, lines, mtime_ms):
    folder = home / "projects" / "-code-x"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sid}.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    os.utime(path, (mtime_ms / 1000, mtime_ms / 1000))


def _fake_system(monkeypatch, procs):
    monkeypatch.setattr(claude_sessions, "_tmux_panes", lambda: PANES)
    monkeypatch.setattr(claude_sessions, "_proc_info", procs.get)
    claude_sessions._summary_cache.clear()


def test_sorted_by_activity_and_joined_to_workspace(tmp_path, monkeypatch):
    _fake_system(monkeypatch, {1: ("1", "/dev/pts/1"), 2: ("2", "/dev/pts/2")})
    _write_session(tmp_path, 1, "old", 1000)
    _write_session(tmp_path, 2, "new", 2000)
    # The last message beats the older status timestamp and flips the order.
    _write_transcript(tmp_path, "old", [
        {"type": "user", "timestamp": "1970-01-01T00:00:01.000Z"},
        {"type": "assistant", "timestamp": "1970-01-01T00:00:05.000Z"},
    ], 9000)

    rows = claude_sessions.list_sessions(WORKSPACES, tmp_path)

    assert [r["session_id"] for r in rows] == ["old", "new"]
    assert rows[0]["last_active"] == 5000
    assert rows[0]["workspace"] == "imgbench"
    assert rows[0]["workspace_display"] == "imgbench.ai"
    assert rows[0]["tmux_window"] == "@7"
    assert rows[1]["workspace"] is None  # tmux session no workspace points at
    assert rows[1]["tmux_session"] == "stray-tmux"


def test_metadata_writes_do_not_count_as_activity(tmp_path, monkeypatch):
    _fake_system(monkeypatch, {1: ("1", "/dev/pts/1")})
    _write_session(tmp_path, 1, "s", 1000)
    _write_transcript(tmp_path, "s", [
        {"type": "ai-title", "aiTitle": "t", "timestamp": "2030-01-01T00:00:00.000Z"},
    ], 9_000_000)
    assert claude_sessions.list_sessions(WORKSPACES, tmp_path)[0]["last_active"] == 1000


def test_waiting_state_is_passed_through(tmp_path, monkeypatch):
    _fake_system(monkeypatch, {1: ("1", "/dev/pts/1")})
    _write_session(tmp_path, 1, "s", 1000, status="waiting", waitingFor="permission prompt",
                   statusUpdatedAt=1500)
    row = claude_sessions.list_sessions(WORKSPACES, tmp_path)[0]
    assert (row["status"], row["waiting_for"], row["status_since"]) == ("waiting", "permission prompt", 1500)
    assert row["on_screen"] is True


def test_tmux_panes_parses_window_names_with_separators(monkeypatch):
    line = "/dev/pts/4\t1\t1\timg\t@7\t/code/img\ta|b\tc\n/dev/pts/5\t0\t1\tother\t@9\t/tmp\tx\n"
    monkeypatch.setattr(claude_sessions.shutil, "which", lambda _: "/usr/bin/tmux")
    monkeypatch.setattr(claude_sessions.subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": line})())
    panes = claude_sessions._tmux_panes()
    assert panes["/dev/pts/4"] == {
        "on_screen": True, "session": "img", "window": "@7", "path": "/code/img", "window_name": "a|b\tc",
    }
    assert panes["/dev/pts/5"]["on_screen"] is False  # nobody attached


def test_dead_and_reused_pids_are_dropped(tmp_path, monkeypatch):
    _fake_system(monkeypatch, {2: ("999", "/dev/pts/2")})
    _write_session(tmp_path, 1, "exited", 1000)
    _write_session(tmp_path, 2, "reused-pid", 1000)
    assert claude_sessions.list_sessions(WORKSPACES, tmp_path) == []


def test_session_outside_tmux_is_listed_without_a_location(tmp_path, monkeypatch):
    _fake_system(monkeypatch, {1: ("1", "/dev/pts/9")})
    _write_session(tmp_path, 1, "bare", 1000)
    row = claude_sessions.list_sessions(WORKSPACES, tmp_path)[0]
    assert row["tmux_session"] is None and row["workspace"] is None


def test_latest_titles_win_and_custom_title_beats_ai_title(tmp_path, monkeypatch):
    _fake_system(monkeypatch, {1: ("1", "/dev/pts/1")})
    _write_session(tmp_path, 1, "s", 1000)
    _write_transcript(tmp_path, "s", [
        {"type": "ai-title", "aiTitle": "first"},
        {"type": "custom-title", "customTitle": "mine"},
        {"type": "ai-title", "aiTitle": "second"},
        {"type": "last-prompt", "lastPrompt": "are we live?"},
    ], 5000)
    row = claude_sessions.list_sessions(WORKSPACES, tmp_path)[0]
    assert row["title"] == "mine"
    assert row["last_prompt"] == "are we live?"


def test_select_window_rejects_non_window_ids(monkeypatch):
    monkeypatch.setattr(claude_sessions.subprocess, "run", lambda *a, **k: 1 / 0)
    assert claude_sessions.select_window("s", "@1; kill-server") is False


def test_priorities_round_trip_and_clear(tmp_path):
    path = tmp_path / "prio.json"
    assert claude_sessions.load_priorities(path) == {}
    claude_sessions.set_priority("abc", "P0", path)
    claude_sessions.set_priority("def", "P2", path)
    assert claude_sessions.load_priorities(path) == {"abc": "P0", "def": "P2"}
    claude_sessions.set_priority("abc", None, path)
    assert claude_sessions.load_priorities(path) == {"def": "P2"}


def test_priorities_reject_bad_values_and_ignore_junk_on_disk(tmp_path):
    path = tmp_path / "prio.json"
    path.write_text('{"a": "P9", "b": "P1"}')
    assert claude_sessions.load_priorities(path) == {"b": "P1"}
    path.write_text('["junk"]')
    assert claude_sessions.load_priorities(path) == {}
    try:
        claude_sessions.set_priority("x", "P3", path)
    except ValueError:
        return
    raise AssertionError("accepted a bad priority")


def test_workspace_priority_comes_from_the_workspace(tmp_path, monkeypatch):
    _fake_system(monkeypatch, {1: ("1", "/dev/pts/1")})
    _write_session(tmp_path, 1, "s", 1000)
    workspaces = [{**WORKSPACES[0], "priority": "P1"}]
    assert claude_sessions.list_sessions(workspaces, tmp_path)[0]["workspace_priority"] == "P1"


def test_background_session_maps_to_the_workspace_working_in_its_folder(tmp_path, monkeypatch):
    _fake_system(monkeypatch, {1: ("1", "/dev/pts/40")})  # its pty is not a tmux pane
    _write_session(tmp_path, 1, "bg", 1000, kind="bg", cwd="/code/img")
    row = claude_sessions.list_sessions(WORKSPACES, tmp_path)[0]
    assert row["background"] is True
    assert (row["workspace"], row["workspace_tmux"], row["tmux_window"]) == ("imgbench", "img-tmux", None)


def test_background_session_falls_back_to_the_workspace_project_folder(tmp_path, monkeypatch):
    _fake_system(monkeypatch, {1: ("1", "/dev/pts/40")})
    _write_session(tmp_path, 1, "bg", 1000, kind="bg", cwd="/code/proj/sub")
    workspaces = [{**WORKSPACES[0], "project": "/code/proj"}]
    assert claude_sessions.list_sessions(workspaces, tmp_path)[0]["workspace"] == "imgbench"
    _write_session(tmp_path, 1, "bg", 1000, kind="bg", cwd="/elsewhere")
    assert claude_sessions.list_sessions(workspaces, tmp_path)[0]["workspace"] is None


def test_terminal_session_outside_tmux_is_not_treated_as_background(tmp_path, monkeypatch):
    _fake_system(monkeypatch, {1: ("1", "/dev/pts/40")})
    _write_session(tmp_path, 1, "s", 1000, kind="interactive", cwd="/code/img")
    row = claude_sessions.list_sessions(WORKSPACES, tmp_path)[0]
    assert row["background"] is False and row["workspace"] is None


class _Run:
    def __init__(self, outputs):
        self.outputs, self.calls = outputs, []

    def __call__(self, args, **_):
        self.calls.append(args)
        stdout = self.outputs.pop(0)
        return type("R", (), {"stdout": stdout, "returncode": 0})()


JOB = "09e690d8"


def test_attach_reuses_an_existing_attach_window(monkeypatch):
    run = _Run([f"@3\tbash\n@9\t/bin/claude attach {JOB}; exec bash\n"])
    monkeypatch.setattr(claude_sessions.shutil, "which", lambda _: "/usr/bin/tmux")
    monkeypatch.setattr(claude_sessions.subprocess, "run", run)
    assert claude_sessions.attach_window(JOB, "lab", "/tmp", "bg: x") == "@9"
    assert len(run.calls) == 1  # no new window


def test_attach_opens_a_background_window_otherwise(monkeypatch):
    run = _Run(["@3\tbash\n", "@12\n"])
    monkeypatch.setattr(claude_sessions.shutil, "which", lambda _: "/usr/bin/tmux")
    monkeypatch.setattr(claude_sessions.subprocess, "run", run)
    assert claude_sessions.attach_window(JOB, "lab", "/tmp", "bg: x") == "@12"
    new_window = run.calls[1]
    assert new_window[:3] == ["tmux", "new-window", "-d"]  # never steals the screen
    assert new_window[new_window.index("-t") + 1] == "=lab:"
    assert f"attach {JOB}; exec" in new_window[-1]


def test_attach_rejects_bad_job_ids(monkeypatch):
    monkeypatch.setattr(claude_sessions.subprocess, "run", lambda *a, **k: 1 / 0)
    assert claude_sessions.attach_window("x; rm -rf ~", "lab", "/tmp", "bg") is None


def test_spare_processes_are_hidden(tmp_path, monkeypatch):
    _fake_system(monkeypatch, {1: ("1", "/dev/pts/40")})
    _write_session(tmp_path, 1, "spare", 1000, kind="bg", spare=True, cwd="/code/img")
    assert claude_sessions.list_sessions(WORKSPACES, tmp_path) == []


def test_blocked_background_job_needs_you(tmp_path, monkeypatch):
    _fake_system(monkeypatch, {1: ("1", "/dev/pts/40")})
    _write_session(tmp_path, 1, "6b5c0af4-0000-0000-0000-000000000000", 1000,
                   kind="bg", jobId="6b5c0af4", cwd="/code/img", status="idle")
    job = tmp_path / "jobs" / "6b5c0af4"
    job.mkdir(parents=True)
    (job / "state.json").write_text(json.dumps({
        "tempo": "blocked", "needs": "send a prompt to start", "intent": "Why is all empty?",
    }))
    row = claude_sessions.list_sessions(WORKSPACES, tmp_path)[0]
    assert (row["status"], row["waiting_for"], row["job_id"]) == ("waiting", "send a prompt to start", "6b5c0af4")
    assert row["title"] == "Why is all empty?"  # no transcript title yet: the job's intent
