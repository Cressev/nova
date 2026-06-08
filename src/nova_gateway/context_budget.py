from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

from .models import ChatEvent, ChatMessage, ChatRole, new_id


def estimate_tokens(text: str) -> int:
    """稳定、可复现的粗略 token 估算。

    参考 cc 的 `tokenCountWithEstimation` 思路：预算阶段优先稳定和保守，
    真实计费仍以模型供应商返回为准。
    """
    cleaned = text or ""
    if not cleaned:
        return 0
    return max(1, (len(cleaned) + 3) // 4)


@dataclass(frozen=True)
class ContextBudgetPlan:
    messages: list[ChatMessage]
    input_tokens: int
    output_tokens: int
    used_tokens: int
    context_window_tokens: int
    effective_window_tokens: int
    output_reserve_tokens: int
    auto_compact_threshold_tokens: int
    remaining_tokens: int
    remaining_percent: float
    context_budget_status: str
    compact_recommended: bool
    dropped_message_count: int
    retained_message_count: int
    key_tool_result_count: int
    key_tool_result_tokens: int
    should_auto_compact: bool

    def as_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "used_tokens": self.used_tokens,
            "context_window_tokens": self.context_window_tokens,
            "context_effective_window_tokens": self.effective_window_tokens,
            "context_output_reserve_tokens": self.output_reserve_tokens,
            "auto_compact_threshold_tokens": self.auto_compact_threshold_tokens,
            "context_remaining_tokens": self.remaining_tokens,
            "context_remaining_percent": self.remaining_percent,
            "context_budget_status": self.context_budget_status,
            "compact_recommended": self.compact_recommended,
            "dropped_message_count": self.dropped_message_count,
            "retained_message_count": self.retained_message_count,
            "key_tool_result_count": self.key_tool_result_count,
            "key_tool_result_tokens": self.key_tool_result_tokens,
            "should_auto_compact": self.should_auto_compact,
        }


def build_context_budget_plan(
    *,
    session_id: str,
    messages: list[ChatMessage],
    events: list[ChatEvent],
    context_window_tokens: int,
) -> ContextBudgetPlan:
    output_reserve = _output_reserve_tokens(context_window_tokens)
    effective_window = max(context_window_tokens - output_reserve, 1)
    threshold = _auto_compact_threshold(effective_window)
    input_tokens = _message_input_tokens(messages) + _event_input_tokens(events)
    output_tokens = _message_output_tokens(messages) + _event_output_tokens(events)
    used_tokens = input_tokens + output_tokens
    remaining = max(context_window_tokens - used_tokens, 0)
    remaining_percent = round((remaining / context_window_tokens) * 100, 1) if context_window_tokens else 0.0
    status = _budget_status(remaining_percent, used_tokens, threshold)
    should_auto_compact = used_tokens >= threshold

    tool_summary = _key_tool_result_summary(session_id, events, max_tokens=max(120, min(2000, effective_window // 4)))
    tool_tokens = estimate_tokens(tool_summary) if tool_summary else 0
    message_budget = max(80, effective_window - tool_tokens)
    retained = _retain_recent_messages(messages, message_budget)
    synthetic_messages = []
    if tool_summary:
        synthetic_messages.append(
            ChatMessage(
                id=new_id("ctx"),
                session_id=session_id,
                role=ChatRole.SYSTEM,
                content=tool_summary,
            )
        )
    budgeted_messages = [*synthetic_messages, *retained]

    return ContextBudgetPlan(
        messages=budgeted_messages,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        used_tokens=used_tokens,
        context_window_tokens=context_window_tokens,
        effective_window_tokens=effective_window,
        output_reserve_tokens=output_reserve,
        auto_compact_threshold_tokens=threshold,
        remaining_tokens=remaining,
        remaining_percent=remaining_percent,
        context_budget_status=status,
        compact_recommended=should_auto_compact or status in {"warning", "critical"},
        dropped_message_count=max(len(messages) - len(retained), 0),
        retained_message_count=len(retained),
        key_tool_result_count=_count_summary_items(tool_summary),
        key_tool_result_tokens=tool_tokens,
        should_auto_compact=should_auto_compact,
    )


def _output_reserve_tokens(context_window_tokens: int) -> int:
    return max(64, min(20_000, max(context_window_tokens // 8, 1)))


def _auto_compact_threshold(effective_window_tokens: int) -> int:
    buffer = max(40, min(13_000, max(effective_window_tokens // 10, 1)))
    return max(effective_window_tokens - buffer, 1)


def _message_input_tokens(messages: Iterable[ChatMessage]) -> int:
    return sum(estimate_tokens(message.content) for message in messages if message.role in {ChatRole.USER, ChatRole.SYSTEM})


def _message_output_tokens(messages: Iterable[ChatMessage]) -> int:
    return sum(estimate_tokens(message.content) for message in messages if message.role in {ChatRole.ASSISTANT, ChatRole.ERROR})


def _event_input_tokens(events: Iterable[ChatEvent]) -> int:
    return sum(estimate_tokens(json.dumps(event.arguments, ensure_ascii=False)) for event in events if event.arguments)


def _event_output_tokens(events: Iterable[ChatEvent]) -> int:
    return sum(estimate_tokens(event.output or "") for event in events if event.output)


def _budget_status(remaining_percent: float, used_tokens: int, threshold: int) -> str:
    if used_tokens >= threshold or remaining_percent <= 5:
        return "critical"
    if remaining_percent <= 15:
        return "warning"
    return "normal"


def _retain_recent_messages(messages: list[ChatMessage], budget_tokens: int) -> list[ChatMessage]:
    retained: list[ChatMessage] = []
    used = 0
    for message in reversed(messages):
        cost = estimate_tokens(message.content)
        if retained and used + cost > budget_tokens:
            break
        retained.append(message)
        used += cost
    retained.reverse()
    return retained or messages[-1:]


def _key_tool_result_summary(session_id: str, events: list[ChatEvent], *, max_tokens: int) -> str:
    selected: list[ChatEvent] = []
    seen: set[str] = set()
    for event in reversed(events):
        if event.event_type != "tool.completed" or not event.output:
            continue
        key = event.id or event.tool or event.title
        if key in seen:
            continue
        seen.add(key)
        selected.append(event)
        if len(selected) >= 6:
            break
    if not selected:
        return ""

    lines = ["# 关键工具结果摘要", "这些是预算器保留的最近关键工具结果，用于在裁剪历史后继续推理："]
    used = estimate_tokens("\n".join(lines))
    count = 0
    for event in reversed(selected):
        preview = _compact_text(event.output or "", 520)
        line = f"- {event.tool or 'tool'} / {event.title}: {preview}"
        cost = estimate_tokens(line)
        if count > 0 and used + cost > max_tokens:
            break
        lines.append(line)
        used += cost
        count += 1
    return "\n".join(lines) if count else ""


def _count_summary_items(summary: str) -> int:
    return sum(1 for line in summary.splitlines() if line.startswith("- "))


def _compact_text(text: str, limit: int) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"
