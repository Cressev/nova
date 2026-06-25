from __future__ import annotations

from dataclasses import dataclass

from ..config.settings import Settings
from ..processes.manager import ProcessManager
from ..providers.bigmodel import BigModelProvider
from ..sessions import AgentSessionService, SessionStore
from ..subagents import SubAgentManager
from ..workspace import WorkspaceManager


@dataclass
class NovaCore:
    """Nova 后端核心对象容器。

    FastAPI 路由只应该编排请求和响应；会话存储、工作区、模型 Provider、
    进程管理这类长生命周期对象统一从这里创建，避免继续散落在 `main.py`
    顶层。当前先保持现有行为不变，后续再逐步把路由迁移到 `app.state.core`。
    """

    settings: Settings
    store: SessionStore
    workspace_manager: WorkspaceManager
    provider: BigModelProvider
    process_manager: ProcessManager
    subagent_manager: SubAgentManager
    agent_sessions: AgentSessionService

    @classmethod
    def from_settings(cls, settings: Settings) -> "NovaCore":
        store = SessionStore(settings.state_dir)
        workspace_manager = WorkspaceManager(
            initial_root=settings.initial_workspace_root,
            allowed_roots=settings.allowed_workspace_roots,
            recent_file=settings.state_dir / "workspace-recents.json",
        )
        provider = BigModelProvider(
            base_url=settings.provider_base_url,
            model=settings.provider_model,
            api_key_file=settings.runtime_secret_file,
        )
        process_manager = ProcessManager()
        return cls(
            settings=settings,
            store=store,
            workspace_manager=workspace_manager,
            provider=provider,
            process_manager=process_manager,
            subagent_manager=SubAgentManager(settings.project_root),
            agent_sessions=AgentSessionService(),
        )
