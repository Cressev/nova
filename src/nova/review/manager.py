from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from nova.lsp import LspManager


class ReviewManager:
    """把 diff、诊断和测试建议合成一个可审查的 Review 视图。"""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.lsp = LspManager(self.project_root)

    def summary(self) -> dict[str, Any]:
        git_status = self._git_status_lines()
        changed_files = self._changed_files(git_status)
        diff_text = self._git_diff(max_bytes=36000)
        diagnostics = self.lsp.diagnostics()
        risks = self._risks(changed_files, diff_text, diagnostics)
        suggested_tests = self._suggested_tests(changed_files, diagnostics)
        return {
            "ok": True,
            "project_root": str(self.project_root),
            "git": {
                "status": git_status,
                "dirty": bool(changed_files),
            },
            "changed_files": changed_files,
            "diff": {
                "files_count": len(changed_files),
                "preview": diff_text[:12000],
            },
            "diagnostics": diagnostics,
            "risks": risks,
            "suggested_tests": suggested_tests,
            "summary": self._summary_text(changed_files, risks, diagnostics, suggested_tests),
        }

    def run_tests(self, command: str | None = None) -> dict[str, Any]:
        selected = (command or self._default_test_command()).strip()
        if not self._is_allowed_test_command(selected):
            raise ValueError("Review 只允许运行项目内测试命令")
        result = subprocess.run(
            selected,
            cwd=self.project_root,
            shell=True,
            text=True,
            capture_output=True,
            timeout=120,
        )
        return {
            "ok": result.returncode == 0,
            "command": selected,
            "exit_code": result.returncode,
            "stdout": (result.stdout or "")[-16000:],
            "stderr": (result.stderr or "")[-16000:],
        }

    def _git_status_lines(self) -> list[str]:
        result = subprocess.run(
            ["git", "-c", "core.quotepath=false", "status", "--short"],
            cwd=self.project_root,
            text=True,
            capture_output=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []
        return [line for line in result.stdout.splitlines() if line.strip()]

    def _git_diff(self, *, max_bytes: int) -> str:
        result = subprocess.run(
            ["git", "-c", "core.quotepath=false", "diff", "--"],
            cwd=self.project_root,
            text=True,
            capture_output=True,
            timeout=10,
        )
        output = result.stdout if result.returncode == 0 else result.stderr
        return (output or "当前没有 diff")[:max_bytes]

    def _changed_files(self, status_lines: list[str]) -> list[str]:
        files: list[str] = []
        for line in status_lines:
            path = line[3:].strip() if len(line) > 3 else ""
            if " -> " in path:
                path = path.split(" -> ", 1)[1].strip()
            for candidate in self._expand_status_path(path):
                if candidate and not self._is_excluded(candidate) and candidate not in files:
                    files.append(candidate)
        return files

    def _expand_status_path(self, relative_path: str) -> list[str]:
        path = relative_path.strip()
        if not path:
            return []
        absolute = self.project_root / path
        if absolute.is_dir():
            expanded: list[str] = []
            for current_root, dirnames, filenames in os.walk(absolute):
                dirnames[:] = [name for name in dirnames if not self._is_excluded(self._relative(Path(current_root) / name))]
                for filename in filenames:
                    rel = self._relative(Path(current_root) / filename)
                    if not self._is_excluded(rel):
                        expanded.append(rel)
            return sorted(expanded)
        return [path.rstrip("/")]

    def _risks(self, changed_files: list[str], diff_text: str, diagnostics: dict[str, Any]) -> list[dict[str, str]]:
        risks: list[dict[str, str]] = []
        diag_summary = diagnostics.get("summary") if isinstance(diagnostics.get("summary"), dict) else {}
        if int(diag_summary.get("error") or 0) > 0:
            risks.append({
                "severity": "high",
                "title": "存在 LSP 错误诊断",
                "detail": "代码当前无法通过基础语法或语言服务检查，先修复诊断再继续提交。",
            })
        if any(path.startswith("src/") and path.endswith(".py") for path in changed_files) and not any(
            path.startswith("tests/") for path in changed_files
        ):
            risks.append({
                "severity": "medium",
                "title": "生产代码变更缺少测试变更",
                "detail": "src 下 Python 代码发生变化，但当前 diff 没有对应 tests 变更。",
            })
        security_words = ("permission", "approval", "sandbox", "subprocess", "shell", "api_key", "secret")
        if any(word in diff_text.lower() for word in security_words):
            risks.append({
                "severity": "high",
                "title": "涉及权限、密钥或命令执行路径",
                "detail": "这类变更需要重点确认权限边界、错误兜底和敏感信息不落前端。",
            })
        if not changed_files:
            risks.append({
                "severity": "info",
                "title": "当前没有未提交变更",
                "detail": "Review 面板仍可用于运行测试和查看诊断。",
            })
        return risks

    def _suggested_tests(self, changed_files: list[str], diagnostics: dict[str, Any]) -> list[dict[str, str]]:
        commands: list[dict[str, str]] = []
        has_python = self._has_path("tests") or any(path.endswith(".py") for path in changed_files)
        has_frontend = any(path.startswith("static/") or path.endswith(".js") for path in changed_files)
        if has_python:
            commands.append({
                "label": "Python 单元测试",
                "command": "PYTHONPATH=src python3 -m unittest discover -s tests",
                "reason": "覆盖后端 API、工具和 LSP/Review 逻辑。",
            })
        if has_frontend:
            commands.append({
                "label": "前端静态测试",
                "command": "for f in tests/frontend*.test.js; do node \"$f\"; done",
                "reason": "覆盖右侧面板、命令菜单和工具展示的静态契约。",
            })
        if diagnostics.get("diagnostics"):
            commands.append({
                "label": "LSP 诊断复核",
                "command": "PYTHONPATH=src python3 -m unittest tests.test_lsp",
                "reason": "当前 Review 已发现语言诊断，需要复核诊断链路。",
            })
        if not commands:
            commands.append({
                "label": "基础冒烟",
                "command": "python3 -m compileall -q src tests",
                "reason": "没有识别出专项测试时，至少确认 Python 文件可编译。",
            })
        return commands

    def _summary_text(
        self,
        changed_files: list[str],
        risks: list[dict[str, str]],
        diagnostics: dict[str, Any],
        suggested_tests: list[dict[str, str]],
    ) -> str:
        risk_text = "、".join(f"{item['severity']}:{item['title']}" for item in risks) or "暂无风险"
        diag_summary = diagnostics.get("summary") if isinstance(diagnostics.get("summary"), dict) else {}
        return (
            "Review summary\n"
            f"- 变更文件：{len(changed_files)} 个\n"
            f"- 诊断：错误 {int(diag_summary.get('error') or 0)}，警告 {int(diag_summary.get('warning') or 0)}\n"
            f"- 风险：{risk_text}\n"
            f"- 建议先运行：{suggested_tests[0]['command'] if suggested_tests else '无'}"
        )

    def _default_test_command(self) -> str:
        summary = self.summary()
        tests = summary.get("suggested_tests") or []
        if tests:
            return str(tests[0]["command"])
        return "python3 -m compileall -q src tests"

    def _has_path(self, relative_path: str) -> bool:
        return (self.project_root / relative_path).exists()

    def _is_excluded(self, relative_path: str) -> bool:
        excluded = (".git", ".nova", "references/", "output/", ".playwright-cli/", "node_modules/")
        return (
            relative_path in excluded
            or any(relative_path.startswith(prefix) for prefix in excluded)
            or "__pycache__" in relative_path.split("/")
            or relative_path.endswith(".pyc")
        )

    def _relative(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.project_root).as_posix()
        except ValueError:
            return path.as_posix()

    def _is_allowed_test_command(self, command: str) -> bool:
        allowed_prefixes = (
            "PYTHONPATH=src python3 -m unittest",
            "python3 -m unittest",
            "python3 -m compileall",
            "pytest",
            "for f in tests/frontend",
            "node tests/frontend",
        )
        return command.startswith(allowed_prefixes)
