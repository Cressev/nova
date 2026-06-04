from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    project_root: Path
    state_dir: Path
    static_dir: Path
    initial_workspace_root: Path
    allowed_workspace_roots: list[Path]
    global_agent_file: Path
    provider_base_url: str
    provider_model: str
    permission_mode: str
    network_access: bool
    max_tool_rounds: int


def _parse_paths(value: str | None, fallback: list[Path]) -> list[Path]:
    if not value:
        return fallback
    return [Path(item).expanduser().resolve() for item in value.split(os.pathsep) if item.strip()]


def load_settings() -> Settings:
    project_root = Path(__file__).resolve().parents[2]
    allowed_roots = _parse_paths(
        os.getenv("NOVA_ALLOWED_WORKSPACE_ROOTS"),
        [project_root.parent.resolve()],
    )
    permission_mode = os.getenv("NOVA_PERMISSION_MODE", "workspace_write").strip()
    if permission_mode not in {"read_only", "ask", "workspace_write"}:
        permission_mode = "ask"
    return Settings(
        project_root=project_root,
        state_dir=project_root / ".nova",
        static_dir=project_root / "static",
        initial_workspace_root=Path(os.getenv("NOVA_PROJECT_ROOT", str(project_root))).expanduser().resolve(),
        allowed_workspace_roots=allowed_roots,
        global_agent_file=Path(os.getenv("NOVA_GLOBAL_AGENT_FILE", "~/.nova/AGENTS.md")).expanduser().resolve(),
        provider_base_url=os.getenv("BIGMODEL_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
        provider_model=os.getenv("BIGMODEL_MODEL", "glm-4.7"),
        permission_mode=permission_mode,
        network_access=os.getenv("NOVA_NETWORK_ACCESS", "false").lower() == "true",
        max_tool_rounds=max(1, min(int(os.getenv("NOVA_MAX_TOOL_ROUNDS", "6")), 12)),
    )
