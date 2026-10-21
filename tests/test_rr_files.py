import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

_loader = SourceFileLoader("rr_files", str(Path(__file__).resolve().parents[1] / "rr-files"))
_spec = importlib.util.spec_from_loader("rr_files", _loader)
rr_files = importlib.util.module_from_spec(_spec)
_loader.exec_module(rr_files)


def test_root_path(tmp_path):
    assert rr_files.files_tab_path("s1", tmp_path, tmp_path) == "/workspaces/s1/files"


def test_subdir_path(tmp_path):
    sub = tmp_path / "a b" / "c"
    assert rr_files.files_tab_path("s 1", sub, tmp_path) == "/workspaces/s%201/files?path=a%20b/c"


def test_outside_project_falls_back_to_root(tmp_path):
    assert rr_files.files_tab_path("s1", Path("/etc"), tmp_path) == "/workspaces/s1/files"


def test_no_project(tmp_path):
    assert rr_files.files_tab_path("s1", tmp_path, None) == "/workspaces/s1/files"
