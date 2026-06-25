from __future__ import annotations

from fastapi import APIRouter

from . import routes as ctx

router = APIRouter()


@router.get("/api/memory/status")
async def memory_status() -> dict:
    return ctx._agent_runtime().memory.status()


@router.get("/api/memory/files/{name}")
async def memory_file(name: str) -> dict:
    return ctx._agent_runtime().memory.read_file(name)


@router.post("/api/memory/files")
async def write_memory_file(payload: dict) -> dict:
    name = str(payload.get("name") or "index.md")
    content = str(payload.get("content") or "")
    return ctx._agent_runtime().memory.write_file(name, content)


@router.get("/api/persona/files/{scope}/{name}")
async def persona_file(scope: str, name: str) -> dict:
    return ctx._agent_runtime().memory.read_persona_file(scope, name)


@router.post("/api/persona/files")
async def write_persona_file(payload: dict) -> dict:
    scope = str(payload.get("scope") or "project")
    name = str(payload.get("name") or "user.md")
    content = str(payload.get("content") or "")
    return ctx._agent_runtime().memory.write_persona_file(scope, name, content)


@router.post("/api/memory/remember")
async def remember(payload: dict) -> dict:
    text = str(payload.get("text") or "").strip()
    if not text:
        raise ctx.HTTPException(status_code=400, detail="text is required")
    return ctx._agent_runtime().memory.propose_fact(text, source="api:remember")


@router.get("/api/memory/candidates")
async def memory_candidates(include_resolved: bool = ctx.Query(default=False)) -> dict:
    return {
        "items": ctx._agent_runtime().memory.memory_candidates(
            include_resolved=include_resolved
        )
    }


@router.post("/api/memory/candidates/{candidate_id}/approve")
async def approve_memory_candidate(candidate_id: str) -> dict:
    try:
        return ctx._agent_runtime().memory.approve_candidate(candidate_id)
    except KeyError as exc:
        raise ctx.HTTPException(
            status_code=404, detail="Memory candidate not found"
        ) from exc
    except ValueError as exc:
        raise ctx.HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/memory/candidates/{candidate_id}/edit")
async def edit_memory_candidate(candidate_id: str, payload: dict) -> dict:
    content = str(payload.get("content") or "").strip()
    name = str(payload.get("name") or "index.md")
    if not content:
        raise ctx.HTTPException(status_code=400, detail="content is required")
    try:
        return ctx._agent_runtime().memory.edit_candidate(
            candidate_id, content=content, name=name
        )
    except KeyError as exc:
        raise ctx.HTTPException(
            status_code=404, detail="Memory candidate not found"
        ) from exc
    except ValueError as exc:
        raise ctx.HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/memory/candidates/{candidate_id}/deny")
async def deny_memory_candidate(candidate_id: str, payload: dict) -> dict:
    reason = str(payload.get("reason") or "").strip()
    try:
        return ctx._agent_runtime().memory.deny_candidate(candidate_id, reason=reason)
    except KeyError as exc:
        raise ctx.HTTPException(
            status_code=404, detail="Memory candidate not found"
        ) from exc
    except ValueError as exc:
        raise ctx.HTTPException(status_code=409, detail=str(exc)) from exc
