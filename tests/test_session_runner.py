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
            )
        )

    def _persist_event(self, event: dict) -> None:
        self.store.upsert_chat_event(
            ChatEvent(
                id=str(event.get("id") or new_id("evt")),
                session_id=str(event["session_id"]),
                type="turn" if event.get("category") == "turn" else "status",
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


def _runtime_event_from_agent_event(event: dict, build_event) -> dict | None:
    if event.get("type") == "agent_status":
        return build_event(
            "agent.status",
            category="status",
            phase="update",
            title=str(event.get("status") or "运行中"),
        )
    return None


async def _collect(iterator) -> list[dict]:
    return [event async for event in iterator]


if __name__ == "__main__":
    unittest.main()
