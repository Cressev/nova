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


if __name__ == "__main__":
    unittest.main()
