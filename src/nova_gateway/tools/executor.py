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
    ) -> tuple[list[dict], str]:
        events: list[dict] = []
        current_arguments = dict(arguments)

        pre_outcomes = self._run_hooks(
            events,
            "PreToolUse",
            call_id,
            tool_name,
            current_arguments,
        )
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
                return self._failed_tool(events, call_id, tool_name, current_arguments, reason, {"hook": outcome.name})
            if outcome.permission_decision == "ask":
                event = {
                    "type": "permission_request",
                    "call_id": call_id,
                    "tool": tool_name,
                    "permission": self._permission_for(tool_name),
                    "title": f"需要审批：{tool_name}",
                    "message": outcome.reason or f"执行 {tool_name} 前需要用户确认。",
                    "arguments": current_arguments,
                    "data": {"reason": "PreToolUse hook 要求审批", "hook": outcome.name},
                }
                events.append(event)
                return events, json.dumps(
                    {
                        "tool": tool_name,
                        "ok": False,
                        "permission_request": True,
                        "permission": event["permission"],
                        "arguments": current_arguments,
                        "output": event["message"],
                        "data": event["data"],
                    },
                    ensure_ascii=False,
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
                "data": {"spec": self._tool_spec_data(tool_name)},
            }
        )
        try:
            result = self.tools.run(tool_name, current_arguments)
        except (ToolExecutionError, OSError, ValueError, subprocess.SubprocessError) as exc:
            message = str(exc)
            self._run_hooks(
                events,
                "PostToolUseFailure",
                call_id,
                tool_name,
                current_arguments,
                error=message,
            )
            return self._failed_tool(events, call_id, tool_name, current_arguments, message, {}, started_at=started_at)

        self._run_hooks(
            events,
            "PostToolUse",
            call_id,
            tool_name,
            current_arguments,
            tool_response={"ok": result.ok, "output": result.output, "data": result.data or {}},
        )
        events.append(
            {
                "type": "tool_done",
                "call_id": call_id,
                "tool": tool_name,
                "ok": result.ok,
                "title": result.title,
                "output": result.output,
                "data": self._tool_done_data(tool_name, result.data or {}, started_at=started_at, ok=result.ok),
            }
        )
        return events, tool_result_as_json(result)

    def run_one_stream(
        self,
        call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        parallel: bool = False,
    ) -> tuple[list[dict], str]:
        events: list[dict] = []
        result_json = ""
        for event in self.iter_one_stream(call_id, tool_name, arguments, parallel=parallel):
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
    ) -> Iterator[dict[str, Any]]:
        if tool_name != "shell_command":
            events, result_json = self.run_one(call_id, tool_name, arguments, parallel=parallel)
            yield from events
            yield {"type": "tool_result_json", "result_json": result_json}
            return

        events: list[dict] = []
        current_arguments = dict(arguments)
        pre_outcomes = self._run_hooks(events, "PreToolUse", call_id, tool_name, current_arguments)
        for outcome in pre_outcomes:
            if outcome.updated_input:
                current_arguments.update(outcome.updated_input)
            if outcome.permission_decision == "deny":
                reason = outcome.reason or "PreToolUse hook 拒绝执行"
                failed_events, result_json = self._failed_tool(events, call_id, tool_name, current_arguments, reason, {"hook": outcome.name})
                yield from failed_events
                yield {"type": "tool_result_json", "result_json": result_json}
                return
            if outcome.permission_decision == "ask":
                event = {
                    "type": "permission_request",
                    "call_id": call_id,
                    "tool": tool_name,
                    "permission": self._permission_for(tool_name),
                    "title": f"需要审批：{tool_name}",
                    "message": outcome.reason or f"执行 {tool_name} 前需要用户确认。",
                    "arguments": current_arguments,
                    "data": {"reason": "PreToolUse hook 要求审批", "hook": outcome.name},
                }
                events.append(event)
                yield from events
                yield {"type": "tool_result_json", "result_json": self._permission_result_json(event)}
                return

        try:
            command, workdir, timeout_ms = self._prepare_shell(current_arguments)
        except (ToolExecutionError, OSError, ValueError) as exc:
            failed_events, result_json = self._failed_tool(events, call_id, tool_name, current_arguments, str(exc), {})
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
            "data": {"spec": self._tool_spec_data(tool_name)},
        }
        yield from events
        yield start_event
        if bool(current_arguments.get("background")):
            job = self.process_manager.start_background(command, cwd=workdir)
            result_json = json.dumps(
                {
                    "tool": tool_name,
                    "title": f"后台执行：{command}",
                    "ok": True,
                    "output": f"已在后台启动 {job['id']}：{command}",
                    "data": self._tool_done_data(tool_name, {"job": job, "background": True}, started_at=started_at, ok=True),
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
                "data": self._tool_done_data(tool_name, {"job": job, "background": True}, started_at=started_at, ok=True),
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
            failed_events, result_json = self._failed_tool([], call_id, tool_name, current_arguments, "shell 未返回完成事件", {})
            yield from failed_events
            yield {"type": "tool_result_json", "result_json": result_json}
            return

        hook_events: list[dict] = []
        self._run_hooks(
            hook_events,
            "PostToolUse" if done_event.get("ok") else "PostToolUseFailure",
            call_id,
            tool_name,
            current_arguments,
            tool_response={"ok": done_event.get("ok"), "output": done_event.get("output"), "data": done_event.get("data") or {}},
            error=None if done_event.get("ok") else str(done_event.get("output") or ""),
        )
        yield from hook_events
        done_event["data"] = self._tool_done_data(
            tool_name,
            done_event.get("data") if isinstance(done_event.get("data"), dict) else {},
            started_at=started_at,
            ok=bool(done_event.get("ok")),
            failure_reason=None if done_event.get("ok") else str(done_event.get("output") or ""),
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
    ) -> tuple[list[dict], str]:
        enriched_data = self._tool_done_data(
            tool_name,
            data,
            started_at=started_at,
            ok=False,
            failure_reason=message,
            arguments=arguments,
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

    def _prepare_shell(self, arguments: dict[str, Any]) -> tuple[str, Any, int]:
        command = str(arguments.get("command") or arguments.get("cmd") or "").strip()
        if not command:
            raise ToolExecutionError("shell_command 需要 command")
        self.tools._check_permission("shell_command")
        if not self.tools._is_allowed_shell_command(command):
            raise ToolExecutionError(f"命令需要审批，当前版本已拦截：{command}")
        workdir = self.tools._resolve_workspace_path(str(arguments.get("workdir") or "."))
        timeout_ms = min(int(arguments.get("timeout_ms") or 10000), 120000)
        return command, workdir, timeout_ms

    def _permission_for(self, tool_name: str) -> str:
        specs = {item["name"]: item for item in self.tools.list_specs()}
        return str(specs.get(tool_name, {}).get("permission") or "unknown")

    def _tool_title(self, tool_name: str, arguments: dict[str, Any]) -> str:
        target = arguments.get("path") or arguments.get("query") or arguments.get("command") or arguments.get("url") or ""
        return f"{tool_name} {target}".strip()

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
    ) -> dict[str, Any]:
        enriched = {key: value for key, value in data.items() if value is not None}
        enriched["spec"] = self._tool_spec_data(tool_name)
        if started_at is not None:
            enriched["duration_ms"] = max(0, int((time.perf_counter() - started_at) * 1000))
        if not ok:
            enriched["failure_reason"] = failure_reason or "工具执行失败"
            enriched["retryable"] = True
            if tool_name == "apply_patch" and "diff" not in enriched and isinstance(arguments, dict):
                patch_text = str(arguments.get("patch") or "")
                enriched["diff"] = self.tools._diff_summary(patch_text) if patch_text else None
        return enriched
