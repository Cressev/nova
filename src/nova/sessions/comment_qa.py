"""划词评论问答的上下文构建器。

拼接分层（用户拍板 26/09/15）：
  1. 系统提示（旁路解释助手定位）
  2. 主对话背景（只读；按预算裁剪，超了从最老开始丢，不做摘要压缩）
  3. 被引用消息全文（永远保留）
  4. 用户选中的内容 quote（永远保留）
  5. 本线程历史（追问时；永远保留）
  6. 当前问题（永远保留）

裁剪只作用于第 2 层，复用 estimate_tokens 的稳定估算。
"""

from __future__ import annotations

from ..context_budget import estimate_tokens
from ..models import ChatMessage, ChatRole, CommentAnchor
from .comments import CommentStore
from .store import SessionStore

SYSTEM_PROMPT = (
    "你是对话旁路解释助手。用户在主对话里选中了助手回复的一段内容并向你提问。"
    "主对话背景仅供理解前因后果。回答要精准简洁，直接解决用户的疑问，"
    "不要大段复述原文，不要引入主对话之外的推测。"
)

# 背景层最多占用的 token（引用全文+线程+问题之外都留给正文回答）
_MAX_BACKGROUND_TOKENS = 6000


def build_comment_messages(
    session_store: SessionStore,
    comment_store: CommentStore,
    anchor: CommentAnchor,
    question: str,
    *,
    background_limit_tokens: int = _MAX_BACKGROUND_TOKENS,
) -> list[ChatMessage]:
    """构建评论问答的完整消息序列。"""
    # 3. 被引用消息全文
    quoted_message = session_store.get_chat_message(anchor.message_id)
    quoted_block = ""
    if quoted_message is not None and quoted_message.content:
        quoted_block = quoted_message.content.strip()

    # 5. 本线程历史
    history = comment_store.list_entries(anchor.id)
    thread_messages: list[ChatMessage] = []
    for entry in history:
        role = ChatRole.USER if entry.role == "user" else ChatRole.ASSISTANT
        thread_messages.append(ChatMessage(session_id=anchor.session_id, role=role, content=entry.content))

    # 固定层 token：系统 + 引用块 + quote + 线程 + 问题
    fixed_text = SYSTEM_PROMPT + quoted_block + anchor.quote + question
    for m in thread_messages:
        fixed_text += m.content
    fixed_tokens = estimate_tokens(fixed_text)

    # 2. 主对话背景（从最老开始丢到预算内）
    background_limit = max(0, background_limit_tokens - fixed_tokens)
    background: list[ChatMessage] = []
    used = 0
    for message in reversed(session_store.list_chat_messages(anchor.session_id)):
        tokens = estimate_tokens(message.content or "")
        if used + tokens > background_limit:
            break
        background.insert(0, message)
        used += tokens

    sid = anchor.session_id
    messages: list[ChatMessage] = [ChatMessage(session_id=sid, role=ChatRole.SYSTEM, content=SYSTEM_PROMPT)]

    if background:
        bg_lines: list[str] = []
        for m in background:
            role_label = "用户" if m.role == ChatRole.USER else "助手"
            bg_lines.append(f"{role_label}: {m.content}")
        messages.append(ChatMessage(
            session_id=sid,
            role=ChatRole.USER,
            content="以下是主对话背景（只读参考）：\n" + "\n".join(bg_lines),
        ))
        messages.append(ChatMessage(
            session_id=sid,
            role=ChatRole.ASSISTANT,
            content="已了解主对话背景，请继续。",
        ))

    cite_parts = ["用户选中的内容如下：", f"「{anchor.quote}」"]
    if quoted_block:
        cite_parts += ["", "选中内容所在消息的完整原文：", quoted_block]
    messages.append(ChatMessage(session_id=sid, role=ChatRole.USER, content="\n".join(cite_parts)))
    messages.append(ChatMessage(session_id=sid, role=ChatRole.ASSISTANT, content="已定位选中内容，请提问。"))

    messages.extend(thread_messages)
    messages.append(ChatMessage(session_id=sid, role=ChatRole.USER, content=question))
    return messages
