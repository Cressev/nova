from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from ..context_budget import ContextBudgetPlan
from ..memory import ProjectMemory
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
        runtime_events: list[dict[str, Any]] = []
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
        try:
            history = [
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
            async for event in self._maybe_auto_compact(
                orchestrator,
                all_turn_messages=all_turn_messages,
                user_message=user_message,
                budget=budget,
            ):
                yield event

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

            async for event in self.deps.runtime_factory().stream(budget.messages):
                if orchestrator.is_cancel_requested():
                    yield {"type": "runtime_event", "event": orchestrator.cancel_turn()}
                    return
                runtime_event = self.deps.runtime_event_from_agent_event(
                    event,
                    orchestrator.event,
                )
                if runtime_event is not None:
                    yield {"type": "runtime_event", "event": runtime_event}
                if event["type"] == "permission_request":
                    orchestrator.register_permission_request(
                        event,
                        runtime_event=runtime_event,
                    )
                if event["type"] == "assistant_delta":
                    answer_parts.append(event["delta"])
                    yield event
                    if orchestrator.is_cancel_requested():
                        yield {"type": "runtime_event", "event": orchestrator.cancel_turn()}
                        return
                    continue
                if event["type"] == "assistant_done_content":
                    if not answer_parts and event.get("content"):
                        answer_parts.append(str(event["content"]))
                    continue
                yield event

            assistant_message = ChatMessage(
                session_id=session_id,
                role=ChatRole.ASSISTANT,
                content="".join(answer_parts),
            )
            self.deps.store.add_chat_message(assistant_message)
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

    async def _maybe_auto_compact(
        self,
        orchestrator: RunOrchestrator,
        *,
        all_turn_messages: list[ChatMessage],
        user_message: ChatMessage,
        budget: ContextBudgetPlan,
    ) -> AsyncIterator[dict]:
        if not budget.should_auto_compact:
            return
        if user_message.content.lstrip().startswith("/compact"):
            return
        memory = ProjectMemory(
            self.deps.project_root_provider(),
            global_agent_file=self.deps.global_agent_file_provider(),
        )
        result = memory.compact_session(
            all_turn_messages,
            instruction="自动上下文预算触发：保留关键事实、当前目标、最近决策和未完成事项。",
        )
        compacted = orchestrator.event(
            "memory.compacted",
            category="status",
            phase="completed",
            title="自动上下文压缩",
            message="上下文预算接近上限，已自动执行 /compact 并写入会话摘要。",
            data={
                "summary": str(result.get("summary") or ""),
                "path": str(result.get("path") or ""),
                "covered_messages": int(result.get("covered_messages") or 0),
                "trigger": "auto_context_budget",
            },
        )
        yield {"type": "runtime_event", "event": compacted}

    def _persist_existing_runtime_event(self, event: dict[str, Any]) -> None:
        self.deps.persist_event(event)
        self.deps.agent_sessions.record_runtime_event(event)
