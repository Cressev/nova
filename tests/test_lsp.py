from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from nova_gateway import main as app_module
from nova_gateway.main import app


class LspApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_lsp_status_detects_python_project_and_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._switch_test_workspace(root)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
            (root / "app.py").write_text("def hello():\n    return 'ok'\n", encoding="utf-8")

            response = self.client.get("/api/lsp/status")

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload["enabled"])
            self.assertEqual(payload["project_root"], str(root.resolve()))
            self.assertIn("python", payload["languages"])
            server = payload["servers"][0]
            self.assertEqual(server["name"], "python")
            self.assertEqual(server["state"], "ready")
            self.assertIn("diagnostics", server["capabilities"])
            self.assertIn("definition", server["capabilities"])

    def test_lsp_diagnostics_reports_python_syntax_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._switch_test_workspace(root)
            (root / "broken.py").write_text("def broken(:\n    pass\n", encoding="utf-8")

            response = self.client.get("/api/lsp/diagnostics", params={"path": "broken.py"})

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["language"], "python")
            self.assertGreaterEqual(payload["summary"]["error"], 1)
            diagnostic = payload["diagnostics"][0]
            self.assertEqual(diagnostic["severity"], "error")
            self.assertEqual(diagnostic["path"], "broken.py")
            self.assertIn("invalid syntax", diagnostic["message"])
            self.assertGreaterEqual(diagnostic["range"]["start"]["line"], 1)

    def test_lsp_definition_finds_python_function_definition(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._switch_test_workspace(root)
            (root / "pkg").mkdir()
            (root / "pkg" / "service.py").write_text(
                "def target(value):\n    return value\n\nresult = target(1)\n",
                encoding="utf-8",
            )

            response = self.client.get(
                "/api/lsp/definition",
                params={"path": "pkg/service.py", "symbol": "target"},
            )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["definition"]["path"], "pkg/service.py")
            self.assertEqual(payload["definition"]["symbol"], "target")
            self.assertEqual(payload["definition"]["kind"], "function")
            self.assertEqual(payload["definition"]["range"]["start"]["line"], 1)

    def _switch_test_workspace(self, root: Path) -> None:
        old_root = app_module.workspace_manager.current_root
        app_module.workspace_manager.current_root = root.resolve()
        self.addCleanup(lambda: setattr(app_module.workspace_manager, "current_root", old_root))


if __name__ == "__main__":
    unittest.main()
