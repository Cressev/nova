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

    def test_query_returns_only_direct_children_not_default_projects(self) -> None:
        study = self.root / "documents" / "study"
        unrelated_project = self.allowed_root / "other"
        unrelated_project.mkdir()
        (unrelated_project / "AGENTS.md").write_text("其他项目", encoding="utf-8")

        candidates = self.manager.status(query=str(study))["candidates"]

        self.assertEqual(candidates, [str(self.allowed_root)])

    def test_select_allows_local_browse_root_descendant(self) -> None:
        work = self.root / "work"
        work.mkdir()

        selected = self.manager.set_current(str(work))

        self.assertEqual(selected, work.resolve())

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

    def test_query_resolves_existing_path_case_insensitively(self) -> None:
        work = self.root / "Documents" / "Work"
        child = work / "ZhiPu"
        child.mkdir(parents=True)

        candidates = self.manager.status(query=str(self.root / "documents" / "work"))["candidates"]

        self.assertEqual(candidates, [str(child)])

    def test_create_folder_inside_browse_root(self) -> None:
        (self.root / "work").mkdir()

        created = self.manager.create_folder(str(self.root / "work" / "nova-new"))

        self.assertTrue(created.is_dir())
        self.assertEqual(created, (self.root / "work" / "nova-new").resolve())


if __name__ == "__main__":
    unittest.main()
