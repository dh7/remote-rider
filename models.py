from typing import Any, Literal

from pydantic import BaseModel, Field


class TabRequest(BaseModel):
    server: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=80)
    port: int = Field(ge=1, le=65535)
    path: str = "/"
    ip: str | None = None
    protocol: str = "http"


class RemoteRequest(BaseModel):
    action: Literal["add", "remove", "remove_kill"] = "add"
    name: str = Field(min_length=1, max_length=80)
    display: str | None = None
    ip: str | None = None
    base: str | None = "netochka"
    position: Literal["top", "bottom"] | None = None
    terminal_session: str | None = None


class ReorderRequest(BaseModel):
    order: list[str]


class TmuxKillRequest(BaseModel):
    host: str
    session: str = Field(min_length=1, max_length=120)
    port: int = Field(default=7000, ge=1, le=65535)


class StartFilesServiceRequest(BaseModel):
    port: int | None = Field(default=None, ge=1, le=65535)


class StartFilesServiceProxyRequest(BaseModel):
    host: str
    hub_port: int = Field(default=7000, ge=1, le=65535)
    port: int | None = Field(default=None, ge=1, le=65535)


class SessionsPutRequest(BaseModel):
    sessions: list[dict[str, Any]]


class SessionTabUpsertRequest(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    tab_id: str | None = Field(default=None, min_length=1, max_length=120)
    service: str | None = Field(default=None, min_length=1, max_length=80)
    port: int | None = Field(default=None, ge=1, le=65535)
    path: str = "/"
    protocol: str = "http"
    machine_name: str | None = Field(default=None, min_length=1, max_length=120)
    machine_host: str | None = Field(default=None, min_length=1, max_length=255)
    activate: bool = False


class SessionTabDeleteRequest(BaseModel):
    tab_id: str | None = Field(default=None, min_length=1, max_length=120)
    label: str | None = Field(default=None, min_length=1, max_length=120)


class AgentStartRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    command: str = Field(min_length=1, max_length=4000)
    cwd: str | None = Field(default=None, min_length=1, max_length=2000)
    tmux_session: str | None = Field(default=None, min_length=1, max_length=120)
    session_name: str | None = Field(default=None, min_length=1, max_length=120)
    machine_host: str | None = Field(default=None, min_length=1, max_length=255)


class AgentStopRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class AgentStartProxyRequest(AgentStartRequest):
    host: str
    hub_port: int = Field(default=7000, ge=1, le=65535)


class AgentStopProxyRequest(AgentStopRequest):
    host: str
    hub_port: int = Field(default=7000, ge=1, le=65535)


class RemoteUpdateRequest(BaseModel):
    branch: str = Field(default="main", min_length=1, max_length=120)


class RemoteUpdateProxyRequest(RemoteUpdateRequest):
    host: str
    hub_port: int = Field(default=7000, ge=1, le=65535)


class UpdateAllRemotesRequest(RemoteUpdateRequest):
    machines: list[str] | None = None


class RemoteGitCheckProxyRequest(BaseModel):
    host: str
    hub_port: int = Field(default=7000, ge=1, le=65535)
    branch: str = Field(default="main", min_length=1, max_length=120)


class SandboxCreateRequest(BaseModel):
    repo_url: str = ""
    local_path: str = ""
    branch: str = Field(default="main", min_length=1, max_length=200)
    auth_path: str = ""
    image: str = "claude-sandbox:latest"


class SandboxCreateProxyRequest(SandboxCreateRequest):
    host: str
    hub_port: int = Field(default=7000, ge=1, le=65535)


class SandboxStopRequest(BaseModel):
    container_id: str = Field(min_length=1)


class SandboxStopProxyRequest(SandboxStopRequest):
    host: str
    hub_port: int = Field(default=7000, ge=1, le=65535)


class SandboxCloneRequest(BaseModel):
    container_id: str = Field(min_length=1)
    new_branch: str = Field(min_length=1, max_length=200)


class SandboxCloneProxyRequest(SandboxCloneRequest):
    host: str
    hub_port: int = Field(default=7000, ge=1, le=65535)


class SandboxBranchRequest(BaseModel):
    container_id: str = Field(min_length=1)
    new_branch: str = Field(min_length=1, max_length=200)


class SandboxBranchProxyRequest(SandboxBranchRequest):
    host: str
    hub_port: int = Field(default=7000, ge=1, le=65535)
