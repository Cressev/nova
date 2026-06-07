from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


HOOK_EVENTS = [
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "PostToolUseFailure",
    "PermissionDenied",
]


@dataclass(frozen=True)
class HookOutcome:
    name: str
    hook_event: str
    permission_decision: str | None = None
    reason: str | None = None
    updated_input: dict[str, Any] | None = None
    additional_context: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


class ToolHookRunner:
    """执行 Nova 工具 hook。

    当前支持两类 hook：声明式 hook（直接在 JSON 中给 decision/updated_input）
    和命令 hook（把 hook 输入写入 stdin，读取 JSON 输出）。这保留了 cc 源码
    hook 生命周期的关键接口，同时避免在核心 runtime 中硬编码具体策略。
    """

    def __init__(self, config: dict[str, Any] | None = None, *, cwd: Path | None = None) -> None:
        raw = config or {}
        self.hooks = raw.get("hooks", raw) if isinstance(raw.get("hooks", raw), dict) else {}
        self.cwd = cwd

    @classmethod
    def from_file(cls, path: Path | None, *, cwd: Path | None = None) -> "ToolHookRunner":
        if path is None:
            return cls(cwd=cwd)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            payload = {}
        return cls(payload if isinstance(payload, dict) else {}, cwd=cwd)

    def enabled(self) -> bool:
        return any(self.hooks.get(event) for event in HOOK_EVENTS)

    def run(
        self,
        hook_event: str,
        *,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_use_id: str,
        tool_response: Any | None = None,
        error: str | None = None,
        reason: str | None = None,
    ) -> list[HookOutcome]:
        outcomes: list[HookOutcome] = []
        for hook in self._matching_hooks(hook_event, tool_name):
            payload = {
                "hook_event_name": hook_event,
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_use_id": tool_use_id,
                "tool_response": tool_response,
                "error": error,
                "reason": reason,
            }
            outcomes.append(self._run_hook(hook_event, hook, payload))
        return outcomes

    def _matching_hooks(self, hook_event: str, tool_name: str) -> list[dict[str, Any]]:
        hooks = self.hooks.get(hook_event, [])
        if not isinstance(hooks, list):
            return []
        return [hook for hook in hooks if isinstance(hook, dict) and self._matches(hook, tool_name)]

    def _matches(self, hook: dict[str, Any], tool_name: str) -> bool:
        matcher = hook.get("matcher", "*")
        if matcher == "*":
            return True
        if isinstance(matcher, str):
            return matcher == tool_name
        if isinstance(matcher, list):
            return tool_name in {str(item) for item in matcher}
        return False

    def _run_hook(self, hook_event: str, hook: dict[str, Any], payload: dict[str, Any]) -> HookOutcome:
        command = hook.get("command")
        output: dict[str, Any] = {}
        if command:
            output = self._run_command_hook(command, payload, int(hook.get("timeout_ms") or 5000))
        merged = {**hook, **output}
        return HookOutcome(
            name=str(merged.get("name") or hook_event),
            hook_event=hook_event,
            permission_decision=self._optional_str(merged.get("permission_decision") or merged.get("decision")),
            reason=self._optional_str(merged.get("reason") or merged.get("permissionDecisionReason") or merged.get("message")),
            updated_input=merged.get("updated_input") if isinstance(merged.get("updated_input"), dict) else None,
            additional_context=self._optional_str(merged.get("additional_context") or merged.get("additionalContext")),
            data={key: value for key, value in merged.items() if key not in {"command", "matcher"}},
        )

    def _run_command_hook(self, command: Any, payload: dict[str, Any], timeout_ms: int) -> dict[str, Any]:
        try:
            result = subprocess.run(
                command if isinstance(command, list) else str(command),
                cwd=self.cwd,
                input=json.dumps(payload, ensure_ascii=False),
                text=True,
                capture_output=True,
                timeout=max(0.1, min(timeout_ms / 1000, 30)),
                shell=not isinstance(command, list),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return {"permission_decision": "deny", "reason": f"Hook 执行失败：{exc}"}
        if result.returncode != 0:
            return {"permission_decision": "deny", "reason": result.stderr.strip() or "Hook 返回非零退出码"}
        try:
            parsed = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return {"additional_context": result.stdout.strip()}
        return parsed if isinstance(parsed, dict) else {}

    def _optional_str(self, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None
