from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PYTHON_BIN = "/Users/liam/.miniforge3/envs/claude/bin/python"


@dataclass(frozen=True)
class GateCommand:
    key: str
    label: str
    command: str
    timeout_seconds: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "command": self.command,
            "timeout_seconds": self.timeout_seconds,
        }


class QualityGateManager:
    """交付前质量门禁。

    Review 负责解释风险；QualityGate 负责回答“现在能不能提交”。
    """

    SECRET_PATTERNS = (
        ("secret", re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*[\"']?[^\"'\s]{8,}")),
        ("secret", re.compile(r"\b(sk|pk)-[A-Za-z0-9_-]{16,}\b")),
        ("secret", re.compile(r"\b[A-Za-z0-9]{24,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    )
    TRANSIENT_PREFIXES = (
        ".codex/",
        ".playwright-cli/",
        "review/",
        "output/",
        "node_modules/",
        "docs/superpowers/plans/",
    )
    TRANSIENT_NAMES = {".DS_Store"}

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()

    def summary(self) -> dict[str, Any]:
        staged_files = self._staged_files()
        staged_diff = self._staged_diff(max_bytes=80000)
        sensitive_findings = self._scan_sensitive_diff(staged_diff)
        forbidden_staged = [path for path in staged_files if self._is_transient(path)]
        untracked_transient = [
            path for path in self._untracked_files() if self._is_transient(path)
        ]
        warnings = self._warnings(
            staged_files=staged_files,
            forbidden_staged=forbidden_staged,
            sensitive_findings=sensitive_findings,
            untracked_transient=untracked_transient,
        )
        commit_allowed = bool(staged_files) and not forbidden_staged and not sensitive_findings
        return {
            "ok": True,
            "project_root": str(self.project_root),
            "fixed_commands": [command.as_dict() for command in self.fixed_commands()],
            "staged_files": staged_files,
            "forbidden_staged_files": forbidden_staged,
            "untracked_transient_files": untracked_transient,
            "sensitive_findings": sensitive_findings,
            "commit_allowed": commit_allowed,
            "warnings": warnings,
            "summary": self._summary_text(commit_allowed, staged_files, warnings),
        }

    def run(self, command_keys: list[str] | None = None) -> dict[str, Any]:
        selected = self._selected_commands(command_keys)
        results = [self._run_command(command) for command in selected]
        return {
            "ok": all(result["ok"] for result in results),
            "project_root": str(self.project_root),
            "results": results,
            "summary": self._run_summary(results),
        }

    def fixed_commands(self) -> list[GateCommand]:
        return [
            GateCommand(
                key="backend",
                label="后端全量单测",
                command=f"PYTHONPATH=src {PYTHON_BIN} -m unittest discover -s tests",
                timeout_seconds=180,
            ),
            GateCommand(
                key="frontend",
                label="前端静态回归",
                command='for f in tests/frontend_*.test.js; do node "$f" || exit 1; done',
                timeout_seconds=60,
            ),
            GateCommand(
                key="compileall",
                label="Python 编译检查",
                command=f"{PYTHON_BIN} -m compileall -q src tests",
                timeout_seconds=60,
            ),
            GateCommand(
                key="diff_check",
                label="Diff 空白检查",
                command="git diff --check",
                timeout_seconds=30,
            ),
        ]

    def _selected_commands(self, command_keys: list[str] | None) -> list[GateCommand]:
        commands = self.fixed_commands()
        if not command_keys:
            return commands
        allowed = {command.key: command for command in commands}
        selected = []
        for key in command_keys:
            if key not in allowed:
                raise ValueError(f"未知质量门禁命令：{key}")
            selected.append(allowed[key])
        return selected

    def _run_command(self, command: GateCommand) -> dict[str, Any]:
        try:
            result = subprocess.run(
                command.command,
                cwd=self.project_root,
                shell=True,
                text=True,
                capture_output=True,
                timeout=command.timeout_seconds,
            )
            return {
                **command.as_dict(),
                "ok": result.returncode == 0,
                "exit_code": result.returncode,
                "stdout": (result.stdout or "")[-16000:],
                "stderr": (result.stderr or "")[-16000:],
            }
        except subprocess.TimeoutExpired as exc:
            return {
                **command.as_dict(),
                "ok": False,
                "exit_code": None,
                "stdout": (exc.stdout or "")[-16000:] if isinstance(exc.stdout, str) else "",
                "stderr": "质量门禁命令超时，Nova 已停止等待。",
            }

    def _staged_files(self) -> list[str]:
        result = self._git(["diff", "--cached", "--name-only", "--diff-filter=ACMR", "--"])
        return [line.strip() for line in result.splitlines() if line.strip()]

    def _untracked_files(self) -> list[str]:
        result = self._git(["status", "--porcelain=v1", "--untracked-files=all"])
        files: list[str] = []
        for line in result.splitlines():
            if not line.startswith("?? "):
                continue
            path = line[3:].strip()
            if path:
                files.append(path)
        return files

    def _staged_diff(self, *, max_bytes: int) -> str:
        return self._git(["diff", "--cached", "--"], max_bytes=max_bytes)

    def _scan_sensitive_diff(self, diff_text: str) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        current_file = ""
        for line_number, line in enumerate(diff_text.splitlines(), start=1):
            if line.startswith("+++ b/"):
                current_file = line.removeprefix("+++ b/")
                continue
            if not line.startswith("+") or line.startswith("+++"):
                continue
            for kind, pattern in self.SECRET_PATTERNS:
                if pattern.search(line):
                    findings.append(
                        {
                            "kind": kind,
                            "file": current_file,
                            "line": line_number,
                            "preview": self._redact(line[1:].strip()),
                        }
                    )
                    break
        return findings

    def _warnings(
        self,
        *,
        staged_files: list[str],
        forbidden_staged: list[str],
        sensitive_findings: list[dict[str, Any]],
        untracked_transient: list[str],
    ) -> list[str]:
        warnings: list[str] = []
        if not staged_files:
            warnings.append("当前没有 staged 文件，不能生成可审计提交。")
        if forbidden_staged:
            warnings.append("staged 区包含临时目录或缓存文件，禁止提交。")
        if sensitive_findings:
            warnings.append("staged diff 疑似包含敏感信息，提交前必须移除。")
        if untracked_transient:
            warnings.append("工作区存在未跟踪临时产物，提交前需确认不会误提交。")
        return warnings

    def _summary_text(self, commit_allowed: bool, staged_files: list[str], warnings: list[str]) -> str:
        state = "允许提交" if commit_allowed else "暂不允许提交"
        warning_text = "；".join(warnings) if warnings else "无阻断项"
        return f"Quality gate：{state}。staged {len(staged_files)} 个文件。{warning_text}。"

    def _run_summary(self, results: list[dict[str, Any]]) -> str:
        failed = [result for result in results if not result["ok"]]
        if failed:
            return f"FAILED：{len(failed)} / {len(results)} 个质量门禁未通过。"
        return f"ALL GREEN：{len(results)} 个质量门禁全部通过。"

    def _is_transient(self, relative_path: str) -> bool:
        normalized = relative_path.replace("\\", "/").strip()
        name = Path(normalized).name
        return (
            name in self.TRANSIENT_NAMES
            or "__pycache__" in normalized.split("/")
            or normalized.endswith(".pyc")
            or any(normalized.startswith(prefix) for prefix in self.TRANSIENT_PREFIXES)
        )

    def _git(self, args: list[str], *, max_bytes: int | None = None) -> str:
        result = subprocess.run(
            ["git", "-c", "core.quotepath=false", *args],
            cwd=self.project_root,
            text=True,
            capture_output=True,
            timeout=10,
        )
        output = result.stdout if result.returncode == 0 else ""
        return output[:max_bytes] if max_bytes is not None else output

    def _redact(self, text: str) -> str:
        redacted = text
        redacted = re.sub(r"(?i)(api[_-]?key|secret|token|password)(\s*[:=]\s*[\"']?)([^\"'\s]+)", r"\1\2***", redacted)
        redacted = re.sub(r"\b(sk|pk)-[A-Za-z0-9_-]{8,}\b", r"\1-***", redacted)
        return redacted[:180]
