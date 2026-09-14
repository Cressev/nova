from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Any
from uuid import uuid4

from pydantic import BaseModel, Field, StringConstraints


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class ChatRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    ERROR = "error"


class ChatSessionCreate(BaseModel):
    title: str | None = Field(default=None, max_length=120)


class ChatSession(BaseModel):
    id: str
    title: str
    workspace: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ChatMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=12000)


class WorkspaceSelect(BaseModel):
    path: str = Field(min_length=1, max_length=1200)


class WorkspaceFolderCreate(BaseModel):
    path: str = Field(min_length=1, max_length=1200)


class WorktreeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class ProviderProfile(BaseModel):
    """一个供应商组（dsh 语义：provider = group，组内多个模型可选）。

    id 稳定（切换/密钥槽位的 key）；protocol 决定实例类（openai/anthropic）；
    models 是该组下用户维护的模型清单，composer 按组列出供选用。
    """
    id: str = Field(min_length=1, max_length=40)
    label: str = Field(default="", max_length=60)
    protocol: str = Field(default="openai", pattern="^(openai|anthropic)$")
    base_url: str = Field(default="", max_length=300)
    api_key_env: str = Field(default="API_KEY", max_length=60)
    models: list[Annotated[str, StringConstraints(min_length=1, max_length=80)]] = Field(
        default_factory=list, max_length=200
    )


class RuntimeConfigUpdate(BaseModel):
    provider_preset: str | None = Field(default=None, min_length=1, max_length=40)
    provider_model: str | None = Field(default=None, min_length=1, max_length=80)
    provider_base_url: str | None = Field(default=None, min_length=1, max_length=300)
    # 用户手动维护的模型清单（dsh ModelListEditor 语义：行可增删；
    # 获取候选→勾选→"添加所选"也写到这里）。当前模型 = provider_model，
    # 下拉的完整选项 = 当前模型 ∪ custom_models。
    custom_models: list[Annotated[str, StringConstraints(min_length=1, max_length=80)]] | None = Field(
        default=None, max_length=60
    )
    # 多供应商组（dsh groups 语义）：每个 profile 自带端点/协议/密钥/模型组；
    # active_provider_id 指向当前启用的组，provider 实例按该组重建。
    provider_profiles: list[ProviderProfile] | None = None
    active_provider_id: str | None = Field(default=None, min_length=1, max_length=40)
    context_window_tokens: int | None = Field(default=None, ge=8192, le=1000000)
    permission_mode: str | None = Field(default=None, pattern="^(read_only|ask|workspace_write|default|plan|accept_edits|dont_ask|bypass_permissions)$")
    sandbox_mode: str | None = Field(default=None, pattern="^(read_only|workspace_write|danger_full_access)$")
    approval_policy: str | None = Field(default=None, pattern="^(untrusted|on_failure|on_request|never|granular)$")
    network_access: bool | None = None
    max_tool_rounds: int | None = Field(default=None, ge=1, le=12)


class RuntimeSecretUpdate(BaseModel):
    bigmodel_api_key: str | None = Field(default=None, max_length=3000)
    # 多供应商组：密钥写到哪组（dsh 语义：每组独立密钥槽）。
    profile_id: str | None = Field(default=None, min_length=1, max_length=40)
    langfuse_public_key: str | None = Field(default=None, max_length=3000)
    langfuse_secret_key: str | None = Field(default=None, max_length=3000)
    langfuse_host: str | None = Field(default=None, max_length=300)
    langfuse_enabled: bool | None = None


class ChatMessage(BaseModel):
    id: str = Field(default_factory=lambda: new_id("msg"))
    session_id: str
    role: ChatRole
    content: str
    created_at: datetime = Field(default_factory=utc_now)


class ChatEvent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("evt"))
    session_id: str
    type: str
    event_type: str | None = None
    phase: str | None = None
    turn_id: str | None = None
    sequence: int | None = None
    status: str = "ok"
    title: str
    message: str | None = None
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    output: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    parallel: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Health(BaseModel):
    ok: bool
    service: str
    version: str


class GitFileStatus(BaseModel):
    path: str
    status: str


class GitStatus(BaseModel):
    available: bool
    branch: str | None = None
    dirty_count: int = 0
    files: list[GitFileStatus] = Field(default_factory=list)
    partial: bool = False


class WorkspaceMode(BaseModel):
    id: str
    label: str
    enabled: bool
    description: str


class WorkspacePermissions(BaseModel):
    workspace_write: bool
    network_access: bool
    approval_policy: str
    permission_mode: str = "workspace_write"
    sandbox_mode: str = "workspace_write"
    approval_policy_id: str = "on_request"
    shell_commands: bool = True


class WorkspaceCommands(BaseModel):
    test: str
    serve: str


class WorkspaceStatus(BaseModel):
    project_root: str
    git: GitStatus
    modes: list[WorkspaceMode]
    permissions: WorkspacePermissions
    commands: WorkspaceCommands
