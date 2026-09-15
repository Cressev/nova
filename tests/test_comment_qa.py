"""划词评论问答（旁路线程）单测。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nova.context_budget import estimate_tokens
from nova.models import ChatMessage, ChatRole
from nova.sessions.comment_qa import build_comment_messages
from nova.sessions.comments import CommentStore
from nova.sessions.store import SessionStore


class CommentQATest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SessionStore(Path(self._tmp.name))
        self.comments = CommentStore(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed(self) -> None:
        from nova.models import ChatSession

        self.store.create_chat_session(ChatSession(id="s1", title="t"))
        self.store.add_chat_message(ChatMessage(session_id="s1", role=ChatRole.USER, content="主线问题"))
        self.store.add_chat_message(ChatMessage(id="m1", session_id="s1", role=ChatRole.ASSISTANT, content="很长的一段回复，其中包含被选中的那句话。"))

    def test_store_roundtrip_and_recovery(self) -> None:
        self._seed()
        anchor = self.comments.create_anchor("s1", "m1", "被选中的那句话", "回复，其中包含", "。")
        self.comments.add_entry(anchor.id, "user", "什么意思")
        self.comments.add_entry(anchor.id, "assistant", "意思是……")
        threads = self.comments.list_threads("s1")
        self.assertEqual(len(threads), 1)
        self.assertEqual(len(threads[0]["entries"]), 2)
        # 重启恢复
        restored = CommentStore(Path(self._tmp.name))
        self.assertEqual(len(restored.list_threads("s1")[0]["entries"]), 2)
        # 删除
        self.assertTrue(restored.delete_anchor("s1", anchor.id))
        self.assertEqual(restored.list_threads("s1"), [])

    def test_build_messages_layers(self) -> None:
        self._seed()
        anchor = self.comments.create_anchor("s1", "m1", "被选中的那句话")
        self.comments.add_entry(anchor.id, "user", "第一问")
        self.comments.add_entry(anchor.id, "assistant", "第一答")
        messages = build_comment_messages(self.store, self.comments, anchor, "第二问")
        # 系统层
        self.assertEqual(messages[0].role, ChatRole.SYSTEM)
        self.assertIn("旁路解释助手", messages[0].content)
        # 背景层存在
        joined = "\n".join(m.content for m in messages)
        self.assertIn("主线问题", joined)
        # 引用消息全文
        self.assertIn("很长的一段回复", joined)
        # quote
        self.assertIn("「被选中的那句话」", joined)
        # 线程历史
        self.assertIn("第一问", joined)
        self.assertIn("第一答", joined)
        # 当前问题在最后
        self.assertEqual(messages[-1].content, "第二问")
        self.assertEqual(messages[-1].role, ChatRole.USER)

    def test_background_trim_drops_oldest(self) -> None:
        self._seed()
        # 塞大量老消息
        for i in range(50):
            self.store.add_chat_message(ChatMessage(session_id="s1", role=ChatRole.USER, content=f"老消息{i}" + "字" * 200))
        anchor = self.comments.create_anchor("s1", "m1", "被选中的那句话")
        messages = build_comment_messages(self.store, self.comments, anchor, "问题", background_limit_tokens=1500)
        bg_text = ""
        for m in messages:
            if m.content.startswith("以下是主对话背景"):
                bg_text = m.content
                break
        # 最老的主线第一条应被丢掉
        self.assertNotIn("主线问题", bg_text)
        self.assertNotIn("老消息0", bg_text)
        # 保留的应是最新若干条
        self.assertIn("老消息49", bg_text)
        # 估算背景不超过预算
        self.assertLessEqual(estimate_tokens(bg_text), 1500)

    def test_comment_not_in_main_context(self) -> None:
        """评论数据不得进入主会话消息序列（旁路语义）。"""
        self._seed()
        anchor = self.comments.create_anchor("s1", "m1", "被选中的那句话")
        self.comments.add_entry(anchor.id, "user", "旁路问题ABC")
        main_messages = self.store.list_chat_messages("s1")
        for m in main_messages:
            self.assertNotIn("旁路问题ABC", m.content or "")


if __name__ == "__main__":
    unittest.main()
