from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nova.runtime.triggers import detect_input_trigger
from nova.skills import SkillManager


class InputTriggerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        root = Path(self.tmpdir.name)
        skill_dir = root / ".nova" / "skills" / "review-code"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: review-code\ndescription: review\nuser-invocable: true\n---\n检查代码。\n",
            encoding="utf-8",
        )
        self.skills = SkillManager(root)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_registered_slash_command_only_triggers_at_token_boundary(self) -> None:
        self.assertEqual(detect_input_trigger("/help", self.skills).kind, "slash")
        self.assertEqual(detect_input_trigger("/help now", self.skills).token, "/help")
        self.assertIsNone(detect_input_trigger("/Users/liam/project", self.skills))
        self.assertIsNone(detect_input_trigger("/helpful", self.skills))
        self.assertIsNone(detect_input_trigger("text /help", self.skills))

    def test_dollar_only_triggers_existing_user_invocable_skill(self) -> None:
        self.assertEqual(detect_input_trigger("$review-code", self.skills).kind, "skill")
        self.assertEqual(detect_input_trigger("$review-code focus", self.skills).token, "$review-code")
        self.assertIsNone(detect_input_trigger("$HOME/project", self.skills))
        self.assertIsNone(detect_input_trigger("$review-code/file", self.skills))
        self.assertIsNone(detect_input_trigger("$unknown", self.skills))
        self.assertIsNone(detect_input_trigger("text $review-code", self.skills))


if __name__ == "__main__":
    unittest.main()
