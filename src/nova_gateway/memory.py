from __future__ import annotations

from pathlib import Path


class ProjectMemory:
    def __init__(self, project_root: Path, *, max_chars: int = 8000) -> None:
        self.project_root = project_root.resolve()
        self.max_chars = max_chars
        self.files = ["AGENTS.md", "CURRENT.md", "PROGRESS.md"]

    def context(self) -> str:
        # 只读取用户明确维护的项目记忆文件，避免把临时日志或密钥类环境信息塞进模型上下文。
        parts: list[str] = []
        remaining = self.max_chars
        for filename in self.files:
            path = self.project_root / filename
            if not path.is_file() or remaining <= 0:
                continue
            content = path.read_text(encoding="utf-8", errors="replace")[:remaining]
            parts.append(f"## {filename}\n{content}")
            remaining -= len(content)
        return "\n\n".join(parts)

    def status(self) -> dict:
        return {
            "enabled": True,
            "files": [
                {
                    "path": filename,
                    "exists": (self.project_root / filename).is_file(),
                }
                for filename in self.files
            ],
            "max_chars": self.max_chars,
        }
