from __future__ import annotations

from fastapi import APIRouter

from . import routes as ctx

router = APIRouter()


@router.post("/api/chat/sessions/{session_id}/cancel")
async def cancel_chat_session_turn(session_id: str) -> dict:
    if ctx.store.get_chat_session(session_id) is None:
        raise ctx.HTTPException(status_code=404, detail="Chat session not found")
    ctx.agent_sessions.request_cancel(session_id)
    cancelled_tools: list[str] = []
    runtime = ctx.agent_sessions.runtime_state(session_id)
    for item in runtime.get("tool_calls", []):
        if item.get("status") not in {"running", "started"}:
            continue
        call_id = str(item.get("call_id") or "")
        if not call_id:
            continue
        try:
            ctx.process_manager.cancel_call(call_id)
            cancelled_tools.append(call_id)
        except KeyError:
            continue
    return {
        "ok": True,
        "session_id": session_id,
        "cancel_requested": True,
        "active": ctx.agent_sessions.is_active(session_id),
        "cancelled_tool_calls": cancelled_tools,
    }


@router.get("/api/chat/sessions", response_model=list[ctx.ChatSession])
async def list_chat_sessions() -> list[ctx.ChatSession]:
    return ctx.store.list_chat_sessions()


@router.post("/api/chat/sessions", response_model=ctx.ChatSession, status_code=201)
async def create_chat_session(payload: ctx.ChatSessionCreate) -> ctx.ChatSession:
    session = ctx.ChatSession(
        id=ctx.new_id("chat"),
        title=payload.title or "新对话",
        workspace=str(ctx.workspace_manager.current_root),
    )
    ctx.store.create_chat_session(session)
    return session


@router.delete("/api/chat/sessions/{session_id}", status_code=204)
async def delete_chat_session(session_id: str) -> ctx.Response:
    session = ctx.store.get_chat_session(session_id)
    if session is None:
        raise ctx.HTTPException(status_code=404, detail="Chat session not found")
    ctx.store.delete_chat_session(session.id)
    return ctx.Response(status_code=204)


@router.get(
    "/api/chat/sessions/{session_id}/messages", response_model=list[ctx.ChatMessage]
)
async def list_chat_messages(session_id: str) -> list[ctx.ChatMessage]:
    if ctx._get_current_chat_session(session_id, auto_switch=True) is None:
        raise ctx.HTTPException(status_code=404, detail="Chat session not found")
    return ctx.store.list_chat_messages(session_id)


@router.get("/api/chat/sessions/{session_id}/timeline")
async def list_chat_timeline(session_id: str) -> dict:
    if ctx._get_current_chat_session(session_id, auto_switch=True) is None:
        raise ctx.HTTPException(status_code=404, detail="Chat session not found")
    return {"items": ctx._chat_timeline_items(session_id)}


@router.get("/api/chat/sessions/{session_id}/runtime-state")
async def chat_runtime_state(session_id: str) -> dict:
    unavailable_reason = None
    try:
        session = ctx._get_current_chat_session(session_id, auto_switch=True)
    except ctx.HTTPException as exc:
        if exc.status_code != 409:
            raise
        session = ctx.store.get_chat_session(session_id)
        unavailable_reason = str(exc.detail)
    if session is None:
        raise ctx.HTTPException(status_code=404, detail="Chat session not found")
    runtime = ctx.agent_sessions.runtime_state(session_id)
    return {
        "session": session.model_dump(mode="json"),
        "timeline": {
            "items": [] if unavailable_reason else ctx._chat_timeline_items(session_id)
        },
        "runtime": runtime,
        "pending_approvals": [
            item.as_dict()
            for item in ctx.agent_sessions.list_pending_approvals(session_id=session_id)
        ],
        "processes": ctx.process_manager.list_jobs(),
        "active": ctx.agent_sessions.is_active(session_id),
        "queued_messages": runtime["queued_messages"],
        "unavailable": bool(unavailable_reason),
        "unavailable_reason": unavailable_reason,
    }


@router.post("/api/chat/sessions/{session_id}/messages", response_model=ctx.ChatMessage)
async def create_chat_message(
    session_id: str, payload: ctx.ChatMessageCreate
) -> ctx.ChatMessage:
    session = ctx._get_current_chat_session(session_id, auto_switch=True)
    if session is None:
        raise ctx.HTTPException(status_code=404, detail="Chat session not found")

    user_message = ctx.ChatMessage(
        session_id=session_id,
        role=ctx.ChatRole.USER,
        content=payload.content,
    )
    ctx.store.add_chat_message(user_message)

    ctx.store.upsert_chat_event(
        ctx.ChatEvent(
            session_id=session_id,
            type="turn",
            event_type="message.received",
            phase="started",
            title="收到用户消息",
            message="Nova 正在调用 GLM-4.7 生成回复。",
            data={"message_id": user_message.id},
        )
    )

    # 系统提示每次动态拼接，不落盘，避免后续记忆污染或难以调整。
    messages = [
        ctx.ChatMessage(
            session_id=session_id,
            role=ctx.ChatRole.SYSTEM,
            content=(
                "你是 Nova，一个本地优先的个人开发 Agent。"
                "请用中文回答，保持直接、务实，并优先帮助用户推进软件开发任务。"
            ),
        ),
        *ctx.store.list_chat_messages(session_id),
    ]

    try:
        answer = await ctx.provider.complete(messages)
        assistant_message = ctx.ChatMessage(
            session_id=session_id,
            role=ctx.ChatRole.ASSISTANT,
            content=answer,
        )
        ctx.store.add_chat_message(assistant_message)
        ctx.store.upsert_chat_event(
            ctx.ChatEvent(
                session_id=session_id,
                type="turn",
                event_type="message.completed",
                phase="completed",
                title="GLM-4.7 回复完成",
                message="模型已返回 assistant 消息。",
                data={"message_id": assistant_message.id},
            )
        )
        return assistant_message
    except ctx.ProviderError as exc:
        error_message = ctx.ChatMessage(
            session_id=session_id,
            role=ctx.ChatRole.ERROR,
            content=str(exc),
        )
        ctx.store.add_chat_message(error_message)
        ctx.store.upsert_chat_event(
            ctx.ChatEvent(
                session_id=session_id,
                type="turn",
                event_type="message.failed",
                phase="failed",
                title="GLM-4.7 调用失败",
                message=str(exc),
                status="error",
            )
        )
        return error_message


@router.post("/api/chat/sessions/{session_id}/stream")
async def stream_chat_message(
    session_id: str,
    payload: ctx.ChatMessageCreate,
) -> ctx.Response:
    if ctx._get_current_chat_session(session_id, auto_switch=True) is None:
        raise ctx.HTTPException(status_code=404, detail="Chat session not found")
    if ctx.agent_sessions.is_active(session_id):
        queued = ctx.ChatMessage(
            session_id=session_id,
            role=ctx.ChatRole.USER,
            content=payload.content,
        )
        ctx.store.add_chat_message(queued)
        ctx.agent_sessions.enqueue_message(session_id, queued)
        return ctx.JSONResponse(
            status_code=202,
            content={
                "ok": True,
                "status": "queued",
                "message": queued.model_dump(mode="json"),
            },
        )
    ctx.agent_sessions.mark_active(session_id)

    async def emit() -> ctx.AsyncIterator[str]:
        async def run_turn(
            user_message: ctx.ChatMessage, *, emit_user: bool
        ) -> ctx.AsyncIterator[str]:
            turn_id = ctx.new_id("turn")
            orchestrator = ctx.RunOrchestrator(
                session_id=session_id,
                turn_id=turn_id,
                agent_sessions=ctx.agent_sessions,
                persist_event=ctx._persist_runtime_event,
                id_factory=ctx.new_id,
            )

            if emit_user:
                ctx.store.add_chat_message(user_message)
                yield ctx._ndjson(
                    {
                        "type": "user_message",
                        "message": user_message.model_dump(mode="json"),
                    }
                )

            started = orchestrator.start_turn(
                user_message_id=user_message.id,
                message=user_message.content,
            )
            yield ctx._ndjson({"type": "runtime_event", "event": started})

            answer_parts: list[str] = []
            try:

                def cancel_event() -> dict:
                    return orchestrator.cancel_turn()

                history = [
                    message
                    for message in ctx.store.list_chat_messages(session_id)
                    if message.id != user_message.id
                ]
                all_turn_messages = [*history, user_message]
                budget = ctx.build_context_budget_plan(
                    session_id=session_id,
                    messages=all_turn_messages,
                    events=ctx.store.list_chat_events(session_id),
                    context_window_tokens=ctx.settings.context_window_tokens,
                )
                if (
                    budget.should_auto_compact
                    and not user_message.content.lstrip().startswith("/compact")
                ):
                    memory = ctx.ProjectMemory(
                        ctx.workspace_manager.current_root,
                        global_agent_file=ctx.settings.global_agent_file,
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
                            "covered_messages": int(
                                result.get("covered_messages") or 0
                            ),
                            "trigger": "auto_context_budget",
                        },
                    )
                    yield ctx._ndjson({"type": "runtime_event", "event": compacted})

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
                yield ctx._ndjson({"type": "runtime_event", "event": budgeted})
                turn_messages = budget.messages
                async for event in ctx._agent_runtime().stream(turn_messages):
                    if orchestrator.is_cancel_requested():
                        yield ctx._ndjson(
                            {"type": "runtime_event", "event": cancel_event()}
                        )
                        return
                    runtime = ctx._runtime_event_from_agent_event(
                        event, orchestrator.event
                    )
                    if runtime is not None:
                        yield ctx._ndjson({"type": "runtime_event", "event": runtime})
                    if event["type"] == "permission_request":
                        orchestrator.register_permission_request(
                            event, runtime_event=runtime
                        )
                    if event["type"] == "assistant_delta":
                        answer_parts.append(event["delta"])
                        yield ctx._ndjson(event)
                        if orchestrator.is_cancel_requested():
                            yield ctx._ndjson(
                                {"type": "runtime_event", "event": cancel_event()}
                            )
                            return
                        continue
                    if event["type"] == "assistant_done_content":
                        if not answer_parts and event.get("content"):
                            answer_parts.append(event["content"])
                        continue
                    yield ctx._ndjson(event)

                assistant_message = ctx.ChatMessage(
                    session_id=session_id,
                    role=ctx.ChatRole.ASSISTANT,
                    content="".join(answer_parts),
                )
                ctx.store.add_chat_message(assistant_message)
                completed = orchestrator.complete_turn(
                    message_id=assistant_message.id,
                    content=assistant_message.content,
                )
                yield ctx._ndjson({"type": "runtime_event", "event": completed})
                yield ctx._ndjson(
                    {
                        "type": "assistant_done",
                        "message": assistant_message.model_dump(mode="json"),
                    }
                )
            except ctx.ProviderError as exc:
                failed = orchestrator.fail_turn(
                    title="模型调用失败",
                    message=str(exc),
                )
                error_message = ctx.ChatMessage(
                    session_id=session_id,
                    role=ctx.ChatRole.ERROR,
                    content=str(exc),
                )
                ctx.store.add_chat_message(error_message)
                yield ctx._ndjson({"type": "runtime_event", "event": failed})
                yield ctx._ndjson(
                    {"type": "error", "message": error_message.model_dump(mode="json")}
                )
            except Exception as exc:
                detail = str(exc) or repr(exc)
                failed = orchestrator.fail_turn(
                    title="运行时异常",
                    message=f"{type(exc).__name__}: {detail}",
                )
                error_message = ctx.ChatMessage(
                    session_id=session_id,
                    role=ctx.ChatRole.ERROR,
                    content=f"Nova 运行时异常：{type(exc).__name__}: {detail}",
                )
                ctx.store.add_chat_message(error_message)
                yield ctx._ndjson({"type": "runtime_event", "event": failed})
                yield ctx._ndjson(
                    {"type": "error", "message": error_message.model_dump(mode="json")}
                )

        first_message = ctx.ChatMessage(
            session_id=session_id,
            role=ctx.ChatRole.USER,
            content=payload.content,
        )
        try:
            async for chunk in run_turn(first_message, emit_user=True):
                yield chunk
            if ctx.agent_sessions.is_cancel_requested(session_id):
                return
            while True:
                queued_messages = ctx.agent_sessions.drain_queued_messages(session_id)
                if not queued_messages:
                    break
                for queued in queued_messages:
                    yield ctx._ndjson(
                        {
                            "type": "queued_message",
                            "message": queued.model_dump(mode="json"),
                        }
                    )
                    async for chunk in run_turn(queued, emit_user=False):
                        yield chunk
                    if ctx.agent_sessions.is_cancel_requested(session_id):
                        return
        finally:
            ctx.agent_sessions.mark_idle(session_id)

    return ctx.StreamingResponse(emit(), media_type="application/x-ndjson")


@router.get("/api/chat/sessions/{session_id}/trace")
async def get_chat_session_trace(session_id: str) -> dict:
    if ctx.store.get_chat_session(session_id) is None:
        raise ctx.HTTPException(status_code=404, detail="Chat session not found")
    return {"items": ctx.store.trace.read(session_id)}
