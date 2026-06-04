from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nova_gateway.agent_runtime import CodexLikeAgentRuntime
from nova_gateway.provider import BigModelProvider


class AgentRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.runtime = CodexLikeAgentRuntime(
            provider=BigModelProvider(),
            project_root=Path(self.tmpdir.name),
        )

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_parse_closed_tool_call(self) -> None:
        payload = self.runtime._parse_tool_call(
            '<tool_call>{"tool":"read_file","arguments":{"path":"README.md"}}</tool_call>'
        )
        self.assertEqual(payload, {"tool": "read_file", "arguments": {"path": "README.md"}})

    def test_parse_unclosed_tool_call(self) -> None:
        # GLM 有时只输出起始标签；Agent Runtime 仍要能识别并执行工具。
        payload = self.runtime._parse_tool_call(
            '<tool_call>{"tool":"read_file","arguments":{"path":"README.md"}}'
        )
        self.assertEqual(payload, {"tool": "read_file", "arguments": {"path": "README.md"}})

    def test_parse_parallel_tool_calls(self) -> None:
        payload = self.runtime._parse_tool_calls(
            '<tool_calls>[{"tool":"read_file","arguments":{"path":"README.md"}},'
            '{"tool":"git_status","arguments":{}}]</tool_calls>'
        )
        self.assertEqual(len(payload), 2)
        self.assertEqual(payload[0]["tool"], "read_file")
        self.assertEqual(payload[1]["tool"], "git_status")

    def test_builtin_tools_command(self) -> None:
        text = self.runtime._builtin_response("/tools")
        self.assertIn("read_file", text)
        self.assertIn("并行", text)


if __name__ == "__main__":
    unittest.main()
