from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

from ..memory import ProjectMemory
from ..models import ChatMessage, ChatRole
from ..processes.manager import ProcessManager
from ..providers.bigmodel import BigModelProvider, ProviderError
from ..tools.executor import ToolExecutor
from ..tools.hooks import ToolHookRunner
from ..tools.workspace import TOOL_SPECS, WorkspaceTools
from .commands import builtin_help_text

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
        sandbox_mode: str = "workspace_write",
        approval_policy: str = "never",
        network_access: bool = False,
        tool_hooks_file: Path | None = None,
        process_manager: ProcessManager | None = None,
    ) -> None:
        self.provider = provider
        self.tools = WorkspaceTools(
            project_root,
            permission_mode=permission_mode,
            sandbox_mode=sandbox_mode,
            network_access=network_access,
        )
        self.hooks = ToolHookRunner.from_file(tool_hooks_file, cwd=project_root)
        self.process_manager = process_manager or ProcessManager()
        self.executor = ToolExecutor(self.tools, hooks=self.hooks, process_manager=self.process_manager)
        self.memory = ProjectMemory(project_root, global_agent_file=global_agent_file)
        self.max_tool_rounds = max_tool_rounds
        self.permission_mode = permission_mode
        self.sandbox_mode = sandbox_mode
        self.approval_policy = approval_policy

    async def stream(
        self,
        messages: list[ChatMessage],
    ) -> AsyncIterator[dict]:
        latest_user = self._latest_user_content(messages)
        if latest_user.startswith("/"):
            yield {"type": "agent_status", "status": "处理内置指令"}
            async for event in self._handle_builtin_command(latest_user, messages):
                yield event
            return
        direct_tool_calls = self._direct_tool_calls_from_user(latest_user)
        if direct_tool_calls:
            yield {"type": "agent_status", "status": "识别到明确工具意图"}
            tool_results: list[str] = []
            async for event in self._run_tool_calls(direct_tool_calls):
                if event["type"] == "tool_result_json":
                    tool_results.append(event["result_json"])
                    continue
                yield event
            yield {"type": "agent_status", "status": "模型基于工具结果生成回答"}
            async for event in self._stream_tool_result_answer(messages, tool_results):
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
        all_tool_results: list[str] = []

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
                    all_tool_results.append(event["result_json"])
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
            yield {"type": "agent_status", "status": "基于最近工具结果生成回答"}
            async for event in self._stream_tool_result_answer(working_messages, all_tool_results):
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
        text = "".join(parts)
        final_tool_calls = self._parse_tool_calls(text)
        if final_tool_calls:
            # 有些模型会在“最终回答”阶段才吐工具标签；这里收回文本，改为真实执行工具。
            yield {"type": "agent_status", "status": "识别到最终回答中的工具调用，转为真实执行"}
            tool_results: list[str] = []
            async for event in self._run_tool_calls(final_tool_calls):
                if event["type"] == "tool_result_json":
                    tool_results.append(event["result_json"])
                    continue
                yield event
            async for event in self._stream_tool_result_answer(working_messages, tool_results):
                yield event
            return

        for part in parts:
            yield {"type": "assistant_delta", "delta": part}

        if not emitted or not text.strip():
            text = self._strip_final_tags(fallback) or "模型没有返回有效内容，请换一种更具体的说法再试。"
            parts = [text]
            for chunk in self._chunk_text(text):
                yield {"type": "assistant_delta", "delta": chunk}
        yield {"type": "assistant_done_content", "content": "".join(parts)}

    async def _stream_tool_result_answer(
        self,
        original_messages: list[ChatMessage],
        result_json_items: list[str],
    ) -> AsyncIterator[dict]:
        fallback = self._answer_from_tool_results(result_json_items)
        if not self.provider.is_configured():
            for chunk in self._chunk_text(fallback, 36):
                yield {"type": "assistant_delta", "delta": chunk}
            yield {"type": "assistant_done_content", "content": fallback}
            return

        prompt = ChatMessage(
            session_id="agent",
            role=ChatRole.USER,
            content=(
                "请基于下面的真实工具结果回答用户。要求：中文、直接、不要编造；"
                "如果工具结果已经足够，就用自然语言解释结果；不要输出 <tool_call>。\n\n"
                "工具结果 JSON：\n" + "\n".join(result_json_items)
            ),
        )
        messages = [
            ChatMessage(
                session_id="agent",
                role=ChatRole.SYSTEM,
                content=self._system_prompt(),
            ),
            *[message for message in original_messages if message.role != ChatRole.ERROR],
            prompt,
        ]
        parts: list[str] = []
        try:
            async for delta in self.provider.stream(messages):
                parts.append(delta)
                yield {"type": "assistant_delta", "delta": delta}
        except ProviderError:
            yield {"type": "agent_status", "status": "模型最终回答失败，使用工具结果兜底"}
            for chunk in self._chunk_text(fallback, 36):
                yield {"type": "assistant_delta", "delta": chunk}
            yield {"type": "assistant_done_content", "content": fallback}
            return

        text = "".join(parts).strip()
        if not text:
            text = fallback
            for chunk in self._chunk_text(text, 36):
                yield {"type": "assistant_delta", "delta": chunk}
        yield {"type": "assistant_done_content", "content": text}

    def _parse_tool_call(self, text: str) -> dict | None:
        calls = self._parse_tool_calls(text)
        return calls[0] if calls else None

    def _parse_tool_calls(self, text: str) -> list[dict]:
        payloads: list[dict] = []
        named_payload = self._extract_named_tool_payload(text)
        if named_payload is not None:
            payloads.append(named_payload)

        calls_payload = self._extract_json_after_marker(text, "<tool_calls>", "[")
        if calls_payload is not None:
            try:
                parsed = json.loads(calls_payload)
            except json.JSONDecodeError:
                parsed = []
            if isinstance(parsed, list):
                payloads.extend(item for item in parsed if isinstance(item, dict))
            elif isinstance(parsed, dict):
                payloads.extend(self._coerce_tool_call_payload(parsed))

        single_payload = None if named_payload is not None else self._extract_tool_payload(text)
        if single_payload is not None:
            try:
                parsed = json.loads(single_payload)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                payloads.extend(self._coerce_tool_call_payload(parsed))
        return payloads

    def _coerce_tool_call_payload(self, payload: dict) -> list[dict]:
        if isinstance(payload.get("tool_calls"), list):
            return [item for item in payload["tool_calls"] if isinstance(item, dict)]
        return [payload]

    def _extract_tool_payload(self, text: str) -> str | None:
        return self._extract_json_after_marker(text, "<tool_call>", "{")

    def _extract_named_tool_payload(self, text: str) -> dict | None:
        start = text.find("<tool_call>")
        if start < 0:
            return None
        body = text[start + len("<tool_call>") :].strip()
        first_json = body.find("{")
        if first_json <= 0:
            return None
        tool_name = body[:first_json].strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tool_name):
            return None
        arguments_json = self._extract_json_after_marker(text, "<tool_call>", "{")
        if arguments_json is None:
            return None
        try:
            arguments = json.loads(arguments_json)
        except json.JSONDecodeError:
            return None
        return {"tool": tool_name, "arguments": arguments if isinstance(arguments, dict) else {}}

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

    def _answer_from_tool_results(self, result_json_items: list[str]) -> str:
        latest_failed: dict | None = None
        for item in reversed(result_json_items):
            try:
                result = json.loads(item)
            except json.JSONDecodeError:
                continue
            if not result.get("ok", False):
                if latest_failed is None:
                    latest_failed = result
                continue
            tool = result.get("tool", "工具")
            title = result.get("title") or tool
            output = str(result.get("output") or "").strip()
            if not output:
                continue
            if tool == "list_files":
                return f"已查看当前文件目录。结果如下：\n{self._limit_lines(output, 120)}"
            if tool == "read_file":
                return f"已读取文件：{title}\n\n{self._limit_lines(output, 120)}"
            if tool == "search_text":
                return f"已完成搜索：{title}\n\n{self._limit_lines(output, 120)}"
            if tool in {"git_status", "git_diff", "shell_command"}:
                return f"{title}：\n{self._limit_lines(output, 120)}"
            return f"{title} 已完成，结果如下：\n{self._limit_lines(output, 120)}"
        if latest_failed is not None:
            tool = latest_failed.get("tool", "工具")
            title = latest_failed.get("title") or f"{tool} 执行失败"
            output = str(latest_failed.get("output") or latest_failed.get("error") or "").strip()
            return f"{title}：\n{self._limit_lines(output or '工具执行失败，但没有返回详细输出。', 120)}"
        return "工具已执行，但没有得到可展示的有效结果。请换一种更具体的请求再试。"

    def _limit_lines(self, text: str, max_lines: int) -> str:
        lines = text.splitlines()
        if len(lines) <= max_lines:
            return text
        return "\n".join(lines[:max_lines]) + f"\n...[已省略 {len(lines) - max_lines} 行]"

    def _tool_title(self, tool_name: str, arguments: dict) -> str:
        target = arguments.get("path") or arguments.get("query") or arguments.get("command") or ""
        return f"{tool_name} {target}".strip()

    def _direct_tool_calls_from_user(self, content: str) -> list[dict]:
        normalized = content.strip().lower()
        wifi_password_terms = ["wifi密码", "wi-fi密码", "wifi 密码", "无线密码", "wifi password"]
        if any(term in normalized for term in wifi_password_terms):
            return [
                {
                    "tool": "shell_command",
                    "arguments": {
                        "command": self._wifi_password_command(),
                        "workdir": ".",
                        "timeout_ms": 12000,
                    },
                }
            ]
        shell_intents = [
            "命令行工具",
            "调用命令",
            "执行命令",
            "shell",
            "terminal",
            "command line",
        ]
        explicit_command = re.search(
            r"(?:执行命令|调用命令|shell|terminal|command line)\s*[:：]\s*(?P<command>.+)",
            content,
            re.IGNORECASE | re.DOTALL,
        )
        if explicit_command is not None:
            command = explicit_command.group("command").strip()
            if command:
                return [{"tool": "shell_command", "arguments": {"command": command, "workdir": ".", "timeout_ms": 120000}}]
        if any(intent in normalized for intent in shell_intents):
            return [{"tool": "shell_command", "arguments": {"command": "pwd", "workdir": ".", "timeout_ms": 5000}}]
        if "文档目录" in normalized or "文档集" in normalized:
            path = "产品研发文档集" if (self.tools.project_root / "产品研发文档集").is_dir() else "."
            return [{"tool": "list_files", "arguments": {"path": path, "limit": 120}}]
        directory_intents = [
            "查看当前文件目录",
            "查看当前目录",
            "列出当前目录",
            "列出文件",
            "文件目录",
            "当前目录",
            "list files",
            "ls",
        ]
        if any(intent in normalized for intent in directory_intents):
            return [{"tool": "list_files", "arguments": {"path": ".", "limit": 120}}]
        return []

    def _wifi_password_command(self) -> str:
        return (
            "powershell.exe -NoProfile -Command "
            "'$OutputEncoding=[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
            "$line=(netsh wlan show interfaces | Select-String \"^\\s*SSID\\s*: \" | Select-Object -First 1); "
            "if (-not $line) { Write-Output \"未检测到活动 WiFi 接口\"; exit 1 }; "
            "$ssid=$line.ToString().Split(\":\",2)[1].Trim(); "
            "Write-Output (\"当前 WiFi：\" + $ssid); "
            "netsh wlan show profile name=\"$ssid\" key=clear'"
        )

    async def _run_tool_calls(self, tool_calls: list[dict]) -> AsyncIterator[dict]:
        normalized = [
            (f"tool_{uuid4().hex[:12]}", name, arguments)
            for name, arguments in (self._normalize_tool_call(call) for call in tool_calls)
            if name
        ]
        skipped = len(tool_calls) - len(normalized)
        if skipped:
            # 模型偶尔会输出空工具名或半截 JSON；跳过而不是在 UI 上刷一屏“未知工具”。
            yield {"type": "agent_status", "status": f"已跳过 {skipped} 个无效工具调用"}
        if not normalized:
            yield {
                "type": "tool_result_json",
                "result_json": json.dumps(
                    {"ok": False, "error": "模型输出了无效工具调用，请按工具 schema 重新选择工具。"},
                    ensure_ascii=False,
                ),
            }
            return
        parallel = len(normalized) > 1 and all(self.tools.supports_parallel(name) for _id, name, _args in normalized)

        if parallel:
            yield {"type": "agent_status", "status": f"并行执行 {len(normalized)} 个只读工具"}
            results = await asyncio.gather(
                *(
                    asyncio.to_thread(self.executor.run_one, call_id, name, args, parallel=True)
                    for call_id, name, args in normalized
                )
            )
            for events, result_json in results:
                for event in events:
                    yield event
                yield {"type": "tool_result_json", "result_json": result_json}
            return

        for call_id, name, arguments in normalized:
            if self._requires_permission_request(name):
                hook_events, hook_decision = self._run_permission_request_hooks(call_id, name, arguments)
                for hook_event in hook_events:
                    yield hook_event
                if hook_decision == "allow":
                    async for event in self._iter_executor_events(call_id, name, arguments):
                        yield event
                    continue
                if hook_decision == "deny":
                    result_json = json.dumps(
                        {"tool": name, "ok": False, "error": "PermissionRequest hook 拒绝执行"},
                        ensure_ascii=False,
                    )
                    yield {
                        "type": "tool_done",
                        "call_id": call_id,
                        "tool": name,
                        "ok": False,
                        "title": f"{name} 执行失败",
                        "output": "PermissionRequest hook 拒绝执行",
                        "data": {"hook_decision": "deny"},
                    }
                    yield {"type": "tool_result_json", "result_json": result_json}
                    continue
                event = self._permission_request_event(call_id, name, arguments)
                yield event
                yield {"type": "tool_result_json", "result_json": self._permission_result_json(event)}
                continue
            async for event in self._iter_executor_events(call_id, name, arguments):
                yield event

    async def _iter_executor_events(self, call_id: str, name: str, arguments: dict) -> AsyncIterator[dict]:
        iterator = self.executor.iter_one_stream(call_id, name, arguments)
        sentinel = object()
        while True:
            event = await asyncio.to_thread(next, iterator, sentinel)
            if event is sentinel:
                break
            yield event

    def _requires_permission_request(self, tool_name: str) -> bool:
        spec = TOOL_SPECS.get(tool_name)
        if not spec or spec.permission == "read":
            return False
        if self.permission_mode in {"ask", "plan"}:
            return True
        if self.approval_policy == "on_request":
            return spec.permission in {"write", "shell"}
        return False

    def _run_permission_request_hooks(self, call_id: str, tool_name: str, arguments: dict) -> tuple[list[dict], str | None]:
        events: list[dict] = []
        decision: str | None = None
        outcomes = self.hooks.run(
            "PermissionRequest",
            tool_name=tool_name,
            tool_input=arguments,
            tool_use_id=call_id,
        )
        for outcome in outcomes:
            events.append(
                {
                    "type": "hook_start",
                    "call_id": call_id,
                    "tool": tool_name,
                    "hook_event": "PermissionRequest",
                    "hook_name": outcome.name,
                    "title": f"Hook PermissionRequest: {outcome.name}",
                    "data": {},
                }
            )
            events.append(
                {
                    "type": "hook_done",
                    "call_id": call_id,
                    "tool": tool_name,
                    "hook_event": "PermissionRequest",
                    "hook_name": outcome.name,
                    "title": f"Hook 完成：{outcome.name}",
                    "data": {
                        "permission_decision": outcome.permission_decision,
                        "reason": outcome.reason,
                        "updated_input": outcome.updated_input,
                        "additional_context": outcome.additional_context,
                    },
                }
            )
            if outcome.updated_input:
                arguments.update(outcome.updated_input)
            if outcome.permission_decision in {"allow", "deny", "ask"}:
                decision = outcome.permission_decision
        return events, decision

    def _permission_request_event(self, call_id: str, tool_name: str, arguments: dict) -> dict:
        spec = TOOL_SPECS.get(tool_name)
        permission = spec.permission if spec else "unknown"
        return {
            "type": "permission_request",
            "call_id": call_id,
            "tool": tool_name,
            "permission": permission,
            "title": f"需要审批：{tool_name}",
            "message": f"执行 {tool_name} 前需要用户确认。",
            "arguments": arguments,
            "data": {"reason": "ask 模式需要审批"},
        }

    def _permission_result_json(self, event: dict) -> str:
        return json.dumps(
            {
                "tool": event.get("tool"),
                "ok": False,
                "permission_request": True,
                "permission": event.get("permission"),
                "title": event.get("title"),
                "output": event.get("message"),
                "arguments": event.get("arguments") if isinstance(event.get("arguments"), dict) else {},
                "data": event.get("data") if isinstance(event.get("data"), dict) else {},
            },
            ensure_ascii=False,
        )

    def _normalize_tool_call(self, tool_call: dict) -> tuple[str, dict]:
        function_call = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
        tool_name = str(
            tool_call.get("tool")
            or tool_call.get("name")
            or function_call.get("name")
            or ""
        ).strip()
        arguments = (
            tool_call.get("arguments")
            or tool_call.get("parameters")
            or tool_call.get("input")
            or function_call.get("arguments")
            or {}
        )
        if isinstance(arguments, str):
            try:
                parsed = json.loads(arguments)
            except json.JSONDecodeError:
                parsed = {}
            arguments = parsed
        return tool_name, arguments if isinstance(arguments, dict) else {}

    def _latest_user_content(self, messages: list[ChatMessage]) -> str:
        for message in reversed(messages):
            if message.role == ChatRole.USER:
                return message.content.strip()
        return ""

    async def _handle_builtin_command(self, content: str, messages: list[ChatMessage] | None = None) -> AsyncIterator[dict]:
        command = content.split(maxsplit=1)[0].lower()
        if command == "/compact":
            yield {"type": "agent_status", "status": "写入压缩边界和会话摘要"}
            parts = content.split(maxsplit=1)
            instruction = parts[1].strip() if len(parts) > 1 else ""
            result = self.memory.compact_session(messages or [], instruction=instruction)
            text = self._compact_response(result)
            yield {
                "type": "compact_done",
                "title": "Conversation compacted",
                "message": "会话已压缩，摘要已写入 .nova/memory/session.md。",
                "summary": result.get("summary", ""),
                "path": result.get("path", ""),
                "covered_messages": result.get("covered_messages", 0),
            }
        else:
            text = self._builtin_response(command, content)
        for chunk in self._chunk_text(text, 36):
            yield {"type": "assistant_delta", "delta": chunk}
        yield {"type": "assistant_done_content", "content": text}

    def _builtin_response(self, command: str, raw_content: str = "") -> str:
        if command == "/tools":
            rows = [
                f"- {item['name']}：{item['description']}；权限={item['permission']}；并行={'是' if item['supports_parallel'] else '否'}"
                for item in self.tools.list_specs()
            ]
            return "当前工具清单：\n" + "\n".join(rows)
        if command in {"/permissions", "/approvals", "/sandbox"}:
            return (
                f"当前权限模式：{self.permission_mode}\n"
                f"当前沙箱：{self.sandbox_mode}\n"
                f"当前审批策略：{self.approval_policy}\n"
                "- read_only：只允许读工具。\n"
                "- ask/on_request：读工具允许，写入和 shell 生成待审批工具调用。\n"
                "- workspace_write：允许工作区写入和受控 shell。\n"
                "- bypass_permissions：跳过权限提示，但仍受工具自身安全校验约束。"
            )
        if command == "/memory":
            parts = raw_content.split(maxsplit=2)
            subcommand = parts[1].lower() if len(parts) >= 2 else ""
            if subcommand == "search":
                if len(parts) < 3 or not parts[2].strip():
                    return "用法：/memory search <关键词>"
                matches = self.memory.search(parts[2].strip())
                if not matches:
                    return f"未找到匹配记忆：{parts[2].strip()}"
                return "记忆搜索结果：\n" + "\n".join(
                    f"- {item['name']}:{item['line']} {item['text']}" for item in matches
                )
            if subcommand in {"summarize", "summary"}:
                return self.memory.summarize()["summary"]
            if subcommand == "compact":
                result = self.memory.compact_memory()
                return f"已压缩记忆到 {result['path']}，后续会继续注入 project.md。\n\n{result['summary']}"
            status = self.memory.status()
            injected_sources = status.get("injected_sources", [])
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
        if command == "/remember":
            parts = raw_content.split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                return "用法：/remember 要长期记住的事实或偏好"
            text = parts[1].strip()
            candidate = self.memory.propose_fact(text, source="builtin:/remember")
            return f"已创建待确认记忆候选：{candidate['content']}\n用户确认后才会写入 {candidate['name']}。"
        if command in {"/ps", "/jobs"}:
            jobs = self.process_manager.list_jobs()
            if not jobs:
                return "当前没有后台任务。"
            return "后台任务：\n" + "\n".join(
                f"- {item['id']} [{item['status']}] exit={item.get('exit_code')} cwd={item['cwd']} cmd={item['command']}"
                for item in jobs
            )
        if command in {"/stop", "/kill"}:
            parts = raw_content.split(maxsplit=1)
            if len(parts) < 2:
                return f"用法：{command} <后台任务ID>"
            job_id = parts[1].strip()
            try:
                job = self.process_manager.kill(job_id)
            except KeyError:
                return f"没有找到后台任务：{job_id}"
            return f"已终止后台任务 {job['id']}，状态：{job['status']}"
        if command == "/status":
            git = self.tools.git_status({}).output
            return (
                f"Nova 本地网关在线。\n模型：{self.provider.model}\n"
                f"权限模式：{self.permission_mode}\n沙箱：{self.sandbox_mode}\n"
                f"审批策略：{self.approval_policy}\n工作区：{self.tools.project_root}\nGit 状态：\n{git}"
            )
        if command == "/model":
            return f"模型：{self.provider.model}\nBase URL：{self.provider.base_url}\n已配置密钥：{'是' if self.provider.is_configured() else '否'}"
        if command == "/review":
            diff = self.tools.git_diff({}).output
            return f"当前 diff 摘要：\n{diff[:3000]}"
        if command == "/plan":
            return "请在 /plan 后写目标和验收标准；Nova 会先拆步骤，再按步骤调用工具执行。"
        if command == "/compact":
            return "用法：/compact [可选压缩要求]。执行后会把当前会话摘要写入 .nova/memory/session.md，并继续注入后续对话。"
        if command == "/clear":
            return "请点击左侧“新对话”创建空线程；Nova 不会自动删除已有历史。"
        return builtin_help_text()

    def _compact_response(self, result: dict) -> str:
        summary = str(result.get("summary") or "")
        preview = summary[:1200].rstrip()
        return (
            "会话已压缩，摘要已写入 `.nova/memory/session.md`，后续对话会继续注入这份会话记忆。\n\n"
            f"覆盖消息数：{result.get('covered_messages', 0)}\n"
            f"文件路径：{result.get('path', '')}\n\n"
            "摘要预览：\n"
            + preview
        )

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
7. 预计耗时较长的 shell 命令可以加 "background": true 后台执行；后台任务可由用户用 /ps 查看、/kill 终止。
8. 需要长期记住用户偏好、项目事实或阶段总结时，调用 memory_write 提出候选事实；候选必须由用户确认后才会真正写入。查询长期记忆用 memory_read 或 memory_search。

可用工具：
- read_file: {"path":"相对路径","max_bytes":24000}
- read_many_files: {"paths":["相对路径"],"max_bytes_each":12000}
- list_files: {"path":".","limit":200}
- glob_files: {"pattern":"**/*.py","path":".","limit":200}
- search_text: {"query":"关键词","path":".","max_results":80}
- git_status: {}
- git_diff: {"path":"可选相对路径","max_bytes":24000}
- shell_command: {"command":"受控 shell 命令","workdir":".","timeout_ms":10000,"background":false}
- replace_in_file: {"path":"文件","old":"原文","new":"新文"}
- edit_file: {"path":"文件","old":"原文","new":"新文"}
- multi_edit: {"path":"文件","edits":[{"old":"原文","new":"新文"}]}
- create_file: {"path":"文件","content":"内容"}
- write_file: {"path":"文件","content":"内容"}
- apply_patch: {"patch":"unified diff"}
- todo_read: {}
- todo_write: {"items":[{"content":"任务","status":"pending|in_progress|completed"}]}
- web_fetch: {"url":"https://example.com","max_bytes":20000}
- web_search: {"query":"搜索关键词","max_bytes":20000}
- memory_read: {"name":"index.md"}
- memory_write: {"name":"index.md","content":"候选记忆事实"}
- memory_search: {"query":"关键词"}

路径必须使用工作区内相对路径。回答使用中文，保持直接、务实。
""".strip()
        return f"{prompt}\n\n项目记忆：\n{memory_context or '暂无可用项目记忆。'}"
