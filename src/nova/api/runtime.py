from __future__ import annotations

from fastapi import APIRouter

from . import routes as ctx

router = APIRouter()


@router.get("/api/runtime/config")
async def runtime_config() -> dict:
    return ctx._runtime_config_payload()


@router.get("/api/runtime/models")
async def runtime_model_list() -> dict:
    """设置面板"自动获取模型列表"：用当前全局 provider 拉可用模型。

    OpenAI 兼容协议走 GET {base_url}/models，Anthropic 走 /v1/models；
    失败返回 502 + 明确原因（不静默降级），前端提示改手动输入。
    """
    if not hasattr(ctx.provider, "list_models"):
        raise ctx.HTTPException(status_code=501, detail="当前提供方不支持模型列表获取。")
    if not ctx.provider.is_configured():
        raise ctx.HTTPException(
            status_code=400,
            detail=f"未配置 {ctx.provider.api_key_env}，请先在设置页填写 API Key。",
        )
    try:
        models = await ctx.provider.list_models()
    except ctx.ProviderError as exc:
        raise ctx.HTTPException(status_code=502, detail=str(exc))
    return {
        "ok": True,
        "count": len(models),
        "models": models,
        "current": ctx.provider.model,
    }


@router.post("/api/runtime/models")
async def probe_model_list(payload: dict) -> dict:
    """设置面板"获取可用模型"（dsh 探测语义）：用表单当前填的端点/协议/密钥
    临时构造 provider 拉列表，不改动全局 provider、不写配置。

    支持对任意供应商组探测——包括尚未保存的 key（dsh：asking with a key
    the form has but not yet stored）。
    """
    protocol = str(payload.get("protocol") or "openai")
    base_url = str(payload.get("base_url") or "").rstrip("/")
    api_key = str(payload.get("api_key") or "").strip()
    if not api_key:
        raise ctx.HTTPException(status_code=400, detail="请先填写该供应商的 API Key。")
    if protocol == "anthropic":
        from ..providers.anthropic import AnthropicProvider
        probe = AnthropicProvider(base_url=base_url or None)
    else:
        from ..providers.bigmodel import BigModelProvider
        probe = BigModelProvider(base_url=base_url or None)
    probe.set_runtime_api_key(api_key)
    if not hasattr(probe, "list_models"):
        raise ctx.HTTPException(status_code=501, detail="该协议不支持模型列表获取。")
    try:
        models = await probe.list_models()
    except ctx.ProviderError as exc:
        raise ctx.HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "count": len(models), "models": models}


@router.patch("/api/runtime/config")
async def update_runtime_config(payload: ctx.RuntimeConfigUpdate) -> dict:
    pending = ctx._read_runtime_config_overrides()
    update = payload.model_dump(exclude_none=True)
    for key, value in update.items():
        if isinstance(value, str):
            pending[key] = value.strip()
        elif isinstance(value, list):
            # 列表（custom_models）：条目去空白、去空、去重，保序
            cleaned: list = []
            for item in value:
                text = item.strip() if isinstance(item, str) else item
                if text and text not in cleaned:
                    cleaned.append(text)
            pending[key] = cleaned
        else:
            pending[key] = value
    # 全局单源：设置写入 ~/.nova/config/runtime-config.json（不随工作区分裂）
    config_file = ctx.settings.runtime_config_file
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(
        ctx.json.dumps(pending, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    ctx._apply_workspace_runtime_config()
    # dsh approval/policy 对齐：审批/沙箱策略切换是 log-only 事件，落到当前活跃会话
    # （无活跃会话时静默跳过——它本来就不进模型 transcript，只是审计日志）。
    if "permission_mode" in update or "sandbox_mode" in update:
        _emit_permission_policy_event(update)
    result = ctx._runtime_config_payload()
    result["pending_config"] = pending
    return result


def _write_profile_api_key(profile_id: str, api_key: str, secret_file) -> None:
    """把某组的密钥写到 api_keys[profile_id] 槽位（多组隔离）。"""
    import json as _json
    try:
        existing: dict = {}
        if secret_file.exists():
            loaded = _json.loads(secret_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
    except (OSError, _json.JSONDecodeError):
        existing = {}
    keys = existing.get("api_keys")
    if not isinstance(keys, dict):
        keys = {}
    value = (api_key or "").strip()
    if value:
        keys[profile_id] = value
    else:
        keys.pop(profile_id, None)
    existing["api_keys"] = keys
    try:
        secret_file.parent.mkdir(parents=True, exist_ok=True)
        secret_file.write_text(_json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _emit_permission_policy_event(update: dict) -> None:
    active = [sid for sid in ctx.agent_sessions.active_session_ids]
    if not active:
        return
    parts = [
        f"{key}={update[key]}"
        for key in ("permission_mode", "sandbox_mode")
        if key in update
    ]
    for session_id in sorted(active)[:1]:
        try:
            ctx.store.upsert_chat_event(
                ctx.ChatEvent(
                    session_id=session_id,
                    type="permission",
                    event_type="permission.policy",
                    phase="completed",
                    title="权限策略已切换",
                    message="；".join(parts),
                    data={"policy": update.get("permission_mode", ""), "sandbox": update.get("sandbox_mode", ""), **update},
                )
            )
        except Exception:
            # 审计事件失败不阻断配置切换
            pass


@router.patch("/api/runtime/secrets")
async def update_runtime_secrets(payload: ctx.RuntimeSecretUpdate) -> dict:
    langfuse_status = None
    if payload.bigmodel_api_key is not None:
        # 多供应商组：按 profile_id 写到 api_keys[profile_id] 槽位（dsh 语义：
        # 每组独立密钥）；未指定时回落到旧的单 api_key 槽位（向后兼容）。
        ctx.provider.set_runtime_api_key(
            payload.bigmodel_api_key,
            api_key_file=ctx._workspace_runtime_secret_file(),
        )
        profile_id = payload.profile_id or getattr(ctx.settings, "provider_preset", "bigmodel")
        _write_profile_api_key(profile_id, payload.bigmodel_api_key, ctx._workspace_runtime_secret_file())
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


@router.get("/api/agent/system-prompt")
async def get_agent_system_prompt() -> dict:
    """轨迹表 SYSTEM 首行的数据源（老会话无 system.prompt 事件时前端虚拟行使用）。"""
    runtime = ctx._agent_runtime()
    provider = getattr(runtime, "_system_prompt", None)
    return {"prompt": provider() if callable(provider) else ""}


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
    processes = ctx._process_jobs_for_session(session.id if session else None)
    background_task_count = len(
        [job for job in processes if job.get("status") in {"running", "started"}]
    )
    session_usage = getattr(ctx.provider, "session_usage", None) or {}
    return {
        "model": ctx.provider.model,
        "session_token_usage": dict(session_usage),
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
