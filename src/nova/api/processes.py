from __future__ import annotations

from fastapi import APIRouter

from . import routes as ctx

router = APIRouter()


@router.get("/api/processes")
async def list_processes(
    session_id: str | None = ctx.Query(default=None, max_length=80)
) -> dict:
    if session_id and ctx.store.get_chat_session(session_id) is None:
        raise ctx.HTTPException(status_code=404, detail="Chat session not found")
    return {"items": ctx._process_jobs_for_session(session_id)}


@router.get("/api/processes/{job_id}")
async def get_process(job_id: str) -> dict:
    job = ctx.process_manager.get(job_id)
    if job is None:
        raise ctx.HTTPException(status_code=404, detail="Process not found")
    return job


@router.delete("/api/processes/{job_id}")
async def kill_process(job_id: str) -> dict:
    try:
        job = ctx.process_manager.kill(job_id)
    except KeyError as exc:
        raise ctx.HTTPException(status_code=404, detail="Process not found") from exc
    call_id = str(job.get("call_id") or "")
    session_id = ctx.agent_sessions.session_id_for_call_id(call_id) if call_id else None
    if session_id:
        ctx._record_control_event(
            session_id,
            "process.killed",
            category="status",
            phase="completed",
            title="用户终止后台任务",
            message=f"后台任务 {job_id} 已被用户终止。",
            call_id=call_id,
            data={"job": job, "job_id": job_id},
        )
    return job


@router.post("/api/tool-calls/retry")
async def retry_tool_call(payload: dict) -> dict:
    tool = str(payload.get("tool") or "").strip()
    arguments = (
        payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
    )
    if not tool:
        raise ctx.HTTPException(status_code=400, detail="tool is required")
    executor = ctx.app_module_tool_executor(ctx._workspace_tools())
    call_id = ctx.new_id("tool")
    events, result_json = executor.run_one_stream(call_id, tool, arguments)
    return {
        "ok": True,
        "call_id": call_id,
        "events": events,
        "result_json": result_json,
    }


@router.post("/api/tool-calls/{call_id}/cancel")
async def cancel_tool_call(call_id: str) -> dict:
    try:
        job = ctx.process_manager.cancel_call(call_id)
    except KeyError as exc:
        raise ctx.HTTPException(status_code=404, detail="Tool call not found") from exc
    session_id = ctx.agent_sessions.request_cancel_for_call(call_id)
    if session_id:
        ctx._record_control_event(
            session_id,
            "tool.cancel.requested",
            category="status",
            phase="requested",
            status="pending",
            title="用户请求取消工具调用",
            message=f"工具调用 {call_id} 已收到取消请求。",
            call_id=call_id,
            data={"job": job, "job_id": job.get("id")},
        )
    return {"ok": True, "status": job["status"], "job": job}
