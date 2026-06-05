from __future__ import annotations

from pathlib import Path


class WorkspaceError(ValueError):
    """工作区切换失败，API 层会返回给前端显示。"""


class WorkspaceManager:
    def __init__(self, *, initial_root: Path, allowed_roots: list[Path]) -> None:
        self.allowed_roots = [path.resolve() for path in allowed_roots]
        self.browse_roots = self._derive_browse_roots(self.allowed_roots)
        self.current_root = self._validate(initial_root)

    def set_current(self, path: str) -> Path:
        self.current_root = self._validate(self._resolve_existing_path(Path(path).expanduser()))
        return self.current_root

    def create_folder(self, path: str) -> Path:
        target = Path(path).expanduser()
        parent = self._resolve_existing_path(target.parent)
        if not parent.exists() or not parent.is_dir():
            raise WorkspaceError(f"父目录不存在：{target.parent}")
        resolved = parent / target.name
        if resolved.exists():
            if resolved.is_dir():
                return self._validate(resolved)
            raise WorkspaceError(f"目标已存在但不是目录：{resolved}")
        if not self._is_allowed(resolved):
            raise WorkspaceError("新建目录不在允许的本地工作区范围内")
        resolved.mkdir()
        return self._validate(resolved)

    def status(self, query: str | None = None) -> dict:
        return {
            "current_root": str(self.current_root),
            "allowed_roots": [str(path) for path in self.allowed_roots],
            "candidates": [str(path) for path in self._candidate_projects(query)],
        }

    def _validate(self, path: Path) -> Path:
        resolved = self._resolve_existing_path(path).resolve()
        if not resolved.exists() or not resolved.is_dir():
            raise WorkspaceError(f"目录不存在：{path}")
        if not self._is_allowed(resolved):
            raise WorkspaceError("目录不在 NOVA_ALLOWED_WORKSPACE_ROOTS 允许范围内")
        return resolved

    def _is_allowed(self, path: Path) -> bool:
        for root in [*self.allowed_roots, *self.browse_roots]:
            if self._same_or_child(path, root):
                return True
        return False

    def _is_browsable(self, path: Path) -> bool:
        for root in [*self.allowed_roots, *self.browse_roots]:
            if self._is_allowed(path) or self._same_or_ancestor(path, root):
                return True
        return False

    def _derive_browse_roots(self, allowed_roots: list[Path]) -> list[Path]:
        roots: list[Path] = []
        for root in allowed_roots:
            parts = root.parts
            if len(parts) >= 3:
                candidate = Path(parts[0], parts[1], parts[2])
            else:
                candidate = root
            if candidate not in roots:
                roots.append(candidate)
        return roots

    def _path_key(self, path: Path) -> str:
        return str(path).rstrip("/\\").casefold()

    def _same_or_child(self, path: Path, root: Path) -> bool:
        path_key = self._path_key(path)
        root_key = self._path_key(root)
        return path_key == root_key or path_key.startswith(f"{root_key}/")

    def _same_or_ancestor(self, path: Path, root: Path) -> bool:
        path_key = self._path_key(path)
        root_key = self._path_key(root)
        return path_key == root_key or root_key.startswith(f"{path_key}/")

    def _resolve_existing_path(self, path: Path) -> Path:
        if path.exists():
            return path
        if not path.is_absolute():
            return path
        parts = path.parts
        if not parts:
            return path
        candidates = [Path(parts[0])]
        for part in parts[1:]:
            next_candidates: list[Path] = []
            seen: set[str] = set()
            for current in candidates:
                exact = current / part
                if exact.exists():
                    key = str(exact)
                    if key not in seen:
                        seen.add(key)
                        next_candidates.append(exact)
                try:
                    children = list(current.iterdir())
                except OSError:
                    children = []
                for child in children:
                    if child.name.casefold() != part.casefold():
                        continue
                    key = str(child)
                    if key in seen:
                        continue
                    seen.add(key)
                    next_candidates.append(child)
            if not next_candidates:
                return path
            candidates = next_candidates
        return candidates[0]

    def _candidate_projects(self, query: str | None = None) -> list[Path]:
        candidates: list[Path] = []
        seen: set[Path] = set()
        query_text = (query or "").strip()

        def add(path: Path, *, require_allowed: bool = True) -> None:
            try:
                resolved = path.resolve()
                is_dir = resolved.is_dir()
            except OSError:
                return
            if resolved in seen or len(candidates) >= 80:
                return
            if not is_dir or resolved.name.startswith("."):
                return
            if require_allowed and not self._is_allowed(resolved):
                return
            seen.add(resolved)
            candidates.append(resolved)

        if query_text:
            query_path = self._resolve_existing_path(Path(query_text).expanduser())
            if query_text.endswith(("/", "\\")) or (query_path.exists() and query_path.is_dir()):
                parent = query_path
                prefix = ""
            else:
                parent = self._resolve_existing_path(query_path.parent)
                prefix = query_path.name.lower()
            try:
                parent_resolved = parent.resolve()
            except OSError:
                parent_resolved = parent
            if parent.exists() and parent.is_dir() and self._is_browsable(parent_resolved):
                try:
                    children = sorted(parent.iterdir())
                except OSError:
                    children = []
                for child in children:
                    if len(candidates) >= 80:
                        return candidates
                    try:
                        child_is_dir = child.is_dir()
                    except OSError:
                        continue
                    if child_is_dir and child.name.lower().startswith(prefix):
                        # 查询目录时只展示该目录的直接子目录；真正切换仍由 _validate 严格校验。
                        add(child, require_allowed=False)
            return candidates

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
