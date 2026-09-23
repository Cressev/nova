from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from ..compaction.pruner import prune_tool_results
from ..models import ChatMessage, ChatRole
from ..skills import SkillManager
from .triggers import detect_input_trigger


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
        skill_manager = getattr(runtime, "skills", None)
        if skill_manager is None:
            skill_manager = SkillManager(runtime.tools.project_root)
        trigger = detect_input_trigger(latest_user, skill_manager)
        if trigger and trigger.kind == "skill":
            yield {"type": "agent_status", "status": "读取技能 SKILL.md"}
            text = runtime._skill_response_from_dollar(latest_user)
            for chunk in runtime._chunk_text(text, 36):
                yield {"type": "assistant_delta", "delta": chunk}
            yield {"type": "assistant_done_content", "content": text}
            return
        if trigger and trigger.kind == "slash":
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

        def system_prompt(read_rounds: int = 0) -> str:
            try:
                return runtime._system_prompt(latest_user=latest_user, read_rounds=read_rounds)
            except TypeError:
                # 保持旧测试替身/第三方 runtime 的无参 prompt seam 兼容。
                return runtime._system_prompt()

        working_messages = [
            ChatMessage(session_id="agent", role=ChatRole.SYSTEM, content=system_prompt()),
            *messages,
        ]
        used_tools = False
        all_tool_results: list[str] = []
        consecutive_read_rounds = 0

        for round_index in range(runtime.max_tool_rounds):
            # dsh pre-step 语义：每个工具轮次开始前重新投影 instruction/persona/
            # memory 上下文，保证本轮工具刚修改 AGENTS.md 后下一轮即可生效。
            working_messages = [
                ChatMessage(session_id="agent", role=ChatRole.SYSTEM, content=system_prompt(consecutive_read_rounds)),
                *[message for message in working_messages if message.role != ChatRole.SYSTEM],
            ]
            yield {"type": "agent_status", "status": f"模型决策中，第 {round_index + 1} 轮"}
            # dsh 逐字输出：决策阶段直接流式，纯文本回答即时下发，
            # 工具调用轮的文本由门控拦截（不展示原始 XML）。
            decision: dict[str, object] = {"content": "", "tool_calls": []}
            # 工具决策轮的 content 只是模型的临时计划/解释，不是最终回答。
            # 先缓冲，等 decision 收口确认没有 tool_calls 后才展示；否则整段丢弃。
            pending_decision_deltas: list[str] = []
            async for event in runtime._stream_tool_decision(working_messages):
                if event.get("type") == "decision":
                    decision = event
                    if event.get("usage"):
                        # token-meter（dsh llm/token-meter）：每轮决策的真实用量
                        yield {"type": "token_usage", "usage": event["usage"]}
                    continue
                if event.get("type") == "assistant_delta":
                    pending_decision_deltas.append(str(event.get("delta") or ""))
                    continue
                yield event
            decision_text = str(decision.get("content") or "")
            tool_calls = decision.get("tool_calls") or runtime._parse_tool_calls(decision_text)
            if not tool_calls and not decision_text.strip():
                decision_text = "".join(pending_decision_deltas)
                decision["content"] = decision_text
            runtime._trace_generation(
                trace_turn_id,
                name=f"tool-decision-{round_index + 1}",
                messages=working_messages,
                content=str(decision["content"] or ""),
                tool_calls=decision["tool_calls"] if isinstance(decision["tool_calls"], list) else [],
            )
            if not tool_calls:
                if decision_text.strip():
                    yield {"type": "agent_status", "status": "生成最终回答"}
                    # 没有工具调用时，之前缓冲的决策文本才是最终正文。
                    for chunk in runtime._chunk_text(decision_text, 36):
                        yield {"type": "assistant_delta", "delta": chunk}
                    yield {"type": "assistant_done_content", "content": decision_text}
                    return
                yield {"type": "agent_status", "status": "生成最终回答"}
                async for event in runtime._stream_final(working_messages, decision_text):
                    yield event
                return

            used_tools = True
            tool_results: list[str] = []
            tool_names = [runtime.tool_orchestrator.normalize_tool_call(call)[0] for call in tool_calls]
            if tool_names and all(runtime.tools.supports_parallel(name) for name in tool_names):
                consecutive_read_rounds += 1
            else:
                consecutive_read_rounds = 0
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
                        # 工具轮的临时 content 不进入 assistant 历史，避免最终模型复述过程文本。
                        content="", 
                    ),
                    ChatMessage(
                        session_id="agent",
                        role=ChatRole.USER,
                        # dsh tool-result-pruner：超大输出先做确定性头尾剪枝再进入上下文。
                        content="工具结果：\n" + "\n".join(prune_tool_results(tool_results)),
                    ),
                ]
            )

        if used_tools:
            yield {"type": "agent_status", "status": "基于最近工具结果生成回答"}
            async for event in runtime._stream_tool_result_answer(working_messages, all_tool_results):
                yield event
