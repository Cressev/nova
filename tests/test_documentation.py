from pathlib import Path
import unittest


class DocumentationTest(unittest.TestCase):
    def test_module_responsibility_table_exists(self) -> None:
        doc = Path("docs/architecture/module-responsibilities.md")
        self.assertTrue(doc.is_file(), "应提供模块职责表文档")
        content = doc.read_text(encoding="utf-8")
        self.assertIn("| 模块 | 职责 | 关键文件 |", content)
        for module in [
            "api",
            "runtime",
            "sessions",
            "tools",
            "permissions",
            "processes",
            "memory",
            "workspace",
            "providers",
            "config",
            "observability",
            "frontend",
        ]:
            self.assertIn(module, content)


if __name__ == "__main__":
    unittest.main()
