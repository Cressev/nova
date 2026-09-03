from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PendingApproval:
    id: str
    session_id: str
    turn_id: str
    call_id: str
    tool: str
    arguments: dict[str, Any]
    permission: str
    reason: str
    risk: str | None = None
    checkpoint_event_id: str | None = None
    checkpoint_data: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "call_id": self.call_id,
            "tool": self.tool,
            "arguments": self.arguments,
            "permission": self.permission,
            "reason": self.reason,
            "risk": self.risk,
            "checkpoint_event_id": self.checkpoint_event_id,
            "checkpoint_data": self.checkpoint_data,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class PendingApprovalStore:
    """保存待审批工具调用，供前端 approve/deny 后续跑。"""

    def __init__(self) -> None:
        self._items: dict[str, PendingApproval] = {}
        self._lock = Lock()

    def create(
        self,
        *,
        session_id: str,
        turn_id: str,
        call_id: str,
        tool: str,
        arguments: dict[str, Any],
        permission: str,
        reason: str,
        risk: str | None = None,
        checkpoint_event_id: str | None = None,
        checkpoint_data: dict[str, Any] | None = None,
    ) -> PendingApproval:
        with self._lock:
            item = PendingApproval(
                id=call_id,
                session_id=session_id,
                turn_id=turn_id,
                call_id=call_id,
                tool=tool,
                arguments=dict(arguments),
                permission=permission,
                reason=reason,
                risk=risk,
                checkpoint_event_id=checkpoint_event_id,
                checkpoint_data=dict(checkpoint_data or {}),
            )
            self._items[item.id] = item
            return item

    def list_pending(self, *, session_id: str | None = None) -> list[PendingApproval]:
        with self._lock:
            items = [item for item in self._items.values() if item.status == "pending"]
            if session_id is not None:
                items = [item for item in items if item.session_id == session_id]
            return sorted(items, key=lambda item: item.created_at)

    def get(self, approval_id: str) -> PendingApproval | None:
        with self._lock:
            return self._items.get(approval_id)

    def update_arguments(self, approval_id: str, arguments: dict[str, Any]) -> PendingApproval | None:
        """ask_user_question 场景：回答后把 _answers 挂进 arguments，approve 续跑时带回执行器。"""
        with self._lock:
            item = self._items.get(approval_id)
            if item is None:
                return None
            item.arguments = dict(arguments)
            item.updated_at = _now()
            return item

    def approve(self, approval_id: str) -> PendingApproval | None:
        return self._finish(approval_id, "approved")

    def deny(self, approval_id: str, reason: str = "") -> PendingApproval | None:
        item = self._finish(approval_id, "denied")
        if item is not None and reason:
            item.reason = reason
        return item

    def _finish(self, approval_id: str, status: str) -> PendingApproval | None:
        with self._lock:
            item = self._items.get(approval_id)
            if item is None:
                return None
            item.status = status
            item.updated_at = _now()
            return item
