from __future__ import annotations

import unittest

from nova.runtime.orchestrator import RunOrchestrator
from nova.sessions.agent_session import AgentSessionService


class RunOrchestratorTest(unittest.TestCase):
    def test_events_are_persisted_and_update_runtime_state(self) -> None:
        persisted: list[dict] = []
        sessions = AgentSessionService()
        orchestrator = RunOrchestrator(
            session_id="chat_a",
            turn_id="turn_a",
            agent_sessions=sessions,
            persist_event=persisted.append,
            id_factory=lambda prefix: f"{prefix}_1",
        )

        started = orchestrator.start_turn(user_message_id="msg_user", message="开始")
        tool = orchestrator.event(
            "tool.started",
            category="tool",
            phase="started",
            status="running",
            title="读取文件",
            tool="read_file",
            call_id="tool_a",
            arguments={"path": "README.md"},
        )
        completed = orchestrator.complete_turn(message_id="msg_assistant", content="完成")

        state = sessions.runtime_state("chat_a")
        self.assertEqual([event["sequence"] for event in [started, tool, completed]], [1, 2, 3])
        self.assertEqual(len(persisted), 3)
        self.assertEqual(state["current_turn"]["status"], "completed")
        self.assertEqual(state["tool_calls"][0]["call_id"], "tool_a")
        self.assertEqual(state["final_answer"]["message_id"], "msg_assistant")

    def test_cancel_and_permission_request_are_centralized(self) -> None:
        sessions = AgentSessionService()
        orchestrator = RunOrchestrator(
            session_id="chat_a",
            turn_id="turn_a",
            agent_sessions=sessions,
            persist_event=lambda _event: None,
            id_factory=lambda prefix: f"{prefix}_1",
        )
        orchestrator.start_turn(user_message_id="msg_user", message="开始")

        runtime_event = orchestrator.event(
            "permission.requested",
            category="permission",
            phase="requested",
            status="pending",
            title="需要审批",
            call_id="tool_shell",
        )
        orchestrator.register_permission_request(
            {
                "type": "permission_request",
                "call_id": "tool_shell",
                "tool": "shell_command",
                "arguments": {"command": "pwd"},
                "permission": "shell",
                "message": "需要确认",
            },
            runtime_event=runtime_event,
        )
        cancelled = orchestrator.cancel_turn()

        self.assertEqual(sessions.list_pending_approvals(session_id="chat_a")[0].tool, "shell_command")
        self.assertTrue(sessions.runtime_state("chat_a")["cancel_requested"])
        self.assertEqual(cancelled["event_type"], "turn.cancelled")


if __name__ == "__main__":
    unittest.main()
