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

    def test_builtin_memory_command_uses_separated_sources(self) -> None:
        Path(self.tmpdir.name, "AGENTS.md").write_text("项目指令", encoding="utf-8")
        Path(self.tmpdir.name, "CURRENT.md").write_text("开发状态", encoding="utf-8")

        text = self.runtime._builtin_response("/memory")

        self.assertIn("注入给开发 Agent", text)
        self.assertIn("只给 Nova 开发过程", text)
        self.assertIn("AGENTS.md", text)
        self.assertIn("CURRENT.md", text)

    def test_memory_context_ignores_development_state_files(self) -> None:
        Path(self.tmpdir.name, "AGENTS.md").write_text("项目指令", encoding="utf-8")
        Path(self.tmpdir.name, "CURRENT.md").write_text("开发状态不应注入", encoding="utf-8")
        Path(self.tmpdir.name, "PROGRESS.md").write_text("进度不应注入", encoding="utf-8")

        context = self.runtime.memory.context()

        self.assertIn("项目指令", context)
        self.assertNotIn("开发状态不应注入", context)
        self.assertNotIn("进度不应注入", context)


if __name__ == "__main__":
    unittest.main()
