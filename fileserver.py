import json
import subprocess
from html import escape
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

app = FastAPI()
import os as _os
# START is the directory the tab was opened on: the landing spot and the only
# writable zone. BOUNDARY is how far navigation may climb — the whole machine.
START = Path(_os.environ["RR_FILES_ROOT"]).expanduser().resolve() if _os.environ.get("RR_FILES_ROOT") else Path.home().resolve()
BOUNDARY = Path("/").resolve()
COOKIE_MAX_AGE = 60 * 60 * 24 * 30

STYLE = """
<style>
  * { box-sizing: border-box; }
  body {
    margin: 0;
    padding: 1rem;
    font-family: monospace;
    background: #111;
    color: #eee;
    min-height: 100vh;
  }
  a { color: #7af; text-decoration: none; display: block; padding: 2px 0; }
  a:hover { color: #fff; }

  .crumbs { white-space: nowrap; overflow-x: auto; overflow-y: hidden; }
  .crumbs a { display: inline; padding: 0; }
  .topbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    margin-bottom: 0.5rem;
  }
  .toolbar-actions {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }
  .btn {
    background: #2f446f;
    color: #fff;
    border: none;
    display: inline-block;
    padding: 0.42rem 0.78rem;
    cursor: pointer;
    font-family: monospace;
    font-size: 0.84rem;
    text-decoration: none;
  }
  .btn:hover { filter: brightness(1.12); }

  .ro-note { color: #fa0; font-size: 0.78rem; border: 1px solid #87611e; background: #4f3a1c; padding: 0.2rem 0.5rem; border-radius: 3px; }
  .dir { color: #fa0; }
  .dir-hidden { color: #666; }
  .img { color: #af7; }
  .file { color: #fff; }
  .file-hidden { color: #666; }

  .entry-row { display: flex; align-items: center; gap: 0.5rem; }
  .git-badge {
    display: inline-block;
    min-width: 1.4rem;
    text-align: center;
    padding: 0.02rem 0.25rem;
    border-radius: 3px;
    font-size: 0.72rem;
    border: 1px solid #444;
  }
  .git-modified { background: #4f3a1c; border-color: #87611e; color: #ffd992; }
  .git-added { background: #1e4d2a; border-color: #2f7d43; color: #abf5bf; }
  .git-deleted { background: #5a1f24; border-color: #8c2f37; color: #ffabb2; }
  .git-untracked { background: #243652; border-color: #3a5d8f; color: #b8d7ff; }
  .git-renamed { background: #43305e; border-color: #6b4e96; color: #d9c4ff; }

  body.editor-page {
    padding: 0;
    height: 100vh;
    overflow: hidden;
    background: #0f1014;
  }
  .editor-shell {
    height: 100vh;
    display: flex;
    flex-direction: column;
    min-height: 0;
    padding: 0.8rem;
    gap: 0.55rem;
  }

  .toolbar {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex-wrap: wrap;
  }
  .toolbar .path {
    color: #d5d5d5;
    max-width: 30rem;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .toolbar .grow { flex: 1; }
  .toolbar a { display: inline-block; }

  .btn {
    background: #2f446f;
    color: #fff;
    border: none;
    padding: 0.42rem 0.78rem;
    cursor: pointer;
    font-family: monospace;
    font-size: 0.84rem;
  }
  .btn:hover { filter: brightness(1.12); }
  .btn.ghost { background: #1d1f26; border: 1px solid #4b5565; }
  .btn.danger { background: #782f35; }
  .btn.active { outline: 1px solid #6aa0ff; }

  .save-path,
  .search-input,
  .replace-input {
    background: #11131a;
    border: 1px solid #3e4455;
    color: #eee;
    font-family: monospace;
    padding: 0.42rem 0.5rem;
    min-width: 13rem;
  }

  .pane-stack {
    flex: 1;
    min-height: 0;
    position: relative;
    border: 1px solid #394050;
    background: #10131b;
  }
  .pane {
    position: absolute;
    inset: 0;
    display: none;
    min-height: 0;
    min-width: 0;
  }
  .pane.active { display: flex; }

  #code-pane { flex-direction: column; }
  textarea {
    width: 100%;
    height: 100%;
    background: #11131a;
    color: #eee;
    border: none;
    font-family: monospace;
    padding: 0.5rem;
  }
  .CodeMirror {
    flex: 1;
    min-height: 0;
    height: 100% !important;
    font-size: 0.9rem;
  }

  #csv-pane {
    flex-direction: column;
    gap: 0.45rem;
    padding: 0.45rem;
  }
  #csv-table {
    flex: 1;
    min-height: 0;
    border: 1px solid #2f3644;
  }

  #view-pane {
    overflow: auto;
    padding: 1rem;
    background: linear-gradient(180deg, #121722 0%, #0e1118 100%);
  }
  .markdown-body {
    max-width: 1000px;
    margin: 0 auto;
    padding: 1.4rem;
    border-radius: 12px;
    border: 1px solid #303646;
    background: #161b27;
  }

  .hidden { display: none !important; }

  .dropzone {
    margin-top: 1rem;
    border: 2px dashed #3e4455;
    border-radius: 8px;
    padding: 2.2rem 1rem;
    text-align: center;
    color: #9aa4b2;
    background: #16181f;
    cursor: pointer;
  }
  .dropzone.drag { border-color: #6aa0ff; background: #1b2740; color: #cfe0ff; }
  .dropzone .hint { margin-top: 0.5rem; font-size: 0.82rem; }
  .upload-status { margin-top: 0.6rem; color: #9aa4b2; font-size: 0.82rem; }
</style>
"""


def _safe(path: str) -> Path:
    target = (BOUNDARY / path).resolve()
    try:
        target.relative_to(BOUNDARY)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Path escapes root") from exc
    return target


def _within_start(target: Path) -> bool:
    try:
        target.resolve().relative_to(START)
        return True
    except ValueError:
        return False


def _require_writable(target: Path) -> None:
    if not _within_start(target):
        raise HTTPException(status_code=403, detail="Read-only: outside the tab's start directory")


def _q(path: str) -> str:
    return quote(path, safe="/")


def _decode_cookie_path(value: str | None) -> str:
    return unquote(value) if value else ""


def _rel(path: Path) -> str:
    return str(path.relative_to(BOUNDARY))


def _is_safe_dir(path: str) -> bool:
    try:
        target = _safe(path)
    except HTTPException:
        return False
    return target.exists() and target.is_dir()


def _is_safe_file(path: str) -> bool:
    try:
        target = _safe(path)
    except HTTPException:
        return False
    return target.exists() and target.is_file()


def _set_context_cookies(
    response: HTMLResponse,
    *,
    view: str | None = None,
    dir_path: str | None = None,
    file_path: str | None = None,
) -> None:
    if view is not None:
        response.set_cookie("fs_last_view", view, max_age=COOKIE_MAX_AGE, samesite="lax")
    if dir_path is not None:
        response.set_cookie("fs_last_dir", _q(dir_path), max_age=COOKIE_MAX_AGE, samesite="lax")
    if file_path is not None:
        response.set_cookie("fs_last_file", _q(file_path), max_age=COOKIE_MAX_AGE, samesite="lax")


def _status_badge(code: str) -> tuple[str, str]:
    if code == "??":
        return "?", "git-untracked"
    if "D" in code:
        return "D", "git-deleted"
    if "A" in code:
        return "A", "git-added"
    if "R" in code:
        return "R", "git-renamed"
    if "M" in code:
        return "M", "git-modified"
    return "*", "git-modified"


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
    priority = {"D": 5, "A": 4, "R": 3, "M": 2, "?": 1, "*": 0}

    for line in out.stdout.splitlines():
        if len(line) < 4:
            continue
        code = line[:2]
        path_part = line[3:]
        if " -> " in path_part:
            path_part = path_part.split(" -> ", 1)[1]

        first = Path(path_part).parts[0] if Path(path_part).parts else ""
        if not first:
            continue

        label, css = _status_badge(code)
        existing = statuses.get(first)
        if not existing or priority[label] > priority[existing[0]]:
            statuses[first] = (label, css)

    return statuses


def breadcrumbs(path: str) -> str:
    parts = Path(path).parts if path else []
    crumbs = ['<a href="/files?path=&force_root=1">/</a>']
    acc = Path()
    for part in parts:
        acc /= part
        crumbs.append(f'<a href="/files?path={_q(str(acc))}">{escape(part)}</a>')
    return f'<div class="crumbs">{" / ".join(crumbs)}</div>'


@app.get("/files", response_class=HTMLResponse)
def browse(request: Request, path: str = "") -> HTMLResponse:
    force_home = request.query_params.get("force_home") == "1"
    force_root = request.query_params.get("force_root") == "1"
    if force_root:
        path = ""
    elif force_home:
        path = _rel(START)
    elif not path:
        last_view = request.cookies.get("fs_last_view", "")
        last_dir = _decode_cookie_path(request.cookies.get("fs_last_dir"))
        last_file = _decode_cookie_path(request.cookies.get("fs_last_file"))

        if last_view == "edit" and last_file and _is_safe_file(last_file):
            return RedirectResponse(url=f"/edit?path={_q(last_file)}", status_code=302)
        if last_dir and _is_safe_dir(last_dir):
            path = last_dir
        else:
            path = _rel(START)

    target = _safe(path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Not found")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Not a directory")

    writable = _within_start(target)
    statuses = _git_status_map(target)
    entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    actions = f'<a class="btn ghost" href="/files?path=&force_home=1">start</a>'
    if writable:
        actions += f'<a class="btn" href="/new-file?path={_q(path)}">Create File</a>'
    else:
        actions += '<span class="ro-note">read-only</span>'
    items = (
        '<div class="topbar">'
        f'{breadcrumbs(path)}'
        f'<div class="toolbar-actions">{actions}</div>'
        '</div><hr>'
    )

    if path:
        parent = "" if Path(path).parent == Path(".") else str(Path(path).parent)
        suffix = "&force_root=1" if not parent else ""
        items += f'<a class="dir" href="/files?path={_q(parent)}{suffix}">..</a>'

    for entry in entries:
        rel = _rel(entry)
        name = escape(entry.name)
        badge = ""
        if entry.name in statuses:
            label, css = statuses[entry.name]
            badge = f'<span class="git-badge {css}">{label}</span>'

        if entry.is_dir():
            klass = "dir-hidden" if entry.name.startswith(".") else "dir"
            items += f'<a class="entry-row {klass}" href="/files?path={_q(rel)}">{badge}[dir] {name}</a>'
        elif entry.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}:
            items += f'<a class="entry-row img" href="/img?path={_q(rel)}" target="_blank">{badge}[img] {name}</a>'
        else:
            klass = "file-hidden" if entry.name.startswith(".") else "file"
            items += f'<a class="entry-row {klass}" href="/edit?path={_q(rel)}">{badge}{name}</a>'

    response = HTMLResponse(f"<html><head>{STYLE}</head><body>{items}</body></html>")
    _set_context_cookies(response, view="files", dir_path=path)
    return response


@app.get("/new-file", response_class=HTMLResponse)
def new_file_form(path: str = "") -> HTMLResponse:
    directory = _safe(path)
    if not directory.exists() or not directory.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")
    _require_writable(directory)

    form = f"""
    <html><head>{STYLE}</head><body>
      <div class="topbar">
        {breadcrumbs(path)}
        <div class="toolbar-actions">
          <a class="btn ghost" href="/files?path={_q(path)}">Back</a>
        </div>
      </div>
      <hr>
      <form method="post" action="/create-file?path={_q(path)}" style="display:flex;gap:0.5rem;align-items:center;flex-wrap:wrap">
        <input class="save-path" type="text" name="new_path" placeholder="new file path" autofocus>
        <button class="btn" type="submit">Create File</button>
      </form>

      <div id="dropzone" class="dropzone">
        <div><strong>Drop files here</strong> to upload into this folder</div>
        <div class="hint">or <button class="btn ghost" type="button" id="pick">choose files</button></div>
        <input id="file-input" type="file" multiple class="hidden">
      </div>
      <div id="upload-status" class="upload-status"></div>

      <script>
        const uploadDir = {json.dumps(path)};
        const dz = document.getElementById('dropzone');
        const fileInput = document.getElementById('file-input');
        const statusEl = document.getElementById('upload-status');

        document.getElementById('pick').addEventListener('click', () => fileInput.click());
        dz.addEventListener('click', (e) => {{ if (e.target === dz || e.target.tagName === 'STRONG' || e.target.tagName === 'DIV') fileInput.click(); }});
        fileInput.addEventListener('change', () => uploadFiles(fileInput.files));

        ['dragenter', 'dragover'].forEach(ev => dz.addEventListener(ev, (e) => {{ e.preventDefault(); dz.classList.add('drag'); }}));
        ['dragleave', 'drop'].forEach(ev => dz.addEventListener(ev, (e) => {{ e.preventDefault(); dz.classList.remove('drag'); }}));
        ['dragover', 'drop'].forEach(ev => window.addEventListener(ev, (e) => e.preventDefault()));
        dz.addEventListener('drop', (e) => uploadFiles(e.dataTransfer.files));

        async function uploadFiles(files) {{
          const list = Array.from(files || []);
          if (!list.length) return;
          let done = 0, failed = 0;
          for (const file of list) {{
            statusEl.textContent = `Uploading ${{file.name}} (${{done + failed + 1}}/${{list.length}})...`;
            try {{
              const res = await fetch('/upload?path=' + encodeURIComponent(uploadDir) + '&name=' + encodeURIComponent(file.name), {{
                method: 'POST',
                headers: {{ 'content-type': 'application/octet-stream' }},
                body: file,
              }});
              if (res.ok) {{ done++; }}
              else {{ failed++; statusEl.textContent = `Failed ${{file.name}}: ${{await res.text()}}`; }}
            }} catch (err) {{ failed++; statusEl.textContent = `Failed ${{file.name}}: ${{err}}`; }}
          }}
          if (!failed) {{
            statusEl.textContent = `Uploaded ${{done}} file(s). Opening folder...`;
            window.location.href = '/files?path=' + encodeURIComponent(uploadDir);
          }} else {{
            statusEl.textContent = `Uploaded ${{done}}, failed ${{failed}}.`;
          }}
        }}
      </script>
    </body></html>
    """
    response = HTMLResponse(form)
    _set_context_cookies(response, view="files", dir_path=path)
    return response


@app.get("/img", response_class=HTMLResponse)
def view_image(path: str) -> str:
    target = _safe(path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Image not found")
    rel = _rel(target)
    return f"""<html><head>{STYLE}</head><body style="margin:0;padding:0;background:#000">
    <img src="/raw?path={_q(rel)}" style="max-width:100%;max-height:100vh;display:block;margin:auto">
    </body></html>"""


@app.get("/edit", response_class=HTMLResponse)
def edit_file(path: str) -> HTMLResponse:
    target = _safe(path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    rel = _rel(target)
    parent = str(Path(rel).parent)
    parent_q = _q(parent)
    content = escape(target.read_text(errors="replace"))
    writable = _within_start(target)

    is_csv = target.suffix.lower() == ".csv"
    is_markdown = target.suffix.lower() in {".md", ".markdown", ".mdown"}

    rel_js = json.dumps(rel)
    parent_js = json.dumps(parent)
    save_as_js = json.dumps(f"{rel}.copy")
    is_csv_js = "true" if is_csv else "false"
    is_md_js = "true" if is_markdown else "false"
    can_write_js = "true" if writable else "false"

    html = f"""<html><head>{STYLE}
    <script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/codemirror.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/mode/meta.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/addon/mode/loadmode.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/addon/edit/matchbrackets.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/addon/search/searchcursor.min.js"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/codemirror.min.css">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/theme/material-darker.min.css">

    <script src="https://cdnjs.cloudflare.com/ajax/libs/PapaParse/5.4.1/papaparse.min.js"></script>
    <script src="https://unpkg.com/tabulator-tables@6.3.0/dist/js/tabulator.min.js"></script>
    <link rel="stylesheet" href="https://unpkg.com/tabulator-tables@6.3.0/dist/css/tabulator_midnight.min.css">

    <script src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.2/marked.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/github-markdown-css/5.5.1/github-markdown-dark.min.css">
    </head><body class="editor-page">

    <div class="editor-shell">
      <div class="toolbar">
        <a class="btn ghost" href="/files?path={parent_q}">Back</a>
        <div class="path">{escape(rel)}</div>
        <div class="grow"></div>
        <button id="mode-code" class="btn ghost" type="button">Code</button>
        <button id="mode-csv" class="btn ghost" type="button">CSV</button>
        <button id="mode-view" class="btn ghost" type="button">View</button>
        <button id="btn-save" class="btn" type="button" onclick="saveFile()">Save</button>
        <button id="btn-saveas" class="btn ghost" type="button" onclick="saveAsFile()">Save As</button>
        <button id="btn-rename" class="btn ghost" type="button" onclick="renameMoveFile()">Rename/Move</button>
        <button id="btn-delete" class="btn danger" type="button" onclick="deleteFile()">Delete</button>
      </div>

      <div class="toolbar">
        <input id="save-path" class="save-path" type="text" placeholder="new path">
        <input id="search-input" class="search-input" type="text" placeholder="search">
        <input id="replace-input" class="replace-input" type="text" placeholder="replace">
        <button class="btn ghost" type="button" onclick="findNext()">Find Next</button>
        <button id="btn-replace" class="btn ghost" type="button" onclick="replaceOne()">Replace</button>
        <button id="btn-replaceall" class="btn ghost" type="button" onclick="replaceAll()">Replace All</button>
      </div>

      <div class="pane-stack">
        <div id="code-pane" class="pane">
          <textarea id="content">{content}</textarea>
        </div>

        <div id="csv-pane" class="pane">
          <div class="toolbar">
            <button class="btn ghost" type="button" onclick="addCsvRow()">Add Row</button>
            <button class="btn ghost" type="button" onclick="addCsvCol()">Add Column</button>
          </div>
          <div id="csv-table"></div>
        </div>

        <div id="view-pane" class="pane">
          <article id="markdown-view" class="markdown-body"></article>
        </div>
      </div>
    </div>

    <script>
      const currentPath = {rel_js};
      const parentPath = {parent_js};
      const isCsvFile = {is_csv_js};
      const isMarkdownFile = {is_md_js};
      const canWrite = {can_write_js};

      const savePathInput = document.getElementById('save-path');
      const searchInput = document.getElementById('search-input');
      const replaceInput = document.getElementById('replace-input');
      const modeCodeBtn = document.getElementById('mode-code');
      const modeCsvBtn = document.getElementById('mode-csv');
      const modeViewBtn = document.getElementById('mode-view');

      const codePane = document.getElementById('code-pane');
      const csvPane = document.getElementById('csv-pane');
      const viewPane = document.getElementById('view-pane');
      const markdownView = document.getElementById('markdown-view');
      const textarea = document.getElementById('content');

      savePathInput.value = {save_as_js};

      let dirty = false;
      let editor = null;
      let csvTable = null;
      let csvColumns = [];
      let currentMode = '';
      let lastSearchPos = null;
      let csvSearchCursor = -1;

      function setCookie(name, value) {{
        document.cookie = `${{name}}=${{encodeURIComponent(value)}}; path=/; max-age={COOKIE_MAX_AGE}; SameSite=Lax`;
      }}

      function getCookie(name) {{
        const prefix = `${{name}}=`;
        for (const part of document.cookie.split(';')) {{
          const item = part.trim();
          if (item.startsWith(prefix)) return decodeURIComponent(item.slice(prefix.length));
        }}
        return '';
      }}

      const savedMode = getCookie('fs_last_edit_mode');

      function markDirty() {{ dirty = true; }}
      function clearDirty() {{ dirty = false; }}
      function requireWrite() {{
        if (!canWrite) {{
          alert('This file is outside the tab\'s start directory and is read-only.');
          return false;
        }}
        return true;
      }}

      window.addEventListener('beforeunload', (e) => {{
        if (!dirty) return;
        e.preventDefault();
        e.returnValue = '';
      }});

      document.addEventListener('keydown', (e) => {{
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {{
          e.preventDefault();
          saveFile();
        }}
      }});

      function initEditor() {{
        editor = CodeMirror.fromTextArea(textarea, {{
          lineNumbers: true,
          theme: 'material-darker',
          indentUnit: 2,
          tabSize: 2,
          lineWrapping: false,
          matchBrackets: true,
        }});
        editor.on('change', markDirty);

        CodeMirror.modeURL = 'https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/mode/%N/%N.min.js';
        const modeInfo = CodeMirror.findModeByFileName(currentPath);
        if (modeInfo) {{
          editor.setOption('mode', modeInfo.mime);
          CodeMirror.autoLoadMode(editor, modeInfo.mode);
        }}

        editor.setSize(null, '100%');
      }}

      function syncCodeText() {{
        if (editor) editor.save();
      }}

      function parseCsvRows(text) {{
        const parsed = Papa.parse(text, {{ skipEmptyLines: false }});
        if (!Array.isArray(parsed.data) || parsed.data.length === 0) return [[]];
        return parsed.data;
      }}

      function csvRowsToTable(rows) {{
        const maxCols = Math.max(1, ...rows.map(r => r.length));
        csvColumns = [];
        for (let i = 0; i < maxCols; i++) {{
          csvColumns.push({{
            title: `C${{i + 1}}`,
            field: `c${{i}}`,
            editor: 'input',
          }});
        }}

        return rows.map((row, idx) => {{
          const obj = {{ id: idx }};
          for (let i = 0; i < maxCols; i++) obj[`c${{i}}`] = row[i] ?? '';
          return obj;
        }});
      }}

      function tableToCsvRows() {{
        if (!csvTable) return [[]];
        const rows = csvTable.getData();
        const cols = csvColumns.map(c => c.field);
        return rows.map(r => cols.map(c => r[c] ?? ''));
      }}

      function renderCsvTableFromText() {{
        if (!window.Tabulator) {{
          csvTable = null;
          return;
        }}

        const rows = parseCsvRows(textarea.value);
        const data = csvRowsToTable(rows);

        if (!csvTable) {{
          csvTable = new Tabulator('#csv-table', {{
            data,
            columns: csvColumns,
            layout: 'fitDataStretch',
            height: '100%',
            index: 'id',
            cellEdited: markDirty,
          }});
          window.requestAnimationFrame(() => csvTable && csvTable.redraw(true));
          return;
        }}

        csvTable.setColumns(csvColumns);
        csvTable.replaceData(data);
        window.requestAnimationFrame(() => csvTable && csvTable.redraw(true));
      }}

      function syncCsvBackToCode() {{
        if (!csvTable) return;
        const rows = tableToCsvRows();
        const text = Papa.unparse(rows);
        textarea.value = text;
        if (editor) editor.setValue(text);
      }}

      function renderMarkdownView() {{
        syncCodeText();
        if (window.marked) {{
          marked.setOptions({{
            breaks: true,
            gfm: true,
            highlight: (code, lang) => {{
              if (lang && window.hljs && hljs.getLanguage(lang)) {{
                return hljs.highlight(code, {{ language: lang }}).value;
              }}
              return window.hljs ? hljs.highlightAuto(code).value : code;
            }},
          }});
          markdownView.innerHTML = marked.parse(textarea.value);
          if (window.hljs) markdownView.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
        }} else {{
          markdownView.textContent = textarea.value;
        }}
      }}

      function setMode(mode) {{
        if (mode === 'csv' && !isCsvFile) mode = 'code';
        if (mode === 'view' && !isMarkdownFile) mode = 'code';

        if (mode === currentMode) {{
          if (mode === 'code' && editor) editor.refresh();
          if (mode === 'csv' && csvTable) window.requestAnimationFrame(() => csvTable && csvTable.redraw(true));
          if (mode === 'view' && isMarkdownFile) renderMarkdownView();
          return;
        }}

        if (currentMode === 'csv' && mode !== 'csv') {{
          syncCsvBackToCode();
        }}

        currentMode = mode;
        setCookie('fs_last_edit_mode', mode);
        codePane.classList.toggle('active', mode === 'code');
        csvPane.classList.toggle('active', mode === 'csv');
        viewPane.classList.toggle('active', mode === 'view');
        modeCodeBtn.classList.toggle('active', mode === 'code');
        modeCsvBtn.classList.toggle('active', mode === 'csv');
        modeViewBtn.classList.toggle('active', mode === 'view');

        if (mode === 'csv') {{
          syncCodeText();
          renderCsvTableFromText();
        }}
        if (mode === 'view') renderMarkdownView();
        if (mode === 'code' && editor) editor.refresh();
      }}

      function syncBeforeSave() {{
        if (currentMode === 'csv') syncCsvBackToCode();
        syncCodeText();
      }}

      async function postJson(url, payload) {{
        const res = await fetch(url, {{
          method: 'POST',
          headers: {{ 'content-type': 'application/json' }},
          body: JSON.stringify(payload || {{}}),
        }});
        if (!res.ok) {{
          const text = await res.text();
          alert(`Error ${{res.status}}: ${{text}}`);
          return false;
        }}
        return true;
      }}

      async function saveFile() {{
        if (!requireWrite()) return;
        syncBeforeSave();
        const ok = await postJson('/save?path=' + encodeURIComponent(currentPath), {{ content: textarea.value }});
        if (ok) {{
          clearDirty();
          window.location.href = '/edit?path=' + encodeURIComponent(currentPath);
        }}
      }}

      async function saveAsFile() {{
        if (!requireWrite()) return;
        syncBeforeSave();
        const newPath = savePathInput.value.trim();
        if (!newPath) return;
        const ok = await postJson('/save-as?path=' + encodeURIComponent(currentPath), {{
          new_path: newPath,
          content: textarea.value,
        }});
        if (ok) {{
          clearDirty();
          window.location.href = '/edit?path=' + encodeURIComponent(newPath);
        }}
      }}

      async function renameMoveFile() {{
        if (!requireWrite()) return;
        syncBeforeSave();
        const newPath = savePathInput.value.trim();
        if (!newPath) return;
        const ok = await postJson('/rename?path=' + encodeURIComponent(currentPath), {{
          new_path: newPath,
          content: textarea.value,
        }});
        if (ok) {{
          clearDirty();
          window.location.href = '/edit?path=' + encodeURIComponent(newPath);
        }}
      }}

      async function deleteFile() {{
        if (!requireWrite()) return;
        if (!confirm('Delete this file?')) return;
        const ok = await postJson('/delete?path=' + encodeURIComponent(currentPath));
        if (ok) {{
          clearDirty();
          window.location.href = '/files?path=' + encodeURIComponent(parentPath);
        }}
      }}

      function findNext() {{
        const q = searchInput.value;
        if (!q) return;

        if (currentMode === 'csv' && csvTable) {{
          const cells = csvTable.getRows().flatMap(r => r.getCells());
          if (!cells.length) return;
          for (let i = 1; i <= cells.length; i++) {{
            const idx = (csvSearchCursor + i) % cells.length;
            const val = String(cells[idx].getValue() ?? '');
            if (val.includes(q)) {{
              csvSearchCursor = idx;
              cells[idx].getElement().scrollIntoView({{ block: 'center', inline: 'center' }});
              return;
            }}
          }}
          return;
        }}

        if (!editor) return;
        const start = lastSearchPos || editor.getCursor();
        let cursor = editor.getSearchCursor(q, start);
        if (!cursor.findNext()) {{
          cursor = editor.getSearchCursor(q, {{ line: 0, ch: 0 }});
          if (!cursor.findNext()) return;
        }}
        editor.setSelection(cursor.from(), cursor.to());
        editor.scrollIntoView({{ from: cursor.from(), to: cursor.to() }}, 60);
        lastSearchPos = cursor.to();
      }}

      function replaceOne() {{
        if (!requireWrite()) return;
        const q = searchInput.value;
        if (!q) return;
        const rep = replaceInput.value;

        if (currentMode === 'csv' && csvTable) {{
          const active = document.activeElement;
          if (active && active.tagName === 'INPUT') {{
            active.value = active.value.replace(q, rep);
            active.dispatchEvent(new Event('change', {{ bubbles: true }}));
            markDirty();
          }}
          return;
        }}

        if (!editor) return;
        const sel = editor.getSelection();
        if (sel && sel.includes(q)) {{
          editor.replaceSelection(sel.replace(q, rep));
          markDirty();
          return;
        }}

        const cursor = editor.getSearchCursor(q, editor.getCursor());
        if (cursor.findNext()) {{
          editor.setSelection(cursor.from(), cursor.to());
          editor.replaceSelection(rep);
          markDirty();
        }}
      }}

      function replaceAll() {{
        if (!requireWrite()) return;
        const q = searchInput.value;
        if (!q) return;
        const rep = replaceInput.value;

        if (currentMode === 'csv' && csvTable) {{
          const rows = csvTable.getData();
          const fields = csvColumns.map(c => c.field);
          let changed = false;
          rows.forEach(row => {{
            fields.forEach(field => {{
              const val = String(row[field] ?? '');
              if (val.includes(q)) {{
                row[field] = val.split(q).join(rep);
                changed = true;
              }}
            }});
          }});
          if (changed) {{
            csvTable.replaceData(rows);
            markDirty();
          }}
          return;
        }}

        if (!editor) return;
        const cursor = editor.getSearchCursor(q, {{ line: 0, ch: 0 }});
        let changed = false;
        while (cursor.findNext()) {{
          cursor.replace(rep);
          changed = true;
        }}
        if (changed) markDirty();
      }}

      function addCsvRow() {{
        if (!csvTable) return;
        const row = {{ id: Date.now() }};
        csvColumns.forEach(c => {{ row[c.field] = ''; }});
        csvTable.addData([row]);
        markDirty();
      }}

      function addCsvCol() {{
        if (!csvTable) return;
        const idx = csvColumns.length;
        const newField = `c${{idx}}`;
        csvColumns.push({{ title: `C${{idx + 1}}`, field: newField, editor: 'input' }});

        const rows = csvTable.getData();
        rows.forEach(r => {{ r[newField] = ''; }});
        csvTable.setColumns(csvColumns);
        csvTable.replaceData(rows);
        markDirty();
      }}

      modeCodeBtn.addEventListener('click', () => setMode('code'));
      modeCsvBtn.addEventListener('click', () => setMode('csv'));
      modeViewBtn.addEventListener('click', () => setMode('view'));

      initEditor();

      if (!canWrite) {{
        if (editor) editor.setOption('readOnly', true);
        ['btn-save', 'btn-saveas', 'btn-rename', 'btn-delete', 'btn-replace', 'btn-replaceall', 'save-path', 'replace-input']
          .forEach(id => {{ const el = document.getElementById(id); if (el) el.classList.add('hidden'); }});
        const note = document.createElement('span');
        note.className = 'path';
        note.style.color = '#fa0';
        note.textContent = 'read-only (outside start dir)';
        document.querySelector('.toolbar').appendChild(note);
      }}

      if (!isCsvFile) modeCsvBtn.classList.add('hidden');
      if (!isMarkdownFile) modeViewBtn.classList.add('hidden');

      let initialMode = 'code';
      if (savedMode === 'csv' && isCsvFile) initialMode = 'csv';
      else if (savedMode === 'view' && isMarkdownFile) initialMode = 'view';
      else if (isMarkdownFile) initialMode = 'view';
      else if (isCsvFile) initialMode = 'csv';
      setMode(initialMode);

      window.saveFile = saveFile;
      window.saveAsFile = saveAsFile;
      window.renameMoveFile = renameMoveFile;
      window.deleteFile = deleteFile;
      window.findNext = findNext;
      window.replaceOne = replaceOne;
      window.replaceAll = replaceAll;
      window.addCsvRow = addCsvRow;
      window.addCsvCol = addCsvCol;
    </script>
    </body></html>"""

    response = HTMLResponse(html)
    _set_context_cookies(response, view="edit", dir_path=parent, file_path=rel)
    return response


async def _read_payload(request: Request) -> dict[str, str]:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        data = await request.json()
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
        return {}

    raw = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qs(raw, keep_blank_values=True)
    return {k: (v[-1] if v else "") for k, v in parsed.items()}


@app.post("/save", response_class=HTMLResponse)
async def save_file(path: str, request: Request) -> HTMLResponse:
    target = _safe(path)
    _require_writable(target)
    if target.exists() and not target.is_file():
        raise HTTPException(status_code=400, detail="Not a file")

    payload = await _read_payload(request)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload.get("content", ""))
    return HTMLResponse("ok")


@app.post("/save-as", response_class=HTMLResponse)
async def save_as(path: str, request: Request) -> HTMLResponse:
    _safe(path)
    payload = await _read_payload(request)
    new_path = payload.get("new_path", "").strip()
    if not new_path:
        raise HTTPException(status_code=400, detail="new_path is required")

    target = _safe(new_path)
    _require_writable(target)
    if target.exists() and target.is_dir():
        raise HTTPException(status_code=400, detail="Cannot overwrite a directory")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload.get("content", ""))
    return HTMLResponse("ok")


@app.post("/rename", response_class=HTMLResponse)
async def rename_file(path: str, request: Request) -> HTMLResponse:
    source = _safe(path)
    _require_writable(source)
    if not source.exists() or not source.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    payload = await _read_payload(request)
    new_path = payload.get("new_path", "").strip()
    if not new_path:
        raise HTTPException(status_code=400, detail="new_path is required")

    target = _safe(new_path)
    _require_writable(target)
    if target.exists() and target.is_dir():
        raise HTTPException(status_code=400, detail="Cannot overwrite a directory")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload.get("content", source.read_text(errors="replace")))

    if target.resolve() != source.resolve():
        source.unlink()

    return HTMLResponse("ok")


@app.post("/delete", response_class=HTMLResponse)
def delete_file(path: str) -> HTMLResponse:
    target = _safe(path)
    _require_writable(target)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    target.unlink()
    return HTMLResponse("ok")


@app.post("/create-file")
async def create_file(path: str, request: Request) -> dict[str, str]:
    directory = _safe(path)
    if not directory.exists() or not directory.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")
    _require_writable(directory)

    payload = await _read_payload(request)
    new_path = payload.get("new_path", "").strip()
    if not new_path:
        raise HTTPException(status_code=400, detail="new_path is required")

    target = _safe(str(Path(path) / new_path))
    _require_writable(target)
    if target.exists():
        raise HTTPException(status_code=400, detail="File already exists")
    if target.suffix == "" and new_path.endswith("/"):
        raise HTTPException(status_code=400, detail="Use a file path, not a directory")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("")
    rel = _rel(target)

    if "application/json" in request.headers.get("content-type", ""):
        return {"status": "ok", "path": rel}

    return RedirectResponse(url=f"/edit?path={_q(rel)}", status_code=303)


@app.post("/upload")
async def upload_file(request: Request, path: str = "", name: str = "") -> dict[str, str]:
    directory = _safe(path)
    if not directory.exists() or not directory.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")
    _require_writable(directory)

    filename = Path(name).name
    if not filename:
        raise HTTPException(status_code=400, detail="name is required")

    target = _safe(str(Path(path) / filename))
    _require_writable(target)
    if target.exists() and target.is_dir():
        raise HTTPException(status_code=400, detail="Cannot overwrite a directory")

    data = await request.body()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {"status": "ok", "path": _rel(target)}


@app.get("/raw")
def raw(path: str) -> FileResponse:
    target = _safe(path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(target)
