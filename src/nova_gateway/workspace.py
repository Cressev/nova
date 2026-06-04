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

    def status(self, query: str | None = None) -> dict:
        return {
            "current_root": str(self.current_root),
            "allowed_roots": [str(path) for path in self.allowed_roots],
            "candidates": [str(path) for path in self._candidate_projects(query)],
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

    def _candidate_projects(self, query: str | None = None) -> list[Path]:
        candidates: list[Path] = []
        seen: set[Path] = set()
        query_text = (query or "").strip()

        def add(path: Path) -> None:
            resolved = path.resolve()
            if resolved in seen or len(candidates) >= 80:
                return
            if resolved.is_dir() and self._is_allowed(resolved) and not resolved.name.startswith("."):
                seen.add(resolved)
                candidates.append(resolved)

        if query_text:
            query_path = Path(query_text).expanduser()
            parent = query_path if query_text.endswith(("/", "\\")) else query_path.parent
            prefix = "" if query_text.endswith(("/", "\\")) else query_path.name.lower()
            if parent.exists() and parent.is_dir() and self._is_allowed(parent.resolve()):
                for child in sorted(parent.iterdir()):
                    if len(candidates) >= 80:
                        return candidates
                    if child.is_dir() and child.name.lower().startswith(prefix):
                        add(child)

        for root in self.allowed_roots:
            if not root.exists():
                continue
            for child in sorted(root.iterdir()):
                if len(candidates) >= 80:
                    return candidates
                if not child.is_dir() or child.name.startswith("."):
                    continue
                if query_text:
                    haystack = f"{child.name}\n{child}".lower()
                    if query_text.lower() not in haystack:
                        continue
                if (child / ".git").exists() or (child / "AGENTS.md").exists():
                    add(child)
        return candidates
