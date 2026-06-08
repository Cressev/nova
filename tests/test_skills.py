from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from nova_gateway import main as app_module
from nova_gateway.main import app
from nova_gateway.models import ChatMessage, ChatRole
from nova_gateway.provider import BigModelProvider
from nova_gateway.agent_runtime import CodexLikeAgentRuntime


class SkillsApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_skills_status_discovers_global_and_project_skill_md(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            global_root = root / "global-skills"
            project_root = root / "project"
            self._write_skill(global_root / "global-review", "global-review", "全局 Review 技能", "全局正文")
            self._write_skill(project_root / ".nova" / "skills" / "project-plan", "project-plan", "项目计划技能", "项目正文")
            old_root = self._switch_workspace(project_root)

            with patch.dict("os.environ", {"NOVA_GLOBAL_SKILLS_DIRS": str(global_root)}, clear=False):
                response = self.client.get("/api/skills/status")

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["project_root"], str(project_root.resolve()))
            by_name = {item["name"]: item for item in payload["skills"]}
            self.assertEqual(by_name["global-review"]["scope"], "global")
            self.assertEqual(by_name["project-plan"]["scope"], "project")
            self.assertEqual(by_name["project-plan"]["trigger"], "$project-plan")
            self.assertIn("项目正文", by_name["project-plan"]["preview"])
            self.assertIn(str(global_root.resolve()), payload["roots"]["global"])
            self.assertIn(str((project_root / ".nova" / "skills").resolve()), payload["roots"]["project"])
            app_module.workspace_manager.current_root = old_root

    def test_skill_detail_reads_full_skill_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            self._write_skill(project_root / ".nova" / "skills" / "project-plan", "project-plan", "项目计划技能", "完整正文")
            old_root = self._switch_workspace(project_root)

            response = self.client.get("/api/skills/project/project-plan")

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["name"], "project-plan")
            self.assertEqual(payload["scope"], "project")
            self.assertIn("完整正文", payload["content"])
            app_module.workspace_manager.current_root = old_root

    def _switch_workspace(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        old_root = app_module.workspace_manager.current_root
        app_module.workspace_manager.current_root = root.resolve()
        return old_root

    def _write_skill(self, skill_dir: Path, name: str, description: str, body: str) -> None:
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\n{body}\n",
            encoding="utf-8",
        )


class SkillsRuntimeTest(unittest.TestCase):
    def test_dollar_skill_command_loads_skill_card(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            skill_dir = root / ".nova" / "skills" / "project-plan"
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: project-plan\ndescription: 项目计划技能\n---\n\n# Project Plan\n\n按计划拆任务。\n",
                encoding="utf-8",
            )
            runtime = CodexLikeAgentRuntime(provider=BigModelProvider(), project_root=root)
            messages = [ChatMessage(session_id="s", role=ChatRole.USER, content="$project-plan 今天先做什么")]

            events = asyncio.run(self._collect(runtime, messages))
            text = "".join(event.get("delta", "") for event in events if event["type"] == "assistant_delta")

            self.assertIn("已加载技能：project-plan", text)
            self.assertIn("按计划拆任务", text)
            self.assertTrue(any(event["type"] == "agent_status" and "技能" in event["status"] for event in events))

    def test_system_prompt_lists_available_skills_without_full_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            skill_dir = root / ".nova" / "skills" / "project-plan"
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: project-plan\ndescription: 项目计划技能\n---\n\n# Project Plan\n\n" + ("很长正文\n" * 20),
                encoding="utf-8",
            )
            runtime = CodexLikeAgentRuntime(provider=BigModelProvider(), project_root=root)

            prompt = runtime._system_prompt()

            self.assertIn("$project-plan", prompt)
            self.assertIn("项目计划技能", prompt)
            self.assertNotIn("很长正文", prompt)

    async def _collect(self, runtime: CodexLikeAgentRuntime, messages: list[ChatMessage]) -> list[dict]:
        return [event async for event in runtime.stream(messages)]
