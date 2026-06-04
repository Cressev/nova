from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from nova_gateway import main as app_module
from nova_gateway.main import app


class ApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_health(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_favicon(self) -> None:
        response = self.client.get("/favicon.ico")
        self.assertEqual(response.status_code, 204)

    def test_workspace_status(self) -> None:
        response = self.client.get("/api/workspace/status")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("project_root", payload)
        self.assertIn("git", payload)
        self.assertIn("modes", payload)
        self.assertIn("permissions", payload)
        self.assertIn("commands", payload)
        self.assertTrue(any(mode["id"] == "local" for mode in payload["modes"]))
        self.assertIn("permission_mode", payload["permissions"])

    def test_runtime_config_tools_and_memory(self) -> None:
        config = self.client.get("/api/runtime/config")
        self.assertEqual(config.status_code, 200)
        self.assertIn("permission_mode", config.json())

        tools = self.client.get("/api/tools")
        self.assertEqual(tools.status_code, 200)
        names = {item["name"] for item in tools.json()["items"]}
        self.assertIn("read_file", names)
        self.assertIn("apply_patch", names)

        memory = self.client.get("/api/memory/status")
        self.assertEqual(memory.status_code, 200)
        self.assertTrue(memory.json()["enabled"])
        development_state = memory.json()["development_state"]
        self.assertTrue(all(not item["injected"] for item in development_state))

    def test_workspace_list_and_select(self) -> None:
        workspaces = self.client.get("/api/workspaces")
        self.assertEqual(workspaces.status_code, 200)
        current = workspaces.json()["current_root"]

        selected = self.client.post("/api/workspace/select", json={"path": current})
        self.assertEqual(selected.status_code, 200)
        self.assertEqual(selected.json()["current_root"], current)

    def test_create_task(self) -> None:
        response = self.client.post("/api/tasks", json={"prompt": "测试任务"})
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertTrue(payload["id"].startswith("task_"))
        self.assertEqual(payload["prompt"], "测试任务")

        detail = self.client.get(f"/api/tasks/{payload['id']}")
        self.assertEqual(detail.status_code, 200)

    def test_chat_session_and_missing_provider_key(self) -> None:
        session_response = self.client.post(
            "/api/chat/sessions",
            json={"title": "测试对话"},
        )
        self.assertEqual(session_response.status_code, 201)
        session = session_response.json()

        with patch.dict("os.environ", {"BIGMODEL_API_KEY": ""}, clear=False):
            message_response = self.client.post(
                f"/api/chat/sessions/{session['id']}/messages",
                json={"content": "你好"},
            )
        self.assertEqual(message_response.status_code, 200)
        message = message_response.json()
        self.assertIn(message["role"], {"assistant", "error"})

        messages = self.client.get(f"/api/chat/sessions/{session['id']}/messages")
        self.assertEqual(messages.status_code, 200)
        self.assertGreaterEqual(len(messages.json()), 2)

    def test_stream_missing_provider_key(self) -> None:
        session_response = self.client.post(
            "/api/chat/sessions",
            json={"title": "流式测试"},
        )
        session = session_response.json()

        with patch.dict("os.environ", {"BIGMODEL_API_KEY": ""}, clear=False):
            with self.client.stream(
                "POST",
                f"/api/chat/sessions/{session['id']}/stream",
                json={"content": "你好"},
            ) as response:
                self.assertEqual(response.status_code, 200)
                body = "".join(response.iter_text())
        self.assertIn("user_message", body)
        self.assertIn("未配置 BIGMODEL_API_KEY", body)

    def test_stream_success_events(self) -> None:
        async def fake_agent_stream(messages):
            # 用假 Agent 流验证网关事件顺序，不依赖真实模型和外网。
            yield {
                "type": "tool_start",
                "tool": "read_file",
                "title": "读取 README.md",
                "arguments": {"path": "README.md"},
            }
            yield {
                "type": "tool_done",
                "tool": "read_file",
                "ok": True,
                "title": "读取 README.md",
                "output": "Nova",
                "data": {"path": "README.md"},
            }
            yield {"type": "assistant_delta", "delta": "你"}
            yield {"type": "assistant_delta", "delta": "好"}
            yield {"type": "assistant_done_content", "content": "你好"}

        session_response = self.client.post(
            "/api/chat/sessions",
            json={"title": "流式成功测试"},
        )
        session = session_response.json()

        class FakeRuntime:
            stream = staticmethod(fake_agent_stream)

        with patch.object(app_module, "_agent_runtime", lambda: FakeRuntime()):
            with self.client.stream(
                "POST",
                f"/api/chat/sessions/{session['id']}/stream",
                json={"content": "你好"},
            ) as response:
                self.assertEqual(response.status_code, 200)
                body = "".join(response.iter_text())

        self.assertIn("user_message", body)
        self.assertIn("tool_start", body)
        self.assertIn("tool_done", body)
        self.assertIn("assistant_delta", body)
        self.assertIn("assistant_done", body)
        self.assertIn("你好", body)


if __name__ == "__main__":
    unittest.main()
