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
