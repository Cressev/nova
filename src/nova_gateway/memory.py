from __future__ import annotations

from pathlib import Path


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
        return "\n\n".join(parts)

    def status(self) -> dict:
        return {
            "enabled": True,
            "policy": "只注入全局和项目级 Agent 指令；Nova 开发状态文件不注入产品内 Agent。",
            "global": self._source_status("全局", self.global_agent_file, injected=True),
            "project": self._source_status("项目", self.project_agent_file, injected=True),
            "development_state": [
                self._source_status("Nova开发状态", self.project_root / filename, injected=False)
                for filename in self.development_state_files
            ],
            "max_chars": self.max_chars,
        }

    def _agent_instruction_sources(self) -> list[tuple[str, Path]]:
        sources: list[tuple[str, Path]] = []
        if self.global_agent_file is not None:
            sources.append(("全局 Agent 指令", self.global_agent_file))
        sources.append(("项目 Agent 指令", self.project_agent_file))
        return sources

    def _source_status(self, scope: str, path: Path | None, *, injected: bool) -> dict:
        return {
            "scope": scope,
            "path": str(path) if path is not None else "",
            "exists": bool(path and path.is_file()),
            "injected": injected,
        }
