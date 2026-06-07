from __future__ import annotations

import importlib
import unittest


class PackageLayoutTest(unittest.TestCase):
    def test_day1_backend_packages_expose_core_runtime_types(self) -> None:
        expected = {
            "nova_gateway.runtime.agent": "CodexLikeAgentRuntime",
            "nova_gateway.runtime.demo": "DemoAgentRuntime",
            "nova_gateway.sessions.store": "TaskStore",
            "nova_gateway.tools.workspace": "WorkspaceTools",
            "nova_gateway.tools.executor": "ToolExecutor",
            "nova_gateway.tools.hooks": "ToolHookRunner",
            "nova_gateway.approvals.store": "PendingApprovalStore",
            "nova_gateway.processes.manager": "ProcessManager",
            "nova_gateway.memory.project": "ProjectMemory",
            "nova_gateway.workspace.manager": "WorkspaceManager",
            "nova_gateway.providers.bigmodel": "BigModelProvider",
            "nova_gateway.config.settings": "Settings",
            "nova_gateway.observability.trace": "TraceRecorder",
        }

        for module_name, exported_name in expected.items():
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                self.assertTrue(hasattr(module, exported_name))
                self.assertIn(
                    "/" + module_name.rsplit(".", 1)[0].replace(".", "/") + "/",
                    module.__file__.replace("\\", "/"),
                )

    def test_legacy_flat_imports_still_work_during_refactor(self) -> None:
        legacy_expected = {
            "nova_gateway.agent_runtime": "CodexLikeAgentRuntime",
            "nova_gateway.runtime": "DemoAgentRuntime",
            "nova_gateway.store": "TaskStore",
            "nova_gateway.agent_tools": "WorkspaceTools",
            "nova_gateway.tool_executor": "ToolExecutor",
            "nova_gateway.tool_hooks": "ToolHookRunner",
            "nova_gateway.pending_approvals": "PendingApprovalStore",
            "nova_gateway.process_manager": "ProcessManager",
            "nova_gateway.memory": "ProjectMemory",
            "nova_gateway.workspace": "WorkspaceManager",
            "nova_gateway.provider": "BigModelProvider",
            "nova_gateway.settings": "Settings",
            "nova_gateway.trace": "TraceRecorder",
        }

        for module_name, exported_name in legacy_expected.items():
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                self.assertTrue(hasattr(module, exported_name))


if __name__ == "__main__":
    unittest.main()
