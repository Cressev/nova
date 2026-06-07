from __future__ import annotations

from pathlib import Path
from typing import Any


class ProjectMemory:
    def __init__(
        self,
        project_root: Path,
        *,
        global_agent_file: Path | None = None,
        max_chars: int = 8000,
    ) -> None:
        self.project_root = project_root.resolve()
        self.global_agent_file = global_agent_file
        self.max_chars = max_chars
        self.project_agent_file = self.project_root / "AGENTS.md"
        self.development_state_files = ["CURRENT.md", "PROGRESS.md", "TODOList.md", "log.md"]
        self.memory_dir = self.project_root / ".nova" / "memory"
        self.global_memory_dir = Path.home() / ".nova" / "memory"
        self.persona_dir = self.project_root / ".nova" / "persona"
        self.global_persona_dir = Path.home() / ".nova" / "persona"
        self.memory_files = ["index.md", "project.md", "session.md"]
        self.persona_files = ["user.md", "soul.md", "tools.md"]

    def context(self) -> str:
        # 只注入“给开发 Agent 的指令”。Nova 自身开发状态文件只给外层 Codex 看，不塞进产品内 Agent。
        parts: list[str] = []
        remaining = self.max_chars
        for label, path in self._agent_instruction_sources():
            if not path.is_file() or remaining <= 0:
                continue
            content = path.read_text(encoding="utf-8", errors="replace")[:remaining]
            parts.append(f"## {label}: {path.name}\n{content}")
            remaining -= len(content)
        for source in [*self.injected_persona_sources(), *self.injected_memory_sources()]:
            if remaining <= 0:
                break
            path = Path(source["path"])
            if not path.is_file():
                continue
            content = path.read_text(encoding="utf-8", errors="replace")[:remaining]
            if not content.strip():
                continue
            label = "人格文件" if source["kind"] == "persona" else "长期记忆"
            parts.append(f"## {label}: {path.name}\n{content}")
            remaining -= len(content)
        return "\n\n".join(parts)

    def status(self) -> dict:
        return {
            "enabled": True,
            "policy": "只注入 Agent 指令、人格文件和长期记忆；Nova 开发状态文件不注入产品内 Agent。",
            "global": self._source_status("全局", self.global_agent_file, injected=True),
            "project": self._source_status("项目", self.project_agent_file, injected=True),
            "injected_sources": [
                self._source_status("全局 Agent 指令", self.global_agent_file, injected=True),
                self._source_status("项目 Agent 指令", self.project_agent_file, injected=True),
                *self.injected_persona_sources(),
                *self.injected_memory_sources(),
            ],
            "persona_files": self.persona_file_statuses(),
            "memory_files": self.memory_file_statuses(),
            "development_state": [
                self._source_status("Nova开发状态", self.project_root / filename, injected=False)
                for filename in self.development_state_files
            ],
            "max_chars": self.max_chars,
        }

    def injected_persona_sources(self) -> list[dict[str, Any]]:
        sources: list[dict[str, Any]] = []
        for filename in self.persona_files:
            sources.append(self._source_status("全局人格", self.global_persona_dir / filename, injected=True, kind="persona"))
        for filename in self.persona_files:
            sources.append(self._source_status("项目人格", self.persona_dir / filename, injected=True, kind="persona"))
        return sources

    def injected_memory_sources(self) -> list[dict[str, Any]]:
        sources: list[dict[str, Any]] = []
        for filename in self.memory_files:
            sources.append(self._source_status("全局记忆", self.global_memory_dir / filename, injected=True, kind="memory"))
        for filename in self.memory_files:
            sources.append(self._source_status("项目记忆", self.memory_dir / filename, injected=True, kind="memory"))
        return sources

    def persona_file_statuses(self) -> list[dict[str, Any]]:
        files: dict[str, Path] = {}
        for filename in self.persona_files:
            files[f"global:{filename}"] = self.global_persona_dir / filename
            files[f"project:{filename}"] = self.persona_dir / filename
        if self.global_persona_dir.is_dir():
            for path in sorted(self.global_persona_dir.glob("*.md")):
                files.setdefault(f"global:{path.name}", path)
        if self.persona_dir.is_dir():
            for path in sorted(self.persona_dir.glob("*.md")):
                files.setdefault(f"project:{path.name}", path)
        return [
            self._source_status("全局人格" if key.startswith("global:") else "项目人格", path, injected=True, kind="persona")
            for key, path in files.items()
        ]

    def memory_file_statuses(self) -> list[dict[str, Any]]:
        files = {filename: self.memory_dir / filename for filename in self.memory_files}
        if self.memory_dir.is_dir():
            for path in sorted(self.memory_dir.glob("*.md")):
                if path.name in self.persona_files:
                    continue
                files.setdefault(path.name, path)
        return [self._source_status("项目记忆", path, injected=True, kind="memory") for path in files.values()]

    def read_file(self, name: str) -> dict[str, Any]:
        path = self._resolve_memory_file(name)
        content = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        return {"name": path.name, "path": str(path), "exists": path.exists(), "content": content, "injected": True}

    def write_file(self, name: str, content: str) -> dict[str, Any]:
        path = self._resolve_memory_file(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return self.read_file(path.name)

    def read_persona_file(self, scope: str, name: str) -> dict[str, Any]:
        path = self._resolve_persona_file(scope, name)
        content = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        return {
            "scope": "global" if self._normalize_persona_scope(scope) == "global" else "project",
            "name": path.name,
            "path": str(path),
            "exists": path.exists(),
            "content": content,
            "injected": True,
        }

    def write_persona_file(self, scope: str, name: str, content: str) -> dict[str, Any]:
        path = self._resolve_persona_file(scope, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return self.read_persona_file(scope, path.name)

    def append_fact(self, text: str) -> dict[str, Any]:
        path = self.memory_dir / "index.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        before = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        line = text.strip()
        content = (before.rstrip() + "\n\n" if before.strip() else "") + f"- {line}\n"
        path.write_text(content, encoding="utf-8")
        return self.read_file("index.md")

    def search(self, query: str) -> list[dict[str, Any]]:
        needle = query.strip().lower()
        if not needle:
            return []
        matches: list[dict[str, Any]] = []
        for item in self.memory_file_statuses():
            path = Path(item["path"])
            if not path.is_file():
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
                if needle in line.lower():
                    matches.append({"name": path.name, "path": str(path), "line": number, "text": line})
                    if len(matches) >= 50:
                        return matches
        return matches

    def _agent_instruction_sources(self) -> list[tuple[str, Path]]:
        sources: list[tuple[str, Path]] = []
        if self.global_agent_file is not None:
            sources.append(("全局 Agent 指令", self.global_agent_file))
        sources.append(("项目 Agent 指令", self.project_agent_file))
        return sources

    def _source_status(self, scope: str, path: Path | None, *, injected: bool, kind: str = "instruction") -> dict:
        return {
            "scope": scope,
            "path": str(path) if path is not None else "",
            "name": path.name if path is not None else "",
            "exists": bool(path and path.is_file()),
            "injected": injected,
            "kind": kind,
        }

    def _resolve_memory_file(self, name: str) -> Path:
        clean = Path(name.strip() or "index.md").name
        if not clean.endswith(".md"):
            clean = f"{clean}.md"
        return self.memory_dir / clean

    def _normalize_persona_scope(self, scope: str) -> str:
        return "global" if str(scope or "").strip() == "global" else "project"

    def _resolve_persona_file(self, scope: str, name: str) -> Path:
        clean = Path(name.strip() or "user.md").name
        if not clean.endswith(".md"):
            clean = f"{clean}.md"
        root = self.global_persona_dir if self._normalize_persona_scope(scope) == "global" else self.persona_dir
        return root / clean
