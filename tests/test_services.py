import services


def test_snapshot_has_no_fileserver(monkeypatch):
    rows = [
        {"kind": "fileserver", "name": "files-8081", "port": 8081, "pid": 999999},
        {"kind": "service", "name": "app", "port": 8999, "label": "App"},
    ]
    saved = {}
    monkeypatch.setattr(services, "_load_service_registry", lambda: list(rows))
    monkeypatch.setattr(services, "_save_service_registry", lambda r: saved.setdefault("rows", r))
    snap = services._local_services_snapshot()
    names = {s["name"] for s in snap["services"]}
    kinds = {s.get("kind") for s in snap["services"]}
    assert "files" not in names
    assert "fileserver" not in kinds
    assert all(r.get("kind") != "fileserver" for r in saved["rows"])


def test_files_start_helpers_removed():
    assert not hasattr(services, "_start_files_service_local")
