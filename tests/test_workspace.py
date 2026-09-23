from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nova.workspace import WorkspaceError, WorkspaceManager


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
        root = Path(tempfile.mkdtemp(dir=self.root))
        real_root = root / "Documents" / "Study" / "Code"
        real_project = real_root / "nova"
        real_project.mkdir(parents=True)
        manager = WorkspaceManager(
            initial_root=real_project,
            allowed_roots=[root / "documents" / "study" / "code"],
        )

        candidates = manager.status(query=str(root))["candidates"]

        self.assertIn(str(root / "Documents"), candidates)

    def test_query_resolves_existing_path_case_insensitively(self) -> None:
        work = self.root / "CaseProbe" / "Work"
        child = work / "ZhiPu"
        child.mkdir(parents=True)

        candidates = self.manager.status(query=str(self.root / "caseprobe" / "work"))["candidates"]

        self.assertEqual(candidates, [str(child)])

    def test_create_folder_inside_browse_root(self) -> None:
        (self.root / "work").mkdir()

        created = self.manager.create_folder(str(self.root / "work" / "nova-new"))

        self.assertTrue(created.is_dir())
        self.assertEqual(created, (self.root / "work" / "nova-new").resolve())

    def test_path_status_existing_directory_can_select_not_create(self) -> None:
        status = self.manager.status(query=str(self.project))["query_status"]

        self.assertTrue(status["exists"])
        self.assertTrue(status["can_select"])
        self.assertFalse(status["can_create"])

    def test_path_status_missing_directory_can_create_not_select(self) -> None:
        (self.root / "work").mkdir()

        status = self.manager.status(query=str(self.root / "work" / "new-project"))["query_status"]

        self.assertFalse(status["exists"])
        self.assertFalse(status["can_select"])
        self.assertTrue(status["can_create"])

    def test_recent_projects_track_switch_and_create(self) -> None:
        work = self.root / "work"
        work.mkdir()

        self.manager.set_current(str(work))
        created = self.manager.create_folder(str(self.root / "work" / "nova-new"))
        recent = self.manager.status()["recent_projects"]

        self.assertEqual(recent[0], str(created))
        self.assertEqual(recent[1], str(work.resolve()))
        self.assertIn(str(self.project.resolve()), recent)

    def test_recent_projects_survive_manager_restart(self) -> None:
        recent_file = self.root / ".nova" / "workspace-recents.json"
        work = self.root / "work"
        work.mkdir()
        manager = WorkspaceManager(
            initial_root=self.project,
            allowed_roots=[self.allowed_root],
            recent_file=recent_file,
        )

        manager.set_current(str(work))
        restarted = WorkspaceManager(
            initial_root=self.project,
            allowed_roots=[self.allowed_root],
            recent_file=recent_file,
        )

        self.assertEqual(restarted.status()["recent_projects"][0], str(self.project.resolve()))
        self.assertIn(str(work.resolve()), restarted.status()["recent_projects"])

    def test_workspace_registry_rename_reorder_delete_and_restart(self) -> None:
        recent_file = self.root / ".nova" / "workspace-recents.json"
        second = self.allowed_root / "second"
        second.mkdir()
        manager = WorkspaceManager(initial_root=self.project, allowed_roots=[self.allowed_root], recent_file=recent_file)
        manager.set_current(str(second))
        second_key = str(second.resolve())
        project_key = str(self.project.resolve())
        renamed = manager.rename_workspace(second_key, "第二项目")
        self.assertEqual(renamed["title"], "第二项目")
        manager.insert_workspace_before(second_key, project_key)
        self.assertEqual([item["path"] for item in manager.list_workspaces()][:2], [second_key, project_key])
        restarted = WorkspaceManager(initial_root=self.project, allowed_roots=[self.allowed_root], recent_file=recent_file)
        self.assertEqual(restarted.list_workspaces()[0]["title"], "第二项目")
        with self.assertRaises(WorkspaceError):
            restarted.delete_workspace(str(self.project))
        restarted.set_current(str(self.project))
        restarted.delete_workspace(str(second))
        self.assertTrue((self.allowed_root / "second").exists())

    def test_manual_session_order_survives_store_restart(self) -> None:
        from nova.models import ChatSession
        from nova.sessions.store import SessionStore
        from datetime import datetime, timezone
        state = self.root / ".nova-state"
        first = ChatSession(id="chat-first", title="第一", workspace=str(self.project), created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
        second = ChatSession(id="chat-second", title="第二", workspace=str(self.project), created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
        store = SessionStore(state)
        store.create_chat_session(first)
        store.create_chat_session(second)
        store.reorder_chat_session("chat-second", workspace=str(self.project), before_session_id="chat-first")
        restarted = SessionStore(state)
        self.assertEqual([item.id for item in restarted.list_chat_sessions(workspace=str(self.project))], ["chat-second", "chat-first"])

    def test_completion_extends_to_common_prefix_for_multiple_matches(self) -> None:
        parent = self.root / "work"
        (parent / "alpha-api").mkdir(parents=True)
        (parent / "alpha-app").mkdir()

        status = self.manager.status(query=str(parent / "al"))

        self.assertEqual(status["completion"]["value"], str(parent / "alpha-ap"))
        self.assertFalse(status["completion"]["is_final"])

    def test_completion_finishes_unique_directory_with_separator(self) -> None:
        parent = self.root / "work"
        (parent / "nova").mkdir(parents=True)

        status = self.manager.status(query=str(parent / "no"))

        self.assertEqual(status["completion"]["value"], f"{parent / 'nova'}/")
        self.assertTrue(status["completion"]["is_final"])


if __name__ == "__main__":
    unittest.main()
