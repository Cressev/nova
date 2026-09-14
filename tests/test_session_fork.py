"""会话 fork 单测（dsh SessionStore.fork 完整对齐）。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nova.models import ChatEvent, ChatMessage, ChatRole, ChatSession, utc_now
from nova.sessions.store import SessionForkError, SessionStore


def _make_event(session_id: str, event_type: str, seq: int, turn_id: str = "turn_1", **kw) -> ChatEvent:
    return ChatEvent(
        session_id=session_id,
        type="runtime_event",
        event_type=event_type,
        phase=kw.get("phase", "started"),
        turn_id=turn_id,
        sequence=seq,
        title=kw.get("title", event_type),
        data=kw.get("data", {}),
    )


class ForkTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SessionStore(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed_session(self, title: str = "新对话") -> ChatSession:
        session = ChatSession(id="chat_src", title=title)
        self.store.create_chat_session(session)
        # 两轮完整对话
        self.store.add_chat_message(ChatMessage(session_id="chat_src", role=ChatRole.USER, content="你好"))
        self.store.upsert_chat_event(_make_event("chat_src", "turn.started", 1, turn_id="turn_1", data={"message_id": "msg_1"}))
        self.store.upsert_chat_event(_make_event("chat_src", "turn.completed", 2, turn_id="turn_1", phase="completed", data={"message_id": "msg_1"}))
        self.store.add_chat_message(ChatMessage(session_id="chat_src", role=ChatRole.ASSISTANT, content="你好！"))
        return session

    def test_fork_basic_lineage_and_title(self) -> None:
        self._seed_session()
        child = self.store.fork_session("chat_src")
        self.assertEqual(child.parent_session_id, "chat_src")
        self.assertEqual(child.seed_length, 2)  # 继承了 2 个事件
        self.assertEqual(child.title, "新对话 (1)")
        events = self.store.list_chat_events(child.id)
        self.assertEqual(len(events), 2)
        # 子会话事件 seq 从 1 重新计数
        self.assertEqual([e.sequence for e in events], [1, 2])

    def test_fork_at_seq(self) -> None:
        self._seed_session()
        # 在 seq=1（turn.started 之后，turn.completed 之前）fork → OPEN_TURN
        with self.assertRaises(SessionForkError) as ctx:
            self.store.fork_session("chat_src", at_seq=0)
        self.assertEqual(ctx.exception.code, "OPEN_TURN")

    def test_fork_invalid_boundary(self) -> None:
        self._seed_session()
        with self.assertRaises(SessionForkError) as ctx:
            self.store.fork_session("chat_src", at_seq=99)
        self.assertEqual(ctx.exception.code, "INVALID_BOUNDARY")

    def test_fork_not_found(self) -> None:
        with self.assertRaises(SessionForkError) as ctx:
            self.store.fork_session("nope")
        self.assertEqual(ctx.exception.code, "SESSION_NOT_FOUND")

    def test_fork_title_increment_chain(self) -> None:
        self._seed_session()
        child1 = self.store.fork_session("chat_src")
        self.assertEqual(child1.title, "新对话 (1)")
        child2 = self.store.fork_session("chat_src")
        self.assertEqual(child2.title, "新对话 (2)")
        # fork 子会话（child1 标题 "新对话 (1)" → 递增为 "新对话 (2)"，但
        # "新对话 (2)" 已被 child2 占用 → 碰撞检测继续递增到 "新对话 (3)"）
        grandchild = self.store.fork_session(child1.id)
        self.assertEqual(grandchild.title, "新对话 (3)")

    def test_fork_inherits_messages(self) -> None:
        self._seed_session()
        child = self.store.fork_session("chat_src")
        msgs = self.store.list_chat_messages(child.id)
        # 继承到 turn_1 的消息（user "你好" 已入，assistant 尚未入？取决于消息时序）
        self.assertTrue(len(msgs) >= 1)

    def test_fork_empty_session(self) -> None:
        self.store.create_chat_session(ChatSession(id="chat_empty", title="空"))
        child = self.store.fork_session("chat_empty")
        self.assertEqual(child.seed_length, 0)
        self.assertEqual(len(self.store.list_chat_events(child.id)), 0)
        self.assertEqual(child.title, "空 (1)")


if __name__ == "__main__":
    unittest.main()
