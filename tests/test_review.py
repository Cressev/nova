from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from nova.app import main as app_module
from nova.app.main import app


class ReviewApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_review_summary_combines_diff_diagnostics_risks_and_tests(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._init_repo(root)
            self._switch_test_workspace(root)
            (root / "pyproject.toml").write_text("[project]\nname='review-demo'\n", encoding="utf-8")
            (root / "src").mkdir()
            (root / "tests").mkdir()
            (root / "src" / "service.py").write_text("def run(:\n    return True\n", encoding="utf-8")

            response = self.client.get("/api/review/summary")

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["project_root"], str(root.resolve()))
            self.assertGreaterEqual(payload["diff"]["files_count"], 1)
            self.assertIn("src/service.py", payload["changed_files"])
            self.assertGreaterEqual(payload["diagnostics"]["summary"]["error"], 1)
            self.assertTrue(any(item["severity"] == "high" for item in payload["risks"]))
            self.assertTrue(any("unittest" in item["command"] for item in payload["suggested_tests"]))
            self.assertIn("Review summary", payload["summary"])

    def test_review_run_tests_executes_suggested_command_and_returns_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._init_repo(root)
            self._switch_test_workspace(root)
            (root / "tests").mkdir()
            (root / "tests" / "test_smoke.py").write_text(
                "import unittest\n\n"
                "class Smoke(unittest.TestCase):\n"
                "    def test_ok(self):\n"
                "        self.assertTrue(True)\n",
                encoding="utf-8",
            )

            response = self.client.post("/api/review/run-tests", json={})

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["exit_code"], 0)
            self.assertIn("python3 -m unittest discover -s tests", payload["command"])
            self.assertIn("Ran 1 test", payload["stderr"] + payload["stdout"])

    def test_review_summary_excludes_python_cache_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._init_repo(root)
            self._switch_test_workspace(root)
            cache = root / "src" / "__pycache__"
            cache.mkdir(parents=True)
            (cache / "service.cpython-313.pyc").write_bytes(b"cache")
            (root / "src" / "service.py").write_text("def ok():\n    return True\n", encoding="utf-8")

            response = self.client.get("/api/review/summary")

            self.assertEqual(response.status_code, 200)
            changed_files = response.json()["changed_files"]
            self.assertIn("src/service.py", changed_files)
            self.assertTrue(all("__pycache__" not in path for path in changed_files))
            self.assertTrue(all(not path.endswith(".pyc") for path in changed_files))

    def _init_repo(self, root: Path) -> None:
        subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.email", "nova@example.com"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "Nova Test"], cwd=root, check=True)
        (root / "README.md").write_text("baseline\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-m", "baseline"], cwd=root, check=True, stdout=subprocess.DEVNULL)

    def _switch_test_workspace(self, root: Path) -> None:
        old_root = app_module.workspace_manager.current_root
        app_module.workspace_manager.current_root = root.resolve()
        self.addCleanup(lambda: setattr(app_module.workspace_manager, "current_root", old_root))


if __name__ == "__main__":
    unittest.main()
