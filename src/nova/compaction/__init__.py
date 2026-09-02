"""上下文压缩能力族（对照 dsh compaction 家族移植）。

模块划分与 dsh 一一对应：
- estimate.py   ← dsh-token-meter/estimate.ts：固定密度 token 计价 + 结构开销
- pruner.py     ← compaction-tool-result-pruner：超大工具输出的确定性头尾剪枝
- summarizer.py ← compaction-basic/summarizer.ts：LLM 结构化 checkpoint 摘要
- engine.py     ← compaction + compaction-basic：锁事件、surface 投影、阈值与保留策略

核心语义（照抄 dsh）：压缩 = 遮蔽（shadow）而非删除——
store 里的消息永远全量保留，压缩只改变"模型看到的表层（surface）"：
被遮蔽的历史由一条 checkpoint 消息替换，摘要因此真正进入模型请求。
"""

from .engine import (
    CompactionConfig,
    CompactionEngine,
    CompactionResult,
    is_context_overflow_error,
)
from .estimate import ROLE_OVERHEAD, estimate_message_tokens, estimate_tokens
from .pruner import PRUNE_MARKER, prune_tool_result_text
from .summarizer import CHECKPOINT_PREAMBLE, COMPACTION_INSTRUCTION, summarize_surface

__all__ = [
    "CHECKPOINT_PREAMBLE",
    "COMPACTION_INSTRUCTION",
    "CompactionConfig",
    "CompactionEngine",
    "CompactionResult",
    "PRUNE_MARKER",
    "ROLE_OVERHEAD",
    "estimate_message_tokens",
    "estimate_tokens",
    "is_context_overflow_error",
    "prune_tool_result_text",
    "summarize_surface",
]
