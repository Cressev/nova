"""压缩引擎（照抄 dsh compaction + compaction-basic 的执行语义）。

对应关系：
- CompactionConfig   ← compaction-basic/config.ts：threshold_ratio 0.8 / retain_ratio 0.16
- CompactionResult   ← compaction/types.ts：CompactionResult
- compact_if_needed  ← compactIfNeeded(trigger)：pressure / context-overflow 两类触发
- compact_now        ← compactNow：空闲会话的显式压缩（/compact）
- surface 投影       ← surfaceOp replace：被遮蔽消息由 checkpoint 消息替换后进入模型请求
- 锁                 ← compaction/start + compaction/end 事件对；活动锁阻塞所有入口

与 dsh 的一处实现差异（语义等价）：dsh 的锁权威在事件日志（跨进程崩溃可检测），
Nova 的 store 是单进程内存 + 文件持久化，锁权威取进程内 asyncio.Lock，
start/end 事件作为持久审计与 UI 追踪；孤儿事件不会卡死会话。
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..models import ChatEvent, ChatMessage, ChatRole, new_id
from .estimate import estimate_messages_tokens, estimate_tokens
from .summarizer import build_checkpoint_content, summarize_surface

# 事件记录器：直接复用 RunOrchestrator.event 的签名。
EventRecorder = Callable[..., dict[str, Any]]

# 自动触发原因（dsh CompactionTrigger）。
CompactionTrigger = str  # "pressure" | "context-overflow"


@dataclass(frozen=True)
class CompactionConfig:
    """压缩策略（数值照抄 compaction-basic DEFAULT）。"""

    threshold_ratio: float = 0.8
    retain_ratio: float = 0.16


@dataclass(frozen=True)
class CompactionResult:
    """一次成功压缩的结果（对应 dsh CompactionResult）。"""

    compaction_id: str
    trigger: CompactionTrigger
    checkpoint_message_id: str
    summary: str
    shadowed_message_ids: list[str] = field(default_factory=list)
    shadowed_token_count: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)


def is_context_overflow_error(exc: BaseException) -> bool:
    """识别 provider 报告的上下文超长错误（context-overflow 触发依据）。"""
    text = str(exc).lower()
    markers = (
        "context length",
        "context window",
        "maximum context",
        "token limit",
        "too long",
        "too many tokens",
        "reduce the length",
        "exceeds the maximum",
        "上下文",
        "超过.*长度",
        "输入过长",
    )
    return any(marker in text for marker in markers)


class CompactionEngine:
    """会话压缩引擎：遮蔽而非删除，摘要回流模型请求。"""

    def __init__(
        self,
        *,
        store: Any,
        provider: Any,
        system_prompt_provider: Callable[[], str],
        context_window_tokens: int,
        config: CompactionConfig | None = None,
    ) -> None:
        self.store = store
        self.provider = provider
        self.system_prompt_provider = system_prompt_provider
        self.context_window_tokens = context_window_tokens
        self.config = config or CompactionConfig()
        self._lock = asyncio.Lock()

    # ---------- 计量 ----------

    def threshold_tokens(self) -> int:
        """自动压缩触发线：window * 0.8（dsh thresholdRatio）。"""
        return math.floor(self.context_window_tokens * self.config.threshold_ratio)

    def retain_tokens(self) -> int:
        """压缩后必须保留的尾部预算：window * 0.16（dsh retainRatio）。"""
        return math.floor(self.context_window_tokens * self.config.retain_ratio)

    # ---------- surface 投影 ----------

    def _summary_events(self, session_id: str) -> list[ChatEvent]:
        return [
            event
            for event in self.store.list_chat_events(session_id)
            if event.event_type == "compaction.summary"
        ]

    def shadowed_message_ids(self, session_id: str) -> set[str]:
        """所有已被 checkpoint 遮蔽的消息 id（历次压缩的并集）。"""
        shadowed: set[str] = set()
        for event in self._summary_events(session_id):
            ids = event.data.get("shadowed_message_ids") or []
            shadowed.update(str(item) for item in ids)
        return shadowed

    def surface_messages(self, session_id: str) -> list[ChatMessage]:
        """模型可见表层：全量消息剔除被遮蔽者。

        checkpoint 消息按 dsh surfaceOp replace 语义放在被遮蔽区域的
        位置（前缀处），而非存储追加序的末尾——模型读到的顺序是
        [checkpoint, 保留尾部, ...]。
        """
        shadowed = self.shadowed_message_ids(session_id)
        checkpoints: list[ChatMessage] = []
        live: list[ChatMessage] = []
        for message in self.store.list_chat_messages(session_id):
            if message.id in shadowed:
                continue
            if message.id.startswith("comp_"):
                checkpoints.append(message)
            else:
                live.append(message)
        return [*checkpoints, *live]

    def measure_surface_tokens(self, session_id: str) -> int:
        """surface 压力 = 系统提示 + 全部表层消息（dsh 按整个路由请求计价）。"""
        surface = self.surface_messages(session_id)
        system_tokens = estimate_tokens(self.system_prompt_provider())
        return estimate_messages_tokens(surface) + system_tokens

    # ---------- 锁 ----------

    def has_active_lock(self) -> bool:
        """进程内锁状态（活动压缩期间阻塞所有入口，dsh 同义）。"""
        return self._lock.locked()

    # ---------- 区域选择 ----------

    def _select_region(
        self, surface: list[ChatMessage]
    ) -> tuple[list[ChatMessage], list[ChatMessage]] | None:
        """选出 [被压缩区, 保留尾部]；无效时返回 None。

        - 尾部从最新消息向前累计 retain_tokens；
        - 配对边界（dsh toolPairing 的消息级对应物）：区域不得结束在 user
          消息上，否则其 assistant 回答会成为孤儿——边界回退到成对完整处。
        """
        retain_budget = self.retain_tokens()
        tail: list[ChatMessage] = []
        used = 0
        for message in reversed(surface):
            cost = estimate_tokens(message.content) + 4
            if tail and used + cost > retain_budget:
                break
            tail.append(message)
            used += cost
        tail.reverse()
        cut = len(surface) - len(tail)
        while cut > 0 and surface[cut - 1].role == ChatRole.USER:
            cut -= 1
        if cut <= 0:
            return None
        return surface[:cut], surface[cut:]

    # ---------- 入口 ----------

    async def compact_if_needed(
        self,
        session_id: str,
        *,
        trigger: CompactionTrigger,
        recorder: EventRecorder,
        events_out: list[dict[str, Any]] | None = None,
        instruction: str = "",
    ) -> CompactionResult | None:
        """pressure：达到阈值才压；context-overflow：溢出必压（dsh 同义）。"""
        if trigger == "pressure":
            used = self.measure_surface_tokens(session_id)
            if used < self.threshold_tokens():
                return None
        return await self._compact(
            session_id,
            trigger=trigger,
            recorder=recorder,
            events_out=events_out,
            instruction=instruction,
        )

    async def compact_now(
        self,
        session_id: str,
        *,
        recorder: EventRecorder,
        events_out: list[dict[str, Any]] | None = None,
        instruction: str = "",
    ) -> CompactionResult | None:
        """空闲会话的显式压缩（/compact）；无有效区域时静默返回 None。"""
        return await self._compact(
            session_id,
            trigger="manual",
            recorder=recorder,
            events_out=events_out,
            instruction=instruction,
        )

    async def _compact(
        self,
        session_id: str,
        *,
        trigger: CompactionTrigger,
        recorder: EventRecorder,
        events_out: list[dict[str, Any]] | None = None,
        instruction: str = "",
    ) -> CompactionResult | None:
        out = events_out if events_out is not None else []
        if self._lock.locked():
            # 活动锁阻塞所有入口（dsh assertNoActiveCompaction）。
            event = recorder(
                "compaction.busy",
                category="compaction",
                phase="skipped",
                status="pending",
                title="压缩被活动锁阻塞",
                message="已有压缩正在进行，本次请求跳过。",
                data={"trigger": trigger},
            )
            out.append(event)
            return None

        async with self._lock:
            compaction_id = new_id("compaction")
            start = recorder(
                "compaction.start",
                category="compaction",
                phase="started",
                title="上下文压缩开始",
                message=f"触发原因：{trigger}",
                data={"compaction_id": compaction_id, "trigger": trigger},
            )
            out.append(start)
            try:
                surface = self.surface_messages(session_id)
                selection = self._select_region(surface)
                if selection is None:
                    end = recorder(
                        "compaction.end",
                        category="compaction",
                        phase="completed",
                        title="上下文压缩结束",
                        message="没有可压缩的有效区域，surface 保持不变。",
                        data={
                            "compaction_id": compaction_id,
                            "trigger": trigger,
                            "error": "empty_region",
                        },
                    )
                    out.append(end)
                    return None
                region, _tail = selection

                summary_result = await summarize_surface(
                    provider=self.provider,
                    system_prompt=self.system_prompt_provider(),
                    messages=region,
                    extra_instruction=instruction,
                )

                checkpoint = ChatMessage(
                    id=new_id("comp"),
                    session_id=session_id,
                    role=ChatRole.USER,
                    content=build_checkpoint_content(summary_result.summary),
                )
                self.store.add_chat_message(checkpoint)

                shadowed_ids = [message.id for message in region]
                shadowed_tokens = estimate_messages_tokens(region)
                summary_event = recorder(
                    "compaction.summary",
                    category="compaction",
                    phase="update",
                    title="上下文已压缩为检查点",
                    message=(
                        f"已将 {len(region)} 条较早历史（约 {shadowed_tokens} tokens）"
                        "浓缩为 checkpoint，模型上下文由摘要继续承接。"
                    ),
                    data={
                        "compaction_id": compaction_id,
                        "trigger": trigger,
                        "checkpoint_message_id": checkpoint.id,
                        "shadowed_message_ids": shadowed_ids,
                        "shadowed_token_count": shadowed_tokens,
                        "provider": summary_result.provider,
                        "model": summary_result.model,
                        "summary": summary_result.summary,
                    },
                )
                out.append(summary_event)
                end = recorder(
                    "compaction.end",
                    category="compaction",
                    phase="completed",
                    title="上下文压缩结束",
                    message="压缩完成，锁已释放。",
                    data={"compaction_id": compaction_id, "trigger": trigger},
                )
                out.append(end)
                return CompactionResult(
                    compaction_id=compaction_id,
                    trigger=trigger,
                    checkpoint_message_id=checkpoint.id,
                    summary=summary_result.summary,
                    shadowed_message_ids=shadowed_ids,
                    shadowed_token_count=shadowed_tokens,
                    events=list(out),
                )
            except Exception as exc:  # 摘要失败：surface 不变，闭合尝试并持久化（dsh 同义）
                end = recorder(
                    "compaction.end",
                    category="compaction",
                    phase="failed",
                    status="failed",
                    title="上下文压缩失败",
                    message=f"{type(exc).__name__}: {exc}",
                    data={
                        "compaction_id": compaction_id,
                        "trigger": trigger,
                        "error": str(exc),
                    },
                )
                out.append(end)
                return None
