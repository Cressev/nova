from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from nova.context_budget import build_context_budget_plan
from nova.models import ChatEvent, ChatMessage, ChatRole, ChatSession, new_id
from nova.providers.bigmodel import ProviderError
from nova.runtime import SessionRunDependencies, SessionRunner
from nova.sessions import AgentSessionService, SessionStore


class SessionRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.store = SessionStore(self.root / "state")
        self.sessions = AgentSessionService()
        self.chat = ChatSession(id="chat_test", title="测试会话", workspace=str(self.root))
        self.store.create_chat_session(self.chat)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_successful_turn_persists_messages_and_runtime_events(self) -> None:
        runner = self._runner(_FakeRuntime([{"type": "assistant_delta", "delta": "完成"}]))

        events = asyncio.run(_collect(runner.run_message(self.chat.id, "开始")))

        self.assertEqual(events[0]["type"], "user_message")
        self.assertTrue(any(event["type"] == "runtime_event" and event["event"]["event_type"] == "turn.started" for event in events))
        self.assertTrue(any(event["type"] == "runtime_event" and event["event"]["event_type"] == "context.budgeted" for event in events))
        done = next(event for event in events if event["type"] == "assistant_done")
        self.assertEqual(done["message"]["content"], "完成")

        messages = self.store.list_chat_messages(self.chat.id)
        self.assertEqual([message.role for message in messages], [ChatRole.USER, ChatRole.ASSISTANT])
        self.assertEqual(messages[-1].content, "完成")
        runtime = self.sessions.runtime_state(self.chat.id)
        self.assertEqual(runtime["current_turn"]["status"], "completed")
        self.assertEqual(runtime["final_answer"]["content"], "完成")

    def test_provider_error_turn_persists_error_message(self) -> None:
        runner = self._runner(_FailingRuntime(ProviderError("模型失败")))

        events = asyncio.run(_collect(runner.run_message(self.chat.id, "开始")))

        error = next(event for event in events if event["type"] == "error")
        self.assertEqual(error["message"]["role"], "error")
        self.assertIn("模型失败", error["message"]["content"])
        failed = next(event for event in events if event["type"] == "runtime_event" and event["event"]["event_type"] == "turn.failed")
        self.assertEqual(failed["event"]["status"], "failed")
        self.assertEqual(self.store.list_chat_messages(self.chat.id)[-1].role, ChatRole.ERROR)

    def test_queued_messages_are_drained_after_current_turn(self) -> None:
        queued = ChatMessage(session_id=self.chat.id, role=ChatRole.USER, content="第二条")
        self.store.add_chat_message(queued)
        self.sessions.enqueue_message(self.chat.id, queued)
        runner = self._runner(
            _FakeRuntime(
                [
                    {"type": "assistant_delta", "delta": "第一轮"},
                    {"type": "assistant_done_content", "content": "第一轮"},
                ],
                [
                    {"type": "assistant_delta", "delta": "第二轮"},
                    {"type": "assistant_done_content", "content": "第二轮"},
                ],
            )
        )

        events = asyncio.run(_collect(runner.run_message(self.chat.id, "第一条")))

        self.assertTrue(any(event["type"] == "queued_message" and event["message"]["content"] == "第二条" for event in events))
        assistant_messages = [
            message.content
            for message in self.store.list_chat_messages(self.chat.id)
            if message.role == ChatRole.ASSISTANT
        ]
        self.assertEqual(assistant_messages, ["第一轮", "第二轮"])
        self.assertEqual(self.sessions.runtime_state(self.chat.id)["queued_messages"], [])

    def test_cancelled_first_turn_keeps_queued_messages(self) -> None:
        queued = ChatMessage(session_id=self.chat.id, role=ChatRole.USER, content="不要误跑")
        self.sessions.enqueue_message(self.chat.id, queued)

        class CancellingRuntime:
            async def stream(inner_self, _messages: list[ChatMessage]):
                yield {"type": "assistant_delta", "delta": "开始"}
                self.sessions.request_cancel(self.chat.id)
                yield {"type": "assistant_delta", "delta": " 不应输出"}

        runner = self._runner(CancellingRuntime())

        events = asyncio.run(_collect(runner.run_message(self.chat.id, "第一条")))

        self.assertFalse(any(event["type"] == "queued_message" for event in events))
        self.assertEqual(
            [message.id for message in self.sessions.queued_messages(self.chat.id)],
            [queued.id],
        )

    def test_cancelled_queued_turn_keeps_later_queued_messages(self) -> None:
        first = ChatMessage(session_id=self.chat.id, role=ChatRole.USER, content="第二条")
        second = ChatMessage(session_id=self.chat.id, role=ChatRole.USER, content="第三条")
        self.sessions.enqueue_message(self.chat.id, first)
        self.sessions.enqueue_message(self.chat.id, second)

        class CancelsDuringSecondTurn:
            def __init__(inner_self) -> None:
                inner_self.calls = 0

            async def stream(inner_self, _messages: list[ChatMessage]):
                inner_self.calls += 1
                if inner_self.calls == 1:
                    yield {"type": "assistant_delta", "delta": "第一轮"}
                    yield {"type": "assistant_done_content", "content": "第一轮"}
                    return
                yield {"type": "assistant_delta", "delta": "第二轮"}
                self.sessions.request_cancel(self.chat.id)
                yield {"type": "assistant_delta", "delta": " 不应输出"}

        runner = self._runner(CancelsDuringSecondTurn())

        events = asyncio.run(_collect(runner.run_message(self.chat.id, "第一条")))

        queued_events = [event for event in events if event["type"] == "queued_message"]
        self.assertEqual([event["message"]["id"] for event in queued_events], [first.id])
        self.assertEqual(
            [message.id for message in self.sessions.queued_messages(self.chat.id)],
            [second.id],
        )
        persisted_users = [
            message.content
            for message in self.store.list_chat_messages(self.chat.id)
            if message.role == ChatRole.USER
        ]
        self.assertEqual(persisted_users, ["第一条", "第二条"])

    def test_approved_tool_call_resumes_through_runner(self) -> None:
        self.sessions.create_pending_approval(
            session_id=self.chat.id,
            turn_id="turn_a",
            call_id="tool_shell",
            tool="shell_command",
            arguments={"command": "pwd", "workdir": "."},
            permission="shell",
            reason="需要审批",
            risk="medium",
            checkpoint_event_id="evt_permission",
            checkpoint_data={"risk": "medium"},
        )
        runner = self._runner(_FakeRuntime([]))

        result = asyncio.run(runner.approve_tool_call("tool_shell"))

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["status"], "approved")
        self.assertTrue(any(event["type"] == "tool_done" for event in result["events"]))
        self.assertEqual(self.sessions.list_pending_approvals(), [])
        stored = self.store.list_chat_events(self.chat.id)
        self.assertTrue(any(event.event_type == "tool.completed" for event in stored))
        runtime = self.sessions.runtime_state(self.chat.id)
        self.assertEqual(runtime["tool_calls"][0]["call_id"], "tool_shell")
        self.assertEqual(runtime["tool_calls"][0]["status"], "completed")

    def _runner(self, runtime: object) -> SessionRunner:
        return SessionRunner(
            SessionRunDependencies(
                store=self.store,
                agent_sessions=self.sessions,
                runtime_factory=lambda: runtime,  # type: ignore[arg-type]
                id_factory=new_id,
                persist_event=self._persist_event,
                runtime_event_from_agent_event=_runtime_event_from_agent_event,
                build_context_budget_plan=build_context_budget_plan,
                context_window_tokens=128000,
                project_root_provider=lambda: self.root,
                global_agent_file_provider=lambda: None,
                tool_orchestrator_factory=lambda: _FakeToolOrchestrator(),
                event_builder_for_existing_turn=_event_builder_for_existing_turn,
                denied_tool_message_builder=_denied_tool_message,
            )
        )

    def _persist_event(self, event: dict) -> None:
        self.store.upsert_chat_event(
            ChatEvent(
                id=str(event.get("id") or new_id("evt")),
                session_id=str(event["session_id"]),
                type=(
                    "tool"
                    if event.get("category") == "tool"
                    else "turn" if event.get("category") == "turn" else "status"
                ),
                event_type=str(event.get("event_type") or ""),
                phase=str(event.get("phase") or ""),
                turn_id=str(event.get("turn_id") or ""),
                sequence=int(event.get("sequence") or 0),
                status=str(event.get("status") or "ok"),
                title=str(event.get("title") or ""),
                message=str(event.get("message") or ""),
                data=event.get("data") if isinstance(event.get("data"), dict) else {},
            )
        )


class _FakeRuntime:
    def __init__(self, *turns: list[dict]) -> None:
        self.turns = list(turns)

    async def stream(self, _messages: list[ChatMessage]):
        events = self.turns.pop(0) if self.turns else []
        for event in events:
            yield event


class _FailingRuntime:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def stream(self, _messages: list[ChatMessage]):
        raise self.error
        yield {}


class _FakeToolOrchestrator:
    async def resume_approved_tool(
        self,
        call_id: str,
        name: str,
        arguments: dict,
    ) -> tuple[list[dict], str]:
        return (
            [
                {
                    "type": "tool_start",
                    "call_id": call_id,
                    "tool": name,
                    "arguments": arguments,
                },
                {
                    "type": "tool_done",
                    "call_id": call_id,
                    "tool": name,
                    "ok": True,
                    "title": "pwd",
                    "output": "/tmp",
                    "data": {},
                },
            ],
            '{"ok": true}',
        )


def _runtime_event_from_agent_event(event: dict, build_event) -> dict | None:
    if event.get("type") == "tool_start":
        return build_event(
            "tool.started",
            category="tool",
            phase="started",
            status="running",
            title=str(event.get("tool") or "工具执行中"),
            tool=str(event.get("tool") or "tool"),
            call_id=str(event.get("call_id") or "tool"),
            arguments=event.get("arguments") if isinstance(event.get("arguments"), dict) else {},
        )
    if event.get("type") == "tool_done":
        return build_event(
            "tool.completed",
            category="tool",
            phase="completed",
            title=str(event.get("title") or "工具完成"),
            tool=str(event.get("tool") or "tool"),
            call_id=str(event.get("call_id") or "tool"),
            output=str(event.get("output") or ""),
            data=event.get("data") if isinstance(event.get("data"), dict) else {},
        )
    if event.get("type") == "agent_status":
        return build_event(
            "agent.status",
            category="status",
            phase="update",
            title=str(event.get("status") or "运行中"),
        )
    return None


def _event_builder_for_existing_turn(session_id: str, turn_id: str):
    sequence = 0

    def build_event(
        event_type: str,
        *,
        category: str,
        phase: str,
        title: str,
        message: str | None = None,
        status: str = "ok",
        tool: str | None = None,
        call_id: str | None = None,
        arguments: dict | None = None,
        output: str | None = None,
        data: dict | None = None,
        persist: bool = False,
    ) -> dict:
        nonlocal sequence
        sequence += 1
        return {
            "id": call_id or new_id("evt"),
            "session_id": session_id,
            "turn_id": turn_id,
            "sequence": sequence,
            "event_type": event_type,
            "category": category,
            "phase": phase,
            "status": status,
            "title": title,
            "message": message or title,
            "tool": tool,
            "call_id": call_id,
            "arguments": arguments or {},
            "output": output,
            "data": data or {},
        }

    return build_event


def _denied_tool_message(*, tool: str, arguments: dict, reason: str) -> str:
    return f"已拒绝 {tool}: {reason}"


async def _collect(iterator) -> list[dict]:
    return [event async for event in iterator]


if __name__ == "__main__":
    unittest.main()
