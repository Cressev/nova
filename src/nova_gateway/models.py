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
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ChatMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=12000)


class ChatMessage(BaseModel):
    id: str = Field(default_factory=lambda: new_id("msg"))
    session_id: str
    role: ChatRole
    content: str
    created_at: datetime = Field(default_factory=utc_now)


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
