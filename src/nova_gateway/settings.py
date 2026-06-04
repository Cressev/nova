from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    project_root: Path
    state_dir: Path
    static_dir: Path


def load_settings() -> Settings:
    project_root = Path(__file__).resolve().parents[2]
    return Settings(
        project_root=project_root,
        state_dir=project_root / ".nova",
        static_dir=project_root / "static",
    )

