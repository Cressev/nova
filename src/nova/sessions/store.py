from __future__ import annotations

import json
import re
from pathlib import Path
from threading import Lock
from typing import Any

from ..models import ChatEvent, ChatMessage, ChatRole, ChatSession, new_id, utc_now
from ..observability.trace import TraceRecorder


class SessionForkError(RuntimeError):
    """会话 fork 失败（dsh SessionForkError 对齐）。

    code 取 dsh 的拒绝码语义：SESSION_NOT_FOUND / INVALID_BOUNDARY /
    OPEN_TURN。
    """
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


def _increased_fork_title(title: str) -> str:
    """fork 子会话标题递增（dsh increasedForkTitle 对齐）。

    半角 (N) 和全角（N）都支持；无编号则追加 (1)。
    """
    ascii_match = re.match(r"^(.*?)\((\d+)\)$", title)
    if ascii_match and ascii_match[2] is not None:
        return f"{ascii_match[1]}({int(ascii_match[2]) + 1})"
    full_match = re.match(r"^(.*?)（(\d+)）$", title)
    if full_match and full_match[2] is not None:
        return f"{full_match[1]}（{int(full_match[2]) + 1}）"
    return f"{title} (1)"


class SessionStore:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.chat_file = self.state_dir / "chats.json"
        self.trace = TraceRecorder(state_dir)
        self._lock = Lock()
        self._chat_sessions: dict[str, ChatSession] = {}
        self._chat_messages: dict[str, list[ChatMessage]] = {}
        self._chat_events: dict[str, list[ChatEvent]] = {}
        self._load()
        self._backfill_placeholder_titles()

    def _backfill_placeholder_titles(self) -> None:
        """dsh session-title 历史回填（一次性、幂等）。

        自动命名上线前的存量会话可能带着占位标题但已有真实对话——正是
        "侧栏全是新对话、找不到历史会话"的来源。此处对占位标题 + 有用户
        消息的会话，用首条用户消息生成兜底标题（source=fallback，之后仍可
        被 LLM 升级、被用户改名钉住）。零消息的空壳保持占位，由列表过滤。
        """
        from .titling import fallback_title, is_placeholder_title

        with self._lock:
            changed = False
            for session in self._chat_sessions.values():
                if session.title_source != "default" or not is_placeholder_title(session.title):
                    continue
                first_user = next(
                    (m for m in self._chat_messages.get(session.id, []) if m.role == ChatRole.USER),
                    None,
                )
                if first_user is None:
                    continue
                title = fallback_title(first_user.content)
                if title and title != session.title:
                    self._chat_sessions[session.id] = session.model_copy(
                        update={"title": title, "title_source": "fallback"}
                    )
                    changed = True
            if changed:
                self._save_chats()

    def _load(self) -> None:
        if self.chat_file.exists():
            chat_payload = json.loads(self.chat_file.read_text(encoding="utf-8"))
            self._chat_sessions = {
                item["id"]: ChatSession.model_validate(item)
                for item in chat_payload.get("sessions", [])
            }
            self._chat_messages = {
                session_id: [ChatMessage.model_validate(item) for item in messages]
                for session_id, messages in chat_payload.get("messages", {}).items()
            }
            self._chat_events = {
                session_id: [ChatEvent.model_validate(item) for item in events]
                for session_id, events in chat_payload.get("events", {}).items()
            }

    def _save_chats(self) -> None:
        # 对话是唯一主存储：session、message、UI 事件统一保存在 chats.json。
        payload = {
            "sessions": [
                session.model_dump(mode="json")
                for session in sorted(
                    self._chat_sessions.values(),
                    key=lambda item: item.updated_at,
                    reverse=True,
                )
            ],
            "messages": {
                session_id: [message.model_dump(mode="json") for message in messages]
                for session_id, messages in self._chat_messages.items()
            },
            "events": {
                session_id: [event.model_dump(mode="json") for event in events]
                for session_id, events in self._chat_events.items()
            },
        }
        self.chat_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def create_chat_session(self, session: ChatSession) -> ChatSession:
        with self._lock:
            self._chat_sessions[session.id] = session
            self._chat_messages.setdefault(session.id, [])
            self._chat_events.setdefault(session.id, [])
            self._save_chats()
            return session

    def list_chat_sessions(self, *, workspace: str | None = None, include_archived: bool = False) -> list[ChatSession]:
        with self._lock:
            sessions = [session for session in self._chat_sessions.values() if include_archived or not session.archived]
            if workspace is not None:
                sessions = [
                    session
                    for session in sessions
                    if session.workspace == workspace
                ]
            has_manual_order = any(item.manual_order is not None for item in sessions)
            if has_manual_order:
                return sorted(
                    sessions,
                    key=lambda item: (item.manual_order is None, item.manual_order if item.manual_order is not None else 0, -item.updated_at.timestamp()),
                )
            return sorted(sessions, key=lambda item: item.updated_at, reverse=True)

    def search_chat_sessions(self, query: str, *, limit: int = 50) -> dict[str, Any]:
        """搜索标题与正文，仅读取内存索引，不触发落盘。"""
        needle = query.replace("\x00", "").strip().casefold()
        if not needle:
            return {"items": [], "hasMore": False}
        with self._lock:
            matches: list[dict[str, str]] = []
            for session in self._chat_sessions.values():
                if session.archived:
                    continue
                messages = self._chat_messages.get(session.id, [])
                haystack = "\n".join(message.content for message in messages)
                title_hit = needle in session.title.casefold()
                body_hit = needle in haystack.casefold()
                if not title_hit and not body_hit:
                    continue
                snippet = session.title
                if body_hit:
                    folded = haystack.casefold()
                    start = max(0, folded.find(needle) - 60)
                    end = min(len(haystack), start + 180)
                    snippet = haystack[start:end].replace("\n", " ")
                matches.append({"session_id": session.id, "title": session.title, "snippet": snippet})
            matches.sort(key=lambda item: item["session_id"])
            return {"items": matches[:limit], "hasMore": len(matches) > limit}

    def reorder_chat_session(self, session_id: str, *, workspace: str | None, before_session_id: str | None) -> list[ChatSession]:
        with self._lock:
            moving = self._chat_sessions.get(session_id)
            if moving is None or moving.archived or moving.workspace != workspace:
                raise KeyError(session_id)
            sessions = [item for item in self._chat_sessions.values() if not item.archived and item.workspace == workspace]
            sessions.sort(key=lambda item: item.updated_at, reverse=True)
            sessions = [item for item in sessions if item.id != session_id]
            index = next((i for i, item in enumerate(sessions) if item.id == before_session_id), len(sessions))
            sessions.insert(index, moving)
            for position, item in enumerate(sessions):
                self._chat_sessions[item.id] = item.model_copy(update={"manual_order": position})
            self._save_chats()
            return [self._chat_sessions[item.id] for item in sessions]

    def get_chat_session(self, session_id: str) -> ChatSession | None:
        with self._lock:
            return self._chat_sessions.get(session_id)

    def delete_chat_session(self, session_id: str) -> bool:
        with self._lock:
            if session_id not in self._chat_sessions:
                return False
            del self._chat_sessions[session_id]
            self._chat_messages.pop(session_id, None)
            self._chat_events.pop(session_id, None)
            self._save_chats()
            return True

    def archive_chat_session(self, session_id: str, archived: bool = True) -> ChatSession | None:
        with self._lock:
            session = self._chat_sessions.get(session_id)
            if session is None:
                return None
            updated = session.model_copy(update={"archived": archived, "updated_at": utc_now()})
            self._chat_sessions[session_id] = updated
            self._save_chats()
            return updated

    def rename_chat_session(self, session_id: str, title: str, *, source: str = "user") -> ChatSession | None:
        """更新会话标题；菜单操作只允许改标题，不改变会话历史。

        source 语义（dsh session-title）：user=用户手动改名，钉住标题，
        自动命名不再覆盖；其余来源经 auto_title_chat_session 走升级链。
        """
        cleaned = title.strip()[:120]
        if not cleaned:
            raise ValueError("会话名称不能为空")
        with self._lock:
            session = self._chat_sessions.get(session_id)
            if session is None:
                return None
            updated = session.model_copy(update={"title": cleaned, "title_source": source, "updated_at": utc_now()})
            self._chat_sessions[session_id] = updated
            self._save_chats()
            return updated

    def auto_title_chat_session(self, session_id: str, title: str, source: str) -> ChatSession | None:
        """自动命名升级链（dsh session-title 的 default→fallback→llm）。

        fallback 只接管仍是占位（default）的会话；llm 只把兜底升级成更好的
        标题；user 来源（手动改名）在此处永不覆盖。会话不存在返回 None。
        """
        cleaned = title.strip()[:120]
        if not cleaned:
            return None
        with self._lock:
            session = self._chat_sessions.get(session_id)
            if session is None:
                return None
            current = session.title_source
            if source == "fallback":
                if current != "default":
                    return None
            elif source == "llm":
                if current not in {"default", "fallback"}:
                    return None
            else:
                return None
            updated = session.model_copy(update={"title": cleaned, "title_source": source, "updated_at": utc_now()})
            self._chat_sessions[session_id] = updated
            self._save_chats()
            return updated

    def add_chat_message(self, message: ChatMessage) -> ChatMessage:
        with self._lock:
            if message.session_id not in self._chat_sessions:
                raise KeyError(message.session_id)
            self._chat_messages.setdefault(message.session_id, []).append(message)
            # 消息写入后同步刷新会话更新时间，前端列表按最近对话排序。
            session = self._chat_sessions[message.session_id]
            self._chat_sessions[message.session_id] = session.model_copy(
                update={"updated_at": utc_now()}
            )
            self._save_chats()
            return message

    def list_chat_messages(self, session_id: str) -> list[ChatMessage]:
        with self._lock:
            return list(self._chat_messages.get(session_id, []))

    def get_chat_message(self, message_id: str) -> ChatMessage | None:
        """按消息 id 跨会话查找（划词评论引用定位用）。"""
        with self._lock:
            for messages in self._chat_messages.values():
                for message in messages:
                    if message.id == message_id:
                        return message
        return None

    def upsert_chat_event(self, event: ChatEvent) -> ChatEvent:
        with self._lock:
            if event.session_id not in self._chat_sessions:
                raise KeyError(event.session_id)
            events = self._chat_events.setdefault(event.session_id, [])
            for index, existing in enumerate(events):
                if existing.id == event.id:
                    # Codex 的同一个 tool item 会先 started 后 completed；完成事件要保留开始事件里的参数。
                    merged = existing.model_copy(
                        update={
                            "type": event.type,
                            "event_type": event.event_type or existing.event_type,
                            "phase": event.phase or existing.phase,
                            "turn_id": event.turn_id or existing.turn_id,
                            "sequence": event.sequence if event.sequence is not None else existing.sequence,
                            "status": event.status,
                            "title": event.title or existing.title,
                            "message": event.message if event.message is not None else existing.message,
                            "tool": event.tool or existing.tool,
                            "arguments": event.arguments or existing.arguments,
                            "output": event.output if event.output is not None else existing.output,
                            "data": event.data or existing.data,
                            "parallel": event.parallel or existing.parallel,
                            "updated_at": utc_now(),
                        }
                    )
                    events[index] = merged
                    self._save_chats()
                    self.trace.append(merged)
                    return merged
            events.append(event)
            session = self._chat_sessions[event.session_id]
            self._chat_sessions[event.session_id] = session.model_copy(
                update={"updated_at": utc_now()}
            )
            self._save_chats()
            self.trace.append(event)
            return event

    def list_chat_events(self, session_id: str) -> list[ChatEvent]:
        with self._lock:
            return list(self._chat_events.get(session_id, []))

    # ---- fork（dsh SessionStore.fork 完整对齐） ----

    def fork_session(
        self,
        source_id: str,
        *,
        at_seq: int | None = None,
        child_id: str | None = None,
    ) -> ChatSession:
        """从 source 会话切片创建子会话（dsh fork 语义）。

        1. 解析源会话（不存在 → SESSION_NOT_FOUND）
        2. 确定 boundary：at_seq 给定则用之，否则取最后一个事件的 seq
        3. 校验 boundary 是合法的事件 seq（INVALID_BOUNDARY）
        4. 校验 boundary 不落在未关闭的 turn 内（OPEN_TURN）——
           即 boundary 之前最后一个 turn 事件不能是 turn.started
           （没有对应的 turn.completed/turn.failed）
        5. 复制 events[0..boundary] + messages（到 boundary 对应的消息为止）
        6. 创建子会话：parent_session_id=source_id, seed_length=boundary+1
        7. 标题递增（dsh increasedForkTitle）
        """
        with self._lock:
            source = self._chat_sessions.get(source_id)
            if source is None:
                raise SessionForkError(
                    f'会话 "{source_id}" 不存在', "SESSION_NOT_FOUND"
                )

            source_events = list(self._chat_events.get(source_id, []))
            source_messages = list(self._chat_messages.get(source_id, []))

            # 确定 boundary
            if not source_events:
                boundary = -1  # 空会话也能 fork（子会话也是空的）
            elif at_seq is not None:
                if at_seq < 0 or at_seq >= len(source_events):
                    last_seq = source_events[-1].sequence
                    raise SessionForkError(
                        f'fork 边界 {at_seq} 不在会话 "{source_id}" 的事件范围内'
                        f"（最后 seq: {last_seq}）",
                        "INVALID_BOUNDARY",
                    )
                boundary = at_seq
            else:
                boundary = len(source_events) - 1

            # OPEN_TURN 校验：boundary 之前最后一个 turn 事件若是 turn.started（无匹配结束），拒绝
            if boundary >= 0:
                seed_slice = source_events[: boundary + 1]
                last_turn_event = None
                for evt in reversed(seed_slice):
                    if evt.event_type in (
                        "turn.started",
                        "turn.completed",
                        "turn.failed",
                        "turn.cancelled",
                    ):
                        last_turn_event = evt.event_type
                        break
                if last_turn_event == "turn.started":
                    raise SessionForkError(
                        f'fork 边界 {boundary} 落在会话 "{source_id}" 的未关闭 turn 内',
                        "OPEN_TURN",
                    )

            # 切片事件
            child_events = [evt.model_copy(deep=True) for evt in source_events[: boundary + 1]] if boundary >= 0 else []

            # 切片消息：到 boundary 对应 turn 的 user message 为止
            # （dsh：fork 继承的是对话历史到某 turn 结束，之后是新分支）
            child_messages: list[ChatMessage] = []
            if boundary >= 0:
                # 找到 boundary 对应的最后一个 turn.completed/failed 的 message_id
                cutoff_message_id: str | None = None
                for evt in source_events[: boundary + 1]:
                    if evt.event_type in ("turn.completed", "turn.failed", "turn.cancelled"):
                        msg_data = evt.data or {}
                        if isinstance(msg_data.get("message_id"), str):
                            cutoff_message_id = msg_data["message_id"]
                # 如果 boundary 落在 turn.started（但 OPEN_TURN 已拒绝），这里 cutoff 为 None → 取所有
                if cutoff_message_id is not None:
                    for msg in source_messages:
                        child_messages.append(msg.model_copy(deep=True))
                        if msg.id == cutoff_message_id:
                            break
                else:
                    # 没有完成的 turn，取到 boundary 的 user message
                    for msg in source_messages:
                        child_messages.append(msg.model_copy(deep=True))
                    # 只保留 user/assistant 到对应位置
                    if child_events:
                        # 找到最后一个 user message
                        user_msgs = [m for m in child_messages if m.role.value == "user"]
                        if user_msgs:
                            last_user = user_msgs[-1]
                            child_messages = [
                                m for m in child_messages
                                if m.created_at <= last_user.created_at
                            ]

            # 重置子会话事件的 sequence 起点（子会话从自己的 seq=1 开始计数）
            for i, evt in enumerate(child_events):
                evt.sequence = i + 1

            # 创建子会话
            child_session_id = child_id or new_id("chat")
            # 标题递增 + 避名碰撞：若已有同名会话，继续递增直到唯一
            child_title = _increased_fork_title(source.title)
            existing_titles = {s.title for s in self._chat_sessions.values()}
            while child_title in existing_titles:
                child_title = _increased_fork_title(child_title)
            child_session = ChatSession(
                id=child_session_id,
                title=child_title,
                workspace=source.workspace,
                parent_session_id=source_id,
                seed_length=boundary + 1 if boundary >= 0 else 0,
            )

            self._chat_sessions[child_session_id] = child_session
            self._chat_messages[child_session_id] = child_messages
            self._chat_events[child_session_id] = child_events
            self._save_chats()

            return child_session
