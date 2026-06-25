from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from nova.app import main as app_module
from nova.app.main import app
from nova.permissions.store import PendingApprovalStore
from nova.processes.manager import ProcessManager


class RuntimeControlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_pending_approval_can_be_approved_and_denied(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            old_permission = app_module.settings.permission_mode
            old_process_manager = app_module.process_manager
            old_pending = app_module.pending_approvals
            old_root = app_module.workspace_manager.current_root
            app_module.agent_sessions.pending_approvals = PendingApprovalStore()
            app_module.pending_approvals = app_module.agent_sessions.pending_approvals
            app_module.process_manager = ProcessManager()
            app_module.workspace_manager.current_root = Path(tmpdir).resolve()
            object.__setattr__(app_module.settings, "permission_mode", "ask")
            self.addCleanup(lambda: object.__setattr__(app_module.settings, "permission_mode", old_permission))
            self.addCleanup(lambda: setattr(app_module.agent_sessions, "pending_approvals", old_pending))
            self.addCleanup(lambda: setattr(app_module, "pending_approvals", old_pending))
            self.addCleanup(lambda: setattr(app_module, "process_manager", old_process_manager))
            self.addCleanup(lambda: setattr(app_module.workspace_manager, "current_root", old_root))
            self.addCleanup(lambda: app_module.process_manager.kill_all())

            session = self.client.post("/api/chat/sessions", json={"title": "审批"}).json()
            with self.client.stream(
                "POST",
                f"/api/chat/sessions/{session['id']}/stream",
                json={"content": "你不会调用命令行工具吗"},
            ) as response:
                self.assertEqual(response.status_code, 200)
                body = "".join(response.iter_text())

            self.assertIn("permission.requested", body)
            pending = self.client.get("/api/approvals/pending").json()["items"]
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["tool"], "shell_command")

            approved = self.client.post(
                f"/api/approvals/{pending[0]['id']}/approve",
                json={},
            )
            self.assertEqual(approved.status_code, 200)
            self.assertTrue(any(event["type"] == "tool_done" for event in approved.json()["events"]))
            self.assertEqual(self.client.get("/api/approvals/pending").json()["items"], [])

            app_module.agent_sessions.create_pending_approval(
                session_id=session["id"],
                turn_id="turn_test",
                call_id="tool_deny",
                tool="shell_command",
                arguments={"command": "pwd", "workdir": "."},
                permission="shell",
                reason="测试拒绝",
            )
            denied = self.client.post("/api/approvals/tool_deny/deny", json={"reason": "不允许"})
            self.assertEqual(denied.status_code, 200)
            self.assertEqual(denied.json()["status"], "denied")
            denied_payload = denied.json()
            self.assertIn("message", denied_payload)
            self.assertEqual(denied_payload["message"]["role"], "assistant")
            self.assertIn("不允许", denied_payload["message"]["content"])
            self.assertIn("替代", denied_payload["message"]["content"])

            messages = self.client.get(f"/api/chat/sessions/{session['id']}/messages").json()
            self.assertEqual(messages[-1]["id"], denied_payload["message"]["id"])
            self.assertEqual(messages[-1]["role"], "assistant")

            timeline = self.client.get(f"/api/chat/sessions/{session['id']}/timeline").json()["items"]
            self.assertTrue(
                any(
                    item["kind"] == "event" and item["item"].get("event_type") == "permission.denied"
                    for item in timeline
                )
            )

    def test_process_manager_streams_output_and_kills_background_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ProcessManager(chunk_size=8)
            self.addCleanup(manager.kill_all)

            events = list(
                manager.run_foreground(
                    "python3 -c 'import sys; print(\"out\"); print(\"err\", file=sys.stderr)'",
                    cwd=Path(tmpdir),
                    timeout_ms=3000,
                )
            )

            streams = [event for event in events if event["type"] == "tool_output"]
            self.assertTrue(any(event["stream"] == "stdout" and "out" in event["chunk"] for event in streams))
            self.assertTrue(any(event["stream"] == "stderr" and "err" in event["chunk"] for event in streams))
            self.assertTrue(any(event["type"] == "tool_done" and event["ok"] for event in events))

            job = manager.start_background("python3 -c 'import time; print(\"start\"); time.sleep(20)'", cwd=Path(tmpdir))
            self.assertEqual(job["status"], "running")
            time.sleep(0.3)
            jobs = manager.list_jobs()
            self.assertTrue(any(item["id"] == job["id"] for item in jobs))
            killed = manager.kill(job["id"])
            self.assertEqual(killed["status"], "killed")
            deadline = time.monotonic() + 1.5
            while time.monotonic() < deadline and manager.get(job["id"])["status"] == "killed":
                time.sleep(0.05)
            self.assertEqual(manager.get(job["id"])["status"], "killed")

    def test_process_manager_emits_foreground_output_before_process_exits(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ProcessManager(chunk_size=1024)
            self.addCleanup(manager.kill_all)
            events: list[dict] = []

            def consume_until_first_output() -> None:
                for event in manager.run_foreground(
                    "python3 -u -c 'import time; print(\"ready\", flush=True); time.sleep(20)'",
                    cwd=Path(tmpdir),
                    timeout_ms=60000,
                    call_id="tool_stream_early",
                ):
                    events.append(event)
                    if event["type"] == "tool_output":
                        break

            thread = threading.Thread(target=consume_until_first_output)
            thread.start()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not events:
                time.sleep(0.05)
            saw_output_before_cancel = any(
                event["type"] == "tool_output" and "ready" in event["chunk"]
                for event in events
            )
            try:
                manager.cancel_call("tool_stream_early")
            except KeyError:
                pass
            thread.join(timeout=5)

            self.assertTrue(saw_output_before_cancel)

    def test_process_manager_cancels_foreground_job_by_call_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ProcessManager(chunk_size=8)
            self.addCleanup(manager.kill_all)
            events: list[dict] = []

            def consume() -> None:
                events.extend(
                    manager.run_foreground(
                        "python3 -u -c 'import time; print(\"started\", flush=True); time.sleep(20)'",
                        cwd=Path(tmpdir),
                        timeout_ms=60000,
                        call_id="tool_cancel_me",
                    )
                )

            thread = threading.Thread(target=consume)
            thread.start()
            time.sleep(0.3)

            cancelled = manager.cancel_call("tool_cancel_me")

            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(cancelled["status"], "cancelled")
            done = next(event for event in events if event["type"] == "tool_done")
            self.assertFalse(done["ok"])
            self.assertEqual(done["data"]["status"], "cancelled")
            self.assertIn("命令已取消", done["output"])

    def test_tool_call_cancel_endpoint_terminates_running_foreground_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            old_process_manager = app_module.process_manager
            app_module.process_manager = ProcessManager(chunk_size=8)
            self.addCleanup(lambda: setattr(app_module, "process_manager", old_process_manager))
            self.addCleanup(lambda: app_module.process_manager.kill_all())
            events: list[dict] = []

            def consume() -> None:
                events.extend(
                    app_module.process_manager.run_foreground(
                        "python3 -u -c 'import time; print(\"started\", flush=True); time.sleep(20)'",
                        cwd=Path(tmpdir),
                        timeout_ms=60000,
                        call_id="tool_api_cancel",
                    )
                )

            thread = threading.Thread(target=consume)
            thread.start()
            time.sleep(0.3)

            response = self.client.post("/api/tool-calls/tool_api_cancel/cancel")

            thread.join(timeout=5)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "cancelled")
            self.assertFalse(thread.is_alive())
            done = next(event for event in events if event["type"] == "tool_done")
            self.assertEqual(done["data"]["status"], "cancelled")

    def test_chat_session_cancel_endpoint_marks_running_turn_cancel_requested(self) -> None:
        session = self.client.post("/api/chat/sessions", json={"title": "停止接口"}).json()
        app_module.agent_sessions.mark_active(session["id"])
        self.addCleanup(lambda: app_module.agent_sessions.mark_idle(session["id"]))

        cancelled = self.client.post(f"/api/chat/sessions/{session['id']}/cancel")

        self.assertEqual(cancelled.status_code, 200)
        self.assertTrue(cancelled.json()["cancel_requested"])
        runtime = self.client.get(f"/api/chat/sessions/{session['id']}/runtime-state").json()["runtime"]
        self.assertTrue(runtime["cancel_requested"])

    def test_chat_stream_stops_when_cancel_is_requested(self) -> None:
        session = self.client.post("/api/chat/sessions", json={"title": "强制停止"}).json()

        async def fake_agent_stream(_messages):
            yield {"type": "assistant_delta", "delta": "开始"}
            app_module.agent_sessions.request_cancel(session["id"])
            await app_module.asyncio.sleep(0)
            yield {"type": "assistant_delta", "delta": " 不应该出现"}

        class FakeRuntime:
            stream = staticmethod(fake_agent_stream)

        with patch.object(app_module, "_agent_runtime", lambda: FakeRuntime()):
            with self.client.stream(
                "POST",
                f"/api/chat/sessions/{session['id']}/stream",
                json={"content": "跑一个长任务"},
            ) as response:
                self.assertEqual(response.status_code, 200)
                lines = [line for line in response.iter_lines() if line.strip()]

        events = [app_module.json.loads(line) for line in lines]
        event_types = [
            event["event"]["event_type"]
            for event in events
            if event.get("type") == "runtime_event"
        ]
        self.assertIn("turn.cancelled", event_types)
        self.assertNotIn("turn.completed", event_types)
        self.assertFalse(any(event.get("delta") == " 不应该出现" for event in events))
        assistant_done = [event for event in events if event.get("type") == "assistant_done"]
        self.assertEqual(assistant_done, [])

    def test_chat_stream_queues_message_while_session_running(self) -> None:
        session = self.client.post("/api/chat/sessions", json={"title": "队列"}).json()
        app_module._active_session_turns.add(session["id"])
        self.addCleanup(lambda: app_module._active_session_turns.discard(session["id"]))

        queued = self.client.post(
            f"/api/chat/sessions/{session['id']}/stream",
            json={"content": "第二条"},
        )

        self.assertEqual(queued.status_code, 202)
        self.assertEqual(queued.json()["status"], "queued")
        messages = self.client.get(f"/api/chat/sessions/{session['id']}/messages").json()
        self.assertTrue(any(message["content"] == "第二条" for message in messages))

    def test_memory_api_reads_writes_and_marks_injected_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            old_root = app_module.workspace_manager.current_root
            app_module.workspace_manager.current_root = Path(tmpdir).resolve()
            self.addCleanup(lambda: setattr(app_module.workspace_manager, "current_root", old_root))

            status = self.client.get("/api/memory/status").json()
            self.assertTrue(status["enabled"])
            self.assertTrue(any(item["injected"] for item in status["injected_sources"]))

            written = self.client.post("/api/memory/files", json={"name": "user.md", "content": "用户偏好：中文"})
            self.assertEqual(written.status_code, 200)
            read = self.client.get("/api/memory/files/user.md")
            self.assertEqual(read.status_code, 200)
            self.assertIn("用户偏好", read.json()["content"])

    def test_memory_remember_creates_candidate_until_user_approves(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            old_root = app_module.workspace_manager.current_root
            root = Path(tmpdir).resolve()
            app_module.workspace_manager.current_root = root
            self.addCleanup(lambda: setattr(app_module.workspace_manager, "current_root", old_root))

            proposed = self.client.post("/api/memory/remember", json={"text": "用户偏好：候选确认后再写入"})

            self.assertEqual(proposed.status_code, 200)
            candidate = proposed.json()
            self.assertEqual(candidate["status"], "pending")
            self.assertIn("候选确认", candidate["content"])
            self.assertFalse((root / ".nova" / "memory" / "index.md").exists())

            status = self.client.get("/api/memory/status").json()
            self.assertEqual(len(status["memory_candidates"]), 1)
            self.assertEqual(status["memory_candidates"][0]["id"], candidate["id"])

            approved = self.client.post(f"/api/memory/candidates/{candidate['id']}/approve", json={})

            self.assertEqual(approved.status_code, 200)
            self.assertEqual(approved.json()["status"], "approved")
            self.assertIn(
                "候选确认后再写入",
                (root / ".nova" / "memory" / "index.md").read_text(encoding="utf-8"),
            )
            self.assertEqual(self.client.get("/api/memory/status").json()["memory_candidates"], [])

    def test_memory_candidate_can_be_edited_or_denied_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            old_root = app_module.workspace_manager.current_root
            root = Path(tmpdir).resolve()
            app_module.workspace_manager.current_root = root
            self.addCleanup(lambda: setattr(app_module.workspace_manager, "current_root", old_root))

            candidate = self.client.post("/api/memory/remember", json={"text": "原始事实"}).json()
            edited = self.client.post(
                f"/api/memory/candidates/{candidate['id']}/edit",
                json={"content": "编辑后的事实", "name": "project.md"},
            )

            self.assertEqual(edited.status_code, 200)
            self.assertEqual(edited.json()["status"], "approved")
            self.assertFalse((root / ".nova" / "memory" / "index.md").exists())
            self.assertIn("编辑后的事实", (root / ".nova" / "memory" / "project.md").read_text(encoding="utf-8"))

            denied_candidate = self.client.post("/api/memory/remember", json={"text": "不要写入"}).json()
            denied = self.client.post(
                f"/api/memory/candidates/{denied_candidate['id']}/deny",
                json={"reason": "用户拒绝"},
            )

            self.assertEqual(denied.status_code, 200)
            self.assertEqual(denied.json()["status"], "denied")
            self.assertNotIn("不要写入", (root / ".nova" / "memory" / "project.md").read_text(encoding="utf-8"))

    def test_memory_status_separates_global_and_project_persona_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            old_root = app_module.workspace_manager.current_root
            root = Path(tmpdir).resolve()
            legacy_persona = root / ".nova" / "memory" / "user.md"
            legacy_persona.parent.mkdir(parents=True, exist_ok=True)
            legacy_persona.write_text("旧位置人格不应继续作为记忆展示", encoding="utf-8")
            app_module.workspace_manager.current_root = root
            self.addCleanup(lambda: setattr(app_module.workspace_manager, "current_root", old_root))

            status = self.client.get("/api/memory/status").json()
            sources = status["injected_sources"]
            scopes = {item["scope"] for item in sources}
            names = {item["name"] for item in sources}

            self.assertIn("全局人格", scopes)
            self.assertIn("项目人格", scopes)
            self.assertIn("soul.md", names)
            self.assertIn("tools.md", names)
            self.assertIn("persona_files", status)
            self.assertIn("memory_files", status)
            persona_paths = [item["path"] for item in status["persona_files"]]
            memory_paths = [item["path"] for item in status["memory_files"]]
            self.assertTrue(any("/.nova/persona/" in path for path in persona_paths))
            self.assertTrue(all("/.nova/memory/" not in path for path in persona_paths))
            self.assertTrue(all(Path(path).name not in {"user.md", "soul.md", "tools.md"} for path in memory_paths))

            written = self.client.post(
                "/api/persona/files",
                json={"scope": "project", "name": "soul.md", "content": "人格：务实"},
            )
            self.assertEqual(written.status_code, 200)
            self.assertIn("/.nova/persona/", written.json()["path"])
            read = self.client.get("/api/persona/files/project/soul.md")
            self.assertEqual(read.status_code, 200)
            self.assertIn("务实", read.json()["content"])


if __name__ == "__main__":
    unittest.main()
