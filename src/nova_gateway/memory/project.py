from __future__ import annotations

from datetime import datetime, timezone
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

    def compact_session(self, messages: list[Any], *, instruction: str = "") -> dict[str, Any]:
        compacted_messages = [
            message for message in messages if not self._message_content(message).lstrip().startswith("/compact")
        ]
        summary = self._build_session_summary(compacted_messages, instruction=instruction)
        path = self.memory_dir / "session.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        before = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        content = summary.rstrip()
        if before.strip():
            content += "\n\n---\n\n## 历史压缩摘要\n\n" + before.strip()[:18000]
        path.write_text(content + "\n", encoding="utf-8")
        result = self.read_file("session.md")
        result["summary"] = summary
        result["covered_messages"] = len(compacted_messages)
        return result

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

    def _build_session_summary(self, messages: list[Any], *, instruction: str = "") -> str:
        now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        user_messages = [self._message_content(message) for message in messages if self._message_role(message) == "user"]
        assistant_messages = [
            self._message_content(message) for message in messages if self._message_role(message) == "assistant"
        ]
        recent_messages = messages[-10:]
        lines = [
            "# 会话压缩摘要",
            "",
            "## 压缩记录",
            f"- 时间：{now}",
            f"- 项目：{self.project_root}",
            f"- 覆盖消息数：{len(messages)}",
        ]
        if instruction.strip():
            lines.append(f"- 用户压缩要求：{instruction.strip()[:500]}")
        lines.extend(
            [
                "",
                "## 当前目标",
                self._bullet_or_empty(user_messages[-3:], "暂无明确用户目标。"),
                "",
                "## 已形成的关键结论",
                self._bullet_or_empty(assistant_messages[-3:], "暂无助手结论。"),
                "",
                "## 最近对话",
            ]
        )
        if recent_messages:
            for message in recent_messages:
                role = {"user": "用户", "assistant": "助手", "system": "系统", "error": "错误"}.get(
                    self._message_role(message), self._message_role(message)
                )
                lines.append(f"- {role}：{self._compact_text(self._message_content(message), 420)}")
        else:
            lines.append("- 暂无可压缩消息。")
        lines.extend(
            [
                "",
                "## 接续提示",
                "- 后续对话应优先读取本文件，保留用户目标、当前决策和最近上下文。",
                "- 如果摘要与代码或用户最新指令冲突，以代码和用户最新指令为准。",
            ]
        )
        return "\n".join(lines)

    def _message_role(self, message: Any) -> str:
        role = getattr(message, "role", "")
        return str(getattr(role, "value", role)).lower()

    def _message_content(self, message: Any) -> str:
        return str(getattr(message, "content", "") or "").strip()

    def _bullet_or_empty(self, items: list[str], empty: str) -> str:
        cleaned = [self._compact_text(item, 360) for item in items if item.strip()]
        if not cleaned:
            return f"- {empty}"
        return "\n".join(f"- {item}" for item in cleaned)

    def _compact_text(self, text: str, limit: int) -> str:
        compact = " ".join(text.split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 1].rstrip() + "…"
