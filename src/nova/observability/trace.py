from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..models import ChatEvent


BEIJING_TZ = ZoneInfo("Asia/Shanghai")


class TraceRecorder:
    def __init__(self, state_dir: Path) -> None:
        self.trace_dir = state_dir / "traces"
        self.trace_dir.mkdir(parents=True, exist_ok=True)

    def append(self, event: ChatEvent) -> None:
        path = self._path_for_event(event)
        payload = self._read_json_array(path)
        payload.append(event.model_dump(mode="json"))
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def read(self, session_id: str) -> list[dict[str, Any]]:
        path = self._find_json_trace(session_id)
        if path is not None:
            return self._read_json_array(path)
        return []

    def _path_for_event(self, event: ChatEvent) -> Path:
        existing = self._find_json_trace(event.session_id)
        if existing is not None:
            return existing
        return self.trace_dir / f"{self._beijing_minute(event.created_at)}_{event.session_id}.json"

    def _find_json_trace(self, session_id: str) -> Path | None:
        suffix = f"_{session_id}.json"
        matches = sorted(
            path
            for path in self.trace_dir.glob("*.json")
            if path.name.endswith(suffix)
        )
        return matches[0] if matches else None

    def _beijing_minute(self, timestamp: datetime) -> str:
        return timestamp.astimezone(BEIJING_TZ).strftime("%Y%m%d%H%M")

    def _read_json_array(self, path: Path) -> list[dict[str, Any]]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return []
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]
