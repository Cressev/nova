from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from nova.runtime import CodexLikeAgentRuntime
from nova.providers.bigmodel import BigModelProvider
from nova.providers.bigmodel import ProviderDecision


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
        self.assertNotIn("git_status", text)
        self.assertNotIn("git_diff", text)

    def test_builtin_status_does_not_call_retired_git_tool(self) -> None:
        def fail_git_status(_arguments):
            raise AssertionError("/status 不应再调用 git_status 专用工具")

        self.runtime.tools.git_status = fail_git_status

        text = self.runtime._builtin_response("/status")

        self.assertIn("Nova 本地网关在线", text)
        self.assertIn("工作区", text)
        self.assertNotIn("Git 状态", text)

    def test_builtin_memory_command_uses_separated_sources(self) -> None:
        Path(self.tmpdir.name, "AGENTS.md").write_text("项目指令", encoding="utf-8")
        Path(self.tmpdir.name, "CURRENT.md").write_text("开发状态", encoding="utf-8")

        text = self.runtime._builtin_response("/memory")

        self.assertIn("注入给开发 Agent", text)
        self.assertIn("只给 Nova 开发过程", text)
        self.assertIn("AGENTS.md", text)
        self.assertIn("CURRENT.md", text)

    def test_builtin_compact_writes_session_memory_and_emits_boundary(self) -> None:
        from nova.models import ChatMessage, ChatRole

        messages = [
            ChatMessage(session_id="s", role=ChatRole.USER, content="我要做一个对标 Codex 的网页 Agent。"),
            ChatMessage(session_id="s", role=ChatRole.ASSISTANT, content="先补齐工具调用和权限审批。"),
            ChatMessage(session_id="s", role=ChatRole.USER, content="/compact"),
        ]

        async def collect_events() -> list[dict]:
            return [event async for event in self.runtime.stream(messages)]

        events = asyncio.run(collect_events())
        text = "".join(event.get("delta", "") for event in events if event["type"] == "assistant_delta")
        session_memory = Path(self.tmpdir.name, ".nova", "memory", "session.md").read_text(encoding="utf-8")

        self.assertIn("会话已压缩", text)
        self.assertIn("对标 Codex", session_memory)
        self.assertIn("先补齐工具调用", session_memory)
        self.assertTrue(any(event["type"] == "compact_done" for event in events))
        self.assertTrue(any(event["type"] == "agent_status" and "压缩边界" in event["status"] for event in events))

    def test_builtin_memory_subcommands_search_summarize_and_compact(self) -> None:
        memory_dir = Path(self.tmpdir.name, ".nova", "memory")
        memory_dir.mkdir(parents=True)
        (memory_dir / "index.md").write_text("- 用户偏好：中文输出\n- 项目目标：对标 Codex\n", encoding="utf-8")
        (memory_dir / "session.md").write_text("# 会话\n继续补 memory compact。\n", encoding="utf-8")

        search = self.runtime._builtin_response("/memory", "/memory search Codex")
        summarize = self.runtime._builtin_response("/memory", "/memory summarize")
        compact = self.runtime._builtin_response("/memory", "/memory compact")

        self.assertIn("index.md:2", search)
        self.assertIn("项目目标：对标 Codex", search)
        self.assertIn("记忆摘要", summarize)
        self.assertIn("session.md", summarize)
        self.assertIn("已压缩记忆", compact)
        self.assertIn("memory/project.md", compact)

    def test_builtin_help_lists_all_required_slash_commands(self) -> None:
        text = self.runtime._builtin_response("/help", "/help")

        for command in [
            "/help",
            "/status",
            "/model",
            "/tools",
            "/permissions",
            "/approvals",
            "/sandbox",
            "/memory",
            "/remember",
            "/ps",
            "/jobs",
            "/stop",
            "/kill",
            "/review",
            "/plan",
            "/compact",
            "/clear",
        ]:
            self.assertIn(command, text)

    def test_builtin_jobs_and_stop_aliases_are_executable(self) -> None:
        jobs = self.runtime._builtin_response("/jobs", "/jobs")
        stop = self.runtime._builtin_response("/stop", "/stop")

        self.assertIn("后台任务", jobs)
        self.assertIn("用法：/stop <后台任务ID>", stop)

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

    def test_wifi_password_request_does_not_bypass_model_with_direct_shell_tool(self) -> None:
        calls = self.runtime._direct_tool_calls_from_user("我的wifi密码是多少")

        self.assertEqual(calls, [])

    def test_wifi_password_request_stream_does_not_call_shell_before_model_decision(self) -> None:
        from nova.models import ChatMessage, ChatRole

        provider = _DecisionProvider(ProviderDecision(content="这需要用户确认权限后再处理。", tool_calls=[]))
        runtime = CodexLikeAgentRuntime(
            provider=provider,  # type: ignore[arg-type]
            project_root=Path(self.tmpdir.name),
        )

        async def collect_events() -> list[dict]:
            return [
                event
                async for event in runtime.stream(
                    [ChatMessage(session_id="s", role=ChatRole.USER, content="我的wifi密码是多少")]
                )
            ]

        events = asyncio.run(collect_events())

        self.assertFalse(any(event["type"] == "tool_start" and event.get("tool") == "shell_command" for event in events))

    def test_direct_tool_answer_uses_provider_when_configured(self) -> None:
        from nova.models import ChatMessage, ChatRole

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

    def test_no_tool_decision_content_is_returned_without_second_model_call(self) -> None:
        from nova.models import ChatMessage, ChatRole

        provider = _DecisionProvider(ProviderDecision(content="普通最终答案", tool_calls=[]))
        runtime = CodexLikeAgentRuntime(
            provider=provider,  # type: ignore[arg-type]
            project_root=Path(self.tmpdir.name),
        )

        async def collect_events() -> list[dict]:
            return [
                event
                async for event in runtime.stream(
                    [ChatMessage(session_id="s", role=ChatRole.USER, content="请直接回答你好")]
                )
            ]

        events = asyncio.run(collect_events())
        text = "".join(event.get("delta", "") for event in events if event["type"] == "assistant_delta")

        self.assertIn("普通最终答案", text)
        self.assertFalse(provider.stream_called)
        self.assertFalse(any(item["type"] == "web_search" for item in provider.seen_tools))
        function_names = [item["function"]["name"] for item in provider.seen_tools if item["type"] == "function"]
        self.assertIn("read_file", function_names)

    def test_web_search_intent_routes_to_local_zai_tool(self) -> None:
        runtime = CodexLikeAgentRuntime(
            provider=BigModelProvider(),
            project_root=Path(self.tmpdir.name),
            network_access=True,
        )

        calls = runtime._direct_tool_calls_from_user("请联网搜索 Nova 最新信息")

        self.assertEqual(calls[0]["tool"], "web_search")
        self.assertEqual(calls[0]["arguments"]["query"], "请联网搜索 Nova 最新信息")
        self.assertEqual(calls[0]["arguments"]["search_engine"], "search_pro")
        self.assertEqual(calls[0]["arguments"]["content_size"], "high")

    def test_langfuse_recorder_observes_turn_generation_and_tool(self) -> None:
        from nova.models import ChatMessage, ChatRole

        Path(self.tmpdir.name, "README.md").write_text("Nova\n", encoding="utf-8")
        provider = _DecisionProvider(
            ProviderDecision(
                content="我先读取 README。",
                tool_calls=[{"tool": "read_file", "arguments": {"path": "README.md", "annotation": "读取说明"}}],
            )
        )
        recorder = _FakeTraceRecorder()
        runtime = CodexLikeAgentRuntime(
            provider=provider,  # type: ignore[arg-type]
            project_root=Path(self.tmpdir.name),
            trace_recorder=recorder,
        )

        async def collect_events() -> list[dict]:
            return [
                event
                async for event in runtime.stream(
                    [ChatMessage(session_id="trace-session", role=ChatRole.USER, content="读取 README")]
                )
            ]

        asyncio.run(collect_events())

        kinds = [item["kind"] for item in recorder.records]
        self.assertIn("turn_start", kinds)
        self.assertIn("generation", kinds)
        self.assertIn("tool", kinds)
        self.assertIn("turn_end", kinds)
        tool_record = next(item for item in recorder.records if item["kind"] == "tool")
        self.assertEqual(tool_record["tool"], "read_file")
        self.assertTrue(tool_record["ok"])

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

    def test_standard_tool_calls_with_annotations_execute_in_parallel(self) -> None:
        Path(self.tmpdir.name, "README.md").write_text("Nova\n", encoding="utf-8")
        Path(self.tmpdir.name, "AGENTS.md").write_text("项目指令\n", encoding="utf-8")

        async def collect_events() -> list[dict]:
            return [
                event
                async for event in self.runtime._run_tool_calls(
                    [
                        {
                            "id": "call_readme",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path":"README.md","annotation":"读取 README"}',
                            },
                        },
                        {
                            "id": "call_agents",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path":"AGENTS.md","annotation":"读取项目指令"}',
                            },
                        },
                    ]
                )
            ]

        events = asyncio.run(collect_events())
        starts = [event for event in events if event["type"] == "tool_start"]

        self.assertEqual(len(starts), 2)
        self.assertTrue(all(event.get("parallel") for event in starts))
        self.assertEqual(starts[0]["title"], "读取 README")
        self.assertEqual(starts[0]["data"]["annotation"], "读取 README")
        self.assertNotIn("annotation", starts[0]["arguments"])

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

    def test_on_request_allows_low_risk_shell_and_prompts_for_high_risk_shell(self) -> None:
        runtime = CodexLikeAgentRuntime(
            provider=BigModelProvider(),
            project_root=Path(self.tmpdir.name),
            permission_mode="workspace_write",
            approval_policy="on_request",
        )

        async def run(command: str) -> list[dict]:
            return [
                event
                async for event in runtime._run_tool_calls(
                    [{"tool": "shell_command", "arguments": {"command": command, "workdir": "."}}]
                )
            ]

        low_risk_events = asyncio.run(run("pwd"))
        high_risk_events = asyncio.run(run("git push origin main"))

        self.assertFalse(any(event["type"] == "permission_request" for event in low_risk_events))
        self.assertTrue(any(event["type"] == "tool_start" for event in low_risk_events))
        request = next(event for event in high_risk_events if event["type"] == "permission_request")
        self.assertEqual(request["arguments"]["command"], "git push origin main")
        self.assertEqual(request["data"]["risk"], "high")

    def test_bypass_permissions_still_refuses_blacklisted_shell_without_prompt(self) -> None:
        runtime = CodexLikeAgentRuntime(
            provider=BigModelProvider(),
            project_root=Path(self.tmpdir.name),
            permission_mode="bypass_permissions",
            approval_policy="on_request",
        )

        async def collect_events() -> list[dict]:
            return [
                event
                async for event in runtime._run_tool_calls(
                    [{"tool": "shell_command", "arguments": {"command": "reboot", "workdir": "."}}]
                )
            ]

        events = asyncio.run(collect_events())
        done = next(event for event in events if event["type"] == "tool_done")

        self.assertFalse(any(event["type"] == "permission_request" for event in events))
        self.assertFalse(done["ok"])
        self.assertIn("黑名单", done["output"])

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


class _DecisionProvider:
    def __init__(self, decision: ProviderDecision) -> None:
        self.decision = decision
        self.seen_tools: list[dict] = []
        self.seen_messages = []
        self.stream_called = False
        self.model = "fake-model"

    def is_configured(self) -> bool:
        return True

    def chat_tool_schemas(
        self,
        _tool_specs,
        *,
        enable_web_search: bool = False,
        enable_web_fetch: bool = True,
        web_search_only: bool = False,
    ) -> list[dict]:
        if web_search_only:
            return [{"type": "web_search", "web_search": {"enable": True}}] if enable_web_search else []
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "读取文件",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            }
        ]
        if enable_web_fetch:
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": "web_fetch",
                        "description": "抓取 URL",
                        "parameters": {"type": "object", "properties": {}, "required": []},
                    },
                }
            )
        if enable_web_search:
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": "Z.ai 搜索",
                        "parameters": {"type": "object", "properties": {}, "required": []},
                    },
                }
            )
        return tools

    async def complete_with_tools(self, _messages, *, tools=None):
        self.seen_messages = list(_messages)
        self.seen_tools = list(tools or [])
        return self.decision

    async def stream(self, _messages):
        self.stream_called = True
        yield "不应该发生的第二次调用"


class _FakeTraceRecorder:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def start_turn(self, **kwargs) -> str:
        self.records.append({"kind": "turn_start", **kwargs})
        return "trace_1"

    def record_generation(self, **kwargs) -> None:
        self.records.append({"kind": "generation", **kwargs})

    def record_tool(self, **kwargs) -> None:
        self.records.append({"kind": "tool", **kwargs})

    def end_turn(self, **kwargs) -> None:
        self.records.append({"kind": "turn_end", **kwargs})
