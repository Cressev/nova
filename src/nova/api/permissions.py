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
    result = await _session_runner().approve_tool_call(approval_id)
    if result is None:
        raise ctx.HTTPException(status_code=404, detail="Approval not found")
    return result


@router.post("/api/approvals/{approval_id}/deny")
async def deny_tool_call(approval_id: str, payload: dict | None = None) -> dict:
    reason = str((payload or {}).get("reason") or "用户拒绝执行")
    try:
        result = _session_runner().deny_tool_call(approval_id, reason=reason)
    except KeyError:
        raise ctx.HTTPException(
            status_code=404, detail="Chat session not found"
        ) from None
    if result is None:
        raise ctx.HTTPException(status_code=404, detail="Approval not found")
    return result


def _session_runner() -> ctx.SessionRunner:
    return ctx.SessionRunner(
        ctx.SessionRunDependencies(
            store=ctx.store,
            agent_sessions=ctx.agent_sessions,
            runtime_factory=ctx._agent_runtime,
            id_factory=ctx.new_id,
            persist_event=ctx._persist_runtime_event,
            runtime_event_from_agent_event=ctx._runtime_event_from_agent_event,
            build_context_budget_plan=ctx.build_context_budget_plan,
            context_window_tokens=ctx.settings.context_window_tokens,
            project_root_provider=lambda: ctx.workspace_manager.current_root,
            global_agent_file_provider=lambda: ctx.settings.global_agent_file,
            tool_orchestrator_factory=ctx._tool_orchestrator,
            event_builder_for_existing_turn=ctx._event_builder_for_existing_turn,
            denied_tool_message_builder=ctx._denied_tool_alternative_message,
            compaction_engine_factory=ctx._compaction_engine,
        )
    )
