from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nova_gateway.workspace import WorkspaceError, WorkspaceManager


class WorkspaceManagerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.allowed_root = self.root / "documents" / "study" / "code"
        self.project = self.allowed_root / "nova"
        self.project.mkdir(parents=True)
        (self.project / "AGENTS.md").write_text("项目指令", encoding="utf-8")
        self.manager = WorkspaceManager(initial_root=self.project, allowed_roots=[self.allowed_root])

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_candidates_can_browse_ancestor_path_toward_allowed_root(self) -> None:
        candidates = self.manager.status(query=str(self.root))["candidates"]

        self.assertIn(str(self.root / "documents"), candidates)

    def test_candidates_show_projects_inside_allowed_root(self) -> None:
        candidates = self.manager.status(query=f"{self.allowed_root}/")["candidates"]

        self.assertIn(str(self.project), candidates)

    def test_select_still_rejects_ancestor_outside_allowed_root(self) -> None:
        with self.assertRaises(WorkspaceError):
            self.manager.set_current(str(self.root / "documents"))

    def test_browsing_handles_windows_mount_case_mismatch(self) -> None:
        real_root = self.root / "Documents" / "Study" / "Code"
        real_project = real_root / "nova"
        real_project.mkdir(parents=True)
        manager = WorkspaceManager(
            initial_root=real_project,
            allowed_roots=[self.root / "documents" / "study" / "code"],
        )

        candidates = manager.status(query=str(self.root))["candidates"]

        self.assertIn(str(self.root / "Documents"), candidates)


if __name__ == "__main__":
    unittest.main()
