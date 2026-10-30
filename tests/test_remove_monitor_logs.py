from pathlib import Path

from services import _local_services_snapshot
from storage import _default_templates, _normalize_control_session

ROOT = Path(__file__).resolve().parent.parent


def _session(tabs):
    return {"name": "gx10-proj", "machine": {"name": "gx10", "host": "100.118.187.64"}, "tabs": tabs}


def test_monitor_and_logs_tabs_are_dropped():
    s = _normalize_control_session(_session([
        {"id": "t1", "label": "Terminal", "service": "terminal", "port": 7681},
        {"id": "tab-monitor", "label": "Monitor", "service": "monitor", "port": 8001},
        {"id": "tab-logs", "label": "Logs", "service": "logs", "port": 8002},
        {"label": "Monitor", "port": 8001},
        {"label": "Logs", "port": 8002, "path": "/"},
    ]))
    assert [t["label"] for t in s["tabs"]] == ["Terminal"]


def test_unrelated_tabs_on_those_ports_are_kept():
    s = _normalize_control_session(_session([
        {"label": "gx10 monitor", "port": 8999},
        {"label": "Grafana", "port": 8001},
        {"label": "Logs", "port": 9100},
    ]))
    assert [t["label"] for t in s["tabs"]] == ["gx10 monitor", "Grafana", "Logs"]


def test_templates_have_no_monitor_or_logs():
    for t in _default_templates():
        assert all(p["label"] not in ("Monitor", "Logs") for p in t["panels"])


def test_services_snapshot_has_no_monitor_or_logs():
    kinds = {row.get("kind") or row.get("name") for row in _local_services_snapshot()["services"]}
    assert not kinds & {"monitor", "logs"}


def test_monitor_and_logs_apps_are_gone():
    assert not (ROOT / "monitor.py").exists()
    assert not (ROOT / "logs.py").exists()
