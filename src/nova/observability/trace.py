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

    def replay(
        self,
        session_id: str,
        *,
        messages: list[dict[str, Any]] | None = None,
        events: list[dict[str, Any]] | None = None,
        processes: list[dict[str, Any]] | None = None,
        pending_approvals: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """把本地 trace 整理成可回放结构。

        trace 的职责是复盘“模型可见输入、输出、工具调用和系统事件”，不保存
        也不重建隐藏思维链。
        """

        trace_file = self._find_json_trace(session_id)
        trace_events = self._read_json_array(trace_file) if trace_file is not None else []
        source_events = trace_events or list(events or [])
        message_items = list(messages or [])
        process_items = list(processes or [])
        approval_items = list(pending_approvals or [])
        turns: dict[str, dict[str, Any]] = {}
        orphan_events: list[dict[str, Any]] = []
        messages_by_id = {
            str(message.get("id")): message
            for message in message_items
            if isinstance(message, dict) and message.get("id")
        }

        def ensure_turn(turn_id: str) -> dict[str, Any]:
            if turn_id not in turns:
                turns[turn_id] = {
                    "turn_id": turn_id,
                    "status": "unknown",
                    "started_at": None,
                    "completed_at": None,
                    "user_message": None,
                    "final_message": None,
                    "events": [],
                    "tool_calls": [],
                    "approvals": [],
                    "processes": [],
                    "queue_events": [],
                    "cancel_events": [],
                }
            return turns[turn_id]

        for event in source_events:
            if not isinstance(event, dict):
                continue
            turn_id = str(event.get("turn_id") or "")
            if not turn_id:
                orphan_events.append(event)
                continue
            turn = ensure_turn(turn_id)
            event_type = str(event.get("event_type") or "")
            phase = str(event.get("phase") or "")
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            turn["events"].append(event)
            if event_type == "turn.started":
                turn["status"] = "running"
                turn["started_at"] = event.get("created_at")
                message_id = str(data.get("message_id") or "")
                if message_id:
                    turn["user_message"] = messages_by_id.get(message_id)
            elif event_type in {"turn.completed", "turn.cancelled", "turn.failed"}:
                turn["status"] = phase or str(event.get("status") or "completed")
                turn["completed_at"] = event.get("created_at") or event.get("updated_at")
                message_id = str(data.get("message_id") or "")
                if message_id:
                    turn["final_message"] = messages_by_id.get(message_id)
            if event_type.startswith("tool."):
                _merge_tool_call(turn, event)
                _merge_process_from_event(turn, event)
            if event_type.startswith("permission."):
                _append_unique(turn["approvals"], _approval_from_event(event))
            if event_type.startswith("queue."):
                turn["queue_events"].append(event)
            if "cancel" in event_type:
                turn["cancel_events"].append(event)

        for approval in approval_items:
            turn_id = str(approval.get("turn_id") or "")
            if not turn_id:
                continue
            _append_unique(ensure_turn(turn_id)["approvals"], dict(approval))
        for process in process_items:
            call_id = str(process.get("call_id") or "")
            target_turn = _turn_for_call_id(turns, call_id) if call_id else None
            if target_turn is not None:
                _append_unique(target_turn["processes"], dict(process))

        return {
            "session_id": session_id,
            "trace_file": trace_file.name if trace_file is not None else None,
            "source": "trace" if trace_events else "timeline",
            "records_hidden_thoughts": False,
            "messages": message_items,
            "events": source_events,
            "turns": list(turns.values()),
            "orphan_events": orphan_events,
        }

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


def _merge_tool_call(turn: dict[str, Any], event: dict[str, Any]) -> None:
    call_id = str(event.get("call_id") or event.get("id") or "")
    if not call_id:
        return
    existing = next((item for item in turn["tool_calls"] if item.get("call_id") == call_id), None)
    if existing is None:
        existing = {
            "call_id": call_id,
            "tool": event.get("tool"),
            "status": event.get("status"),
            "arguments": event.get("arguments") if isinstance(event.get("arguments"), dict) else {},
            "output": event.get("output"),
            "events": [],
        }
        turn["tool_calls"].append(existing)
    existing["tool"] = event.get("tool") or existing.get("tool")
    existing["status"] = _tool_status(event) or existing.get("status")
    if isinstance(event.get("arguments"), dict) and event["arguments"]:
        existing["arguments"] = event["arguments"]
    if event.get("output") is not None:
        existing["output"] = event.get("output")
    existing["events"].append(event)


def _tool_status(event: dict[str, Any]) -> str:
    phase = str(event.get("phase") or "")
    if phase == "completed":
        return "completed"
    if phase == "failed":
        return "failed"
    if phase == "output":
        return "running"
    return str(event.get("status") or "")


def _approval_from_event(event: dict[str, Any]) -> dict[str, Any]:
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    return {
        "id": event.get("id"),
        "call_id": event.get("call_id") or event.get("id"),
        "tool": event.get("tool"),
        "status": event.get("status"),
        "phase": event.get("phase"),
        "permission": data.get("permission"),
        "risk": data.get("risk"),
        "message": event.get("message"),
        "arguments": event.get("arguments") if isinstance(event.get("arguments"), dict) else {},
    }


def _merge_process_from_event(turn: dict[str, Any], event: dict[str, Any]) -> None:
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    nested_job = data.get("job") if isinstance(data.get("job"), dict) else {}
    job_id = data.get("job_id") or nested_job.get("id")
    if not job_id:
        return
    process = dict(nested_job) if nested_job else {"id": job_id}
    process.setdefault("call_id", event.get("call_id"))
    _append_unique(turn["processes"], process)


def _turn_for_call_id(turns: dict[str, dict[str, Any]], call_id: str) -> dict[str, Any] | None:
    for turn in turns.values():
        if any(item.get("call_id") == call_id for item in turn.get("tool_calls", [])):
            return turn
        if any(item.get("call_id") == call_id for item in turn.get("approvals", [])):
            return turn
    return None


def _append_unique(items: list[dict[str, Any]], item: dict[str, Any]) -> None:
    key = item.get("id") or item.get("call_id")
    if key and any((existing.get("id") or existing.get("call_id")) == key for existing in items):
        return
    items.append(item)
