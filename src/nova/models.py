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


class ChatSessionRename(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class ChatSessionArchive(BaseModel):
    archived: bool = True


class ChatSession(BaseModel):
    id: str
    title: str
    workspace: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    # dsh fork 谱系：parent_session_id 指向 fork 来源会话；
    # seed_length 标记从父会话继承的事件边界（前 N 条是继承的，之后是子会话自己的）。
    parent_session_id: str | None = None
    seed_length: int | None = None
    archived: bool = False


class ChatMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=12000)


class ChatSessionFork(BaseModel):
    """会话 fork 请求（dsh session.fork 语义）。

    at_seq 指定在哪个事件边界切片；省略则取最后一个事件的 seq。
    边界不得落在未关闭的 turn 内（OPEN_TURN 校验）。
    """
    at_seq: int | None = Field(default=None, ge=0)


class WorkspaceSelect(BaseModel):
    path: str = Field(min_length=1, max_length=1200)


class WorkspaceFolderCreate(BaseModel):
    path: str = Field(min_length=1, max_length=1200)


class WorktreeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class ModelEntry(BaseModel):
    """单个模型条目（dsh DeepSeekModelDraft 语义：id 必填，name/context_window/max_tokens 可选）。

    id 是发给 API 的模型标识；name 是显示名（留空则回退 id）；
    context_window/max_tokens 为高级配置，留空时用 provider 默认值。
    """
    id: str = Field(min_length=1, max_length=80)
    name: str | None = Field(default=None, max_length=80)
    context_window: int | None = Field(default=None, ge=1, le=10_000_000)
    max_tokens: int | None = Field(default=None, ge=1, le=1_000_000)


class ProviderProfile(BaseModel):
    """一个供应商组（dsh 语义：provider = group，组内多个模型可选）。

    id 稳定（切换/密钥槽位的 key）；protocol 决定实例类（openai/anthropic）；
    models 是该组下用户维护的模型清单（dsh ModelListEditor：每行 id + 高级配置），
    composer 按组列出供选用。
    """
    id: str = Field(min_length=1, max_length=40)
    label: str = Field(default="", max_length=60)
    protocol: str = Field(default="openai", pattern="^(openai|anthropic)$")
    base_url: str = Field(default="", max_length=300)
    api_key_env: str = Field(default="API_KEY", max_length=60)
    models: list[ModelEntry] = Field(default_factory=list, max_length=200)


class CommentAnchor(BaseModel):
    """划词评论的锚点（W3C TextQuoteSelector 语义）。

    quote=选中原文；prefix/suffix=前后各约 32 字的上下文快照；
    occurrence=quote 在消息全文中第几次出现（0 基），用于消息内有
    重复文本时消歧。渲染端凭这四样在重排后的 markdown 里重新定位。
    """

    id: str = Field(min_length=1, max_length=64)
    session_id: str = Field(min_length=1, max_length=64)
    message_id: str = Field(min_length=1, max_length=64)
    quote: str = Field(min_length=1, max_length=2000)
    prefix: str = Field(default="", max_length=200)
    suffix: str = Field(default="", max_length=200)
    occurrence: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)


class CommentEntry(BaseModel):
    """评论线程里的一条消息（用户提问或助手回答）。"""

    id: str = Field(min_length=1, max_length=64)
    anchor_id: str = Field(min_length=1, max_length=64)
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(default="", max_length=20000)
    created_at: datetime = Field(default_factory=utc_now)


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
