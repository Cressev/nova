from __future__ import annotations

import concurrent.futures
import json
import subprocess
import time
from typing import Any, Callable, Iterator

from ..processes.manager import ProcessManager
from .errors import (
    INVALID_ARGS,
    INVALID_TOOL_OUTPUT,
    TOOL_ABORTED_BEFORE_DISPATCH,
    TOOL_PERMISSION_DENIED,
    TOOL_TIMEOUT,
    UNKNOWN_TOOL,
)
from .hooks import HookOutcome, ToolHookRunner
from .validation import snapshot_arguments, validate_arguments
from .workspace import TOOL_SPECS, ToolExecutionError, WorkspaceTools, tool_result_as_json

# 同步 body 的超时线程池：daemon 线程，超时后结果照常返回 TIMEOUT，
# 慢线程在后台自然结束（Python 无法强杀线程；dsh 同样是协作式契约——
# "cannot hard-kill same-process code"）。
_BODY_POOL = concurrent.futures.ThreadPoolExecutor(
    max_workers=8,
    thread_name_prefix="nova-tool-body",
)


class ToolExecutor:
    """统一工具执行入口。

    Runtime 只关心事件流；权限、hook、工具失败兜底都集中在这里，后续接
    approve/deny、取消、分片 stdout 时不用再改模型循环。
    """

    def __init__(
        self,
        tools: WorkspaceTools,
        *,
        hooks: ToolHookRunner | None = None,
        process_manager: ProcessManager | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> None:
        self.tools = tools
        self.hooks = hooks or ToolHookRunner(cwd=tools.project_root)
        self.process_manager = process_manager or ProcessManager()
        # dsh 语义：每个工具派发前重查取消——body 未启动的取消是
        # ABORTED_BEFORE_DISPATCH，body 已启动的是 ABORTED。取消权威在
        # 会话层（agent_sessions.cancel_requested），这里只读。
        self.cancel_requested = cancel_requested or (lambda: False)

    def run_one(
        self,
        call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        parallel: bool = False,
        require_permission: bool = False,
        approved: bool = False,
    ) -> tuple[list[dict], str]:
        events: list[dict] = []
        current_arguments = dict(arguments)
        hook_contexts: list[str] = []
        # approved 来自用户在前端点击“允许”后的续跑。它只跳过工具权限二次询问，
        # 仍然保留 Pre/Post hook、黑名单和工具失败兜底。
        permission_preapproved = approved

        pre_outcomes = self._run_hooks(
            events,
            "PreToolUse",
            call_id,
            tool_name,
            current_arguments,
        )
        self._collect_hook_contexts(hook_contexts, pre_outcomes)
        for outcome in pre_outcomes:
            if outcome.updated_input:
                current_arguments.update(outcome.updated_input)
            if outcome.permission_decision == "deny":
                reason = outcome.reason or "PreToolUse hook 拒绝执行"
                self._run_hooks(
                    events,
                    "PermissionDenied",
                    call_id,
                    tool_name,
                    current_arguments,
                    reason=reason,
                )
                return self._failed_tool(
                    events,
                    call_id,
                    tool_name,
                    current_arguments,
                    reason,
                    {"hook": outcome.name, "error_code": TOOL_PERMISSION_DENIED},
                    hook_contexts=hook_contexts,
                )
            if outcome.permission_decision == "ask":
                event = self._permission_request_event(
                    call_id,
                    tool_name,
                    current_arguments,
                    message=outcome.reason or f"执行 {tool_name} 前需要用户确认。",
                    data={"reason": "PreToolUse hook 要求审批", "hook": outcome.name},
                    hook_contexts=hook_contexts,
                )
                events.append(event)
                return events, self._permission_result_json(event)
            if outcome.permission_decision == "allow":
                permission_preapproved = True

        if self._needs_permission_request(tool_name, current_arguments, require_permission) and not permission_preapproved:
            permission_action = self._run_permission_request_flow(
                events,
                call_id,
                tool_name,
                current_arguments,
                hook_contexts,
            )
            if permission_action == "ask":
                event = self._permission_request_event(
                    call_id,
                    tool_name,
                    current_arguments,
                    message=self._latest_hook_reason(events, "PermissionRequest"),
                    data=self._permission_request_data(tool_name, current_arguments),
                    hook_contexts=hook_contexts,
                )
                events.append(event)
                return events, self._permission_result_json(event)
            if permission_action == "deny":
                reason = self._latest_hook_reason(events, "PermissionRequest") or "PermissionRequest hook 拒绝执行"
                self._run_hooks(
                    events,
                    "PermissionDenied",
                    call_id,
                    tool_name,
                    current_arguments,
                    reason=reason,
                )
                self._collect_hook_contexts_from_events(hook_contexts, events)
                return self._failed_tool(
                    events,
                    call_id,
                    tool_name,
                    current_arguments,
                    reason,
                    {"hook_decision": "deny", "error_code": TOOL_PERMISSION_DENIED},
                    hook_contexts=hook_contexts,
                )

        # ---- dsh 管线顺序：快照 → pre-hook/权限 → 验证 → 取消重查 → body ----
        # 参数在 hook 改写后过无损 JSON 快照边界（隔离 + 非序列化提前爆炸）。
        try:
            current_arguments = snapshot_arguments(current_arguments)
        except ValueError as exc:
            return self._failed_tool(
                events,
                call_id,
                tool_name,
                current_arguments,
                str(exc),
                {"error_code": INVALID_ARGS},
                hook_contexts=hook_contexts,
            )
        current_arguments = self._normalize_argument_aliases(tool_name, current_arguments)
        violations = self._validate_tool_arguments(tool_name, current_arguments)
        if violations:
            # INVALID_ARGS：body 永不执行（dsh ToolArgsError 语义），并把
            # path 限定的违规清单交给模型以便自我纠正。
            return self._failed_tool(
                events,
                call_id,
                tool_name,
                current_arguments,
                f"参数违反 {tool_name} 契约：" + "；".join(violations),
                {"error_code": INVALID_ARGS, "violations": violations},
                hook_contexts=hook_contexts,
            )
        if self.cancel_requested():
            return self._failed_tool(
                events,
                call_id,
                tool_name,
                current_arguments,
                "回合已取消，工具未启动。",
                {"error_code": TOOL_ABORTED_BEFORE_DISPATCH},
                hook_contexts=hook_contexts,
            )

        started_at = time.perf_counter()
        events.append(
            {
                "type": "tool_start",
                "call_id": call_id,
                "tool": tool_name,
                "arguments": current_arguments,
                "title": self._tool_title(tool_name, current_arguments),
                "parallel": parallel,
                "data": self._tool_start_data(tool_name, None),
            }
        )
        try:
            result = self._run_body_with_timeout(
                tool_name,
                current_arguments,
                require_permission or permission_preapproved,
            )
        except concurrent.futures.TimeoutError as exc:
            message = f"工具 {tool_name} 超过执行预算（{self._tool_timeout_ms(tool_name)}ms）被中止。"
            self._run_hooks(
                events,
                "PostToolUseFailure",
                call_id,
                tool_name,
                current_arguments,
                error=message,
            )
            return self._failed_tool(
                events,
                call_id,
                tool_name,
                current_arguments,
                message,
                {"error_code": TOOL_TIMEOUT, "timeout_ms": self._tool_timeout_ms(tool_name)},
                started_at=started_at,
                hook_contexts=hook_contexts,
            )
        except (ToolExecutionError, OSError, ValueError, subprocess.SubprocessError) as exc:
            message = str(exc)
            error_code = getattr(exc, "code", "TOOL_ERROR")
            failure_outcomes = self._run_hooks(
                events,
                "PostToolUseFailure",
                call_id,
                tool_name,
                current_arguments,
                error=message,
            )
            self._collect_hook_contexts(hook_contexts, failure_outcomes)
            return self._failed_tool(
                events,
                call_id,
                tool_name,
                current_arguments,
                message,
                {"error_code": error_code},
                started_at=started_at,
                hook_contexts=hook_contexts,
            )

        post_outcomes = self._run_hooks(
            events,
            "PostToolUse",
            call_id,
            tool_name,
            current_arguments,
            tool_response={"ok": result.ok, "output": result.output, "data": result.data or {}},
        )
        self._collect_hook_contexts(hook_contexts, post_outcomes)
        done_data = self._tool_done_data(
            tool_name,
            result.data or {},
            started_at=started_at,
            ok=result.ok,
            hook_contexts=hook_contexts,
        )
        events.append(
            {
                "type": "tool_done",
                "call_id": call_id,
                "tool": tool_name,
                "ok": result.ok,
                "title": result.title,
                "output": result.output,
                "data": done_data,
            }
        )
        return events, self._tool_result_json(result, done_data)

    def run_one_stream(
        self,
        call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        parallel: bool = False,
        require_permission: bool = False,
        approved: bool = False,
    ) -> tuple[list[dict], str]:
        events: list[dict] = []
        result_json = ""
        for event in self.iter_one_stream(
            call_id,
            tool_name,
            arguments,
            parallel=parallel,
            require_permission=require_permission,
            approved=approved,
        ):
            if event["type"] == "tool_result_json":
                result_json = str(event["result_json"])
            else:
                events.append(event)
        return events, result_json

    def iter_one_stream(
        self,
        call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        parallel: bool = False,
        require_permission: bool = False,
        approved: bool = False,
    ) -> Iterator[dict[str, Any]]:
        if tool_name == "ask_user_question":
            # dsh ask-user：未回答 → 发 user_question 事件挂起本轮（同 permission_request
            # 机制注册 pending approval）；用户回答后续跑时 arguments 携带 _answers。
            raw_arguments = dict(arguments)
            answers = raw_arguments.pop("_answers", None)
            if answers is None:
                yield from self._ask_user_first_pass(call_id, raw_arguments)
                return
            events, result_json = self.run_one(call_id, "ask_user_question", raw_arguments)
            # run_one 走通用管线（验证 questions 契约 + hooks）；结果由 answers 合成
            synthesized = self._synthesize_question_result_json(call_id, raw_arguments, answers)
            answer_output = str(json.loads(synthesized).get("output") or "")
            for event in events:
                if event.get("type") == "tool_done" and event.get("ok"):
                    event["title"] = "ask_user_question (answered)"
                    event["output"] = answer_output
                yield event
            yield {"type": "tool_result_json", "result_json": synthesized}
            return

        if tool_name != "bash":
            events, result_json = self.run_one(
                call_id,
                tool_name,
                arguments,
                parallel=parallel,
                require_permission=require_permission,
                approved=approved,
            )
            yield from events
            yield {"type": "tool_result_json", "result_json": result_json}
            return

        events: list[dict] = []
        current_arguments = dict(arguments)
        hook_contexts: list[str] = []
        # approved 表示这次执行来自 pending approval 的续跑，不再重复问同一个权限。
        permission_preapproved = approved
        pre_outcomes = self._run_hooks(events, "PreToolUse", call_id, tool_name, current_arguments)
        self._collect_hook_contexts(hook_contexts, pre_outcomes)
        for outcome in pre_outcomes:
            if outcome.updated_input:
                current_arguments.update(outcome.updated_input)
            if outcome.permission_decision == "deny":
                reason = outcome.reason or "PreToolUse hook 拒绝执行"
                failed_events, result_json = self._failed_tool(
                    events,
                    call_id,
                    tool_name,
                    current_arguments,
                    reason,
                    {"hook": outcome.name, "error_code": TOOL_PERMISSION_DENIED},
                    hook_contexts=hook_contexts,
                )
                yield from failed_events
                yield {"type": "tool_result_json", "result_json": result_json}
                return
            if outcome.permission_decision == "ask":
                event = self._permission_request_event(
                    call_id,
                    tool_name,
                    current_arguments,
                    message=outcome.reason or f"执行 {tool_name} 前需要用户确认。",
                    data={"reason": "PreToolUse hook 要求审批", "hook": outcome.name},
                    hook_contexts=hook_contexts,
                )
                events.append(event)
                yield from events
                yield {"type": "tool_result_json", "result_json": self._permission_result_json(event)}
                return
            if outcome.permission_decision == "allow":
                permission_preapproved = True

        if self._needs_permission_request(tool_name, current_arguments, require_permission) and not permission_preapproved:
            permission_action = self._run_permission_request_flow(
                events,
                call_id,
                tool_name,
                current_arguments,
                hook_contexts,
            )
            if permission_action == "ask":
                event = self._permission_request_event(
                    call_id,
                    tool_name,
                    current_arguments,
                    message=self._latest_hook_reason(events, "PermissionRequest"),
                    data=self._permission_request_data(tool_name, current_arguments),
                    hook_contexts=hook_contexts,
                )
                events.append(event)
                yield from events
                yield {"type": "tool_result_json", "result_json": self._permission_result_json(event)}
                return
            if permission_action == "deny":
                reason = self._latest_hook_reason(events, "PermissionRequest") or "PermissionRequest hook 拒绝执行"
                self._run_hooks(events, "PermissionDenied", call_id, tool_name, current_arguments, reason=reason)
                self._collect_hook_contexts_from_events(hook_contexts, events)
                failed_events, result_json = self._failed_tool(
                    events,
                    call_id,
                    tool_name,
                    current_arguments,
                    reason,
                    {"hook_decision": "deny"},
                    hook_contexts=hook_contexts,
                )
                yield from failed_events
                yield {"type": "tool_result_json", "result_json": result_json}
                return

        # ---- dsh 管线（流式 shell 路径同样）：快照 → 验证 → 取消重查 ----
        try:
            current_arguments = snapshot_arguments(current_arguments)
        except ValueError as exc:
            failed_events, result_json = self._failed_tool(
                events, call_id, tool_name, current_arguments, str(exc), {"error_code": INVALID_ARGS}, hook_contexts=hook_contexts
            )
            yield from failed_events
            yield {"type": "tool_result_json", "result_json": result_json}
            return
        current_arguments = self._normalize_argument_aliases(tool_name, current_arguments)
        violations = self._validate_tool_arguments(tool_name, current_arguments)
        if violations:
            failed_events, result_json = self._failed_tool(
                events,
                call_id,
                tool_name,
                current_arguments,
                f"参数违反 {tool_name} 契约：" + "；".join(violations),
                {"error_code": INVALID_ARGS, "violations": violations},
                hook_contexts=hook_contexts,
            )
            yield from failed_events
            yield {"type": "tool_result_json", "result_json": result_json}
            return
        if self.cancel_requested():
            failed_events, result_json = self._failed_tool(
                events,
                call_id,
                tool_name,
                current_arguments,
                "回合已取消，工具未启动。",
                {"error_code": TOOL_ABORTED_BEFORE_DISPATCH},
                hook_contexts=hook_contexts,
            )
            yield from failed_events
            yield {"type": "tool_result_json", "result_json": result_json}
            return

        try:
            command, workdir, timeout_ms = self._prepare_shell(current_arguments, approved=require_permission or permission_preapproved)
        except (ToolExecutionError, OSError, ValueError) as exc:
            failed_events, result_json = self._failed_tool(
                events, call_id, tool_name, current_arguments, str(exc), {"error_code": getattr(exc, "code", "TOOL_ERROR")}, hook_contexts=hook_contexts
            )
            yield from failed_events
            yield {"type": "tool_result_json", "result_json": result_json}
            return

        started_at = time.perf_counter()
        start_event = {
            "type": "tool_start",
            "call_id": call_id,
            "tool": tool_name,
            "arguments": current_arguments,
            "title": self._tool_title(tool_name, current_arguments),
            "parallel": parallel,
            "data": self._tool_start_data(tool_name, None),
        }
        yield from events
        yield start_event
        if bool(current_arguments.get("run_in_background")):
            job = self.process_manager.start_background(
                command,
                cwd=workdir,
                call_id=call_id,
            )
            done_data = self._tool_done_data(
                tool_name,
                {"job": job, "background": True},
                started_at=started_at,
                ok=True,
                hook_contexts=hook_contexts,
                )
            result_json = json.dumps(
                {
                    "tool": tool_name,
                    "title": f"后台执行：{command}",
                    "ok": True,
                    "output": f"已在后台启动 {job['id']}：{command}",
                    "data": done_data,
                },
                ensure_ascii=False,
            )
            yield {
                "type": "tool_done",
                "call_id": call_id,
                "tool": tool_name,
                "ok": True,
                "title": f"后台执行：{command}",
                "output": f"已在后台启动 {job['id']}：{command}",
                "data": done_data,
            }
            yield {"type": "tool_result_json", "result_json": result_json}
            return

        done_event: dict[str, Any] | None = None
        for event in self.process_manager.run_foreground(
            command,
            cwd=workdir,
            timeout_ms=timeout_ms,
            call_id=call_id,
            tool=tool_name,
            title=str(current_arguments.get("description") or "").strip() or None,
        ):
            if event["type"] == "tool_done":
                done_event = event
            else:
                yield event
        if done_event is None:
            failed_events, result_json = self._failed_tool([], call_id, tool_name, current_arguments, "shell 未返回完成事件", {}, hook_contexts=hook_contexts)
            yield from failed_events
            yield {"type": "tool_result_json", "result_json": result_json}
            return

        hook_events: list[dict] = []
        shell_outcomes = self._run_hooks(
            hook_events,
            "PostToolUse" if done_event.get("ok") else "PostToolUseFailure",
            call_id,
            tool_name,
            current_arguments,
            tool_response={"ok": done_event.get("ok"), "output": done_event.get("output"), "data": done_event.get("data") or {}},
            error=None if done_event.get("ok") else str(done_event.get("output") or ""),
        )
        self._collect_hook_contexts(hook_contexts, shell_outcomes)
        yield from hook_events
        done_event["data"] = self._tool_done_data(
            tool_name,
            done_event.get("data") if isinstance(done_event.get("data"), dict) else {},
            started_at=started_at,
            ok=bool(done_event.get("ok")),
            failure_reason=None if done_event.get("ok") else str(done_event.get("output") or ""),
            hook_contexts=hook_contexts,
        )
        yield done_event
        result_json = json.dumps(
            {
                "tool": tool_name,
                "title": done_event.get("title") or tool_name,
                "ok": bool(done_event.get("ok")),
                "output": str(done_event.get("output") or ""),
                "data": done_event.get("data") if isinstance(done_event.get("data"), dict) else {},
            },
            ensure_ascii=False,
        )
        yield {"type": "tool_result_json", "result_json": result_json}

    def _run_hooks(
        self,
        events: list[dict],
        hook_event: str,
        call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        tool_response: Any | None = None,
        error: str | None = None,
        reason: str | None = None,
    ) -> list[HookOutcome]:
        outcomes = self.hooks.run(
            hook_event,
            tool_name=tool_name,
            tool_input=arguments,
            tool_use_id=call_id,
            tool_response=tool_response,
            error=error,
            reason=reason,
        )
        for outcome in outcomes:
            events.append(
                {
                    "type": "hook_start",
                    "call_id": call_id,
                    "tool": tool_name,
                    "hook_event": hook_event,
                    "hook_name": outcome.name,
                    "title": f"Hook {hook_event}: {outcome.name}",
                    "data": {},
                }
            )
            events.append(
                {
                    "type": "hook_done",
                    "call_id": call_id,
                    "tool": tool_name,
                    "hook_event": hook_event,
                    "hook_name": outcome.name,
                    "title": f"Hook 完成：{outcome.name}",
                    "data": {
                        "permission_decision": outcome.permission_decision,
                        "reason": outcome.reason,
                        "updated_input": outcome.updated_input,
                        "additional_context": outcome.additional_context,
                    },
                }
            )
        return outcomes

    def _failed_tool(
        self,
        events: list[dict],
        call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        message: str,
        data: dict[str, Any],
        *,
        started_at: float | None = None,
        hook_contexts: list[str] | None = None,
    ) -> tuple[list[dict], str]:
        enriched_data = self._tool_done_data(
            tool_name,
            data,
            started_at=started_at,
            ok=False,
            failure_reason=message,
            arguments=arguments,
            hook_contexts=hook_contexts,
        )
        result_json = json.dumps(
            {"tool": tool_name, "ok": False, "error": message, "data": self._sanitize_data(enriched_data)},
            ensure_ascii=False,
        )
        events.append(
            {
                "type": "tool_done",
                "call_id": call_id,
                "tool": tool_name,
                "arguments": arguments,
                "ok": False,
                "title": f"{tool_name} 执行失败",
                "output": message,
                "data": enriched_data,
            }
        )
        return events, result_json

    def _run_permission_request_flow(
        self,
        events: list[dict],
        call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        hook_contexts: list[str],
    ) -> str:
        outcomes = self._run_hooks(events, "PermissionRequest", call_id, tool_name, arguments)
        self._collect_hook_contexts(hook_contexts, outcomes)
        decision: str | None = None
        for outcome in outcomes:
            if outcome.updated_input and outcome.permission_decision != "deny":
                arguments.update(outcome.updated_input)
            if outcome.permission_decision == "deny":
                decision = "deny"
            elif outcome.permission_decision == "ask" and decision not in {"deny"}:
                decision = "ask"
            elif outcome.permission_decision == "allow" and decision is None:
                decision = "allow"
        return decision or "ask"

    def _permission_request_event(
        self,
        call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        message: str | None = None,
        data: dict[str, Any] | None = None,
        hook_contexts: list[str] | None = None,
    ) -> dict[str, Any]:
        request_data = dict(data or {})
        contexts = self._unique_contexts(hook_contexts or [])
        if contexts:
            request_data["hook_contexts"] = contexts
        return {
            "type": "permission_request",
            "call_id": call_id,
            "tool": tool_name,
            "permission": self._permission_for(tool_name),
            "title": f"需要审批：{tool_name}",
            "message": message or f"执行 {tool_name} 前需要用户确认。",
            "arguments": arguments,
            "data": request_data,
        }

    def _permission_result_json(self, event: dict) -> str:
        return json.dumps(
            {
                "tool": event.get("tool"),
                "ok": False,
                "permission_request": True,
                "permission": event.get("permission"),
                "title": event.get("title"),
                "output": event.get("message"),
                "arguments": event.get("arguments") if isinstance(event.get("arguments"), dict) else {},
                "data": event.get("data") if isinstance(event.get("data"), dict) else {},
            },
            ensure_ascii=False,
        )

    def _sanitize_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """输出契约（dsh INVALID_TOOL_OUTPUT 语义）：结果 data 必须无损 JSON。

        非序列化值转字符串并在 error_code 标记，绝不让 json.dumps 在
        事件流中途炸掉整个 turn。
        """
        try:
            json.dumps(data, allow_nan=False)
            return data
        except (TypeError, ValueError):
            sanitized: dict[str, Any] = {}
            degraded = False
            for key, value in data.items():
                try:
                    json.dumps({key: value}, allow_nan=False)
                    sanitized[key] = value
                except (TypeError, ValueError):
                    sanitized[key] = repr(value)
                    degraded = True
            if degraded:
                sanitized["error_code"] = INVALID_TOOL_OUTPUT
            return sanitized

    def _tool_result_json(self, result: Any, data: dict[str, Any]) -> str:
        return json.dumps(
            {
                "tool": result.tool,
                "title": result.title,
                "ok": result.ok,
                "output": result.output,
                "data": self._sanitize_data(data),
            },
            ensure_ascii=False,
        )

    def _collect_hook_contexts(self, contexts: list[str], outcomes: list[HookOutcome]) -> None:
        for outcome in outcomes:
            if outcome.additional_context:
                contexts.append(outcome.additional_context)

    def _collect_hook_contexts_from_events(self, contexts: list[str], events: list[dict]) -> None:
        for event in events:
            if event.get("type") != "hook_done":
                continue
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            context = data.get("additional_context")
            if context:
                contexts.append(str(context))

    def _unique_contexts(self, contexts: list[str]) -> list[str]:
        unique: list[str] = []
        for context in contexts:
            text = str(context).strip()
            if text and text not in unique:
                unique.append(text)
        return unique

    def _latest_hook_reason(self, events: list[dict], hook_event: str) -> str | None:
        for event in reversed(events):
            if event.get("type") != "hook_done" or event.get("hook_event") != hook_event:
                continue
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            reason = data.get("reason")
            if reason:
                return str(reason)
        return None

    def _validate_tool_arguments(self, tool_name: str, arguments: dict[str, Any]) -> list[str]:
        """按工具契约验证参数；动态 MCP 工具无契约时不设防（返回空）。"""
        spec = TOOL_SPECS.get(tool_name)
        if spec is None or not spec.json_schema:
            return []
        return validate_arguments(spec.json_schema, arguments)

    def _normalize_argument_aliases(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """历史别名归一：shell 的 cmd → command（契约只认规范名）。"""
        if tool_name == "bash" and "cmd" in arguments and "command" not in arguments:
            arguments = dict(arguments)
            arguments["command"] = arguments.pop("cmd")
        return arguments

    def _tool_timeout_ms(self, tool_name: str) -> int:
        spec = TOOL_SPECS.get(tool_name)
        return spec.timeout_ms if spec else 30000

    def _run_body_with_timeout(self, tool_name: str, arguments: dict[str, Any], approved: bool) -> Any:
        """协作式超时预算包装（dsh tool-call-timeout-policy 的同步镜像）。

        预算耗尽立即返回 TIMEOUT 结果；后台线程无法强杀，会自然结束
        （与 dsh 的进程内协作契约一致——声明 timeout 即承诺可协作停止）。
        """
        timeout_ms = self._tool_timeout_ms(tool_name)
        future = _BODY_POOL.submit(
            self._run_tool_with_optional_approval,
            tool_name,
            arguments,
            approved,
        )
        try:
            return future.result(timeout=timeout_ms / 1000.0)
        except concurrent.futures.TimeoutError:
            # 不 cancel 线程（Python 无法中断），只放弃等待；shell 的子进程
            # 由 ProcessManager 的 job 清理兜底。
            future.cancel()
            raise

    def _run_tool_with_optional_approval(self, tool_name: str, arguments: dict[str, Any], approved: bool) -> Any:
        if not approved or self.tools.permission_mode != "ask":
            return self.tools.run(tool_name, arguments)
        original_mode = self.tools.permission_mode
        self.tools.permission_mode = "workspace_write"
        try:
            return self.tools.run(tool_name, arguments)
        finally:
            self.tools.permission_mode = original_mode

    def _ask_user_first_pass(self, call_id: str, arguments: dict[str, Any]) -> Iterator[dict]:
        """ask_user_question 首轮：验证契约 → user_question 事件（挂起等待回答）。

        事件结构与 permission_request 同形（call_id/tool/arguments/data），
        session_runner 据此注册 pending approval；用户经 /api/approvals/{id}/answer
        回答后 approve 流程带 _answers 续跑。
        """
        from .validation import validate_arguments
        from .workspace import TOOL_SPECS

        schema = TOOL_SPECS["ask_user_question"].json_schema
        violations = validate_arguments(schema, arguments)
        if violations:
            failed_events, result_json = self._failed_tool(
                [], call_id, "ask_user_question", arguments,
                "参数违反 ask_user_question 契约：" + "；".join(violations),
                {"error_code": "INVALID_ARGS", "violations": violations},
            )
            yield from failed_events
            yield {"type": "tool_result_json", "result_json": result_json}
            return
        questions = arguments.get("questions") if isinstance(arguments.get("questions"), list) else []
        event = {
            "type": "user_question",
            "call_id": call_id,
            "tool": "ask_user_question",
            "title": "向用户提问",
            "ok": False,
            "arguments": arguments,
            "message": "等待用户回答后继续。",
            "data": {
                "reason": "ask_user_question 需要用户输入",
                "questions": questions,
                "user_question": True,
            },
        }
        yield event
        yield {"type": "tool_result_json", "result_json": self._question_pending_json(event)}

    def _question_pending_json(self, event: dict) -> str:
        return json.dumps(
            {
                "tool": event.get("tool"),
                "ok": False,
                "user_question": True,
                "permission": "ask",
                "title": event.get("title"),
                "output": event.get("message"),
                "arguments": event.get("arguments") if isinstance(event.get("arguments"), dict) else {},
                "data": event.get("data") if isinstance(event.get("data"), dict) else {},
            },
            ensure_ascii=False,
        )

    def _synthesize_question_result_json(self, call_id: str, arguments: dict[str, Any], answers: Any) -> str:
        """续跑路径：把用户回答合成工具结果（dsh：回答即工具结果）。"""
        questions = arguments.get("questions") if isinstance(arguments.get("questions"), list) else []
        ids = [str(q.get("id")) for q in questions if isinstance(q, dict)]
        if isinstance(answers, dict):
            payload = {qid: answers.get(qid) for qid in ids}
        else:
            payload = {"answers": answers}
        return json.dumps(
            {
                "tool": "ask_user_question",
                "title": "ask_user_question (answered)",
                "ok": True,
                "output": "User answers:\n"
                + "\n".join(f"- {qid}: {json.dumps(payload.get(qid), ensure_ascii=False)}" for qid in ids),
                "data": {"answers": payload},
            },
            ensure_ascii=False,
        )

    def _prepare_shell(self, arguments: dict[str, Any], *, approved: bool = False) -> tuple[str, Any, int]:
        command = str(arguments.get("command") or "").strip()
        if not command:
            raise ToolExecutionError("bash 需要 command")
        if approved and self.tools.permission_mode == "ask":
            original_mode = self.tools.permission_mode
            self.tools.permission_mode = "workspace_write"
            try:
                self.tools._check_permission("bash")
            finally:
                self.tools.permission_mode = original_mode
        else:
            self.tools._check_permission("bash")
        risk = self.tools.shell_command_risk(command)
        if risk["blocked"]:
            raise ToolExecutionError(f"命令命中黑名单，拒绝执行：{risk['reason']}：{command}")
        workdir = self.tools._resolve_workspace_path(str(arguments.get("workdir") or "."))
        timeout_ms = min(int(arguments.get("timeoutMs") or 30000), 120000)
        return command, workdir, timeout_ms

    def _needs_permission_request(self, tool_name: str, arguments: dict[str, Any], require_permission: bool) -> bool:
        if not require_permission:
            return False
        if self.tools.permission_mode == "bypass_permissions":
            return False
        if tool_name != "bash":
            return True
        if self.tools.permission_mode == "ask":
            return True
        risk = self.tools.shell_command_risk(str(arguments.get("command") or arguments.get("cmd") or ""))
        return bool(risk["blocked"]) is False and str(risk["risk"]) in {"medium", "high"}

    def _permission_request_data(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name != "bash":
            return {"reason": "ask 模式需要审批"}
        risk = self.tools.shell_command_risk(str(arguments.get("command") or arguments.get("cmd") or ""))
        return {
            "reason": "中高风险 shell 命令需要审批" if self.tools.permission_mode != "ask" else "ask 模式需要审批",
            "risk": risk["risk"],
            "risk_reason": risk["reason"],
            "blocked": risk["blocked"],
        }

    def _permission_for(self, tool_name: str) -> str:
        specs = {item["name"]: item for item in self.tools.list_specs()}
        return str(specs.get(tool_name, {}).get("permission") or "unknown")

    def _tool_title(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """工具行标题（dsh presentCall 的精简形态）。bash 用必填 description。"""
        if tool_name == "bash":
            description = str(arguments.get("description") or "").strip()
            return description or str(arguments.get("command") or "bash")[:80]
        target = (
            arguments.get("file_path")
            or arguments.get("path")
            or arguments.get("pattern")
            or arguments.get("query")
            or arguments.get("url")
            or ""
        )
        return f"{tool_name} {target}".strip()

    def _tool_start_data(self, tool_name: str, annotation: str | None = None) -> dict[str, Any]:
        data: dict[str, Any] = {"spec": self._tool_spec_data(tool_name)}
        if annotation:
            data["annotation"] = annotation
        return data

    def _tool_spec_data(self, tool_name: str) -> dict[str, Any]:
        dynamic_specs = {item["name"]: item for item in self.tools.list_specs()}
        if tool_name in dynamic_specs:
            item = dynamic_specs[tool_name]
            return {
                "name": item.get("name"),
                "description": item.get("description"),
                "permission": item.get("permission"),
                "risk": item.get("risk"),
                "category": item.get("category"),
                "schema": item.get("schema") or {},
                "supports_parallel": bool(item.get("supports_parallel")),
                "interrupt_behavior": item.get("interrupt_behavior"),
                "mcp": item.get("mcp"),
            }
        spec = TOOL_SPECS.get(tool_name)
        if spec is None:
            return {"name": tool_name, "permission": "unknown", "risk": "unknown", "schema": {}}
        return {
            "name": spec.name,
            "description": spec.description,
            "permission": spec.permission,
            "risk": spec.risk,
            "category": spec.category,
            "schema": spec.schema,
            "supports_parallel": spec.supports_parallel,
            "interrupt_behavior": spec.interrupt_behavior,
        }

    def _tool_done_data(
        self,
        tool_name: str,
        data: dict[str, Any],
        *,
        started_at: float | None,
        ok: bool,
        failure_reason: str | None = None,
        arguments: dict[str, Any] | None = None,
        hook_contexts: list[str] | None = None,
        annotation: str = "",
    ) -> dict[str, Any]:
        enriched = {key: value for key, value in data.items() if value is not None}
        enriched["spec"] = self._tool_spec_data(tool_name)
        if annotation:
            enriched["annotation"] = annotation
        contexts = self._unique_contexts(hook_contexts or [])
        if contexts:
            enriched["hook_contexts"] = contexts
        if started_at is not None:
            enriched["duration_ms"] = max(0, int((time.perf_counter() - started_at) * 1000))
        if not ok:
            enriched["failure_reason"] = failure_reason or "工具执行失败"
            enriched["retryable"] = True
        return enriched
