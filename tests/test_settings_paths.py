from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nova.config.settings import load_settings


class SettingsPathTest(unittest.TestCase):
    def test_nova_home_is_user_state_not_source_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            nova_home = root / "nova-home"
            workspace = root / "customer-workspace"
            workspace.mkdir()

            with patch.dict(
                "os.environ",
                {
                    "NOVA_HOME": str(nova_home),
                    "NOVA_PROJECT_ROOT": str(workspace),
                },
                clear=False,
            ):
                settings = load_settings()

            self.assertEqual(settings.nova_home, nova_home.resolve())
            self.assertEqual(settings.initial_workspace_root, workspace.resolve())
            self.assertEqual(settings.state_dir, nova_home.resolve() / "sessions")
            self.assertEqual(settings.runtime_config_file, nova_home.resolve() / "config" / "runtime-config.json")
            self.assertEqual(settings.runtime_secret_file, nova_home.resolve() / "secrets" / "runtime-secrets.json")
            self.assertEqual(settings.tool_hooks_file, nova_home.resolve() / "hooks" / "hooks.json")
            self.assertNotEqual(settings.state_dir, settings.source_root / ".nova")


if __name__ == "__main__":
    unittest.main()
