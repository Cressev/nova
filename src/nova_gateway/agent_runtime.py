from __future__ import annotations

import asyncio
import json
import re
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

from .agent_tools import ToolExecutionError, WorkspaceTools, tool_result_as_json
from .memory import ProjectMemory
from .models import ChatMessage, ChatRole
from .provider import BigModelProvider

TOOL_CALL_PATTERN = re.compile(
    r"<tool_call>\s*(?P<payload>\{.*?\})\s*(?:</tool_call>)?",
    re.DOTALL,
)
TOOL_CALLS_PATTERN = re.compile(
    r"<tool_calls>\s*(?P<payload>\[.*?\])\s*(?:</tool_calls>)?",
    re.DOTALL,
)


class CodexLikeAgentRuntime:
    def __init__(
        self,
        *,
        provider: BigModelProvider,
        project_root: Path,
        global_agent_file: Path | None = None,
        max_tool_rounds: int = 4,
        permission_mode: str = "workspace_write",
    ) -> None:
        self.provider = provider
        self.tools = WorkspaceTools(project_root, permission_mode=permission_mode)
        self.memory = ProjectMemory(project_root, global_agent_file=global_agent_file)
        self.max_tool_rounds = max_tool_rounds
        self.permission_mode = permission_mode

    async def stream(
        self,
        messages: list[ChatMessage],
    ) -> AsyncIterator[dict]:
        latest_user = self._latest_user_content(messages)
        if latest_user.startswith("/"):
            yield {"type": "agent_status", "status": "处理内置指令"}
            async for event in self._handle_builtin_command(latest_user):
                yield event
            return

        working_messages = [
            ChatMessage(
                session_id="agent",
                role=ChatRole.SYSTEM,
                content=self._system_prompt(),
            ),
            *messages,
        ]
        used_tools = False

        for _round in range(self.max_tool_rounds):
            yield {"type": "agent_status", "status": f"模型决策中，第 {_round + 1} 轮"}
            decision = await self.provider.complete(working_messages)
            tool_calls = self._parse_tool_calls(decision)
            if not tool_calls:
                yield {"type": "agent_status", "status": "生成最终回答"}
                async for event in self._stream_final(working_messages, decision):
                    yield event
                return

            used_tools = True
            tool_results: list[str] = []
            async for event in self._run_tool_calls(tool_calls):
                if event["type"] == "tool_result_json":
                    tool_results.append(event["result_json"])
                    continue
                yield event

            working_messages.extend(
                [
                    ChatMessage(
                        session_id="agent",
                        role=ChatRole.ASSISTANT,
                        content=decision,
                    ),
                    ChatMessage(
                        session_id="agent",
                        role=ChatRole.USER,
                        content="工具结果：\n" + "\n".join(tool_results),
                    ),
                ]
            )

        if used_tools:
            yield {"type": "agent_status", "status": "工具轮次已用完，生成总结"}
            async for event in self._stream_final(
                working_messages,
                "工具轮次已用完，请基于已有工具结果给出最终答复。",
            ):
                yield event

    async def _stream_final(
        self,
        working_messages: list[ChatMessage],
        fallback: str,
    ) -> AsyncIterator[dict]:
        final_prompt = ChatMessage(
            session_id="agent",
            role=ChatRole.USER,
            content=(
                "请给用户最终答复。要求：中文、直接、说明做了什么；"
                "如果已经使用工具，要引用工具结果；不要再输出 <tool_call>。"
            ),
        )
        emitted = False
        parts: list[str] = []
        async for delta in self.provider.stream([*working_messages, final_prompt]):
            emitted = True
            parts.append(delta)
            yield {"type": "assistant_delta", "delta": delta}

        if not emitted:
            text = self._strip_final_tags(fallback)
            parts.append(text)
            for chunk in self._chunk_text(text):
                yield {"type": "assistant_delta", "delta": chunk}
        yield {"type": "assistant_done_content", "content": "".join(parts)}

    def _parse_tool_call(self, text: str) -> dict | None:
        calls = self._parse_tool_calls(text)
        return calls[0] if calls else None

    def _parse_tool_calls(self, text: str) -> list[dict]:
        payloads: list[dict] = []
        calls_payload = self._extract_json_after_marker(text, "<tool_calls>", "[")
        if calls_payload is not None:
            try:
                parsed = json.loads(calls_payload)
            except json.JSONDecodeError:
                parsed = []
            if isinstance(parsed, list):
                payloads.extend(item for item in parsed if isinstance(item, dict))

        single_payload = self._extract_tool_payload(text)
        if single_payload is not None:
            try:
                parsed = json.loads(single_payload)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                payloads.append(parsed)
        return payloads

    def _extract_tool_payload(self, text: str) -> str | None:
        return self._extract_json_after_marker(text, "<tool_call>", "{")

    def _extract_json_after_marker(self, text: str, marker: str, open_char: str) -> str | None:
        start = text.find(marker)
        if start < 0:
            pattern = TOOL_CALLS_PATTERN if marker == "<tool_calls>" else TOOL_CALL_PATTERN
            match = pattern.search(text)
            if match:
                return match.group("payload")
            return None
        # GLM 偶尔只输出起始标签和 JSON，不输出结束标签；这里按括号配平提取第一个 JSON 对象。
        body = text[start + len(marker) :].strip()
        first = body.find(open_char)
        if first < 0:
            return None

        close_char = "}" if open_char == "{" else "]"
        depth = 0
        in_string = False
        escape = False
        for index, char in enumerate(body[first:], start=first):
            if escape:
                escape = False
                continue
            if char == "\\" and in_string:
                escape = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == open_char:
                depth += 1
            elif char == close_char:
                depth -= 1
                if depth == 0:
                    return body[first : index + 1]
        return None

    def _strip_final_tags(self, text: str) -> str:
        return (
            text.replace("<final>", "")
            .replace("</final>", "")
            .replace("<tool_call>", "")
            .replace("</tool_call>", "")
            .strip()
        )

    def _chunk_text(self, text: str, size: int = 24) -> list[str]:
        return [text[index : index + size] for index in range(0, len(text), size)] or [""]

    def _tool_title(self, tool_name: str, arguments: dict) -> str:
        target = arguments.get("path") or arguments.get("query") or arguments.get("command") or ""
        return f"{tool_name} {target}".strip()

    async def _run_tool_calls(self, tool_calls: list[dict]) -> AsyncIterator[dict]:
        normalized = [self._normalize_tool_call(item) for item in tool_calls]
        parallel = len(normalized) > 1 and all(self.tools.supports_parallel(name) for name, _args in normalized)

        if parallel:
            yield {"type": "agent_status", "status": f"并行执行 {len(normalized)} 个只读工具"}
            for name, arguments in normalized:
                yield {
                    "type": "tool_start",
                    "tool": name,
                    "arguments": arguments,
                    "title": self._tool_title(name, arguments),
                    "parallel": True,
                }
            results = await asyncio.gather(
                *(asyncio.to_thread(self._run_one_tool, name, args) for name, args in normalized)
            )
            for event, result_json in results:
                yield event
                yield {"type": "tool_result_json", "result_json": result_json}
            return

        for name, arguments in normalized:
            yield {
                "type": "tool_start",
                "tool": name,
                "arguments": arguments,
                "title": self._tool_title(name, arguments),
                "parallel": False,
            }
            event, result_json = self._run_one_tool(name, arguments)
            yield event
            yield {"type": "tool_result_json", "result_json": result_json}

    def _run_one_tool(self, tool_name: str, arguments: dict) -> tuple[dict, str]:
        try:
            result = self.tools.run(tool_name, arguments)
            result_json = tool_result_as_json(result)
            return (
                {
                    "type": "tool_done",
                    "tool": tool_name,
                    "ok": result.ok,
                    "title": result.title,
                    "output": result.output,
                    "data": result.data or {},
                },
                result_json,
            )
        except (ToolExecutionError, OSError, ValueError, subprocess.SubprocessError) as exc:  # type: ignore[name-defined]
            result_json = json.dumps(
                {"tool": tool_name, "ok": False, "error": str(exc)},
                ensure_ascii=False,
            )
            return (
                {
                    "type": "tool_done",
                    "tool": tool_name,
                    "ok": False,
                    "title": f"{tool_name} 执行失败",
                    "output": str(exc),
                    "data": {},
                },
                result_json,
            )

    def _normalize_tool_call(self, tool_call: dict) -> tuple[str, dict]:
        tool_name = str(tool_call.get("tool") or "")
        arguments = tool_call.get("arguments") or {}
        return tool_name, arguments if isinstance(arguments, dict) else {}

    def _latest_user_content(self, messages: list[ChatMessage]) -> str:
        for message in reversed(messages):
            if message.role == ChatRole.USER:
                return message.content.strip()
        return ""

    async def _handle_builtin_command(self, content: str) -> AsyncIterator[dict]:
        command = content.split(maxsplit=1)[0].lower()
        text = self._builtin_response(command)
        for chunk in self._chunk_text(text, 36):
            yield {"type": "assistant_delta", "delta": chunk}
        yield {"type": "assistant_done_content", "content": text}

    def _builtin_response(self, command: str) -> str:
        if command == "/tools":
            rows = [
                f"- {item['name']}：{item['description']}；权限={item['permission']}；并行={'是' if item['supports_parallel'] else '否'}"
                for item in self.tools.list_specs()
            ]
            return "当前工具清单：\n" + "\n".join(rows)
        if command == "/permissions":
            return (
                f"当前权限模式：{self.permission_mode}\n"
                "- read_only：只允许读工具。\n"
                "- ask：读工具允许，写入和 shell 会被拦截，等待后续审批 UI。\n"
                "- workspace_write：允许工作区写入和受控 shell。"
            )
        if command == "/memory":
            status = self.memory.status()
            injected_sources = [
                source
                for source in [status.get("global"), status.get("project")]
                if source is not None
            ]
            development_sources = status.get("development_state", [])
            injected_rows = [
                f"- {item['scope']}：{item['path']}（{'存在' if item['exists'] else '缺失'}）"
                for item in injected_sources
            ]
            ignored_rows = [
                f"- {item['path']}（{'存在' if item['exists'] else '缺失'}）"
                for item in development_sources
            ]
            return (
                "项目记忆已启用。\n"
                "注入给开发 Agent：\n"
                + "\n".join(injected_rows)
                + "\n\n只给 Nova 开发过程，不注入产品内 Agent：\n"
                + "\n".join(ignored_rows)
            )
        if command == "/status":
            git = self.tools.git_status({}).output
            return f"Nova 本地网关在线。\n权限模式：{self.permission_mode}\nGit 状态：\n{git}"
        if command == "/review":
            diff = self.tools.git_diff({}).output
            return f"当前 diff 摘要：\n{diff[:3000]}"
        if command == "/plan":
            return "请在 /plan 后写目标和验收标准；Nova 会先拆步骤，再按步骤调用工具执行。"
        return "可用内置指令：/status、/tools、/permissions、/memory、/review、/plan、/help。"

    def _system_prompt(self) -> str:
        memory_context = self.memory.context()
        prompt = """
你是 Nova 的 Codex-like 本地开发 Agent。你的工作方式要接近 Codex CLI：

1. 先理解用户目标和当前代码上下文。
2. 需要上下文时调用工具，而不是猜测。
3. 单工具调用格式：
<tool_call>{"tool":"工具名","arguments":{...}}</tool_call>
4. 多个只读工具可以并行调用，格式：
<tool_calls>[{"tool":"read_file","arguments":{...}},{"tool":"search_text","arguments":{...}}]</tool_calls>
5. 不需要工具时，不要输出工具调用，直接给最终答复。
6. 不要请求执行破坏性命令；需要修改文件时优先使用 apply_patch、replace_in_file 或 create_file。

可用工具：
- read_file: {"path":"相对路径","max_bytes":24000}
- list_files: {"path":".","limit":200}
- search_text: {"query":"关键词","path":".","max_results":80}
- git_status: {}
- git_diff: {"path":"可选相对路径","max_bytes":24000}
- shell_command: {"command":"受控 shell 命令","workdir":".","timeout_ms":10000}
- replace_in_file: {"path":"文件","old":"原文","new":"新文"}
- create_file: {"path":"文件","content":"内容"}
- apply_patch: {"patch":"unified diff"}

路径必须使用工作区内相对路径。回答使用中文，保持直接、务实。
""".strip()
        return f"{prompt}\n\n项目记忆：\n{memory_context or '暂无可用项目记忆。'}"
