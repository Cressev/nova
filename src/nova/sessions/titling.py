"""会话自动命名（dsh session-title 机制的 Nova 移植）。

dsh 语义（packages/session/session-title + session-title-llm）：
1. 首条用户消息落地时，同步生成确定性兜底标题（首条消息的前几个词，
   CJK 感知截断，清洗终端控制序列）；
2. 首轮对话完成后，用辅助 LLM 生成更好的标题（跟随消息语言，一行纯文本，
   失败静默保留兜底）；
3. 用户手动改名会"钉住"标题（source=user），此后自动生成不再覆盖。
标题来源记录在 ChatSession.title_source：default → fallback → llm，user 最高。
"""

from __future__ import annotations

import re
import unicodedata

# 终端转义序列（dsh normalize.ts 同款清单）：OSC（含未闭合尾部）、CSI、
# 两字节 ESC、C0/C1 控制字符、双向/不可见控制符——标题绝不能带这些。
_OSC_SEQUENCE = re.compile(r"(?:\x1b\]|\x9d)(?:(?!\x07|\x1b\\)[\s\S])*(?:\x07|\x1b\\|$)")
_CSI_SEQUENCE = re.compile(r"(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]")
_ESC_SEQUENCE = re.compile(r"\x1b[@-_]")
_CONTROL_CHARACTER = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_DIRECTIONAL_CONTROL = re.compile(r"[\u200b\u200e\u200f\u202a-\u202e\u2060-\u2064\u2066-\u206f\ufeff]")

# CJK 感知上限：中文标题取前 16 个汉字，英文取前 8 个词，统一 96 字节封顶。
_FALLBACK_MAX_CJK_CHARS = 16
_FALLBACK_MAX_WORDS = 8
_FALLBACK_MAX_BYTES = 96
_LLM_TIMEOUT_SECONDS = 4.0

# 会话创建时的占位标题：仍是这些值时视为"未命名"，允许自动命名接管。
DEFAULT_PLACEHOLDER_TITLES = frozenset({"新对话", "新线程", "新会话", ""})

# dsh session-title-llm 的语言感知指令（中文化改写）：跟随消息语言、
# 一行纯文本、不给解释不写代码。
_LLM_SYSTEM_PROMPT = (
    "根据提供的用户消息，为一个 AI 编码助手会话生成一个简洁的标题。"
    "只输出标题本身，一行纯文本：不要引号、前缀、解释、Markdown、XML 或终端控制码，不要代码。"
    "使用消息本身的语言（中文消息给中文标题，英文消息给英文标题）。"
    "中文标题约 16 个汉字以内；其他语言约 8 个词以内。"
)


def clean_title_text(raw: str) -> str:
    """清洗标题文本：去控制序列、折叠空白，返回单行 trimmed 文本。"""
    cleaned = (
        raw.replace("\x00", "")
    )
    cleaned = _OSC_SEQUENCE.sub("", cleaned)
    cleaned = _CSI_SEQUENCE.sub("", cleaned)
    cleaned = _ESC_SEQUENCE.sub("", cleaned)
    cleaned = _CONTROL_CHARACTER.sub("", cleaned)
    cleaned = _DIRECTIONAL_CONTROL.sub("", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _is_cjk(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x4E00 <= codepoint <= 0x9FFF
        or 0x3400 <= codepoint <= 0x4DBF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x20000 <= codepoint <= 0x2FA1F
    )


def truncate_utf8(text: str, max_bytes: int) -> str:
    """按 UTF-8 字节预算截断，不切断 Unicode 码点（dsh truncateTitleUtf8）。"""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore").rstrip()


def normalize_title(raw: str, max_bytes: int = _FALLBACK_MAX_BYTES) -> str:
    """规范化一个候选标题并施加字节预算；可能清洗后为空。"""
    return truncate_utf8(clean_title_text(raw), max_bytes).strip()


def fallback_title(raw: str) -> str:
    """确定性兜底标题：首条用户消息的前几个词（dsh fallbackSessionTitle）。

    CJK 占比高时按汉字数截断（16 字），否则按空白词数截断（8 词），
    最后统一 96 字节封顶。清洗后为空返回空串（调用方放弃命名）。
    """
    cleaned = clean_title_text(raw)
    if not cleaned:
        return ""
    characters = list(cleaned)
    cjk_count = sum(1 for ch in characters if _is_cjk(ch))
    if cjk_count * 2 >= len(characters):
        picked: list[str] = []
        for ch in characters:
            if _is_cjk(ch):
                picked.append(ch)
                if len(picked) >= _FALLBACK_MAX_CJK_CHARS:
                    break
            else:
                picked.append(ch)
                if len("".join(picked).encode("utf-8")) > _FALLBACK_MAX_BYTES:
                    picked.pop()
                    break
        candidate = "".join(picked)
    else:
        words = cleaned.split(" ")[:_FALLBACK_MAX_WORDS]
        candidate = " ".join(words)
    return truncate_utf8(candidate, _FALLBACK_MAX_BYTES).strip()


def is_placeholder_title(title: str) -> bool:
    """会话标题是否仍是占位（未命名）状态。"""
    return clean_title_text(title) in DEFAULT_PLACEHOLDER_TITLES


def llm_title_messages(user_texts: list[str]) -> list:
    """组装辅助 LLM 请求消息（系统指令 + JSON 框住的用户消息，dsh frameMessages）。"""
    import json

    from ..models import ChatMessage, ChatRole

    return [
        ChatMessage(
            session_id="title",
            role=ChatRole.SYSTEM,
            content=_LLM_SYSTEM_PROMPT,
        ),
        ChatMessage(
            session_id="title",
            role=ChatRole.USER,
            content="根据以下 JSON 数组中的用户消息生成会话标题：\n"
            + json.dumps([{"text": normalize_title(t, 4000)} for t in user_texts], ensure_ascii=False),
        ),
    ]


async def generate_llm_title(provider, user_texts: list[str]) -> str | None:
    """非阻塞辅助 LLM 标题生成；任何失败（超时/异常/空输出）返回 None。

    使用 asyncio.wait_for 施加 4s 上限；输出走 normalize_title 清洗，
    超过 48 字节（标题比兜底更短）或退化为占位词时放弃。
    """
    import asyncio

    if not user_texts:
        return None
    try:
        raw = await asyncio.wait_for(
            provider.complete(llm_title_messages(user_texts)),
            timeout=_LLM_TIMEOUT_SECONDS,
        )
    except Exception:
        return None
    if not isinstance(raw, str):
        return None
    # LLM 偶尔包引号/句号：引号与句读字符从两端反复剥除后再清洗
    # （“标题”。这类混排需要迭代到稳定）。
    stripped = raw.strip()
    while stripped and (stripped[0] in "\"'“”‘’«»„" or stripped[-1] in "\"'“”‘’«»„。.!?！？…"):
        stripped = stripped.strip("\"'“”‘’«»„。.!?！？…").strip()
    title = normalize_title(stripped, 48)
    if not title or title in DEFAULT_PLACEHOLDER_TITLES:
        return None
    if unicodedata.normalize("NFKC", title).lower() in {
        unicodedata.normalize("NFKC", item).lower() for item in DEFAULT_PLACEHOLDER_TITLES
    }:
        return None
    return title
