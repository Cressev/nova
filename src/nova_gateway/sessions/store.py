from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

from ..models import ChatEvent, ChatMessage, ChatSession, Task, TaskStatus, TimelineEvent, utc_now
from ..observability.trace import TraceRecorder


class TaskStore:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.session_file = self.state_dir / "sessions.json"
        self.chat_file = self.state_dir / "chats.json"
        self.trace = TraceRecorder(state_dir)
        self._lock = Lock()
        self._tasks: dict[str, Task] = {}
        self._events: dict[str, list[TimelineEvent]] = {}
        self._chat_sessions: dict[str, ChatSession] = {}
        self._chat_messages: dict[str, list[ChatMessage]] = {}
        self._chat_events: dict[str, list[ChatEvent]] = {}
        self._load()

    def _load(self) -> None:
        # 任务和聊天分开存储：旧版本只有 sessions.json，新版本新增 chats.json。
        if self.session_file.exists():
            payload = json.loads(self.session_file.read_text(encoding="utf-8"))
            self._tasks = {
                item["id"]: Task.model_validate(item)
                for item in payload.get("tasks", [])
            }
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

    def _save(self) -> None:
        payload = {
            "tasks": [
                task.model_dump(mode="json")
                for task in sorted(
                    self._tasks.values(),
                    key=lambda item: item.created_at,
                    reverse=True,
                )
            ]
        }
        self.session_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _save_chats(self) -> None:
        # 聊天数据单独落盘，便于后续把任务 trace 和对话历史分别清理或迁移。
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

    def create_task(self, task: Task) -> Task:
        with self._lock:
            self._tasks[task.id] = task
            self._events[task.id] = []
            self._save()
            return task

    def list_tasks(self) -> list[Task]:
        with self._lock:
            return sorted(
                self._tasks.values(),
                key=lambda item: item.created_at,
                reverse=True,
            )

    def get_task(self, task_id: str) -> Task | None:
        with self._lock:
            return self._tasks.get(task_id)

    def update_task(
        self,
        task_id: str,
        *,
        status: TaskStatus | None = None,
        summary: str | None = None,
    ) -> Task | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            updated = task.model_copy(
                update={
                    "status": status or task.status,
                    "summary": summary if summary is not None else task.summary,
                    "updated_at": utc_now(),
                }
            )
            self._tasks[task_id] = updated
            self._save()
            return updated

    def add_event(self, event: TimelineEvent) -> TimelineEvent:
        with self._lock:
            self._events.setdefault(event.task_id, []).append(event)
            self.trace.append(event)
            return event

    def list_events(self, task_id: str) -> list[TimelineEvent]:
        with self._lock:
            events = self._events.get(task_id)
            if events is not None:
                return list(events)
        return [
            TimelineEvent.model_validate(item)
            for item in self.trace.read(task_id)
        ]

    def create_chat_session(self, session: ChatSession) -> ChatSession:
        with self._lock:
            self._chat_sessions[session.id] = session
            self._chat_messages.setdefault(session.id, [])
            self._chat_events.setdefault(session.id, [])
            self._save_chats()
            return session

    def list_chat_sessions(self, *, workspace: str | None = None) -> list[ChatSession]:
        with self._lock:
            sessions = self._chat_sessions.values()
            if workspace is not None:
                sessions = [
                    session
                    for session in sessions
                    if session.workspace == workspace
                ]
            return sorted(
                sessions,
                key=lambda item: item.updated_at,
                reverse=True,
            )

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
                    return merged
            events.append(event)
            session = self._chat_sessions[event.session_id]
            self._chat_sessions[event.session_id] = session.model_copy(
                update={"updated_at": utc_now()}
            )
            self._save_chats()
            return event

    def list_chat_events(self, session_id: str) -> list[ChatEvent]:
        with self._lock:
            return list(self._chat_events.get(session_id, []))
