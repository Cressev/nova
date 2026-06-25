from __future__ import annotations

from fastapi import APIRouter

from . import routes as ctx

router = APIRouter()


@router.get("/", include_in_schema=False)
async def index() -> ctx.HTMLResponse:
    index_path = ctx.settings.static_dir / "index.html"
    app_js_path = ctx.settings.static_dir / "js" / "app.js"
    version = int(app_js_path.stat().st_mtime)
    html = index_path.read_text(encoding="utf-8").replace(
        'src="/static/js/app.js"',
        f'src="/static/js/app.js?v={version}"',
    )
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
