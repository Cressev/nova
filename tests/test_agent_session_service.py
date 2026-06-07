from __future__ import annotations

import unittest

from nova_gateway.models import ChatMessage, ChatRole
from nova_gateway.sessions.agent_session import AgentSessionService


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


if __name__ == "__main__":
    unittest.main()
