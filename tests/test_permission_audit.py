"""权限审批三事件（asked/decided/policy）落库与回放——对齐 dsh approval 事件审计语义。"""

import unittest

from nova.runtime.orchestrator import RunOrchestrator


class _FakeAgentSessions:
    def __init__(self) -> None:
        self.recorded: list[dict] = []

    def record_runtime_event(self, event: dict) -> None:
        self.recorded.append(event)

    def create_pending_approval(self, **kwargs):
        return kwargs


class _FakeStore:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def upsert_chat_event(self, event) -> dict:
        self.events.append(event)
        return event


class PermissionAuditEventTest(unittest.TestCase):
    def _orchestrator(self):
        store = _FakeStore()
        sessions = _FakeAgentSessions()
        orch = RunOrchestrator(
            session_id="s1",
            turn_id="t1",
            agent_sessions=sessions,
            id_factory=lambda prefix: f"{prefix}_id",
            persist_event=lambda event: store.events.append(event),
        )
        return orch, store

    def test_register_permission_request_emits_asked(self) -> None:
        """register_permission_request 落 permission.asked（dsh approval/asked 对齐）。"""
        orch, store = self._orchestrator()
        orch.register_permission_request(
            {
                "type": "permission_request",
                "call_id": "call_1",
                "tool": "bash",
                "permission": "shell",
                "message": "执行前需确认",
                "arguments": {"command": "pwd"},
                "data": {"risk": "medium"},
            }
        )
        asked = [e for e in store.events if e["event_type"] == "permission.asked"]
        self.assertEqual(len(asked), 1)
        self.assertEqual(asked[0]["tool"], "bash")
        # 独立 id：不得与 permission.requested(call_id) 冲突
        self.assertEqual(asked[0]["id"], "asked-call_1")
        self.assertEqual(asked[0]["data"]["call_id"], "call_1")

    def test_emit_permission_policy_is_log_only(self) -> None:
        """emit_permission_policy 产出 policy 切换事件（log-only 审计）。"""
        orch, store = self._orchestrator()
        orch.emit_permission_policy(policy="never", reason="headless")
        policy = [e for e in store.events if e["event_type"] == "permission.policy"]
        self.assertEqual(len(policy), 1)
        self.assertEqual(policy[0]["data"]["policy"], "never")


if __name__ == "__main__":
    unittest.main()


class SandboxConfineTest(unittest.TestCase):
    """bash 内核级沙箱 confine（对齐 dsh bash-sandbox fail-closed 语义）。"""

    def test_seatbelt_profile_read_only(self) -> None:
        from nova.tools.sandbox import SandboxPolicy, seatbelt_profile_args

        args = seatbelt_profile_args(SandboxPolicy("read_only", "/w/root"))
        self.assertEqual(args[0], "-p")
        # read_only：无 subpath 白名单
        self.assertNotIn("subpath", args[1])

    def test_seatbelt_profile_workspace_write(self) -> None:
        from nova.tools.sandbox import SandboxPolicy, seatbelt_profile_args

        args = seatbelt_profile_args(SandboxPolicy("workspace_write", "/w/root"))
        self.assertIn('(allow file-write* (subpath "/w/root"))', args[1])
        self.assertIn("(deny file-write*)", args[1])

    def test_confine_unknown_platform_fail_closed(self) -> None:
        from nova.tools.sandbox import SandboxPolicy, SandboxUnavailableError, confine

        with self.assertRaises(SandboxUnavailableError):
            confine("echo hi", SandboxPolicy("workspace_write", "/w"), platform="windows")

    def test_probe_runner_reports_platform(self) -> None:
        from nova.tools.sandbox import probe_runner

        info = probe_runner()
        self.assertIn(info["platform"], {"darwin", "linux", "windows"})
        self.assertIn("available", info)


if __name__ == "__main__":
    unittest.main()
