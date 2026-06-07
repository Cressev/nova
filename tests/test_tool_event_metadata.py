from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nova_gateway.tools.executor import ToolExecutor
from nova_gateway.tools.workspace import WorkspaceTools


class ToolEventMetadataTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        (self.root / "README.md").write_text("Nova 工具测试\n", encoding="utf-8")
        self.executor = ToolExecutor(WorkspaceTools(self.root))

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_tool_events_carry_spec_duration_and_diff_preview(self) -> None:
        events, _result_json = self.executor.run_one_stream(
            "tool_write_preview",
            "write_file",
            {"path": "README.md", "content": "Nova 新内容\n"},
        )

        start = next(event for event in events if event["type"] == "tool_start")
        done = next(event for event in events if event["type"] == "tool_done")

        self.assertEqual(start["data"]["spec"]["permission"], "write")
        self.assertEqual(start["data"]["spec"]["risk"], "high")
        self.assertEqual(start["data"]["spec"]["schema"]["path"], "文件")
        self.assertGreaterEqual(done["data"]["duration_ms"], 0)
        self.assertEqual(done["data"]["diff"]["files"], ["README.md"])
        self.assertEqual(done["data"]["diff"]["additions"], 1)
        self.assertEqual(done["data"]["diff"]["deletions"], 1)
        self.assertIn("+Nova 新内容", done["data"]["diff"]["preview"])

    def test_failed_tool_event_includes_failure_reason_and_retryable_flag(self) -> None:
        events, _result_json = self.executor.run_one_stream(
            "tool_failed_read",
            "read_file",
            {"path": "missing.md"},
        )

        done = next(event for event in events if event["type"] == "tool_done")

        self.assertFalse(done["ok"])
        self.assertIn("文件不存在", done["data"]["failure_reason"])
        self.assertTrue(done["data"]["retryable"])
        self.assertEqual(done["data"]["spec"]["permission"], "read")


if __name__ == "__main__":
    unittest.main()
