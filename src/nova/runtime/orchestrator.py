from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..sessions import AgentSessionService


class RunOrchestrator:
    """单轮 Agent 运行编排器。

    它不负责模型和工具的具体执行，只负责把一轮运行的状态事件统一写入：
    本地 timeline、内存 runtime state、pending approval。这样 Web/TUI 后续
    可以复用同一条运行状态链路。
    """

    def __init__(
        self,
        *,
        session_id: str,
        turn_id: str,
        agent_sessions: AgentSessionService,
        persist_event: Callable[[dict[str, Any]], None],
        id_factory: Callable[[str], str],
    ) -> None:
        self.session_id = session_id
        self.turn_id = turn_id
        self.agent_sessions = agent_sessions
        self.persist_event = persist_event
        self.id_factory = id_factory
        self.sequence = 0

    def start_turn(self, *, user_message_id: str | None, message: str) -> dict[str, Any]:
        self.agent_sessions.start_turn(
            self.session_id,
            turn_id=self.turn_id,
            user_message_id=user_message_id,
        )
        return self.event(
            "turn.started",
            category="turn",
            phase="started",
            title="开始处理用户请求",
            message=message,
            data={"message_id": user_message_id},
        )

    def complete_turn(self, *, message_id: str, content: str) -> dict[str, Any]:
        self.agent_sessions.complete_turn(
            self.session_id,
            message_id=message_id,
            content=content,
        )
        return self.event(
            "turn.completed",
            category="turn",
            phase="completed",
            title="本轮回复完成",
            message="Nova 已完成本次运行。",
            data={"message_id": message_id},
        )

    def fail_turn(self, *, title: str, message: str) -> dict[str, Any]:
        self.agent_sessions.fail_turn(self.session_id, reason=message)
        return self.event(
            "turn.failed",
            category="turn",
            phase="failed",
            status="failed",
            title=title,
            message=message,
            data={"turn_id": self.turn_id},
        )

    def cancel_turn(self, *, message: str = "用户已强制停止当前 Agent 运行。") -> dict[str, Any]:
        self.agent_sessions.cancel_turn(self.session_id)
        return self.event(
            "turn.cancelled",
            category="turn",
            phase="cancelled",
            status="cancelled",
            title="当前运行已停止",
            message=message,
            data={"turn_id": self.turn_id},
        )

    def is_cancel_requested(self) -> bool:
        return self.agent_sessions.is_cancel_requested(self.session_id)

    def register_permission_request(
        self,
        raw_event: dict[str, Any],
        *,
        runtime_event: dict[str, Any] | None = None,
    ) -> None:
        self.agent_sessions.create_pending_approval(
            session_id=self.session_id,
            turn_id=self.turn_id,
            call_id=str(raw_event.get("call_id") or (runtime_event or {}).get("id") or self.id_factory("tool")),
            tool=str(raw_event.get("tool") or "tool"),
            arguments=raw_event.get("arguments") if isinstance(raw_event.get("arguments"), dict) else {},
            permission=str(raw_event.get("permission") or ""),
            reason=str(raw_event.get("message") or "执行工具前需要用户确认。"),
        )

    def event(
        self,
        event_type: str,
        *,
        category: str,
        phase: str,
        title: str,
        message: str | None = None,
        status: str = "ok",
        tool: str | None = None,
        call_id: str | None = None,
        arguments: dict[str, Any] | None = None,
        output: str | None = None,
        data: dict[str, Any] | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        self.sequence += 1
        event = {
            "id": call_id or self.id_factory("evt"),
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "sequence": self.sequence,
            "event_type": event_type,
            "category": category,
            "phase": phase,
            "status": status,
            "title": title,
            "message": message or title,
            "tool": tool,
            "call_id": call_id,
            "arguments": arguments or {},
            "output": output,
            "data": data or {},
        }
        if persist:
            self.persist_event(event)
        self.agent_sessions.record_runtime_event(event)
        return event
