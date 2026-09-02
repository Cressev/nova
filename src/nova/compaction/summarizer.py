"""LLM 结构化 checkpoint 摘要（照抄 dsh compaction-basic/summarizer.ts）。

dsh 的两个关键设计一并移植：
1. 摘要指令作为"最后一条 user message"拼在原对话之后（而非独立 system
   prompt），复刻上一个真实请求的前缀 → provider 的 KV 前缀缓存可复用；
2. 8 段固定结构 checkpoint（Primary Request / Key Concepts / Files and
   Code / Errors and Fixes / Pending Jobs / Current Work / Next Step /
   Critical Context），要求保留精确路径、命令、错误串与数字，且遇到旧
   checkpoint 时合并而非照抄。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import ChatMessage, ChatRole

# checkpoint 内结构化摘要的包裹标签（dsh 同款）。
SUMMARY_OPEN_TAG = "<compacted-summary>"
SUMMARY_CLOSE_TAG = "</compacted-summary>"

# 摘要指令：作为回放对话之后的最后一条 user message 交付。
# 保留 dsh 原文（英文工程散文是刻意要求：路径/命令/错误串零损耗）。
COMPACTION_INSTRUCTION = "\n".join(
    [
        "You are now acting as a compaction engine for this AI coding assistant. Condense the conversation ABOVE into a structured checkpoint that lets another model resume the work with no loss of essential context.",
        "",
        "Output EXACTLY the Markdown structure below: keep every section, in order. Use terse bullets, not prose paragraphs. Write \"(none)\" for an empty section — never drop a section.",
        "",
        "## Primary Request and Intent",
        "- [the user's original and evolving goals; quote verbatim where the exact wording matters]",
        "",
        "## Key Technical Concepts",
        "- [technologies, frameworks, patterns, and conventions in play]",
        "",
        "## Files and Code",
        "- [exact path: why it matters, key changes or snippets]",
        "",
        "## Errors and Fixes",
        "- [error: how it was resolved, plus any related user feedback]",
        "",
        "## Pending Jobs",
        "- [explicitly requested work not yet completed]",
        "",
        "## Current Work",
        "- [precisely what was in progress at this checkpoint]",
        "",
        "## Next Step",
        "- [the single next action, directly in line with the most recent request, or \"(none)\"]",
        "",
        "## Critical Context",
        "- [decisions and their rationale, constraints, user preferences, open questions, data needed to continue]",
        "",
        "Rules:",
        "- Write concise English engineering prose. Preserve exact file paths, commands, error strings, identifiers, numeric values, function signatures, and syntax fragments.",
        "- Capture user feedback and explicit instructions faithfully, especially corrections.",
        "- Do NOT mention this summarization request or that the context was compacted.",
        "- Output only the checkpoint text: do not call any tool or take any other action.",
        f"- If the conversation already contains a {SUMMARY_OPEN_TAG} block, it is a PRIOR checkpoint. Do not copy it forward verbatim: preserve still-true facts, drop stale ones, and merge newer information into a single consolidated summary under the same structure.",
    ]
)

# 让替换用的 user message 成为既成上下文的框架语（dsh 原文）。
CHECKPOINT_PREAMBLE = (
    "This is an automatically generated checkpoint condensing an earlier span of the "
    "conversation to free up context. Treat the captured context as established "
    "background and build on it without restating it. Continue the task directly "
    "from the messages that follow, without acknowledging this checkpoint."
)


@dataclass(frozen=True)
class SummaryResult:
    """一次摘要生成的产物与调用信封（对应 dsh SummaryResult）。"""

    summary: str
    provider: str
    model: str
    raw_output: str


async def summarize_surface(
    *,
    provider: object,
    system_prompt: str | None,
    messages: list[ChatMessage],
    extra_instruction: str = "",
) -> SummaryResult:
    """调用一次 LLM 把 surface 区域浓缩为 checkpoint 文本。

    请求形状与 dsh 完全一致：[原 system prompt, *被压缩消息, 摘要指令(user)]。
    system prompt 与消息前缀逐字复刻上一真实请求，provider 侧前缀缓存命中。
    用户在 /compact 后附带的额外要求会作为补充段落接在基础指令之后。
    """
    instruction = COMPACTION_INSTRUCTION
    if extra_instruction.strip():
        instruction += (
            "\n\nAdditional user instruction for this compaction: "
            + extra_instruction.strip()
        )

    payload: list[ChatMessage] = []
    if system_prompt:
        payload.append(
            ChatMessage(session_id="agent", role=ChatRole.SYSTEM, content=system_prompt)
        )
    payload.extend(messages)
    payload.append(
        ChatMessage(session_id="agent", role=ChatRole.USER, content=instruction)
    )

    complete = getattr(provider, "complete", None)
    if not callable(complete):
        raise TypeError("compaction summarizer 需要 provider.complete(messages)")
    raw = await complete(payload)
    text = str(raw or "").strip()
    if not text:
        raise ValueError("摘要模型返回空内容")
    return SummaryResult(
        summary=text,
        provider=str(getattr(provider, "provider_name", "bigmodel")),
        model=str(getattr(provider, "model", "glm-4.7")),
        raw_output=text,
    )


def build_checkpoint_content(summary: str) -> str:
    """checkpoint 消息正文 = 框架语 + 标签包裹的结构化摘要。"""

    return f"{CHECKPOINT_PREAMBLE}\n\n{SUMMARY_OPEN_TAG}\n{summary.strip()}\n{SUMMARY_CLOSE_TAG}"
