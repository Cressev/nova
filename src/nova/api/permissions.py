from __future__ import annotations

from fastapi import APIRouter

from . import routes as ctx

router = APIRouter()


@router.get("/api/approvals/pending")
async def list_pending_approvals(
    session_id: str | None = ctx.Query(default=None, max_length=80)
) -> dict:
    return {
        "items": [
            item.as_dict()
            for item in ctx.agent_sessions.list_pending_approvals(session_id=session_id)
        ]
    }


@router.post("/api/approvals/{approval_id}/approve")
async def approve_tool_call(approval_id: str) -> dict:
    item = ctx.agent_sessions.approve_pending_approval(approval_id)
    if item is None:
        raise ctx.HTTPException(status_code=404, detail="Approval not found")
    tools = ctx.WorkspaceTools(
        ctx.workspace_manager.current_root,
        permission_mode="workspace_write",
        sandbox_mode=ctx.settings.sandbox_mode,
        network_access=ctx.settings.network_access,
        zai_api_key=ctx.provider.api_key_for_tools(),
    )
    executor = ctx.app_module_tool_executor(tools)
    events, result_json = executor.run_one_stream(
        item.call_id, item.tool, item.arguments
    )
    for event in events:
        runtime_event = ctx._runtime_event_from_agent_event(
            event,
            ctx._event_builder_for_existing_turn(item.session_id, item.turn_id),
        )
        if runtime_event is not None:
            ctx._persist_runtime_event(runtime_event)
    return {
        "ok": True,
        "status": "approved",
        "approval": item.as_dict(),
        "events": events,
        "result_json": result_json,
    }


@router.post("/api/approvals/{approval_id}/deny")
async def deny_tool_call(approval_id: str, payload: dict | None = None) -> dict:
    reason = str((payload or {}).get("reason") or "用户拒绝执行")
    item = ctx.agent_sessions.deny_pending_approval(approval_id, reason=reason)
    if item is None:
        raise ctx.HTTPException(status_code=404, detail="Approval not found")
    event = ctx._event_builder_for_existing_turn(item.session_id, item.turn_id)(
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
    ctx._persist_runtime_event(event)
    assistant_message = ctx.ChatMessage(
        session_id=item.session_id,
        role=ctx.ChatRole.ASSISTANT,
        content=ctx._denied_tool_alternative_message(
            tool=item.tool,
            arguments=item.arguments,
            reason=reason,
        ),
    )
    try:
        ctx.store.add_chat_message(assistant_message)
    except KeyError:
        raise ctx.HTTPException(
            status_code=404, detail="Chat session not found"
        ) from None
    return {
        "ok": True,
        "status": "denied",
        "approval": item.as_dict(),
        "event": event,
        "message": assistant_message.model_dump(mode="json"),
    }
