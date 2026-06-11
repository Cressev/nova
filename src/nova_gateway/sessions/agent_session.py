from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from ..approvals.store import PendingApproval, PendingApprovalStore
from ..models import ChatMessage, utc_now


@dataclass
class AgentToolCallState:
    call_id: str
    turn_id: str
    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)
    status: str = "running"
    output: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    sequence: int = 0
    updated_at: str = field(default_factory=lambda: utc_now().isoformat())

    def as_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "turn_id": self.turn_id,
            "tool": self.tool,
            "arguments": self.arguments,
            "status": self.status,
            "output": self.output,
            "data": self.data,
            "sequence": self.sequence,
            "updated_at": self.updated_at,
        }


@dataclass
class AgentTurnState:
    turn_id: str
    user_message_id: str | None = None
    task_id: str | None = None
    status: str = "running"
    started_at: str = field(default_factory=lambda: utc_now().isoformat())
    updated_at: str = field(default_factory=lambda: utc_now().isoformat())

    def as_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "user_message_id": self.user_message_id,
            "task_id": self.task_id,
            "status": self.status,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
        }


@dataclass
class AgentFinalAnswerState:
    message_id: str
    content: str
    created_at: str = field(default_factory=lambda: utc_now().isoformat())

    def as_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "content": self.content,
            "created_at": self.created_at,
        }


@dataclass
class AgentSessionRuntime:
    session_id: str
    active: bool = False
    current_turn: AgentTurnState | None = None
    tool_calls: dict[str, AgentToolCallState] = field(default_factory=dict)
    background_job_ids: list[str] = field(default_factory=list)
    cancel_requested: bool = False
    final_answer: AgentFinalAnswerState | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "active": self.active,
            "current_turn": self.current_turn.as_dict() if self.current_turn else None,
            "tool_calls": [
                item.as_dict()
                for item in sorted(self.tool_calls.values(), key=lambda tool_call: tool_call.sequence)
            ],
            "background_job_ids": list(self.background_job_ids),
            "cancel_requested": self.cancel_requested,
            "final_answer": self.final_answer.as_dict() if self.final_answer else None,
        }


class AgentSessionService:
    """管理每个会话的运行中状态和排队输入。

    这里承接原来散落在 `main.py` 的 active/queue/pending approval 状态；
    后续后台任务和取消信号也继续收进来。
    """

    def __init__(self) -> None:
        self.active_session_ids: set[str] = set()
        self.queued_session_messages: dict[str, list[ChatMessage]] = defaultdict(list)
        self.pending_approvals = PendingApprovalStore()
        self._sessions: dict[str, AgentSessionRuntime] = {}
        self._tool_sequence = 0
        self._lock = Lock()

    def _runtime_for(self, session_id: str) -> AgentSessionRuntime:
        runtime = self._sessions.get(session_id)
        if runtime is None:
            runtime = AgentSessionRuntime(session_id=session_id)
            self._sessions[session_id] = runtime
        return runtime

    def is_active(self, session_id: str) -> bool:
        with self._lock:
            runtime = self._sessions.get(session_id)
            return session_id in self.active_session_ids or bool(runtime and runtime.active)

    def mark_active(self, session_id: str) -> None:
        with self._lock:
            self.active_session_ids.add(session_id)
            self._runtime_for(session_id).active = True

    def mark_idle(self, session_id: str) -> None:
        with self._lock:
            self.active_session_ids.discard(session_id)
            self._runtime_for(session_id).active = False

    def enqueue_message(self, session_id: str, message: ChatMessage) -> None:
        with self._lock:
            self.queued_session_messages.setdefault(session_id, []).append(message)

    def drain_queued_messages(self, session_id: str) -> list[ChatMessage]:
        with self._lock:
            return self.queued_session_messages.pop(session_id, [])

    def start_turn(
        self,
        session_id: str,
        *,
        turn_id: str,
        user_message_id: str | None = None,
        task_id: str | None = None,
    ) -> AgentTurnState:
        with self._lock:
            runtime = self._runtime_for(session_id)
            runtime.active = True
            runtime.cancel_requested = False
            runtime.current_turn = AgentTurnState(
                turn_id=turn_id,
                user_message_id=user_message_id,
                task_id=task_id,
            )
            runtime.tool_calls = {}
            runtime.background_job_ids = []
            runtime.final_answer = None
            self.active_session_ids.add(session_id)
            return runtime.current_turn

    def complete_turn(self, session_id: str, *, message_id: str, content: str) -> None:
        with self._lock:
            runtime = self._runtime_for(session_id)
            runtime.active = False
            if runtime.current_turn is not None:
                runtime.current_turn.status = "completed"
                runtime.current_turn.updated_at = utc_now().isoformat()
            runtime.final_answer = AgentFinalAnswerState(message_id=message_id, content=content)
            self.active_session_ids.discard(session_id)

    def fail_turn(self, session_id: str, *, reason: str) -> None:
        with self._lock:
            runtime = self._runtime_for(session_id)
            runtime.active = False
            if runtime.current_turn is not None:
                runtime.current_turn.status = "failed"
                runtime.current_turn.updated_at = utc_now().isoformat()
            runtime.final_answer = AgentFinalAnswerState(message_id="", content=reason)
            self.active_session_ids.discard(session_id)

    def cancel_turn(self, session_id: str, *, reason: str = "用户已停止当前运行") -> None:
        with self._lock:
            runtime = self._runtime_for(session_id)
            runtime.active = False
            runtime.cancel_requested = True
            if runtime.current_turn is not None:
                runtime.current_turn.status = "cancelled"
                runtime.current_turn.updated_at = utc_now().isoformat()
            runtime.final_answer = AgentFinalAnswerState(message_id="", content=reason)
            self.active_session_ids.discard(session_id)

    def request_cancel(self, session_id: str) -> None:
        with self._lock:
            self._runtime_for(session_id).cancel_requested = True

    def is_cancel_requested(self, session_id: str) -> bool:
        with self._lock:
            runtime = self._sessions.get(session_id)
            return bool(runtime and runtime.cancel_requested)

    def request_cancel_for_call(self, call_id: str) -> str | None:
        with self._lock:
            for session_id, runtime in self._sessions.items():
                if call_id in runtime.tool_calls:
                    runtime.cancel_requested = True
                    runtime.tool_calls[call_id].status = "cancelled"
                    runtime.tool_calls[call_id].updated_at = utc_now().isoformat()
                    return session_id
            return None

    def record_background_job(
        self,
        session_id: str,
        *,
        turn_id: str,
        job_id: str,
        call_id: str | None = None,
    ) -> None:
        with self._lock:
            runtime = self._runtime_for(session_id)
            if job_id not in runtime.background_job_ids:
                runtime.background_job_ids.append(job_id)
            if call_id:
                existing_tool = runtime.tool_calls.get(call_id)
                self._record_tool_call_locked(
                    runtime,
                    turn_id=turn_id,
                    call_id=call_id,
                    tool=existing_tool.tool if existing_tool else "shell_command",
                    status="background",
                    data={"job_id": job_id},
                )

    def record_tool_call(
        self,
        session_id: str,
        *,
        turn_id: str,
        call_id: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
        status: str = "running",
        output: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            runtime = self._runtime_for(session_id)
            self._record_tool_call_locked(
                runtime,
                turn_id=turn_id,
                call_id=call_id,
                tool=tool,
                arguments=arguments,
                status=status,
                output=output,
                data=data,
            )

    def record_runtime_event(self, event: dict[str, Any]) -> None:
        session_id = str(event.get("session_id") or "")
        if not session_id:
            return
        event_type = str(event.get("event_type") or "")
        category = str(event.get("category") or "")
        phase = str(event.get("phase") or "")
        turn_id = str(event.get("turn_id") or "")
        with self._lock:
            runtime = self._runtime_for(session_id)
            if category == "turn" and runtime.current_turn is not None:
                if phase in {"completed", "failed"}:
                    runtime.current_turn.status = phase
                    runtime.current_turn.updated_at = utc_now().isoformat()
            if category == "tool":
                call_id = str(event.get("call_id") or event.get("id") or "")
                if call_id:
                    status = str(event.get("status") or "running")
                    if phase == "completed":
                        status = "completed"
                    elif phase == "failed":
                        status = "failed"
                    self._record_tool_call_locked(
                        runtime,
                        turn_id=turn_id,
                        call_id=call_id,
                        tool=str(event.get("tool") or "tool"),
                        arguments=event.get("arguments") if isinstance(event.get("arguments"), dict) else None,
                        status=status,
                        output=event.get("output") if isinstance(event.get("output"), str) else None,
                        data=event.get("data") if isinstance(event.get("data"), dict) else None,
                    )
                    data = event.get("data") if isinstance(event.get("data"), dict) else {}
                    nested_job = data.get("job") if isinstance(data.get("job"), dict) else {}
                    job_id = data.get("job_id") or nested_job.get("id")
                    if isinstance(job_id, str) and job_id and job_id not in runtime.background_job_ids:
                        runtime.background_job_ids.append(job_id)
            if event_type == "tool.cancel.requested":
                runtime.cancel_requested = True

    def runtime_state(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            runtime = self._runtime_for(session_id).as_dict()
            runtime["queued_messages"] = [
                message.model_dump(mode="json")
                for message in self.queued_session_messages.get(session_id, [])
            ]
            return runtime

    def _record_tool_call_locked(
        self,
        runtime: AgentSessionRuntime,
        *,
        turn_id: str,
        call_id: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
        status: str = "running",
        output: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        current = runtime.tool_calls.get(call_id)
        if current is None:
            self._tool_sequence += 1
            current = AgentToolCallState(
                call_id=call_id,
                turn_id=turn_id,
                tool=tool,
                sequence=self._tool_sequence,
            )
            runtime.tool_calls[call_id] = current
        current.turn_id = turn_id or current.turn_id
        current.tool = tool or current.tool
        current.status = status
        current.updated_at = utc_now().isoformat()
        if arguments is not None:
            current.arguments = dict(arguments)
        if output is not None:
            current.output = output
        if data is not None:
            current.data = {**current.data, **data}

    def create_pending_approval(
        self,
        *,
        session_id: str,
        turn_id: str,
        call_id: str,
        tool: str,
        arguments: dict,
        permission: str,
        reason: str,
    ) -> PendingApproval:
        return self.pending_approvals.create(
            session_id=session_id,
            turn_id=turn_id,
            call_id=call_id,
            tool=tool,
            arguments=arguments,
            permission=permission,
            reason=reason,
        )

    def list_pending_approvals(self, *, session_id: str | None = None) -> list[PendingApproval]:
        return self.pending_approvals.list_pending(session_id=session_id)

    def approve_pending_approval(self, approval_id: str) -> PendingApproval | None:
        return self.pending_approvals.approve(approval_id)

    def deny_pending_approval(self, approval_id: str, *, reason: str = "") -> PendingApproval | None:
        return self.pending_approvals.deny(approval_id, reason=reason)
