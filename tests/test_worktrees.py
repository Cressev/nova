from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from nova_gateway import main as app_module
from nova_gateway.main import app


class WorktreeApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self._git(["init"], cwd=self.repo)
        self._git(["checkout", "-b", "main"], cwd=self.repo)
        self._git(["config", "user.email", "nova@example.local"], cwd=self.repo)
        self._git(["config", "user.name", "Nova Test"], cwd=self.repo)
        (self.repo / "README.md").write_text("初始内容\n", encoding="utf-8")
        self._git(["add", "README.md"], cwd=self.repo)
        self._git(["commit", "-m", "init"], cwd=self.repo)

        self.old_current = app_module.workspace_manager.current_root
        self.old_allowed = app_module.workspace_manager.allowed_roots
        self.old_browse = app_module.workspace_manager.browse_roots
        app_module.workspace_manager.allowed_roots = [self.root.resolve()]
        app_module.workspace_manager.browse_roots = app_module.workspace_manager._derive_browse_roots([self.root.resolve()])
        app_module.workspace_manager.current_root = self.repo.resolve()

    def tearDown(self) -> None:
        app_module.workspace_manager.current_root = self.old_current
        app_module.workspace_manager.allowed_roots = self.old_allowed
        app_module.workspace_manager.browse_roots = self.old_browse
        self.tmpdir.cleanup()

    def test_create_switch_diff_and_cleanup_worktree(self) -> None:
        created = self.client.post("/api/worktrees", json={"name": "demo"})

        self.assertEqual(created.status_code, 201)
        payload = created.json()
        self.assertEqual(payload["name"], "demo")
        self.assertEqual(payload["branch"], "worktree-demo")
        worktree_path = Path(payload["path"])
        self.assertTrue(worktree_path.is_dir())
        self.assertEqual(app_module.workspace_manager.current_root, worktree_path.resolve())
        self.assertEqual(payload["original_root"], str(self.repo.resolve()))

        (worktree_path / "feature.txt").write_text("worktree diff\n", encoding="utf-8")
        diff = self.client.get("/api/worktrees/current/diff")

        self.assertEqual(diff.status_code, 200)
        self.assertIn("feature.txt", diff.json()["diff"])
        self.assertGreater(diff.json()["dirty_count"], 0)

        refused = self.client.delete("/api/worktrees/demo")
        self.assertEqual(refused.status_code, 409)
        self.assertIn("discard=true", refused.json()["detail"])

        cleaned = self.client.delete("/api/worktrees/demo", params={"discard": "true"})
        self.assertEqual(cleaned.status_code, 200)
        self.assertFalse(worktree_path.exists())
        self.assertEqual(app_module.workspace_manager.current_root, self.repo.resolve())

        listed = self.client.get("/api/worktrees")
        self.assertEqual(listed.status_code, 200)
        self.assertFalse(any(item["name"] == "demo" for item in listed.json()["items"]))

    def test_rejects_unsafe_worktree_name(self) -> None:
        response = self.client.post("/api/worktrees", json={"name": "../escape"})

        self.assertEqual(response.status_code, 400)

    def test_cleanup_nested_worktree_name(self) -> None:
        created = self.client.post("/api/worktrees", json={"name": "feature/demo"})
        self.assertEqual(created.status_code, 201, created.text)

        response = self.client.delete("/api/worktrees/feature%2Fdemo", params={"discard": "true"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["removed"])

    def _git(self, args: list[str], *, cwd: Path) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout


if __name__ == "__main__":
    unittest.main()
