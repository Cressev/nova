from __future__ import annotations

import json
import os
from pathlib import Path


class WorkspaceError(ValueError):
    """工作区切换失败，API 层会返回给前端显示。"""


class WorkspaceManager:
    def __init__(self, *, initial_root: Path, allowed_roots: list[Path], recent_file: Path | None = None) -> None:
        self.allowed_roots = [self._resolve_existing_path(path.expanduser()) for path in allowed_roots]
        self.browse_roots = self._derive_browse_roots(self.allowed_roots)
        self.recent_file = recent_file
        self.registry_file = recent_file.with_name("workspaces.json") if recent_file else None
        self.current_root = self._validate(initial_root)
        self._recent_projects = self._load_recent_projects()
        self._workspace_registry = self._load_workspace_registry()
        self._ensure_workspace_registered(self.current_root)
        self._remember_recent(self.current_root)

    def set_current(self, path: str) -> Path:
        self.current_root = self._validate(self._resolve_existing_path(Path(path).expanduser()))
        self._ensure_workspace_registered(self.current_root)
        self._remember_recent(self.current_root)
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
        selected = self._validate(resolved)
        self._remember_recent(selected)
        return selected

    def status(self, query: str | None = None) -> dict:
        workspaces = self.list_workspaces()
        return {
            "current_root": str(self.current_root),
            "allowed_roots": [str(path) for path in self.allowed_roots],
            "candidates": [str(path) for path in self._candidate_projects(query)],
            "recent_projects": [str(path) for path in self._recent_projects if path.exists() and path.is_dir()],
            "workspaces": workspaces,
            "completion": self.path_completion(query),
            "query_status": self.path_status(query),
        }

    def list_workspaces(self) -> list[dict[str, str]]:
        return [item.copy() for item in self._workspace_registry if Path(item["path"]).is_dir()]

    def rename_workspace(self, path: str, title: str) -> dict[str, str]:
        key = str(self._validate(Path(path)))
        cleaned = title.strip()[:120]
        if not cleaned:
            raise WorkspaceError("工作区名称不能为空")
        for item in self._workspace_registry:
            if item["path"] == key:
                item["title"] = cleaned
                self._save_workspace_registry()
                return item.copy()
        raise WorkspaceError("工作区不存在")

    def delete_workspace(self, path: str) -> None:
        key = str(self._validate(Path(path)))
        if key == str(self.current_root):
            raise WorkspaceError("不能删除当前工作区")
        before = len(self._workspace_registry)
        self._workspace_registry = [item for item in self._workspace_registry if item["path"] != key]
        if len(self._workspace_registry) == before:
            raise WorkspaceError("工作区不存在")
        self._save_workspace_registry()

    def insert_workspace_before(self, path: str, anchor: str | None = None) -> list[dict[str, str]]:
        key = str(self._validate(Path(path)))
        anchor_key = str(self._validate(Path(anchor))) if anchor else None
        items = [item for item in self._workspace_registry if item["path"] != key]
        item = next((entry for entry in self._workspace_registry if entry["path"] == key), {"path": key, "title": Path(key).name})
        index = next((i for i, entry in enumerate(items) if entry["path"] == anchor_key), len(items)) if anchor_key else len(items)
        items.insert(index, item)
        self._workspace_registry = items
        self._save_workspace_registry()
        return self.list_workspaces()

    def path_completion(self, query: str | None = None) -> dict:
        query_text = (query or "").strip()
        if not query_text:
            return {"value": "", "is_final": False, "reason": "请输入路径后再补全"}

        candidates = [str(path) for path in self._candidate_projects(query_text)]
        if not candidates:
            return {"value": "", "is_final": False, "reason": "没有可补全的候选目录"}

        if len(candidates) == 1:
            value = candidates[0]
            if not value.endswith(("/", "\\")):
                value = f"{value}/"
            return {"value": value, "is_final": True, "reason": "唯一候选，已补全到目录"}

        common_prefix = os.path.commonprefix(candidates)
        if len(common_prefix) <= len(query_text):
            return {"value": "", "is_final": False, "reason": "多个候选暂无更长公共前缀"}
        return {"value": common_prefix, "is_final": False, "reason": "多个候选，已补全到公共前缀"}

    def path_status(self, query: str | None = None) -> dict:
        query_text = (query or "").strip()
        if not query_text:
            return {
                "path": "",
                "exists": False,
                "is_dir": False,
                "parent_exists": False,
                "can_select": False,
                "can_create": False,
                "reason": "请输入本地目录路径",
            }

        raw_path = Path(query_text).expanduser()
        resolved = self._resolve_existing_path(raw_path)
        exists = resolved.exists()
        is_dir = exists and resolved.is_dir()
        can_select = False
        can_create = False
        parent_exists = False
        reason = ""

        if exists:
            can_select = is_dir and self._is_allowed(resolved)
            if not is_dir:
                reason = "目标已存在但不是目录"
            elif not can_select:
                reason = "目录不在允许的本地工作区范围内"
            else:
                reason = "目录已存在，可直接切换"
        else:
            parent = self._resolve_existing_path(raw_path.parent)
            parent_exists = parent.exists() and parent.is_dir()
            target = parent / raw_path.name if parent_exists else raw_path
            can_create = parent_exists and self._is_allowed(target)
            if not parent_exists:
                reason = "父目录不存在，无法新建"
            elif not can_create:
                reason = "新建目录不在允许的本地工作区范围内"
            else:
                reason = "目录不存在，可新建并切换"

        return {
            "path": str(resolved if exists else raw_path),
            "exists": exists,
            "is_dir": is_dir,
            "parent_exists": parent_exists,
            "can_select": can_select,
            "can_create": can_create,
            "reason": reason,
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

    def _canonical_path(self, path: Path) -> Path:
        try:
            return path.resolve()
        except OSError:
            return path

    def _path_key(self, path: Path) -> str:
        return str(self._canonical_path(path)).rstrip("/\\").casefold()

    def _same_or_child(self, path: Path, root: Path) -> bool:
        path_key = self._path_key(path)
        root_key = self._path_key(root)
        return path_key == root_key or path_key.startswith(f"{root_key}/")

    def _same_or_ancestor(self, path: Path, root: Path) -> bool:
        path_key = self._path_key(path)
        root_key = self._path_key(root)
        return path_key == root_key or root_key.startswith(f"{path_key}/")

    def _resolve_existing_path(self, path: Path) -> Path:
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
                exact = current / part
                if not next_candidates and exact.exists():
                    key = str(exact)
                    if key not in seen:
                        seen.add(key)
                        next_candidates.append(exact)
            if not next_candidates:
                return path
            candidates = next_candidates
        return candidates[0]

    def _remember_recent(self, path: Path) -> None:
        try:
            resolved = path.resolve()
        except OSError:
            return
        self._recent_projects = [item for item in self._recent_projects if item != resolved]
        self._recent_projects.insert(0, resolved)
        self._recent_projects = self._recent_projects[:12]
        self._save_recent_projects()

    def _load_recent_projects(self) -> list[Path]:
        if self.recent_file is None or not self.recent_file.exists():
            return []
        try:
            payload = json.loads(self.recent_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(payload, list):
            return []
        recent: list[Path] = []
        for item in payload:
            if not isinstance(item, str) or not item.strip():
                continue
            path = self._resolve_existing_path(Path(item).expanduser())
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if resolved.exists() and resolved.is_dir() and self._is_allowed(resolved) and resolved not in recent:
                recent.append(resolved)
        return recent[:12]

    def _save_recent_projects(self) -> None:
        if self.recent_file is None:
            return
        try:
            self.recent_file.parent.mkdir(parents=True, exist_ok=True)
            self.recent_file.write_text(
                json.dumps([str(path) for path in self._recent_projects], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            return

    def _load_workspace_registry(self) -> list[dict[str, str]]:
        if not self.registry_file or not self.registry_file.exists():
            return []
        try:
            raw = json.loads(self.registry_file.read_text(encoding="utf-8"))
            return [item for item in raw if isinstance(item, dict) and isinstance(item.get("path"), str) and isinstance(item.get("title"), str)]
        except (OSError, ValueError):
            return []

    def _save_workspace_registry(self) -> None:
        if not self.registry_file:
            return
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)
        self.registry_file.write_text(json.dumps(self._workspace_registry, ensure_ascii=False, indent=2), encoding="utf-8")

    def _ensure_workspace_registered(self, path: Path) -> None:
        key = str(path)
        if not any(item["path"] == key for item in self._workspace_registry):
            self._workspace_registry.append({"path": key, "title": path.name or str(path)})
            self._save_workspace_registry()

    def _candidate_projects(self, query: str | None = None) -> list[Path]:
        candidates: list[Path] = []
        seen: set[str] = set()
        query_text = (query or "").strip()

        def add(path: Path, *, require_allowed: bool = True) -> None:
            try:
                is_dir = path.is_dir()
            except OSError:
                return
            key = self._path_key(path)
            if key in seen or len(candidates) >= 80:
                return
            if not is_dir or path.name.startswith("."):
                return
            if require_allowed and not self._is_allowed(path):
                return
            seen.add(key)
            candidates.append(path)

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
            try:
                children = sorted(root.iterdir())
            except OSError:
                continue
            for child in children:
                if len(candidates) >= 80:
                    return candidates
                try:
                    child_is_dir = child.is_dir()
                except OSError:
                    continue
                if not child_is_dir or child.name.startswith("."):
                    continue
                if query_text:
                    haystack = f"{child.name}\n{child}".lower()
                    if query_text.lower() not in haystack:
                        continue
                try:
                    looks_like_project = (child / ".git").exists() or (child / "AGENTS.md").exists()
                except OSError:
                    continue
                if looks_like_project:
                    add(child)
        return candidates
