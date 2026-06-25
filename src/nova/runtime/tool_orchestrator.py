from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from uuid import uuid4

from ..tools.workspace import TOOL_SPECS


class ToolOrchestrator:
    """工具调用编排器。

    AgentLoop 决定“要不要用工具、用哪些工具”；这里决定“这些工具如何执行”：
    只读工具能否并行、写入或 shell 是否需要审批、执行事件如何流式返回。
    """

    def __init__(
        self,
        *,
        tools: object,
        executor: object,
        permission_mode: str,
        approval_policy: str,
        trace_tool_event: Callable[[dict], None] | None = None,
    ) -> None:
        self.tools = tools
        self.executor = executor
        self.permission_mode = permission_mode
        self.approval_policy = approval_policy
        self.trace_tool_event = trace_tool_event

    async def run(self, tool_calls: list[dict]) -> AsyncIterator[dict]:
        normalized = [
            (f"tool_{uuid4().hex[:12]}", name, arguments)
            for name, arguments in (self.normalize_tool_call(call) for call in tool_calls)
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

        parallel = len(normalized) > 1 and all(
            self.tools.supports_parallel(name) for _call_id, name, _arguments in normalized
        )
        if parallel:
            yield {"type": "agent_status", "status": f"并行执行 {len(normalized)} 个只读工具"}
            results = await asyncio.gather(
                *(
                    asyncio.to_thread(self.executor.run_one, call_id, name, arguments, parallel=True)
                    for call_id, name, arguments in normalized
                )
            )
            for events, result_json in results:
                for event in events:
                    self._trace(event)
                    yield event
                yield {"type": "tool_result_json", "result_json": result_json}
            return

        for call_id, name, arguments in normalized:
            async for event in self.iter_executor_events(
                call_id,
                name,
                arguments,
                require_permission=self.requires_permission_request(name),
            ):
                self._trace(event)
                yield event

    async def iter_executor_events(
        self,
        call_id: str,
        name: str,
        arguments: dict,
        *,
        require_permission: bool = False,
    ) -> AsyncIterator[dict]:
        iterator = self.executor.iter_one_stream(
            call_id,
            name,
            arguments,
            require_permission=require_permission,
        )
        sentinel = object()
        while True:
            event = await asyncio.to_thread(next, iterator, sentinel)
            if event is sentinel:
                break
            yield event

    def requires_permission_request(self, tool_name: str) -> bool:
        spec = TOOL_SPECS.get(tool_name)
        if not spec or spec.permission == "read":
            return False
        if self.permission_mode == "bypass_permissions":
            return False
        if self.permission_mode in {"ask", "plan"}:
            return True
        if self.approval_policy == "on_request":
            return spec.permission in {"write", "shell"}
        return False

    def normalize_tool_call(self, tool_call: dict) -> tuple[str, dict]:
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

    def _trace(self, event: dict) -> None:
        if self.trace_tool_event is None:
            return
        self.trace_tool_event(event)
