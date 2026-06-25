from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

from ..memory import ProjectMemory
from ..models import ChatMessage, ChatRole
from ..processes.manager import ProcessManager
from ..providers.bigmodel import BigModelProvider, ProviderError
from ..review import ReviewManager
from ..skills import SkillManager
from ..tools.executor import ToolExecutor
from ..tools.hooks import ToolHookRunner
from ..tools.workspace import TOOL_SPECS, WorkspaceTools
from .commands import builtin_help_text
from .loop import AgentLoop
from .tool_orchestrator import ToolOrchestrator

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
        trace_recorder: object | None = None,
    ) -> None:
        tool_api_key = None
        api_key_for_tools = getattr(provider, "api_key_for_tools", None)
        if callable(api_key_for_tools):
            tool_api_key = api_key_for_tools()
        self.provider = provider
        self.tools = WorkspaceTools(
            project_root,
            permission_mode=permission_mode,
            sandbox_mode=sandbox_mode,
            network_access=network_access,
            zai_api_key=tool_api_key,
        )
        self.hooks = ToolHookRunner.from_file(tool_hooks_file, cwd=project_root)
        self.process_manager = process_manager or ProcessManager()
        self.executor = ToolExecutor(self.tools, hooks=self.hooks, process_manager=self.process_manager)
        self.memory = ProjectMemory(project_root, global_agent_file=global_agent_file)
        self.max_tool_rounds = max_tool_rounds
        self.permission_mode = permission_mode
        self.sandbox_mode = sandbox_mode
        self.approval_policy = approval_policy
        self.trace_recorder = trace_recorder
        self.agent_loop = AgentLoop(self)
        self.tool_orchestrator = ToolOrchestrator(
            tools=self.tools,
            executor=self.executor,
            permission_mode=self.permission_mode,
            approval_policy=self.approval_policy,
            trace_tool_event=self._trace_tool_event,
        )

    async def stream(
        self,
        messages: list[ChatMessage],
    ) -> AsyncIterator[dict]:
        latest_user = self._latest_user_content(messages)
        trace_turn_id = self._trace_start(messages, latest_user)
        self._active_trace_turn_id = trace_turn_id
        trace_output_parts: list[str] = []
        trace_status = "ok"
        try:
            async for event in self._stream_inner(messages, latest_user, trace_turn_id, trace_output_parts):
                if event.get("type") == "assistant_done_content" and event.get("content"):
                    trace_output_parts[:] = [str(event.get("content") or "")]
                yield event
        except Exception:
            trace_status = "failed"
            raise
        finally:
            self._trace_end(trace_turn_id, output="".join(trace_output_parts), status=trace_status)
            self._active_trace_turn_id = None

    async def _stream_inner(
        self,
        messages: list[ChatMessage],
        latest_user: str,
        trace_turn_id: str,
        trace_output_parts: list[str],
    ) -> AsyncIterator[dict]:
        async for event in self.agent_loop.run(
            messages,
            latest_user=latest_user,
            trace_turn_id=trace_turn_id,
        ):
            yield event

    async def _complete_tool_decision(self, messages: list[ChatMessage]) -> dict[str, object]:
        complete_with_tools = getattr(self.provider, "complete_with_tools", None)
        if callable(complete_with_tools):
            chat_schemas = getattr(self.provider, "chat_tool_schemas", None)
            if callable(chat_schemas):
                tools = chat_schemas(
                    TOOL_SPECS,
                    enable_web_search=self.tools.network_access,
                    enable_web_fetch=self.tools.network_access and self._latest_user_has_url(messages),
                )
            else:
                schemas = getattr(self.provider, "openai_tool_schemas", None)
                tools = schemas(TOOL_SPECS) if callable(schemas) else None
            decision = await complete_with_tools(messages, tools=tools)
            return {"content": decision.content, "tool_calls": decision.tool_calls}
        text = await self.provider.complete(messages)
        return {"content": text, "tool_calls": []}

    def _web_search_messages(self, messages: list[ChatMessage]) -> list[ChatMessage]:
        prompt = (
            "你是 Nova 的联网搜索回答模式。当前请求已经路由到 BigModel 内置 web_search。"
            "请直接使用内置 web_search 检索并回答用户；不要输出 function tool_calls，"
            "不要声称没有联网搜索能力。回答使用中文，尽量给出来源、日期或 ref。"
        )
        return [
            ChatMessage(session_id="agent", role=ChatRole.SYSTEM, content=prompt),
            *[message for message in messages if message.role != ChatRole.SYSTEM],
        ]

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
            if tool == "shell_command":
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
        if self.tools.network_access and self._content_wants_web_search(content):
            return [
                {
                    "tool": "web_search",
                    "arguments": {
                        "query": content.strip(),
                        "search_engine": "search_pro",
                        "count": 10,
                        "search_recency_filter": "noLimit",
                        "content_size": "high",
                    },
                }
            ]
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

    async def _run_tool_calls(self, tool_calls: list[dict]) -> AsyncIterator[dict]:
        self._sync_tool_orchestrator_policy()
        async for event in self.tool_orchestrator.run(tool_calls):
            yield event

    async def _iter_executor_events(
        self,
        call_id: str,
        name: str,
        arguments: dict,
        *,
        require_permission: bool = False,
    ) -> AsyncIterator[dict]:
        async for event in self.tool_orchestrator.iter_executor_events(
            call_id,
            name,
            arguments,
            require_permission=require_permission,
        ):
            yield event

    def _requires_permission_request(self, tool_name: str) -> bool:
        self._sync_tool_orchestrator_policy()
        return self.tool_orchestrator.requires_permission_request(tool_name)

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
        return self.tool_orchestrator.normalize_tool_call(tool_call)

    def _sync_tool_orchestrator_policy(self) -> None:
        self.tool_orchestrator.permission_mode = self.permission_mode
        self.tool_orchestrator.approval_policy = self.approval_policy

    def _latest_user_content(self, messages: list[ChatMessage]) -> str:
        for message in reversed(messages):
            if message.role == ChatRole.USER:
                return message.content.strip()
        return ""

    def _latest_user_has_url(self, messages: list[ChatMessage]) -> bool:
        return bool(re.search(r"https?://\\S+", self._latest_user_content(messages), re.IGNORECASE))

    def _latest_user_wants_web_search(self, messages: list[ChatMessage]) -> bool:
        return self._content_wants_web_search(self._latest_user_content(messages))

    def _content_wants_web_search(self, content: str) -> bool:
        content = content.lower()
        if re.search(r"https?://\S+", content, re.IGNORECASE):
            return False
        search_terms = [
            "联网",
            "搜索",
            "查一下",
            "查找",
            "最新",
            "新闻",
            "实时",
            "今天",
            "最近",
            "官网",
            "来源",
            "价格",
            "政策",
            "版本",
            "发布",
            "web search",
            "search web",
            "latest",
            "news",
        ]
        return any(term in content for term in search_terms)

    def _trace_start(self, messages: list[ChatMessage], latest_user: str) -> str:
        recorder = self.trace_recorder
        session_id = messages[-1].session_id if messages else "agent"
        turn_id = f"turn_{uuid4().hex[:12]}"
        if recorder is None:
            return turn_id
        start_turn = getattr(recorder, "start_turn", None)
        if not callable(start_turn):
            return turn_id
        try:
            return str(
                start_turn(
                    session_id=session_id,
                    turn_id=turn_id,
                    user_input=latest_user,
                    metadata={
                        "workspace": str(self.tools.project_root),
                        "permission_mode": self.permission_mode,
                        "sandbox_mode": self.sandbox_mode,
                        "network_access": self.tools.network_access,
                    },
                )
            )
        except Exception:
            return turn_id

    def _trace_generation(
        self,
        turn_id: str,
        *,
        name: str,
        messages: list[ChatMessage],
        content: str,
        tool_calls: list[dict],
    ) -> None:
        recorder = self.trace_recorder
        record_generation = getattr(recorder, "record_generation", None) if recorder is not None else None
        if not callable(record_generation):
            return
        try:
            record_generation(
                turn_id=turn_id,
                name=name,
                model=getattr(self.provider, "model", "unknown"),
                input_messages=[{"role": message.role.value, "content": message.content} for message in messages],
                output=content,
                tool_calls=tool_calls,
            )
        except Exception:
            return

    def _trace_tool_event(self, event: dict) -> None:
        if event.get("type") != "tool_done":
            return
        recorder = self.trace_recorder
        record_tool = getattr(recorder, "record_tool", None) if recorder is not None else None
        if not callable(record_tool):
            return
        try:
            record_tool(
                turn_id=str(getattr(self, "_active_trace_turn_id", None) or "current"),
                call_id=str(event.get("call_id") or ""),
                tool=str(event.get("tool") or "tool"),
                arguments=event.get("arguments") if isinstance(event.get("arguments"), dict) else {},
                output=str(event.get("output") or ""),
                ok=bool(event.get("ok")),
                metadata=event.get("data") if isinstance(event.get("data"), dict) else {},
            )
        except Exception:
            return

    def _trace_end(self, turn_id: str, *, output: str, status: str = "ok") -> None:
        recorder = self.trace_recorder
        end_turn = getattr(recorder, "end_turn", None) if recorder is not None else None
        if not callable(end_turn):
            return
        try:
            end_turn(turn_id=turn_id, output=output, status=status)
        except Exception:
            return

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
        if command == "/skills":
            skills = SkillManager(self.tools.project_root).list_skills()
            if not skills:
                return "当前没有发现可用技能。项目级技能放在 `.nova/skills/<name>/SKILL.md`，全局技能放在 `~/.nova/skills/<name>/SKILL.md`。"
            rows = [
                f"- {skill.trigger}（{skill.scope}）：{skill.description or skill.name}；路径={skill.file_path}"
                for skill in skills
            ]
            return "当前技能清单：\n" + "\n".join(rows)
        if command == "/skill":
            parts = raw_content.split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                return "用法：/skill <技能名>。也可以直接输入 `$技能名` 触发。"
            return self._skill_response(parts[1].strip())
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
            return (
                f"Nova 本地网关在线。\n模型：{self.provider.model}\n"
                f"权限模式：{self.permission_mode}\n沙箱：{self.sandbox_mode}\n"
                f"审批策略：{self.approval_policy}\n工作区：{self.tools.project_root}"
            )
        if command == "/model":
            return f"模型：{self.provider.model}\nBase URL：{self.provider.base_url}\n已配置密钥：{'是' if self.provider.is_configured() else '否'}"
        if command == "/review":
            summary = ReviewManager(self.tools.project_root).summary()
            risks = "\n".join(
                f"- {item['severity']}：{item['title']}。{item['detail']}"
                for item in summary.get("risks", [])
            )
            tests = "\n".join(
                f"- {item['label']}：`{item['command']}`"
                for item in summary.get("suggested_tests", [])
            )
            return f"{summary['summary']}\n\n风险：\n{risks or '- 暂无'}\n\n建议测试：\n{tests or '- 暂无'}"
        if command == "/plan":
            return "请在 /plan 后写目标和验收标准；Nova 会先拆步骤，再按步骤调用工具执行。"
        if command == "/compact":
            return "用法：/compact [可选压缩要求]。执行后会把当前会话摘要写入 .nova/memory/session.md，并继续注入后续对话。"
        if command == "/clear":
            return "请点击左侧“新对话”创建空线程；Nova 不会自动删除已有历史。"
        return builtin_help_text()

    def _skill_response_from_dollar(self, raw_content: str) -> str:
        name = raw_content[1:].split(maxsplit=1)[0].strip()
        if not name:
            return "用法：$技能名 [补充要求]"
        return self._skill_response(name)

    def _skill_response(self, name: str) -> str:
        skill = SkillManager(self.tools.project_root).find(name)
        if skill is None:
            return f"未找到技能：{name}\n可用 `/skills` 查看当前发现的技能。"
        return (
            f"已加载技能：{skill.name}\n"
            f"来源：{skill.scope}\n"
            f"路径：{skill.file_path}\n"
            f"触发方式：{skill.trigger}\n"
            f"说明：{skill.description or '无'}\n\n"
            "SKILL.md：\n"
            f"{skill.content}"
        )

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
        skill_context = SkillManager(self.tools.project_root).skill_index_prompt()
        tool_rows = "\n".join(
            f"- {item['name']}: {json.dumps(item['schema'], ensure_ascii=False)}"
            for item in self.tools.list_specs()
        )
        prompt = """
你是 Nova 的 Codex-like 本地开发 Agent。你的工作方式要接近 Codex CLI：

1. 先理解用户目标和当前代码上下文。
2. 需要上下文时调用工具，而不是猜测。
3. 优先使用接口提供的标准 tools/tool_calls 机制选择工具。每个工具 arguments 必须包含 "annotation"，用一句很短的中文说明这次调用目的，便于 UI 展示工具执行卡片。
4. 只有在当前模型接口没有提供标准 tools/tool_calls 时，才使用下面的兼容文本格式。
5. 单工具调用兼容格式：
<tool_call>{"tool":"工具名","arguments":{...}}</tool_call>
6. 多个只读工具可以并行调用，兼容格式：
<tool_calls>[{"tool":"read_file","arguments":{...}},{"tool":"search_text","arguments":{...}}]</tool_calls>
7. 不需要工具时，不要输出工具调用，直接给最终答复。
8. 不要请求执行破坏性命令；需要修改文件时优先使用 apply_patch，生成或覆盖整文件时才使用 write_file。
9. 预计耗时较长的 shell 命令可以加 "background": true 后台执行；后台任务可由用户用 /ps 查看、/kill 终止。
10. 需要长期记住用户偏好、项目事实或阶段总结时，调用 memory_write 提出候选事实；候选必须由用户确认后才会真正写入。查询长期记忆用 memory_read 或 memory_search。
11. 当用户输入 $技能名 或请求明显匹配某个技能说明时，先引用对应技能；不要凭空假设技能内容，用户也可以用 /skill <技能名> 显式读取 SKILL.md。
12. 需要最新网络信息时，使用本地 web_search 工具；它会调用 Z.ai Web Search API 返回结构化来源。需要抓取明确 URL 时才使用 web_fetch。

可用工具：
__TOOL_ROWS__

路径必须使用工作区内相对路径。回答使用中文，保持直接、务实。
""".replace("__TOOL_ROWS__", tool_rows).strip()
        return (
            f"{prompt}\n\n"
            f"可用技能索引：\n{skill_context}\n\n"
            f"项目记忆：\n{memory_context or '暂无可用项目记忆。'}"
        )
