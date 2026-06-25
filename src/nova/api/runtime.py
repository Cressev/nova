from __future__ import annotations

from fastapi import APIRouter

from . import routes as ctx

router = APIRouter()


@router.get("/api/runtime/config")
async def runtime_config() -> dict:
    return ctx._runtime_config_payload()


@router.patch("/api/runtime/config")
async def update_runtime_config(payload: ctx.RuntimeConfigUpdate) -> dict:
    pending = ctx._read_runtime_config_overrides()
    update = payload.model_dump(exclude_none=True)
    for key, value in update.items():
        if isinstance(value, str):
            pending[key] = value.strip()
        else:
            pending[key] = value
    config_file = ctx._workspace_runtime_config_file()
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(
        ctx.json.dumps(pending, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    ctx._apply_workspace_runtime_config()
    result = ctx._runtime_config_payload()
    result["pending_config"] = pending
    return result


@router.patch("/api/runtime/secrets")
async def update_runtime_secrets(payload: ctx.RuntimeSecretUpdate) -> dict:
    langfuse_status = None
    if payload.bigmodel_api_key is not None:
        ctx.provider.set_runtime_api_key(
            payload.bigmodel_api_key,
            api_key_file=ctx._workspace_runtime_secret_file(),
        )
    if (
        payload.langfuse_public_key is not None
        or payload.langfuse_secret_key is not None
        or payload.langfuse_host is not None
        or payload.langfuse_enabled is not None
    ):
        langfuse_status = ctx.update_langfuse_secrets(
            ctx._workspace_runtime_secret_file(),
            public_key=payload.langfuse_public_key,
            secret_key=payload.langfuse_secret_key,
            host=payload.langfuse_host,
            enabled=payload.langfuse_enabled,
        )
    if langfuse_status is None:
        langfuse_status = ctx.load_langfuse_config(
            ctx._workspace_runtime_secret_file()
        ).status()
    return {
        "ok": True,
        "api_key_set": ctx.provider.is_configured(),
        "api_key_source": ctx.provider.api_key_source(),
        "provider_configured": ctx.provider.is_configured(),
        "langfuse_configured": langfuse_status["configured"],
        "langfuse_public_key_set": langfuse_status["public_key_set"],
        "langfuse_secret_key_set": langfuse_status["secret_key_set"],
        "langfuse_host": langfuse_status["host"],
        "langfuse_enabled": langfuse_status["enabled"],
    }


@router.post("/api/runtime/restart")
async def restart_runtime() -> dict:
    def delayed_restart() -> None:
        ctx.time.sleep(0.35)
        ctx.os.execv(ctx.sys.executable, [ctx.sys.executable, *ctx.sys.argv])

    ctx.threading.Thread(target=delayed_restart, daemon=True).start()
    return {"ok": True, "message": "Nova 正在重启，配置会在进程重新加载后生效。"}


@router.get("/api/runtime/statusline")
async def runtime_statusline(
    session_id: str | None = ctx.Query(default=None, max_length=80)
) -> dict:
    unavailable_reason = None
    session = None
    if session_id:
        try:
            session = ctx._get_current_chat_session(session_id, auto_switch=True)
        except ctx.HTTPException as exc:
            if exc.status_code != 409:
                raise
            unavailable_reason = str(exc.detail)
            session = ctx.store.get_chat_session(session_id)
    token_usage = (
        ctx._context_budget_status(session.id).as_dict()
        if session
        else {
            **ctx._empty_context_budget(ctx.settings.context_window_tokens),
        }
    )
    context_window = ctx.settings.context_window_tokens
    processes = ctx.process_manager.list_jobs()
    background_task_count = len(
        [job for job in processes if job.get("status") in {"running", "started"}]
    )
    return {
        "model": ctx.provider.model,
        "session_id": session.id if session else None,
        "thread_title": session.title if session else "新线程",
        "workspace": str(ctx.workspace_manager.current_root),
        "project": ctx.workspace_manager.current_root.name,
        "current_project": ctx.workspace_manager.current_root.name,
        "current_project_path": str(ctx.workspace_manager.current_root),
        "permission_mode": ctx.settings.permission_mode,
        "sandbox_mode": ctx.settings.sandbox_mode,
        "approval_policy": ctx.settings.approval_policy,
        "background_task_count": background_task_count,
        "background_tasks": background_task_count,
        "status": (
            "unavailable"
            if unavailable_reason
            else ("working" if session_id and session is None else "ready")
        ),
        "unavailable_reason": unavailable_reason,
        "estimated": True,
        **token_usage,
    }


@router.get("/api/commands")
async def command_list() -> dict:
    return {"items": ctx.list_builtin_commands()}
