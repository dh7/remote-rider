from storage import _default_templates, _normalize_control_session


def _session(tabs):
    return {"name": "gx10-proj", "machine": {"name": "gx10", "host": "100.118.187.64"}, "tabs": tabs}


def _files(s):
    return [t for t in s["tabs"] if t["label"] == "Files"]


def test_legacy_files_tab_becomes_builtin():
    s = _normalize_control_session(_session([
        {"id": "t1", "label": "Terminal", "service": "terminal", "port": 7681},
        {"id": "t2", "label": "Files", "service": "files", "port": 8080, "path": "/files"},
    ]))
    assert _files(s) == [{"id": "tab-files", "label": "Files", "service": "builtin", "port": 7000,
                          "path": "/workspaces/gx10-proj/files", "protocol": "http"}]
    assert [t["label"] for t in s["tabs"]] == ["Terminal", "Files"]


def test_legacy_files_tab_deduped_against_builtin():
    builtin = {"id": "tab-files", "label": "Files", "service": "builtin", "port": 7000,
               "path": "/workspaces/gx10-proj/files", "protocol": "http"}
    s = _normalize_control_session(_session([builtin, {"label": "Files", "port": 8080, "path": "/files"}]))
    assert _files(s) == [builtin]


def test_migration_is_idempotent():
    once = _normalize_control_session(_session([{"label": "Files", "port": 8080, "path": "/files"}]))
    assert _normalize_control_session(once) == once


def test_templates_have_no_fileserver():
    for t in _default_templates():
        assert all(p.get("port") != 8080 for p in t["panels"])


def test_workspace_priority_is_kept_and_validated():
    from storage import _normalize_control_session
    base = {"name": "w", "machine": {"host": "1.2.3.4"}, "tabs": []}
    assert _normalize_control_session({**base, "priority": "P0"})["priority"] == "P0"
    assert "priority" not in _normalize_control_session({**base, "priority": "urgent"})
    assert "priority" not in _normalize_control_session(base)


def test_save_without_priority_field_keeps_the_stored_one():
    from storage import _carry_over_priorities, _normalize_control_session
    current = [{"name": "a", "priority": "P0"}, {"name": "b", "priority": "P1"}, {"name": "c", "priority": "P2"}]
    raw = [
        {"name": "a", "machine": {"host": "h"}},                    # old client: field missing -> kept
        {"name": "b", "machine": {"host": "h"}, "priority": None},  # explicit clear -> cleared
        {"name": "c", "machine": {"host": "h"}, "priority": "P0"},  # explicit change -> changed
    ]
    normalized = [_normalize_control_session(r) for r in raw]
    _carry_over_priorities(raw, normalized, current)
    assert [s.get("priority") for s in normalized] == ["P0", None, "P0"]
