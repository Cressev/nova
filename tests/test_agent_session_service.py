from __future__ import annotations

import unittest

from nova_gateway.models import ChatMessage, ChatRole
from nova_gateway.sessions.agent_session import AgentSessionService
from nova_gateway import main as app_module


class AgentSessionServiceTest(unittest.TestCase):
    def test_active_turn_state_is_session_scoped(self) -> None:
        service = AgentSessionService()

        self.assertFalse(service.is_active("chat_a"))
        service.mark_active("chat_a")

        self.assertTrue(service.is_active("chat_a"))
        self.assertFalse(service.is_active("chat_b"))

        service.mark_idle("chat_a")
        self.assertFalse(service.is_active("chat_a"))

    def test_queued_messages_are_drained_once_in_order(self) -> None:
        service = AgentSessionService()
        first = ChatMessage(session_id="chat_a", role=ChatRole.USER, content="第一条")
        second = ChatMessage(session_id="chat_a", role=ChatRole.USER, content="第二条")

        service.enqueue_message("chat_a", first)
        service.enqueue_message("chat_a", second)

        drained = service.drain_queued_messages("chat_a")

        self.assertEqual([message.content for message in drained], ["第一条", "第二条"])
        self.assertEqual(service.drain_queued_messages("chat_a"), [])

    def test_pending_approvals_are_owned_by_session_service(self) -> None:
        service = AgentSessionService()

        item = service.create_pending_approval(
            session_id="chat_a",
            turn_id="turn_a",
            call_id="tool_a",
            tool="shell_command",
            arguments={"command": "pwd"},
            permission="shell",
            reason="需要审批",
        )

        self.assertEqual(item.id, "tool_a")
        self.assertEqual([pending.id for pending in service.list_pending_approvals()], ["tool_a"])
        self.assertEqual(service.approve_pending_approval("tool_a").status, "approved")
        self.assertEqual(service.list_pending_approvals(), [])

    def test_main_uses_agent_session_service_for_pending_approvals(self) -> None:
        self.assertIs(app_module.pending_approvals, app_module.agent_sessions.pending_approvals)


if __name__ == "__main__":
    unittest.main()
