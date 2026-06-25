from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from nova.models import ChatMessage, ChatRole
from nova.providers.bigmodel import BigModelProvider
from nova.tools.workspace import TOOL_SPECS


class BigModelProviderTest(unittest.TestCase):
    def test_assistant_message_text_never_executes_reasoning_content(self) -> None:
        provider = BigModelProvider()
        text = provider._assistant_message_text(
            {
                "content": "",
                "reasoning_content": (
                    '<tool_calls>[{"tool":"read_file","arguments":{"path":"README.md"}}]</tool_calls>'
                ),
            }
        )

        self.assertEqual(text, "")

    def test_complete_does_not_expose_plain_reasoning_content(self) -> None:
        provider = BigModelProvider()
        text = provider._assistant_message_text(
            {
                "content": "",
                "reasoning_content": "这里是模型内部推理，不应该展示给用户。",
            }
        )

        self.assertEqual(text, "")

    def test_openai_tool_schemas_include_annotation_parameter(self) -> None:
        provider = BigModelProvider()

        schemas = provider.openai_tool_schemas(TOOL_SPECS)
        read_file = next(item for item in schemas if item["function"]["name"] == "read_file")
        properties = read_file["function"]["parameters"]["properties"]

        self.assertEqual(read_file["type"], "function")
        self.assertIn("path", properties)
        self.assertEqual(properties["annotation"]["type"], "string")
        self.assertIn("annotation", read_file["function"]["parameters"]["required"])

    def test_openai_function_schemas_expose_local_web_search_tool(self) -> None:
        provider = BigModelProvider()

        schemas = provider.openai_tool_schemas()
        function_names = [item["function"]["name"] for item in schemas if item["type"] == "function"]

        self.assertIn("web_search", function_names)
        self.assertIn("web_fetch", function_names)

    def test_openai_function_schemas_hide_retired_model_tools(self) -> None:
        provider = BigModelProvider()

        schemas = provider.openai_tool_schemas()
        function_names = {item["function"]["name"] for item in schemas if item["type"] == "function"}

        self.assertIn("read_file", function_names)
        self.assertIn("write_file", function_names)
        self.assertIn("apply_patch", function_names)
        for retired in {"read_many_files", "git_status", "git_diff", "replace_in_file", "edit_file", "multi_edit", "create_file"}:
            self.assertNotIn(retired, function_names)

    def test_chat_tool_schemas_can_hide_web_fetch_for_search_queries_without_url(self) -> None:
        provider = BigModelProvider()

        schemas = provider.chat_tool_schemas(TOOL_SPECS, enable_web_search=True, enable_web_fetch=False)
        function_names = [item["function"]["name"] for item in schemas if item["type"] == "function"]

        self.assertNotIn("web_fetch", function_names)
        self.assertIn("web_search", function_names)
        self.assertFalse(any(item["type"] == "web_search" for item in schemas))

    def test_chat_tool_schemas_skip_bigmodel_web_search_when_network_disabled(self) -> None:
        provider = BigModelProvider()

        schemas = provider.chat_tool_schemas(TOOL_SPECS, enable_web_search=False)

        function_names = [item["function"]["name"] for item in schemas if item["type"] == "function"]
        self.assertNotIn("web_search", function_names)
        self.assertFalse(any(item["type"] == "web_search" for item in schemas))

    def test_complete_with_tools_returns_standard_tool_calls(self) -> None:
        provider = BigModelProvider(client_factory=lambda _api_key: _FakeOpenAIClient())
        provider.set_runtime_api_key("test-key")
        messages = [ChatMessage(session_id="s", role=ChatRole.USER, content="读三个文件")]

        decision = asyncio.run(provider.complete_with_tools(messages, tools=provider.openai_tool_schemas()))

        self.assertEqual(decision.content, "我会并行读取这些文件。")
        self.assertEqual(len(decision.tool_calls), 3)
        self.assertEqual(decision.tool_calls[0]["tool"], "read_file")
        self.assertEqual(decision.tool_calls[0]["arguments"]["annotation"], "读取 README")

    def test_complete_uses_openai_sdk_client_for_text_response(self) -> None:
        fake_client = _FakeOpenAIClient()
        provider = BigModelProvider(client_factory=lambda _api_key: fake_client)
        provider.set_runtime_api_key("test-key")
        messages = [ChatMessage(session_id="s", role=ChatRole.USER, content="你好")]

        text = asyncio.run(provider.complete(messages))

        self.assertEqual(text, "这是普通文本回答。")
        self.assertEqual(fake_client.chat.completions.calls[-1]["stream"], False)
        self.assertNotIn("tools", fake_client.chat.completions.calls[-1])

    def test_stream_uses_openai_sdk_client_for_text_deltas(self) -> None:
        fake_client = _FakeOpenAIClient()
        provider = BigModelProvider(client_factory=lambda _api_key: fake_client)
        provider.set_runtime_api_key("test-key")
        messages = [ChatMessage(session_id="s", role=ChatRole.USER, content="流式回答")]

        async def collect() -> list[str]:
            return [delta async for delta in provider.stream(messages)]

        self.assertEqual(asyncio.run(collect()), ["流式", "回答"])
        self.assertEqual(fake_client.chat.completions.calls[-1]["stream"], True)

    def test_stream_delta_ignores_empty_choices_and_reasoning_only_delta(self) -> None:
        provider = BigModelProvider()

        self.assertIsNone(provider._stream_delta_text({}))
        self.assertIsNone(provider._stream_delta_text({"reasoning_content": "<tool_calls>[]</tool_calls>"}))
        self.assertEqual(provider._stream_delta_text({"content": "可展示文本"}), "可展示文本")


class _FakeCompletions:
    def __init__(self) -> None:
        self.calls = []

    async def create(self, **_kwargs):
        self.calls.append(_kwargs)
        if _kwargs.get("stream"):
            return _FakeStream()
        if "tools" not in _kwargs:
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="这是普通文本回答。", tool_calls=None)
                    )
                ]
            )
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="我会并行读取这些文件。",
                        tool_calls=[
                            SimpleNamespace(
                                id="call_readme",
                                function=SimpleNamespace(
                                    name="read_file",
                                    arguments='{"path":"README.md","annotation":"读取 README"}',
                                ),
                            ),
                            SimpleNamespace(
                                id="call_agents",
                                function=SimpleNamespace(
                                    name="read_file",
                                    arguments='{"path":"AGENTS.md","annotation":"读取项目指令"}',
                                ),
                            ),
                            SimpleNamespace(
                                id="call_progress",
                                function=SimpleNamespace(
                                    name="read_file",
                                    arguments='{"path":"PROGRESS.md","annotation":"读取进度"}',
                                ),
                            ),
                        ],
                    )
                )
            ]
        )


class _FakeOpenAIClient:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions())


class _FakeStream:
    def __aiter__(self):
        self._chunks = iter(
            [
                SimpleNamespace(choices=[]),
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(reasoning_content="内部推理"))]),
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="流式"))]),
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="回答"))]),
            ]
        )
        return self

    async def __anext__(self):
        try:
            return next(self._chunks)
        except StopIteration as exc:
            raise StopAsyncIteration from exc
