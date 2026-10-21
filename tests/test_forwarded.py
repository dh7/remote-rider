from fastapi import FastAPI
from fastapi.testclient import TestClient

import forwarded


def _app():
    app = FastAPI()
    app.add_middleware(forwarded.ForwardedMiddleware)

    @app.get("/p")
    def p():  # sync -> runs in the threadpool; the contextvar must propagate
        return {"prefix": forwarded.prefix(), "via": forwarded.via_gateway()}

    return app


def test_prefix_from_header():
    r = TestClient(_app()).get("/p", headers={"X-Forwarded-Prefix": "/m/100.118.187.64/7000", "X-RR-Via-Gateway": "1"})
    assert r.json() == {"prefix": "/m/100.118.187.64/7000", "via": True}


def test_no_header_means_no_prefix():
    assert TestClient(_app()).get("/p").json() == {"prefix": "", "via": False}


def test_invalid_prefix_is_ignored():
    for bad in ['/m/x"><script>/1', "/evil", "/m/1.2.3.4/99999999", "//attacker.com", "/m/1.2.3.4/7000/extra"]:
        r = TestClient(_app()).get("/p", headers={"X-Forwarded-Prefix": bad})
        assert r.json()["prefix"] == "", bad


def test_file_base_uses_prefix(monkeypatch):
    import workspace_builtins as wb
    monkeypatch.setattr(forwarded, "prefix", lambda: "/m/100.118.187.64/7000")
    assert wb._file_base("a b") == "/m/100.118.187.64/7000/workspaces/a%20b/files"


def test_hub_control_context_reports_gateway(monkeypatch):
    monkeypatch.setenv("RUN_MODE", "control")
    monkeypatch.setenv("PUBLIC_HOST", "100.119.43.10")
    import main
    c = TestClient(main.app)
    d = c.get("/control/context", headers={"X-RR-Via-Gateway": "1"}).json()
    assert d["via_gateway"] is True and d["hub_host"] == "100.119.43.10"
    assert c.get("/control/context").json()["via_gateway"] is False


def test_logs_index_escapes_and_is_relative():
    import logs
    r = TestClient(logs.app).get("/", params={"path": '"><script>alert(1)</script>'})
    assert "<script>alert(1)" not in r.text
    assert "EventSource('stream?path='" in r.text
