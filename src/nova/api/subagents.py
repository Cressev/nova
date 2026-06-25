from __future__ import annotations

from fastapi import APIRouter

from . import routes as ctx

router = APIRouter()


@router.get("/api/subagents")
async def list_subagents() -> dict:
    return {"items": ctx.subagent_manager.list()}


@router.post("/api/subagents", status_code=201)
async def spawn_subagent(payload: dict) -> dict:
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        raise ctx.HTTPException(status_code=400, detail="prompt is required")
    name = str(payload.get("name") or "worker").strip()
    return ctx.subagent_manager.spawn(
        prompt=prompt,
        name=name,
        project_root=ctx.workspace_manager.current_root,
    )


@router.get("/api/subagents/{agent_id}")
async def get_subagent(agent_id: str) -> dict:
    run = ctx.subagent_manager.get(agent_id)
    if run is None:
        raise ctx.HTTPException(status_code=404, detail="Subagent not found")
    return run


@router.post("/api/subagents/{agent_id}/wait")
async def wait_subagent(agent_id: str, payload: dict | None = None) -> dict:
    timeout_ms = 1000
    if isinstance(payload, dict):
        timeout_ms = int(payload.get("timeout_ms") or timeout_ms)
    run = ctx.subagent_manager.wait(agent_id, timeout_ms=timeout_ms)
    if run is None:
        raise ctx.HTTPException(status_code=404, detail="Subagent not found")
    return run


@router.delete("/api/subagents/{agent_id}")
async def close_subagent(agent_id: str) -> dict:
    run = ctx.subagent_manager.close(agent_id)
    if run is None:
        raise ctx.HTTPException(status_code=404, detail="Subagent not found")
    return run
