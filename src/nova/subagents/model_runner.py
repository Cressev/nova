"""模型子代理 runner 工厂（从 routes._subagent_runner 抽出，支持参数化）。

provider/schema 可覆盖：workflow 脚本编排里 agent(prompt, {provider, model,
schema}) 需要为单个子代理换提供方或强制 JSON 结果。默认路径（routes 全局
patch）行为与旧 _subagent_runner 完全一致：受限只读上下文 + Scope/Result
格式 + 失败降级本地摘要。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Callable

from ..models import ChatMessage, ChatRole
from ..providers.bigmodel import BigModelProvider, ProviderError
from ..runtime.agent import CodexLikeAgentRuntime


def build_subagent_runner(
    *,
    provider: BigModelProvider,
    global_agent_file: Path | str | None = None,
    max_tool_rounds: int = 4,
    langfuse_factory: Callable[[str], Any] | None = None,
    fallback_summary: bool = True,
    fallback_summary_fn: Callable[[Any], str] | None = None,
    schema: dict[str, Any] | None = None,
    timeout_s: float = 100.0,
) -> Callable[[Any], str]:
    """构造 SubAgentRunner。

    schema 非 None 时：提示词改为“只输出符合 schema 的 JSON”，成功时返回值
    仍是文本（JSON 字符串），由调用方 parse+校验——runner 不吞掉校验失败，
    保持“子代理输出原文”语义，错误在桥层显式化。
    """

    def runner(run: Any) -> str:
        async def collect() -> str:
            recorder = langfuse_factory(str(run.workspace)) if langfuse_factory else None
            runtime = CodexLikeAgentRuntime(
                provider=provider,
                project_root=Path(run.workspace),
                global_agent_file=global_agent_file,
                max_tool_rounds=max_tool_rounds,
                permission_mode="read_only",
                sandbox_mode="read_only",
                approval_policy="never",
                network_access=False,
                trace_recorder=recorder,
            )
            if schema is not None:
                prompt = (
                    f"{run.prompt}\n\n"
                    "输出要求：只输出一个符合以下 JSON Schema 的 JSON 对象；"
                    "不要 markdown 代码块、不要任何其他文字：\n"
                    + json.dumps(schema, ensure_ascii=False)
                )
            else:
                prompt = (
                    "你是 Nova 的子 Agent。只处理下面委派给你的范围，不要再 spawn 子 Agent。"
                    "先用只读方式核对事实，最后必须用以下格式回答：\n"
                    "Scope: <你的任务范围>\nResult: <结论>\nKey files: <相关文件>\nIssues: <需要主 Agent 知道的问题>\n\n"
                    f"委派任务：{run.prompt}"
                )
            content = ""
            messages = [ChatMessage(session_id=run.id, role=ChatRole.USER, content=prompt)]
            async for event in runtime.stream(messages):
                if run.cancel_requested:
                    return content or "Scope: 已取消\nResult: 子 Agent 收到取消请求。"
                if event.get("type") == "agent_status":
                    run.add_event("status", str(event.get("status") or "子 Agent 状态"))
                if event.get("type") == "assistant_done_content":
                    content = str(event.get("content") or "")
            return content or "Scope: 子 Agent\nResult: 未收到模型最终回答。"

        try:
            return asyncio.run(asyncio.wait_for(collect(), timeout_s))
        except (
            asyncio.TimeoutError,
            ProviderError,
            RuntimeError,
            OSError,
            ValueError,
        ) as exc:
            if not fallback_summary:
                raise
            run.add_event("fallback", "子 Agent 使用本地兜底", f"{type(exc).__name__}: {exc}")
            if fallback_summary_fn is not None:
                return fallback_summary_fn(run)
            return (
                f"Scope: {run.prompt[:160]}\n"
                "Result: 子 Agent 执行失败（模型或超时），无可靠结果。\n"
            )

    # asyncio.TimeoutError 不能放进元组字面量别名——展开写清楚：
    return runner
