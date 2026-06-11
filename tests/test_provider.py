from __future__ import annotations

import unittest

from nova_gateway.providers.bigmodel import BigModelProvider


class BigModelProviderTest(unittest.TestCase):
    def test_complete_uses_reasoning_content_only_for_tool_calls(self) -> None:
        provider = BigModelProvider()
        text = provider._assistant_message_text(
            {
                "content": "",
                "reasoning_content": (
                    '<tool_calls>[{"tool":"read_file","arguments":{"path":"README.md"}}]</tool_calls>'
                ),
            }
        )

        self.assertIn("<tool_calls>", text)
        self.assertIn("read_file", text)

    def test_complete_does_not_expose_plain_reasoning_content(self) -> None:
        provider = BigModelProvider()
        text = provider._assistant_message_text(
            {
                "content": "",
                "reasoning_content": "这里是模型内部推理，不应该展示给用户。",
            }
        )

        self.assertEqual(text, "")

    def test_stream_delta_ignores_empty_choices_and_reasoning_only_delta(self) -> None:
        provider = BigModelProvider()

        self.assertIsNone(provider._stream_delta_text({}))
        self.assertIsNone(provider._stream_delta_text({"reasoning_content": "<tool_calls>[]</tool_calls>"}))
        self.assertEqual(provider._stream_delta_text({"content": "可展示文本"}), "可展示文本")
