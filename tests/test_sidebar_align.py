"""侧边栏对齐（dsh D1/D2）API 层测试：占位空壳会话不进列表。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from nova.app.main import app
from nova.app import main as app_module
from nova.models import ChatMessage, ChatRole
from nova.sessions.store import SessionStore


class PlaceholderSessionFilterTest(unittest.TestCase):
    """GET /api/chat/sessions 过滤：占位标题且零消息的空壳不出现，数据保留。"""

    def setUp(self) -> None:
        self._real_store = app_module.store
        app_module.store = SessionStore(Path(tempfile.mkdtemp(prefix="nova-filter-")))
        self.addCleanup(self._restore_store)
        self.client = TestClient(app)

    def _restore_store(self) -> None:
        app_module.store = self._real_store

    def test_placeholder_empty_sessions_hidden_but_kept(self) -> None:
        store = app_module.store
        empty = app_module.ChatSession(id="chat_ghost", title="新对话", workspace="/tmp")
        store.create_chat_session(empty)
        titled = app_module.ChatSession(id="chat_real", title="修复登录滚动条", workspace="/tmp")
        store.create_chat_session(titled)
        store.add_chat_message(ChatMessage(session_id="chat_real", role=ChatRole.USER, content="你好"))
        # 占位但已有消息的会话（如草稿转正前就被写过消息）仍要显示
        placeholder_with_msgs = app_module.ChatSession(id="chat_ph", title="新线程", workspace="/tmp")
        store.create_chat_session(placeholder_with_msgs)
        store.add_chat_message(ChatMessage(session_id="chat_ph", role=ChatRole.USER, content="第一条"))

        payload = self.client.get("/api/chat/sessions").json()
        ids = [item["id"] for item in payload]
        self.assertNotIn("chat_ghost", ids)
        self.assertIn("chat_real", ids)
        self.assertIn("chat_ph", ids)
        # 数据保留：store 层仍能取到 ghost（只是不进侧栏）
        self.assertIsNotNone(store.get_chat_session("chat_ghost"))

    def test_backfill_titles_legacy_placeholder_sessions(self) -> None:
        """启动回填：占位标题 + 有用户消息的存量会话 → 首条消息兜底标题。"""
        store = app_module.store
        legacy = app_module.ChatSession(
            id="chat_legacy", title="新对话", workspace="/tmp", title_source="default"
        )
        store.create_chat_session(legacy)
        store.add_chat_message(ChatMessage(
            session_id="chat_legacy", role=ChatRole.USER,
            content="帮我把登录页面的滚动条遮挡问题修复一下，谢谢",
        ))
        # 重新加载触发回填迁移（幂等：二次加载结果不变）
        store2 = SessionStore(store.state_dir)
        reloaded = store2.get_chat_session("chat_legacy")
        # 16 个汉字上限截断
        self.assertEqual(reloaded.title, "帮我把登录页面的滚动条遮挡问题修")
        self.assertEqual(reloaded.title_source, "fallback")
        store3 = SessionStore(store.state_dir)
        self.assertEqual(store3.get_chat_session("chat_legacy").title, reloaded.title)
        # 用户已改名的会话绝不回填
        renamed = app_module.ChatSession(
            id="chat_renamed", title="我的会话", workspace="/tmp", title_source="user"
        )
        store3.create_chat_session(renamed)
        store3.add_chat_message(ChatMessage(session_id="chat_renamed", role=ChatRole.USER, content="随便什么内容"))
        store4 = SessionStore(store.state_dir)
        self.assertEqual(store4.get_chat_session("chat_renamed").title, "我的会话")


if __name__ == "__main__":
    unittest.main()
