from __future__ import annotations

from fastapi import APIRouter

from . import routes as ctx

router = APIRouter()


@router.get("/api/workspaces")
async def workspace_list(
    q: str | None = ctx.Query(default=None, max_length=1200)
) -> dict:
    return ctx.workspace_manager.status(query=q)


@router.post("/api/workspace/select")
async def select_workspace(payload: ctx.WorkspaceSelect) -> dict:
    try:
        ctx._switch_workspace(payload.path)
        return ctx.workspace_manager.status()
    except ctx.WorkspaceError as exc:
        raise ctx.HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/workspace/folders")
async def create_workspace_folder(payload: ctx.WorkspaceFolderCreate) -> dict:
    if ctx.settings.permission_mode not in {"workspace_write", "bypass_permissions"}:
        raise ctx.HTTPException(status_code=403, detail="当前权限模式不允许新建目录")
    try:
        created = ctx.workspace_manager.create_folder(payload.path)
        ctx._switch_workspace(str(created))
        return ctx.workspace_manager.status(query=str(created))
    except ctx.WorkspaceError as exc:
        raise ctx.HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/worktrees")
async def list_worktrees() -> dict:
    try:
        manager = ctx._worktree_manager()
        current_name = manager.name_for_path(ctx.workspace_manager.current_root)
        return {
            "items": [item.as_dict() for item in manager.list()],
            "current": current_name,
            "repo_root": str(manager.repo_root),
        }
    except ctx.WorktreeError as exc:
        return {"items": [], "current": None, "repo_root": None, "error": str(exc)}


@router.post("/api/worktrees", status_code=201)
async def create_worktree(payload: ctx.WorktreeCreate) -> dict:
    try:
        manager = ctx._worktree_manager()
        created = manager.create(payload.name)
        ctx._switch_workspace(created["path"])
        return created
    except (ctx.WorktreeError, ctx.WorkspaceError) as exc:
        raise ctx.HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/worktrees/current/diff")
async def current_worktree_diff() -> dict:
    try:
        manager = ctx._worktree_manager()
        current_name = manager.name_for_path(ctx.workspace_manager.current_root)
        if current_name is None:
            raise ctx.WorktreeError("当前项目不是 Nova 工作树，请先创建或切换到工作树")
        result = manager.diff(current_name)
        result["name"] = current_name
        return result
    except ctx.WorktreeError as exc:
        raise ctx.HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/worktrees/{name:path}")
async def delete_worktree(name: str, discard: bool = ctx.Query(default=False)) -> dict:
    try:
        manager = ctx._worktree_manager()
        target_path = manager.path_for(name)
        if ctx.workspace_manager.current_root == target_path.resolve():
            ctx._switch_workspace(str(manager.repo_root))
        return manager.remove(name, discard=discard)
    except ctx.WorktreeError as exc:
        status_code = 409 if "discard=true" in str(exc) else 400
        raise ctx.HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.get("/api/workspace/status", response_model=ctx.WorkspaceStatus)
async def workspace_status(
    quick: bool = ctx.Query(default=False),
) -> ctx.WorkspaceStatus:
    git_status = ctx._read_git_status(quick=quick)
    return ctx.WorkspaceStatus(
        project_root=str(ctx.workspace_manager.current_root),
        git=git_status,
        modes=[
            ctx.WorkspaceMode(
                id="local",
                label="本地",
                enabled=True,
                description="直接在当前项目目录中工作。",
            ),
            ctx.WorkspaceMode(
                id="worktree",
                label="工作树",
                enabled=git_status.available,
                description="隔离变更，在.nova/worktrees 中创建 Git worktree。",
            ),
            ctx.WorkspaceMode(
                id="cloud",
                label="云端",
                enabled=False,
                description="远程环境，后续版本实现。",
            ),
        ],
        permissions=ctx.WorkspacePermissions(
            workspace_write=ctx.settings.permission_mode
            in {"workspace_write", "bypass_permissions"},
            network_access=ctx.settings.network_access,
            approval_policy=ctx._permission_mode_label(ctx.settings.permission_mode),
            permission_mode=ctx.settings.permission_mode,
            sandbox_mode=ctx.settings.sandbox_mode,
            approval_policy_id=ctx.settings.approval_policy,
            shell_commands=ctx.settings.permission_mode
            in {"workspace_write", "bypass_permissions"},
        ),
        commands=ctx.WorkspaceCommands(
            test="PYTHONPATH=src python3 -m unittest discover -s tests",
            serve=(
                "PYTHONPATH=src python3 -m nova.cli serve "
                "--host 127.0.0.1 --port 8765"
            ),
        ),
    )
