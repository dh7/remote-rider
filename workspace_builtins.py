from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from importlib.machinery import SourceFileLoader
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote
from urllib.request import urlopen

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from config import HERE, SESSIONS_LOCK, SERVICE_LOCK
from storage import _load_normalized_control_sessions, _load_service_registry


router = APIRouter(prefix="/workspaces")


def _load_script(name: str):
    path = HERE / name
    loader = SourceFileLoader(name.replace("-", "_"), str(path))
    spec = importlib.util.spec_from_loader(name.replace("-", "_"), loader)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_rr_notes = _load_script("rr-notes")
_rr_skill = _load_script("rr-skill")


def _workspace(session_name: str) -> dict[str, Any]:
    with SESSIONS_LOCK:
        sessions = _load_normalized_control_sessions()
    session = next((row for row in sessions if row.get("name") == session_name), None)
    if session:
        return session

    control_url = os.getenv("RR_CONTROL", "").rstrip("/")
    if control_url:
        try:
            with urlopen(f"{control_url}/sessions/{quote(session_name, safe='')}", timeout=3) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            remote_session = payload.get("session") if isinstance(payload, dict) else None
            if isinstance(remote_session, dict):
                return remote_session
        except Exception:
            pass

    raise HTTPException(status_code=404, detail="workspace not found")


def _project_for(session_name: str) -> Path:
    session = _workspace(session_name)
    project = str(session.get("project", "")).strip()
    if project:
        return Path(project).expanduser().resolve()

    with SERVICE_LOCK:
        rows = _load_service_registry()
    for row in rows:
        if row.get("session_name") == session_name and row.get("project"):
            return Path(str(row["project"])).expanduser().resolve()
    for row in rows:
        if row.get("session_name") == session_name and row.get("root_path"):
            return Path(str(row["root_path"])).expanduser().resolve()

    candidate = Path.home() / "code" / session_name
    if candidate.exists():
        return candidate.resolve()
    code_root = Path.home() / "code"
    if code_root.exists():
        for rr_path in code_root.glob("*/.rr"):
            try:
                data = json.loads(rr_path.read_text())
            except Exception:
                continue
            if isinstance(data, dict) and data.get("session") == session_name:
                return rr_path.parent.resolve()
    raise HTTPException(status_code=400, detail="workspace project path is not configured")


def _safe(root: Path, rel_path: str) -> Path:
    target = (root / unquote(rel_path or "")).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="path escapes workspace root") from exc
    return target


def _q(path: str) -> str:
    return quote(path, safe="/")


def _rel(root: Path, path: Path) -> str:
    return str(path.resolve().relative_to(root.resolve()))


def _git_status_map(directory: Path) -> dict[str, tuple[str, str]]:
    try:
        probe = subprocess.run(
            ["git", "-C", str(directory), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode != 0:
            return {}
        out = subprocess.run(
            ["git", "-C", str(directory), "status", "--porcelain", "--untracked-files=all"],
            capture_output=True,
            text=True,
            check=False,
        )
        if out.returncode != 0:
            return {}
    except Exception:
        return {}

    statuses: dict[str, tuple[str, str]] = {}
    for line in out.stdout.splitlines():
        if len(line) < 4:
            continue
        code = line[:2]
        path_part = line[3:].split(" -> ", 1)[-1]
        first = Path(path_part).parts[0] if Path(path_part).parts else ""
        if not first:
            continue
        if "D" in code:
            statuses[first] = ("D", "git-deleted")
        elif "A" in code:
            statuses[first] = ("A", "git-added")
        elif "M" in code:
            statuses[first] = ("M", "git-modified")
        elif code == "??":
            statuses[first] = ("?", "git-untracked")
        else:
            statuses[first] = ("*", "git-modified")
    return statuses


FILE_STYLE = """
<style>
* { box-sizing: border-box; }
body { margin: 0; padding: 1rem; font-family: monospace; background: #111; color: #eee; min-height: 100vh; }
a { color: #7af; text-decoration: none; display: block; padding: 2px 0; }
a:hover { color: #fff; }
.topbar { display: flex; align-items: center; justify-content: space-between; gap: .75rem; margin-bottom: .5rem; }
.crumbs { white-space: nowrap; overflow-x: auto; }
.crumbs a { display: inline; padding: 0; }
.btn { background: #2f446f; color: #fff; border: none; display: inline-block; padding: .42rem .78rem; cursor: pointer; font-family: monospace; font-size: .84rem; text-decoration: none; }
.btn.ghost { background: #1d1f26; border: 1px solid #4b5565; }
.btn.danger { background: #782f35; }
.dir { color: #fa0; }
.file { color: #fff; }
.entry-row { display: flex; align-items: center; gap: .5rem; }
.git-badge { display: inline-block; min-width: 1.4rem; text-align: center; padding: .02rem .25rem; border-radius: 3px; font-size: .72rem; border: 1px solid #444; }
.git-modified { background: #4f3a1c; border-color: #87611e; color: #ffd992; }
.git-added { background: #1e4d2a; border-color: #2f7d43; color: #abf5bf; }
.git-deleted { background: #5a1f24; border-color: #8c2f37; color: #ffabb2; }
.git-untracked { background: #243652; border-color: #3a5d8f; color: #b8d7ff; }
textarea { width: 100%; min-height: 72vh; background: #11131a; color: #eee; border: 1px solid #394050; font-family: monospace; padding: .5rem; }
input { background: #11131a; border: 1px solid #3e4455; color: #eee; font-family: monospace; padding: .42rem .5rem; min-width: 16rem; }
</style>
"""


def _file_base(session_name: str) -> str:
    return f"/workspaces/{quote(session_name, safe='')}/files"


def _breadcrumbs(session_name: str, path: str) -> str:
    base = _file_base(session_name)
    parts = Path(path).parts if path else []
    crumbs = [f'<a href="{base}">home</a>']
    acc = Path()
    for part in parts:
        acc /= part
        crumbs.append(f'/ <a href="{base}?path={_q(str(acc))}">{escape(part)}</a>')
    return f'<div class="crumbs">{" ".join(crumbs)}</div>'


@router.get("/{session_name}/notes", response_class=HTMLResponse)
def notes_ui(session_name: str) -> str:
    _workspace(session_name)
    return _rr_notes.HTML.replace(
        "const resp = await fetch(path, opts);",
        "const resp = await fetch(location.pathname.replace(/\\/$/, '') + path, opts);",
    )


@router.get("/{session_name}/notes/api/state")
def notes_state(session_name: str) -> dict[str, Any]:
    project = _project_for(session_name)
    return {
        "project": str(project),
        "files": {kind: _rr_notes.read_file(project, kind) for kind in _rr_notes.FILES},
        "git": _rr_notes.git_info(project),
    }


@router.post("/{session_name}/notes/api/create")
async def notes_create(session_name: str, request: Request) -> dict[str, Any]:
    project = _project_for(session_name)
    body = await request.json()
    kind = str(body.get("kind", ""))
    if kind not in {"todo", "status"}:
        raise HTTPException(status_code=400, detail="only TODO.md and STATUS.md can be created")
    path = _rr_notes.file_for(project, kind)
    if path is None:
        raise HTTPException(status_code=400, detail="unknown file kind")
    if not path.exists():
        path.write_text(_rr_notes.default_content(kind))
    return {"status": "ok", "file": _rr_notes.read_file(project, kind)}


@router.post("/{session_name}/notes/api/save")
async def notes_save(session_name: str, request: Request) -> dict[str, Any]:
    project = _project_for(session_name)
    body = await request.json()
    kind = str(body.get("kind", ""))
    if kind not in _rr_notes.FILES:
        raise HTTPException(status_code=400, detail="unknown file kind")
    path = _rr_notes.file_for(project, kind)
    if path is None:
        raise HTTPException(status_code=400, detail="file target not found")
    if kind == "agent" and not path.exists():
        raise HTTPException(status_code=400, detail="agent file does not exist")
    path.write_text(str(body.get("content", "")))
    return {"status": "ok", "file": _rr_notes.read_file(project, kind)}


@router.get("/{session_name}/skills", response_class=HTMLResponse)
def skills_ui(session_name: str) -> str:
    _workspace(session_name)
    return _rr_skill.HTML.replace(
        "const resp = await fetch(path, opts);",
        "const resp = await fetch(location.pathname.replace(/\\/$/, '') + path, opts);",
    )


@router.get("/{session_name}/skills/api/state")
def skills_state(session_name: str) -> JSONResponse:
    project = _project_for(session_name)
    dh7skill = _rr_skill.find_dh7skill()
    try:
        return JSONResponse({
            "project": str(project),
            "skills": _rr_skill.run_json([dh7skill, "list", "--json"]),
            "external_skills": _rr_skill.run_json([dh7skill, "external-list", "--project", str(project), "--json"]),
            "installed": _rr_skill.run_json([dh7skill, "installed", "--project", str(project), "--json"]),
            "env": _rr_skill.run_json([dh7skill, "check-env", "--project", str(project), "--json"]),
        })
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=500)


@router.post("/{session_name}/skills/api/{action}")
async def skills_action(session_name: str, action: str, request: Request) -> JSONResponse:
    project = _project_for(session_name)
    dh7skill = _rr_skill.find_dh7skill()
    body = await request.json()
    name = str(body.get("name", "")).strip()
    passphrase = str(body.get("passphrase", ""))
    try:
        if action == "add":
            if not name:
                raise RuntimeError("name is required")
            result = _rr_skill.run_json([dh7skill, "add", name, "--project", str(project), "--json"], passphrase)
        elif action == "add-external":
            repo = str(body.get("repo", "")).strip()
            if not repo or not name:
                raise RuntimeError("repo and name are required")
            result = _rr_skill.run_json([dh7skill, "add-external", repo, "--skill", name, "--project", str(project), "--json"])
        elif action == "remove":
            if not name:
                raise RuntimeError("name is required")
            result = _rr_skill.run_json([dh7skill, "remove", name, "--project", str(project), "--json"], passphrase)
        elif action == "sync":
            result = _rr_skill.run_json([dh7skill, "sync", "--project", str(project), "--json"], passphrase)
        else:
            raise HTTPException(status_code=404, detail="not found")
        return JSONResponse(result)
    except HTTPException:
        raise
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=500)


@router.get("/{session_name}/files", response_class=HTMLResponse)
def files_browse(session_name: str, path: str = "") -> HTMLResponse:
    root = _project_for(session_name)
    target = _safe(root, path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="not found")
    if not target.is_dir():
        return RedirectResponse(url=f"{_file_base(session_name)}/edit?path={_q(_rel(root, target))}", status_code=302)

    base = _file_base(session_name)
    statuses = _git_status_map(target)
    entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    html = [
        "<html><head>",
        FILE_STYLE,
        "</head><body>",
        '<div class="topbar">',
        _breadcrumbs(session_name, path),
        f'<a class="btn" href="{base}/new-file?path={_q(path)}">Create File</a>',
        "</div><hr>",
    ]
    if path:
        parent = str(Path(path).parent) if Path(path).parent != Path(path) else ""
        html.append(f'<a class="dir" href="{base}?path={_q(parent)}">..</a>')
    for entry in entries:
        rel = _rel(root, entry)
        badge = ""
        if entry.name in statuses:
            label, css = statuses[entry.name]
            badge = f'<span class="git-badge {css}">{label}</span>'
        name = escape(entry.name)
        if entry.is_dir():
            html.append(f'<a class="entry-row dir" href="{base}?path={_q(rel)}">{badge}[dir] {name}</a>')
        else:
            html.append(f'<a class="entry-row file" href="{base}/edit?path={_q(rel)}">{badge}{name}</a>')
    html.append("</body></html>")
    return HTMLResponse("".join(html))


@router.get("/{session_name}/files/raw")
def files_raw(session_name: str, path: str) -> FileResponse:
    root = _project_for(session_name)
    target = _safe(root, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(target)


@router.get("/{session_name}/files/new-file", response_class=HTMLResponse)
def files_new_form(session_name: str, path: str = "") -> HTMLResponse:
    base = _file_base(session_name)
    form = f"""
    <html><head>{FILE_STYLE}</head><body>
      <div class="topbar">{_breadcrumbs(session_name, path)}<a class="btn ghost" href="{base}?path={_q(path)}">Back</a></div>
      <hr>
      <form method="post" action="{base}/create-file?path={_q(path)}" style="display:flex;gap:.5rem;align-items:center;flex-wrap:wrap">
        <input type="text" name="new_path" placeholder="new file path" autofocus>
        <button class="btn" type="submit">Create File</button>
      </form>
    </body></html>
    """
    return HTMLResponse(form)


@router.post("/{session_name}/files/create-file")
async def files_create(session_name: str, request: Request, path: str = "") -> RedirectResponse:
    root = _project_for(session_name)
    form = parse_qs((await request.body()).decode())
    new_path = str(form.get("new_path", [""])[0]).strip()
    if not new_path:
        raise HTTPException(status_code=400, detail="path is required")
    target = _safe(root, str(Path(path) / new_path))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch(exist_ok=True)
    return RedirectResponse(url=f"{_file_base(session_name)}/edit?path={_q(_rel(root, target))}", status_code=303)


@router.get("/{session_name}/files/edit", response_class=HTMLResponse)
def files_edit(session_name: str, path: str) -> HTMLResponse:
    root = _project_for(session_name)
    target = _safe(root, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="not found")
    rel = _rel(root, target)
    parent = str(Path(rel).parent)
    base = _file_base(session_name)
    content = escape(target.read_text(errors="replace"))
    html = f"""
    <html><head>{FILE_STYLE}</head><body>
      <form method="post" action="{base}/save?path={_q(rel)}">
        <div class="topbar">
          <a class="btn ghost" href="{base}?path={_q(parent)}">Back</a>
          <div>{escape(rel)}</div>
          <button class="btn" type="submit">Save</button>
        </div>
        <textarea name="content">{content}</textarea>
      </form>
    </body></html>
    """
    return HTMLResponse(html)


@router.post("/{session_name}/files/save")
async def files_save(session_name: str, request: Request, path: str) -> RedirectResponse:
    root = _project_for(session_name)
    target = _safe(root, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="not found")
    form = parse_qs((await request.body()).decode())
    target.write_text(str(form.get("content", [""])[0]))
    return RedirectResponse(url=f"{_file_base(session_name)}/edit?path={_q(path)}", status_code=303)
