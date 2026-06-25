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
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..config.settings import load_settings
from ..context_budget import build_context_budget_plan, estimate_tokens
from ..core import NovaCore
from ..lsp import LspManager
from ..memory import ProjectMemory
from ..mcp import McpManager
from ..observability.langfuse import (
    LangfuseTraceRecorder,
    load_langfuse_config,
    update_langfuse_secrets,
)
from ..providers.bigmodel import BigModelProvider, ProviderError
from ..processes.manager import ProcessManager
from ..review import ReviewManager
from ..runtime import CodexLikeAgentRuntime, RunOrchestrator
from ..runtime.commands import list_builtin_commands
from ..sessions import AgentSessionService, SessionStore
from ..skills import SkillManager
from ..subagents import SubAgentManager, SubAgentRun
from ..tools.executor import ToolExecutor
from ..tools.workspace import WorkspaceTools
from ..models import (
    ChatEvent,
    ChatMessage,
    ChatMessageCreate,
    ChatRole,
    ChatSession,
    ChatSessionCreate,
    Health,
    RuntimeConfigUpdate,
    RuntimeSecretUpdate,
    GitFileStatus,
    GitStatus,
    WorkspaceCommands,
    WorkspaceFolderCreate,
    WorkspaceMode,
    WorkspacePermissions,
    WorkspaceSelect,
    WorkspaceStatus,
    WorktreeCreate,
    new_id,
)
from ..workspace import WorkspaceError, WorkspaceManager
from ..worktrees import WorktreeError, WorktreeManager

PERMISSION_MODES = [
    "read_only",
    "ask",
    "workspace_write",
    "default",
    "plan",
    "accept_edits",
    "dont_ask",
    "bypass_permissions",
]
SANDBOX_MODES = ["read_only", "workspace_write", "danger_full_access"]
APPROVAL_POLICIES = ["untrusted", "on_failure", "on_request", "never", "granular"]

settings = load_settings()
core = NovaCore.from_settings(settings)
store = core.store
workspace_manager = core.workspace_manager
provider = core.provider
process_manager = core.process_manager
subagent_manager = core.subagent_manager
agent_sessions = core.agent_sessions
pending_approvals = agent_sessions.pending_approvals
_active_session_turns = agent_sessions.active_session_ids
_queued_session_messages = agent_sessions.queued_session_messages
_runtime_override = None
_DEFAULT_RUNTIME_CONFIG_FILE = settings.runtime_config_file
_DEFAULT_RUNTIME_SECRET_FILE = settings.runtime_secret_file
_DEFAULT_TOOL_HOOKS_FILE = settings.tool_hooks_file
_RUNTIME_BASE_CONFIG = {
    "provider_model": settings.provider_model,
    "provider_base_url": settings.provider_base_url,
    "context_window_tokens": settings.context_window_tokens,
    "permission_mode": settings.permission_mode,
    "sandbox_mode": settings.sandbox_mode,
    "approval_policy": settings.approval_policy,
    "network_access": settings.network_access,
    "max_tool_rounds": settings.max_tool_rounds,
}

app = FastAPI(title="Nova", version=__version__)
app.state.core = core
app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")


def _workspace_tools() -> WorkspaceTools:
    return WorkspaceTools(
        workspace_manager.current_root,
        permission_mode=settings.permission_mode,
        sandbox_mode=settings.sandbox_mode,
        network_access=settings.network_access,
        zai_api_key=provider.api_key_for_tools(),
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
        tool_hooks_file=_workspace_tool_hooks_file(),
        process_manager=process_manager,
        trace_recorder=LangfuseTraceRecorder(
            load_langfuse_config(_workspace_runtime_secret_file())
        ),
    )


def _subagent_runner(run: SubAgentRun) -> str:
    """在受限上下文中运行一个子 Agent；模型不可用时回退到本地摘要。"""

    async def collect() -> str:
        runtime = CodexLikeAgentRuntime(
            provider=provider,
            project_root=Path(run.workspace),
            global_agent_file=settings.global_agent_file,
            max_tool_rounds=min(settings.max_tool_rounds, 4),
            permission_mode="read_only",
            sandbox_mode="read_only",
            approval_policy="never",
            network_access=False,
            process_manager=ProcessManager(),
            trace_recorder=LangfuseTraceRecorder(
                load_langfuse_config(
                    _workspace_runtime_secret_file(Path(run.workspace))
                )
            ),
        )
        prompt = (
            "你是 Nova 的子 Agent。只处理下面委派给你的范围，不要再 spawn 子 Agent。"
            "先用只读方式核对事实，最后必须用以下格式回答：\n"
            "Scope: <你的任务范围>\nResult: <结论>\nKey files: <相关文件>\nIssues: <需要主 Agent 知道的问题>\n\n"
            f"委派任务：{run.prompt}"
        )
        content = ""
        messages = [ChatMessage(session_id=run.id, role=ChatRole.USER, content=prompt)]
        async for event in runtime.stream(messages):
            if run.cancel_requested:
                return content or "Scope: 已取消\nResult: 子 Agent 收到取消请求。"
            if event.get("type") == "agent_status":
                run.add_event("status", str(event.get("status") or "子 Agent 状态"))
            if event.get("type") == "assistant_done_content":
                content = str(event.get("content") or "")
        return content or "Scope: 子 Agent\nResult: 未收到模型最终回答。"

    try:
        return asyncio.run(asyncio.wait_for(collect(), timeout=20.0))
    except (
        asyncio.TimeoutError,
        ProviderError,
        RuntimeError,
        OSError,
        ValueError,
    ) as exc:
        run.add_event(
            "fallback", "子 Agent 使用本地兜底", f"{type(exc).__name__}: {exc}"
        )
        return SubAgentManager(Path(run.workspace))._local_summary_runner(run)


subagent_manager.default_runner = _subagent_runner


@contextmanager
def patch_runtime_for_test(runtime):
    global _runtime_override
    old = _runtime_override
    _runtime_override = runtime
    try:
        yield
    finally:
        _runtime_override = old


def app_module_tool_executor(tools: WorkspaceTools) -> ToolExecutor:
    return ToolExecutor(tools, process_manager=process_manager)


def _same_path(left: Path, right: Path) -> bool:
    return left.expanduser().resolve() == right.expanduser().resolve()


def _workspace_runtime_config_file(root: Path | None = None) -> Path:
    if not _same_path(settings.runtime_config_file, _DEFAULT_RUNTIME_CONFIG_FILE):
        return settings.runtime_config_file
    current = root or workspace_manager.current_root
    if (current / ".nova").is_dir():
        return current / ".nova" / "config" / "runtime-config.json"
    return settings.runtime_config_file


def _workspace_runtime_secret_file(root: Path | None = None) -> Path:
    # 密钥默认保存在用户级 Nova Home，避免把 API Key 写进任意项目目录。
    return settings.runtime_secret_file


def _workspace_tool_hooks_file(root: Path | None = None) -> Path:
    if not _same_path(settings.tool_hooks_file, _DEFAULT_TOOL_HOOKS_FILE):
        return settings.tool_hooks_file
    current = root or workspace_manager.current_root
    if (current / ".nova").is_dir():
        return current / ".nova" / "hooks" / "hooks.json"
    return settings.tool_hooks_file


def _normalize_runtime_config(payload: dict) -> dict:
    config = dict(_RUNTIME_BASE_CONFIG)
    config.update(payload)
    if config["permission_mode"] not in PERMISSION_MODES:
        config["permission_mode"] = _RUNTIME_BASE_CONFIG["permission_mode"]
    if config["sandbox_mode"] not in SANDBOX_MODES:
        config["sandbox_mode"] = _RUNTIME_BASE_CONFIG["sandbox_mode"]
    if config["approval_policy"] not in APPROVAL_POLICIES:
        config["approval_policy"] = _RUNTIME_BASE_CONFIG["approval_policy"]
    config["network_access"] = bool(config["network_access"])
    config["max_tool_rounds"] = max(1, min(int(config["max_tool_rounds"]), 12))
    config["context_window_tokens"] = max(
        8192, min(int(config["context_window_tokens"]), 1000000)
    )
    config["provider_model"] = (
        str(config["provider_model"]).strip() or _RUNTIME_BASE_CONFIG["provider_model"]
    )
    config["provider_base_url"] = (
        str(config["provider_base_url"]).strip().rstrip("/")
        or _RUNTIME_BASE_CONFIG["provider_base_url"]
    )
    return config


def _effective_runtime_config(root: Path | None = None) -> dict:
    return _normalize_runtime_config(_read_runtime_config_overrides(root))


def _apply_workspace_runtime_config(root: Path | None = None) -> None:
    # 参考 cc 的 cwd-first 设计：项目切换后，模型、权限、hooks 都按当前项目重新解析。
    _apply_runtime_config(_effective_runtime_config(root))


def _switch_workspace(path: str) -> Path:
    selected = workspace_manager.set_current(path)
    _apply_workspace_runtime_config(selected)
    return selected


def _worktree_manager() -> WorktreeManager:
    return WorktreeManager.from_workspace(workspace_manager.current_root)


def _runtime_config_payload() -> dict:
    pending = _read_runtime_config_overrides()
    langfuse_status = load_langfuse_config(_workspace_runtime_secret_file()).status()
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
    restart_required = any(
        pending.get(key) != value for key, value in effective.items() if key in pending
    )
    return {
        "model": provider.model,
        "base_url": provider.base_url,
        "permission_mode": settings.permission_mode,
        "sandbox_mode": settings.sandbox_mode,
        "approval_policy": settings.approval_policy,
        "network_access": settings.network_access,
        "max_tool_rounds": settings.max_tool_rounds,
        "context_window_tokens": settings.context_window_tokens,
        "worktree_enabled": _git_available(),
        "approval_ui_enabled": True,
        "tool_parallel_readonly": True,
        "memory_enabled": True,
        "hooks_enabled": _workspace_tool_hooks_file().exists(),
        "tool_hooks_file": str(_workspace_tool_hooks_file()),
        "permission_modes": PERMISSION_MODES,
        "sandbox_modes": SANDBOX_MODES,
        "approval_policies": APPROVAL_POLICIES,
        "editable": True,
        "restart_required": restart_required,
        "restart_note": "运行配置保存后会立即影响下一次请求；重启仅用于兜底刷新进程状态。",
        "pending_config": pending,
        "api_key_set": provider.is_configured(),
        "api_key_source": provider.api_key_source(),
        "langfuse_configured": langfuse_status["configured"],
        "langfuse_public_key_set": langfuse_status["public_key_set"],
        "langfuse_secret_key_set": langfuse_status["secret_key_set"],
        "langfuse_host": langfuse_status["host"],
        "langfuse_enabled": langfuse_status["enabled"],
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


def _read_runtime_config_overrides(root: Path | None = None) -> dict:
    merged: dict = {}
    try:
        user_payload = json.loads(
            settings.runtime_config_file.read_text(encoding="utf-8")
        )
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        user_payload = {}
    if isinstance(user_payload, dict):
        merged.update(user_payload)
    project_file = _workspace_runtime_config_file(root)
    if project_file != settings.runtime_config_file:
        try:
            project_payload = json.loads(project_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            project_payload = {}
        if isinstance(project_payload, dict):
            merged.update(project_payload)
    return merged


def _permission_mode_label(permission_mode: str) -> str:
    return {
        "read_only": "只读：只允许读工具",
        "ask": "询问：写入和 shell 需要审批",
        "workspace_write": "工作区写入：自动允许当前项目内写入",
        "plan": "计划：只拆方案，不执行写入或 shell",
        "bypass_permissions": "跳过权限：跳过审批并允许完全访问",
        "accept_edits": "接受编辑：允许写文件，shell 仍需限制",
        "dont_ask": "不询问：未预批准的高风险工具会被拒绝",
    }.get(permission_mode, "询问：写入和 shell 需要审批")


def _read_git_status(quick: bool = False) -> GitStatus:
    # 这里只读 Git 状态，避免在状态刷新时产生 stage/revert/push 等副作用。
    try:
        branch = _git(["branch", "--show-current"]).strip() or None
        if quick:
            return GitStatus(available=True, branch=branch, partial=True)
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


def _git_available() -> bool:
    try:
        _git(["rev-parse", "--is-inside-work-tree"])
    except (OSError, subprocess.CalledProcessError):
        return False
    return True


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


def _chat_timeline_items(session_id: str) -> list[dict]:
    items: list[dict] = []
    for message in store.list_chat_messages(session_id):
        items.append(
            {
                "kind": "message",
                "created_at": message.created_at,
                "item": message.model_dump(mode="json"),
            }
        )
    for event in store.list_chat_events(session_id):
        items.append(
            {
                "kind": "event",
                "created_at": event.created_at,
                "item": event.model_dump(mode="json"),
            }
        )
    items.sort(key=lambda item: item["created_at"])
    return [{"kind": item["kind"], "item": item["item"]} for item in items]


def _get_current_chat_session(
    session_id: str, *, auto_switch: bool = False
) -> ChatSession | None:
    # 会话按项目目录隔离，避免切换项目后把上下文发进错误仓库。
    session = store.get_chat_session(session_id)
    if session is None:
        return None
    if session.workspace != str(workspace_manager.current_root):
        if auto_switch and session.workspace:
            try:
                _switch_workspace(session.workspace)
            except WorkspaceError as exc:
                raise HTTPException(
                    status_code=409, detail=f"历史线程所属项目不可用：{exc}"
                ) from exc
            return session
        return None
    return session


def _estimate_tokens(text: str) -> int:
    return estimate_tokens(text)


def _estimate_session_tokens(session_id: str) -> dict:
    return _context_budget_status(session_id).as_dict()


def _context_budget_status(session_id: str):
    return build_context_budget_plan(
        session_id=session_id,
        messages=store.list_chat_messages(session_id),
        events=store.list_chat_events(session_id),
        context_window_tokens=settings.context_window_tokens,
    )


def _empty_context_budget(context_window_tokens: int) -> dict:
    plan = build_context_budget_plan(
        session_id="empty",
        messages=[],
        events=[],
        context_window_tokens=context_window_tokens,
    )
    return plan.as_dict()


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
            arguments=(
                event.get("arguments")
                if isinstance(event.get("arguments"), dict)
                else {}
            ),
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
            arguments=(
                event.get("arguments")
                if isinstance(event.get("arguments"), dict)
                else {}
            ),
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
    if event_type == "compact_done":
        return build_event(
            "memory.compacted",
            category="status",
            phase="completed",
            title=str(event.get("title") or "上下文已压缩"),
            message=str(
                event.get("message") or "会话摘要已写入 .nova/memory/session.md。"
            ),
            data={
                "summary": str(event.get("summary") or ""),
                "path": str(event.get("path") or ""),
                "covered_messages": int(event.get("covered_messages") or 0),
            },
        )
    return None


def _denied_tool_alternative_message(*, tool: str, arguments: dict, reason: str) -> str:
    command = ""
    if tool == "shell_command":
        command = str(arguments.get("command") or "").strip()
    target = command or json.dumps(arguments, ensure_ascii=False)
    target_line = f"这次被拒绝的是 `{tool}`"
    if target:
        target_line += f"：`{target}`"
    return (
        f"已按你的选择停止执行工具。拒绝原因：{reason}。\n\n"
        f"{target_line}。我不会继续绕过这个权限，也不会擅自执行等价的高风险操作。\n\n"
        "替代路径：我可以先基于当前上下文做只读分析，给出预计影响、需要执行的命令和风险点；"
        "如果你确认某一步可以执行，再重新发起对应工具调用并等待你的授权。"
    )


def _event_builder_for_existing_turn(session_id: str, turn_id: str):
    sequence = len(
        [
            event
            for event in store.list_chat_events(session_id)
            if event.turn_id == turn_id
        ]
    )

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
                else (
                    "turn"
                    if category == "turn"
                    else (
                        "permission"
                        if category == "permission"
                        else "hook" if category == "hook" else "status"
                    )
                )
            ),
            event_type=str(event.get("event_type") or ""),
            phase=str(event.get("phase") or ""),
            turn_id=str(event.get("turn_id") or ""),
            sequence=int(event.get("sequence") or 0),
            status=status,
            title=str(event.get("title") or event.get("event_type") or "运行事件"),
            message=str(event.get("message") or event.get("title") or ""),
            tool=str(event.get("tool")) if event.get("tool") else None,
            arguments=(
                event.get("arguments")
                if isinstance(event.get("arguments"), dict)
                else {}
            ),
            output=(
                str(event.get("output")) if event.get("output") is not None else None
            ),
            data=event.get("data") if isinstance(event.get("data"), dict) else {},
            parallel=(
                bool((event.get("data") or {}).get("parallel"))
                if isinstance(event.get("data"), dict)
                else False
            ),
        )
    )


def _ndjson(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


def register_api_routes() -> None:
    from . import (
        chat,
        memory,
        permissions,
        processes,
        runtime,
        subagents,
        system,
        tools,
        workspace,
    )

    for router in (
        system.router,
        runtime.router,
        tools.router,
        memory.router,
        permissions.router,
        processes.router,
        subagents.router,
        workspace.router,
        chat.router,
    ):
        app.include_router(router)


register_api_routes()
