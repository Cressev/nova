from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


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
    status: str = "ok"
    title: str
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    output: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    parallel: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class TaskCreate(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)
    workspace: str | None = None


class Task(BaseModel):
    id: str
    prompt: str
    workspace: str
    status: TaskStatus = TaskStatus.QUEUED
    summary: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class TimelineEvent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("evt"))
    task_id: str
    type: str
    title: str
    message: str
    status: str = "ok"
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


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
