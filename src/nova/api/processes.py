from __future__ import annotations

from fastapi import APIRouter

from . import routes as ctx

router = APIRouter()


@router.get("/api/processes")
async def list_processes() -> dict:
    return {"items": ctx.process_manager.list_jobs()}


@router.get("/api/processes/{job_id}")
async def get_process(job_id: str) -> dict:
    job = ctx.process_manager.get(job_id)
    if job is None:
        raise ctx.HTTPException(status_code=404, detail="Process not found")
    return job


@router.delete("/api/processes/{job_id}")
async def kill_process(job_id: str) -> dict:
    try:
        return ctx.process_manager.kill(job_id)
    except KeyError as exc:
        raise ctx.HTTPException(status_code=404, detail="Process not found") from exc


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
    ctx.agent_sessions.request_cancel_for_call(call_id)
    return {"ok": True, "status": job["status"], "job": job}
