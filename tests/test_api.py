from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
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
        self.assertIn("context_window_tokens", config.json())

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

    def test_runtime_config_update_takes_effect_without_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            old_path = app_module.settings.runtime_config_file
            old_permission = app_module.settings.permission_mode
            old_sandbox = app_module.settings.sandbox_mode
            old_approval = app_module.settings.approval_policy
            old_network = app_module.settings.network_access
            old_rounds = app_module.settings.max_tool_rounds
            old_context = app_module.settings.context_window_tokens
            old_model = app_module.provider.model
            old_base_url = app_module.provider.base_url
            object.__setattr__(app_module.settings, "runtime_config_file", Path(tmpdir) / "runtime-config.json")
            self.addCleanup(lambda: object.__setattr__(app_module.settings, "runtime_config_file", old_path))
            self.addCleanup(lambda: object.__setattr__(app_module.settings, "permission_mode", old_permission))
            self.addCleanup(lambda: object.__setattr__(app_module.settings, "sandbox_mode", old_sandbox))
            self.addCleanup(lambda: object.__setattr__(app_module.settings, "approval_policy", old_approval))
            self.addCleanup(lambda: object.__setattr__(app_module.settings, "network_access", old_network))
            self.addCleanup(lambda: object.__setattr__(app_module.settings, "max_tool_rounds", old_rounds))
            self.addCleanup(lambda: object.__setattr__(app_module.settings, "context_window_tokens", old_context))
            self.addCleanup(lambda: setattr(app_module.provider, "model", old_model))
            self.addCleanup(lambda: setattr(app_module.provider, "base_url", old_base_url))

            response = self.client.patch(
                "/api/runtime/config",
                json={
                    "provider_model": "glm-4.7",
                    "provider_base_url": "https://open.bigmodel.cn/api/paas/v4",
                    "context_window_tokens": 256000,
                    "permission_mode": "ask",
                    "sandbox_mode": "workspace_write",
                    "approval_policy": "on_request",
                    "network_access": True,
                    "max_tool_rounds": 8,
                },
            )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertFalse(payload["restart_required"])
            self.assertEqual(payload["permission_mode"], "ask")
            self.assertEqual(payload["approval_policy"], "on_request")
            self.assertTrue(payload["network_access"])
            self.assertEqual(payload["pending_config"]["permission_mode"], "ask")
            self.assertEqual(payload["pending_config"]["sandbox_mode"], "workspace_write")
            self.assertEqual(payload["pending_config"]["approval_policy"], "on_request")
            self.assertEqual(payload["pending_config"]["context_window_tokens"], 256000)
            self.assertEqual(app_module.settings.permission_mode, "ask")
            self.assertEqual(app_module.settings.max_tool_rounds, 8)
            self.assertEqual(app_module.provider.model, "glm-4.7")

    def test_runtime_config_exposes_permission_choices_and_hook_status(self) -> None:
        response = self.client.get("/api/runtime/config")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("permission_modes", payload)
        self.assertIn("sandbox_modes", payload)
        self.assertIn("approval_policies", payload)
        self.assertIn("hooks_enabled", payload)
        self.assertIn("danger_full_access", payload["sandbox_modes"])
        self.assertIn("on_request", payload["approval_policies"])

    def test_runtime_api_key_update_takes_effect_without_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            secret_file = Path(tmpdir) / "runtime-secrets.json"
            old_path = app_module.settings.runtime_secret_file
            object.__setattr__(app_module.settings, "runtime_secret_file", secret_file)
            app_module.provider.clear_runtime_api_key(persist=False)
            self.addCleanup(lambda: object.__setattr__(app_module.settings, "runtime_secret_file", old_path))
            self.addCleanup(lambda: app_module.provider.clear_runtime_api_key(persist=False))

            with patch.dict("os.environ", {"BIGMODEL_API_KEY": ""}, clear=False):
                before = self.client.get("/api/provider")
                self.assertFalse(before.json()["configured"])

                response = self.client.patch(
                    "/api/runtime/secrets",
                    json={"bigmodel_api_key": "test-runtime-key"},
                )

                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertTrue(payload["provider_configured"])
                self.assertTrue(payload["api_key_set"])
                self.assertNotIn("test-runtime-key", response.text)
                self.assertTrue(secret_file.exists())

                after = self.client.get("/api/provider")
                self.assertTrue(after.json()["configured"])

    def test_runtime_statusline_estimates_session_tokens(self) -> None:
        session_response = self.client.post(
            "/api/chat/sessions",
            json={"title": "状态线测试"},
        )
        session = session_response.json()
        app_module.store.add_chat_message(
            app_module.ChatMessage(
                session_id=session["id"],
                role=app_module.ChatRole.USER,
                content="请总结 README",
            )
        )
        app_module.store.add_chat_message(
            app_module.ChatMessage(
                session_id=session["id"],
                role=app_module.ChatRole.ASSISTANT,
                content="README 是项目说明。",
            )
        )

        response = self.client.get("/api/runtime/statusline", params={"session_id": session["id"]})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["session_id"], session["id"])
        self.assertEqual(payload["model"], app_module.provider.model)
        self.assertGreater(payload["used_tokens"], 0)
        self.assertGreater(payload["context_remaining_tokens"], 0)
        self.assertTrue(payload["estimated"])

    def test_workspace_list_and_select(self) -> None:
        workspaces = self.client.get("/api/workspaces")
        self.assertEqual(workspaces.status_code, 200)
        current = workspaces.json()["current_root"]

        selected = self.client.post("/api/workspace/select", json={"path": current})
        self.assertEqual(selected.status_code, 200)
        self.assertEqual(selected.json()["current_root"], current)

        queried = self.client.get("/api/workspaces", params={"q": current[: max(1, len(current) - 2)]})
        self.assertEqual(queried.status_code, 200)
        self.assertIn(current, queried.json()["candidates"])
        self.assertIn("query_status", queried.json())

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
        self.assertEqual(session["workspace"], str(app_module.workspace_manager.current_root))

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

    def test_chat_sessions_are_project_scoped_and_deletable(self) -> None:
        session_response = self.client.post(
            "/api/chat/sessions",
            json={"title": "可删除对话"},
        )
        self.assertEqual(session_response.status_code, 201)
        session = session_response.json()

        sessions = self.client.get("/api/chat/sessions")
        self.assertEqual(sessions.status_code, 200)
        self.assertTrue(any(item["id"] == session["id"] for item in sessions.json()))

        delete_response = self.client.delete(f"/api/chat/sessions/{session['id']}")
        self.assertEqual(delete_response.status_code, 204)

        missing_messages = self.client.get(f"/api/chat/sessions/{session['id']}/messages")
        self.assertEqual(missing_messages.status_code, 404)

    def test_chat_sessions_list_includes_other_workspaces(self) -> None:
        other = app_module.ChatSession(
            id=app_module.new_id("chat"),
            title="其他项目线程",
            workspace="/mnt/d/documents/Work/other-project",
        )
        app_module.store.create_chat_session(other)
        self.addCleanup(lambda: app_module.store.delete_chat_session(other.id))

        sessions = self.client.get("/api/chat/sessions")

        self.assertEqual(sessions.status_code, 200)
        self.assertTrue(any(item["id"] == other.id for item in sessions.json()))

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
                "call_id": "tool_test_readme",
                "tool": "read_file",
                "title": "读取 README.md",
                "arguments": {"path": "README.md"},
            }
            yield {
                "type": "tool_done",
                "call_id": "tool_test_readme",
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

        timeline = self.client.get(f"/api/chat/sessions/{session['id']}/timeline")
        self.assertEqual(timeline.status_code, 200)
        items = timeline.json()["items"]
        tool_events = [
            item["item"]
            for item in items
            if item["kind"] == "event" and item["item"]["type"] == "tool"
        ]
        self.assertEqual(len(tool_events), 1)
        self.assertEqual(tool_events[0]["tool"], "read_file")
        self.assertEqual(tool_events[0]["arguments"], {"path": "README.md"})
        self.assertEqual(tool_events[0]["output"], "Nova")
        self.assertEqual(tool_events[0]["status"], "ok")

    def test_stream_emits_runtime_event_backbone(self) -> None:
        async def fake_agent_stream(messages):
            yield {"type": "agent_status", "status": "模型决策中"}
            yield {
                "type": "tool_start",
                "call_id": "tool_test_status",
                "tool": "git_status",
                "title": "读取 Git 状态",
                "arguments": {},
            }
            yield {
                "type": "tool_done",
                "call_id": "tool_test_status",
                "tool": "git_status",
                "ok": True,
                "title": "Git 状态",
                "output": "clean",
                "data": {},
            }
            yield {"type": "assistant_delta", "delta": "完成"}
            yield {"type": "assistant_done_content", "content": "完成"}

        session_response = self.client.post(
            "/api/chat/sessions",
            json={"title": "运行时事件骨架"},
        )
        session = session_response.json()

        class FakeRuntime:
            stream = staticmethod(fake_agent_stream)

        with patch.object(app_module, "_agent_runtime", lambda: FakeRuntime()):
            with self.client.stream(
                "POST",
                f"/api/chat/sessions/{session['id']}/stream",
                json={"content": "查看 git 状态"},
            ) as response:
                self.assertEqual(response.status_code, 200)
                lines = [
                    line
                    for line in response.iter_lines()
                    if line.strip()
                ]

        runtime_events = [
            app_module.json.loads(line)["event"]
            for line in lines
            if app_module.json.loads(line).get("type") == "runtime_event"
        ]
        event_types = [event["event_type"] for event in runtime_events]
        self.assertIn("turn.started", event_types)
        self.assertIn("agent.status", event_types)
        self.assertIn("tool.started", event_types)
        self.assertIn("tool.completed", event_types)
        self.assertIn("turn.completed", event_types)

        turn_ids = {event["turn_id"] for event in runtime_events}
        self.assertEqual(len(turn_ids), 1)
        sequences = [event["sequence"] for event in runtime_events]
        self.assertEqual(sequences, sorted(sequences))
        self.assertEqual(sequences, list(range(1, len(sequences) + 1)))

        timeline = self.client.get(f"/api/chat/sessions/{session['id']}/timeline")
        self.assertEqual(timeline.status_code, 200)
        stored_events = [
            item["item"]
            for item in timeline.json()["items"]
            if item["kind"] == "event"
        ]
        stored_event_types = [event.get("event_type") for event in stored_events]
        self.assertIn("turn.started", stored_event_types)
        self.assertIn("tool.completed", stored_event_types)
        self.assertIn("turn.completed", stored_event_types)
        self.assertTrue(all(event.get("turn_id") for event in stored_events))
        self.assertEqual(
            [event.get("sequence") for event in stored_events],
            sorted(event.get("sequence") for event in stored_events),
        )

    def test_stream_maps_permission_request_runtime_event(self) -> None:
        async def fake_agent_stream(messages):
            yield {
                "type": "permission_request",
                "call_id": "tool_needs_shell",
                "tool": "shell_command",
                "permission": "shell",
                "title": "需要审批：shell_command",
                "message": "执行命令前需要用户确认。",
                "arguments": {"command": "pwd"},
                "data": {"reason": "ask 模式需要审批"},
            }
            yield {"type": "assistant_delta", "delta": "需要审批"}
            yield {"type": "assistant_done_content", "content": "需要审批"}

        session_response = self.client.post(
            "/api/chat/sessions",
            json={"title": "权限事件测试"},
        )
        session = session_response.json()

        class FakeRuntime:
            stream = staticmethod(fake_agent_stream)

        with patch.object(app_module, "_agent_runtime", lambda: FakeRuntime()):
            with self.client.stream(
                "POST",
                f"/api/chat/sessions/{session['id']}/stream",
                json={"content": "执行 pwd"},
            ) as response:
                self.assertEqual(response.status_code, 200)
                lines = [line for line in response.iter_lines() if line.strip()]

        runtime_events = [
            app_module.json.loads(line)["event"]
            for line in lines
            if app_module.json.loads(line).get("type") == "runtime_event"
        ]
        permission_events = [
            event for event in runtime_events if event["event_type"] == "permission.requested"
        ]
        self.assertEqual(len(permission_events), 1)
        self.assertEqual(permission_events[0]["tool"], "shell_command")
        self.assertEqual(permission_events[0]["arguments"], {"command": "pwd"})

        timeline = self.client.get(f"/api/chat/sessions/{session['id']}/timeline")
        stored = [
            item["item"]
            for item in timeline.json()["items"]
            if item["kind"] == "event" and item["item"].get("event_type") == "permission.requested"
        ]
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["type"], "permission")

    def test_stream_processes_queued_messages_after_current_turn(self) -> None:
        seen_prompts: list[str] = []

        async def fake_agent_stream(messages):
            last_user = next(message.content for message in reversed(messages) if message.role == app_module.ChatRole.USER)
            seen_prompts.append(last_user)
            yield {"type": "assistant_delta", "delta": f"回复：{last_user}"}
            yield {"type": "assistant_done_content", "content": f"回复：{last_user}"}

        session_response = self.client.post(
            "/api/chat/sessions",
            json={"title": "队列续跑"},
        )
        session = session_response.json()
        queued_message = app_module.ChatMessage(
            session_id=session["id"],
            role=app_module.ChatRole.USER,
            content="第二条",
        )
        app_module.store.add_chat_message(queued_message)
        app_module.agent_sessions.enqueue_message(session["id"], queued_message)
        self.addCleanup(lambda: app_module.agent_sessions.drain_queued_messages(session["id"]))

        class FakeRuntime:
            stream = staticmethod(fake_agent_stream)

        with patch.object(app_module, "_agent_runtime", lambda: FakeRuntime()):
            with self.client.stream(
                "POST",
                f"/api/chat/sessions/{session['id']}/stream",
                json={"content": "第一条"},
            ) as response:
                self.assertEqual(response.status_code, 200)
                lines = [line for line in response.iter_lines() if line.strip()]

        events = [app_module.json.loads(line) for line in lines]
        self.assertEqual(seen_prompts, ["第一条", "第二条"])
        self.assertEqual(
            [event["message"]["content"] for event in events if event["type"] == "assistant_done"],
            ["回复：第一条", "回复：第二条"],
        )
        queued_events = [event for event in events if event["type"] == "queued_message"]
        self.assertEqual(len(queued_events), 1)
        self.assertEqual(queued_events[0]["message"]["id"], queued_message.id)


if __name__ == "__main__":
    unittest.main()
