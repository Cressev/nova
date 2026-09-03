from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from nova.models import ChatEvent
from nova.observability.trace import TraceRecorder


class TraceRecorderTest(unittest.TestCase):
    def test_trace_file_uses_session_id_with_beijing_minute_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recorder = TraceRecorder(Path(tmp))
            first = ChatEvent(
                session_id="chat_abc123",
                type="turn",
                event_type="turn.started",
                title="开始",
                message="开始执行",
                created_at=datetime(2026, 6, 16, 9, 40, 20, tzinfo=timezone.utc),
            )
            second = ChatEvent(
                session_id="chat_abc123",
                type="turn",
                event_type="turn.completed",
                title="完成",
                message="执行完成",
                created_at=datetime(2026, 6, 16, 9, 41, 10, tzinfo=timezone.utc),
            )

            recorder.append(first)
            recorder.append(second)

            trace_files = sorted((Path(tmp) / "traces").glob("*.json"))
            self.assertEqual([path.name for path in trace_files], ["202606161740_chat_abc123.json"])
            payload = json.loads(trace_files[0].read_text(encoding="utf-8"))
            self.assertEqual([item["title"] for item in payload], ["开始", "完成"])
            self.assertEqual([item["title"] for item in recorder.read("chat_abc123")], ["开始", "完成"])

    def test_replay_groups_turn_tool_approval_and_process_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recorder = TraceRecorder(Path(tmp))
            common = {
                "session_id": "chat_trace",
                "turn_id": "turn_1",
                "created_at": datetime(2026, 6, 16, 9, 40, 20, tzinfo=timezone.utc),
            }
            recorder.append(
                ChatEvent(
                    **common,
                    type="turn",
                    event_type="turn.started",
                    phase="started",
                    title="开始",
                    data={"message_id": "msg_user"},
                )
            )
            recorder.append(
                ChatEvent(
                    **common,
                    id="tool_1",
                    type="permission",
                    event_type="permission.requested",
                    phase="requested",
                    status="pending",
                    title="需要审批",
                    tool="bash",
                    arguments={"command": "git push"},
                    data={"permission": "shell", "risk": "high"},
                )
            )
            recorder.append(
                ChatEvent(
                    **common,
                    id="tool_1",
                    type="tool",
                    event_type="tool.completed",
                    phase="completed",
                    title="执行完成",
                    tool="bash",
                    output="ok",
                    data={"job_id": "proc_1"},
                )
            )

            replay = recorder.replay(
                "chat_trace",
                messages=[
                    {"id": "msg_user", "role": "user", "content": "部署"},
                    {"id": "msg_assistant", "role": "assistant", "content": "完成"},
                ],
                processes=[{"id": "proc_1", "call_id": "tool_1", "status": "completed"}],
            )

            self.assertFalse(replay["records_hidden_thoughts"])
            self.assertEqual(replay["source"], "trace")
            self.assertEqual(replay["trace_file"], "202606161740_chat_trace.json")
            turn = replay["turns"][0]
            self.assertEqual(turn["turn_id"], "turn_1")
            self.assertEqual(turn["user_message"]["id"], "msg_user")
            self.assertEqual(turn["tool_calls"][0]["call_id"], "tool_1")
            self.assertEqual(turn["tool_calls"][0]["status"], "completed")
            self.assertEqual(turn["approvals"][0]["risk"], "high")
            self.assertEqual(turn["processes"][0]["id"], "proc_1")


if __name__ == "__main__":
    unittest.main()
