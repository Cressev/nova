from __future__ import annotations

import json
import re
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

from .agent_tools import ToolExecutionError, WorkspaceTools, tool_result_as_json
from .models import ChatMessage, ChatRole
from .provider import BigModelProvider

TOOL_CALL_PATTERN = re.compile(
    r"<tool_call>\s*(?P<payload>\{.*?\})\s*(?:</tool_call>)?",
    re.DOTALL,
)


class CodexLikeAgentRuntime:
    def __init__(
        self,
        *,
        provider: BigModelProvider,
        project_root: Path,
        max_tool_rounds: int = 4,
    ) -> None:
        self.provider = provider
        self.tools = WorkspaceTools(project_root)
        self.max_tool_rounds = max_tool_rounds

    async def stream(
        self,
        messages: list[ChatMessage],
    ) -> AsyncIterator[dict]:
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
            decision = await self.provider.complete(working_messages)
            tool_call = self._parse_tool_call(decision)
            if tool_call is None:
                async for event in self._stream_final(working_messages, decision):
                    yield event
                return

            used_tools = True
            tool_name = str(tool_call.get("tool") or "")
            arguments = tool_call.get("arguments") or {}
            if not isinstance(arguments, dict):
                arguments = {}

            yield {
                "type": "tool_start",
                "tool": tool_name,
                "arguments": arguments,
                "title": self._tool_title(tool_name, arguments),
            }
            try:
                result = self.tools.run(tool_name, arguments)
                result_json = tool_result_as_json(result)
                yield {
                    "type": "tool_done",
                    "tool": tool_name,
                    "ok": result.ok,
                    "title": result.title,
                    "output": result.output,
                    "data": result.data or {},
                }
            except (ToolExecutionError, OSError, ValueError, subprocess.SubprocessError) as exc:  # type: ignore[name-defined]
                result_json = json.dumps(
                    {
                        "tool": tool_name,
                        "ok": False,
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                )
                yield {
                    "type": "tool_done",
                    "tool": tool_name,
                    "ok": False,
                    "title": f"{tool_name} 执行失败",
                    "output": str(exc),
                    "data": {},
                }

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
                        content=f"工具结果：\n{result_json}",
                    ),
                ]
            )

        if used_tools:
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
        payload_text = self._extract_tool_payload(text)
        if payload_text is None:
            return None
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _extract_tool_payload(self, text: str) -> str | None:
        marker = "<tool_call>"
        start = text.find(marker)
        if start < 0:
            match = TOOL_CALL_PATTERN.search(text)
            if match:
                return match.group("payload")
            return None
        # GLM 偶尔只输出起始标签和 JSON，不输出结束标签；这里按括号配平提取第一个 JSON 对象。
        body = text[start + len(marker) :].strip()
        first_brace = body.find("{")
        if first_brace < 0:
            return None

        depth = 0
        in_string = False
        escape = False
        for index, char in enumerate(body[first_brace:], start=first_brace):
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
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return body[first_brace : index + 1]
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

    def _system_prompt(self) -> str:
        return """
你是 Nova 的 Codex-like 本地开发 Agent。你的工作方式要接近 Codex CLI：

1. 先理解用户目标和当前代码上下文。
2. 需要上下文时调用工具，而不是猜测。
3. 每次只能输出一个工具调用，格式必须严格为：
<tool_call>{"tool":"工具名","arguments":{...}}</tool_call>
4. 不需要工具时，不要输出工具调用，直接给最终答复。
5. 不要请求执行破坏性命令；需要修改文件时优先使用 replace_in_file 或 create_file。

可用工具：
- read_file: {"path":"相对路径","max_bytes":24000}
- list_files: {"path":".","limit":200}
- search_text: {"query":"关键词","path":".","max_results":80}
- shell_command: {"command":"受控 shell 命令","workdir":".","timeout_ms":10000}
- replace_in_file: {"path":"文件","old":"原文","new":"新文"}
- create_file: {"path":"文件","content":"内容"}
- git_status: {}

路径必须使用工作区内相对路径。回答使用中文，保持直接、务实。
""".strip()
