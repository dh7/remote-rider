import json
import os
import shutil
import subprocess
import time
from typing import Any
from urllib.request import Request as UrlRequest, urlopen

from config import HERE
from host_utils import _is_local_host, _is_port_busy_for_bind, _normalize_host


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _run_docker(args: list[str], timeout: int = 30) -> dict[str, Any]:
    if not _docker_available():
        return {"ok": False, "stdout": "", "stderr": "docker not found", "returncode": 127}
    try:
        result = subprocess.run(
            ["docker", *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": "docker command timed out", "returncode": -1}


def _sandbox_image_exists(image: str) -> bool:
    return _run_docker(["image", "inspect", image])["ok"]


def _build_sandbox_image(tag: str = "claude-sandbox:latest") -> dict[str, Any]:
    dockerfile = HERE / "Dockerfile.sandbox"
    if not dockerfile.exists():
        return {"ok": False, "stderr": f"Dockerfile.sandbox not found at {dockerfile}"}
    result = _run_docker(
        ["build", "-t", tag, "-f", str(dockerfile), str(HERE)],
        timeout=600,
    )
    return {"ok": result["ok"], "stderr": result["stderr"][:400]}


def _pick_sandbox_port(start: int = 7700) -> int:
    port = start
    while _is_port_busy_for_bind(port):
        port += 1
    return port


def _parse_docker_labels(raw: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    for entry in raw.split(","):
        if "=" in entry:
            k, _, v = entry.partition("=")
            labels[k.strip()] = v.strip()
    return labels


def list_sandboxes() -> list[dict[str, Any]]:
    result = _run_docker([
        "ps", "-a",
        "--filter", "label=remote-rider.sandbox=1",
        "--format", "{{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Labels}}",
    ])
    if not result["ok"] or not result["stdout"]:
        return []

    sandboxes = []
    for line in result["stdout"].splitlines():
        parts = line.split("\t", 3)
        if len(parts) < 3:
            continue
        container_id, name, status = parts[0], parts[1], parts[2]
        labels = _parse_docker_labels(parts[3] if len(parts) > 3 else "")
        ttyd_port_raw = labels.get("remote-rider.ttyd_port", "0")
        sandboxes.append({
            "id": container_id,
            "name": name,
            "status": status,
            "running": status.lower().startswith("up"),
            "branch": labels.get("remote-rider.branch", ""),
            "repo": labels.get("remote-rider.repo", ""),
            "ttyd_port": int(ttyd_port_raw) if ttyd_port_raw.isdigit() else None,
        })
    return sandboxes


def create_sandbox(
    branch: str,
    repo_url: str = "",
    local_path: str = "",
    auth_path: str = "",
    image: str = "claude-sandbox:latest",
) -> dict[str, Any]:
    if not _docker_available():
        return {"status": "error", "reason": "docker not available on this machine"}

    if not repo_url and not local_path:
        return {"status": "error", "reason": "provide either repo_url or local_path"}

    if not _sandbox_image_exists(image):
        build = _build_sandbox_image(image)
        if not build["ok"]:
            return {"status": "error", "reason": f"image build failed: {build.get('stderr', '')[:200]}"}

    if not auth_path:
        auth_path = os.path.expanduser("~/.claude")

    ttyd_port = _pick_sandbox_port(7700)
    safe_branch = branch.replace("/", "-").replace(" ", "-")[:60]
    container_name = f"claude-sandbox-{safe_branch}-{int(time.time()) % 100000}"
    source_label = local_path or repo_url

    args = [
        "run", "-d",
        "--name", container_name,
        "-p", f"{ttyd_port}:7681",
        "-e", f"BRANCH={branch}",
        "-e", "TERM=xterm-256color",
        "--label", "remote-rider.sandbox=1",
        f"--label=remote-rider.branch={branch}",
        f"--label=remote-rider.repo={source_label}",
        f"--label=remote-rider.ttyd_port={ttyd_port}",
    ]

    if local_path:
        # Mount the existing repo directly — preserves git history, config, Claude memory
        expanded = os.path.expanduser(local_path)
        args += ["-v", f"{expanded}:/workspace"]
    else:
        args += ["-e", f"REPO_URL={repo_url}"]

    if os.path.isdir(auth_path):
        args += ["-v", f"{auth_path}:/root/.claude"]

    args.append(image)

    result = _run_docker(args, timeout=60)
    if not result["ok"]:
        return {"status": "error", "reason": result["stderr"][:300]}

    return {
        "status": "ok",
        "container_id": result["stdout"][:12],
        "name": container_name,
        "ttyd_port": ttyd_port,
        "branch": branch,
        "repo": source_label,
    }


def stop_sandbox(container_id: str) -> dict[str, Any]:
    stop = _run_docker(["stop", container_id], timeout=15)
    rm = _run_docker(["rm", container_id], timeout=10)
    return {"ok": stop["ok"], "removed": rm["ok"]}


def clone_sandbox(container_id: str, new_branch: str) -> dict[str, Any]:
    tag = f"claude-sandbox-clone-{int(time.time()) % 100000}"
    safe_branch = new_branch.replace("/", "-").replace(" ", "-")[:60]
    new_name = f"claude-sandbox-{safe_branch}-{int(time.time()) % 100000}"
    ttyd_port = _pick_sandbox_port(7700)

    commit = _run_docker(["commit", container_id, tag], timeout=60)
    if not commit["ok"]:
        return {"status": "error", "reason": f"commit failed: {commit['stderr'][:200]}"}

    args = [
        "run", "-d",
        "--name", new_name,
        "-p", f"{ttyd_port}:7681",
        "-e", f"BRANCH={new_branch}",
        "-e", "TERM=xterm-256color",
        "--label", "remote-rider.sandbox=1",
        f"--label=remote-rider.branch={new_branch}",
        f"--label=remote-rider.ttyd_port={ttyd_port}",
        tag,
    ]
    run = _run_docker(args, timeout=30)
    _run_docker(["rmi", tag], timeout=10)

    if not run["ok"]:
        return {"status": "error", "reason": f"run failed: {run['stderr'][:200]}"}

    return {
        "status": "ok",
        "container_id": run["stdout"][:12],
        "name": new_name,
        "ttyd_port": ttyd_port,
        "branch": new_branch,
    }


def _proxy_post(host: str, hub_port: int, path: str, body: dict, timeout: int = 90) -> dict[str, Any]:
    raw = json.dumps(body).encode()
    req = UrlRequest(
        f"http://{host}:{hub_port}{path}",
        data=raw,
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}


def _proxy_get(host: str, hub_port: int, path: str, timeout: int = 15) -> dict[str, Any]:
    req = UrlRequest(f"http://{host}:{hub_port}{path}", method="GET")
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:
        return {"status": "error", "reason": str(exc), "sandboxes": []}


def create_sandbox_proxy(host: str, hub_port: int, branch: str, repo_url: str = "", local_path: str = "", auth_path: str = "", image: str = "claude-sandbox:latest") -> dict[str, Any]:
    cleaned = _normalize_host(host)
    if _is_local_host(cleaned):
        return create_sandbox(branch=branch, repo_url=repo_url, local_path=local_path, auth_path=auth_path, image=image)
    return _proxy_post(cleaned, hub_port, "/sandbox/create", {
        "branch": branch, "repo_url": repo_url, "local_path": local_path, "auth_path": auth_path, "image": image,
    }, timeout=120)


def list_sandboxes_proxy(host: str, hub_port: int) -> dict[str, Any]:
    cleaned = _normalize_host(host)
    if _is_local_host(cleaned):
        return {"sandboxes": list_sandboxes()}
    return _proxy_get(cleaned, hub_port, "/sandbox/list")


def stop_sandbox_proxy(host: str, hub_port: int, container_id: str) -> dict[str, Any]:
    cleaned = _normalize_host(host)
    if _is_local_host(cleaned):
        return stop_sandbox(container_id)
    return _proxy_post(cleaned, hub_port, "/sandbox/stop", {"container_id": container_id})


def clone_sandbox_proxy(host: str, hub_port: int, container_id: str, new_branch: str) -> dict[str, Any]:
    cleaned = _normalize_host(host)
    if _is_local_host(cleaned):
        return clone_sandbox(container_id, new_branch)
    return _proxy_post(cleaned, hub_port, "/sandbox/clone", {
        "container_id": container_id, "new_branch": new_branch,
    }, timeout=120)
