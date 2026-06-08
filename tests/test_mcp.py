from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from nova_gateway import main as app_module
from nova_gateway.main import app


class McpApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_mcp_status_loads_project_servers_for_stdio_http_and_sse(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._switch_test_workspace(root)
            self._write_mcp_config(root)

            response = self.client.get("/api/mcp/status")

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["config_path"], str(root / ".nova" / "mcp.json"))
            servers = {item["name"]: item for item in payload["servers"]}
            self.assertEqual(set(servers), {"demo", "docs-http", "events-sse"})
            self.assertEqual(servers["demo"]["transport"], "stdio")
            self.assertEqual(servers["docs-http"]["transport"], "http")
            self.assertEqual(servers["events-sse"]["transport"], "sse")
            self.assertEqual(servers["demo"]["status"], "connected")
            self.assertEqual(servers["docs-http"]["status"], "configured")
            self.assertEqual(servers["events-sse"]["status"], "configured")
            self.assertIn("mcp__demo__echo", {tool["name"] for tool in payload["tools"]})
            self.assertIn("demo://readme", {resource["uri"] for resource in payload["resources"]})

    def test_mcp_demo_tool_call_returns_tool_event_detail_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._switch_test_workspace(root)
            self._write_mcp_config(root)

            response = self.client.post(
                "/api/mcp/tools/mcp__demo__echo/call",
                json={"arguments": {"text": "hello mcp"}},
            )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["tool"], "mcp__demo__echo")
            self.assertEqual(payload["server"], "demo")
            self.assertEqual(payload["result"]["content"], "hello mcp")
            self.assertGreaterEqual(len(payload["events"]), 2)
            self.assertEqual(payload["events"][0]["type"], "tool_start")
            self.assertEqual(payload["events"][0]["data"]["spec"]["category"], "mcp")
            self.assertEqual(payload["events"][-1]["type"], "tool_done")
            self.assertEqual(payload["events"][-1]["data"]["mcp"]["server"], "demo")

    def test_tools_api_includes_mcp_tools_with_normalized_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._switch_test_workspace(root)
            self._write_mcp_config(root)

            response = self.client.get("/api/tools")

            self.assertEqual(response.status_code, 200)
            tools = {item["name"]: item for item in response.json()["items"]}
            self.assertIn("mcp__demo__echo", tools)
            self.assertEqual(tools["mcp__demo__echo"]["category"], "mcp")
            self.assertTrue(tools["mcp__demo__echo"]["read_only"])

    def _switch_test_workspace(self, root: Path) -> None:
        old_root = app_module.workspace_manager.current_root
        app_module.workspace_manager.current_root = root.resolve()
        self.addCleanup(lambda: setattr(app_module.workspace_manager, "current_root", old_root))

    def _write_mcp_config(self, root: Path) -> None:
        config_file = root / ".nova" / "mcp.json"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "demo": {
                            "type": "stdio",
                            "command": "python3",
                            "args": ["-m", "nova_gateway.mcp.demo_server"],
                        },
                        "docs-http": {
                            "type": "http",
                            "url": "https://example.test/mcp",
                            "headers": {"X-Demo": "1"},
                        },
                        "events-sse": {
                            "type": "sse",
                            "url": "https://example.test/events",
                        },
                    }
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
