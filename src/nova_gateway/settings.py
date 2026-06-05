from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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
    context_window_tokens: int
    runtime_config_file: Path


def _parse_paths(value: str | None, fallback: list[Path]) -> list[Path]:
    if not value:
        return fallback
    return [Path(item).expanduser().resolve() for item in value.split(os.pathsep) if item.strip()]


def _load_runtime_overrides(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _string_override(overrides: dict[str, Any], key: str, fallback: str) -> str:
    value = overrides.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _int_override(overrides: dict[str, Any], key: str, fallback: int, *, minimum: int, maximum: int) -> int:
    value = overrides.get(key, fallback)
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = fallback
    return max(minimum, min(number, maximum))


def _env_int(name: str, fallback: int) -> int:
    try:
        return int(os.getenv(name, str(fallback)))
    except ValueError:
        return fallback


def _bool_override(overrides: dict[str, Any], key: str, fallback: bool) -> bool:
    value = overrides.get(key, fallback)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    return fallback


def load_settings() -> Settings:
    project_root = Path(__file__).resolve().parents[2]
    runtime_config_file = project_root / ".nova" / "runtime-config.json"
    overrides = _load_runtime_overrides(runtime_config_file)
    allowed_roots = _parse_paths(
        os.getenv("NOVA_ALLOWED_WORKSPACE_ROOTS"),
        [project_root.parent.resolve()],
    )
    permission_mode = _string_override(
        overrides,
        "permission_mode",
        os.getenv("NOVA_PERMISSION_MODE", "workspace_write").strip(),
    )
    if permission_mode not in {"read_only", "ask", "workspace_write"}:
        permission_mode = "ask"
    return Settings(
        project_root=project_root,
        state_dir=project_root / ".nova",
        static_dir=project_root / "static",
        initial_workspace_root=Path(os.getenv("NOVA_PROJECT_ROOT", str(project_root))).expanduser().resolve(),
        allowed_workspace_roots=allowed_roots,
        global_agent_file=Path(os.getenv("NOVA_GLOBAL_AGENT_FILE", "~/.nova/AGENTS.md")).expanduser().resolve(),
        provider_base_url=_string_override(
            overrides,
            "provider_base_url",
            os.getenv("BIGMODEL_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
        ),
        provider_model=_string_override(overrides, "provider_model", os.getenv("BIGMODEL_MODEL", "glm-4.7")),
        permission_mode=permission_mode,
        network_access=_bool_override(overrides, "network_access", os.getenv("NOVA_NETWORK_ACCESS", "false").lower() == "true"),
        max_tool_rounds=_int_override(
            overrides,
            "max_tool_rounds",
            _env_int("NOVA_MAX_TOOL_ROUNDS", 6),
            minimum=1,
            maximum=12,
        ),
        context_window_tokens=_int_override(
            overrides,
            "context_window_tokens",
            _env_int("NOVA_CONTEXT_WINDOW_TOKENS", 128000),
            minimum=8192,
            maximum=1000000,
        ),
        runtime_config_file=runtime_config_file,
    )
