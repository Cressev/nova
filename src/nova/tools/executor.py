from __future__ import annotations

import json
import subprocess
import time
from typing import Any, Iterator

from ..processes.manager import ProcessManager
from .hooks import HookOutcome, ToolHookRunner
from .workspace import TOOL_SPECS, ToolExecutionError, WorkspaceTools, tool_result_as_json


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
    ) -> None:
        self.tools = tools
        self.hooks = hooks or ToolHookRunner(cwd=tools.project_root)
        self.process_manager = process_manager or ProcessManager()

    def run_one(
        self,
        call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        parallel: bool = False,
        require_permission: bool = False,
    ) -> tuple[list[dict], str]:
        events: list[dict] = []
        current_arguments = dict(arguments)
        annotation = self._pop_annotation(current_arguments)
        hook_contexts: list[str] = []
        permission_preapproved = False

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
                    {"hook": outcome.name},
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
                    {"hook_decision": "deny"},
                    hook_contexts=hook_contexts,
                )

        started_at = time.perf_counter()
        events.append(
            {
                "type": "tool_start",
                "call_id": call_id,
                "tool": tool_name,
                "arguments": current_arguments,
                "title": annotation or self._tool_title(tool_name, current_arguments),
                "parallel": parallel,
                "data": self._tool_start_data(tool_name, annotation),
            }
        )
        try:
            result = self._run_tool_with_optional_approval(tool_name, current_arguments, require_permission or permission_preapproved)
        except (ToolExecutionError, OSError, ValueError, subprocess.SubprocessError) as exc:
            message = str(exc)
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
                {},
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
            annotation=annotation,
        )
        events.append(
            {
                "type": "tool_done",
                "call_id": call_id,
                "tool": tool_name,
                "ok": result.ok,
                "title": annotation or result.title,
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
    ) -> tuple[list[dict], str]:
        events: list[dict] = []
        result_json = ""
        for event in self.iter_one_stream(call_id, tool_name, arguments, parallel=parallel, require_permission=require_permission):
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
    ) -> Iterator[dict[str, Any]]:
        if tool_name != "shell_command":
            events, result_json = self.run_one(call_id, tool_name, arguments, parallel=parallel, require_permission=require_permission)
            yield from events
            yield {"type": "tool_result_json", "result_json": result_json}
            return

        events: list[dict] = []
        current_arguments = dict(arguments)
        annotation = self._pop_annotation(current_arguments)
        hook_contexts: list[str] = []
        permission_preapproved = False
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
                    {"hook": outcome.name},
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

        try:
            command, workdir, timeout_ms = self._prepare_shell(current_arguments, approved=require_permission or permission_preapproved)
        except (ToolExecutionError, OSError, ValueError) as exc:
            failed_events, result_json = self._failed_tool(events, call_id, tool_name, current_arguments, str(exc), {}, hook_contexts=hook_contexts)
            yield from failed_events
            yield {"type": "tool_result_json", "result_json": result_json}
            return

        started_at = time.perf_counter()
        start_event = {
            "type": "tool_start",
            "call_id": call_id,
            "tool": tool_name,
            "arguments": current_arguments,
            "title": annotation or self._tool_title(tool_name, current_arguments),
            "parallel": parallel,
            "data": self._tool_start_data(tool_name, annotation),
        }
        yield from events
        yield start_event
        if bool(current_arguments.get("background")):
            job = self.process_manager.start_background(command, cwd=workdir)
            done_data = self._tool_done_data(
                tool_name,
                {"job": job, "background": True},
                started_at=started_at,
                ok=True,
                hook_contexts=hook_contexts,
                annotation=annotation,
            )
            result_json = json.dumps(
                {
                    "tool": tool_name,
                    "title": annotation or f"后台执行：{command}",
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
                "title": annotation or f"后台执行：{command}",
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
            annotation=annotation,
        )
        if annotation:
            done_event["title"] = annotation
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
            {"tool": tool_name, "ok": False, "error": message, "data": enriched_data},
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

    def _tool_result_json(self, result: Any, data: dict[str, Any]) -> str:
        return json.dumps(
            {
                "tool": result.tool,
                "title": result.title,
                "ok": result.ok,
                "output": result.output,
                "data": data,
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

    def _run_tool_with_optional_approval(self, tool_name: str, arguments: dict[str, Any], approved: bool) -> Any:
        if not approved or self.tools.permission_mode != "ask":
            return self.tools.run(tool_name, arguments)
        original_mode = self.tools.permission_mode
        self.tools.permission_mode = "workspace_write"
        try:
            return self.tools.run(tool_name, arguments)
        finally:
            self.tools.permission_mode = original_mode

    def _prepare_shell(self, arguments: dict[str, Any], *, approved: bool = False) -> tuple[str, Any, int]:
        command = str(arguments.get("command") or arguments.get("cmd") or "").strip()
        if not command:
            raise ToolExecutionError("shell_command 需要 command")
        if approved and self.tools.permission_mode == "ask":
            original_mode = self.tools.permission_mode
            self.tools.permission_mode = "workspace_write"
            try:
                self.tools._check_permission("shell_command")
            finally:
                self.tools.permission_mode = original_mode
        else:
            self.tools._check_permission("shell_command")
        risk = self.tools.shell_command_risk(command)
        if risk["blocked"]:
            raise ToolExecutionError(f"命令命中黑名单，拒绝执行：{risk['reason']}：{command}")
        workdir = self.tools._resolve_workspace_path(str(arguments.get("workdir") or "."))
        timeout_ms = min(int(arguments.get("timeout_ms") or 10000), 120000)
        return command, workdir, timeout_ms

    def _needs_permission_request(self, tool_name: str, arguments: dict[str, Any], require_permission: bool) -> bool:
        if not require_permission:
            return False
        if self.tools.permission_mode == "bypass_permissions":
            return False
        if tool_name != "shell_command":
            return True
        if self.tools.permission_mode == "ask":
            return True
        risk = self.tools.shell_command_risk(str(arguments.get("command") or arguments.get("cmd") or ""))
        return bool(risk["blocked"]) is False and str(risk["risk"]) in {"medium", "high"}

    def _permission_request_data(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name != "shell_command":
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
        target = arguments.get("path") or arguments.get("query") or arguments.get("command") or arguments.get("url") or ""
        return f"{tool_name} {target}".strip()

    def _pop_annotation(self, arguments: dict[str, Any]) -> str:
        raw = arguments.pop("annotation", "") or arguments.pop("anotation", "")
        text = str(raw).strip()
        if len(text) > 80:
            return text[:80].rstrip() + "..."
        return text

    def _tool_start_data(self, tool_name: str, annotation: str) -> dict[str, Any]:
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
            if tool_name == "apply_patch" and "diff" not in enriched and isinstance(arguments, dict):
                patch_text = str(arguments.get("patch") or "")
                enriched["diff"] = self.tools._diff_summary(patch_text) if patch_text else None
        return enriched
