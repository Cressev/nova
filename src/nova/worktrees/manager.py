from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class WorktreeError(ValueError):
    """工作树操作失败，API 层会把原因展示给用户。"""


_VALID_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class WorktreeRecord:
    name: str
    path: Path
    branch: str
    head: str | None = None
    dirty_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": str(self.path),
            "branch": self.branch,
            "head": self.head,
            "dirty_count": self.dirty_count,
        }


class WorktreeManager:
    def __init__(self, *, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()

    @classmethod
    def from_workspace(cls, workspace_root: Path) -> "WorktreeManager":
        root = workspace_root.resolve()
        parts = root.parts
        for index in range(len(parts) - 2):
            if parts[index] == ".nova" and parts[index + 1] == "worktrees":
                return cls(repo_root=Path(*parts[:index]))
        return cls(repo_root=cls.find_git_root(workspace_root))

    @staticmethod
    def find_git_root(path: Path) -> Path:
        result = _run_git(["rev-parse", "--show-toplevel"], cwd=path)
        root = result.stdout.strip()
        if not root:
            raise WorktreeError("当前目录不是 Git 仓库，无法创建工作树")
        return Path(root).resolve()

    def create(self, name: str) -> dict[str, Any]:
        slug = self._validate_name(name)
        worktree_path = self.path_for(slug)
        branch = self.branch_for(slug)
        existed = worktree_path.exists() and self._is_git_worktree(worktree_path)
        if not existed:
            worktree_path.parent.mkdir(parents=True, exist_ok=True)
            result = _run_git(
                ["worktree", "add", "-B", branch, str(worktree_path), "HEAD"],
                cwd=self.repo_root,
                check=False,
            )
            if result.returncode != 0:
                raise WorktreeError(result.stderr.strip() or "git worktree add 失败")
        return {
            "name": slug,
            "path": str(worktree_path),
            "branch": branch,
            "original_root": str(self.repo_root),
            "existed": existed,
        }

    def list(self) -> list[WorktreeRecord]:
        result = _run_git(["worktree", "list", "--porcelain"], cwd=self.repo_root)
        records: list[WorktreeRecord] = []
        current: dict[str, str] = {}
        for line in [*result.stdout.splitlines(), ""]:
            if not line:
                record = self._record_from_porcelain(current)
                if record is not None:
                    records.append(record)
                current = {}
                continue
            key, _, value = line.partition(" ")
            current[key] = value
        return sorted(records, key=lambda item: item.name)

    def diff(self, name: str | None = None) -> dict[str, Any]:
        path = self.path_for(self._validate_name(name)) if name else self.repo_root
        if not self._is_git_worktree(path):
            raise WorktreeError(f"不是可用的 Git 工作树：{path}")
        status = _run_git(["status", "--porcelain=v1"], cwd=path).stdout
        diff = _run_git(["diff", "--stat", "--patch"], cwd=path).stdout
        output = "\n".join(part for part in [status.strip(), diff.strip()] if part)
        return {
            "path": str(path),
            "dirty_count": len([line for line in status.splitlines() if line.strip()]),
            "diff": output,
        }

    def remove(self, name: str, *, discard: bool = False) -> dict[str, Any]:
        slug = self._validate_name(name)
        path = self.path_for(slug)
        branch = self.branch_for(slug)
        if not path.exists():
            return {"name": slug, "path": str(path), "branch": branch, "removed": False}
        if not self._is_git_worktree(path):
            raise WorktreeError("目标路径不是 Nova 创建的 Git 工作树")
        status = _run_git(["status", "--porcelain=v1"], cwd=path).stdout
        dirty_count = len([line for line in status.splitlines() if line.strip()])
        if dirty_count > 0 and not discard:
            raise WorktreeError(f"工作树有 {dirty_count} 个未提交改动；如需丢弃并清理，请带 discard=true")
        result = _run_git(["worktree", "remove", "--force", str(path)], cwd=self.repo_root, check=False)
        if result.returncode != 0:
            raise WorktreeError(result.stderr.strip() or "git worktree remove 失败")
        _run_git(["branch", "-D", branch], cwd=self.repo_root, check=False)
        return {
            "name": slug,
            "path": str(path),
            "branch": branch,
            "removed": True,
            "discarded_changes": dirty_count,
        }

    def path_for(self, name: str) -> Path:
        return self.repo_root / ".nova" / "worktrees" / name.replace("/", "+")

    def branch_for(self, name: str) -> str:
        return f"worktree-{name.replace('/', '+')}"

    def name_for_path(self, path: Path) -> str | None:
        try:
            relative = path.resolve().relative_to(self.repo_root / ".nova" / "worktrees")
        except ValueError:
            return None
        if len(relative.parts) != 1:
            return None
        return relative.parts[0].replace("+", "/")

    def _record_from_porcelain(self, payload: dict[str, str]) -> WorktreeRecord | None:
        raw_path = payload.get("worktree")
        if not raw_path:
            return None
        path = Path(raw_path).resolve()
        name = self.name_for_path(path)
        branch = payload.get("branch", "").removeprefix("refs/heads/")
        if not name or not branch.startswith("worktree-"):
            return None
        status = _run_git(["status", "--porcelain=v1"], cwd=path, check=False)
        dirty_count = len([line for line in status.stdout.splitlines() if line.strip()]) if status.returncode == 0 else 0
        return WorktreeRecord(name=name, path=path, branch=branch, head=payload.get("HEAD"), dirty_count=dirty_count)

    def _is_git_worktree(self, path: Path) -> bool:
        result = _run_git(["rev-parse", "--is-inside-work-tree"], cwd=path, check=False)
        return result.returncode == 0 and result.stdout.strip() == "true"

    def _validate_name(self, name: str | None) -> str:
        slug = (name or "nova-worktree").strip()
        if not slug or len(slug) > 64:
            raise WorktreeError("工作树名称必须为 1-64 个字符")
        for segment in slug.split("/"):
            if segment in {"", ".", ".."} or not _VALID_SEGMENT.match(segment):
                raise WorktreeError("工作树名称只能包含字母、数字、点、下划线、短横线和安全的 / 分段")
        return slug


def _run_git(args: list[str], *, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorktreeError(f"git 命令执行失败：{exc}") from exc
    if check and result.returncode != 0:
        raise WorktreeError(result.stderr.strip() or f"git {' '.join(args)} 失败")
    return result
