from __future__ import annotations

from collections import defaultdict
from threading import Lock

from ..approvals.store import PendingApproval, PendingApprovalStore
from ..models import ChatMessage


class AgentSessionService:
    """管理每个会话的运行中状态和排队输入。

    这里承接原来散落在 `main.py` 的 active/queue/pending approval 状态；
    后续后台任务和取消信号也继续收进来。
    """

    def __init__(self) -> None:
        self.active_session_ids: set[str] = set()
        self.queued_session_messages: dict[str, list[ChatMessage]] = defaultdict(list)
        self.pending_approvals = PendingApprovalStore()
        self._lock = Lock()

    def is_active(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self.active_session_ids

    def mark_active(self, session_id: str) -> None:
        with self._lock:
            self.active_session_ids.add(session_id)

    def mark_idle(self, session_id: str) -> None:
        with self._lock:
            self.active_session_ids.discard(session_id)

    def enqueue_message(self, session_id: str, message: ChatMessage) -> None:
        with self._lock:
            self.queued_session_messages.setdefault(session_id, []).append(message)

    def drain_queued_messages(self, session_id: str) -> list[ChatMessage]:
        with self._lock:
            return self.queued_session_messages.pop(session_id, [])

    def create_pending_approval(
        self,
        *,
        session_id: str,
        turn_id: str,
        call_id: str,
        tool: str,
        arguments: dict,
        permission: str,
        reason: str,
    ) -> PendingApproval:
        return self.pending_approvals.create(
            session_id=session_id,
            turn_id=turn_id,
            call_id=call_id,
            tool=tool,
            arguments=arguments,
            permission=permission,
            reason=reason,
        )

    def list_pending_approvals(self, *, session_id: str | None = None) -> list[PendingApproval]:
        return self.pending_approvals.list_pending(session_id=session_id)

    def approve_pending_approval(self, approval_id: str) -> PendingApproval | None:
        return self.pending_approvals.approve(approval_id)

    def deny_pending_approval(self, approval_id: str, *, reason: str = "") -> PendingApproval | None:
        return self.pending_approvals.deny(approval_id, reason=reason)
