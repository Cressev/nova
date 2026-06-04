from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import TimelineEvent


class TraceRecorder:
    def __init__(self, state_dir: Path) -> None:
        self.trace_dir = state_dir / "traces"
        self.trace_dir.mkdir(parents=True, exist_ok=True)

    def append(self, event: TimelineEvent) -> None:
        path = self.trace_dir / f"{event.task_id}.jsonl"
        with path.open("a", encoding="utf-8") as file:
            file.write(
                json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
                + "\n"
            )

    def read(self, task_id: str) -> list[dict[str, Any]]:
        path = self.trace_dir / f"{task_id}.jsonl"
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

