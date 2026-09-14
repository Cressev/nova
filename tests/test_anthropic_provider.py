"""AnthropicProvider 协议转换单测（httpx.MockTransport，不联网）。"""

from __future__ import annotations

import asyncio
import json
import unittest

import httpx

from nova.models import ChatMessage, ChatRole
from nova.providers.anthropic import AnthropicProvider


def _messages() -> list[ChatMessage]:
    return [
        ChatMessage(session_id="t", role=ChatRole.SYSTEM, content="系统提示"),
        ChatMessage(session_id="t", role=ChatRole.USER, content="读 a.py"),
    ]


def _provider(handler) -> AnthropicProvider:
    return AnthropicProvider(
        base_url="https://api.anthropic.com",
        model="claude-test",
        http_client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


class AnthropicProtocolTest(unittest.TestCase):
    def test_complete_with_tools_normalization_and_usage(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["headers"] = dict(request.headers)
            captured["body"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(200, json={
                "content": [
                    {"type": "text", "text": "需要读文件"},
                    {"type": "tool_use", "id": "toolu_1", "name": "read", "input": {"file_path": "a.py"}},
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 11, "output_tokens": 7},
            })

        provider = _provider(handler)
        provider.set_runtime_api_key("test-key")
        decision = asyncio.run(provider.complete_with_tools(_messages(), tools=[{
            "name": "read", "description": "read a file",
            "input_schema": {"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"]},
        }]))

        self.assertEqual(decision["content"], "需要读文件")
        self.assertEqual(len(decision["tool_calls"]), 1)
        call = decision["tool_calls"][0]
        self.assertEqual((call["id"], call["tool"], call["arguments"]), ("toolu_1", "read", {"file_path": "a.py"}))
        # 请求形状：system 顶层、messages 只含 user/assistant、tools anthropic 形
        self.assertTrue(captured["url"].endswith("/v1/messages"))
        self.assertEqual(captured["headers"]["x-api-key"], "test-key")
        self.assertEqual(captured["headers"]["anthropic-version"], "2023-06-01")
        body = captured["body"]
        self.assertEqual(body["system"], "系统提示")
        self.assertEqual([m["role"] for m in body["messages"]], ["user"])
        self.assertEqual(body["tools"][0]["name"], "read")
        self.assertIn("input_schema", body["tools"][0])
        # token-meter 同口径
        self.assertEqual(provider.last_usage, {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18})

    def test_stream_with_tools_sse_aggregation(self) -> None:
        sse = "\n".join([
            'data: {"type":"message_start","message":{"usage":{"input_tokens":9}}}',
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"text"}}',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"想想"}}',
            'data: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"toolu_2","name":"bash"}}',
            'data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\\"command\\":"}}',
            'data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"\\"ls\\"}"}}',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"好"}}',
            'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":4}}',
            'data: {"type":"message_stop"}',
            "",
        ])

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

        provider = AnthropicProvider(
            base_url="https://api.anthropic.com",
            model="claude-test",
            http_client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        provider.set_runtime_api_key("test-key")

        async def collect():
            events = []
            async for event in provider.stream_with_tools(_messages(), tools=[]):
                events.append(event)
            return events

        events = asyncio.run(collect())
        deltas = [e for e in events if e["type"] == "delta"]
        decision = events[-1]
        self.assertEqual("".join(d["text"] for d in deltas), "想想好")
        self.assertEqual(decision["tool_calls"], [
            {"id": "toolu_2", "type": "function", "tool": "bash", "arguments": {"command": "ls"}},
        ])
        self.assertEqual(decision["usage"], {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13})

    def test_missing_key_explicit_error(self) -> None:
        import os

        provider = AnthropicProvider(base_url="https://api.anthropic.com", api_key_env="NOVA_TEST_MISSING_KEY")
        saved = os.environ.pop("NOVA_TEST_MISSING_KEY", None)
        try:
            with self.assertRaises(Exception) as ctx:
                asyncio.run(provider.complete_with_tools(_messages()))
            self.assertIn("NOVA_TEST_MISSING_KEY", str(ctx.exception))
        finally:
            if saved is not None:
                os.environ["NOVA_TEST_MISSING_KEY"] = saved

    def test_tool_schemas_anthropic_shape(self) -> None:
        from nova.tools.workspace import TOOL_SPECS

        provider = AnthropicProvider()
        schemas = provider.openai_tool_schemas({"read": TOOL_SPECS["read"]})
        schema = schemas[0]
        self.assertEqual(set(schema.keys()), {"name", "description", "input_schema"})
        self.assertEqual(schema["input_schema"]["type"], "object")


if __name__ == "__main__":
    unittest.main()


class ListModelsTest(unittest.TestCase):
    def test_anthropic_list_models(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/v1/models")
            assert request.headers["x-api-key"] == "test-key"
            return httpx.Response(200, json={"data": [
                {"id": "claude-sonnet-4-5", "display_name": "Sonnet 4.5"},
                {"id": "claude-opus-4", "display_name": "Opus 4"},
            ]})

        provider = AnthropicProvider(
            base_url="https://api.anthropic.com",
            http_client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        provider.set_runtime_api_key("test-key")
        models = asyncio.run(provider.list_models())
        self.assertEqual([m["id"] for m in models], ["claude-opus-4", "claude-sonnet-4-5"])
        self.assertEqual(models[1]["display_name"], "Sonnet 4.5")

    def test_anthropic_list_models_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "invalid key"})

        provider = AnthropicProvider(
            base_url="https://api.anthropic.com",
            http_client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        provider.set_runtime_api_key("bad")
        from nova.providers.anthropic import ProviderError as AnthropicError
        with self.assertRaises(AnthropicError):
            asyncio.run(provider.list_models())
