from __future__ import annotations

import unittest
from pathlib import Path


class NoWifiShortcutTest(unittest.TestCase):
    def test_runtime_agent_has_no_wifi_password_shell_shortcut(self) -> None:
        source = Path("src/nova/runtime/agent.py").read_text(encoding="utf-8")

        self.assertNotIn("_wifi_password_command", source)
        self.assertNotIn("netsh wlan show", source)
        self.assertNotIn("wifi密码", source.lower())


if __name__ == "__main__":
    unittest.main()
