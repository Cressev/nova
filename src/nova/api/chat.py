from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from . import routes as ctx

router = APIRouter()


# ---- 划词评论问答（旁路线程；不写主会话 messages/events） ----

_comment_store: ctx.CommentStore | None = None


def _comments() -> ctx.CommentStore:
    """评论存储单例（与 chats.json 同目录的 comments.json）。"""
    global _comment_store
    if _comment_store is None:
        _comment_store = ctx.CommentStore(ctx.store.state_dir)
    return _comment_store


class CommentAsk(BaseModel):
    """评论提问载荷：新提问带锚点字段；追问只带 anchor_id。"""

    anchor_id: str | None = None
    message_id: str | None = None
    quote: str | None = None
    prefix: str | None = None
    suffix: str | None = None
    occurrence: int = 0
    question: str


@router.get("/api/chat/sessions/{session_id}/comments")
async def list_comment_threads(session_id: str) -> dict:
    if ctx.store.get_chat_session(session_id) is None:
        raise ctx.HTTPException(status_code=404, detail="Chat session not found")
    return {"threads": _comments().list_threads(session_id)}


@router.post("/api/chat/sessions/{session_id}/comments/stream")
async def stream_comment_answer(session_id: str, payload: CommentAsk) -> ctx.Response:
    if ctx.store.get_chat_session(session_id) is None:
        raise ctx.HTTPException(status_code=404, detail="Chat session not found")
    question = (payload.question or "").strip()
    if not question:
        raise ctx.HTTPException(status_code=400, detail="问题不能为空")

    comments = _comments()
    # 解析锚点：追问用已有锚点；新提问现建锚点
    if payload.anchor_id:
        anchor = comments.get_anchor(payload.anchor_id)
        if anchor is None:
            raise ctx.HTTPException(status_code=404, detail="评论线程不存在")
    else:
        if not payload.message_id or not payload.quote:
            raise ctx.HTTPException(status_code=400, detail="缺少锚点信息（message_id/quote）")
        if ctx.store.get_chat_message(payload.message_id) is None:
            raise ctx.HTTPException(status_code=404, detail="被引用的消息不存在")
        anchor = comments.create_anchor(
            session_id=session_id,
            message_id=payload.message_id,
            quote=payload.quote.strip()[:2000],
            prefix=payload.prefix or "",
            suffix=payload.suffix or "",
            occurrence=payload.occurrence,
        )
    comments.add_entry(anchor.id, "user", question)

    async def emit():
        yield ctx._ndjson({"type": "thread_started", "anchor": anchor.model_dump(mode="json")})
        answer_parts: list[str] = []
        try:
            messages = ctx.build_comment_messages(ctx.store, comments, anchor, question)
            async for chunk in ctx.provider.stream(messages):
                # provider.stream 的结构块是 {"type": "reasoning_delta", "text": ...}
                # ——思考过程也透传给前端流式显示；字符串块是正文增量。
                if isinstance(chunk, str):
                    answer_parts.append(chunk)
                    yield ctx._ndjson({"type": "delta", "text": chunk})
                elif isinstance(chunk, dict) and chunk.get("type") == "reasoning_delta":
                    reasoning = str(chunk.get("text") or "")
                    if reasoning:
                        yield ctx._ndjson({"type": "reasoning_delta", "text": reasoning})
        except Exception as exc:  # noqa: BLE001 - 旁路错误直接回传前端
            yield ctx._ndjson({"type": "error", "message": str(exc)})
            return
        answer = "".join(answer_parts)
        entry = comments.add_entry(anchor.id, "assistant", answer)
        yield ctx._ndjson({"type": "done", "entry": entry.model_dump(mode="json")})

    return ctx.StreamingResponse(emit(), media_type="application/x-ndjson")


@router.delete("/api/chat/sessions/{session_id}/anchors/{anchor_id}", status_code=204)
async def delete_comment_thread(session_id: str, anchor_id: str) -> ctx.Response:
    if not _comments().delete_anchor(session_id, anchor_id):
        raise ctx.HTTPException(status_code=404, detail="评论线程不存在")
    return ctx.Response(status_code=204)


@router.post("/api/chat/sessions/{session_id}/fork", status_code=201)
async def fork_chat_session(session_id: str, payload: ctx.ChatSessionFork) -> ctx.ChatSession:
    """会话 fork（dsh session.fork 对齐）：从 source 切片创建子会话。

    at_seq 指定事件边界；省略取最后一个事件。子会话继承父会话到该边界的
    全部对话历史 + 事件，之后是新分支。标题自动递增（Title (N)）。
    """
    source = ctx.store.get_chat_session(session_id)
    if source is None:
        raise ctx.HTTPException(status_code=404, detail="Chat session not found")
    try:
        child = ctx.store.fork_session(
            session_id,
            at_seq=payload.at_seq,
        )
    except ctx.SessionForkError as exc:
        status_code = 404 if exc.code == "SESSION_NOT_FOUND" else 400
        raise ctx.HTTPException(status_code=status_code, detail=f"{exc.code}: {exc}")
    return child


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
    ctx._record_control_event(
        session_id,
        "turn.cancel.requested",
        category="turn",
        phase="requested",
        status="pending",
        title="用户请求停止当前运行",
        message="前端停止按钮已请求中断当前 turn。",
        data={"cancelled_tool_calls": cancelled_tools},
    )
    return {
        "ok": True,
        "session_id": session_id,
        "cancel_requested": True,
        "active": ctx.agent_sessions.is_active(session_id),
        "cancelled_tool_calls": cancelled_tools,
    }


@router.post("/api/chat/sessions/{session_id}/queue/clear")
async def clear_chat_session_queue(session_id: str) -> dict:
    if ctx.store.get_chat_session(session_id) is None:
        raise ctx.HTTPException(status_code=404, detail="Chat session not found")
    cleared = ctx.agent_sessions.clear_queued_messages(session_id)
    ctx._record_control_event(
        session_id,
        "queue.cleared",
        category="status",
        phase="completed",
        title="用户清空排队消息",
        message=f"已清空 {len(cleared)} 条排队消息。",
        data={
            "cleared_message_ids": [message.id for message in cleared],
            "cleared_count": len(cleared),
        },
    )
    return {
        "ok": True,
        "session_id": session_id,
        "cleared_count": len(cleared),
        "cleared_messages": [message.model_dump(mode="json") for message in cleared],
        "queued_messages": [],
    }


@router.get("/api/chat/sessions", response_model=list[ctx.ChatSession])
async def list_chat_sessions(
    workspace: str | None = ctx.Query(default=None),
    include_archived: bool = ctx.Query(default=False),
) -> list[ctx.ChatSession]:
    return ctx.store.list_chat_sessions(workspace=workspace, include_archived=include_archived)


@router.get("/api/chat/sessions/search")
async def search_chat_sessions(
    q: str = ctx.Query(min_length=1, max_length=500),
) -> dict:
    cleaned = q.replace("\x00", "").strip()
    if not cleaned:
        raise ctx.HTTPException(status_code=400, detail="搜索词不能为空")
    return ctx.store.search_chat_sessions(cleaned, limit=50)


@router.post("/api/chat/sessions", response_model=ctx.ChatSession, status_code=201)
async def create_chat_session(payload: ctx.ChatSessionCreate) -> ctx.ChatSession:
    session = ctx.ChatSession(
        id=ctx.new_id("chat"),
        title=payload.title or "新对话",
        workspace=str(ctx.workspace_manager.current_root),
    )
    ctx.store.create_chat_session(session)
    return session


@router.post("/api/chat/sessions/reorder")
async def reorder_chat_session(payload: ctx.ChatSessionReorder) -> dict:
    try:
        sessions = ctx.store.reorder_chat_session(payload.session_id, workspace=payload.workspace, before_session_id=payload.before_session_id)
    except KeyError as exc:
        raise ctx.HTTPException(status_code=404, detail="会话不存在或不属于目标 Workspace") from exc
    return {"items": [session.model_dump(mode="json") for session in sessions]}


@router.patch("/api/chat/sessions/{session_id}", response_model=ctx.ChatSession)
async def rename_chat_session(session_id: str, payload: ctx.ChatSessionRename) -> ctx.ChatSession:
    try:
        session = ctx.store.rename_chat_session(session_id, payload.title)
    except ValueError as exc:
        raise ctx.HTTPException(status_code=400, detail=str(exc))
    if session is None:
        raise ctx.HTTPException(status_code=404, detail="Chat session not found")
    return session


@router.post("/api/chat/sessions/{session_id}/archive", response_model=ctx.ChatSession)
async def archive_chat_session(session_id: str, payload: ctx.ChatSessionArchive) -> ctx.ChatSession:
    session = ctx.store.archive_chat_session(session_id, payload.archived)
    if session is None:
        raise ctx.HTTPException(status_code=404, detail="Chat session not found")
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
async def chat_runtime_state(
    session_id: str,
    passive: bool = ctx.Query(default=False),
) -> dict:
    unavailable_reason = None
    try:
        session = ctx.store.get_chat_session(session_id) if passive else ctx._get_current_chat_session(session_id, auto_switch=True)
    except ctx.HTTPException as exc:
        if exc.status_code != 409:
            raise
        session = ctx.store.get_chat_session(session_id)
        unavailable_reason = str(exc.detail)
    if session is None:
        raise ctx.HTTPException(status_code=404, detail="Chat session not found")
    runtime = ctx.agent_sessions.runtime_state(session_id)
    processes = ctx._process_jobs_for_session(session_id)
    descendant_ids: set[str] = set()
    frontier = [session_id]
    all_sessions = ctx.store.list_chat_sessions(include_archived=True)
    while frontier:
        parent_id = frontier.pop()
        for candidate in all_sessions:
            if candidate.parent_session_id == parent_id and candidate.id not in descendant_ids:
                descendant_ids.add(candidate.id)
                frontier.append(candidate.id)
    descendant_running = any(ctx.agent_sessions.is_active(child_id) for child_id in descendant_ids)
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
        "processes": processes,
        "active": ctx.agent_sessions.is_active(session_id),
        "descendant_running": descendant_running,
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


@router.post("/api/chat/sessions/{session_id}/feedback")
async def record_feedback(session_id: str, payload: dict) -> dict:
    """记录人类反馈（dsh feedback 对齐：durable、可回放、绑定消息）。"""
    message_id = str(payload.get("message_id") or "").strip()
    rating = str(payload.get("rating") or "").strip()
    if not message_id or rating not in {"up", "down"}:
        raise ctx.HTTPException(status_code=422, detail="message_id 与 rating(up|down) 必填")
    comment = str(payload.get("comment") or "").strip()
    ctx.store.upsert_chat_event(
        ctx.ChatEvent(
            session_id=session_id,
            type="feedback",
            event_type="feedback.recorded",
            phase="completed",
            title="用户反馈" + ("👍" if rating == "up" else "👎"),
            message=comment or ("正面反馈" if rating == "up" else "负面反馈"),
            data={"message_id": message_id, "rating": rating, "comment": comment},
        )
    )
    return {"ok": True, "message_id": message_id, "rating": rating}


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
        ctx.agent_sessions.enqueue_message(session_id, queued)
        queued_messages = ctx.agent_sessions.queued_messages(session_id)
        ctx._record_control_event(
            session_id,
            "queue.enqueued",
            category="status",
            phase="queued",
            status="pending",
            title="用户消息已加入队列",
            message="当前 turn 仍在运行，新消息会在本轮工具调用结束后继续处理。",
            data={
                "message_id": queued.id,
                "queued_count": len(queued_messages),
            },
        )
        return ctx.JSONResponse(
            status_code=202,
            content={
                "ok": True,
                "status": "queued",
                "message": queued.model_dump(mode="json"),
                "queued_count": len(queued_messages),
                "queued_messages": [
                    message.model_dump(mode="json") for message in queued_messages
                ],
            },
        )
    ctx.agent_sessions.mark_active(session_id)

    async def emit() -> ctx.AsyncIterator[str]:
        runner = ctx.SessionRunner(
            ctx.SessionRunDependencies(
                store=ctx.store,
                agent_sessions=ctx.agent_sessions,
                runtime_factory=lambda sid=session_id: ctx._agent_runtime_for_session(sid),
                id_factory=ctx.new_id,
                persist_event=ctx._persist_runtime_event,
                runtime_event_from_agent_event=ctx._runtime_event_from_agent_event,
                build_context_budget_plan=ctx.build_context_budget_plan,
                context_window_tokens=ctx.settings.context_window_tokens,
                project_root_provider=lambda: ctx.workspace_manager.current_root,
                global_agent_file_provider=lambda: ctx.settings.global_agent_file,
                tool_orchestrator_factory=lambda sid=session_id: ctx._tool_orchestrator(sid),
                event_builder_for_existing_turn=ctx._event_builder_for_existing_turn,
                denied_tool_message_builder=ctx._denied_tool_alternative_message,
                compaction_engine_factory=ctx._compaction_engine,
            )
        )
        try:
            titled = False
            async for event in runner.run_message(session_id, payload.content):
                if event.get("type") == "session_title":
                    titled = True
                yield ctx._ndjson(event)
        finally:
            ctx.agent_sessions.mark_idle(session_id)
        # dsh session-title-llm 升级：首轮完成且标题仍是兜底时，用辅助 LLM
        # 生成更好的标题（跟随消息语言，4s 上限，失败静默保留兜底）。
        # 放在流末尾（mark_idle 之后）：前端 finally 的 reloadSessions 能拿到终值。
        try:
            session = ctx.store.get_chat_session(session_id)
        except Exception:
            session = None
        if session is not None and session.title_source in {"default", "fallback"}:
            user_texts = [
                message.content
                for message in ctx.store.list_chat_messages(session_id)
                if message.role == ctx.ChatRole.USER
            ]
            if user_texts:
                from ..sessions.titling import generate_llm_title

                title = await generate_llm_title(ctx.provider, user_texts)
                if title:
                    updated = ctx.store.auto_title_chat_session(session_id, title, "llm")
                    if updated is not None:
                        yield ctx._ndjson(
                            {
                                "type": "session_title",
                                "title": updated.title,
                                "source": "llm",
                            }
                        )

    return ctx.StreamingResponse(emit(), media_type="application/x-ndjson")


@router.get("/api/chat/sessions/{session_id}/trace")
async def get_chat_session_trace(session_id: str) -> dict:
    if ctx.store.get_chat_session(session_id) is None:
        raise ctx.HTTPException(status_code=404, detail="Chat session not found")
    return {"items": ctx.store.trace.read(session_id)}


@router.get("/api/chat/sessions/{session_id}/trace/replay")
async def get_chat_session_trace_replay(session_id: str) -> dict:
    if ctx.store.get_chat_session(session_id) is None:
        raise ctx.HTTPException(status_code=404, detail="Chat session not found")
    return ctx.store.trace.replay(
        session_id,
        messages=[
            message.model_dump(mode="json")
            for message in ctx.store.list_chat_messages(session_id)
        ],
        events=[
            event.model_dump(mode="json")
            for event in ctx.store.list_chat_events(session_id)
        ],
        processes=ctx._process_jobs_for_session(session_id),
        pending_approvals=[
            item.as_dict()
            for item in ctx.agent_sessions.list_pending_approvals(session_id=session_id)
        ],
    )
