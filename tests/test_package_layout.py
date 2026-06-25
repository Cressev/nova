from __future__ import annotations

import importlib
import unittest


class PackageLayoutTest(unittest.TestCase):
    def test_backend_packages_expose_long_term_runtime_types(self) -> None:
        expected = {
            "nova.app.main": "app",
            "nova.api": "__doc__",
            "nova.api.routes": "app",
            "nova.api.system": "router",
            "nova.api.runtime": "router",
            "nova.api.chat": "router",
            "nova.api.workspace": "router",
            "nova.api.tools": "router",
            "nova.api.memory": "router",
            "nova.api.permissions": "router",
            "nova.api.processes": "router",
            "nova.api.subagents": "router",
            "nova.core": "NovaCore",
            "nova.core.container": "NovaCore",
            "nova.runtime.agent": "CodexLikeAgentRuntime",
            "nova.runtime.orchestrator": "RunOrchestrator",
            "nova.sessions.store": "SessionStore",
            "nova.tools.workspace": "WorkspaceTools",
            "nova.tools.executor": "ToolExecutor",
            "nova.tools.hooks": "ToolHookRunner",
            "nova.permissions.store": "PendingApprovalStore",
            "nova.processes.manager": "ProcessManager",
            "nova.memory.project": "ProjectMemory",
            "nova.workspace.manager": "WorkspaceManager",
            "nova.providers.bigmodel": "BigModelProvider",
            "nova.config.settings": "Settings",
            "nova.observability.trace": "TraceRecorder",
            "nova.subagents.manager": "SubAgentManager",
            "nova.skills.manager": "SkillManager",
            "nova.tui": "__doc__",
        }

        for module_name, exported_name in expected.items():
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                self.assertTrue(hasattr(module, exported_name))
                expected_path = "/nova/api/" if module_name == "nova.app.main" else "/" + module_name.rsplit(".", 1)[0].replace(".", "/") + "/"
                self.assertIn(
                    expected_path,
                    module.__file__.replace("\\", "/"),
                )


if __name__ == "__main__":
    unittest.main()
