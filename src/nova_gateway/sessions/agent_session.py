from __future__ import annotations

from collections import defaultdict
from threading import Lock

from ..models import ChatMessage


class AgentSessionService:
    """管理每个会话的运行中状态和排队输入。

    这里先承接原来散落在 `main.py` 的 active/queue 状态，后续再把 turn、
    审批、后台任务和取消信号继续收进来。
    """

    def __init__(self) -> None:
        self.active_session_ids: set[str] = set()
        self.queued_session_messages: dict[str, list[ChatMessage]] = defaultdict(list)
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
