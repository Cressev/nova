from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from nova.app import main as app_module
from nova.app.main import app
from nova.subagents import SubAgentManager


class SubAgentRunnerSourceTest(unittest.TestCase):
    def test_main_subagent_runner_has_timeout_fallback(self) -> None:
        source = Path(app_module.__file__).read_text(encoding="utf-8")
        self.assertIn("asyncio.wait_for(collect()", source)
        self.assertIn("TimeoutError", source)
        self.assertIn("子 Agent 使用本地兜底", source)


class SubAgentApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_spawn_status_wait_and_close_subagent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            manager = SubAgentManager(project_root=root, default_runner=self._quick_runner)
            old_manager = app_module.subagent_manager
            old_root = app_module.workspace_manager.current_root
            app_module.subagent_manager = manager
            app_module.workspace_manager.current_root = root
            self.addCleanup(lambda: setattr(app_module, "subagent_manager", old_manager))
            self.addCleanup(lambda: setattr(app_module.workspace_manager, "current_root", old_root))

            created = self.client.post("/api/subagents", json={"prompt": "检查当前 diff", "name": "reviewer"})

            self.assertEqual(created.status_code, 201)
            agent_id = created.json()["id"]
            self.assertEqual(created.json()["status"], "running")

            status = self.client.get(f"/api/subagents/{agent_id}")
            self.assertEqual(status.status_code, 200)
            self.assertEqual(status.json()["name"], "reviewer")
            self.assertIn(status.json()["status"], {"running", "completed"})

            waited = self.client.post(f"/api/subagents/{agent_id}/wait", json={"timeout_ms": 2000})
            self.assertEqual(waited.status_code, 200)
            self.assertEqual(waited.json()["status"], "completed")
            self.assertIn("Scope:", waited.json()["result"])
            self.assertTrue(waited.json()["events"])

            listed = self.client.get("/api/subagents")
            self.assertEqual(listed.status_code, 200)
            self.assertTrue(any(item["id"] == agent_id for item in listed.json()["items"]))

            closed = self.client.delete(f"/api/subagents/{agent_id}")
            self.assertEqual(closed.status_code, 200)
            self.assertEqual(closed.json()["status"], "closed")

    def test_close_running_subagent_requests_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            def slow_runner(run):
                while not run.cancel_requested:
                    time.sleep(0.02)
                return "cancelled cooperatively"

            manager = SubAgentManager(project_root=root, default_runner=slow_runner)
            old_manager = app_module.subagent_manager
            old_root = app_module.workspace_manager.current_root
            app_module.subagent_manager = manager
            app_module.workspace_manager.current_root = root
            self.addCleanup(lambda: setattr(app_module, "subagent_manager", old_manager))
            self.addCleanup(lambda: setattr(app_module.workspace_manager, "current_root", old_root))

            created = self.client.post("/api/subagents", json={"prompt": "长任务", "name": "slow"})
            agent_id = created.json()["id"]

            closed = self.client.delete(f"/api/subagents/{agent_id}")

            self.assertEqual(closed.status_code, 200)
            self.assertEqual(closed.json()["status"], "cancelled")
            self.assertTrue(closed.json()["cancel_requested"])

    def _quick_runner(self, run) -> str:
        run.add_event("progress", "读取任务", "子 Agent 开始执行")
        return "Scope: 检查当前 diff\nResult: 已完成最小子任务。"


if __name__ == "__main__":
    unittest.main()
