from __future__ import annotations

import difflib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ToolExecutionError(RuntimeError):
    """工具执行失败时抛出，外层会把错误作为模型可读的工具结果。"""


@dataclass(frozen=True)
class ToolResult:
    tool: str
    title: str
    output: str
    ok: bool = True
    data: dict[str, Any] | None = None


class WorkspaceTools:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()

    def run(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        handlers = {
            "read_file": self.read_file,
            "list_files": self.list_files,
            "search_text": self.search_text,
            "shell_command": self.shell_command,
            "replace_in_file": self.replace_in_file,
            "create_file": self.create_file,
            "git_status": self.git_status,
        }
        handler = handlers.get(name)
        if handler is None:
            raise ToolExecutionError(f"未知工具：{name}")
        return handler(arguments)

    def read_file(self, arguments: dict[str, Any]) -> ToolResult:
        path = self._resolve_workspace_path(str(arguments.get("path", "")))
        max_bytes = int(arguments.get("max_bytes") or 24000)
        if not path.is_file():
            raise ToolExecutionError(f"文件不存在：{self._display(path)}")
        content = path.read_bytes()[:max_bytes].decode("utf-8", errors="replace")
        return ToolResult(
            tool="read_file",
            title=f"读取 {self._display(path)}",
            output=content,
            data={"path": self._display(path), "bytes": len(content.encode("utf-8"))},
        )

    def list_files(self, arguments: dict[str, Any]) -> ToolResult:
        root = self._resolve_workspace_path(str(arguments.get("path") or "."))
        limit = min(int(arguments.get("limit") or 200), 500)
        if not root.exists():
            raise ToolExecutionError(f"路径不存在：{self._display(root)}")
        files: list[str] = []
        for path in root.rglob("*"):
            if len(files) >= limit:
                break
            if self._is_ignored(path):
                continue
            if path.is_file():
                files.append(self._display(path))
        return ToolResult(
            tool="list_files",
            title=f"列出 {self._display(root)}",
            output="\n".join(files) or "未找到文件",
            data={"count": len(files)},
        )

    def search_text(self, arguments: dict[str, Any]) -> ToolResult:
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ToolExecutionError("search_text 需要 query")
        path = self._resolve_workspace_path(str(arguments.get("path") or "."))
        max_results = min(int(arguments.get("max_results") or 80), 200)
        command = [
            "rg",
            "-n",
            "--fixed-strings",
            query,
            str(path),
            "--glob",
            "!.git/**",
            "--glob",
            "!.nova/**",
            "--glob",
            "!references/upstream/**",
            "--glob",
            "!output/**",
            "--glob",
            "!.playwright-cli/**",
        ]
        result = subprocess.run(
            command,
            cwd=self.project_root,
            text=True,
            capture_output=True,
            timeout=10,
        )
        lines = result.stdout.splitlines()[:max_results]
        return ToolResult(
            tool="search_text",
            title=f"搜索 {query}",
            output="\n".join(lines) or "未找到匹配结果",
            ok=result.returncode in {0, 1},
            data={"query": query, "count": len(lines)},
        )

    def shell_command(self, arguments: dict[str, Any]) -> ToolResult:
        command = str(arguments.get("command") or arguments.get("cmd") or "").strip()
        if not command:
            raise ToolExecutionError("shell_command 需要 command")
        if not self._is_allowed_shell_command(command):
            raise ToolExecutionError(f"命令需要审批，当前版本已拦截：{command}")
        workdir = self._resolve_workspace_path(str(arguments.get("workdir") or "."))
        timeout = min(int(arguments.get("timeout_ms") or 10000) / 1000, 30)
        result = subprocess.run(
            command,
            cwd=workdir,
            shell=True,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        output = "\n".join(
            part for part in [result.stdout.strip(), result.stderr.strip()] if part
        )
        if len(output) > 24000:
            output = output[:24000] + "\n...[输出已截断]"
        return ToolResult(
            tool="shell_command",
            title=f"执行命令：{command}",
            output=output or f"命令退出码：{result.returncode}",
            ok=result.returncode == 0,
            data={"exit_code": result.returncode, "workdir": self._display(workdir)},
        )

    def replace_in_file(self, arguments: dict[str, Any]) -> ToolResult:
        path = self._resolve_workspace_path(str(arguments.get("path", "")))
        old = str(arguments.get("old") or "")
        new = str(arguments.get("new") or "")
        if not old:
            raise ToolExecutionError("replace_in_file 需要 old 文本")
        if not path.is_file():
            raise ToolExecutionError(f"文件不存在：{self._display(path)}")
        before = path.read_text(encoding="utf-8")
        if old not in before:
            raise ToolExecutionError(f"文件中找不到待替换文本：{self._display(path)}")
        after = before.replace(old, new, 1)
        path.write_text(after, encoding="utf-8")
        diff = "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"a/{self._display(path)}",
                tofile=f"b/{self._display(path)}",
            )
        )
        return ToolResult(
            tool="replace_in_file",
            title=f"修改 {self._display(path)}",
            output=diff[:24000],
            data={"path": self._display(path)},
        )

    def create_file(self, arguments: dict[str, Any]) -> ToolResult:
        path = self._resolve_workspace_path(str(arguments.get("path", "")))
        content = str(arguments.get("content") or "")
        if path.exists():
            raise ToolExecutionError(f"文件已存在：{self._display(path)}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return ToolResult(
            tool="create_file",
            title=f"创建 {self._display(path)}",
            output=f"已创建 {self._display(path)}，字符数 {len(content)}",
            data={"path": self._display(path)},
        )

    def git_status(self, _arguments: dict[str, Any]) -> ToolResult:
        result = subprocess.run(
            "git -c core.quotepath=false status --short --branch",
            cwd=self.project_root,
            shell=True,
            text=True,
            capture_output=True,
            timeout=5,
        )
        return ToolResult(
            tool="git_status",
            title="读取 Git 状态",
            output=result.stdout.strip() or result.stderr.strip(),
            ok=result.returncode == 0,
        )

    def _resolve_workspace_path(self, value: str) -> Path:
        if not value:
            raise ToolExecutionError("路径不能为空")
        raw = Path(value)
        path = raw if raw.is_absolute() else self.project_root / raw
        resolved = path.resolve()
        if resolved != self.project_root and self.project_root not in resolved.parents:
            raise ToolExecutionError(f"拒绝访问工作区外路径：{value}")
        if self._is_protected(resolved):
            raise ToolExecutionError(f"拒绝访问受保护路径：{self._display(resolved)}")
        return resolved

    def _is_protected(self, path: Path) -> bool:
        protected = [".git", ".nova", "references/upstream", ".playwright-cli", "output"]
        rel = self._display(path)
        return any(rel == item or rel.startswith(f"{item}/") for item in protected)

    def _is_ignored(self, path: Path) -> bool:
        rel = self._display(path)
        return self._is_protected(path) or "/__pycache__/" in f"/{rel}/" or rel.endswith(".pyc")

    def _display(self, path: Path) -> str:
        return path.resolve().relative_to(self.project_root).as_posix()

    def _is_allowed_shell_command(self, command: str) -> bool:
        lowered = command.strip().lower()
        blocked = ["rm ", "rm -", "sudo", "chmod ", "chown ", "mkfs", "dd ", ":(){", "git push"]
        if any(token in lowered for token in blocked):
            return False
        allowed_prefixes = (
            "pwd",
            "ls",
            "find ",
            "rg ",
            "grep ",
            "sed ",
            "cat ",
            "git status",
            "git diff",
            "git log",
            "python",
            "python3",
            "pytest",
            "curl -s http://127.0.0.1",
        )
        return lowered.startswith(allowed_prefixes)


def tool_result_as_json(result: ToolResult) -> str:
    return json.dumps(
        {
            "tool": result.tool,
            "title": result.title,
            "ok": result.ok,
            "output": result.output,
            "data": result.data or {},
        },
        ensure_ascii=False,
    )
