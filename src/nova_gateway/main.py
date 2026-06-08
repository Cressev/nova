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
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config.settings import load_settings
from .context_budget import build_context_budget_plan, estimate_tokens
from .memory import ProjectMemory
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
    WorktreeCreate,
    new_id,
)
from .workspace import WorkspaceError, WorkspaceManager
from .worktrees import WorktreeError, WorktreeManager

PERMISSION_MODES = ["read_only", "ask", "workspace_write", "default", "plan", "accept_edits", "dont_ask", "bypass_permissions"]
SANDBOX_MODES = ["read_only", "workspace_write", "danger_full_access"]
APPROVAL_POLICIES = ["untrusted", "on_failure", "on_request", "never", "granular"]

settings = load_settings()
store = TaskStore(settings.state_dir)
workspace_manager = WorkspaceManager(
    initial_root=settings.initial_workspace_root,
    allowed_roots=settings.allowed_workspace_roots,
    recent_file=settings.state_dir / "workspace-recents.json",
)
provider = BigModelProvider(
    base_url=settings.provider_base_url,
    model=settings.provider_model,
    api_key_file=settings.runtime_secret_file,
)
process_manager = ProcessManager()
agent_sessions = AgentSessionService()
pending_approvals = agent_sessions.pending_approvals
_active_session_turns = agent_sessions.active_session_ids
_queued_session_messages = agent_sessions.queued_session_messages
_runtime_override = None
_DEFAULT_RUNTIME_CONFIG_FILE = settings.project_root / ".nova" / "runtime-config.json"
_DEFAULT_RUNTIME_SECRET_FILE = settings.project_root / ".nova" / "runtime-secrets.json"
_DEFAULT_TOOL_HOOKS_FILE = settings.project_root / ".nova" / "hooks.json"
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
        tool_hooks_file=_workspace_tool_hooks_file(),
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


def _same_path(left: Path, right: Path) -> bool:
    return left.expanduser().resolve() == right.expanduser().resolve()


def _workspace_runtime_config_file(root: Path | None = None) -> Path:
    if not _same_path(settings.runtime_config_file, _DEFAULT_RUNTIME_CONFIG_FILE):
        return settings.runtime_config_file
    return (root or workspace_manager.current_root) / ".nova" / "runtime-config.json"


def _workspace_runtime_secret_file(root: Path | None = None) -> Path:
    if not _same_path(settings.runtime_secret_file, _DEFAULT_RUNTIME_SECRET_FILE):
        return settings.runtime_secret_file
    return (root or workspace_manager.current_root) / ".nova" / "runtime-secrets.json"


def _workspace_tool_hooks_file(root: Path | None = None) -> Path:
    if not _same_path(settings.tool_hooks_file, _DEFAULT_TOOL_HOOKS_FILE):
        return settings.tool_hooks_file
    return (root or workspace_manager.current_root) / ".nova" / "hooks.json"


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
    config["context_window_tokens"] = max(8192, min(int(config["context_window_tokens"]), 1000000))
    config["provider_model"] = str(config["provider_model"]).strip() or _RUNTIME_BASE_CONFIG["provider_model"]
    config["provider_base_url"] = str(config["provider_base_url"]).strip().rstrip("/") or _RUNTIME_BASE_CONFIG["provider_base_url"]
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
    config_file = _workspace_runtime_config_file()
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(
        json.dumps(pending, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _apply_workspace_runtime_config()
    result = _runtime_config_payload()
    result["pending_config"] = pending
    return result


@app.patch("/api/runtime/secrets")
async def update_runtime_secrets(payload: RuntimeSecretUpdate) -> dict:
    if payload.bigmodel_api_key is not None:
        provider.set_runtime_api_key(
            payload.bigmodel_api_key,
            api_key_file=_workspace_runtime_secret_file(),
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
    try:
        payload = json.loads(_workspace_runtime_config_file(root).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


@app.get("/api/runtime/statusline")
async def runtime_statusline(session_id: str | None = Query(default=None, max_length=80)) -> dict:
    unavailable_reason = None
    session = None
    if session_id:
        try:
            session = _get_current_chat_session(session_id, auto_switch=True)
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
            unavailable_reason = str(exc.detail)
            session = store.get_chat_session(session_id)
    token_usage = _context_budget_status(session.id).as_dict() if session else {
        **_empty_context_budget(settings.context_window_tokens),
    }
    context_window = settings.context_window_tokens
    return {
        "model": provider.model,
        "session_id": session.id if session else None,
        "thread_title": session.title if session else "新线程",
        "workspace": str(workspace_manager.current_root),
        "project": workspace_manager.current_root.name,
        "permission_mode": settings.permission_mode,
        "sandbox_mode": settings.sandbox_mode,
        "approval_policy": settings.approval_policy,
        "status": "unavailable" if unavailable_reason else ("working" if session_id and session is None else "ready"),
        "unavailable_reason": unavailable_reason,
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


@app.get("/api/persona/files/{scope}/{name}")
async def persona_file(scope: str, name: str) -> dict:
    return _agent_runtime().memory.read_persona_file(scope, name)


@app.post("/api/persona/files")
async def write_persona_file(payload: dict) -> dict:
    scope = str(payload.get("scope") or "project")
    name = str(payload.get("name") or "user.md")
    content = str(payload.get("content") or "")
    return _agent_runtime().memory.write_persona_file(scope, name, content)


@app.post("/api/memory/remember")
async def remember(payload: dict) -> dict:
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    return _agent_runtime().memory.propose_fact(text, source="api:remember")


@app.get("/api/memory/candidates")
async def memory_candidates(include_resolved: bool = Query(default=False)) -> dict:
    return {"items": _agent_runtime().memory.memory_candidates(include_resolved=include_resolved)}


@app.post("/api/memory/candidates/{candidate_id}/approve")
async def approve_memory_candidate(candidate_id: str) -> dict:
    try:
        return _agent_runtime().memory.approve_candidate(candidate_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Memory candidate not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/memory/candidates/{candidate_id}/edit")
async def edit_memory_candidate(candidate_id: str, payload: dict) -> dict:
    content = str(payload.get("content") or "").strip()
    name = str(payload.get("name") or "index.md")
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    try:
        return _agent_runtime().memory.edit_candidate(candidate_id, content=content, name=name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Memory candidate not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/memory/candidates/{candidate_id}/deny")
async def deny_memory_candidate(candidate_id: str, payload: dict) -> dict:
    reason = str(payload.get("reason") or "").strip()
    try:
        return _agent_runtime().memory.deny_candidate(candidate_id, reason=reason)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Memory candidate not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/approvals/pending")
async def list_pending_approvals(session_id: str | None = Query(default=None, max_length=80)) -> dict:
    return {"items": [item.as_dict() for item in agent_sessions.list_pending_approvals(session_id=session_id)]}


@app.post("/api/approvals/{approval_id}/approve")
async def approve_tool_call(approval_id: str) -> dict:
    item = agent_sessions.approve_pending_approval(approval_id)
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
    item = agent_sessions.deny_pending_approval(approval_id, reason=reason)
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
    assistant_message = ChatMessage(
        session_id=item.session_id,
        role=ChatRole.ASSISTANT,
        content=_denied_tool_alternative_message(
            tool=item.tool,
            arguments=item.arguments,
            reason=reason,
        ),
    )
    try:
        store.add_chat_message(assistant_message)
    except KeyError:
        raise HTTPException(status_code=404, detail="Chat session not found") from None
    return {
        "ok": True,
        "status": "denied",
        "approval": item.as_dict(),
        "event": event,
        "message": assistant_message.model_dump(mode="json"),
    }


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


@app.post("/api/tool-calls/retry")
async def retry_tool_call(payload: dict) -> dict:
    tool = str(payload.get("tool") or "").strip()
    arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
    if not tool:
        raise HTTPException(status_code=400, detail="tool is required")
    executor = app_module_tool_executor(_workspace_tools())
    call_id = new_id("tool")
    events, result_json = executor.run_one_stream(call_id, tool, arguments)
    return {"ok": True, "call_id": call_id, "events": events, "result_json": result_json}


@app.post("/api/tool-calls/{call_id}/cancel")
async def cancel_tool_call(call_id: str) -> dict:
    try:
        job = process_manager.cancel_call(call_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Tool call not found") from exc
    agent_sessions.request_cancel_for_call(call_id)
    return {"ok": True, "status": job["status"], "job": job}


@app.get("/api/workspaces")
async def workspace_list(q: str | None = Query(default=None, max_length=1200)) -> dict:
    return workspace_manager.status(query=q)


@app.post("/api/workspace/select")
async def select_workspace(payload: WorkspaceSelect) -> dict:
    try:
        _switch_workspace(payload.path)
        return workspace_manager.status()
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/workspace/folders")
async def create_workspace_folder(payload: WorkspaceFolderCreate) -> dict:
    if settings.permission_mode not in {"workspace_write", "bypass_permissions"}:
        raise HTTPException(status_code=403, detail="当前权限模式不允许新建目录")
    try:
        created = workspace_manager.create_folder(payload.path)
        _switch_workspace(str(created))
        return workspace_manager.status(query=str(created))
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/worktrees")
async def list_worktrees() -> dict:
    try:
        manager = _worktree_manager()
        current_name = manager.name_for_path(workspace_manager.current_root)
        return {
            "items": [item.as_dict() for item in manager.list()],
            "current": current_name,
            "repo_root": str(manager.repo_root),
        }
    except WorktreeError as exc:
        return {"items": [], "current": None, "repo_root": None, "error": str(exc)}


@app.post("/api/worktrees", status_code=201)
async def create_worktree(payload: WorktreeCreate) -> dict:
    try:
        manager = _worktree_manager()
        created = manager.create(payload.name)
        _switch_workspace(created["path"])
        return created
    except (WorktreeError, WorkspaceError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/worktrees/current/diff")
async def current_worktree_diff() -> dict:
    try:
        manager = _worktree_manager()
        current_name = manager.name_for_path(workspace_manager.current_root)
        if current_name is None:
            raise WorktreeError("当前项目不是 Nova 工作树，请先创建或切换到工作树")
        result = manager.diff(current_name)
        result["name"] = current_name
        return result
    except WorktreeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/worktrees/{name:path}")
async def delete_worktree(name: str, discard: bool = Query(default=False)) -> dict:
    try:
        manager = _worktree_manager()
        target_path = manager.path_for(name)
        if workspace_manager.current_root == target_path.resolve():
            _switch_workspace(str(manager.repo_root))
        return manager.remove(name, discard=discard)
    except WorktreeError as exc:
        status_code = 409 if "discard=true" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


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
                enabled=_git_available(),
                description="隔离变更，在 .nova/worktrees 中创建 Git worktree。",
            ),
            WorkspaceMode(
                id="cloud",
                label="云端",
                enabled=False,
                description="远程环境，后续版本实现。",
            ),
        ],
        permissions=WorkspacePermissions(
            workspace_write=settings.permission_mode in {"workspace_write", "bypass_permissions"},
            network_access=settings.network_access,
            approval_policy=_permission_mode_label(settings.permission_mode),
            permission_mode=settings.permission_mode,
            sandbox_mode=settings.sandbox_mode,
            approval_policy_id=settings.approval_policy,
            shell_commands=settings.permission_mode in {"workspace_write", "bypass_permissions"},
        ),
        commands=WorkspaceCommands(
            test="PYTHONPATH=src python3 -m unittest discover -s tests",
            serve=(
                "PYTHONPATH=src python3 -m nova_gateway.cli serve "
                "--host 127.0.0.1 --port 8765"
            ),
        ),
    )


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
    if _get_current_chat_session(session_id, auto_switch=True) is None:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return store.list_chat_messages(session_id)


@app.get("/api/chat/sessions/{session_id}/timeline")
async def list_chat_timeline(session_id: str) -> dict:
    if _get_current_chat_session(session_id, auto_switch=True) is None:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return {"items": _chat_timeline_items(session_id)}


@app.get("/api/chat/sessions/{session_id}/runtime-state")
async def chat_runtime_state(session_id: str) -> dict:
    unavailable_reason = None
    try:
        session = _get_current_chat_session(session_id, auto_switch=True)
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        session = store.get_chat_session(session_id)
        unavailable_reason = str(exc.detail)
    if session is None:
        raise HTTPException(status_code=404, detail="Chat session not found")
    runtime = agent_sessions.runtime_state(session_id)
    return {
        "session": session.model_dump(mode="json"),
        "timeline": {"items": [] if unavailable_reason else _chat_timeline_items(session_id)},
        "runtime": runtime,
        "pending_approvals": [
            item.as_dict() for item in agent_sessions.list_pending_approvals(session_id=session_id)
        ],
        "processes": process_manager.list_jobs(),
        "active": agent_sessions.is_active(session_id),
        "queued_messages": runtime["queued_messages"],
        "unavailable": bool(unavailable_reason),
        "unavailable_reason": unavailable_reason,
    }


def _chat_timeline_items(session_id: str) -> list[dict]:
    items: list[dict] = []
    for message in store.list_chat_messages(session_id):
        items.append({"kind": "message", "created_at": message.created_at, "item": message.model_dump(mode="json")})
    for event in store.list_chat_events(session_id):
        items.append({"kind": "event", "created_at": event.created_at, "item": event.model_dump(mode="json")})
    items.sort(key=lambda item: item["created_at"])
    return [{"kind": item["kind"], "item": item["item"]} for item in items]


@app.post("/api/chat/sessions/{session_id}/messages", response_model=ChatMessage)
async def create_chat_message(session_id: str, payload: ChatMessageCreate) -> ChatMessage:
    session = _get_current_chat_session(session_id, auto_switch=True)
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
    if _get_current_chat_session(session_id, auto_switch=True) is None:
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
        async def run_turn(user_message: ChatMessage, *, emit_user: bool) -> AsyncIterator[str]:
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
                agent_sessions.record_runtime_event(event)
                return event

            if emit_user:
                store.add_chat_message(user_message)
                yield _ndjson({"type": "user_message", "message": user_message.model_dump(mode="json")})

            task = store.create_task(
                Task(
                    id=new_id("task"),
                    prompt=user_message.content,
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
            agent_sessions.start_turn(
                session_id,
                turn_id=turn_id,
                user_message_id=user_message.id,
                task_id=task.id,
            )
            started = runtime_event(
                "turn.started",
                category="turn",
                phase="started",
                title="开始处理用户请求",
                message=user_message.content,
                data={"message_id": user_message.id, "task_id": task.id},
            )
            yield _ndjson({"type": "runtime_event", "event": started})

            answer_parts: list[str] = []
            try:
                history = [message for message in store.list_chat_messages(session_id) if message.id != user_message.id]
                all_turn_messages = [*history, user_message]
                budget = build_context_budget_plan(
                    session_id=session_id,
                    messages=all_turn_messages,
                    events=store.list_chat_events(session_id),
                    context_window_tokens=settings.context_window_tokens,
                )
                if budget.should_auto_compact and not user_message.content.lstrip().startswith("/compact"):
                    memory = ProjectMemory(workspace_manager.current_root, global_agent_file=settings.global_agent_file)
                    result = memory.compact_session(
                        all_turn_messages,
                        instruction="自动上下文预算触发：保留关键事实、当前目标、最近决策和未完成事项。",
                    )
                    compacted = runtime_event(
                        "memory.compacted",
                        category="status",
                        phase="completed",
                        title="自动上下文压缩",
                        message="上下文预算接近上限，已自动执行 /compact 并写入会话摘要。",
                        data={
                            "summary": str(result.get("summary") or ""),
                            "path": str(result.get("path") or ""),
                            "covered_messages": int(result.get("covered_messages") or 0),
                            "trigger": "auto_context_budget",
                        },
                    )
                    yield _ndjson({"type": "runtime_event", "event": compacted})

                budgeted = runtime_event(
                    "context.budgeted",
                    category="status",
                    phase="update",
                    title="上下文预算已应用",
                    message=(
                        f"保留 {budget.retained_message_count} 条最近消息，"
                        f"裁剪 {budget.dropped_message_count} 条历史消息，"
                        f"关键工具结果 {budget.key_tool_result_count} 条。"
                    ),
                    data={
                        **budget.as_dict(),
                        "message_ids": [message.id for message in budget.messages],
                    },
                )
                yield _ndjson({"type": "runtime_event", "event": budgeted})
                turn_messages = budget.messages
                async for event in _agent_runtime().stream(turn_messages):
                    runtime = _runtime_event_from_agent_event(event, runtime_event)
                    if runtime is not None:
                        yield _ndjson({"type": "runtime_event", "event": runtime})
                    if event["type"] == "permission_request":
                        agent_sessions.create_pending_approval(
                            session_id=session_id,
                            turn_id=turn_id,
                            call_id=str(event.get("call_id") or (runtime.get("id") if runtime else new_id("tool"))),
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
                agent_sessions.complete_turn(
                    session_id,
                    message_id=assistant_message.id,
                    content=assistant_message.content,
                )
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
                yield _ndjson(
                    {
                        "type": "assistant_done",
                        "message": assistant_message.model_dump(mode="json"),
                    }
                )
            except ProviderError as exc:
                agent_sessions.fail_turn(session_id, reason=str(exc))
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
                agent_sessions.fail_turn(session_id, reason=f"{type(exc).__name__}: {detail}")
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

        first_message = ChatMessage(
            session_id=session_id,
            role=ChatRole.USER,
            content=payload.content,
        )
        try:
            async for chunk in run_turn(first_message, emit_user=True):
                yield chunk
            while True:
                queued_messages = agent_sessions.drain_queued_messages(session_id)
                if not queued_messages:
                    break
                for queued in queued_messages:
                    yield _ndjson({"type": "queued_message", "message": queued.model_dump(mode="json")})
                    async for chunk in run_turn(queued, emit_user=False):
                        yield chunk
        finally:
            agent_sessions.mark_idle(session_id)

    return StreamingResponse(emit(), media_type="application/x-ndjson")


def _get_current_chat_session(session_id: str, *, auto_switch: bool = False) -> ChatSession | None:
    # 会话按项目目录隔离，避免切换项目后把上下文发进错误仓库。
    session = store.get_chat_session(session_id)
    if session is None:
        return None
    if session.workspace != str(workspace_manager.current_root):
        if auto_switch and session.workspace:
            try:
                _switch_workspace(session.workspace)
            except WorkspaceError as exc:
                raise HTTPException(status_code=409, detail=f"历史线程所属项目不可用：{exc}") from exc
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
    if event_type == "compact_done":
        return build_event(
            "memory.compacted",
            category="status",
            phase="completed",
            title=str(event.get("title") or "上下文已压缩"),
            message=str(event.get("message") or "会话摘要已写入 .nova/memory/session.md。"),
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
