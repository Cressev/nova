from __future__ import annotations

from pathlib import Path


class WorkspaceError(ValueError):
    """工作区切换失败，API 层会返回给前端显示。"""


class WorkspaceManager:
    def __init__(self, *, initial_root: Path, allowed_roots: list[Path]) -> None:
        self.allowed_roots = [path.resolve() for path in allowed_roots]
        self.current_root = self._validate(initial_root)

    def set_current(self, path: str) -> Path:
        self.current_root = self._validate(Path(path).expanduser())
        return self.current_root

    def status(self) -> dict:
        return {
            "current_root": str(self.current_root),
            "allowed_roots": [str(path) for path in self.allowed_roots],
            "candidates": [str(path) for path in self._candidate_projects()],
        }

    def _validate(self, path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.exists() or not resolved.is_dir():
            raise WorkspaceError(f"目录不存在：{path}")
        if not self._is_allowed(resolved):
            raise WorkspaceError("目录不在 NOVA_ALLOWED_WORKSPACE_ROOTS 允许范围内")
        return resolved

    def _is_allowed(self, path: Path) -> bool:
        for root in self.allowed_roots:
            if path == root or root in path.parents:
                return True
        return False

    def _candidate_projects(self) -> list[Path]:
        candidates: list[Path] = []
        for root in self.allowed_roots:
            if not root.exists():
                continue
            for child in sorted(root.iterdir()):
                if len(candidates) >= 80:
                    return candidates
                if not child.is_dir() or child.name.startswith("."):
                    continue
                if (child / ".git").exists() or (child / "AGENTS.md").exists():
                    candidates.append(child.resolve())
        return candidates
