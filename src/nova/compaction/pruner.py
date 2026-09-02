"""确定性工具结果剪枝（照抄 dsh compaction-tool-result-pruner 的默认策略）。

dsh 的立场：剪枝是"不用模型、可重放"的便宜操作，先于昂贵的 LLM 压缩执行，
直接减少需要压缩的历史。超过 threshold 的工具输出保留头部与尾部，
中段替换为 PRUNE_MARKER，读者能看出内容被裁且首尾信息不丢。
"""

from __future__ import annotations

from dataclasses import dataclass

# 剪枝标记：中段被裁的可见痕迹（dsh 同款文案）。
PRUNE_MARKER = "\n\n[... tool result middle pruned ...]\n\n"


@dataclass(frozen=True)
class PrunerConfig:
    """dsh DEFAULTS 的 1:1 移植。"""

    threshold_chars: int = 8192
    head_chars: int = 4096
    tail_chars: int = 1024


DEFAULTS = PrunerConfig()


def prune_tool_result_text(text: str | None, config: PrunerConfig | None = None) -> str:
    """对单段工具输出做头尾保留剪枝；不超阈值时原样返回。"""
    cfg = config or DEFAULTS
    cleaned = text or ""
    if len(cleaned) <= cfg.threshold_chars:
        return cleaned
    head = cleaned[: cfg.head_chars]
    tail = cleaned[-cfg.tail_chars:] if cfg.tail_chars > 0 else ""
    return head + PRUNE_MARKER + tail


def prune_tool_results(texts: list[str], config: PrunerConfig | None = None) -> list[str]:
    """批量剪枝（进入模型上下文的工具结果列表）。"""
    return [prune_tool_result_text(text, config) for text in texts]
