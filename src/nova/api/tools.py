from __future__ import annotations

from fastapi import APIRouter

from . import routes as ctx

router = APIRouter()


@router.get("/api/tools")
async def tool_list() -> dict:
    return {"items": ctx._workspace_tools().list_specs()}


@router.get("/api/mcp/status")
async def mcp_status() -> dict:
    return ctx.McpManager(ctx.workspace_manager.current_root).status()


@router.get("/api/lsp/status")
async def lsp_status() -> dict:
    return ctx.LspManager(ctx.workspace_manager.current_root).status()


@router.get("/api/lsp/diagnostics")
async def lsp_diagnostics(
    path: str | None = ctx.Query(default=None, max_length=400)
) -> dict:
    try:
        return ctx.LspManager(ctx.workspace_manager.current_root).diagnostics(path=path)
    except ValueError as exc:
        raise ctx.HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/lsp/definition")
async def lsp_definition(
    path: str = ctx.Query(..., max_length=400),
    symbol: str = ctx.Query(..., max_length=160),
) -> dict:
    try:
        return ctx.LspManager(ctx.workspace_manager.current_root).definition(
            path=path, symbol=symbol
        )
    except ValueError as exc:
        raise ctx.HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/review/summary")
async def review_summary() -> dict:
    return ctx.ReviewManager(ctx.workspace_manager.current_root).summary()


@router.post("/api/review/run-tests")
async def review_run_tests(payload: dict | None = None) -> dict:
    command = payload.get("command") if isinstance(payload, dict) else None
    try:
        return ctx.ReviewManager(ctx.workspace_manager.current_root).run_tests(
            command=command
        )
    except ValueError as exc:
        raise ctx.HTTPException(status_code=400, detail=str(exc)) from exc
    except ctx.subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "command": str(exc.cmd),
            "exit_code": None,
            "stdout": (
                (exc.stdout or "")[-16000:] if isinstance(exc.stdout, str) else ""
            ),
            "stderr": "测试命令超时，Review 已停止等待。",
        }


@router.post("/api/mcp/tools/{tool_name}/call")
async def call_mcp_tool(tool_name: str, payload: dict) -> dict:
    arguments = (
        payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
    )
    executor = ctx.app_module_tool_executor(ctx._workspace_tools())
    call_id = ctx.new_id("mcp")
    try:
        events, result_json = executor.run_one_stream(call_id, tool_name, arguments)
    except ValueError as exc:
        raise ctx.HTTPException(status_code=404, detail=str(exc)) from exc
    result_payload = ctx.json.loads(result_json) if result_json else {}
    if not result_payload.get("ok"):
        raise ctx.HTTPException(status_code=400, detail=result_payload)
    data = (
        result_payload.get("data")
        if isinstance(result_payload.get("data"), dict)
        else {}
    )
    mcp_data = data.get("mcp") if isinstance(data.get("mcp"), dict) else {}
    return {
        "ok": True,
        "call_id": call_id,
        "tool": tool_name,
        "server": mcp_data.get("server"),
        "result": {"content": result_payload.get("output") or ""},
        "events": events,
        "result_json": result_payload,
    }


@router.get("/api/skills/status")
async def skills_status() -> dict:
    return ctx.SkillManager(ctx.workspace_manager.current_root).status()


@router.get("/api/skills/{scope}/{name}")
async def skill_detail(scope: str, name: str) -> dict:
    skill = ctx.SkillManager(ctx.workspace_manager.current_root).find(name, scope=scope)
    if skill is None:
        raise ctx.HTTPException(status_code=404, detail="Skill not found")
    return skill.as_detail()
