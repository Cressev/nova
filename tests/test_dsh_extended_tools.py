"""dsh 扩展工具族测试：read_image / skill / job_* / ask_user_question /
subagent 家族 / goal 三件 / schedule 三件 / lsp / session_search。"""

from __future__ import annotations

import json
import struct
import tempfile
import time
import unittest
import zlib
from pathlib import Path

from nova.processes import ProcessManager
from nova.tools.executor import ToolExecutor
from nova.tools.workspace import ToolExecutionError, WorkspaceTools


def _make_tools(**kwargs) -> WorkspaceTools:
    root = Path(tempfile.mkdtemp())
    tools = WorkspaceTools(root, permission_mode="bypass_permissions", **kwargs)
    tools.process_manager = ProcessManager()
    return tools


def _tiny_png(width: int = 3, height: int = 2) -> bytes:
    header = b"\x89PNG\r\n\x1a\n"

    def chunk(typ: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + typ
            + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    return header + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class ReadImageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tools = _make_tools()

    def test_png_signature_and_dimensions(self) -> None:
        (self.tools.project_root / "img.png").write_bytes(_tiny_png(4, 3))
        result = self.tools.run("read_image", {"file_path": "img.png"})
        self.assertTrue(result.ok)
        self.assertEqual(result.data["media_type"], "image/png")
        self.assertEqual(result.data["width"], 4)
        self.assertEqual(result.data["height"], 3)
        self.assertTrue(result.data["data_url"].startswith("data:image/png;base64,"))

    def test_rejects_non_image_extension(self) -> None:
        (self.tools.project_root / "note.txt").write_text("hi", encoding="utf-8")
        with self.assertRaises(ToolExecutionError) as ctx:
            self.tools.run("read_image", {"file_path": "note.txt"})
        self.assertIn("only accepts PNG/JPEG/WebP/GIF", str(ctx.exception))

    def test_rejects_fake_image_payload(self) -> None:
        (self.tools.project_root / "fake.png").write_bytes(b"not a png at all")
        with self.assertRaises(ToolExecutionError):
            self.tools.run("read_image", {"file_path": "fake.png"})


class SkillToolTest(unittest.TestCase):
    def test_unknown_skill_errors(self) -> None:
        tools = _make_tools()
        with self.assertRaises(ToolExecutionError) as ctx:
            tools.run("skill", {"name": "no-such-skill"})
        self.assertIn("is unknown or no longer available", str(ctx.exception))

    def test_loads_skill_content(self) -> None:
        tools = _make_tools()
        skill_dir = tools.project_root / ".nova" / "skills" / "demo"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\ndescription: 演示技能\n---\n# Demo\n步骤一。", encoding="utf-8")
        result = tools.run("skill", {"name": "demo"})
        self.assertIn("# Demo", result.output)
        self.assertIn("步骤一", result.output)


class JobsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tools = _make_tools()

    def test_job_output_wait_and_incremental_cursor(self) -> None:
        bg = self.tools.process_manager.start_background(
            "echo part1; sleep 0.2; echo part2", cwd=self.tools.project_root
        )
        result = self.tools.run("job_output", {"job_id": bg["id"], "wait": True, "timeout_ms": 5000})
        self.assertIn("part1", result.output)
        self.assertIn("part2", result.output)
        self.assertIn("[status: completed]", result.output)
        second = self.tools.run("job_output", {"job_id": bg["id"]})
        self.assertIn("(no new output)", second.output, "二次读取只应返回增量")

    def test_job_output_unknown_id(self) -> None:
        with self.assertRaises(ToolExecutionError) as ctx:
            self.tools.run("job_output", {"job_id": "job_missing"})
        self.assertIn("unknown job id", str(ctx.exception))

    def test_job_list_and_kill(self) -> None:
        bg = self.tools.process_manager.start_background(
            "echo x; sleep 5", cwd=self.tools.project_root
        )
        listing = self.tools.run("job_list", {})
        self.assertIn(bg["id"], listing.output)
        killed = self.tools.run("job_kill", {"job_id": bg["id"]})
        self.assertTrue(killed.ok)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status = self.tools.process_manager.get(bg["id"]) or {}
            if status.get("status") in {"killed", "cancelled"}:
                break
            time.sleep(0.05)
        self.assertIn(
            (self.tools.process_manager.get(bg["id"]) or {}).get("status"),
            {"killed", "cancelled"},
        )


class AskUserQuestionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = ToolExecutor(_make_tools())

    def test_first_pass_emits_user_question_pending(self) -> None:
        events, result_json = self.executor.run_one_stream(
            "call_q",
            "ask_user_question",
            {"questions": [{"id": "m", "question": "选哪个？"}]},
        )
        self.assertEqual([e["type"] for e in events], ["user_question"])
        payload = json.loads(result_json)
        self.assertTrue(payload["user_question"])
        self.assertEqual(payload["data"]["questions"][0]["id"], "m")

    def test_invalid_questions_rejected_before_event(self) -> None:
        events, result_json = self.executor.run_one_stream(
            "call_bad", "ask_user_question", {"questions": [{"question": "缺 id"}]}
        )
        payload = json.loads(result_json)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["data"]["error_code"], "INVALID_ARGS")

    def test_resume_with_answers_returns_them_as_result(self) -> None:
        events, result_json = self.executor.run_one_stream(
            "call_q2",
            "ask_user_question",
            {
                "questions": [{"id": "a", "question": "Q1"}, {"id": "b", "question": "Q2"}],
                "_answers": {"a": "yes", "b": ["x", "y"]},
            },
        )
        payload = json.loads(result_json)
        self.assertTrue(payload["ok"])
        self.assertIn("- a: \"yes\"", payload["output"])
        self.assertEqual(payload["data"]["answers"]["b"], ["x", "y"])


class SubagentFamilyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tools = _make_tools()

    def test_background_spawn_returns_durable_id(self) -> None:
        result = self.tools.run(
            "subagent",
            {"description": "Research helpers", "prompt": "调研测试任务", "run_in_background": True},
        )
        self.assertTrue(result.ok)
        run_id = result.data["run"]["id"]
        self.assertTrue(run_id.startswith("subagent_"))
        listing = self.tools.run("list_agents", {})
        self.assertIn(run_id, listing.output)

    def test_send_message_unknown_id(self) -> None:
        with self.assertRaises(ToolExecutionError):
            self.tools.run("send_message", {"subagent_id": "subagent_none", "message": "hi"})

    def test_interrupt_agent_unknown_id(self) -> None:
        with self.assertRaises(ToolExecutionError):
            self.tools.run("interrupt_agent", {"agent_id": "subagent_none"})


class GoalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tools = _make_tools()

    def test_create_get_update_state_machine(self) -> None:
        created = self.tools.run("create_goal", {"objective": "完成对齐"})
        goal_id = created.data["goal_id"]
        goal = self.tools.run("get_goal", {}).data["goal"]
        self.assertEqual(goal["phase"], "active")
        self.assertEqual(goal["revision"], 1)

        paused = self.tools.run(
            "update_goal", {"goal_id": goal_id, "revision": 1, "action": "pause"}
        )
        self.assertEqual(paused.data["phase"], "paused")
        resumed = self.tools.run(
            "update_goal", {"goal_id": goal_id, "revision": 2, "action": "resume"}
        )
        self.assertEqual(resumed.data["phase"], "active")

    def test_revision_mismatch_rejected(self) -> None:
        created = self.tools.run("create_goal", {"objective": "x"})
        with self.assertRaises(ToolExecutionError) as ctx:
            self.tools.run(
                "update_goal",
                {"goal_id": created.data["goal_id"], "revision": 99, "action": "complete"},
            )
        self.assertIn("revision mismatch", str(ctx.exception))

    def test_blocked_requires_reason(self) -> None:
        created = self.tools.run("create_goal", {"objective": "x"})
        with self.assertRaises(ToolExecutionError) as ctx:
            self.tools.run(
                "update_goal",
                {"goal_id": created.data["goal_id"], "revision": 1, "action": "blocked"},
            )
        self.assertIn("blocked_reason is required", str(ctx.exception))

    def test_second_goal_rejected_while_active(self) -> None:
        self.tools.run("create_goal", {"objective": "one"})
        with self.assertRaises(ToolExecutionError) as ctx:
            self.tools.run("create_goal", {"objective": "two"})
        self.assertIn("already active", str(ctx.exception))


class ScheduleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tools = _make_tools()

    def test_exactly_one_selector(self) -> None:
        with self.assertRaises(ToolExecutionError) as ctx:
            self.tools.run(
                "schedule_create",
                {"prompt": "p", "after_seconds": 10, "every_seconds": 300},
            )
        self.assertIn("exactly one selector", str(ctx.exception))

    def test_every_seconds_minimum(self) -> None:
        with self.assertRaises(ToolExecutionError) as ctx:
            self.tools.run("schedule_create", {"prompt": "p", "every_seconds": 30})
        self.assertIn("at least 300", str(ctx.exception))

    def test_due_delivery_and_list_delete(self) -> None:
        created = self.tools.run("schedule_create", {"prompt": "提醒 A", "after_seconds": 1})
        schedule_id = created.data["id"]
        self.assertEqual(self.tools.pop_due_schedule_prompts(), [], "未到期不送达")
        time.sleep(1.1)
        self.assertEqual(self.tools.pop_due_schedule_prompts(), ["提醒 A"], "一次性提醒到期送达")
        listing = self.tools.run("schedule_list", {})
        self.assertIn("(no active reminders)", listing.output, "送达后一次性提醒移除")

        repeat = self.tools.run("schedule_create", {"prompt": "循环 B", "every_seconds": 300})
        deleted = self.tools.run("schedule_delete", {"id": repeat.data["id"]})
        self.assertTrue(deleted.data["deleted"])
        missing = self.tools.run("schedule_delete", {"id": "rem_xxx"})
        self.assertFalse(missing.data["deleted"])


class LspTest(unittest.TestCase):
    def test_diagnostics_runs_on_project(self) -> None:
        tools = _make_tools()
        result = tools.run("lsp", {"operation": "diagnostics"})
        self.assertTrue(result.ok)
        self.assertIn("summary", result.data)

    def test_go_to_definition_requires_path_and_symbol(self) -> None:
        tools = _make_tools()
        with self.assertRaises(ToolExecutionError):
            tools.run("lsp", {"operation": "goToDefinition"})

    def test_unknown_operation(self) -> None:
        tools = _make_tools()
        with self.assertRaises(ToolExecutionError):
            tools.run("lsp", {"operation": "hover"})


class SessionSearchTest(unittest.TestCase):
    def test_without_store_mounted(self) -> None:
        tools = _make_tools()
        with self.assertRaises(ToolExecutionError) as ctx:
            tools.run("session_search", {"query": "nova"})
        self.assertIn("no session store", str(ctx.exception))

    def test_search_finds_prior_session_message(self) -> None:
        tools = _make_tools()

        class FakeMessage:
            def __init__(self, content: str) -> None:
                self.content = content
                self.role = type("R", (), {"value": "user"})()
                self.created_at = "2026-09-03T00:00:00+00:00"

        class FakeSession:
            def __init__(self, sid: str) -> None:
                self.id = sid

        class FakeStore:
            def list_chat_sessions(self, workspace=None):
                return [FakeSession("s1"), FakeSession("s2")]

            def list_chat_messages(self, session_id):
                if session_id == "s1":
                    return [FakeMessage(" Nova 对齐 测试Nova 内容 ")]
                return [FakeMessage("无关内容")]

        tools.session_store = FakeStore()
        result = tools.run("session_search", {"query": "nova"})
        self.assertIn("s1", result.output)
        self.assertNotIn("s2", result.output)
        self.assertEqual(result.data["results"][0]["matches"], 2)


if __name__ == "__main__":
    unittest.main()
