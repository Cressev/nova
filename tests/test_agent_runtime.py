from __future__ import annotations

import asyncio
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

    def test_normalize_tool_call_variants(self) -> None:
        self.assertEqual(
            self.runtime._normalize_tool_call({"name": "read_file", "parameters": {"path": "README.md"}}),
            ("read_file", {"path": "README.md"}),
        )
        self.assertEqual(
            self.runtime._normalize_tool_call(
                {"function": {"name": "read_file", "arguments": '{"path":"README.md"}'}}
            ),
            ("read_file", {"path": "README.md"}),
        )

    def test_parse_wrapped_tool_calls(self) -> None:
        payload = self.runtime._parse_tool_calls(
            '<tool_call>{"tool_calls":[{"name":"git_status","parameters":{}}]}</tool_call>'
        )
        self.assertEqual(payload, [{"name": "git_status", "parameters": {}}])

    def test_parse_named_tool_call_without_json_wrapper(self) -> None:
        payload = self.runtime._parse_tool_calls('<tool_call>list_files{"path":".","limit":100}</think>')

        self.assertEqual(payload, [{"tool": "list_files", "arguments": {"path": ".", "limit": 100}}])

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

    def test_answer_from_tool_results_uses_latest_successful_output(self) -> None:
        text = self.runtime._answer_from_tool_results(
            [
                '{"tool":"list_files","title":"列出 .","ok":true,"output":"README.md\\nsrc/main.py","data":{}}'
            ]
        )

        self.assertIn("已查看当前文件目录", text)
        self.assertIn("README.md", text)

    def test_direct_directory_intent_routes_to_list_files(self) -> None:
        calls = self.runtime._direct_tool_calls_from_user("查看当前文件目录")

        self.assertEqual(calls, [{"tool": "list_files", "arguments": {"path": ".", "limit": 120}}])

    def test_direct_document_directory_intent_routes_to_docs_folder(self) -> None:
        Path(self.tmpdir.name, "产品研发文档集").mkdir()

        calls = self.runtime._direct_tool_calls_from_user("查看当前文档目录")

        self.assertEqual(calls, [{"tool": "list_files", "arguments": {"path": "产品研发文档集", "limit": 120}}])

    def test_direct_shell_intent_routes_to_safe_shell_command(self) -> None:
        calls = self.runtime._direct_tool_calls_from_user("你不会调用命令行工具吗")

        self.assertEqual(
            calls,
            [{"tool": "shell_command", "arguments": {"command": "pwd", "workdir": ".", "timeout_ms": 5000}}],
        )

    def test_direct_shell_intent_uses_command_after_colon(self) -> None:
        calls = self.runtime._direct_tool_calls_from_user("执行命令：python3 --version")

        self.assertEqual(calls[0]["tool"], "shell_command")
        self.assertEqual(calls[0]["arguments"]["command"], "python3 --version")

    def test_wifi_password_request_routes_to_shell_tool(self) -> None:
        calls = self.runtime._direct_tool_calls_from_user("我的wifi密码是多少")

        self.assertEqual(calls[0]["tool"], "shell_command")
        self.assertIn("netsh wlan show", calls[0]["arguments"]["command"])

    def test_wifi_password_request_stream_calls_shell_tool(self) -> None:
        from nova_gateway.models import ChatMessage, ChatRole

        async def collect_events() -> list[dict]:
            return [
                event
                async for event in self.runtime.stream(
                    [ChatMessage(session_id="s", role=ChatRole.USER, content="我的wifi密码是多少")]
                )
            ]

        events = asyncio.run(collect_events())

        self.assertTrue(any(event["type"] == "tool_start" and event["tool"] == "shell_command" for event in events))

    def test_direct_tool_answer_uses_provider_when_configured(self) -> None:
        from nova_gateway.models import ChatMessage, ChatRole

        async def fake_stream(_messages):
            yield "这是模型基于工具结果生成的回答"

        self.runtime.provider.is_configured = lambda: True  # type: ignore[method-assign]
        self.runtime.provider.stream = fake_stream  # type: ignore[method-assign]
        Path(self.tmpdir.name, "README.md").write_text("Nova\n", encoding="utf-8")

        async def collect_events() -> list[dict]:
            return [
                event
                async for event in self.runtime.stream(
                    [ChatMessage(session_id="s", role=ChatRole.USER, content="查看当前文件目录")]
                )
            ]

        events = asyncio.run(collect_events())
        text = "".join(event.get("delta", "") for event in events if event["type"] == "assistant_delta")

        self.assertIn("这是模型基于工具结果生成的回答", text)
        self.assertNotIn("已查看当前文件目录", text)

    def test_tool_events_have_stable_call_id(self) -> None:
        async def collect_events() -> list[dict]:
            return [
                event
                async for event in self.runtime._run_tool_calls(
                    [{"tool": "list_files", "arguments": {"path": ".", "limit": 5}}]
                )
            ]

        events = asyncio.run(collect_events())
        start = next(event for event in events if event["type"] == "tool_start")
        done = next(event for event in events if event["type"] == "tool_done")

        self.assertTrue(start["call_id"].startswith("tool_"))
        self.assertEqual(start["call_id"], done["call_id"])

    def test_shell_tool_start_streams_before_long_command_finishes_and_can_cancel(self) -> None:
        async def collect_events() -> list[dict]:
            generator = self.runtime._run_tool_calls(
                [
                    {
                        "tool": "shell_command",
                        "arguments": {
                            "command": "python3 -u -c 'import time; print(\"ready\", flush=True); time.sleep(20)'",
                            "workdir": ".",
                            "timeout_ms": 60000,
                        },
                    }
                ]
            )
            first = await anext(generator)
            self.assertEqual(first["type"], "tool_start")
            remaining_task = asyncio.create_task(_collect_async(generator))
            await asyncio.sleep(0.1)
            self.runtime.process_manager.cancel_call(first["call_id"])
            return [first, *(await remaining_task)]

        async def _collect_async(generator) -> list[dict]:
            return [event async for event in generator]

        events = asyncio.run(collect_events())
        done = next(event for event in events if event["type"] == "tool_done")

        self.assertFalse(done["ok"])
        self.assertEqual(done["data"]["status"], "cancelled")

    def test_parallel_readonly_tools_use_executor_hooks(self) -> None:
        hook_file = Path(self.tmpdir.name, ".nova-hooks.json")
        hook_file.write_text(
            '{"hooks":{"PreToolUse":[{"name":"readme-alias","matcher":"read_file",'
            '"updated_input":{"path":"README.md"}}]}}',
            encoding="utf-8",
        )
        Path(self.tmpdir.name, "README.md").write_text("Nova\n", encoding="utf-8")
        runtime = CodexLikeAgentRuntime(
            provider=BigModelProvider(),
            project_root=Path(self.tmpdir.name),
            tool_hooks_file=hook_file,
        )

        async def collect_events() -> list[dict]:
            return [
                event
                async for event in runtime._run_tool_calls(
                    [
                        {"tool": "read_file", "arguments": {"path": "ALIAS.md"}},
                        {"tool": "list_files", "arguments": {"path": ".", "limit": 5}},
                    ]
                )
            ]

        events = asyncio.run(collect_events())
        hook_events = [event for event in events if event["type"] == "hook_done"]
        read_result = next(
            event["result_json"]
            for event in events
            if event["type"] == "tool_result_json" and '"tool": "read_file"' in event["result_json"]
        )

        self.assertTrue(any(event["hook_name"] == "readme-alias" for event in hook_events))
        self.assertIn("Nova", read_result)
        self.assertTrue(any(event["type"] == "tool_start" and event.get("parallel") for event in events))

    def test_ask_permission_emits_permission_request_for_shell(self) -> None:
        runtime = CodexLikeAgentRuntime(
            provider=BigModelProvider(),
            project_root=Path(self.tmpdir.name),
            permission_mode="ask",
        )

        async def collect_events() -> list[dict]:
            return [
                event
                async for event in runtime._run_tool_calls(
                    [{"tool": "shell_command", "arguments": {"command": "pwd", "workdir": "."}}]
                )
            ]

        events = asyncio.run(collect_events())
        request = next(event for event in events if event["type"] == "permission_request")
        result = next(event for event in events if event["type"] == "tool_result_json")

        self.assertEqual(request["tool"], "shell_command")
        self.assertEqual(request["permission"], "shell")
        self.assertEqual(request["arguments"]["command"], "pwd")
        self.assertIn("permission_request", result["result_json"])

    def test_tool_hooks_emit_runtime_events_and_can_deny(self) -> None:
        hook_file = Path(self.tmpdir.name, ".nova-hooks.json")
        hook_file.write_text(
            '{"hooks":{"PreToolUse":[{"name":"deny-shell","matcher":"shell_command",'
            '"permission_decision":"deny","reason":"hook 拒绝执行"}]}}',
            encoding="utf-8",
        )
        runtime = CodexLikeAgentRuntime(
            provider=BigModelProvider(),
            project_root=Path(self.tmpdir.name),
            tool_hooks_file=hook_file,
        )

        async def collect_events() -> list[dict]:
            return [
                event
                async for event in runtime._run_tool_calls(
                    [{"tool": "shell_command", "arguments": {"command": "pwd", "workdir": "."}}]
                )
            ]

        events = asyncio.run(collect_events())

        self.assertTrue(any(event["type"] == "hook_start" and event["hook_event"] == "PreToolUse" for event in events))
        done = next(event for event in events if event["type"] == "tool_done")
        self.assertFalse(done["ok"])
        self.assertIn("hook 拒绝执行", done["output"])

    def test_final_stream_tool_calls_are_executed_not_rendered_as_text(self) -> None:
        async def fake_stream(_messages):
            yield '<tool_calls>[{"tool":"shell_command","arguments":{"command":"pwd","workdir":".","timeout_ms":5000}}]</tool_calls>'

        self.runtime.provider.stream = fake_stream  # type: ignore[method-assign]

        async def collect_events() -> list[dict]:
            return [event async for event in self.runtime._stream_final([], "")]

        events = asyncio.run(collect_events())
        deltas = "".join(event.get("delta", "") for event in events if event["type"] == "assistant_delta")

        self.assertTrue(any(event["type"] == "tool_start" and event["tool"] == "shell_command" for event in events))
        self.assertNotIn("<tool_calls>", deltas)


if __name__ == "__main__":
    unittest.main()
