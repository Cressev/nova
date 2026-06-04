from __future__ import annotations

import asyncio
import json
import subprocess
from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .agent_runtime import CodexLikeAgentRuntime
from .agent_tools import WorkspaceTools
from .models import (
    ChatMessage,
    ChatMessageCreate,
    ChatRole,
    ChatSession,
    ChatSessionCreate,
    Health,
    Task,
    TaskCreate,
    TimelineEvent,
    GitFileStatus,
    GitStatus,
    WorkspaceCommands,
    WorkspaceMode,
    WorkspacePermissions,
    WorkspaceSelect,
    WorkspaceStatus,
    new_id,
)
from .provider import BigModelProvider, ProviderError
from .runtime import DemoAgentRuntime
from .settings import load_settings
from .store import TaskStore
from .workspace import WorkspaceError, WorkspaceManager

settings = load_settings()
store = TaskStore(settings.state_dir)
workspace_manager = WorkspaceManager(
    initial_root=settings.initial_workspace_root,
    allowed_roots=settings.allowed_workspace_roots,
)
provider = BigModelProvider(
    base_url=settings.provider_base_url,
    model=settings.provider_model,
)

app = FastAPI(title="Nova Gateway", version=__version__)
app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")


def _workspace_tools() -> WorkspaceTools:
    return WorkspaceTools(
        workspace_manager.current_root,
        permission_mode=settings.permission_mode,
    )


def _agent_runtime() -> CodexLikeAgentRuntime:
    return CodexLikeAgentRuntime(
        provider=provider,
        project_root=workspace_manager.current_root,
        global_agent_file=settings.global_agent_file,
        max_tool_rounds=settings.max_tool_rounds,
        permission_mode=settings.permission_mode,
    )


def _demo_runtime() -> DemoAgentRuntime:
    return DemoAgentRuntime(store=store, project_root=workspace_manager.current_root)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(settings.static_dir / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    return Response(status_code=204)


@app.get("/api/health", response_model=Health)
async def health() -> Health:
    return Health(ok=True, service="nova-gateway", version=__version__)


@app.get("/api/provider")
async def provider_status() -> dict:
    return {
        "provider": "bigmodel",
        "model": provider.model,
        "base_url": provider.base_url,
        "configured": provider.is_configured(),
        "api_key_env": provider.api_key_env,
    }


@app.get("/api/runtime/config")
async def runtime_config() -> dict:
    return {
        "model": provider.model,
        "base_url": provider.base_url,
        "permission_mode": settings.permission_mode,
        "network_access": settings.network_access,
        "max_tool_rounds": settings.max_tool_rounds,
        "worktree_enabled": False,
        "approval_ui_enabled": False,
        "tool_parallel_readonly": True,
        "memory_enabled": True,
    }


@app.get("/api/tools")
async def tool_list() -> dict:
    return {"items": _workspace_tools().list_specs()}


@app.get("/api/memory/status")
async def memory_status() -> dict:
    return _agent_runtime().memory.status()


@app.get("/api/workspaces")
async def workspace_list() -> dict:
    return workspace_manager.status()


@app.post("/api/workspace/select")
async def select_workspace(payload: WorkspaceSelect) -> dict:
    try:
        workspace_manager.set_current(payload.path)
        return workspace_manager.status()
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/workspace/status", response_model=WorkspaceStatus)
async def workspace_status() -> WorkspaceStatus:
    return WorkspaceStatus(
        project_root=str(workspace_manager.current_root),
        git=_read_git_status(),
        modes=[
            WorkspaceMode(
                id="local",
                label="本地",
                enabled=True,
                description="直接在当前项目目录中工作。",
            ),
            WorkspaceMode(
                id="worktree",
                label="工作树",
                enabled=False,
                description="隔离变更，后续版本实现。",
            ),
            WorkspaceMode(
                id="cloud",
                label="云端",
                enabled=False,
                description="远程环境，后续版本实现。",
            ),
        ],
        permissions=WorkspacePermissions(
            workspace_write=settings.permission_mode == "workspace_write",
            network_access=settings.network_access,
            approval_policy=(
                "自动允许工作区写入"
                if settings.permission_mode == "workspace_write"
                else "需要审批" if settings.permission_mode == "ask" else "只读"
            ),
            permission_mode=settings.permission_mode,
            shell_commands=settings.permission_mode == "workspace_write",
        ),
        commands=WorkspaceCommands(
            test="PYTHONPATH=src python3 -m unittest discover -s tests",
            serve=(
                "PYTHONPATH=src python3 -m nova_gateway.cli serve "
                "--host 127.0.0.1 --port 8765"
            ),
        ),
    )


def _read_git_status() -> GitStatus:
    # 这里只读 Git 状态，避免在状态刷新时产生 stage/revert/push 等副作用。
    try:
        branch = _git(["branch", "--show-current"]).strip() or None
        porcelain = _git(["-c", "core.quotepath=false", "status", "--porcelain=v1"])
    except (OSError, subprocess.CalledProcessError):
        return GitStatus(available=False)

    files: list[GitFileStatus] = []
    for line in porcelain.splitlines():
        if not line:
            continue
        status = line[:2].strip() or "?"
        path = line[3:].strip()
        files.append(GitFileStatus(path=path, status=status))
    return GitStatus(
        available=True,
        branch=branch,
        dirty_count=len(files),
        files=files[:40],
    )


def _git(args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=workspace_manager.current_root,
        check=True,
        capture_output=True,
        text=True,
        timeout=3,
    )
    return result.stdout


@app.get("/api/chat/sessions", response_model=list[ChatSession])
async def list_chat_sessions() -> list[ChatSession]:
    return store.list_chat_sessions()


@app.post("/api/chat/sessions", response_model=ChatSession, status_code=201)
async def create_chat_session(payload: ChatSessionCreate) -> ChatSession:
    session = ChatSession(id=new_id("chat"), title=payload.title or "新对话")
    store.create_chat_session(session)
    return session


@app.get("/api/chat/sessions/{session_id}/messages", response_model=list[ChatMessage])
async def list_chat_messages(session_id: str) -> list[ChatMessage]:
    if store.get_chat_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return store.list_chat_messages(session_id)


@app.post("/api/chat/sessions/{session_id}/messages", response_model=ChatMessage)
async def create_chat_message(session_id: str, payload: ChatMessageCreate) -> ChatMessage:
    session = store.get_chat_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Chat session not found")

    user_message = ChatMessage(
        session_id=session_id,
        role=ChatRole.USER,
        content=payload.content,
    )
    store.add_chat_message(user_message)

    # 对话是用户主入口；task 是内部运行记录，用来复用已有 trace 和事件时间线。
    task = store.create_task(
        Task(
            id=new_id("task"),
            prompt=payload.content,
            workspace=str(workspace_manager.current_root),
        )
    )
    store.add_event(
        TimelineEvent(
            task_id=task.id,
            type="chat_message_received",
            title="收到用户消息",
            message="Nova 正在调用 GLM-4.7 生成回复。",
            data={"session_id": session_id},
        )
    )

    # 系统提示每次动态拼接，不落盘，避免后续记忆污染或难以调整。
    messages = [
        ChatMessage(
            session_id=session_id,
            role=ChatRole.SYSTEM,
            content=(
                "你是 Nova，一个本地优先的个人开发 Agent。"
                "请用中文回答，保持直接、务实，并优先帮助用户推进软件开发任务。"
            ),
        ),
        *store.list_chat_messages(session_id),
    ]

    try:
        answer = await provider.complete(messages)
        assistant_message = ChatMessage(
            session_id=session_id,
            role=ChatRole.ASSISTANT,
            content=answer,
        )
        store.add_chat_message(assistant_message)
        store.add_event(
            TimelineEvent(
                task_id=task.id,
                type="assistant_message_completed",
                title="GLM-4.7 回复完成",
                message="模型已返回 assistant 消息。",
                data={"session_id": session_id, "message_id": assistant_message.id},
            )
        )
        return assistant_message
    except ProviderError as exc:
        error_message = ChatMessage(
            session_id=session_id,
            role=ChatRole.ERROR,
            content=str(exc),
        )
        store.add_chat_message(error_message)
        store.add_event(
            TimelineEvent(
                task_id=task.id,
                type="assistant_message_failed",
                title="GLM-4.7 调用失败",
                message=str(exc),
                status="error",
                data={"session_id": session_id},
            )
        )
        return error_message


@app.post("/api/chat/sessions/{session_id}/stream")
async def stream_chat_message(
    session_id: str,
    payload: ChatMessageCreate,
) -> StreamingResponse:
    if store.get_chat_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Chat session not found")

    async def emit() -> AsyncIterator[str]:
        user_message = ChatMessage(
            session_id=session_id,
            role=ChatRole.USER,
            content=payload.content,
        )
        store.add_chat_message(user_message)
        yield _ndjson({"type": "user_message", "message": user_message.model_dump(mode="json")})

        task = store.create_task(
            Task(
                id=new_id("task"),
                prompt=payload.content,
                workspace=str(workspace_manager.current_root),
            )
        )
        store.add_event(
            TimelineEvent(
                task_id=task.id,
                type="chat_stream_started",
                title="开始流式回复",
                message="Nova 正在调用 GLM-4.7 生成流式回复。",
                data={"session_id": session_id},
            )
        )

        answer_parts: list[str] = []
        try:
            async for event in _agent_runtime().stream(store.list_chat_messages(session_id)):
                if event["type"] == "assistant_delta":
                    answer_parts.append(event["delta"])
                    yield _ndjson(event)
                    continue
                if event["type"] == "assistant_done_content":
                    if not answer_parts and event.get("content"):
                        answer_parts.append(event["content"])
                    continue
                yield _ndjson(event)

            assistant_message = ChatMessage(
                session_id=session_id,
                role=ChatRole.ASSISTANT,
                content="".join(answer_parts),
            )
            store.add_chat_message(assistant_message)
            store.add_event(
                TimelineEvent(
                    task_id=task.id,
                    type="assistant_stream_completed",
                    title="流式回复完成",
                    message="GLM-4.7 已完成本次流式回复。",
                    data={"session_id": session_id, "message_id": assistant_message.id},
                )
            )
            yield _ndjson(
                {
                    "type": "assistant_done",
                    "message": assistant_message.model_dump(mode="json"),
                }
            )
        except ProviderError as exc:
            error_message = ChatMessage(
                session_id=session_id,
                role=ChatRole.ERROR,
                content=str(exc),
            )
            store.add_chat_message(error_message)
            store.add_event(
                TimelineEvent(
                    task_id=task.id,
                    type="assistant_stream_failed",
                    title="流式回复失败",
                    message=str(exc),
                    status="error",
                    data={"session_id": session_id},
                )
            )
            yield _ndjson({"type": "error", "message": error_message.model_dump(mode="json")})
        except Exception as exc:
            detail = str(exc) or repr(exc)
            error_message = ChatMessage(
                session_id=session_id,
                role=ChatRole.ERROR,
                content=f"Nova 运行时异常：{type(exc).__name__}: {detail}",
            )
            store.add_chat_message(error_message)
            store.add_event(
                TimelineEvent(
                    task_id=task.id,
                    type="assistant_runtime_failed",
                    title="运行时异常",
                    message=detail,
                    status="error",
                    data={"session_id": session_id},
                )
            )
            yield _ndjson({"type": "error", "message": error_message.model_dump(mode="json")})

    return StreamingResponse(emit(), media_type="application/x-ndjson")


def _ndjson(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


@app.get("/api/tasks", response_model=list[Task])
async def list_tasks() -> list[Task]:
    return store.list_tasks()


@app.post("/api/tasks", response_model=Task, status_code=201)
async def create_task(payload: TaskCreate) -> Task:
    task = store.create_task(
        Task(
            id=new_id("task"),
            prompt=payload.prompt,
            workspace=payload.workspace or str(workspace_manager.current_root),
        )
    )
    asyncio.create_task(_demo_runtime().run(task))
    return task


@app.get("/api/tasks/{task_id}", response_model=Task)
async def get_task(task_id: str) -> Task:
    task = store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.get("/api/tasks/{task_id}/events")
async def list_task_events(task_id: str) -> dict:
    if store.get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"items": store.list_events(task_id)}


@app.get("/api/tasks/{task_id}/trace")
async def get_task_trace(task_id: str) -> dict:
    if store.get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"items": store.trace.read(task_id)}
