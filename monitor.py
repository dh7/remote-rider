import datetime

import psutil
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI()

STYLE = """
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #111; color: #eee; font-family: monospace; padding: 1rem; }
  h2 { color: #88f; margin-bottom: 0.75rem; font-size: 1rem; }
  table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
  th { color: #aaa; text-align: left; padding: 0.3rem 0.5rem; border-bottom: 1px solid #333; }
  td { padding: 0.25rem 0.5rem; }
  tr:hover td { background: #1a1a3a; }
  .bar { display: inline-block; background: #44f; height: 8px; min-width: 2px; }
  .updated { color: #555; font-size: 0.75rem; margin-top: 0.5rem; }
</style>
"""


def cpu_bar(pct: float) -> str:
    w = int(pct * 1.2)
    return f'<span class="bar" style="width:{w}px"></span> {pct:.1f}%'


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    procs = sorted(
        psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "status"]),
        key=lambda p: p.info["cpu_percent"] or 0,
        reverse=True,
    )[:25]

    rows = ""
    for p in procs:
        i = p.info
        rows += (
            "<tr>"
            f"<td>{i['pid']}</td>"
            f"<td>{i['name'][:30]}</td>"
            f"<td>{cpu_bar(i['cpu_percent'] or 0)}</td>"
            f"<td>{i['memory_percent']:.1f}%</td>"
            f"<td>{i['status']}</td>"
            "</tr>"
        )

    ts = datetime.datetime.now().strftime("%H:%M:%S")
    return f"""<!DOCTYPE html>
<html><head>{STYLE}<meta http-equiv="refresh" content="3"></head><body>
<h2>Process Monitor</h2>
<table>
  <thead><tr><th>PID</th><th>Name</th><th>CPU</th><th>MEM</th><th>Status</th></tr></thead>
  <tbody>{rows}</tbody>
</table>
<div class="updated">Updated {ts} - refreshes every 3s</div>
</body></html>"""
