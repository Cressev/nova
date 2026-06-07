from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nova_gateway.agent_tools import WorkspaceTools
from nova_gateway.tool_executor import ToolExecutor
from nova_gateway.tool_hooks import ToolHookRunner


class ToolHooksTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        (self.root / "README.md").write_text("Nova\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_pre_tool_hook_can_rewrite_input(self) -> None:
        hooks = ToolHookRunner(
            {
                "PreToolUse": [
                    {
                        "name": "readme-alias",
                        "matcher": "read_file",
                        "updated_input": {"path": "README.md"},
                        "additional_context": "已将别名解析为 README.md",
                    }
                ]
            }
        )
        executor = ToolExecutor(WorkspaceTools(self.root), hooks=hooks)

        events, result_json = executor.run_one("tool_read", "read_file", {"path": "ALIAS.md"})

        self.assertTrue(any(event["type"] == "hook_start" and event["hook_event"] == "PreToolUse" for event in events))
        self.assertTrue(any(event["type"] == "hook_done" and event["data"].get("updated_input") for event in events))
        self.assertIn("Nova", result_json)

    def test_pre_tool_hook_can_deny_tool_and_emit_permission_denied_hook(self) -> None:
        hooks = ToolHookRunner(
            {
                "PreToolUse": [
                    {
                        "name": "deny-shell",
                        "matcher": "shell_command",
                        "permission_decision": "deny",
                        "reason": "测试拒绝 shell",
                    }
                ],
                "PermissionDenied": [
                    {
                        "name": "record-denial",
                        "matcher": "shell_command",
                        "additional_context": "shell 已被策略拒绝",
                    }
                ],
            }
        )
        executor = ToolExecutor(WorkspaceTools(self.root), hooks=hooks)

        events, result_json = executor.run_one("tool_shell", "shell_command", {"command": "pwd"})

        self.assertTrue(any(event["type"] == "hook_start" and event["hook_event"] == "PermissionDenied" for event in events))
        done = next(event for event in events if event["type"] == "tool_done")
        self.assertEqual(done["arguments"], {"command": "pwd"})
        self.assertIn("测试拒绝 shell", result_json)
        self.assertIn('"ok": false', result_json)


if __name__ == "__main__":
    unittest.main()
