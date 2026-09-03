from __future__ import annotations

import difflib
import fnmatch
import hashlib
import json
import os
import re
import shlex
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..memory import ProjectMemory
from ..mcp import McpManager
from .web_search import ZaiWebSearchError, run_zai_web_search


class ToolExecutionError(RuntimeError):
    """工具执行失败时抛出，外层会把错误作为模型可读的工具结果。

    code 对照 dsh 的结构化错误词汇（errors.py 常量）：未知工具、权限拒绝
    等可路由失败需要机器可读的码，而不是让下游解析中文 message。
    """

    def __init__(self, message: str, *, code: str = "TOOL_ERROR") -> None:
        super().__init__(message)
        self.code = code


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
    category: str = "general"
    risk: str = "low"
    interrupt_behavior: str = "block"
    hooks_enabled: bool = True
    model_visible: bool = True
    # 受支持 JSON Schema 子集声明的参数契约（dsh defineTool.parameters 的镜像）。
    # 执行器在 hook/权限之后、body 之前按它验证，违反即 INVALID_ARGS 且 body 不执行。
    json_schema: dict[str, Any] | None = None
    # 协作式超时预算（毫秒）。dsh 语义：registry 层强制、绝不发给模型。
    timeout_ms: int = 30000



TOOL_SPECS: dict[str, ToolSpec] = {
    "read": ToolSpec(
        name="read",
        description="Read a UTF-8 text file and return line-numbered content.",
        read_only=True,
        supports_parallel=True,
        permission="read",
        schema={"file_path": "src/app.py", "offset": 1, "limit": 200},
        category="filesystem",
        risk="low",
        interrupt_behavior="cancel",
    ),
    "write": ToolSpec(
        name="write",
        description="Create or fully replace a UTF-8 text file.",
        read_only=False,
        supports_parallel=False,
        permission="write",
        schema={"file_path": "src/new.py", "content": "完整文件内容"},
        category="filesystem",
        risk="medium",
    ),
    "edit": ToolSpec(
        name="edit",
        description="Replace literal old_string with new_string in an existing UTF-8 text file. By default old_string must appear exactly once.",
        read_only=False,
        supports_parallel=False,
        permission="write",
        schema={"file_path": "src/app.py", "old_string": "原文", "new_string": "新文", "replace_all": False},
        category="filesystem",
        risk="medium",
    ),
    "glob": ToolSpec(
        name="glob",
        description="Find files whose paths match a glob pattern. Returns matching file paths — never directories.",
        read_only=True,
        supports_parallel=True,
        permission="read",
        schema={"pattern": "**/*.py", "path": "."},
        category="filesystem",
        risk="low",
        interrupt_behavior="cancel",
    ),
    "grep": ToolSpec(
        name="grep",
        description="Search file contents with a regular expression. Returns matching lines with line numbers, grouped by file.",
        read_only=True,
        supports_parallel=True,
        permission="read",
        schema={"pattern": "正则表达式", "path": ".", "include": "*.py"},
        category="filesystem",
        risk="low",
        interrupt_behavior="cancel",
    ),
    "bash": ToolSpec(
        name="bash",
        description="Execute a bash command in the session workspace with a timeout, returning stdout, a marked stderr section, and exit-status markers.",
        read_only=False,
        supports_parallel=False,
        permission="shell",
        schema={"command": "ls -la", "description": "List files in current directory", "timeoutMs": 10000, "workdir": "."},
        category="shell",
        risk="medium",
    ),
    "todo_write": ToolSpec(
        name="todo_write",
        description="Record the COMPLETE task list, replacing any previous list.",
        read_only=False,
        supports_parallel=False,
        permission="write",
        schema={"todos": [{"content": "任务", "status": "pending"}]},
        category="planning",
        risk="low",
    ),
    "web_fetch": ToolSpec(
        name="web_fetch",
        description="Fetch an HTTP(S) URL and return the response body as text.",
        read_only=True,
        supports_parallel=True,
        permission="network",
        schema={"url": "https://example.com"},
        category="web",
        risk="low",
        interrupt_behavior="cancel",
    ),
    "web_search": ToolSpec(
        name="web_search",
        description="Search the web for current information. Returns structured sources.",
        read_only=True,
        supports_parallel=True,
        permission="network",
        schema={"query": "搜索词"},
        category="web",
        risk="low",
        interrupt_behavior="cancel",
    ),
    "memory_write": ToolSpec(
        name="memory_write",
        description="Save or update ONE durable memory entry (layered memory: index in prompt, details on disk).",
        read_only=False,
        supports_parallel=False,
        permission="write",
        schema={"scope": "project", "title": "条目标题", "content": "完整正文", "id": "可选显式 id"},
        category="memory",
        risk="medium",
    ),
    "memory_remove": ToolSpec(
        name="memory_remove",
        description="Delete ONE durable memory entry by its id from the index.",
        read_only=False,
        supports_parallel=False,
        permission="write",
        schema={"scope": "project", "id": "条目 id"},
        category="memory",
        risk="medium",
    ),
}

# ---------------------------------------------------------------------------
# 参数契约（dsh defineTool parameters 的逐字对齐：字段名、必填、enum、
# additionalProperties: false 全闭合）。
# ---------------------------------------------------------------------------
_TOOL_CONTRACTS: dict[str, dict[str, Any]] = {
    "read": {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Path to read, resolved by the filesystem backend."},
            "offset": {"type": "integer", "description": "1-based first line to return. Defaults to 1."},
            "limit": {"type": "integer", "description": "Maximum number of lines to return. Defaults to 2000."},
        },
        "required": ["file_path"],
        "additionalProperties": False,
    },
    "write": {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Path to write, resolved by the filesystem backend."},
            "content": {"type": "string", "description": "Full UTF-8 text content to write."},
        },
        "required": ["file_path", "content"],
        "additionalProperties": False,
    },
    "edit": {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Path to edit, resolved by the filesystem backend."},
            "old_string": {"type": "string", "description": "Literal text to replace. Must match exactly."},
            "new_string": {"type": "string", "description": "Literal replacement text. Use an empty string to delete the match."},
            "replace_all": {"type": "boolean", "description": "Replace all matches. Defaults to false; when false, old_string must appear exactly once."},
        },
        "required": ["file_path", "old_string", "new_string"],
        "additionalProperties": False,
    },
    "glob": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern to match file paths against (e.g. \"**/*.ts\", \"src/**/*.test.js\")."},
            "path": {"type": "string", "description": "Directory to search in. Defaults to the session workspace; a relative path resolves against it."},
        },
        "required": ["pattern"],
        "additionalProperties": False,
    },
    "grep": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regular expression to search for."},
            "path": {"type": "string", "description": "File or directory to search. Defaults to the session workspace; a relative path resolves against it."},
            "include": {"type": "string", "description": "One glob filter for which files to search (e.g. \"*.ts\", \"*.{js,jsx}\"). Not a list; negation is not supported."},
        },
        "required": ["pattern"],
        "additionalProperties": False,
    },
    "bash": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The bash command to execute."},
            "description": {"type": "string", "description": "Clear, concise description of what this command does in active voice, 5-10 words (shown in the UI)."},
            "timeoutMs": {"type": "integer", "description": "Timeout in milliseconds. The executor applies its configured default and cap, and kills the command on expiry."},
            "workdir": {"type": "string", "description": "Working directory for this command. Defaults to the session workspace; a relative path is resolved against it."},
            "run_in_background": {"type": "boolean", "description": "Run in the background and return a job id immediately. No timeout applies."},
        },
        "required": ["command", "description"],
        "additionalProperties": False,
    },
    "todo_write": {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "The COMPLETE task list, replacing any previous list.",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "What the task is — a short imperative line."},
                        "status": {"type": "string", "enum": ["pending", "in_progress", "completed"], "description": "pending (not started) | in_progress (now) | completed (done)."},
                    },
                    "required": ["content", "status"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["todos"],
        "additionalProperties": False,
    },
    "web_fetch": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The HTTP(S) URL to fetch."},
        },
        "required": ["url"],
        "additionalProperties": False,
    },
    "web_search": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query."},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    "memory_write": {
        "type": "object",
        "properties": {
            "scope": {"type": "string", "enum": ["global", "project"], "description": "Which memory scope: `global` (every project) or `project` (current workspace)."},
            "title": {"type": "string", "description": "Short label for the index, e.g. \"Prefers concise Chinese replies\"."},
            "content": {"type": "string", "description": "The COMPLETE detail text for this entry, replacing its previous body."},
            "id": {"type": "string", "description": "Optional explicit id of an existing entry to update in place."},
        },
        "required": ["scope", "title", "content"],
        "additionalProperties": False,
    },
    "memory_remove": {
        "type": "object",
        "properties": {
            "scope": {"type": "string", "enum": ["global", "project"], "description": "Which memory scope: `global` (every project) or `project` (current workspace)."},
            "id": {"type": "string", "description": "The entry id exactly as shown in the index."},
        },
        "required": ["scope", "id"],
        "additionalProperties": False,
    },
}

# 每工具超时预算（毫秒）：读类 30s；bash 钳 120s；网络 60s；todo 10s。
_TOOL_TIMEOUTS: dict[str, int] = {
    "read": 30000,
    "write": 30000,
    "edit": 30000,
    "glob": 30000,
    "grep": 30000,
    "bash": 120000,
    "todo_write": 10000,
    "web_fetch": 60000,
    "web_search": 60000,
    "memory_write": 30000,
    "memory_remove": 30000,
}

# read 工具的渲染上限（dsh read-render 同值）
READ_LIMIT = 2000
READ_MAX_LINE_LENGTH = 2000
READ_MAX_BYTES = 50 * 1024
# glob/grep 的内联上限与落盘阈值（dsh fs-search 同语义）
GLOB_MAX_RESULTS = 100
GREP_MAX_MATCHES = 250
# bash 输出单流保留上限（尾部保留，dsh 同语义）
BASH_MAX_STREAM_CHARS = 30000

# 注册期静态校验 + 合约挂载
from dataclasses import replace as _dc_replace  # noqa: E402

from ..memory import layered  # noqa: E402
from .validation import assert_supported_schema as _assert_schema  # noqa: E402

for _name, _schema in _TOOL_CONTRACTS.items():
    _assert_schema(_schema, f"TOOL_CONTRACTS.{_name}")

TOOL_SPECS = {
    _name: _dc_replace(
        spec,
        json_schema=_TOOL_CONTRACTS.get(_name),
        timeout_ms=_TOOL_TIMEOUTS.get(_name, 30000),
    )
    for _name, spec in TOOL_SPECS.items()
}


class WorkspaceTools:
    def __init__(
        self,
        project_root: Path,
        *,
        permission_mode: str = "workspace_write",
        sandbox_mode: str | None = None,
        network_access: bool = False,
        zai_api_key: str | None = None,
        web_search_client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.permission_mode = permission_mode
        self.sandbox_mode = sandbox_mode or ("read_only" if permission_mode == "read_only" else "workspace_write")
        self.network_access = network_access
        self.zai_api_key = zai_api_key
        self.web_search_client_factory = web_search_client_factory
        self._file_snapshots: dict[str, dict[str, Any]] = {}

    def run(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        if name.startswith("mcp__"):
            return self.mcp_tool(name, arguments)
        handlers = {
            "read": self.read,
            "write": self.write,
            "edit": self.edit,
            "glob": self.glob,
            "grep": self.grep,
            "bash": self.bash,
            "todo_write": self.todo_write,
            "web_fetch": self.web_fetch,
            "web_search": self.web_search,
            "memory_write": self.memory_write,
            "memory_remove": self.memory_remove,
        }
        handler = handlers.get(name)
        if handler is None:
            raise ToolExecutionError(f"unknown tool \"{name}\"", code="UNKNOWN_TOOL")
        self._check_permission(name)
        return handler(arguments)

    def _ui_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        """UI/提示词展示用的参数示例（浅拷贝，原样展示）。"""
        return dict(schema)

    def list_specs(self, *, include_internal: bool = False) -> list[dict[str, Any]]:
        local_specs = [
            {
                "name": spec.name,
                "description": spec.description,
                "read_only": spec.read_only,
                "supports_parallel": spec.supports_parallel,
                "permission": spec.permission,
                "schema": self._ui_schema(spec.schema),
                "category": spec.category,
                "risk": spec.risk,
                "interrupt_behavior": spec.interrupt_behavior,
                "hooks_enabled": spec.hooks_enabled,
            }
            for spec in TOOL_SPECS.values()
            if include_internal or spec.model_visible
        ]
        return [*local_specs, *McpManager(self.project_root).list_tool_specs()]

    def supports_parallel(self, name: str) -> bool:
        if name.startswith("mcp__"):
            return any(item["name"] == name and item["supports_parallel"] for item in self.list_specs())
        spec = TOOL_SPECS.get(name)
        return bool(spec and spec.supports_parallel and spec.read_only)

    def mcp_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        payload = McpManager(self.project_root).call_tool(name, arguments)
        return ToolResult(
            tool=name,
            title=f"MCP {payload['server']}:{name}",
            output=str(payload["output"]),
            ok=bool(payload["ok"]),
            data=payload["data"],
        )

    # ------------------------------------------------------------------
    # dsh 对齐的 11 个工具实现。返回形态对齐 dsh render：read 是行号信封，
    # bash 是 stdout + [stderr] 段 + 退出标记（非零退出不是工具失败）。
    # ------------------------------------------------------------------

    def read(self, arguments: dict[str, Any]) -> ToolResult:
        """read（dsh tool-fs/read）：行号窗口 + 续读页脚 + 行长/字节上限。"""
        raw_path = str(arguments.get("file_path") or "").strip()
        if not raw_path:
            raise ToolExecutionError("file_path must be a non-empty string")
        offset = arguments.get("offset")
        offset = 1 if offset is None else int(offset)
        if offset < 1:
            raise ToolExecutionError("offset must be a positive integer")
        limit = arguments.get("limit")
        limit = READ_LIMIT if limit is None else int(limit)
        if limit < 1 or limit > READ_LIMIT:
            raise ToolExecutionError(f"limit must be less than or equal to {READ_LIMIT}")

        path = self._resolve_read_path(raw_path)
        if not path.is_file():
            raise ToolExecutionError(f"文件不存在：{self._display(path)}")
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ToolExecutionError(f"读取失败：{exc}") from exc
        revision = self._remember_file_snapshot(path)

        all_lines = text.splitlines()
        total_lines = len(all_lines)
        selected = all_lines[offset - 1 : offset - 1 + limit]

        rendered_lines: list[str] = []
        byte_budget = READ_MAX_BYTES
        truncated_by_bytes = False
        for index, line in enumerate(selected):
            if len(line) > READ_MAX_LINE_LENGTH:
                line = f"{line[:READ_MAX_LINE_LENGTH]}... (line truncated to {READ_MAX_LINE_LENGTH} chars)"
            encoded_len = len(line.encode("utf-8")) + 1
            if byte_budget - encoded_len < 0:
                truncated_by_bytes = True
                break
            byte_budget -= encoded_len
            rendered_lines.append(f"{offset + index}: {line}")

        end_line = offset + len(rendered_lines) - 1 if rendered_lines else max(0, offset - 1)
        if truncated_by_bytes:
            footer = f"(Output capped. Showing lines {offset}-{end_line}. Use offset={end_line + 1} to continue.)"
        elif end_line < total_lines:
            footer = f"(Showing lines {offset}-{end_line} of {total_lines}. Use offset={end_line + 1} to continue.)"
        else:
            footer = f"(End of file - total {total_lines} lines)"

        body = "\n".join(rendered_lines) + ("\n\n" + footer if rendered_lines else footer)
        display = self._display(path)
        output = f"<path>{display}</path>\n<type>file</type>\n<content>\n{body}\n</content>"
        return ToolResult(
            tool="read",
            title=f"读取 {display}",
            output=output,
            data={
                "path": display,
                "offset": offset,
                "totalLines": total_lines,
                "truncatedByBytes": truncated_by_bytes,
                "file_revision": revision["sha256"],
            },
        )

    def _resolve_read_path(self, value: str) -> Path:
        """read 允许工作区内路径 + 两个记忆 scope 目录（dsh fs 后端同时
        服务工作区与 memory 目录；模型要按索引读 items/<id>.md）。"""
        candidate = Path(value)
        if not candidate.is_absolute():
            resolved = (self.project_root / candidate).resolve()
        else:
            resolved = candidate.resolve()
        for allowed_root in (self.project_root, layered.project_dir(self.project_root), layered.global_dir()):
            if resolved == allowed_root or allowed_root in resolved.parents:
                return resolved
        raise ToolExecutionError(
            f"路径超出允许范围（工作区或记忆目录）：{value}", code="PERMISSION_DENIED"
        )

    @staticmethod
    def _diff_preview(display_path: str, before: str, after: str) -> dict[str, Any]:
        """UI 工具卡的 diff 预览元数据（dsh 返回文案不含此字段，仅事件 data 用）。"""
        import difflib

        before_lines = before.splitlines()
        after_lines = after.splitlines()
        diff = list(difflib.unified_diff(before_lines, after_lines, lineterm="", n=1))
        additions = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
        deletions = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
        preview_lines = [line for line in diff if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
        return {
            "files": [display_path],
            "additions": additions,
            "deletions": deletions,
            "preview": "\n".join(preview_lines[:20]),
        }

    def write(self, arguments: dict[str, Any]) -> ToolResult:
        """write（dsh tool-fs/write）：整文件创建/替换；空 content 合法。"""
        raw_path = str(arguments.get("file_path") or "").strip()
        if not raw_path:
            raise ToolExecutionError("file_path must be a non-empty string")
        content = str(arguments.get("content") or "")
        path = self._resolve_workspace_path(raw_path)
        if self._is_protected(path):
            raise ToolExecutionError(f"受保护路径拒绝写入：{self._display(path)}", code="PERMISSION_DENIED")
        existed = path.is_file()
        before = path.read_text(encoding="utf-8", errors="replace") if existed else ""
        if existed:
            self._reject_if_file_changed_since_read(path)
        operation = "update" if existed else "create"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        revision = self._remember_file_snapshot(path)
        return ToolResult(
            tool="write",
            title=f"{'覆盖' if existed else '新建'} {self._display(path)}",
            output=f"File {operation}d: {self._display(path)} ({len(content.encode('utf-8'))} bytes)",
            data={
                "path": self._display(path),
                "operation": operation,
                "bytes": len(content.encode("utf-8")),
                "file_revision": revision["sha256"],
                "diff": self._diff_preview(self._display(path), before, content),
            },
        )

    def edit(self, arguments: dict[str, Any]) -> ToolResult:
        """edit（dsh tool-fs/edit）：old 唯一性强制 + replace_all + 空新串=删除。"""
        raw_path = str(arguments.get("file_path") or "").strip()
        if not raw_path:
            raise ToolExecutionError("file_path must be a non-empty string")
        old_string = str(arguments.get("old_string") or "")
        new_string = str(arguments.get("new_string") or "")
        if not old_string:
            raise ToolExecutionError("old_string must be a non-empty string")
        if old_string == new_string:
            raise ToolExecutionError("old_string and new_string must differ")
        replace_all = bool(arguments.get("replace_all") or False)

        path = self._resolve_workspace_path(raw_path)
        if not path.is_file():
            raise ToolExecutionError(f"文件不存在：{self._display(path)}")
        if self._is_protected(path):
            raise ToolExecutionError(f"受保护路径拒绝写入：{self._display(path)}", code="PERMISSION_DENIED")
        self._reject_if_file_changed_since_read(path)
        before = path.read_text(encoding="utf-8", errors="replace")
        occurrences = before.count(old_string)
        if occurrences == 0:
            raise ToolExecutionError(f"old_string not found in {self._display(path)}")
        if occurrences > 1 and not replace_all:
            line_numbers = []
            search_from = 0
            while True:
                index = before.find(old_string, search_from)
                if index < 0:
                    break
                line_numbers.append(before.count("\n", 0, index) + 1)
                search_from = index + 1
            raise ToolExecutionError(
                f"No replacement was performed. Multiple occurrences of old_string in lines [{', '.join(map(str, line_numbers))}]. "
                "Please ensure it is unique or set replace_all to true"
            )
        after = before.replace(old_string, new_string) if replace_all else before.replace(old_string, new_string, 1)
        path.write_text(after, encoding="utf-8")
        revision = self._remember_file_snapshot(path)
        return ToolResult(
            tool="edit",
            title=f"编辑 {self._display(path)}",
            output=(
                f"Replaced {occurrences if replace_all else 1} occurrence(s) in {self._display(path)}"
            ),
            data={
                "path": self._display(path),
                "replacements": occurrences if replace_all else 1,
                "replace_all": replace_all,
                "file_revision": revision["sha256"],
                "diff": self._diff_preview(self._display(path), before, after),
            },
        )

    @staticmethod
    def _glob_regex(pattern: str) -> "re.Pattern[str]":
        """标准 glob 语义翻译：`**/` 跨目录（可选）、`**` 任意、`*`/`?`
        不跨目录分隔符。`**/*.py` 同时命中根级与嵌套 .py。"""
        out = ["(?s:"]
        i = 0
        while i < len(pattern):
            ch = pattern[i]
            if ch == "*":
                if pattern.startswith("**/", i):
                    out.append("(?:.*/)?")
                    i += 3
                elif pattern.startswith("**", i):
                    out.append(".*")
                    i += 2
                else:
                    out.append("[^/]*")
                    i += 1
            elif ch == "?":
                out.append("[^/]")
                i += 1
            else:
                out.append(re.escape(ch))
                i += 1
        out.append(")\Z")
        return re.compile("".join(out))

    def glob(self, arguments: dict[str, Any]) -> ToolResult:
        """glob（dsh fs-search/glob）：只回文件路径（永不回目录），含隐藏
        与被忽略文件，排除 VCS 元数据目录；修改时间序；超 100 条落盘报路径。"""
        pattern = str(arguments.get("pattern") or "").strip()
        if not pattern:
            raise ToolExecutionError("glob 需要 pattern")
        root = self._resolve_workspace_path(str(arguments.get("path") or "."))
        if not root.exists():
            raise ToolExecutionError(f"路径不存在：{self._display(root)}")

        regex = self._glob_regex(pattern)
        vcs_dirs = {".git", ".hg", ".svn"}
        matches: list[tuple[float, Path]] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in vcs_dirs]
            for filename in filenames:
                file_path = Path(dirpath) / filename
                rel = file_path.relative_to(root).as_posix()
                if regex.match(rel) or regex.match(filename):
                    try:
                        matches.append((file_path.stat().st_mtime, file_path))
                    except OSError:
                        continue
        matches.sort(key=lambda item: item[0], reverse=True)
        paths = [self._display(file_path) for _, file_path in matches]

        data: dict[str, Any] = {"root": self._display(root), "count": len(paths)}
        if len(paths) > GLOB_MAX_RESULTS:
            spill = self._spill_file("glob", "\n".join(paths) + "\n")
            shown = paths[:GLOB_MAX_RESULTS]
            data["spillPath"] = str(spill)
            output = (
                "\n".join(shown)
                + f"\n\n(Showing first {GLOB_MAX_RESULTS} of {len(paths)} matches in modification-time order; "
                + f"complete sorted list saved at {spill})"
            )
        else:
            output = "\n".join(paths) if paths else "(no matches)"
        return ToolResult(tool="glob", title=f"glob {pattern}", output=output, data=data)

    def grep(self, arguments: dict[str, Any]) -> ToolResult:
        """grep（dsh fs-search/grep）：按文件分组的行号匹配；超 250 条落盘。"""
        pattern = str(arguments.get("pattern") or "").strip()
        if not pattern:
            raise ToolExecutionError("grep 需要 pattern")
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise ToolExecutionError(f"无效正则表达式：{exc}") from exc
        root = self._resolve_workspace_path(str(arguments.get("path") or "."))
        if not root.exists():
            raise ToolExecutionError(f"路径不存在：{self._display(root)}")
        include = str(arguments.get("include") or "").strip() or None
        include_regex = re.compile(fnmatch.translate(include)) if include else None

        search_files = [root] if root.is_file() else []
        if not search_files:
            vcs_dirs = {".git", ".hg", ".svn"}
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in vcs_dirs]
                for filename in filenames:
                    if include_regex is None or include_regex.match(filename):
                        search_files.append(Path(dirpath) / filename)

        groups: list[str] = []
        total = 0
        truncated = False
        for file_path in search_files:
            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            hit_lines: list[str] = []
            for number, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    total += 1
                    if total <= GREP_MAX_MATCHES:
                        hit_lines.append(f"{number}: {line.strip()[:300]}")
                    else:
                        truncated = True
            if hit_lines:
                groups.append(f"{self._display(file_path)}:\n" + "\n".join(hit_lines))

        data: dict[str, Any] = {"pattern": pattern, "matches": total}
        if truncated:
            spill = self._spill_file("grep", f"pattern: {pattern}\n\n" + "\n\n".join(groups))
            data["spillPath"] = str(spill)
            output = "\n".join(groups) + f"\n\n(Showing first {GREP_MAX_MATCHES} matches; full match list saved at {spill})"
        else:
            output = "\n".join(groups) if groups else "(no matches)"
        return ToolResult(tool="grep", title=f"grep {pattern}", output=output, data=data)

    def bash(self, arguments: dict[str, Any]) -> ToolResult:
        """bash（dsh tool-bash 同步路径）：stdout + [stderr] 段 + 退出标记。

        非零退出不是工具失败（ok=True，模型读 [exit code: N] 自行决策）；
        只有基础设施失败（spawn 失败）才是错误。长输出尾部保留 + 全量落盘。
        """
        command = str(arguments.get("command") or "").strip()
        if not command:
            raise ToolExecutionError("bash 需要 command")
        risk = self.shell_command_risk(command)
        if risk["blocked"]:
            raise ToolExecutionError(f"命令命中黑名单，拒绝执行：{risk['reason']}：{command}", code="PERMISSION_DENIED")
        workdir = self._resolve_workspace_path(str(arguments.get("workdir") or "."))
        timeout_ms = min(int(arguments.get("timeoutMs") or 30000), 120000)
        try:
            completed = subprocess.run(
                command,
                cwd=workdir,
                shell=True,
                text=True,
                errors="replace",
                capture_output=True,
                timeout=timeout_ms / 1000,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
            out_text, out_trunc, out_spill = self._tail_clip(stdout)
            err_text, err_trunc, err_spill = self._tail_clip(stderr)
            body = self._render_bash_body(out_text, err_text, out_trunc, err_trunc, out_spill, err_spill)
            marker = f"[timed out after {timeout_ms}ms]"
            output = (body + "\n" if body and not body.endswith("\n") else body) + marker
            return ToolResult(
                tool="bash",
                title=str(arguments.get("description") or command)[:80],
                output=output,
                ok=True,
                data={"timedOut": True, "timeoutMs": timeout_ms, "workdir": self._display(workdir)},
            )
        except OSError as exc:
            raise ToolExecutionError(f"命令启动失败：{exc}") from exc

        out_text, out_trunc, out_spill = self._tail_clip(completed.stdout or "")
        err_text, err_trunc, err_spill = self._tail_clip(completed.stderr or "")
        body = self._render_bash_body(out_text, err_text, out_trunc, err_trunc, out_spill, err_spill)
        markers: list[str] = []
        if completed.returncode != 0:
            markers.append(f"[exit code: {completed.returncode}]")
        if markers:
            output = (body + "\n" if body and not body.endswith("\n") else body) + "\n".join(markers)
        else:
            output = body
        return ToolResult(
            tool="bash",
            title=str(arguments.get("description") or command)[:80],
            output=output,
            ok=True,
            data={
                "exitCode": completed.returncode,
                "timedOut": False,
                "timeoutMs": timeout_ms,
                "workdir": self._display(workdir),
                "stdoutTruncated": out_trunc,
                "stderrTruncated": err_trunc,
                **({"stdoutSpillPath": str(out_spill)} if out_spill else {}),
                **({"stderrSpillPath": str(err_spill)} if err_spill else {}),
            },
        )

    def _tail_clip(self, text: str) -> tuple[str, bool, Path | None]:
        """bash 输出尾部保留 + 全量落盘（dsh 语义：截断保最新，报 spill 路径）。"""
        if len(text) <= BASH_MAX_STREAM_CHARS:
            return text, False, None
        spill = self._spill_file("bash", text)
        return text[-BASH_MAX_STREAM_CHARS:], True, spill

    def _render_bash_body(
        self,
        stdout_text: str,
        stderr_text: str,
        stdout_truncated: bool,
        stderr_truncated: bool,
        stdout_spill: Path | None,
        stderr_spill: Path | None,
    ) -> str:
        def with_notice(text: str, truncated: bool, spill: Path | None) -> str:
            if not truncated:
                return text
            return f"{text}\n[output truncated; full output: {spill if spill else '(unavailable)'}]"

        out = with_notice(stdout_text.rstrip("\n"), stdout_truncated, stdout_spill)
        err = with_notice(stderr_text.rstrip("\n"), stderr_truncated, stderr_spill)
        body = out
        if err:
            if body and not body.endswith("\n"):
                body += "\n"
            body += f"[stderr]\n{err}"
        return body or "(no output)"

    def _spill_file(self, prefix: str, content: str) -> Path:
        """超限结果的完整落盘文件（dsh spill 同语义）。"""
        import time as _time

        spill_dir = self.project_root / ".nova" / "spill"
        spill_dir.mkdir(parents=True, exist_ok=True)
        path = spill_dir / f"{prefix}-{int(_time.time() * 1000)}.log"
        path.write_text(content, encoding="utf-8", errors="replace")
        return path

    def todo_write(self, arguments: dict[str, Any]) -> ToolResult:
        """todo_write（dsh tool-todo）：COMPLETE 清单整替。"""
        todos = arguments.get("todos")
        if not isinstance(todos, list):
            raise ToolExecutionError("todo_write 需要 todos 数组")
        normalized = []
        for item in todos:
            if not isinstance(item, dict):
                raise ToolExecutionError("todos 每项必须是对象")
            content = str(item.get("content") or "").strip()
            status = str(item.get("status") or "").strip()
            if not content or status not in {"pending", "in_progress", "completed"}:
                raise ToolExecutionError("todos 每项需要 content 与 status(pending|in_progress|completed)")
            normalized.append({"content": content, "status": status})
        state_path = self.project_root / ".nova" / "agent-todos.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({"items": normalized}, ensure_ascii=False, indent=2), encoding="utf-8")
        preview = "; ".join(f"[{item['status']}] {item['content']}" for item in normalized[:6])
        return ToolResult(
            tool="todo_write",
            title="更新任务清单",
            output=f"Task list updated ({len(normalized)} items): {preview}",
            data={"count": len(normalized)},
        )

    def web_fetch(self, arguments: dict[str, Any]) -> ToolResult:
        if not self.network_access:
            raise ToolExecutionError("当前网络访问关闭，禁止执行 web_fetch")
        url = str(arguments.get("url") or "").strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ToolExecutionError("web_fetch 只支持 http/https URL")
        max_bytes = min(int(arguments.get("max_bytes") or 20000), 50000)
        request = urllib.request.Request(url, headers={"User-Agent": "Nova-Agent/0.1"})
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - 受 network_access 控制。
            content = response.read(max_bytes).decode("utf-8", errors="replace")
            status = getattr(response, "status", 200)
        return ToolResult(
            tool="web_fetch",
            title=f"抓取 {url}",
            output=content,
            ok=200 <= int(status) < 400,
            data={"url": url, "status": int(status), "bytes": len(content.encode("utf-8"))},
        )

    def web_search(self, arguments: dict[str, Any]) -> ToolResult:
        if not self.network_access:
            raise ToolExecutionError("当前网络访问关闭，禁止执行 web_search")
        try:
            payload = run_zai_web_search(
                arguments,
                api_key=self.zai_api_key,
                client_factory=self.web_search_client_factory,
            )
        except ZaiWebSearchError as exc:
            raise ToolExecutionError(str(exc)) from exc
        query = str(payload.get("query") or arguments.get("query") or "")
        return ToolResult(
            tool="web_search",
            title=f"搜索 {query}",
            output=str(payload.get("output") or ""),
            ok=True,
            data={
                "provider": payload.get("provider"),
                "query": query,
                "request": payload.get("request") if isinstance(payload.get("request"), dict) else {},
                "results": payload.get("results") if isinstance(payload.get("results"), list) else [],
                "raw": payload.get("raw") if isinstance(payload.get("raw"), dict) else {},
            },
        )

    def memory_write(self, arguments: dict[str, Any]) -> ToolResult:
        """memory_write（dsh memory-plugin）：scope/title/content，索引自动重建。"""
        scope = str(arguments.get("scope") or "").strip()
        if scope not in {"global", "project"}:
            raise ToolExecutionError("scope must be `global` or `project`")
        try:
            entry = layered.normalize_entry(arguments)
        except layered.MemoryEntryError as exc:
            raise ToolExecutionError(str(exc), code="INVALID_ARGS") from exc
        dir_path = layered.global_dir() if scope == "global" else layered.project_dir(self.project_root)
        layered.reconcile_index(dir_path)
        existing = layered.read_index(dir_path)
        written = layered.write_entry(
            dir_path,
            entry,
            existing,
            has_explicit_id=bool(str(arguments.get("id") or "").strip()),
        )
        return ToolResult(
            tool="memory_write",
            title=f"Save memory: {entry['title'][:60]}",
            output=(
                f"Memory ({scope}) saved: {written['id']} — {entry['title']} "
                f"({written['total']} entries) → {written['path']}"
            ),
            data={"scope": scope, "id": written["id"], "title": entry["title"], "path": written["path"], "total": written["total"]},
        )

    def memory_remove(self, arguments: dict[str, Any]) -> ToolResult:
        """memory_remove（dsh memory-plugin）：按 id 删除条目。"""
        scope = str(arguments.get("scope") or "").strip()
        if scope not in {"global", "project"}:
            raise ToolExecutionError("scope must be `global` or `project`")
        entry_id = str(arguments.get("id") or "").strip()
        if not entry_id:
            raise ToolExecutionError("memory_remove 需要 id")
        dir_path = layered.global_dir() if scope == "global" else layered.project_dir(self.project_root)
        try:
            removed = layered.remove_entry(dir_path, entry_id)
        except layered.MemoryEntryError as exc:
            raise ToolExecutionError(str(exc), code="INVALID_ARGS") from exc
        return ToolResult(
            tool="memory_remove",
            title=f"Remove memory: {entry_id}",
            output=f"Memory ({scope}) removed: {entry_id} ({removed['total']} entries remain) — was {removed['path']}",
            data={"scope": scope, "id": entry_id, "path": removed["path"], "total": removed["total"]},
        )

    def _resolve_workspace_path(self, value: str) -> Path:
        if not value:
            raise ToolExecutionError("路径不能为空")
        raw = Path(value)
        path = raw if raw.is_absolute() else self.project_root / raw
        resolved = path.resolve()
        if self.sandbox_mode == "danger_full_access":
            return resolved
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
        try:
            return path.resolve().relative_to(self.project_root).as_posix()
        except ValueError:
            return str(path.resolve())

    def _is_allowed_shell_command(self, command: str) -> bool:
        return not bool(self.shell_command_risk(command)["blocked"])

    def shell_command_risk(self, command: str) -> dict[str, Any]:
        command = command.strip()
        if not command:
            return {"risk": "high", "blocked": True, "reason": "空 shell 命令"}
        if ":(){" in command.replace(" ", ""):
            return {"risk": "high", "blocked": True, "reason": "命令命中 shell fork bomb 黑名单"}
        try:
            tokens = self._shell_tokens(command)
        except (IndexError, ValueError):
            return {"risk": "high", "blocked": False, "reason": "命令解析失败，按高风险处理"}
        segments = self._shell_command_segments(tokens)
        if not segments:
            return {"risk": "high", "blocked": True, "reason": "空 shell 命令"}

        reasons: list[str] = []
        highest = "low"
        for segment in segments:
            argv = self._effective_shell_argv(segment)
            if not argv:
                continue
            command_name = Path(argv[0]).name.lower()
            if command_name in {"reboot", "shutdown"}:
                return {"risk": "high", "blocked": True, "reason": f"{command_name} 属于系统级破坏性命令黑名单"}
            if command_name == "rm" and self._rm_targets_filesystem_root(argv):
                return {"risk": "high", "blocked": True, "reason": "rm -rf / 属于破坏性命令黑名单"}

            risk, reason = self._segment_shell_risk(argv)
            if self._risk_rank(risk) > self._risk_rank(highest):
                highest = risk
            if reason:
                reasons.append(reason)

        if self._has_network_download_piped_to_shell(segments):
            highest = "high"
            reasons.append("下载脚本并管道给 shell 执行")

        return {
            "risk": highest,
            "blocked": False,
            "reason": "；".join(dict.fromkeys(reasons)) or "普通命令",
        }

    def _shell_tokens(self, command: str) -> list[str]:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)

    def _shell_command_segments(self, tokens: list[str]) -> list[list[str]]:
        segments: list[list[str]] = []
        current: list[str] = []
        separators = {";", "&&", "||", "|", "(", ")"}
        for token in tokens:
            if token in separators:
                if current:
                    segments.append(current)
                    current = []
                continue
            current.append(token)
        if current:
            segments.append(current)
        return segments

    def _effective_shell_argv(self, segment: list[str]) -> list[str]:
        argv = list(segment)
        while argv and self._is_env_assignment(argv[0]):
            argv.pop(0)
        if argv and Path(argv[0]).name.lower() == "env":
            argv.pop(0)
            while argv and self._is_env_assignment(argv[0]):
                argv.pop(0)
        if argv and Path(argv[0]).name.lower() in {"sudo", "doas"}:
            argv = self._unwrap_privilege_command(argv)
        return argv

    def _unwrap_privilege_command(self, argv: list[str]) -> list[str]:
        rest = argv[1:]
        index = 0
        while index < len(rest) and rest[index].startswith("-"):
            option = rest[index]
            index += 1
            if option in {"-u", "-g", "-h", "-p"} and index < len(rest):
                index += 1
        return rest[index:] or argv

    def _is_env_assignment(self, token: str) -> bool:
        return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", token))

    def _rm_targets_filesystem_root(self, argv: list[str]) -> bool:
        recursive = False
        force = False
        targets: list[str] = []
        for arg in argv[1:]:
            if arg == "--":
                continue
            if arg.startswith("--"):
                recursive = recursive or arg in {"--recursive", "--dir"}
                force = force or arg == "--force"
                continue
            if arg.startswith("-") and len(arg) > 1:
                recursive = recursive or "r" in arg.lower() or "R" in arg
                force = force or "f" in arg.lower()
                continue
            targets.append(arg)
        root_targets = {"/", "//", "/.", "/./"}
        return recursive and force and any(target in root_targets or target.startswith("/*") for target in targets)

    def _segment_shell_risk(self, argv: list[str]) -> tuple[str, str]:
        command_name = Path(argv[0]).name.lower()
        subcommand = argv[1].lower() if len(argv) > 1 else ""
        if command_name == "git" and subcommand == "push":
            return "high", "git push 会修改远端仓库"
        if command_name == "rm" and any(self._rm_has_recursive_flag(arg) for arg in argv[1:]):
            return "high", "递归删除文件"
        if command_name in {"sudo", "su", "doas", "chmod", "chown", "dd", "mkfs", "mount", "umount", "systemctl", "service", "iptables", "ufw"}:
            return "high", f"{command_name} 会修改系统、权限或设备状态"
        if command_name in {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}:
            return "high", "PowerShell 命令可能绕过当前 shell 风险识别"
        if command_name in {"npm", "pnpm", "yarn"} and subcommand in {"install", "add", "publish", "link"}:
            return ("high" if subcommand == "publish" else "medium"), f"{command_name} {subcommand} 会修改依赖或发布包"
        if command_name in {"pip", "pip3", "uv"} and subcommand in {"install", "add", "sync"}:
            return "medium", f"{command_name} {subcommand} 会修改 Python 环境"
        if command_name == "cargo" and subcommand == "install":
            return "medium", "cargo install 会安装可执行程序"
        if command_name in {"curl", "wget"}:
            return "medium", f"{command_name} 会访问网络"
        if command_name in {"kill", "pkill", "killall"}:
            return "medium", f"{command_name} 会终止进程"
        return "low", ""

    def _rm_has_recursive_flag(self, arg: str) -> bool:
        if arg in {"--recursive", "--dir"}:
            return True
        return arg.startswith("-") and ("r" in arg.lower() or "R" in arg)

    def _has_network_download_piped_to_shell(self, segments: list[list[str]]) -> bool:
        if len(segments) < 2:
            return False
        downloaders = {"curl", "wget"}
        shells = {"sh", "bash", "zsh", "fish", "dash"}
        for left, right in zip(segments, segments[1:]):
            left_argv = self._effective_shell_argv(left)
            right_argv = self._effective_shell_argv(right)
            if not left_argv or not right_argv:
                continue
            if Path(left_argv[0]).name.lower() in downloaders and Path(right_argv[0]).name.lower() in shells:
                return True
        return False

    def _risk_rank(self, risk: str) -> int:
        return {"low": 0, "medium": 1, "high": 2}.get(risk, 2)

    def _file_snapshot_key(self, path: Path) -> str:
        return str(path.resolve())

    def _file_revision(self, path: Path) -> dict[str, Any]:
        content = path.read_bytes()
        stat = path.stat()
        return {
            "sha256": hashlib.sha256(content).hexdigest(),
            "mtime_ns": stat.st_mtime_ns,
            "size": stat.st_size,
        }

    def _remember_file_snapshot(self, path: Path) -> dict[str, Any]:
        revision = self._file_revision(path)
        self._file_snapshots[self._file_snapshot_key(path)] = revision
        return revision

    def _reject_if_file_changed_since_read(self, path: Path) -> None:
        key = self._file_snapshot_key(path)
        previous = self._file_snapshots.get(key)
        if previous is None:
            return
        current = self._file_revision(path)
        if current["sha256"] == previous.get("sha256"):
            return
        raise ToolExecutionError(
            f"{self._display(path)} 已被外部修改；为避免覆盖用户改动，请先重新读取该文件后再编辑。"
        )

    def _check_permission(self, name: str) -> None:
        spec = TOOL_SPECS.get(name)
        if spec is None or spec.permission == "read":
            return
        if spec.permission == "network" and not self.network_access:
            raise ToolExecutionError(f"当前网络访问关闭，禁止执行 {name}", code="PERMISSION_DENIED")
        if self.sandbox_mode == "read_only" and spec.permission in {"write", "shell"}:
            raise ToolExecutionError(f"当前沙箱模式为 read_only，禁止执行 {name}", code="PERMISSION_DENIED")
        if self.permission_mode == "bypass_permissions":
            return
        if self.permission_mode == "plan" and spec.permission != "read":
            raise ToolExecutionError(f"当前权限模式为 plan，只规划不执行 {name}", code="PERMISSION_DENIED")
        if self.permission_mode == "accept_edits" and spec.permission in {"shell", "network"}:
            raise ToolExecutionError(f"当前权限模式为 accept_edits，禁止自动执行 {name}")
        if self.permission_mode == "dont_ask" and spec.permission != "read":
            raise ToolExecutionError(f"当前权限模式为 dont_ask，未预批准的 {name} 会被拒绝")
        if self.permission_mode == "read_only":
            raise ToolExecutionError(f"当前权限模式为 read_only，禁止执行 {name}")
        if self.permission_mode == "ask":
            raise ToolExecutionError(f"{name} 需要用户审批；当前版本尚未实现前端审批确认")

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
