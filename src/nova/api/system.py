from __future__ import annotations

from fastapi import APIRouter

from . import routes as ctx

router = APIRouter()


@router.get("/", include_in_schema=False)
async def index() -> ctx.HTMLResponse:
    # 前端已迁移 Vite + React：产物在 static/index.html，资源带内容 hash，
    # 无需再按 app.js mtime 拼版本参数（旧 vanilla 静态结构的破缓存手段）。
    index_path = ctx.settings.static_dir / "index.html"
    html = index_path.read_text(encoding="utf-8")
    return ctx.HTMLResponse(
        html,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


@router.get("/favicon.ico", include_in_schema=False)
async def favicon() -> ctx.Response:
    return ctx.Response(status_code=204)


@router.get("/api/health", response_model=ctx.Health)
async def health() -> ctx.Health:
    return ctx.Health(ok=True, service="nova", version=ctx.__version__)


@router.get("/api/provider")
async def provider_status() -> dict:
    return {
        "provider": "bigmodel",
        "model": ctx.provider.model,
        "base_url": ctx.provider.base_url,
        "configured": ctx.provider.is_configured(),
        "api_key_source": ctx.provider.api_key_source(),
        "api_key_env": ctx.provider.api_key_env,
    }
