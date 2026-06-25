from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nova.config.settings import Settings
from nova.core import NovaCore
from nova.app.main import app, core as app_core


class NovaCoreTest(unittest.TestCase):
    def test_from_settings_builds_shared_runtime_services(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            workspace = root / "workspace"
            workspace.mkdir()
            settings = self._settings(root, workspace)

            core = NovaCore.from_settings(settings)

            self.assertIs(core.settings, settings)
            self.assertEqual(core.store.state_dir, settings.state_dir)
            self.assertEqual(core.workspace_manager.current_root, workspace)
            self.assertEqual(core.provider.base_url, "https://example.test/v4")
            self.assertEqual(core.provider.model, "glm-test")
            self.assertIs(core.agent_sessions.pending_approvals, core.agent_sessions.pending_approvals)
            self.assertTrue(settings.state_dir.exists())

    def test_fastapi_app_exposes_same_core_instance(self) -> None:
        self.assertIs(app.state.core, app_core)

    def _settings(self, root: Path, workspace: Path) -> Settings:
        nova_home = root / "nova-home"
        return Settings(
            source_root=root,
            nova_home=nova_home,
            project_root=root,
            state_dir=nova_home / "sessions",
            static_dir=root / "static",
            initial_workspace_root=workspace,
            allowed_workspace_roots=[root],
            global_agent_file=nova_home / "AGENTS.md",
            provider_base_url="https://example.test/v4",
            provider_model="glm-test",
            permission_mode="workspace_write",
            sandbox_mode="workspace_write",
            approval_policy="never",
            network_access=False,
            max_tool_rounds=4,
            context_window_tokens=128000,
            runtime_config_file=nova_home / "config" / "runtime-config.json",
            runtime_secret_file=nova_home / "secrets" / "runtime-secrets.json",
            tool_hooks_file=nova_home / "hooks" / "hooks.json",
        )


if __name__ == "__main__":
    unittest.main()
