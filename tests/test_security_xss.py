"""Values reflected into inline <script> blocks must not break out (public origin = full tailnet power)."""
import httpx
import pytest
from fastapi.testclient import TestClient

PAYLOAD = "</script><svg onload=alert(1)>/../.."


@pytest.fixture
def client(tmp_path, monkeypatch):
    import main
    import workspace_builtins as wb
    monkeypatch.setattr(wb, "_project_for", lambda s: tmp_path)
    return TestClient(main.app), tmp_path


def test_upload_form_path_cannot_close_script(client):
    c, _ = client
    r = c.get("/workspaces/s/files/upload-form", params={"path": PAYLOAD})
    assert r.status_code == 200
    assert "</script><svg" not in r.text
    assert "<svg onload" not in r.text


def test_editor_filename_cannot_close_script(client):
    c, root = client
    d = root / "x<"
    d.mkdir()
    (d / "script><img src=x onerror=alert(1)>").write_text("hi")
    r = c.get("/workspaces/s/files/edit", params={"path": "x</script><img src=x onerror=alert(1)>"})
    assert r.status_code == 200
    assert "</script><img" not in r.text
    assert "<img src=x onerror" not in r.text.replace("&lt;img", "")


def test_js_helper_roundtrips():
    import json
    import workspace_builtins as wb
    for v in [PAYLOAD, "<!--", "a&b", " ", True, "plain/path.txt"]:
        assert json.loads(wb._js(v)) == v
        assert "<" not in wb._js(v) and ">" not in wb._js(v)


def test_gateway_drops_backend_cors_headers():
    from tests.test_review_fixes import HOST, _gw
    from tests.jwt_helpers import make_token

    def h(request):
        return httpx.Response(200, headers={"access-control-allow-origin": "https://evil.example",
                                            "access-control-allow-credentials": "true"}, stream=httpx.ByteStream(b"x"))
    r = _gw(h).get(f"/m/{HOST}/8999/", headers={"Cf-Access-Jwt-Assertion": make_token()})
    assert r.status_code == 200
    assert "access-control-allow-origin" not in r.headers
    assert "access-control-allow-credentials" not in r.headers
