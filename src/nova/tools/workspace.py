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
    category: str = "general"
    risk: str = "low"
    interrupt_behavior: str = "block"
    hooks_enabled: bool = True
    model_visible: bool = True


TOOL_SPECS: dict[str, ToolSpec] = {
    "read_file": ToolSpec(
        name="read_file",
        description="读取工作区内文件内容。",
        read_only=True,
        supports_parallel=True,
        permission="read",
        schema={"path": "相对路径", "max_bytes": 24000},
        category="filesystem",
        risk="low",
        interrupt_behavior="cancel",
    ),
    "read_many_files": ToolSpec(
        name="read_many_files",
        description="一次读取多个工作区内文件。",
        read_only=True,
        supports_parallel=True,
        permission="read",
        schema={"paths": ["相对路径"], "max_bytes_each": 12000},
        category="filesystem",
        risk="low",
        interrupt_behavior="cancel",
        model_visible=False,
    ),
    "list_files": ToolSpec(
        name="list_files",
        description="列出工作区内文件。",
        read_only=True,
        supports_parallel=True,
        permission="read",
        schema={"path": ".", "limit": 200},
        category="filesystem",
        risk="low",
        interrupt_behavior="cancel",
    ),
    "glob_files": ToolSpec(
        name="glob_files",
        description="按 glob 模式查找工作区文件。",
        read_only=True,
        supports_parallel=True,
        permission="read",
        schema={"pattern": "**/*.py", "path": ".", "limit": 200},
        category="filesystem",
        risk="low",
        interrupt_behavior="cancel",
    ),
    "search_text": ToolSpec(
        name="search_text",
        description="使用 ripgrep 在工作区内搜索固定文本。",
        read_only=True,
        supports_parallel=True,
        permission="read",
        schema={"query": "关键词", "path": ".", "max_results": 80},
        category="search",
        risk="low",
        interrupt_behavior="cancel",
    ),
    "shell_command": ToolSpec(
        name="shell_command",
        description="执行受控白名单 shell 命令。",
        read_only=False,
        supports_parallel=False,
        permission="shell",
        schema={"command": "受控命令", "workdir": ".", "timeout_ms": 10000, "background": False},
        category="shell",
        risk="high",
        interrupt_behavior="cancel",
    ),
    "replace_in_file": ToolSpec(
        name="replace_in_file",
        description="替换文件中的第一处匹配文本。",
        read_only=False,
        supports_parallel=False,
        permission="write",
        schema={"path": "文件", "old": "原文", "new": "新文"},
        category="filesystem",
        risk="medium",
        interrupt_behavior="block",
        model_visible=False,
    ),
    "edit_file": ToolSpec(
        name="edit_file",
        description="replace_in_file 的 Claude/Codex 风格别名，替换文件中的第一处匹配文本。",
        read_only=False,
        supports_parallel=False,
        permission="write",
        schema={"path": "文件", "old": "原文", "new": "新文"},
        category="filesystem",
        risk="medium",
        interrupt_behavior="block",
        model_visible=False,
    ),
    "multi_edit": ToolSpec(
        name="multi_edit",
        description="按顺序对同一文件执行多处文本替换。",
        read_only=False,
        supports_parallel=False,
        permission="write",
        schema={"path": "文件", "edits": [{"old": "原文", "new": "新文"}]},
        category="filesystem",
        risk="high",
        interrupt_behavior="block",
        model_visible=False,
    ),
    "create_file": ToolSpec(
        name="create_file",
        description="创建新文件。",
        read_only=False,
        supports_parallel=False,
        permission="write",
        schema={"path": "文件", "content": "内容"},
        category="filesystem",
        risk="medium",
        interrupt_behavior="block",
        model_visible=False,
    ),
    "write_file": ToolSpec(
        name="write_file",
        description="创建或覆盖写入文件，并在覆盖时返回 diff。",
        read_only=False,
        supports_parallel=False,
        permission="write",
        schema={"path": "文件", "content": "内容"},
        category="filesystem",
        risk="high",
        interrupt_behavior="block",
    ),
    "apply_patch": ToolSpec(
        name="apply_patch",
        description="应用 unified diff 补丁到工作区。",
        read_only=False,
        supports_parallel=False,
        permission="write",
        schema={"patch": "unified diff"},
        category="filesystem",
        risk="high",
        interrupt_behavior="block",
    ),
    "todo_write": ToolSpec(
        name="todo_write",
        description="写入当前 Agent 的任务清单快照。",
        read_only=False,
        supports_parallel=False,
        permission="write",
        schema={"items": [{"content": "任务", "status": "pending|in_progress|completed"}]},
        category="planning",
        risk="low",
        interrupt_behavior="block",
    ),
    "todo_read": ToolSpec(
        name="todo_read",
        description="读取当前 Agent 的任务清单快照。",
        read_only=True,
        supports_parallel=True,
        permission="read",
        schema={},
        category="planning",
        risk="low",
        interrupt_behavior="cancel",
    ),
    "web_fetch": ToolSpec(
        name="web_fetch",
        description="在允许网络访问时抓取 URL 文本。",
        read_only=True,
        supports_parallel=False,
        permission="network",
        schema={"url": "https://example.com", "max_bytes": 20000},
        category="network",
        risk="medium",
        interrupt_behavior="cancel",
    ),
    "web_search": ToolSpec(
        name="web_search",
        description="在允许网络访问时执行网页搜索并返回可读摘要。",
        read_only=True,
        supports_parallel=False,
        permission="network",
        schema={
            "query": "搜索关键词",
            "count": 10,
            "search_engine": "search_pro",
            "search_domain_filter": "可选限定域名",
            "search_recency_filter": "noLimit",
            "content_size": "high",
        },
        category="network",
        risk="medium",
        interrupt_behavior="cancel",
    ),
    "memory_read": ToolSpec(
        name="memory_read",
        description="读取 .nova/memory 中的长期记忆文件。",
        read_only=True,
        supports_parallel=True,
        permission="read",
        schema={"name": "index.md"},
        category="memory",
        risk="low",
        interrupt_behavior="cancel",
    ),
    "memory_write": ToolSpec(
        name="memory_write",
        description="写入 .nova/memory 中的长期记忆文件。",
        read_only=False,
        supports_parallel=False,
        permission="write",
        schema={"name": "index.md", "content": "记忆内容"},
        category="memory",
        risk="medium",
        interrupt_behavior="block",
    ),
    "memory_search": ToolSpec(
        name="memory_search",
        description="搜索 .nova/memory 中的长期记忆。",
        read_only=True,
        supports_parallel=True,
        permission="read",
        schema={"query": "关键词"},
        category="memory",
        risk="low",
        interrupt_behavior="cancel",
    ),
    "memory_summarize": ToolSpec(
        name="memory_summarize",
        description="汇总 .nova/memory 中的长期记忆，生成可续跑摘要。",
        read_only=True,
        supports_parallel=True,
        permission="read",
        schema={"max_chars_per_file": 1200},
        category="memory",
        risk="low",
        interrupt_behavior="cancel",
    ),
    "memory_compact": ToolSpec(
        name="memory_compact",
        description="压缩 .nova/memory 中的长期记忆，并写入 project.md。",
        read_only=False,
        supports_parallel=False,
        permission="write",
        schema={"max_chars": 12000},
        category="memory",
        risk="medium",
        interrupt_behavior="block",
    ),
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
            "read_file": self.read_file,
            "read_many_files": self.read_many_files,
            "list_files": self.list_files,
            "glob_files": self.glob_files,
            "search_text": self.search_text,
            "shell_command": self.shell_command,
            "replace_in_file": self.replace_in_file,
            "edit_file": self.edit_file,
            "multi_edit": self.multi_edit,
            "create_file": self.create_file,
            "write_file": self.write_file,
            "apply_patch": self.apply_patch,
            "todo_read": self.todo_read,
            "todo_write": self.todo_write,
            "web_fetch": self.web_fetch,
            "web_search": self.web_search,
            "memory_read": self.memory_read,
            "memory_write": self.memory_write,
            "memory_search": self.memory_search,
            "memory_summarize": self.memory_summarize,
            "memory_compact": self.memory_compact,
        }
        handler = handlers.get(name)
        if handler is None:
            raise ToolExecutionError(f"未知工具：{name}")
        self._check_permission(name)
        return handler(arguments)

    def list_specs(self, *, include_internal: bool = False) -> list[dict[str, Any]]:
        local_specs = [
            {
                "name": spec.name,
                "description": spec.description,
                "read_only": spec.read_only,
                "supports_parallel": spec.supports_parallel,
                "permission": spec.permission,
                "schema": self._schema_with_annotation(spec.schema),
                "category": spec.category,
                "risk": spec.risk,
                "interrupt_behavior": spec.interrupt_behavior,
                "hooks_enabled": spec.hooks_enabled,
            }
            for spec in TOOL_SPECS.values()
            if include_internal or spec.model_visible
        ]
        return [*local_specs, *McpManager(self.project_root).list_tool_specs()]

    def _schema_with_annotation(self, schema: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(schema)
        enriched.setdefault("annotation", "简短说明这次工具调用要做什么")
        return enriched

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

    def read_file(self, arguments: dict[str, Any]) -> ToolResult:
        path = self._resolve_workspace_path(str(arguments.get("path", "")))
        max_bytes = int(arguments.get("max_bytes") or 24000)
        if not path.is_file():
            raise ToolExecutionError(f"文件不存在：{self._display(path)}")
        content = path.read_bytes()[:max_bytes].decode("utf-8", errors="replace")
        revision = self._remember_file_snapshot(path)
        return ToolResult(
            tool="read_file",
            title=f"读取 {self._display(path)}",
            output=content,
            data={"path": self._display(path), "bytes": len(content.encode("utf-8")), "file_revision": revision["sha256"]},
        )

    def read_many_files(self, arguments: dict[str, Any]) -> ToolResult:
        paths = arguments.get("paths") or []
        if not isinstance(paths, list) or not paths:
            raise ToolExecutionError("read_many_files 需要 paths")
        max_bytes_each = min(int(arguments.get("max_bytes_each") or 12000), 24000)
        sections: list[str] = []
        data: list[dict[str, Any]] = []
        for raw_path in paths[:20]:
            path = self._resolve_workspace_path(str(raw_path))
            if not path.is_file():
                raise ToolExecutionError(f"文件不存在：{self._display(path)}")
            content = path.read_bytes()[:max_bytes_each].decode("utf-8", errors="replace")
            display = self._display(path)
            revision = self._remember_file_snapshot(path)
            sections.append(f"--- {display} ---\n{content}")
            data.append({"path": display, "bytes": len(content.encode("utf-8")), "file_revision": revision["sha256"]})
        return ToolResult(
            tool="read_many_files",
            title=f"读取 {len(data)} 个文件",
            output="\n\n".join(sections),
            data={"files": data},
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

    def glob_files(self, arguments: dict[str, Any]) -> ToolResult:
        pattern = str(arguments.get("pattern") or "").strip()
        if not pattern:
            raise ToolExecutionError("glob_files 需要 pattern")
        root = self._resolve_workspace_path(str(arguments.get("path") or "."))
        limit = min(int(arguments.get("limit") or 200), 500)
        matches: list[str] = []
        for current_root, dirnames, filenames in os.walk(root):
            current = Path(current_root)
            dirnames[:] = [dirname for dirname in dirnames if not self._is_ignored(current / dirname)]
            for filename in filenames:
                path = current / filename
                if self._is_ignored(path):
                    continue
                rel = self._display(path)
                rel_to_root = path.relative_to(root).as_posix()
                if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(rel_to_root, pattern):
                    matches.append(rel)
                    if len(matches) >= limit:
                        break
            if len(matches) >= limit:
                break
        return ToolResult(
            tool="glob_files",
            title=f"Glob {pattern}",
            output="\n".join(matches) or "未找到匹配文件",
            data={"pattern": pattern, "count": len(matches)},
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
        risk = self.shell_command_risk(command)
        if risk["blocked"]:
            raise ToolExecutionError(f"命令命中黑名单，拒绝执行：{risk['reason']}：{command}")
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
        self._reject_if_file_changed_since_read(path)
        before = path.read_text(encoding="utf-8")
        if old not in before:
            raise ToolExecutionError(f"文件中找不到待替换文本：{self._display(path)}")
        after = before.replace(old, new, 1)
        path.write_text(after, encoding="utf-8")
        self._remember_file_snapshot(path)
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
            data={"path": self._display(path), "diff": self._diff_summary(diff, fallback_path=self._display(path))},
        )

    def edit_file(self, arguments: dict[str, Any]) -> ToolResult:
        result = self.replace_in_file(arguments)
        return ToolResult(
            tool="edit_file",
            title=result.title,
            output=result.output,
            ok=result.ok,
            data=result.data,
        )

    def multi_edit(self, arguments: dict[str, Any]) -> ToolResult:
        path = self._resolve_workspace_path(str(arguments.get("path", "")))
        edits = arguments.get("edits") or []
        if not isinstance(edits, list) or not edits:
            raise ToolExecutionError("multi_edit 需要 edits 数组")
        if not path.is_file():
            raise ToolExecutionError(f"文件不存在：{self._display(path)}")
        self._reject_if_file_changed_since_read(path)
        before = path.read_text(encoding="utf-8")
        after = before
        applied = 0
        for edit in edits[:50]:
            if not isinstance(edit, dict):
                continue
            old = str(edit.get("old") or "")
            new = str(edit.get("new") or "")
            if not old:
                raise ToolExecutionError("multi_edit 的每个 edit 都需要 old 文本")
            if old not in after:
                raise ToolExecutionError(f"文件中找不到待替换文本：{old[:80]}")
            after = after.replace(old, new, 1)
            applied += 1
        path.write_text(after, encoding="utf-8")
        self._remember_file_snapshot(path)
        diff = "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"a/{self._display(path)}",
                tofile=f"b/{self._display(path)}",
            )
        )
        return ToolResult(
            tool="multi_edit",
            title=f"批量修改 {self._display(path)}",
            output=diff[:24000],
            data={"path": self._display(path), "edits": applied, "diff": self._diff_summary(diff, fallback_path=self._display(path))},
        )

    def create_file(self, arguments: dict[str, Any]) -> ToolResult:
        path = self._resolve_workspace_path(str(arguments.get("path", "")))
        content = str(arguments.get("content") or "")
        if path.exists():
            raise ToolExecutionError(f"文件已存在：{self._display(path)}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self._remember_file_snapshot(path)
        return ToolResult(
            tool="create_file",
            title=f"创建 {self._display(path)}",
            output=f"已创建 {self._display(path)}，字符数 {len(content)}",
            data={"path": self._display(path)},
        )

    def write_file(self, arguments: dict[str, Any]) -> ToolResult:
        path = self._resolve_workspace_path(str(arguments.get("path", "")))
        content = str(arguments.get("content") or "")
        if path.exists():
            self._reject_if_file_changed_since_read(path)
        before = path.read_text(encoding="utf-8") if path.exists() else ""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        revision = self._remember_file_snapshot(path)
        if before:
            output = "".join(
                difflib.unified_diff(
                    before.splitlines(keepends=True),
                    content.splitlines(keepends=True),
                    fromfile=f"a/{self._display(path)}",
                    tofile=f"b/{self._display(path)}",
                )
            )
        else:
            output = f"已写入 {self._display(path)}，字符数 {len(content)}"
        return ToolResult(
            tool="write_file",
            title=f"写入 {self._display(path)}",
            output=output[:24000],
            data={
                "path": self._display(path),
                "bytes": len(content.encode("utf-8")),
                "file_revision": revision["sha256"],
                "diff": self._diff_summary(output, fallback_path=self._display(path)) if before else None,
            },
        )

    def apply_patch(self, arguments: dict[str, Any]) -> ToolResult:
        patch_text = str(arguments.get("patch") or "")
        if not patch_text.strip():
            raise ToolExecutionError("apply_patch 需要 patch")
        self._validate_patch_paths(patch_text)
        patch_paths = self._patch_target_paths(patch_text)
        for path in patch_paths:
            if path.exists():
                self._reject_if_file_changed_since_read(path)
        applier = "git apply"
        try:
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
            output = result.stdout.strip() or result.stderr.strip() or "补丁已应用"
            ok = result.returncode == 0
        except FileNotFoundError:
            self._apply_unified_diff_without_git(patch_text)
            applier = "Python fallback"
            output = "补丁已应用"
            ok = True
        for path in patch_paths:
            if path.exists():
                self._remember_file_snapshot(path)
        return ToolResult(
            tool="apply_patch",
            title="应用补丁",
            output=output,
            ok=ok,
            data={"exit_code": 0 if ok else 1, "applier": applier, "diff": self._diff_summary(patch_text)},
        )

    def todo_write(self, arguments: dict[str, Any]) -> ToolResult:
        items = arguments.get("items") or []
        if not isinstance(items, list):
            raise ToolExecutionError("todo_write 需要 items 数组")
        normalized: list[dict[str, str]] = []
        for item in items[:80]:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "").strip()
            status = str(item.get("status") or "pending").strip()
            if content:
                normalized.append(
                    {
                        "content": content,
                        "status": status if status in {"pending", "in_progress", "completed"} else "pending",
                    }
                )
        state_path = self.project_root / ".nova" / "agent-todos.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({"items": normalized}, ensure_ascii=False, indent=2), encoding="utf-8")
        return ToolResult(
            tool="todo_write",
            title="更新任务清单",
            output="\n".join(f"- [{item['status']}] {item['content']}" for item in normalized) or "任务清单已清空",
            data={"count": len(normalized)},
        )

    def todo_read(self, _arguments: dict[str, Any]) -> ToolResult:
        state_path = self.project_root / ".nova" / "agent-todos.json"
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            payload = {"items": []}
        items = payload.get("items") if isinstance(payload, dict) else []
        if not isinstance(items, list):
            items = []
        lines = []
        for item in items:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "").strip()
            status = str(item.get("status") or "pending").strip()
            if content:
                lines.append(f"- [{status}] {content}")
        return ToolResult(
            tool="todo_read",
            title="读取任务清单",
            output="\n".join(lines) or "暂无任务",
            data={"count": len(lines)},
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

    def memory_read(self, arguments: dict[str, Any]) -> ToolResult:
        name = Path(str(arguments.get("name") or "index.md")).name
        path = self.project_root / ".nova" / "memory" / name
        content = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        return ToolResult(
            tool="memory_read",
            title=f"读取记忆 {name}",
            output=content or "记忆文件不存在或为空",
            data={"name": name, "path": str(path), "exists": path.exists()},
        )

    def memory_write(self, arguments: dict[str, Any]) -> ToolResult:
        name = Path(str(arguments.get("name") or "index.md")).name
        if not name.endswith(".md"):
            name = f"{name}.md"
        content = str(arguments.get("content") or "")
        candidate = ProjectMemory(self.project_root).propose_fact(content, name=name, source="tool:memory_write")
        return ToolResult(
            tool="memory_write",
            title=f"候选记忆 {name}",
            output=f"已创建待确认记忆候选，用户确认后才会写入 {name}。",
            data={"name": name, "path": candidate["path"], "memory_candidates": [candidate]},
        )

    def memory_search(self, arguments: dict[str, Any]) -> ToolResult:
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ToolExecutionError("memory_search 需要 query")
        matches = ProjectMemory(self.project_root).search(query)
        lines = [f"{item['name']}:{item['line']}: {item['text']}" for item in matches]
        return ToolResult(
            tool="memory_search",
            title=f"搜索记忆 {query}",
            output="\n".join(lines) or "未找到匹配记忆",
            data={"query": query, "count": len(lines)},
        )

    def memory_summarize(self, arguments: dict[str, Any]) -> ToolResult:
        max_chars_per_file = int(arguments.get("max_chars_per_file") or 1200)
        result = ProjectMemory(self.project_root).summarize(max_chars_per_file=max_chars_per_file)
        return ToolResult(
            tool="memory_summarize",
            title="汇总长期记忆",
            output=result["summary"],
            data={"count": result["count"], "files": result["files"]},
        )

    def memory_compact(self, arguments: dict[str, Any]) -> ToolResult:
        max_chars = int(arguments.get("max_chars") or 12000)
        result = ProjectMemory(self.project_root).compact_memory(max_chars=max_chars)
        relative_path = Path(result["path"]).relative_to(self.project_root)
        return ToolResult(
            tool="memory_compact",
            title="压缩长期记忆",
            output=f"已压缩记忆到 {relative_path}，字符数 {len(result['summary'])}\n\n{result['summary']}",
            data={"path": result["path"], "bytes": result["bytes"]},
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

    def _diff_summary(self, diff_text: str, *, fallback_path: str | None = None) -> dict[str, Any]:
        files: list[str] = []
        additions = 0
        deletions = 0
        for line in diff_text.splitlines():
            if line.startswith("+++ b/"):
                path = line.removeprefix("+++ b/")
                if path not in files:
                    files.append(path)
                continue
            if line.startswith("--- a/"):
                path = line.removeprefix("--- a/")
                if path != "/dev/null" and path not in files:
                    files.append(path)
                continue
            if line.startswith("+") and not line.startswith("+++"):
                additions += 1
            if line.startswith("-") and not line.startswith("---"):
                deletions += 1
        if not files and fallback_path:
            files.append(fallback_path)
        return {
            "files": files,
            "additions": additions,
            "deletions": deletions,
            "preview": diff_text[:8000],
        }

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

    def _patch_target_paths(self, patch_text: str) -> list[Path]:
        paths: list[Path] = []
        seen: set[str] = set()
        for line in patch_text.splitlines():
            if not (line.startswith("+++ ") or line.startswith("--- ")):
                continue
            raw = line[4:].strip()
            if raw == "/dev/null":
                continue
            if raw.startswith("a/") or raw.startswith("b/"):
                raw = raw[2:]
            path = self._resolve_workspace_path(raw)
            key = self._file_snapshot_key(path)
            if key not in seen:
                seen.add(key)
                paths.append(path)
        return paths

    def _apply_unified_diff_without_git(self, patch_text: str) -> None:
        lines = patch_text.splitlines()
        index = 0
        while index < len(lines):
            if not lines[index].startswith("--- "):
                index += 1
                continue
            old_raw = lines[index][4:].strip()
            index += 1
            if index >= len(lines) or not lines[index].startswith("+++ "):
                raise ToolExecutionError("补丁格式错误：缺少 +++ 文件头")
            new_raw = lines[index][4:].strip()
            index += 1
            hunks: list[tuple[int, list[str]]] = []
            while index < len(lines) and not lines[index].startswith("--- "):
                header = lines[index]
                if not header.startswith("@@"):
                    index += 1
                    continue
                match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", header)
                if not match:
                    raise ToolExecutionError(f"补丁格式错误：无法解析 hunk 头 {header}")
                old_start = int(match.group(1))
                index += 1
                body: list[str] = []
                while index < len(lines) and not lines[index].startswith("@@") and not lines[index].startswith("--- "):
                    body.append(lines[index])
                    index += 1
                hunks.append((old_start, body))
            self._apply_unified_diff_file(old_raw, new_raw, hunks)

    def _apply_unified_diff_file(self, old_raw: str, new_raw: str, hunks: list[tuple[int, list[str]]]) -> None:
        target_raw = new_raw if new_raw != "/dev/null" else old_raw
        if target_raw.startswith("a/") or target_raw.startswith("b/"):
            target_raw = target_raw[2:]
        target = self._resolve_workspace_path(target_raw)
        original = [] if old_raw == "/dev/null" or not target.exists() else target.read_text(encoding="utf-8").splitlines()
        result: list[str] = []
        cursor = 0
        for old_start, body in hunks:
            hunk_index = max(old_start - 1, 0)
            if hunk_index < cursor:
                raise ToolExecutionError("补丁 hunk 顺序错误，无法应用")
            result.extend(original[cursor:hunk_index])
            cursor = hunk_index
            for line in body:
                if not line:
                    marker, value = " ", ""
                else:
                    marker, value = line[0], line[1:]
                if marker == "\\":
                    continue
                if marker == " ":
                    if cursor >= len(original) or original[cursor] != value:
                        raise ToolExecutionError(f"补丁上下文不匹配：{target_raw}")
                    result.append(original[cursor])
                    cursor += 1
                    continue
                if marker == "-":
                    if cursor >= len(original) or original[cursor] != value:
                        raise ToolExecutionError(f"补丁删除行不匹配：{target_raw}")
                    cursor += 1
                    continue
                if marker == "+":
                    result.append(value)
                    continue
                raise ToolExecutionError(f"补丁行格式错误：{line[:80]}")
        result.extend(original[cursor:])
        if new_raw == "/dev/null":
            target.unlink(missing_ok=True)
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(result) + "\n", encoding="utf-8")

    def _check_permission(self, name: str) -> None:
        spec = TOOL_SPECS.get(name)
        if spec is None or spec.permission == "read":
            return
        if spec.permission == "network" and not self.network_access:
            raise ToolExecutionError(f"当前网络访问关闭，禁止执行 {name}")
        if self.sandbox_mode == "read_only" and spec.permission in {"write", "shell"}:
            raise ToolExecutionError(f"当前沙箱模式为 read_only，禁止执行 {name}")
        if self.permission_mode == "bypass_permissions":
            return
        if self.permission_mode == "plan" and spec.permission != "read":
            raise ToolExecutionError(f"当前权限模式为 plan，只规划不执行 {name}")
        if self.permission_mode == "accept_edits" and spec.permission in {"shell", "network"}:
            raise ToolExecutionError(f"当前权限模式为 accept_edits，禁止自动执行 {name}")
        if self.permission_mode == "dont_ask" and spec.permission != "read":
            raise ToolExecutionError(f"当前权限模式为 dont_ask，未预批准的 {name} 会被拒绝")
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
