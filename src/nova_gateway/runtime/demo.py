from __future__ import annotations

import asyncio
from pathlib import Path

from ..models import Task, TaskStatus, TimelineEvent
from ..sessions.store import TaskStore


class DemoAgentRuntime:
    """A replaceable runtime that makes the Web UI and trace loop usable first."""

    def __init__(self, store: TaskStore, project_root: Path) -> None:
        self.store = store
        self.project_root = project_root

    async def run(self, task: Task) -> None:
        try:
            self.store.update_task(task.id, status=TaskStatus.RUNNING)
            self._event(task, "run_started", "任务已启动", "Nova 正在读取项目上下文。")
            await asyncio.sleep(0.25)

            context_files = self._discover_context_files()
            self._event(
                task,
                "context_loaded",
                "上下文已加载",
                f"已找到 {len(context_files)} 个项目记忆文件。",
                {"files": [str(path.relative_to(self.project_root)) for path in context_files]},
            )
            await asyncio.sleep(0.25)

            self._event(
                task,
                "tool_call_started",
                "工具调用：项目扫描",
                "模拟读取项目结构，并准备后续接入真实工具注册表。",
                {"tool": "list_project"},
            )
            await asyncio.sleep(0.35)

            self._event(
                task,
                "tool_call_completed",
                "工具完成：项目扫描",
                "当前 MVP 已确认项目具备记忆文件，尚未接入真实模型 Provider。",
                {"tool": "list_project", "status": "ok"},
            )
            await asyncio.sleep(0.2)

            summary = (
                "已完成一次 Nova MVP 演示运行：创建任务、加载上下文、记录工具事件、"
                "写入本地 JSONL trace，并将过程展示到 Web UI。"
            )
            self.store.update_task(task.id, status=TaskStatus.COMPLETED, summary=summary)
            self._event(task, "run_completed", "任务完成", summary)
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            self.store.update_task(task.id, status=TaskStatus.FAILED, summary=str(exc))
            self._event(task, "run_failed", "任务失败", str(exc), status="error")

    def _discover_context_files(self) -> list[Path]:
        names = ["AGENTS.md", "CURRENT.md", "PROGRESS.md", "log.md", "user-queries.md"]
        return [self.project_root / name for name in names if (self.project_root / name).exists()]

    def _event(
        self,
        task: Task,
        event_type: str,
        title: str,
        message: str,
        data: dict | None = None,
        status: str = "ok",
    ) -> None:
        self.store.add_event(
            TimelineEvent(
                task_id=task.id,
                type=event_type,
                title=title,
                message=message,
                status=status,
                data=data or {},
            )
        )
