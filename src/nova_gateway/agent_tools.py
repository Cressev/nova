from __future__ import annotations

import difflib
import json
import os
import shlex
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


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    read_only: bool
    supports_parallel: bool
    permission: str
    schema: dict[str, Any]


TOOL_SPECS: dict[str, ToolSpec] = {
    "read_file": ToolSpec(
        name="read_file",
        description="读取工作区内文件内容。",
        read_only=True,
        supports_parallel=True,
        permission="read",
        schema={"path": "相对路径", "max_bytes": 24000},
    ),
    "list_files": ToolSpec(
        name="list_files",
        description="列出工作区内文件。",
        read_only=True,
        supports_parallel=True,
        permission="read",
        schema={"path": ".", "limit": 200},
    ),
    "search_text": ToolSpec(
        name="search_text",
        description="使用 ripgrep 在工作区内搜索固定文本。",
        read_only=True,
        supports_parallel=True,
        permission="read",
        schema={"query": "关键词", "path": ".", "max_results": 80},
    ),
    "git_status": ToolSpec(
        name="git_status",
        description="读取 Git 分支和工作区变更摘要。",
        read_only=True,
        supports_parallel=True,
        permission="read",
        schema={},
    ),
    "git_diff": ToolSpec(
        name="git_diff",
        description="读取当前工作区 diff。",
        read_only=True,
        supports_parallel=True,
        permission="read",
        schema={"path": "可选相对路径", "max_bytes": 24000},
    ),
    "shell_command": ToolSpec(
        name="shell_command",
        description="执行受控白名单 shell 命令。",
        read_only=False,
        supports_parallel=False,
        permission="shell",
        schema={"command": "受控命令", "workdir": ".", "timeout_ms": 10000},
    ),
    "replace_in_file": ToolSpec(
        name="replace_in_file",
        description="替换文件中的第一处匹配文本。",
        read_only=False,
        supports_parallel=False,
        permission="write",
        schema={"path": "文件", "old": "原文", "new": "新文"},
    ),
    "create_file": ToolSpec(
        name="create_file",
        description="创建新文件。",
        read_only=False,
        supports_parallel=False,
        permission="write",
        schema={"path": "文件", "content": "内容"},
    ),
    "apply_patch": ToolSpec(
        name="apply_patch",
        description="应用 unified diff 补丁到工作区。",
        read_only=False,
        supports_parallel=False,
        permission="write",
        schema={"patch": "unified diff"},
    ),
}


class WorkspaceTools:
    def __init__(self, project_root: Path, *, permission_mode: str = "workspace_write") -> None:
        self.project_root = project_root.resolve()
        self.permission_mode = permission_mode

    def run(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        handlers = {
            "read_file": self.read_file,
            "list_files": self.list_files,
            "search_text": self.search_text,
            "shell_command": self.shell_command,
            "replace_in_file": self.replace_in_file,
            "create_file": self.create_file,
            "git_status": self.git_status,
            "git_diff": self.git_diff,
            "apply_patch": self.apply_patch,
        }
        handler = handlers.get(name)
        if handler is None:
            raise ToolExecutionError(f"未知工具：{name}")
        self._check_permission(name)
        return handler(arguments)

    def list_specs(self) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "read_only": spec.read_only,
                "supports_parallel": spec.supports_parallel,
                "permission": spec.permission,
                "schema": spec.schema,
            }
            for spec in TOOL_SPECS.values()
        ]

    def supports_parallel(self, name: str) -> bool:
        spec = TOOL_SPECS.get(name)
        return bool(spec and spec.supports_parallel and spec.read_only)

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
        for current_root, dirnames, filenames in os.walk(root):
            current = Path(current_root)
            # os.walk 支持原地剪枝，避免进入 .git、上游源码缓存和输出目录这类大目录。
            dirnames[:] = [
                dirname
                for dirname in dirnames
                if not self._is_ignored(current / dirname)
            ]
            for filename in filenames:
                if len(files) >= limit:
                    break
                path = current / filename
                if self._is_ignored(path):
                    continue
                files.append(self._display(path))
            if len(files) >= limit:
                break
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
            errors="replace",
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

    def git_diff(self, arguments: dict[str, Any]) -> ToolResult:
        path_value = str(arguments.get("path") or "").strip()
        max_bytes = int(arguments.get("max_bytes") or 24000)
        command = ["git", "-c", "core.quotepath=false", "diff", "--"]
        title = "读取 Git diff"
        if path_value:
            path = self._resolve_workspace_path(path_value)
            command.append(self._display(path))
            title = f"读取 Git diff：{self._display(path)}"
        result = subprocess.run(
            command,
            cwd=self.project_root,
            text=True,
            capture_output=True,
            timeout=10,
        )
        output = (result.stdout or result.stderr or "当前没有 diff")[:max_bytes]
        return ToolResult(
            tool="git_diff",
            title=title,
            output=output,
            ok=result.returncode == 0,
            data={"exit_code": result.returncode},
        )

    def apply_patch(self, arguments: dict[str, Any]) -> ToolResult:
        patch_text = str(arguments.get("patch") or "")
        if not patch_text.strip():
            raise ToolExecutionError("apply_patch 需要 patch")
        self._validate_patch_paths(patch_text)
        check = subprocess.run(
            ["git", "apply", "--check", "-"],
            cwd=self.project_root,
            input=patch_text,
            text=True,
            capture_output=True,
            timeout=10,
        )
        if check.returncode != 0:
            raise ToolExecutionError(check.stderr.strip() or "补丁校验失败")
        result = subprocess.run(
            ["git", "apply", "-"],
            cwd=self.project_root,
            input=patch_text,
            text=True,
            capture_output=True,
            timeout=10,
        )
        return ToolResult(
            tool="apply_patch",
            title="应用补丁",
            output=result.stdout.strip() or result.stderr.strip() or "补丁已应用",
            ok=result.returncode == 0,
            data={"exit_code": result.returncode},
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
        try:
            first_token = shlex.split(command)[0]
        except (IndexError, ValueError):
            return False
        if first_token in {"rm", "sudo", "chmod", "chown", "mkfs", "dd"}:
            return False
        if first_token.lower() in {"powershell.exe", "powershell", "pwsh", "pwsh.exe"}:
            return self._is_allowed_powershell_command(lowered)
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

    def _is_allowed_powershell_command(self, lowered: str) -> bool:
        return (
            "netsh wlan show" in lowered
            and " key=clear" in lowered
            and not any(token in lowered for token in ["remove-", "set-", "new-", "invoke-", "iex", "downloadstring"])
        )

    def _check_permission(self, name: str) -> None:
        spec = TOOL_SPECS.get(name)
        if spec is None or spec.permission == "read":
            return
        if self.permission_mode == "read_only":
            raise ToolExecutionError(f"当前权限模式为 read_only，禁止执行 {name}")
        if self.permission_mode == "ask":
            raise ToolExecutionError(f"{name} 需要用户审批；当前版本尚未实现前端审批确认")

    def _validate_patch_paths(self, patch_text: str) -> None:
        for line in patch_text.splitlines():
            if not (line.startswith("+++ ") or line.startswith("--- ")):
                continue
            raw = line[4:].strip()
            if raw == "/dev/null":
                continue
            if raw.startswith("a/") or raw.startswith("b/"):
                raw = raw[2:]
            self._resolve_workspace_path(raw)


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


def tool_specs_as_jsonable() -> list[dict[str, Any]]:
    return WorkspaceTools(Path.cwd()).list_specs()
