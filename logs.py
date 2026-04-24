import subprocess
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse

app = FastAPI()


def default_log_path() -> str:
    candidates = [
        "/var/log/syslog",
        "/var/log/messages",
        "/private/var/log/system.log",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    return candidates[0]


DEFAULT_LOG = default_log_path()

STYLE = """
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #111; color: #eee; font-family: monospace;
         display: flex; flex-direction: column; height: 100vh; }
  #toolbar { padding: 0.5rem; background: #1a1a1a; border-bottom: 1px solid #333;
             display: flex; gap: 0.5rem; align-items: center; }
  #toolbar label { color: #aaa; font-size: 0.8rem; }
  #toolbar input { flex: 1; background: #0d0d0d; color: #eee; border: 1px solid #444;
                   padding: 0.3rem 0.5rem; font-family: monospace; font-size: 0.85rem; }
  #toolbar button { background: #2a2a6a; color: #eee; border: none; padding: 0.3rem 0.8rem;
                    cursor: pointer; font-family: monospace; font-size: 0.85rem; }
  #log { flex: 1; overflow-y: auto; padding: 0.5rem; font-size: 0.8rem; line-height: 1.5; }
  .line { white-space: pre-wrap; word-break: break-all; }
  .line:hover { background: #1a1a1a; }
</style>
"""


@app.get("/", response_class=HTMLResponse)
def index(path: str = DEFAULT_LOG) -> str:
    return f"""<!DOCTYPE html>
<html><head>{STYLE}</head><body>
<div id="toolbar">
  <label>File:</label>
  <input id="path" value="{path}">
  <button onclick="reload()">Tail</button>
</div>
<div id="log"></div>
<script>
  let es = null;
  function reload() {{
    const path = document.getElementById('path').value;
    if (es) es.close();
    document.getElementById('log').innerHTML = '';
    es = new EventSource('/stream?path=' + encodeURIComponent(path));
    es.onmessage = function(e) {{
      const log = document.getElementById('log');
      const div = document.createElement('div');
      div.className = 'line';
      div.textContent = e.data;
      log.appendChild(div);
      log.scrollTop = log.scrollHeight;
    }};
  }}
  reload();
</script>
</body></html>"""


@app.get("/stream")
def stream(path: str = DEFAULT_LOG) -> StreamingResponse:
    def generate():
        try:
            proc = subprocess.Popen(
                ["tail", "-f", "-n", "100", path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            for line in proc.stdout:
                yield f"data: {line.rstrip()}\n\n"
        except Exception as exc:  # pragma: no cover
            yield f"data: [error] {exc}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
