"""固定密度 token 计价（照抄 dsh-token-meter/src/estimate.ts 的启发式）。

dsh 的计价原则：估算与回放共用同一套定价，密度固定 4 chars/token，
并为每条消息计入角色字段框架开销（ROLE_OVERHEAD）。预算阶段优先
稳定与保守，真实计费仍以 provider 返回的 usage 为准。
"""

from __future__ import annotations

from ..models import ChatMessage

# 固定文本密度：在需要精确 tokenizer 之前统一使用的估算密度。
CHARS_PER_TOKEN = 4

# 每条消息的角色字段框架开销（dsh ROLE_OVERHEAD）。
ROLE_OVERHEAD = 4


def estimate_tokens(text: str | None) -> int:
    """稳定、可复现的粗略 token 估算（与旧 context_budget.estimate_tokens 兼容）。"""
    cleaned = text or ""
    if not cleaned:
        return 0
    return max(1, (len(cleaned) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


def estimate_message_tokens(message: ChatMessage) -> int:
    """单条消息计价：内容密度估算 + 角色框架开销。"""
    return ROLE_OVERHEAD + estimate_tokens(message.content)


def estimate_messages_tokens(messages: list[ChatMessage]) -> int:
    """一组消息的总价（surface 压力测量用）。"""
    return sum(estimate_message_tokens(message) for message in messages)
