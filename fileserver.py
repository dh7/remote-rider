from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

app = FastAPI()
ROOT = Path.home()

STYLE = """
<style>
  body { font-family: monospace; background: #111; color: #eee; padding: 1rem; }
  a    { color: #7af; text-decoration: none; display: block; padding: 2px 0; }
  a:hover { color: #fff; }
  .crumbs { white-space: nowrap; overflow-x: auto; overflow-y: hidden; }
  .crumbs a { display: inline; padding: 0; }
  .dir  { color: #fa0; }
  .dir-hidden { color: #666; }
  .img  { color: #af7; }
  .file { color: #fff; }
  .file-hidden { color: #666; }
  textarea { width:100%; height:80vh; background:#1a1a1a; color:#eee;
             border:1px solid #444; font-family:monospace; padding:0.5rem; }
  button { background:#4a4a8a; color:#fff; border:none; padding:0.5rem 1rem;
           cursor:pointer; margin-top:0.5rem; }
</style>
"""


def breadcrumbs(path: str) -> str:
    parts = Path(path).parts if path else []
    crumbs = '<a href="/files?path=">home</a> '
    accumulated = ""
    for part in parts:
        accumulated = str(Path(accumulated) / part)
        crumbs += f'/ <a href="/files?path={accumulated}">{part}</a> '
    return f"<div class=\"crumbs\">{crumbs}</div><hr>"


@app.get("/files", response_class=HTMLResponse)
def browse(path: str = "") -> str:
    target = ROOT / path
    if not target.exists():
        raise HTTPException(status_code=404, detail="Not found")

    entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name))
    items = breadcrumbs(path)

    if path:
        parent = str(Path(path).parent) if Path(path).parent != Path(path) else ""
        items += f'<a class="dir" href="/files?path={parent}">..</a>'

    for entry in entries:
        try:
            rel = str(entry.relative_to(ROOT))
        except ValueError:
            continue

        if entry.is_dir():
            dir_class = "dir-hidden" if entry.name.startswith(".") else "dir"
            items += f'<a class="{dir_class}" href="/files?path={rel}">[dir] {entry.name}</a>'
        elif entry.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}:
            items += f'<a class="img" href="/img?path={rel}" target="_blank">[img] {entry.name}</a>'
        else:
            file_class = "file-hidden" if entry.name.startswith(".") else "file"
            items += f'<a class="{file_class}" href="/edit?path={rel}">{entry.name}</a>'

    return f"<html><head>{STYLE}</head><body>{items}</body></html>"


@app.get("/img", response_class=HTMLResponse)
def view_image(path: str) -> str:
    return f"""<html><head>{STYLE}</head><body style="margin:0;background:#000">
    <img src="/raw?path={path}" style="max-width:100%;max-height:100vh;display:block;margin:auto">
    </body></html>"""


@app.get("/edit", response_class=HTMLResponse)
def edit_file(path: str) -> str:
    content = (ROOT / path).read_text(errors="replace")
    escaped = content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<html><head>{STYLE}</head><body>
    <a href="/files?path={str(Path(path).parent)}">back</a>
    <h4 style="color:#eee">{path}</h4>
    <form method="post" action="/save?path={path}">
      <textarea name="content">{escaped}</textarea><br>
      <button type="submit">Save</button>
    </form></body></html>"""


@app.post("/save", response_class=HTMLResponse)
async def save_file(path: str, request: Request) -> HTMLResponse:
    form = await request.form()
    (ROOT / path).write_text(form["content"])
    parent = str(Path(path).parent)
    return HTMLResponse(f'<meta http-equiv="refresh" content="0;url=/files?path={parent}">')


@app.get("/raw")
def raw(path: str) -> FileResponse:
    return FileResponse(ROOT / path)
