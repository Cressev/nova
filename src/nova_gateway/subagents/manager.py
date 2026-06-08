from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nova_gateway.models import new_id, utc_now

SubAgentRunner = Callable[["SubAgentRun"], str]


@dataclass
class SubAgentRun:
    id: str
    name: str
    prompt: str
    workspace: str
    status: str = "running"
    result: str | None = None
    error: str | None = None
    cancel_requested: bool = False
    created_at: str = field(default_factory=lambda: utc_now().isoformat())
    updated_at: str = field(default_factory=lambda: utc_now().isoformat())
    completed_at: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    def add_event(self, event_type: str, title: str, message: str | None = None, **data: Any) -> None:
        self.events.append(
            {
                "type": event_type,
                "title": title,
                "message": message,
                "data": data,
                "created_at": utc_now().isoformat(),
            }
        )
        self.updated_at = utc_now().isoformat()

    def as_dict(self, *, include_events: bool = True) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "name": self.name,
            "prompt": self.prompt,
            "workspace": self.workspace,
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "cancel_requested": self.cancel_requested,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
        }
        if include_events:
            payload["events"] = list(self.events)
        return payload


class SubAgentManager:
    """管理最小子 Agent 生命周期：spawn、status、wait、close。"""

    def __init__(self, project_root: Path, default_runner: SubAgentRunner | None = None) -> None:
        self.project_root = project_root.resolve()
        self.default_runner = default_runner or self._local_summary_runner
        self._runs: dict[str, SubAgentRun] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)

    def spawn(
        self,
        *,
        prompt: str,
        name: str | None = None,
        project_root: Path | None = None,
        runner: SubAgentRunner | None = None,
    ) -> dict[str, Any]:
        root = (project_root or self.project_root).resolve()
        run = SubAgentRun(
            id=new_id("subagent"),
            name=(name or "worker").strip()[:80] or "worker",
            prompt=prompt.strip(),
            workspace=str(root),
        )
        run.add_event("spawned", "子 Agent 已创建", f"{run.name} 开始处理任务。")
        with self._condition:
            self._runs[run.id] = run
            created_payload = run.as_dict()
        thread = threading.Thread(target=self._run, args=(run.id, runner or self.default_runner), daemon=True)
        with self._condition:
            self._threads[run.id] = thread
        thread.start()
        return created_payload

    def list(self) -> list[dict[str, Any]]:
        with self._condition:
            runs = list(self._runs.values())
        return [run.as_dict(include_events=False) for run in sorted(runs, key=lambda item: item.created_at, reverse=True)]

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._condition:
            run = self._runs.get(run_id)
            return run.as_dict() if run else None

    def wait(self, run_id: str, *, timeout_ms: int = 1000) -> dict[str, Any] | None:
        deadline = max(timeout_ms, 1) / 1000
        with self._condition:
            run = self._runs.get(run_id)
            if run is None:
                return None
            if run.status not in {"completed", "failed", "cancelled", "closed"}:
                self._condition.wait_for(
                    lambda: run.status in {"completed", "failed", "cancelled", "closed"},
                    timeout=deadline,
                )
            return run.as_dict()

    def close(self, run_id: str) -> dict[str, Any] | None:
        with self._condition:
            run = self._runs.get(run_id)
            if run is None:
                return None
            run.cancel_requested = True
            if run.status in {"completed", "failed"}:
                run.status = "closed"
                run.add_event("closed", "子 Agent 已关闭", "结果已保留，任务行已关闭。")
            elif run.status == "running":
                run.status = "cancelled"
                run.completed_at = utc_now().isoformat()
                run.add_event("cancelled", "子 Agent 已请求取消", "正在等待子 Agent 协作退出。")
            run.updated_at = utc_now().isoformat()
            self._condition.notify_all()
            return run.as_dict()

    def _run(self, run_id: str, runner: SubAgentRunner) -> None:
        with self._condition:
            run = self._runs[run_id]
        try:
            run.add_event("running", "子 Agent 运行中", "开始执行受委派任务。")
            result = runner(run)
            with self._condition:
                if run.status == "cancelled" or run.cancel_requested:
                    run.result = result
                    run.completed_at = utc_now().isoformat()
                    run.updated_at = utc_now().isoformat()
                else:
                    run.status = "completed"
                    run.result = result
                    run.completed_at = utc_now().isoformat()
                    run.add_event("completed", "子 Agent 已完成", "结果已可读取。")
                self._condition.notify_all()
        except Exception as exc:  # noqa: BLE001 - 子线程边界必须兜底。
            with self._condition:
                run.status = "failed"
                run.error = f"{type(exc).__name__}: {exc}"
                run.completed_at = utc_now().isoformat()
                run.add_event("failed", "子 Agent 失败", run.error)
                self._condition.notify_all()

    def _local_summary_runner(self, run: SubAgentRun) -> str:
        root = Path(run.workspace)
        files = []
        for child in sorted(root.iterdir())[:8] if root.exists() else []:
            files.append(child.name + ("/" if child.is_dir() else ""))
        return (
            f"Scope: {run.prompt[:160]}\n"
            "Result: 子 Agent 已在本地只读模式完成任务登记；模型不可用时使用该兜底摘要。\n"
            f"Key files: {', '.join(files) if files else '暂无'}"
        )
