from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import AsyncIterator
from contextlib import contextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .approvals.store import PendingApprovalStore
from .config.settings import load_settings
from .providers.bigmodel import BigModelProvider, ProviderError
from .processes.manager import ProcessManager
from .runtime import CodexLikeAgentRuntime, DemoAgentRuntime
from .sessions import AgentSessionService, TaskStore
from .tools.executor import ToolExecutor
from .tools.workspace import WorkspaceTools
from .models import (
    ChatEvent,
    ChatMessage,
    ChatMessageCreate,
    ChatRole,
    ChatSession,
    ChatSessionCreate,
    Health,
    RuntimeConfigUpdate,
    RuntimeSecretUpdate,
    Task,
    TaskCreate,
    TimelineEvent,
    GitFileStatus,
    GitStatus,
    WorkspaceCommands,
    WorkspaceFolderCreate,
    WorkspaceMode,
    WorkspacePermissions,
    WorkspaceSelect,
    WorkspaceStatus,
    new_id,
)
from .workspace import WorkspaceError, WorkspaceManager

PERMISSION_MODES = ["read_only", "ask", "workspace_write", "default", "plan", "accept_edits", "dont_ask", "bypass_permissions"]
SANDBOX_MODES = ["read_only", "workspace_write", "danger_full_access"]
APPROVAL_POLICIES = ["untrusted", "on_failure", "on_request", "never", "granular"]

settings = load_settings()
store = TaskStore(settings.state_dir)
workspace_manager = WorkspaceManager(
    initial_root=settings.initial_workspace_root,
    allowed_roots=settings.allowed_workspace_roots,
)
provider = BigModelProvider(
    base_url=settings.provider_base_url,
    model=settings.provider_model,
    api_key_file=settings.runtime_secret_file,
)
pending_approvals = PendingApprovalStore()
process_manager = ProcessManager()
agent_sessions = AgentSessionService()
_active_session_turns = agent_sessions.active_session_ids
_queued_session_messages = agent_sessions.queued_session_messages
_runtime_override = None

app = FastAPI(title="Nova Gateway", version=__version__)
app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")


def _workspace_tools() -> WorkspaceTools:
    return WorkspaceTools(
        workspace_manager.current_root,
        permission_mode=settings.permission_mode,
        sandbox_mode=settings.sandbox_mode,
        network_access=settings.network_access,
    )


def _agent_runtime() -> CodexLikeAgentRuntime:
    if _runtime_override is not None:
        return _runtime_override
    return CodexLikeAgentRuntime(
        provider=provider,
        project_root=workspace_manager.current_root,
        global_agent_file=settings.global_agent_file,
        max_tool_rounds=settings.max_tool_rounds,
        permission_mode=settings.permission_mode,
        sandbox_mode=settings.sandbox_mode,
        approval_policy=settings.approval_policy,
        network_access=settings.network_access,
        tool_hooks_file=settings.tool_hooks_file,
        process_manager=process_manager,
    )


@contextmanager
def patch_runtime_for_test(runtime):
    global _runtime_override
    old = _runtime_override
    _runtime_override = runtime
    try:
        yield
    finally:
        _runtime_override = old


def _demo_runtime() -> DemoAgentRuntime:
    return DemoAgentRuntime(store=store, project_root=workspace_manager.current_root)


def app_module_tool_executor(tools: WorkspaceTools) -> ToolExecutor:
    return ToolExecutor(tools, process_manager=process_manager)


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
        "api_key_source": provider.api_key_source(),
        "api_key_env": provider.api_key_env,
    }


@app.get("/api/runtime/config")
async def runtime_config() -> dict:
    return _runtime_config_payload()


@app.patch("/api/runtime/config")
async def update_runtime_config(payload: RuntimeConfigUpdate) -> dict:
    pending = _read_runtime_config_overrides()
    update = payload.model_dump(exclude_none=True)
    for key, value in update.items():
        if isinstance(value, str):
            pending[key] = value.strip()
        else:
            pending[key] = value
    settings.runtime_config_file.parent.mkdir(parents=True, exist_ok=True)
    settings.runtime_config_file.write_text(
        json.dumps(pending, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _apply_runtime_config(update)
    result = _runtime_config_payload()
    result["pending_config"] = pending
    return result


@app.patch("/api/runtime/secrets")
async def update_runtime_secrets(payload: RuntimeSecretUpdate) -> dict:
    if payload.bigmodel_api_key is not None:
        provider.set_runtime_api_key(
            payload.bigmodel_api_key,
            api_key_file=settings.runtime_secret_file,
        )
    return {
        "ok": True,
        "api_key_set": provider.is_configured(),
        "api_key_source": provider.api_key_source(),
        "provider_configured": provider.is_configured(),
    }


@app.post("/api/runtime/restart")
async def restart_runtime() -> dict:
    def delayed_restart() -> None:
        time.sleep(0.35)
        os.execv(sys.executable, [sys.executable, *sys.argv])

    threading.Thread(target=delayed_restart, daemon=True).start()
    return {"ok": True, "message": "Nova 正在重启，配置会在进程重新加载后生效。"}


def _runtime_config_payload() -> dict:
    pending = _read_runtime_config_overrides()
    effective = {
        "provider_model": provider.model,
        "provider_base_url": provider.base_url,
        "context_window_tokens": settings.context_window_tokens,
        "permission_mode": settings.permission_mode,
        "sandbox_mode": settings.sandbox_mode,
        "approval_policy": settings.approval_policy,
        "network_access": settings.network_access,
        "max_tool_rounds": settings.max_tool_rounds,
    }
    restart_required = any(pending.get(key) != value for key, value in effective.items() if key in pending)
    return {
        "model": provider.model,
        "base_url": provider.base_url,
        "permission_mode": settings.permission_mode,
        "sandbox_mode": settings.sandbox_mode,
        "approval_policy": settings.approval_policy,
        "network_access": settings.network_access,
        "max_tool_rounds": settings.max_tool_rounds,
        "context_window_tokens": settings.context_window_tokens,
        "worktree_enabled": False,
        "approval_ui_enabled": True,
        "tool_parallel_readonly": True,
        "memory_enabled": True,
        "hooks_enabled": settings.tool_hooks_file.exists(),
        "tool_hooks_file": str(settings.tool_hooks_file),
        "permission_modes": PERMISSION_MODES,
        "sandbox_modes": SANDBOX_MODES,
        "approval_policies": APPROVAL_POLICIES,
        "editable": True,
        "restart_required": restart_required,
        "restart_note": "运行配置保存后会立即影响下一次请求；重启仅用于兜底刷新进程状态。",
        "pending_config": pending,
        "api_key_set": provider.is_configured(),
        "api_key_source": provider.api_key_source(),
    }


def _apply_runtime_config(update: dict) -> None:
    """把设置页保存的配置同步到当前进程，避免用户每次切换权限后都要重启。"""
    for key, value in update.items():
        if key == "provider_model" and isinstance(value, str):
            provider.model = value.strip()
            object.__setattr__(settings, key, provider.model)
            continue
        if key == "provider_base_url" and isinstance(value, str):
            provider.base_url = value.strip().rstrip("/")
            object.__setattr__(settings, key, provider.base_url)
            continue
        if hasattr(settings, key):
            object.__setattr__(settings, key, value)


def _read_runtime_config_overrides() -> dict:
    try:
        payload = json.loads(settings.runtime_config_file.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


@app.get("/api/runtime/statusline")
async def runtime_statusline(session_id: str | None = Query(default=None, max_length=80)) -> dict:
    session = _get_current_chat_session(session_id) if session_id else None
    token_usage = _estimate_session_tokens(session.id) if session else {
        "input_tokens": 0,
        "output_tokens": 0,
        "used_tokens": 0,
    }
    context_window = settings.context_window_tokens
    remaining_tokens = max(context_window - token_usage["used_tokens"], 0)
    remaining_percent = round((remaining_tokens / context_window) * 100, 1) if context_window else None
    return {
        "model": provider.model,
        "session_id": session.id if session else None,
        "thread_title": session.title if session else "新线程",
        "workspace": str(workspace_manager.current_root),
        "project": workspace_manager.current_root.name,
        "permission_mode": settings.permission_mode,
        "sandbox_mode": settings.sandbox_mode,
        "approval_policy": settings.approval_policy,
        "status": "working" if session_id and session is None else "ready",
        "context_window_tokens": context_window,
        "context_remaining_tokens": remaining_tokens,
        "context_remaining_percent": remaining_percent,
        "estimated": True,
        **token_usage,
    }


@app.get("/api/tools")
async def tool_list() -> dict:
    return {"items": _workspace_tools().list_specs()}


@app.get("/api/memory/status")
async def memory_status() -> dict:
    return _agent_runtime().memory.status()


@app.get("/api/memory/files/{name}")
async def memory_file(name: str) -> dict:
    return _agent_runtime().memory.read_file(name)


@app.post("/api/memory/files")
async def write_memory_file(payload: dict) -> dict:
    name = str(payload.get("name") or "index.md")
    content = str(payload.get("content") or "")
    return _agent_runtime().memory.write_file(name, content)


@app.post("/api/memory/remember")
async def remember(payload: dict) -> dict:
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    return _agent_runtime().memory.append_fact(text)


@app.get("/api/approvals/pending")
async def list_pending_approvals(session_id: str | None = Query(default=None, max_length=80)) -> dict:
    return {"items": [item.as_dict() for item in pending_approvals.list_pending(session_id=session_id)]}


@app.post("/api/approvals/{approval_id}/approve")
async def approve_tool_call(approval_id: str) -> dict:
    item = pending_approvals.approve(approval_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    tools = WorkspaceTools(
        workspace_manager.current_root,
        permission_mode="workspace_write",
        sandbox_mode=settings.sandbox_mode,
        network_access=settings.network_access,
    )
    executor = app_module_tool_executor(tools)
    events, result_json = executor.run_one_stream(item.call_id, item.tool, item.arguments)
    for event in events:
        runtime_event = _runtime_event_from_agent_event(
            event,
            _event_builder_for_existing_turn(item.session_id, item.turn_id),
        )
        if runtime_event is not None:
            _persist_runtime_event(runtime_event)
    return {"ok": True, "status": "approved", "approval": item.as_dict(), "events": events, "result_json": result_json}


@app.post("/api/approvals/{approval_id}/deny")
async def deny_tool_call(approval_id: str, payload: dict | None = None) -> dict:
    reason = str((payload or {}).get("reason") or "用户拒绝执行")
    item = pending_approvals.deny(approval_id, reason=reason)
    if item is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    event = _event_builder_for_existing_turn(item.session_id, item.turn_id)(
        "permission.denied",
        category="permission",
        phase="denied",
        status="failed",
        title=f"已拒绝：{item.tool}",
        message=reason,
        tool=item.tool,
        call_id=item.call_id,
        arguments=item.arguments,
        data={"permission": item.permission},
    )
    _persist_runtime_event(event)
    return {"ok": True, "status": "denied", "approval": item.as_dict(), "event": event}


@app.get("/api/processes")
async def list_processes() -> dict:
    return {"items": process_manager.list_jobs()}


@app.get("/api/processes/{job_id}")
async def get_process(job_id: str) -> dict:
    job = process_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Process not found")
    return job


@app.delete("/api/processes/{job_id}")
async def kill_process(job_id: str) -> dict:
    try:
        return process_manager.kill(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Process not found") from exc


@app.get("/api/workspaces")
async def workspace_list(q: str | None = Query(default=None, max_length=1200)) -> dict:
    return workspace_manager.status(query=q)


@app.post("/api/workspace/select")
async def select_workspace(payload: WorkspaceSelect) -> dict:
    try:
        workspace_manager.set_current(payload.path)
        return workspace_manager.status()
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/workspace/folders")
async def create_workspace_folder(payload: WorkspaceFolderCreate) -> dict:
    if settings.permission_mode != "workspace_write":
        raise HTTPException(status_code=403, detail="当前权限模式不允许新建目录")
    try:
        created = workspace_manager.create_folder(payload.path)
        workspace_manager.set_current(str(created))
        return workspace_manager.status(query=str(created))
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
            sandbox_mode=settings.sandbox_mode,
            approval_policy_id=settings.approval_policy,
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
    session = ChatSession(
        id=new_id("chat"),
        title=payload.title or "新对话",
        workspace=str(workspace_manager.current_root),
    )
    store.create_chat_session(session)
    return session


@app.delete("/api/chat/sessions/{session_id}", status_code=204)
async def delete_chat_session(session_id: str) -> Response:
    session = store.get_chat_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Chat session not found")
    store.delete_chat_session(session.id)
    return Response(status_code=204)


@app.get("/api/chat/sessions/{session_id}/messages", response_model=list[ChatMessage])
async def list_chat_messages(session_id: str) -> list[ChatMessage]:
    if _get_current_chat_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return store.list_chat_messages(session_id)


@app.get("/api/chat/sessions/{session_id}/timeline")
async def list_chat_timeline(session_id: str) -> dict:
    if _get_current_chat_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Chat session not found")
    items: list[dict] = []
    for message in store.list_chat_messages(session_id):
        items.append({"kind": "message", "created_at": message.created_at, "item": message.model_dump(mode="json")})
    for event in store.list_chat_events(session_id):
        items.append({"kind": "event", "created_at": event.created_at, "item": event.model_dump(mode="json")})
    items.sort(key=lambda item: item["created_at"])
    return {"items": [{"kind": item["kind"], "item": item["item"]} for item in items]}


@app.post("/api/chat/sessions/{session_id}/messages", response_model=ChatMessage)
async def create_chat_message(session_id: str, payload: ChatMessageCreate) -> ChatMessage:
    session = _get_current_chat_session(session_id)
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
) -> Response:
    if _get_current_chat_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Chat session not found")
    if agent_sessions.is_active(session_id):
        queued = ChatMessage(
            session_id=session_id,
            role=ChatRole.USER,
            content=payload.content,
        )
        store.add_chat_message(queued)
        agent_sessions.enqueue_message(session_id, queued)
        return JSONResponse(
            status_code=202,
            content={"ok": True, "status": "queued", "message": queued.model_dump(mode="json")},
        )
    agent_sessions.mark_active(session_id)

    async def emit() -> AsyncIterator[str]:
        turn_id = new_id("turn")
        sequence = 0

        def runtime_event(
            event_type: str,
            *,
            category: str,
            phase: str,
            title: str,
            message: str | None = None,
            status: str = "ok",
            tool: str | None = None,
            call_id: str | None = None,
            arguments: dict | None = None,
            output: str | None = None,
            data: dict | None = None,
            persist: bool = True,
        ) -> dict:
            nonlocal sequence
            sequence += 1
            event = {
                "id": call_id or new_id("evt"),
                "session_id": session_id,
                "turn_id": turn_id,
                "sequence": sequence,
                "event_type": event_type,
                "category": category,
                "phase": phase,
                "status": status,
                "title": title,
                "message": message or title,
                "tool": tool,
                "call_id": call_id,
                "arguments": arguments or {},
                "output": output,
                "data": data or {},
            }
            if persist:
                _persist_runtime_event(event)
            return event

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
        started = runtime_event(
            "turn.started",
            category="turn",
            phase="started",
            title="开始处理用户请求",
            message=payload.content,
            data={"message_id": user_message.id, "task_id": task.id},
        )
        yield _ndjson({"type": "runtime_event", "event": started})

        answer_parts: list[str] = []
        try:
            async for event in _agent_runtime().stream(store.list_chat_messages(session_id)):
                runtime = _runtime_event_from_agent_event(event, runtime_event)
                if runtime is not None:
                    yield _ndjson({"type": "runtime_event", "event": runtime})
                if event["type"] == "permission_request":
                    pending_approvals.create(
                        session_id=session_id,
                        turn_id=turn_id,
                        call_id=str(event.get("call_id") or runtime.get("id") if runtime else new_id("tool")),
                        tool=str(event.get("tool") or "tool"),
                        arguments=event.get("arguments") if isinstance(event.get("arguments"), dict) else {},
                        permission=str(event.get("permission") or ""),
                        reason=str(event.get("message") or "执行工具前需要用户确认。"),
                    )
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
            completed = runtime_event(
                "turn.completed",
                category="turn",
                phase="completed",
                title="本轮回复完成",
                message="Nova 已完成本次运行。",
                data={"message_id": assistant_message.id, "task_id": task.id},
            )
            store.add_event(
                TimelineEvent(
                    task_id=task.id,
                    type="assistant_stream_completed",
                    title="流式回复完成",
                    message="GLM-4.7 已完成本次流式回复。",
                    data={"session_id": session_id, "message_id": assistant_message.id},
                )
            )
            yield _ndjson({"type": "runtime_event", "event": completed})
            for queued in agent_sessions.drain_queued_messages(session_id):
                queued_event = runtime_event(
                    "turn.queued_message",
                    category="turn",
                    phase="queued",
                    title="已收到排队消息",
                    message=queued.content,
                    data={"message_id": queued.id, "task_id": task.id},
                )
                yield _ndjson({"type": "queued_message", "message": queued.model_dump(mode="json"), "event": queued_event})
            yield _ndjson(
                {
                    "type": "assistant_done",
                    "message": assistant_message.model_dump(mode="json"),
                }
            )
        except ProviderError as exc:
            failed = runtime_event(
                "turn.failed",
                category="turn",
                phase="failed",
                status="failed",
                title="模型调用失败",
                message=str(exc),
                data={"task_id": task.id},
            )
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
            yield _ndjson({"type": "runtime_event", "event": failed})
            yield _ndjson({"type": "error", "message": error_message.model_dump(mode="json")})
        except Exception as exc:
            detail = str(exc) or repr(exc)
            failed = runtime_event(
                "turn.failed",
                category="turn",
                phase="failed",
                status="failed",
                title="运行时异常",
                message=f"{type(exc).__name__}: {detail}",
                data={"task_id": task.id},
            )
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
            yield _ndjson({"type": "runtime_event", "event": failed})
            yield _ndjson({"type": "error", "message": error_message.model_dump(mode="json")})
        finally:
            agent_sessions.mark_idle(session_id)

    return StreamingResponse(emit(), media_type="application/x-ndjson")


def _get_current_chat_session(session_id: str) -> ChatSession | None:
    # 会话按项目目录隔离，避免切换项目后把上下文发进错误仓库。
    session = store.get_chat_session(session_id)
    if session is None:
        return None
    if session.workspace != str(workspace_manager.current_root):
        return None
    return session


def _estimate_tokens(text: str) -> int:
    # Web statusline 只需要稳定估算，真实计费 token 仍以模型供应商返回为准。
    cleaned = text or ""
    if not cleaned:
        return 0
    return max(1, (len(cleaned) + 3) // 4)


def _estimate_session_tokens(session_id: str) -> dict:
    input_tokens = 0
    output_tokens = 0
    for message in store.list_chat_messages(session_id):
        if message.role in {ChatRole.USER, ChatRole.SYSTEM}:
            input_tokens += _estimate_tokens(message.content)
        else:
            output_tokens += _estimate_tokens(message.content)
    for event in store.list_chat_events(session_id):
        if event.arguments:
            input_tokens += _estimate_tokens(json.dumps(event.arguments, ensure_ascii=False))
        if event.output:
            output_tokens += _estimate_tokens(event.output)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "used_tokens": input_tokens + output_tokens,
    }


def _runtime_event_from_agent_event(event: dict, build_event) -> dict | None:
    event_type = event.get("type")
    if event_type == "tool_start":
        return build_event(
            "tool.started",
            category="tool",
            phase="started",
            status="running",
            title=str(event.get("title") or event.get("tool") or "工具执行中"),
            message="工具已开始执行。",
            tool=str(event.get("tool") or "tool"),
            call_id=str(event.get("call_id") or new_id("tool")),
            arguments=event.get("arguments") if isinstance(event.get("arguments"), dict) else {},
            data={
                "parallel": bool(event.get("parallel", False)),
                **(event.get("data") if isinstance(event.get("data"), dict) else {}),
            },
        )
    if event_type == "tool_done":
        ok = bool(event.get("ok", False))
        return build_event(
            "tool.completed",
            category="tool",
            phase="completed" if ok else "failed",
            status="ok" if ok else "failed",
            title=str(event.get("title") or event.get("tool") or "工具完成"),
            message="工具执行完成。" if ok else "工具执行失败。",
            tool=str(event.get("tool") or "tool"),
            call_id=str(event.get("call_id") or new_id("tool")),
            output=str(event.get("output") or ""),
            data=event.get("data") if isinstance(event.get("data"), dict) else {},
        )
    if event_type == "tool_output":
        stream = str(event.get("stream") or "stdout")
        chunk = str(event.get("chunk") or "")
        return build_event(
            "tool.output",
            category="tool",
            phase="output",
            status="running",
            title=f"{event.get('tool') or 'tool'} {stream}",
            message=chunk,
            tool=str(event.get("tool") or "tool"),
            call_id=str(event.get("call_id") or new_id("tool")),
            output=chunk,
            data={
                "stream": stream,
                **(event.get("data") if isinstance(event.get("data"), dict) else {}),
            },
        )
    if event_type == "permission_request":
        return build_event(
            "permission.requested",
            category="permission",
            phase="requested",
            status="pending",
            title=str(event.get("title") or f"需要审批：{event.get('tool') or '工具'}"),
            message=str(event.get("message") or "执行工具前需要用户确认。"),
            tool=str(event.get("tool") or "tool"),
            call_id=str(event.get("call_id") or new_id("tool")),
            arguments=event.get("arguments") if isinstance(event.get("arguments"), dict) else {},
            data={
                "permission": str(event.get("permission") or ""),
                **(event.get("data") if isinstance(event.get("data"), dict) else {}),
            },
        )
    if event_type == "hook_start":
        hook_event = str(event.get("hook_event") or "Hook")
        hook_name = str(event.get("hook_name") or hook_event)
        return build_event(
            "hook.started",
            category="hook",
            phase="started",
            status="running",
            title=str(event.get("title") or f"Hook {hook_event}: {hook_name}"),
            message="Hook 已开始执行。",
            tool=str(event.get("tool") or "tool"),
            call_id=str(event.get("call_id") or new_id("hook")),
            data={
                "hook_event": hook_event,
                "hook_name": hook_name,
                **(event.get("data") if isinstance(event.get("data"), dict) else {}),
            },
        )
    if event_type == "hook_done":
        hook_event = str(event.get("hook_event") or "Hook")
        hook_name = str(event.get("hook_name") or hook_event)
        return build_event(
            "hook.completed",
            category="hook",
            phase="completed",
            title=str(event.get("title") or f"Hook 完成：{hook_name}"),
            message="Hook 已完成。",
            tool=str(event.get("tool") or "tool"),
            call_id=str(event.get("call_id") or new_id("hook")),
            data={
                "hook_event": hook_event,
                "hook_name": hook_name,
                **(event.get("data") if isinstance(event.get("data"), dict) else {}),
            },
        )
    if event_type == "agent_status":
        status = str(event.get("status") or "运行中")
        return build_event(
            "agent.status",
            category="status",
            phase="update",
            title=status,
            message=status,
        )
    return None


def _event_builder_for_existing_turn(session_id: str, turn_id: str):
    sequence = len([event for event in store.list_chat_events(session_id) if event.turn_id == turn_id])

    def build_event(
        event_type: str,
        *,
        category: str,
        phase: str,
        title: str,
        message: str | None = None,
        status: str = "ok",
        tool: str | None = None,
        call_id: str | None = None,
        arguments: dict | None = None,
        output: str | None = None,
        data: dict | None = None,
        persist: bool = False,
    ) -> dict:
        nonlocal sequence
        sequence += 1
        return {
            "id": call_id or new_id("evt"),
            "session_id": session_id,
            "turn_id": turn_id,
            "sequence": sequence,
            "event_type": event_type,
            "category": category,
            "phase": phase,
            "status": status,
            "title": title,
            "message": message or title,
            "tool": tool,
            "call_id": call_id,
            "arguments": arguments or {},
            "output": output,
            "data": data or {},
        }

    return build_event


def _persist_runtime_event(event: dict) -> None:
    category = str(event.get("category") or "status")
    status = str(event.get("status") or "ok")
    store.upsert_chat_event(
        ChatEvent(
            id=str(event.get("id") or new_id("evt")),
            session_id=str(event["session_id"]),
            type=(
                "tool"
                if category == "tool"
                else "turn"
                if category == "turn"
                else "permission"
                if category == "permission"
                else "hook"
                if category == "hook"
                else "status"
            ),
            event_type=str(event.get("event_type") or ""),
            phase=str(event.get("phase") or ""),
            turn_id=str(event.get("turn_id") or ""),
            sequence=int(event.get("sequence") or 0),
            status=status,
            title=str(event.get("title") or event.get("event_type") or "运行事件"),
            message=str(event.get("message") or event.get("title") or ""),
            tool=str(event.get("tool")) if event.get("tool") else None,
            arguments=event.get("arguments") if isinstance(event.get("arguments"), dict) else {},
            output=str(event.get("output")) if event.get("output") is not None else None,
            data=event.get("data") if isinstance(event.get("data"), dict) else {},
            parallel=bool((event.get("data") or {}).get("parallel")) if isinstance(event.get("data"), dict) else False,
        )
    )


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
