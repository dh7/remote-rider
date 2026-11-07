import json
import threading

import pytest
from fastapi.testclient import TestClient

import claude_sessions
import queue_store as q


@pytest.fixture
def qf(tmp_path, monkeypatch):
    path = tmp_path / "queue.json"
    monkeypatch.setattr(q, "QUEUE_FILE", path)
    return path


ME = {"pid": 4242, "proc_start": "777", "session_id": "s-1"}


def _add(prompt, **kw):
    return q.add({"prompt": prompt, "machine": kw.pop("machine", "gx10"), **kw})


# --- ordering and matching -----------------------------------------------------

def test_next_takes_most_urgent_then_oldest(qf):
    _add("old none")
    _add("p2", priority="P2")
    first_p0 = _add("p0 a", priority="P0")
    _add("p0 b", priority="P0")
    assert q.take_next("gx10", ME)["id"] == first_p0["id"]
    assert q.take_next("gx10", ME)["prompt"] == "p0 b"
    assert q.take_next("gx10", ME)["prompt"] == "p2"
    assert q.take_next("gx10", ME)["prompt"] == "old none"
    assert q.take_next("gx10", ME) is None


def test_machine_only_task_goes_to_any_agent_on_that_machine(qf):
    _add("new model: flux 3")
    assert q.take_next("netochka", ME) is None
    task = q.take_next("gx10", ME, workspace="gx10-gx10-1", folder="/home/dh/code/capsules")
    assert task["prompt"] == "new model: flux 3"


def test_workspace_is_a_hard_filter(qf):
    _add("ws task", workspace="img")
    assert q.take_next("gx10", ME) is None
    assert q.take_next("gx10", ME, workspace="other") is None
    assert q.take_next("gx10", ME, workspace="img")["prompt"] == "ws task"


def test_folder_matches_itself_and_below_only(qf):
    _add("folder task", folder="/code/img/")
    assert q.take_next("gx10", ME, folder="/code/imgx") is None   # prefix, not a subfolder
    assert q.take_next("gx10", ME, folder="/code") is None
    assert q.take_next("gx10", ME) is None
    assert q.take_next("gx10", ME, folder="/code/img/capsules")["prompt"] == "folder task"


def test_take_is_exclusive_under_concurrency(qf):
    for i in range(5):
        _add(f"t{i}")
    got, lock = [], threading.Lock()

    def worker():
        while (task := q.take_next("gx10", ME)) is not None:
            with lock:
                got.append(task["id"])

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(got) == 5 and len(set(got)) == 5


# --- lifecycle -----------------------------------------------------------------

def test_done_and_release(qf):
    _add("a")
    task = q.take_next("gx10", ME)
    assert task["taken_by"]["pid"] == 4242 and task["lease_until"] is None
    released = q.release(task["id"], "too big for me")
    assert released["status"] == "waiting" and released["note"] == "too big for me"
    again = q.take_next("gx10", ME)
    assert again["id"] == task["id"]
    done = q.finish(again["id"], "capsule PR #12")
    assert done["status"] == "done" and done["note"] == "capsule PR #12"
    with pytest.raises(q.QueueError):
        q.finish(again["id"])


def test_a_task_requeued_three_times_is_flagged_stuck(qf):
    _add("cursed")
    for _ in range(q.MAX_REQUEUES):
        q.release(q.take_next("gx10", ME)["id"])
    task = q.list_tasks()[0]
    assert task["stuck"] and task["status"] == "waiting"
    assert q.take_next("gx10", ME) is None          # not handed out any more
    q.update(task["id"], {"stuck": False})          # dh7 unsticks it from the view
    assert q.take_next("gx10", ME)["id"] == task["id"]


def test_only_waiting_tasks_are_editable_and_delete_cancels(qf):
    task = _add("x")
    assert q.update(task["id"], {"priority": "P0", "prompt": "y"})["priority"] == "P0"
    q.take_next("gx10", ME)
    with pytest.raises(q.QueueError) as err:
        q.update(task["id"], {"prompt": "z"})
    assert err.value.status == 409
    assert q.cancel(task["id"])["status"] == "cancelled"
    assert q.list_tasks(status="cancelled")[0]["id"] == task["id"]


def test_add_requires_prompt_and_machine(qf):
    with pytest.raises(q.QueueError):
        q.add({"prompt": " ", "machine": "gx10"})
    with pytest.raises(q.QueueError):
        q.add({"prompt": "x", "machine": ""})


def test_old_finished_tasks_are_pruned(qf, monkeypatch):
    task = _add("x")
    q.finish(task["id"])
    monkeypatch.setattr(q, "_now", lambda: task["created_at"] + q.KEEP_FINISHED_S + 10)
    _add("y")  # any save prunes
    assert [t["prompt"] for t in q.list_tasks()] == ["y"]


# --- auto re-queue -------------------------------------------------------------

def test_reap_requeues_tasks_whose_claude_died(qf):
    _add("a")
    _add("b")
    a = q.take_next("gx10", {"pid": 1, "proc_start": "1"})
    b = q.take_next("gx10", {"pid": 2, "proc_start": "2"})
    asked = []

    def alive(machine, procs):
        asked.append((machine, procs))
        return [pid == 2 for pid, _ in procs]

    assert q.reap(alive) == 1
    assert asked == [("gx10", [(1, "1"), (2, "2")])]
    by_id = {t["id"]: t for t in q.list_tasks()}
    assert by_id[a["id"]]["status"] == "waiting" and by_id[a["id"]]["requeues"] == 1
    assert by_id[b["id"]]["status"] == "taken"


def test_reap_keeps_tasks_when_the_machine_is_unreachable(qf):
    _add("a")
    q.take_next("gx10", ME)
    assert q.reap(lambda machine, procs: None) == 0
    assert q.list_tasks()[0]["status"] == "taken"


def test_a_long_running_live_task_is_never_requeued(qf, monkeypatch):
    task = _add("train for 3 days")
    taken = q.take_next("gx10", ME)
    monkeypatch.setattr(q, "_now", lambda: taken["taken_at"] + 30 * 24 * 3600)
    assert q.reap(lambda machine, procs: [True]) == 0
    assert q.list_tasks()[0]["status"] == "taken"
    assert task["id"] == taken["id"]


def test_takers_without_a_pid_hold_a_lease(qf, monkeypatch):
    _add("curl agent")
    taken = q.take_next("gx10", {})
    assert taken["lease_until"]
    assert q.reap(lambda *a: pytest.fail("no pid: must not ask")) == 0
    monkeypatch.setattr(q, "_now", lambda: taken["lease_until"] + 1)
    assert q.reap(lambda *a: [True]) == 1


# --- runtime helpers ----------------------------------------------------------

def test_procs_alive_checks_start_time(monkeypatch):
    import os
    me = os.getpid()
    start = open(f"/proc/{me}/stat").read().rsplit(")", 1)[1].split()[19]
    assert claude_sessions.procs_alive([(me, start), (me, "1"), (me, None), (999999999, None)]) == [
        True, False, True, False]


def test_list_dirs_stays_inside_home(tmp_path):
    (tmp_path / "code" / "img").mkdir(parents=True)
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "file.txt").write_text("x")
    top = claude_sessions.list_dirs(None, home=tmp_path)
    assert top["dirs"] == ["code"] and top["parent"] is None
    assert claude_sessions.list_dirs(str(tmp_path / "code"), home=tmp_path)["dirs"] == ["img"]
    assert claude_sessions.list_dirs("/etc", home=tmp_path)["path"] == str(tmp_path.resolve())


class _Run:
    def __init__(self, outputs):
        self.outputs, self.calls = outputs, []

    def __call__(self, args, **_):
        self.calls.append(args)
        return type("R", (), {"stdout": self.outputs.pop(0), "returncode": 0})()


def test_launch_window_is_byobu_f2_with_the_prompt_in_a_private_file(tmp_path, monkeypatch):
    run = _Run(["@31\n"])
    monkeypatch.setattr(claude_sessions.shutil, "which", lambda _: "/usr/bin/tmux")
    monkeypatch.setattr(claude_sessions.subprocess, "run", run)
    prompt = "Add model \"flux 3\"\n$(rm -rf ~) `x` 'y'"
    sid = "11111111-2222-3333-4444-555555555555"
    window = claude_sessions.launch_window("lab", str(tmp_path), prompt, sid, "✦ add", prompt_dir=tmp_path / "p")
    assert window == "@31"
    cmd = run.calls[0]
    assert cmd[:3] == ["tmux", "new-window", "-d"]          # never steals the screen
    assert cmd[cmd.index("-t") + 1] == "=lab:" and cmd[cmd.index("-c") + 1] == str(tmp_path)
    shell = cmd[-1]
    assert prompt not in shell and "rm -rf" not in shell      # prompt never in the shell line
    assert f"--session-id {sid}" in shell and "--dangerously-skip-permissions" not in shell
    pf = tmp_path / "p" / f"{sid}.txt"
    assert pf.read_text() == prompt and oct(pf.stat().st_mode & 0o777) == "0o600"


def test_launch_window_autonomous_flag(tmp_path, monkeypatch):
    run = _Run(["@2\n"])
    monkeypatch.setattr(claude_sessions.shutil, "which", lambda _: "/usr/bin/tmux")
    monkeypatch.setattr(claude_sessions.subprocess, "run", run)
    claude_sessions.launch_window("lab", "/nope", "p", "11111111-2222-3333-4444-555555555555", "x",
                                  autonomous=True, prompt_dir=tmp_path)
    assert "--dangerously-skip-permissions" in run.calls[0][-1]


# --- API -----------------------------------------------------------------------

@pytest.fixture
def api(qf, monkeypatch):
    import main
    monkeypatch.setattr(main, "servers", lambda: [
        {"name": "netochka", "ip": "100.119.43.10"}, {"name": "gx10", "ip": "100.118.187.64"}])
    monkeypatch.setattr(main, "_queue_reaped_at", 0.0)
    return TestClient(main.app)


def test_queue_api_round_trip(api):
    r = api.post("/queue", json={"prompt": "new model flux 3", "machine": "100.118.187.64", "priority": "P1"})
    assert r.status_code == 200 and r.json()["task"]["machine"] == "gx10"   # IP -> machine name
    assert api.post("/queue/next", json={"machine": "netochka"}).status_code == 204
    r = api.post("/queue/next", json={"machine": "gx10", "folder": "/x", "pid": 1, "proc_start": "1"})
    task = r.json()["task"]
    assert task["status"] == "taken"
    assert api.patch(f"/queue/{task['id']}", json={"prompt": "x"}).status_code == 409
    assert api.post(f"/queue/{task['id']}/done", json={"note": "ok"}).json()["task"]["status"] == "done"
    assert api.get("/queue?status=waiting,taken").json()["tasks"] == []
    assert api.post("/queue/nope/done", json={}).status_code == 404


def test_queue_list_requeues_dead_takers(api, monkeypatch):
    import main
    api.post("/queue", json={"prompt": "a", "machine": "gx10"})
    api.post("/queue/next", json={"machine": "gx10", "pid": 5, "proc_start": "9"})
    monkeypatch.setattr(main, "_queue_alive", lambda machine, procs: [False])
    assert api.get("/queue").json()["tasks"][0]["status"] == "taken"   # checked at most every 30 s
    monkeypatch.setattr(main, "_queue_reaped_at", 0.0)
    assert api.get("/queue").json()["tasks"][0]["status"] == "waiting"


def test_patch_can_clear_priority(api):
    task = api.post("/queue", json={"prompt": "a", "machine": "gx10", "priority": "P0"}).json()["task"]
    assert api.patch(f"/queue/{task['id']}", json={"priority": None}).json()["task"]["priority"] is None
    assert api.patch(f"/queue/{task['id']}", json={"note": "x"}).json()["task"]["priority"] is None


def test_autonomous_start_only_from_the_page(api, monkeypatch):
    import main
    monkeypatch.setattr(main, "_control_workspace", lambda name: pytest.fail("must refuse first"))
    r = api.post("/claude/start", json={"workspace": "lab", "prompt": "x", "autonomous": True})
    assert r.status_code == 403


def test_start_launches_locally_and_sets_priority(api, monkeypatch):
    import main
    calls = {}
    monkeypatch.setattr(main, "_control_workspace", lambda name: {
        "name": "lab", "machine": {"name": "netochka", "host": "100.119.43.10"},
        "project": "/code/lab", "tabs": [{"label": "Terminal", "service": "terminal", "path": "/?arg=lab-tmux"}]})
    monkeypatch.setattr(main, "_is_local_host", lambda host: True)
    monkeypatch.setattr(main, "_ensure_tmux_session", lambda s, cwd=None: calls.setdefault("ensure", s))

    def launch(tmux, folder, prompt, sid, label, autonomous, name=None):
        calls["launch"] = (tmux, folder, prompt, autonomous)
        calls["label"] = label
        return "@44"
    monkeypatch.setattr(main, "_claude_launch_window", launch)
    prio = {}
    monkeypatch.setattr(main, "_set_claude_priority", lambda sid, p: prio.__setitem__(sid, p))
    r = api.post("/claude/start", json={"workspace": "lab", "prompt": "do it", "priority": "P0",
                                        "autonomous": True}, headers={"sec-fetch-site": "same-origin"})
    body = r.json()
    assert r.status_code == 200 and body["window"] == "@44" and body["folder"] == "/code/lab"
    assert calls["launch"] == ("lab-tmux", "/code/lab", "do it", True) and calls["ensure"] == "lab-tmux"
    assert calls["label"] == "✦ do it"   # no name: the tab shows the prompt start
    assert prio == {body["session_id"]: "P0"}


def test_start_with_a_name_names_the_session_and_the_tab(api, monkeypatch):
    import main
    seen = {}
    monkeypatch.setattr(main, "_control_workspace", lambda name: {
        "name": "lab", "machine": {"name": "netochka", "host": "100.119.43.10"}, "project": "/code/lab", "tabs": []})
    monkeypatch.setattr(main, "_is_local_host", lambda host: True)
    monkeypatch.setattr(main, "_ensure_tmux_session", lambda s, cwd=None: None)

    def launch(tmux, folder, prompt, sid, label, autonomous, name=None):
        seen.update(label=label, name=name)
        return "@45"
    monkeypatch.setattr(main, "_claude_launch_window", launch)
    r = api.post("/claude/start", json={"workspace": "lab", "prompt": "do it", "name": "  flux2  capsule "})
    assert r.status_code == 200 and seen == {"label": "flux2 capsule", "name": "flux2 capsule"}


def test_launch_window_passes_the_session_name(tmp_path, monkeypatch):
    run = _Run(["@3\n"])
    monkeypatch.setattr(claude_sessions.shutil, "which", lambda _: "/usr/bin/tmux")
    monkeypatch.setattr(claude_sessions.subprocess, "run", run)
    claude_sessions.launch_window("lab", "/nope", "p", "11111111-2222-3333-4444-555555555555", "flux2",
                                  prompt_dir=tmp_path, name="flux2 capsule")
    cmd = run.calls[0]
    assert cmd[cmd.index("-n") + 1] == "flux2" and "--name 'flux2 capsule'" in cmd[-1]


def test_queued_task_keeps_its_name(api):
    task = api.post("/queue", json={"prompt": "p", "name": "flux2", "machine": "gx10"}).json()["task"]
    assert task["name"] == "flux2"
    assert api.patch(f"/queue/{task['id']}", json={"name": "flux3"}).json()["task"]["name"] == "flux3"


def test_machine_defaults_to_the_caller_by_tailnet_ip(api):
    import main
    gx10 = TestClient(main.app, client=("100.118.187.64", 5000))
    task = gx10.post("/queue", json={"prompt": "from gx10"}).json()["task"]
    assert task["machine"] == "gx10"
    assert gx10.post("/queue/next", json={}).json()["task"]["id"] == task["id"]
    stranger = TestClient(main.app, client=("10.9.9.9", 5000))
    assert stranger.post("/queue", json={"prompt": "who am I"}).status_code == 400
