from __future__ import annotations

import asyncio
import json
import unittest

from nova.runtime import ToolOrchestrator


class ToolOrchestratorTest(unittest.TestCase):
    def test_parallel_readonly_calls_are_coordinated_in_one_place(self) -> None:
        traced: list[dict] = []
        orchestrator = ToolOrchestrator(
            tools=_FakeTools(parallel_tools={"read_file"}),
            executor=_FakeExecutor(),
            permission_mode="workspace_write",
            approval_policy="never",
            trace_tool_event=traced.append,
        )

        async def collect_events() -> list[dict]:
            return [
                event
                async for event in orchestrator.run(
                    [
                        {"tool": "read_file", "arguments": {"path": "README.md"}},
                        {
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path":"AGENTS.md"}',
                            }
                        },
                    ]
                )
            ]

        events = asyncio.run(collect_events())
        starts = [event for event in events if event["type"] == "tool_start"]
        results = [json.loads(event["result_json"]) for event in events if event["type"] == "tool_result_json"]

        self.assertIn("并行执行 2 个只读工具", [event.get("status") for event in events])
        self.assertEqual([event["arguments"]["path"] for event in starts], ["README.md", "AGENTS.md"])
        self.assertTrue(all(event["parallel"] for event in starts))
        self.assertEqual([item["path"] for item in results], ["README.md", "AGENTS.md"])
        self.assertEqual([event["type"] for event in traced], ["tool_start", "tool_done", "tool_start", "tool_done"])

    def test_permission_policy_matches_runtime_modes(self) -> None:
        orchestrator = ToolOrchestrator(
            tools=_FakeTools(),
            executor=_FakeExecutor(),
            permission_mode="ask",
            approval_policy="never",
        )

        self.assertFalse(orchestrator.requires_permission_request("read_file"))
        self.assertTrue(orchestrator.requires_permission_request("shell_command"))

        orchestrator.permission_mode = "bypass_permissions"
        self.assertFalse(orchestrator.requires_permission_request("shell_command"))

        orchestrator.permission_mode = "workspace_write"
        orchestrator.approval_policy = "on_request"
        self.assertTrue(orchestrator.requires_permission_request("shell_command"))

    def test_resume_approved_tool_marks_executor_call_as_approved(self) -> None:
        executor = _FakeExecutor()
        orchestrator = ToolOrchestrator(
            tools=_FakeTools(),
            executor=executor,
            permission_mode="ask",
            approval_policy="on_request",
        )

        events, result_json = asyncio.run(
            orchestrator.resume_approved_tool(
                "tool_shell",
                "shell_command",
                {"command": "pwd"},
            )
        )

        self.assertTrue(json.loads(result_json)["ok"])
        self.assertEqual(events[0]["call_id"], "tool_shell")
        self.assertTrue(events[0]["approved"])


class _FakeTools:
    def __init__(self, parallel_tools: set[str] | None = None) -> None:
        self.parallel_tools = parallel_tools or set()

    def supports_parallel(self, name: str) -> bool:
        return name in self.parallel_tools


class _FakeExecutor:
    def run_one(self, call_id: str, name: str, arguments: dict, *, parallel: bool = False):
        events = [
            {
                "type": "tool_start",
                "call_id": call_id,
                "tool": name,
                "arguments": arguments,
                "parallel": parallel,
            },
            {
                "type": "tool_done",
                "call_id": call_id,
                "tool": name,
                "arguments": arguments,
                "parallel": parallel,
                "ok": True,
            },
        ]
        return events, json.dumps({"ok": True, "tool": name, **arguments}, ensure_ascii=False)

    def iter_one_stream(
        self,
        call_id: str,
        name: str,
        arguments: dict,
        *,
        require_permission: bool = False,
        approved: bool = False,
    ):
        yield {
            "type": "tool_start",
            "call_id": call_id,
            "tool": name,
            "arguments": arguments,
            "require_permission": require_permission,
            "approved": approved,
        }
        yield {
            "type": "tool_done",
            "call_id": call_id,
            "tool": name,
            "arguments": arguments,
            "ok": True,
        }
        yield {
            "type": "tool_result_json",
            "result_json": json.dumps({"ok": True, "tool": name, **arguments}, ensure_ascii=False),
        }


if __name__ == "__main__":
    unittest.main()
