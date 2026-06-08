from __future__ import annotations

import tempfile
import unittest
import json
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

    def test_permission_request_hook_allow_skips_permission_prompt_and_runs_tool(self) -> None:
        hooks = ToolHookRunner(
            {
                "PermissionRequest": [
                    {
                        "name": "allow-safe-write",
                        "matcher": "create_file",
                        "permission_decision": "allow",
                        "reason": "测试允许创建文件",
                        "additional_context": "审批 hook 已确认该写入安全",
                    }
                ]
            }
        )
        executor = ToolExecutor(WorkspaceTools(self.root), hooks=hooks)

        events, result_json = executor.run_one(
            "tool_create",
            "create_file",
            {"path": "notes.txt", "content": "ok"},
            require_permission=True,
        )

        self.assertTrue((self.root / "notes.txt").exists())
        self.assertFalse(any(event["type"] == "permission_request" for event in events))
        self.assertTrue(any(event["type"] == "hook_done" and event["hook_event"] == "PermissionRequest" for event in events))
        done = next(event for event in events if event["type"] == "tool_done")
        self.assertTrue(done["ok"])
        self.assertIn("审批 hook 已确认该写入安全", json.dumps(done["data"], ensure_ascii=False))
        self.assertIn('"ok": true', result_json)

    def test_permission_request_hook_deny_blocks_tool_and_records_context(self) -> None:
        hooks = ToolHookRunner(
            {
                "PermissionRequest": [
                    {
                        "name": "deny-dangerous-write",
                        "matcher": "write_file",
                        "permission_decision": "deny",
                        "reason": "测试拒绝覆盖文件",
                        "additional_context": "覆盖写入需要人工复核",
                    }
                ],
                "PermissionDenied": [
                    {
                        "name": "record-denial",
                        "matcher": "write_file",
                        "additional_context": "已记录拒绝事件",
                    }
                ],
            }
        )
        executor = ToolExecutor(WorkspaceTools(self.root), hooks=hooks)

        events, result_json = executor.run_one(
            "tool_write",
            "write_file",
            {"path": "README.md", "content": "replace"},
            require_permission=True,
        )

        self.assertEqual("Nova\n", (self.root / "README.md").read_text(encoding="utf-8"))
        self.assertFalse(any(event["type"] == "permission_request" for event in events))
        self.assertTrue(any(event["type"] == "hook_done" and event["hook_event"] == "PermissionDenied" for event in events))
        done = next(event for event in events if event["type"] == "tool_done")
        self.assertFalse(done["ok"])
        self.assertIn("测试拒绝覆盖文件", result_json)
        self.assertIn("覆盖写入需要人工复核", json.dumps(done["data"], ensure_ascii=False))

    def test_permission_request_hook_ask_emits_prompt_with_additional_context(self) -> None:
        hooks = ToolHookRunner(
            {
                "PermissionRequest": [
                    {
                        "name": "ask-before-write",
                        "matcher": "create_file",
                        "permission_decision": "ask",
                        "reason": "测试要求人工确认",
                        "additional_context": "需要用户确认目标路径",
                    }
                ]
            }
        )
        executor = ToolExecutor(WorkspaceTools(self.root), hooks=hooks)

        events, result_json = executor.run_one(
            "tool_create",
            "create_file",
            {"path": "manual.txt", "content": "ok"},
            require_permission=True,
        )

        self.assertFalse((self.root / "manual.txt").exists())
        request = next(event for event in events if event["type"] == "permission_request")
        self.assertEqual("测试要求人工确认", request["message"])
        self.assertIn("需要用户确认目标路径", json.dumps(request["data"], ensure_ascii=False))
        self.assertIn('"permission_request": true', result_json)

    def test_pre_and_post_hook_additional_contexts_are_in_tool_result_data(self) -> None:
        hooks = ToolHookRunner(
            {
                "PreToolUse": [
                    {
                        "name": "add-pre-context",
                        "matcher": "read_file",
                        "additional_context": "读取前上下文",
                    }
                ],
                "PostToolUse": [
                    {
                        "name": "add-post-context",
                        "matcher": "read_file",
                        "additional_context": "读取后上下文",
                    }
                ],
            }
        )
        executor = ToolExecutor(WorkspaceTools(self.root), hooks=hooks)

        events, result_json = executor.run_one("tool_read", "read_file", {"path": "README.md"})

        done = next(event for event in events if event["type"] == "tool_done")
        self.assertIn("读取前上下文", json.dumps(done["data"], ensure_ascii=False))
        self.assertIn("读取后上下文", json.dumps(done["data"], ensure_ascii=False))
        self.assertIn("读取前上下文", result_json)
        self.assertIn("读取后上下文", result_json)


if __name__ == "__main__":
    unittest.main()
