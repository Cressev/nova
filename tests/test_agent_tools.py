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

    def test_list_files_prunes_protected_directories(self) -> None:
        (self.root / ".git").mkdir()
        (self.root / ".git" / "hidden.txt").write_text("hidden", encoding="utf-8")

        result = self.tools.run("list_files", {"path": ".", "limit": 20})

        self.assertIn("README.md", result.output)
        self.assertNotIn(".git/hidden.txt", result.output)

    def test_shell_allowlist_and_blocklist(self) -> None:
        ok = self.tools.run("shell_command", {"command": "pwd"})
        self.assertTrue(ok.ok)
        with self.assertRaises(ToolExecutionError):
            self.tools.run("shell_command", {"command": "rm -rf .nova"})

    def test_powershell_allowlist_only_allows_wlan_query(self) -> None:
        command = (
            "powershell.exe -NoProfile -Command "
            "\"$line=(netsh wlan show interfaces | Select-String '^\\s*SSID\\s*: ' | Select-Object -First 1); "
            "if (-not $line) { Write-Output '未检测到活动 WiFi 接口'; exit 1 }; "
            "$ssid=$line.ToString().Split(':',2)[1].Trim(); "
            "netsh wlan show profile name=\\\"$ssid\\\" key=clear\""
        )

        self.assertTrue(self.tools._is_allowed_shell_command(command))
        self.assertFalse(self.tools._is_allowed_shell_command("powershell.exe -NoProfile -Command \"Remove-Item x\""))

    def test_read_only_permission_blocks_write(self) -> None:
        tools = WorkspaceTools(self.root, permission_mode="read_only")
        with self.assertRaises(ToolExecutionError):
            tools.run("create_file", {"path": "new.txt", "content": "x"})

    def test_tool_specs_include_parallel_flag(self) -> None:
        specs = {item["name"]: item for item in self.tools.list_specs()}
        self.assertTrue(specs["read_file"]["supports_parallel"])
        self.assertFalse(specs["create_file"]["supports_parallel"])

    def test_tool_catalog_exposes_codex_like_metadata_and_core_tools(self) -> None:
        specs = {item["name"]: item for item in self.tools.list_specs()}

        for name in [
            "read_file",
            "read_many_files",
            "list_files",
            "glob_files",
            "search_text",
            "git_status",
            "git_diff",
            "shell_command",
            "replace_in_file",
            "create_file",
            "write_file",
            "edit_file",
            "multi_edit",
            "apply_patch",
            "todo_read",
            "todo_write",
            "web_fetch",
            "web_search",
            "memory_search",
            "memory_summarize",
            "memory_compact",
        ]:
            self.assertIn(name, specs)
            self.assertIn("category", specs[name])
            self.assertIn("risk", specs[name])
            self.assertIn("interrupt_behavior", specs[name])
            self.assertTrue(specs[name]["hooks_enabled"])

        self.assertEqual(specs["read_file"]["category"], "filesystem")
        self.assertEqual(specs["shell_command"]["permission"], "shell")
        self.assertEqual(specs["web_fetch"]["permission"], "network")
        self.assertEqual(specs["web_search"]["permission"], "network")
        self.assertEqual(specs["memory_summarize"]["category"], "memory")
        self.assertEqual(specs["memory_compact"]["permission"], "write")

    def test_memory_search_summarize_and_compact_tools(self) -> None:
        memory_dir = self.root / ".nova" / "memory"
        memory_dir.mkdir(parents=True)
        (memory_dir / "index.md").write_text("- 用户偏好：中文输出\n- 项目目标：对标 Codex\n", encoding="utf-8")
        (memory_dir / "session.md").write_text("# 会话\n正在实现 memory summarize。\n", encoding="utf-8")

        search = self.tools.run("memory_search", {"query": "Codex"})
        summarize = self.tools.run("memory_summarize", {})
        compact = self.tools.run("memory_compact", {"max_chars": 1200})

        self.assertIn("index.md:2", search.output)
        self.assertIn("项目目标：对标 Codex", search.output)
        self.assertIn("记忆摘要", summarize.output)
        self.assertIn("index.md", summarize.output)
        self.assertIn("session.md", summarize.output)
        self.assertIn("已压缩记忆", compact.output)
        self.assertIn("memory/project.md", compact.output)
        self.assertIn("记忆摘要", (memory_dir / "project.md").read_text(encoding="utf-8"))

    def test_memory_write_tool_creates_pending_candidate_instead_of_writing_file(self) -> None:
        result = self.tools.run("memory_write", {"name": "index.md", "content": "用户偏好：先确认"})

        self.assertTrue(result.ok)
        self.assertIn("待确认", result.output)
        self.assertFalse((self.root / ".nova" / "memory" / "index.md").exists())
        candidates = result.data["memory_candidates"]
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["status"], "pending")
        self.assertIn("先确认", candidates[0]["content"])

    def test_write_file_overwrites_and_returns_diff(self) -> None:
        result = self.tools.run("write_file", {"path": "README.md", "content": "Nova 新内容\n"})

        self.assertIn("-Nova 工具测试", result.output)
        self.assertIn("+Nova 新内容", result.output)
        self.assertEqual(result.data["diff"]["files"], ["README.md"])
        self.assertEqual(result.data["diff"]["additions"], 1)
        self.assertEqual(result.data["diff"]["deletions"], 1)
        self.assertIn("+Nova 新内容", result.data["diff"]["preview"])
        self.assertEqual((self.root / "README.md").read_text(encoding="utf-8"), "Nova 新内容\n")

    def test_edit_file_alias_and_multi_edit(self) -> None:
        self.tools.run("edit_file", {"path": "README.md", "old": "Nova", "new": "Nova Agent"})
        result = self.tools.run(
            "multi_edit",
            {
                "path": "README.md",
                "edits": [
                    {"old": "Agent", "new": "Gateway"},
                    {"old": "工具测试", "new": "工具链测试"},
                ],
            },
        )

        content = (self.root / "README.md").read_text(encoding="utf-8")
        self.assertIn("Nova Gateway 工具链测试", content)
        self.assertIn("+Nova Gateway 工具链测试", result.output)

    def test_todo_read_returns_current_todo_snapshot(self) -> None:
        self.tools.run("todo_write", {"items": [{"content": "补齐工具", "status": "in_progress"}]})

        result = self.tools.run("todo_read", {})

        self.assertIn("补齐工具", result.output)

    def test_danger_full_access_allows_workspace_outside_path(self) -> None:
        outside = Path(self.tmpdir.name).parent / f"{Path(self.tmpdir.name).name}-outside.txt"
        outside.write_text("外部文件\n", encoding="utf-8")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        tools = WorkspaceTools(self.root, sandbox_mode="danger_full_access")

        result = tools.run("read_file", {"path": str(outside)})

        self.assertTrue(result.ok)
        self.assertIn("外部文件", result.output)

    def test_network_tools_require_network_access(self) -> None:
        tools = WorkspaceTools(self.root, network_access=False)

        with self.assertRaises(ToolExecutionError):
            tools.run("web_fetch", {"url": "https://example.com"})
        with self.assertRaises(ToolExecutionError):
            tools.run("web_search", {"query": "Nova"})

    def test_codex_like_permission_modes_have_real_behavior(self) -> None:
        WorkspaceTools(self.root, permission_mode="accept_edits").run(
            "create_file",
            {"path": "accepted.txt", "content": "ok"},
        )

        with self.assertRaises(ToolExecutionError):
            WorkspaceTools(self.root, permission_mode="accept_edits").run("shell_command", {"command": "pwd"})

        with self.assertRaises(ToolExecutionError):
            WorkspaceTools(self.root, permission_mode="plan").run("create_file", {"path": "planned.txt", "content": "x"})

        with self.assertRaises(ToolExecutionError):
            WorkspaceTools(self.root, permission_mode="dont_ask").run("create_file", {"path": "denied.txt", "content": "x"})

        result = WorkspaceTools(self.root, permission_mode="bypass_permissions").run("shell_command", {"command": "pwd"})
        self.assertTrue(result.ok)


if __name__ == "__main__":
    unittest.main()
