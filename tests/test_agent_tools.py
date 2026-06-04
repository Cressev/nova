from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nova_gateway.agent_tools import ToolExecutionError, WorkspaceTools


class WorkspaceToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        (self.root / "README.md").write_text("Nova 工具测试\n", encoding="utf-8")
        self.tools = WorkspaceTools(self.root)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_read_file_inside_workspace(self) -> None:
        result = self.tools.run("read_file", {"path": "README.md"})
        self.assertTrue(result.ok)
        self.assertIn("Nova 工具测试", result.output)

    def test_reject_path_outside_workspace(self) -> None:
        with self.assertRaises(ToolExecutionError):
            self.tools.run("read_file", {"path": "../README.md"})

    def test_reject_protected_directory(self) -> None:
        with self.assertRaises(ToolExecutionError):
            self.tools.run("list_files", {"path": ".git"})

    def test_shell_allowlist_and_blocklist(self) -> None:
        ok = self.tools.run("shell_command", {"command": "pwd"})
        self.assertTrue(ok.ok)
        with self.assertRaises(ToolExecutionError):
            self.tools.run("shell_command", {"command": "rm -rf .nova"})


if __name__ == "__main__":
    unittest.main()
