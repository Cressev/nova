from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from ..models import ChatMessage, ChatRole


class AgentLoop:
    """Nova 的模型-工具-模型循环。

    这层只负责一件事：根据用户消息决定走内置指令、直达工具、模型工具决策，
    还是最终回答。会话状态、HTTP streaming、审批登记和事件持久化交给外层
    `RunOrchestrator`，具体工具执行暂时仍由 runtime 暴露的执行方法承接。
    """

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    async def run(
        self,
        messages: list[ChatMessage],
        *,
        latest_user: str,
        trace_turn_id: str,
    ) -> AsyncIterator[dict]:
        runtime = self.runtime
        if latest_user.startswith("$"):
            yield {"type": "agent_status", "status": "读取技能 SKILL.md"}
            text = runtime._skill_response_from_dollar(latest_user)
            for chunk in runtime._chunk_text(text, 36):
                yield {"type": "assistant_delta", "delta": chunk}
            yield {"type": "assistant_done_content", "content": text}
            return
        if latest_user.startswith("/"):
            yield {"type": "agent_status", "status": "处理内置指令"}
            async for event in runtime._handle_builtin_command(latest_user, messages):
                yield event
            return
        direct_tool_calls = runtime._direct_tool_calls_from_user(latest_user)
        if direct_tool_calls:
            yield {"type": "agent_status", "status": "识别到明确工具意图"}
            tool_results: list[str] = []
            async for event in runtime._run_tool_calls(direct_tool_calls):
                if event["type"] == "tool_result_json":
                    tool_results.append(event["result_json"])
                    continue
                yield event
            yield {"type": "agent_status", "status": "模型基于工具结果生成回答"}
            async for event in runtime._stream_tool_result_answer(messages, tool_results):
                yield event
            return

        working_messages = [
            ChatMessage(
                session_id="agent",
                role=ChatRole.SYSTEM,
                content=runtime._system_prompt(),
            ),
            *messages,
        ]
        used_tools = False
        all_tool_results: list[str] = []

        for round_index in range(runtime.max_tool_rounds):
            yield {"type": "agent_status", "status": f"模型决策中，第 {round_index + 1} 轮"}
            decision = await runtime._complete_tool_decision(working_messages)
            runtime._trace_generation(
                trace_turn_id,
                name=f"tool-decision-{round_index + 1}",
                messages=working_messages,
                content=str(decision["content"] or ""),
                tool_calls=decision["tool_calls"] if isinstance(decision["tool_calls"], list) else [],
            )
            decision_text = decision["content"]
            tool_calls = decision["tool_calls"] or runtime._parse_tool_calls(decision_text)
            if not tool_calls:
                if str(decision_text).strip():
                    yield {"type": "agent_status", "status": "生成最终回答"}
                    for chunk in runtime._chunk_text(str(decision_text), 36):
                        yield {"type": "assistant_delta", "delta": chunk}
                    yield {"type": "assistant_done_content", "content": str(decision_text)}
                    return
                yield {"type": "agent_status", "status": "生成最终回答"}
                async for event in runtime._stream_final(working_messages, decision_text):
                    yield event
                return

            used_tools = True
            tool_results: list[str] = []
            async for event in runtime._run_tool_calls(tool_calls):
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
                        content=decision_text or "已选择工具调用。",
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
            async for event in runtime._stream_tool_result_answer(working_messages, all_tool_results):
                yield event
