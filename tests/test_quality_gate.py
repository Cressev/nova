from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from nova.review import QualityGateManager


class QualityGateManagerTest(unittest.TestCase):
    def test_summary_blocks_secret_and_reports_transient_untracked_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._init_repo(root)
            (root / "src").mkdir()
            secret_name = "API" + "_KEY"
            secret_value = "secret" + "-fixture-value"
            (root / "src" / "settings.py").write_text(
                f'{secret_name} = "{secret_value}"\n',
                encoding="utf-8",
            )
            (root / "review").mkdir()
            (root / "review" / "scratch.md").write_text("临时审查输出\n", encoding="utf-8")
            subprocess.run(["git", "add", "src/settings.py"], cwd=root, check=True)

            summary = QualityGateManager(root).summary()

            self.assertFalse(summary["commit_allowed"])
            self.assertIn("src/settings.py", summary["staged_files"])
            self.assertTrue(any(item["kind"] == "secret" for item in summary["sensitive_findings"]))
            self.assertIn("review/scratch.md", summary["untracked_transient_files"])
            self.assertTrue(any("敏感信息" in warning for warning in summary["warnings"]))

    def test_summary_allows_clean_staged_source_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._init_repo(root)
            (root / "README.md").write_text("baseline\nnext\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=root, check=True)

            summary = QualityGateManager(root).summary()

            self.assertTrue(summary["commit_allowed"])
            self.assertEqual(summary["sensitive_findings"], [])
            self.assertEqual(summary["forbidden_staged_files"], [])
            self.assertTrue(any("unittest discover" in item["command"] for item in summary["fixed_commands"]))

    def _init_repo(self, root: Path) -> None:
        subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.email", "nova@example.com"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "Nova Test"], cwd=root, check=True)
        (root / "README.md").write_text("baseline\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-m", "baseline"], cwd=root, check=True, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    unittest.main()
