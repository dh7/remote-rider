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


# File kinds that get an inline preview instead of the text editor.
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico", ".avif"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".m4v", ".mkv", ".ogv"}
AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac", ".opus"}


def _file_kind(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in IMAGE_EXTS:
        return "img"
    if ext in VIDEO_EXTS:
        return "vid"
    if ext in AUDIO_EXTS:
        return "snd"
    return "file"


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
.toolbar-actions { display: flex; align-items: center; gap: .5rem; }
.crumbs-wrap { display: flex; align-items: center; gap: .5rem; min-width: 0; flex: 1; }
.home-btn { flex: 0 0 auto; padding: .3rem .55rem; font-size: 1rem; line-height: 1; text-decoration: none; }
.row { display: flex; align-items: center; gap: .4rem; }
.row > a { flex: 1; min-width: 0; }
.row-del { background: none; border: none; color: #b07a7f; cursor: pointer; font-family: monospace; font-size: .9rem; padding: 2px 7px; opacity: .55; flex: 0 0 auto; }
.row-del:hover { opacity: 1; color: #ff7b83; }
.vid { color: #f9b; }
.snd { color: #c9f; }
.media-wrap { display: flex; align-items: center; justify-content: center; padding: 1rem; min-height: 60vh; }
.media-wrap img { max-width: 100%; max-height: 82vh; display: block; margin: auto; }
.media-wrap video { max-width: 100%; max-height: 82vh; }
.media-wrap audio { width: min(560px, 90vw); }
.dropzone { margin-top: 1rem; border: 2px dashed #3e4455; border-radius: 8px; padding: 2.2rem 1rem; text-align: center; color: #9aa4b2; background: #16181f; cursor: pointer; }
.dropzone.drag { border-color: #6aa0ff; background: #1b2740; color: #cfe0ff; }
.dropzone .hint { margin-top: .5rem; font-size: .82rem; }
.upload-status { margin-top: .6rem; color: #9aa4b2; font-size: .82rem; }
.up-results { margin-top: .9rem; display: flex; flex-direction: column; gap: .35rem; }
.up-row { display: flex; align-items: center; gap: .5rem; background: #16181f; border: 1px solid #2f3644; border-radius: 5px; padding: .35rem .5rem; }
.up-path { flex: 1; min-width: 0; overflow-x: auto; white-space: nowrap; color: #cfe0ff; font-size: .82rem; }
.copy-btn { background: #2f446f; color: #fff; border: none; cursor: pointer; font-family: monospace; font-size: .8rem; padding: .3rem .55rem; border-radius: 4px; flex: 0 0 auto; }
.copy-btn:hover { filter: brightness(1.15); }
.copy-btn.done { background: #2f7d43; }
.hidden { display: none !important; }
</style>
"""


def _file_base(session_name: str) -> str:
    return f"/workspaces/{quote(session_name, safe='')}/files"


def _breadcrumbs(session_name: str, path: str) -> str:
    base = _file_base(session_name)
    parts = Path(path).parts if path else []
    crumbs = [f'<a href="{base}">🏠</a>']
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
    base = _file_base(session_name)
    if not target.is_dir():
        # Navigating directly to a file: route by kind, same as clicking a row.
        rel = _q(_rel(root, target))
        kind = _file_kind(target)
        if kind == "img":
            return RedirectResponse(url=f"{base}/img?path={rel}", status_code=302)
        if kind in ("vid", "snd"):
            return RedirectResponse(url=f"{base}/media?path={rel}", status_code=302)
        return RedirectResponse(url=f"{base}/edit?path={rel}", status_code=302)

    statuses = _git_status_map(target)
    entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    home = f'<a class="btn ghost home-btn" href="{base}" title="Workspace home">🏠</a>'
    html = [
        "<html><head>",
        FILE_STYLE,
        "</head><body>",
        '<div class="topbar">',
        f'<div class="crumbs-wrap">{home}{_breadcrumbs(session_name, path)}</div>',
        '<div class="toolbar-actions">',
        f'<a class="btn" href="{base}/new-file?path={_q(path)}">Create File</a>',
        f'<a class="btn" href="{base}/upload-form?path={_q(path)}">Upload</a>',
        "</div></div><hr>",
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
            continue
        kind = _file_kind(entry)
        if kind == "img":
            link = f'<a class="entry-row img" href="{base}/img?path={_q(rel)}">{badge}[img] {name}</a>'
        elif kind == "vid":
            link = f'<a class="entry-row vid" href="{base}/media?path={_q(rel)}">{badge}[vid] {name}</a>'
        elif kind == "snd":
            link = f'<a class="entry-row snd" href="{base}/media?path={_q(rel)}">{badge}[snd] {name}</a>'
        else:
            link = f'<a class="entry-row file" href="{base}/edit?path={_q(rel)}">{badge}{name}</a>'
        attr = escape(rel, quote=True)
        html.append(
            f'<div class="row">{link}'
            f'<button class="row-del" data-path="{attr}" data-name="{name}" title="Delete">🗑</button>'
            "</div>"
        )
    html.append(f"""
    <script>
      document.querySelectorAll('.row-del').forEach(function (btn) {{
        btn.addEventListener('click', async function (e) {{
          e.preventDefault(); e.stopPropagation();
          if (!confirm('Delete ' + btn.dataset.name + '?')) return;
          try {{
            const res = await fetch('{base}/delete?path=' + encodeURIComponent(btn.dataset.path), {{ method: 'POST' }});
            if (res.ok) {{ location.reload(); }}
            else {{ alert('Delete failed: ' + (await res.text())); }}
          }} catch (err) {{ alert('Delete failed: ' + err); }}
        }});
      }});
    </script>
    """)
    html.append("</body></html>")
    return HTMLResponse("".join(html))


@router.get("/{session_name}/files/raw")
def files_raw(session_name: str, path: str) -> FileResponse:
    root = _project_for(session_name)
    target = _safe(root, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(target)


def _preview_topbar(session_name: str, rel: str) -> str:
    """Breadcrumbs to the containing folder + Back, for preview pages."""
    base = _file_base(session_name)
    parent = str(Path(rel).parent)
    if parent == ".":
        parent = ""
    return (
        '<div class="topbar">'
        f'{_breadcrumbs(session_name, parent)}'
        f'<div class="toolbar-actions"><a class="btn ghost" href="{base}?path={_q(parent)}">Back</a></div>'
        "</div><hr>"
    )


@router.get("/{session_name}/files/img", response_class=HTMLResponse)
def files_img(session_name: str, path: str) -> HTMLResponse:
    root = _project_for(session_name)
    target = _safe(root, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="not found")
    base = _file_base(session_name)
    rel = _rel(root, target)
    return HTMLResponse(
        f"<html><head>{FILE_STYLE}</head><body>"
        f"{_preview_topbar(session_name, rel)}"
        f'<div class="media-wrap"><img src="{base}/raw?path={_q(rel)}"></div>'
        "</body></html>"
    )


@router.get("/{session_name}/files/media", response_class=HTMLResponse)
def files_media(session_name: str, path: str) -> HTMLResponse:
    root = _project_for(session_name)
    target = _safe(root, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="not found")
    base = _file_base(session_name)
    rel = _rel(root, target)
    src = f"{base}/raw?path={_q(rel)}"
    if _file_kind(target) == "snd":
        player = f'<audio controls autoplay src="{src}">Your browser cannot play this audio.</audio>'
    else:
        player = f'<video controls autoplay playsinline src="{src}">Your browser cannot play this video.</video>'
    return HTMLResponse(
        f"<html><head>{FILE_STYLE}</head><body>"
        f"{_preview_topbar(session_name, rel)}"
        f'<div class="media-wrap">{player}</div>'
        "</body></html>"
    )


@router.get("/{session_name}/files/upload-form", response_class=HTMLResponse)
def files_upload_form(session_name: str, path: str = "") -> HTMLResponse:
    root = _project_for(session_name)
    directory = _safe(root, path)
    if not directory.exists() or not directory.is_dir():
        raise HTTPException(status_code=404, detail="not found")
    base = _file_base(session_name)
    page = f"""
    <html><head>{FILE_STYLE}</head><body>
      <div class="topbar">
        {_breadcrumbs(session_name, path)}
        <div class="toolbar-actions"><a class="btn ghost" href="{base}?path={_q(path)}">Back</a></div>
      </div>
      <hr>
      <div id="dropzone" class="dropzone">
        <div><strong>Drop files here</strong>, paste an image, or</div>
        <div class="hint"><button class="btn ghost" type="button" id="pick">choose files</button></div>
        <input id="file-input" type="file" multiple class="hidden">
      </div>
      <div id="upload-status" class="upload-status"></div>
      <div id="up-results" class="up-results"></div>
      <script>
        const uploadDir = {json.dumps(path)};
        const base = {json.dumps(base)};
        const dz = document.getElementById('dropzone');
        const fileInput = document.getElementById('file-input');
        const statusEl = document.getElementById('upload-status');
        const resultsEl = document.getElementById('up-results');
        const usedNames = new Set();
        const extByMime = {{ 'image/png':'png','image/jpeg':'jpg','image/gif':'gif','image/webp':'webp','image/svg+xml':'svg','image/bmp':'bmp','image/avif':'avif','image/x-icon':'ico' }};
        function pad(n) {{ return String(n).padStart(2, '0'); }}
        function stamp() {{ const d = new Date(); return '' + d.getFullYear() + pad(d.getMonth()+1) + pad(d.getDate()) + '-' + pad(d.getHours()) + pad(d.getMinutes()) + pad(d.getSeconds()); }}
        function canonicalName(file) {{
          let ext = extByMime[file.type];
          if (!ext && file.name && file.name.includes('.')) ext = file.name.split('.').pop().toLowerCase();
          if (!ext) ext = 'png';
          const b = 'pasted-' + stamp(); let name = b + '.' + ext, i = 2;
          while (usedNames.has(name)) {{ name = b + '-' + i + '.' + ext; i++; }}
          usedNames.add(name); return name;
        }}
        async function copyText(text) {{
          try {{ if (navigator.clipboard && navigator.clipboard.writeText) {{ await navigator.clipboard.writeText(text); return true; }} }} catch (e) {{}}
          try {{ const ta = document.createElement('textarea'); ta.value = text; ta.style.position='fixed'; ta.style.opacity='0'; document.body.appendChild(ta); ta.focus(); ta.select(); const ok = document.execCommand('copy'); document.body.removeChild(ta); return ok; }} catch (e) {{ return false; }}
        }}
        function addResult(relPath) {{
          const row = document.createElement('div'); row.className = 'up-row';
          const code = document.createElement('code'); code.className = 'up-path'; code.textContent = relPath;
          const btn = document.createElement('button'); btn.className = 'copy-btn'; btn.textContent = '📋 Copy';
          btn.addEventListener('click', async () => {{ const ok = await copyText(relPath); btn.textContent = ok ? '✓ Copied' : '⚠ Failed'; btn.classList.toggle('done', ok); setTimeout(() => {{ btn.textContent = '📋 Copy'; btn.classList.remove('done'); }}, 1600); }});
          row.appendChild(code); row.appendChild(btn); resultsEl.prepend(row);
        }}
        document.getElementById('pick').addEventListener('click', () => fileInput.click());
        dz.addEventListener('click', (e) => {{ if (e.target === dz || e.target.tagName === 'STRONG' || e.target.tagName === 'DIV') fileInput.click(); }});
        fileInput.addEventListener('change', () => uploadFiles(fileInput.files, false));
        ['dragenter','dragover'].forEach(ev => dz.addEventListener(ev, (e) => {{ e.preventDefault(); dz.classList.add('drag'); }}));
        ['dragleave','drop'].forEach(ev => dz.addEventListener(ev, (e) => {{ e.preventDefault(); dz.classList.remove('drag'); }}));
        ['dragover','drop'].forEach(ev => window.addEventListener(ev, (e) => e.preventDefault()));
        dz.addEventListener('drop', (e) => uploadFiles(e.dataTransfer.files, false));
        window.addEventListener('paste', (e) => {{
          const items = (e.clipboardData && e.clipboardData.items) || []; const files = [];
          for (const it of items) {{ if (it.kind === 'file') {{ const f = it.getAsFile(); if (f) files.push(f); }} }}
          if (files.length) {{ e.preventDefault(); uploadFiles(files, true); }}
        }});
        async function uploadFiles(files, fromPaste) {{
          const list = Array.from(files || []); if (!list.length) return;
          let done = 0, failed = 0;
          for (const file of list) {{
            const name = fromPaste ? canonicalName(file) : file.name;
            statusEl.textContent = `Uploading ${{name}} (${{done + failed + 1}}/${{list.length}})...`;
            try {{
              const res = await fetch(base + '/upload?path=' + encodeURIComponent(uploadDir) + '&name=' + encodeURIComponent(name), {{ method: 'POST', headers: {{ 'content-type': 'application/octet-stream' }}, body: file }});
              if (res.ok) {{ done++; const data = await res.json(); addResult(data.path); }}
              else {{ failed++; statusEl.textContent = `Failed ${{name}}: ${{await res.text()}}`; }}
            }} catch (err) {{ failed++; statusEl.textContent = `Failed ${{name}}: ${{err}}`; }}
          }}
          statusEl.textContent = failed ? `Uploaded ${{done}}, failed ${{failed}}. Paths below.` : `Uploaded ${{done}} file(s). Paths below (go Back to see them in the folder).`;
        }}
      </script>
    </body></html>
    """
    return HTMLResponse(page)


@router.post("/{session_name}/files/upload")
async def files_upload(session_name: str, request: Request, path: str = "", name: str = "") -> dict[str, str]:
    root = _project_for(session_name)
    directory = _safe(root, path)
    if not directory.exists() or not directory.is_dir():
        raise HTTPException(status_code=404, detail="not found")
    filename = Path(name).name
    if not filename:
        raise HTTPException(status_code=400, detail="name is required")
    target = _safe(root, str(Path(path) / filename))
    if target.exists() and target.is_dir():
        raise HTTPException(status_code=400, detail="cannot overwrite a directory")
    data = await request.body()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {"status": "ok", "path": _rel(root, target)}


@router.post("/{session_name}/files/delete")
def files_delete(session_name: str, path: str) -> HTMLResponse:
    root = _project_for(session_name)
    target = _safe(root, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="not found")
    target.unlink()
    return HTMLResponse("ok")


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
