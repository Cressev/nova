from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

import re

from ..compaction import CompactionEngine, is_context_overflow_error

# 漏网工具标签清扫：<tool_call>...</tool_call>、<tool_calls>...</tool_calls>（含未闭合）。
TOOL_TAG_PATTERN = re.compile(
    r"<tool_calls?>\s*[\s\S]*?(?:</tool_calls?>|$)",
)
from ..context_budget import ContextBudgetPlan
from ..models import ChatMessage, ChatRole
from ..providers.bigmodel import ProviderError
from ..sessions import AgentSessionService, SessionStore
from .agent import CodexLikeAgentRuntime
from .orchestrator import RunOrchestrator


@dataclass(frozen=True)
class SessionRunDependencies:
    """SessionRunner 运行一轮对话需要的外部能力。

    这里用回调注入 API 层已有的 store、预算器和事件转换函数，避免 runtime
    反向依赖 FastAPI。后续 TUI/CLI 也可以传入自己的序列化和持久化实现。
    """

    store: SessionStore
    agent_sessions: AgentSessionService
    runtime_factory: Callable[[], CodexLikeAgentRuntime]
    id_factory: Callable[[str], str]
    persist_event: Callable[[dict[str, Any]], None]
    runtime_event_from_agent_event: Callable[[dict, Callable[..., dict]], dict | None]
    build_context_budget_plan: Callable[..., ContextBudgetPlan]
    context_window_tokens: int
    project_root_provider: Callable[[], Any]
    global_agent_file_provider: Callable[[], Any]
    tool_orchestrator_factory: Callable[[], Any]
    event_builder_for_existing_turn: Callable[[str, str], Callable[..., dict[str, Any]]]
    denied_tool_message_builder: Callable[..., str]
    # 压缩引擎工厂（dsh compaction 能力注入点；测试可替换为 stub）。
    compaction_engine_factory: Callable[[], Any] | None = None


class SessionRunner:
    """运行一个 chat session 的 turn 流。

    API 层负责把这里产出的 dict 编码成 NDJSON；SessionRunner 负责用户消息落库、
    上下文预算、AgentLoop 调用、取消检查、错误落库和排队消息续跑。
    """

    def __init__(self, deps: SessionRunDependencies) -> None:
        self.deps = deps

    async def approve_tool_call(self, approval_id: str) -> dict | None:
        """批准一个 pending tool call，并从 checkpoint 继续执行。

        API/TUI 只表达“用户批准了”，真正的工具续跑、事件转换和持久化都在这里
        统一完成，避免不同入口各自拼执行器导致行为漂移。
        """

        item = self.deps.agent_sessions.approve_pending_approval(approval_id)
        if item is None:
            return None

        events, result_json = await self.deps.tool_orchestrator_factory().resume_approved_tool(
            item.call_id,
            item.tool,
            item.arguments,
        )
        build_event = self.deps.event_builder_for_existing_turn(
            item.session_id,
            item.turn_id,
        )
        approved_event = build_event(
            "permission.approved",
            category="permission",
            phase="approved",
            title=f"已允许：{item.tool}",
            message="用户已批准该工具调用，Nova 将从 pending checkpoint 继续执行。",
            tool=item.tool,
            call_id=item.call_id,
            arguments=item.arguments,
            data={
                "permission": item.permission,
                "risk": item.risk,
                "checkpoint_event_id": item.checkpoint_event_id,
            },
        )
        self._persist_existing_runtime_event(approved_event)
        runtime_events: list[dict[str, Any]] = []
        runtime_events.append(approved_event)
        for event in events:
            runtime_event = self.deps.runtime_event_from_agent_event(event, build_event)
            if runtime_event is None:
                continue
            self._persist_existing_runtime_event(runtime_event)
            runtime_events.append(runtime_event)

        return {
            "ok": True,
            "status": "approved",
            "approval": item.as_dict(),
            "events": events,
            "runtime_events": runtime_events,
            "result_json": result_json,
        }

    async def answer_question(self, approval_id: str, answers: dict) -> dict | None:
        """ask_user_question 的回答链路（dsh 语义）。

        回答合成工具结果后，模型基于该结果继续本轮：续答复用 agent 的
        tool-result 应答形态（历史 + 结果提示词），产物落库并随响应返回，
        前端原地渲染，不需要用户再发一条消息。
        """
        item = self.deps.agent_sessions.answer_pending_approval(approval_id, answers)
        if item is None:
            return None
        events, result_json = await self.deps.tool_orchestrator_factory().resume_approved_tool(
            item.call_id,
            item.tool,
            item.arguments,
        )
        build_event = self.deps.event_builder_for_existing_turn(
            item.session_id,
            item.turn_id,
        )
        answered_event = build_event(
            "permission.approved",
            category="permission",
            phase="approved",
            title="ask_user_question (answered)",
            message="用户已回答问题，回答将作为工具结果继续本轮。",
            tool=item.tool,
            call_id=item.call_id,
            arguments=item.arguments,
            data={"permission": item.permission, "risk": item.risk},
        )
        self._persist_existing_runtime_event(answered_event)
        runtime_events: list[dict[str, Any]] = [answered_event]
        for event in events:
            runtime_event = self.deps.runtime_event_from_agent_event(event, build_event)
            if runtime_event is None:
                continue
            self._persist_existing_runtime_event(runtime_event)
            runtime_events.append(runtime_event)

        # 模型续答：工具结果（含答案）作为提示喂给模型，产物作为 assistant 消息落库。
        assistant_message: dict[str, Any] | None = None
        runtime = self.deps.runtime_factory()
        if runtime.provider.is_configured():
            history = [
                message
                for message in self.deps.store.list_chat_messages(item.session_id)
                if message.role != ChatRole.ASSISTANT or message.content.strip()
            ]
            continuation = ChatMessage(
                session_id=item.session_id,
                role=ChatRole.USER,
                content=(
                    "你刚才调用了 ask_user_question 向用户提问，用户已经回答。"
                    "请基于真实工具结果继续完成本轮任务，中文直接回答，不要编造，"
                    "不要再输出 <tool_call>。\n\n工具结果 JSON：\n" + str(result_json)
                ),
            )
            parts: list[str] = []
            try:
                async for event in runtime.provider.stream([*history, continuation]):
                    if isinstance(event, str):
                        parts.append(event)
            except ProviderError as error:
                parts = [f"续答失败：{error}"]
            text = "".join(parts).strip() or "（模型没有返回续答内容）"
            message = ChatMessage(
                session_id=item.session_id,
                role=ChatRole.ASSISTANT,
                content=text,
            )
            self.deps.store.add_chat_message(message)
            assistant_message = message.model_dump(mode="json")

        return {
            "ok": True,
            "status": "answered",
            "approval": item.as_dict(),
            "events": events,
            "runtime_events": runtime_events,
            "result_json": result_json,
            "message": assistant_message,
        }

    def deny_tool_call(self, approval_id: str, *, reason: str) -> dict | None:
        """拒绝 pending tool call，并把拒绝结果写回会话上下文。"""

        item = self.deps.agent_sessions.deny_pending_approval(
            approval_id,
            reason=reason,
        )
        if item is None:
            return None

        event = self.deps.event_builder_for_existing_turn(
            item.session_id,
            item.turn_id,
        )(
            "permission.denied",
            category="permission",
            phase="denied",
            status="failed",
            title=f"已拒绝：{item.tool}",
            message=reason,
            tool=item.tool,
            call_id=item.call_id,
            arguments=item.arguments,
            data={
                "permission": item.permission,
                "risk": item.risk,
                "checkpoint_event_id": item.checkpoint_event_id,
            },
        )
        self._persist_existing_runtime_event(event)
        assistant_message = ChatMessage(
            session_id=item.session_id,
            role=ChatRole.ASSISTANT,
            content=self.deps.denied_tool_message_builder(
                tool=item.tool,
                arguments=item.arguments,
                reason=reason,
            ),
        )
        self.deps.store.add_chat_message(assistant_message)
        return {
            "ok": True,
            "status": "denied",
            "approval": item.as_dict(),
            "event": event,
            "message": assistant_message.model_dump(mode="json"),
        }

    async def run_message(self, session_id: str, content: str) -> AsyncIterator[dict]:
        first_message = ChatMessage(
            session_id=session_id,
            role=ChatRole.USER,
            content=content,
        )
        async for event in self.run_turn(session_id, first_message, emit_user=True):
            yield event
        if self.deps.agent_sessions.is_cancel_requested(session_id):
            return
        while True:
            queued = self.deps.agent_sessions.pop_queued_message(session_id)
            if queued is None:
                break
            yield {
                "type": "queued_message",
                "message": queued.model_dump(mode="json"),
            }
            async for event in self.run_turn(session_id, queued, emit_user=False):
                yield event
            if self.deps.agent_sessions.is_cancel_requested(session_id):
                return

    async def run_turn(
        self,
        session_id: str,
        user_message: ChatMessage,
        *,
        emit_user: bool,
    ) -> AsyncIterator[dict]:
        turn_id = self.deps.id_factory("turn")
        orchestrator = RunOrchestrator(
            session_id=session_id,
            turn_id=turn_id,
            agent_sessions=self.deps.agent_sessions,
            persist_event=self.deps.persist_event,
            id_factory=self.deps.id_factory,
        )

        # dsh 语义：系统提示词在会话最开始拼接一次（轨迹表首行 SYSTEM，
        # 早于任何 user/tool 记录），而不是把思考内容当 sys 记录追加在轮末。
        existing_events = self.deps.store.list_chat_events(session_id)
        if not any(e.event_type == "system.prompt" for e in existing_events) and not any(
            e.event_type.startswith("turn.") for e in existing_events
        ):
            prompt_runtime = self.deps.runtime_factory()
            prompt_provider = getattr(prompt_runtime, "_system_prompt", None)
            prompt_text = prompt_provider() if callable(prompt_provider) else None
            if prompt_text:
                orchestrator.event(
                    "system.prompt",
                    category="system",
                    phase="completed",
                    title="System prompt",
                    message=prompt_text,
                )

        if emit_user:
            self.deps.store.add_chat_message(user_message)
            yield {
                "type": "user_message",
                "message": user_message.model_dump(mode="json"),
            }
        else:
            existing_message_ids = {
                message.id for message in self.deps.store.list_chat_messages(session_id)
            }
            if user_message.id not in existing_message_ids:
                self.deps.store.add_chat_message(user_message)

        started = orchestrator.start_turn(
            user_message_id=user_message.id,
            message=user_message.content,
        )
        yield {"type": "runtime_event", "event": started}

        answer_parts: list[str] = []

        reasoning_parts: list[str] = []
        engine = self._compaction_engine()
        try:
            # /compact 手动路径：与运行中的轮次天然串行（作为用户消息排队进入），
            # 对应 dsh command-compact 的排队语义；空闲会话立即执行。
            stripped = user_message.content.strip()
            if stripped == "/compact" or stripped.startswith("/compact "):
                extra = stripped[len("/compact"):].strip()
                async for event in self._run_manual_compaction(
                    orchestrator, engine, session_id, extra
                ):
                    yield event
                return

            # pre-step 压力压缩（dsh agent/pre-step 瀑布位）：先压缩再推导请求。
            async for event in self._pre_step_compaction(orchestrator, engine, session_id):
                yield event

            # surface 投影：被 checkpoint 遮蔽的历史不再进入模型请求。
            history = [
                message
                for message in engine.surface_messages(session_id)
                if message.id != user_message.id
            ] if engine is not None else [
                message
                for message in self.deps.store.list_chat_messages(session_id)
                if message.id != user_message.id
            ]
            all_turn_messages = [*history, user_message]
            budget = self.deps.build_context_budget_plan(
                session_id=session_id,
                messages=all_turn_messages,
                events=self.deps.store.list_chat_events(session_id),
                context_window_tokens=self.deps.context_window_tokens,
            )

            budgeted = orchestrator.event(
                "context.budgeted",
                category="status",
                phase="update",
                title="上下文预算已应用",
                message=(
                    f"保留 {budget.retained_message_count} 条最近消息，"
                    f"裁剪 {budget.dropped_message_count} 条历史消息，"
                    f"关键工具结果 {budget.key_tool_result_count} 条。"
                ),
                data={
                    **budget.as_dict(),
                    "message_ids": [message.id for message in budget.messages],
                },
            )
            yield {"type": "runtime_event", "event": budgeted}

            # 溢出恢复（dsh request-error 位）：请求超长失败 → 压缩 → 重试一次。
            overflow_retry_used = False
            while True:
                runtime = self.deps.runtime_factory()
                try:
                    stream = runtime.stream(budget.messages, trace_turn_id=turn_id)
                except TypeError:
                    stream = runtime.stream(budget.messages)
                try:
                    async for event in stream:
                        if orchestrator.is_cancel_requested():
                            yield {"type": "runtime_event", "event": orchestrator.cancel_turn()}
                            return
                        runtime_event = self.deps.runtime_event_from_agent_event(
                            event,
                            orchestrator.event,
                        )
                        if runtime_event is not None:
                            yield {"type": "runtime_event", "event": runtime_event}
                        if event["type"] in {"permission_request", "user_question"}:
                            orchestrator.register_permission_request(
                                event,
                                runtime_event=runtime_event,
                            )
                        if event["type"] == "reasoning_delta":
                            # 思考增量：直接下发（不入 answer_parts，正文与思考分离）
                            reasoning_parts.append(str(event.get("delta") or ""))
                            yield event
                            continue
                        if event["type"] == "assistant_delta":
                            answer_parts.append(event["delta"])
                            yield event
                            if orchestrator.is_cancel_requested():
                                yield {"type": "runtime_event", "event": orchestrator.cancel_turn()}
                                return
                            continue
                        if event["type"] == "assistant_done_content":
                            if not answer_parts and event.get("content"):
                                answer_parts.append(str(event.get("content")))
                            continue
                        yield event
                    break
                except ProviderError as stream_exc:
                    if (
                        not overflow_retry_used
                        and not answer_parts
                        and engine is not None
                        and is_context_overflow_error(stream_exc)
                    ):
                        overflow_retry_used = True
                        retry_events: list[dict[str, Any]] = []
                        result = await engine.compact_if_needed(
                            session_id,
                            trigger="context-overflow",
                            recorder=orchestrator.event,
                            events_out=retry_events,
                        )
                        for retry_event in retry_events:
                            yield {"type": "runtime_event", "event": retry_event}
                        if result is not None:
                            history = [
                                message
                                for message in engine.surface_messages(session_id)
                                if message.id != user_message.id
                            ]
                            budget = self.deps.build_context_budget_plan(
                                session_id=session_id,
                                messages=[*history, user_message],
                                events=self.deps.store.list_chat_events(session_id),
                                context_window_tokens=self.deps.context_window_tokens,
                            )
                            continue
                    raise

            # 保险带：任何漏网的工具标签都不允许进入持久化消息
            # （前端渲染器遇到原始 XML 会崩，历史气泡全部丢失）。
            answer_text = TOOL_TAG_PATTERN.sub("", "".join(answer_parts)).strip()
            assistant_message = ChatMessage(
                session_id=session_id,
                role=ChatRole.ASSISTANT,
                content=answer_text,
            )
            self.deps.store.add_chat_message(assistant_message)
            # 思考过程持久化：turn 收口时落一条 reasoning.completed 事件，
            # 前端恢复会话时把思考渲染为 Think 披露行（时间位置=工具调用之前）。
            reasoning_text = "".join(reasoning_parts).strip()
            if reasoning_text:
                reasoning_event = orchestrator.event(
                    "reasoning.completed",
                    category="reasoning",
                    phase="completed",
                    title="模型思考",
                    message=reasoning_text,
                    data={"chars": len(reasoning_text)},
                )
                yield {"type": "runtime_event", "event": reasoning_event}
            completed = orchestrator.complete_turn(
                message_id=assistant_message.id,
                content=assistant_message.content,
            )
            yield {"type": "runtime_event", "event": completed}
            yield {
                "type": "assistant_done",
                "message": assistant_message.model_dump(mode="json"),
            }
        except ProviderError as exc:
            failed = orchestrator.fail_turn(title="模型调用失败", message=str(exc))
            error_message = ChatMessage(
                session_id=session_id,
                role=ChatRole.ERROR,
                content=str(exc),
            )
            self.deps.store.add_chat_message(error_message)
            yield {"type": "runtime_event", "event": failed}
            yield {"type": "error", "message": error_message.model_dump(mode="json")}
        except Exception as exc:
            detail = str(exc) or repr(exc)
            failed = orchestrator.fail_turn(
                title="运行时异常",
                message=f"{type(exc).__name__}: {detail}",
            )
            error_message = ChatMessage(
                session_id=session_id,
                role=ChatRole.ERROR,
                content=f"Nova 运行时异常：{type(exc).__name__}: {detail}",
            )
            self.deps.store.add_chat_message(error_message)
            yield {"type": "runtime_event", "event": failed}
            yield {"type": "error", "message": error_message.model_dump(mode="json")}

    def _compaction_engine(self) -> CompactionEngine | None:
        """压缩引擎（dsh ctx.compaction 注入点）；未注入时退化为纯预算裁剪。"""
        factory = getattr(self.deps, "compaction_engine_factory", None)
        if factory is None:
            return None
        return factory()

    async def _pre_step_compaction(
        self,
        orchestrator: RunOrchestrator,
        engine: CompactionEngine | None,
        session_id: str,
    ) -> AsyncIterator[dict]:
        """dsh agent/pre-step 压力位：达到阈值先压缩，再推导本轮请求。"""
        if engine is None:
            return
        used = engine.measure_surface_tokens(session_id)
        if used < engine.threshold_tokens():
            return
        events: list[dict[str, Any]] = []
        await engine.compact_if_needed(
            session_id,
            trigger="pressure",
            recorder=orchestrator.event,
            events_out=events,
        )
        for event in events:
            yield {"type": "runtime_event", "event": event}

    async def _run_manual_compaction(
        self,
        orchestrator: RunOrchestrator,
        engine: CompactionEngine | None,
        session_id: str,
        extra_instruction: str,
    ) -> AsyncIterator[dict]:
        """dsh command-compact 语义：显式把历史压缩为 checkpoint 并回流上下文。"""
        if engine is None:
            fallback = orchestrator.event(
                "compaction.unavailable",
                category="compaction",
                phase="failed",
                status="failed",
                title="压缩引擎不可用",
                message="当前部署未注入压缩引擎。",
            )
            yield {"type": "runtime_event", "event": fallback}
            return
        events: list[dict[str, Any]] = []
        result = await engine.compact_now(
            session_id,
            recorder=orchestrator.event,
            events_out=events,
            instruction=extra_instruction,
        )
        for event in events:
            yield {"type": "runtime_event", "event": event}
        if result is None:
            content = "压缩未能产生新的检查点（可能没有可压缩区域，或摘要生成失败）；上下文保持不变。"
        else:
            preview = result.summary.strip().splitlines()[0][:200] if result.summary.strip() else ""
            content = (
                f"已压缩 {len(result.shadowed_message_ids)} 条较早历史"
                f"（约 {result.shadowed_token_count} tokens）为检查点。{preview}"
            )
        assistant_message = ChatMessage(
            session_id=session_id,
            role=ChatRole.ASSISTANT,
            content=content,
        )
        self.deps.store.add_chat_message(assistant_message)
        completed = orchestrator.complete_turn(
            message_id=assistant_message.id,
            content=assistant_message.content,
        )
        yield {"type": "runtime_event", "event": completed}
        yield {"type": "assistant_done", "message": assistant_message.model_dump(mode="json")}

    def _persist_existing_runtime_event(self, event: dict[str, Any]) -> None:
        self.deps.persist_event(event)
        self.deps.agent_sessions.record_runtime_event(event)
