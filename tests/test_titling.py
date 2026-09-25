from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from nova.models import ChatSession
from nova.sessions.store import SessionStore
from nova.sessions.titling import (
    fallback_title,
    generate_llm_title,
    is_placeholder_title,
    normalize_title,
)


def _session(title: str, source: str = "default") -> ChatSession:
    return ChatSession(
        id="chat_t1",
        title=title,
        workspace="/tmp/proj",
        title_source=source,
    )


class FallbackTitleTest(unittest.TestCase):
    def test_cjk_title_takes_leading_characters(self) -> None:
        # 16 个汉字上限：这句话恰好前 16 字全是汉字，第 16 字处截断。
        self.assertEqual(fallback_title("帮我修复登录页面的滚动条遮挡问题，谢谢"), "帮我修复登录页面的滚动条遮挡问题")
        self.assertEqual(fallback_title("重构 parser"), "重构 parser")

    def test_english_title_takes_leading_words(self) -> None:
        self.assertEqual(
            fallback_title("please fix the login page scrollbar overlap bug"),
            "please fix the login page scrollbar overlap bug"[:96].rsplit(" ", 1)[0]
            if len("please fix the login page scrollbar overlap bug") > 96
            else "please fix the login page scrollbar overlap bug",
        )
        self.assertEqual(fallback_title("one two three four five six seven eight nine ten"), "one two three four five six seven eight")

    def test_control_sequences_stripped(self) -> None:
        self.assertEqual(fallback_title("\x1b[31m红色标题\x1b[0m"), "红色标题")
        self.assertEqual(normalize_title("a\x1b]0;title\x07b"), "ab")

    def test_whitespace_collapsed_and_trimmed(self) -> None:
        # 折叠空白为单空格：tab/换行都变空格，前后 trim。
        self.assertEqual(fallback_title("  多  空\t白\n行  "), "多 空 白 行")

    def test_empty_after_clean_returns_empty(self) -> None:
        self.assertEqual(fallback_title("   "), "")
        self.assertEqual(fallback_title("\x1b[2K"), "")

    def test_placeholder_detection(self) -> None:
        self.assertTrue(is_placeholder_title("新对话"))
        self.assertTrue(is_placeholder_title(" 新线程 "))
        self.assertFalse(is_placeholder_title("修复滚动条"))


class AutoTitleStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.store = SessionStore(Path(self.tmpdir.name))
        self.store.create_chat_session(
            ChatSession(id="chat_s1", title="新对话", workspace="/tmp/proj")
        )

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_fallback_applies_only_to_default(self) -> None:
        updated = self.store.auto_title_chat_session("chat_s1", "修复登录滚动条", "fallback")
        self.assertIsNotNone(updated)
        self.assertEqual(updated.title, "修复登录滚动条")
        self.assertEqual(updated.title_source, "fallback")
        # 已有兜底后不再重复接管
        again = self.store.auto_title_chat_session("chat_s1", "另一个标题", "fallback")
        self.assertIsNone(again)

    def test_llm_upgrades_fallback_but_not_user(self) -> None:
        self.store.auto_title_chat_session("chat_s1", "修复登录滚动条", "fallback")
        upgraded = self.store.auto_title_chat_session("chat_s1", "修复登录页滚动条遮挡", "llm")
        self.assertIsNotNone(upgraded)
        self.assertEqual(upgraded.title_source, "llm")
        # llm 之后再尝试 llm 不覆盖
        self.assertIsNone(self.store.auto_title_chat_session("chat_s1", "第三次标题", "llm"))

    def test_user_rename_pins_against_auto(self) -> None:
        self.store.rename_chat_session("chat_s1", "我的会话")
        self.assertIsNone(self.store.auto_title_chat_session("chat_s1", "自动标题", "fallback"))
        self.assertIsNone(self.store.auto_title_chat_session("chat_s1", "自动标题", "llm"))
        session = self.store.get_chat_session("chat_s1")
        self.assertEqual(session.title, "我的会话")
        self.assertEqual(session.title_source, "user")

    def test_unknown_session_returns_none(self) -> None:
        self.assertIsNone(self.store.auto_title_chat_session("chat_missing", "标题", "fallback"))


class _FakeProvider:
    def __init__(self, output: str) -> None:
        self.output = output
        self.calls: list[list] = []

    async def complete(self, messages) -> str:
        self.calls.append(messages)
        return self.output


class GenerateLlmTitleTest(unittest.TestCase):
    def test_normalizes_and_strips_quotes(self) -> None:
        provider = _FakeProvider('“修复登录页滚动条”。')
        title = asyncio.run(generate_llm_title(provider, ["帮我修复登录页的滚动条"]))
        self.assertEqual(title, "修复登录页滚动条")

    def test_placeholder_output_rejected(self) -> None:
        provider = _FakeProvider("新对话")
        self.assertIsNone(asyncio.run(generate_llm_title(provider, ["内容"])))

    def test_failure_returns_none(self) -> None:
        class BrokenProvider:
            async def complete(self, messages):
                raise RuntimeError("boom")

        self.assertIsNone(asyncio.run(generate_llm_title(BrokenProvider(), ["内容"])))

    def test_empty_inputs_return_none(self) -> None:
        self.assertIsNone(asyncio.run(generate_llm_title(_FakeProvider("x"), [])))


if __name__ == "__main__":
    unittest.main()
