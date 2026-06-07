# OpenCode / Crush 源码参考结论

## 背景

用户要求把 opcode 源码下载下来，便于后续参考。这里按上下文判断，实际目标是 OpenCode / opencode。

本次没有把参考源码提交进 Nova 仓库。源码放在 `references/upstream/` 下，该目录已经被 `.gitignore` 忽略，只作为本地阅读材料。

## 已下载源码

- OpenCode：`references/upstream/opencode`
  - 远程仓库：`https://github.com/opencode-ai/opencode.git`
  - 当前提交：`73ee493`
  - 当前状态：README 标明项目已归档，后续开发迁移到 Crush。
- Crush：`references/upstream/crush`
  - 远程仓库：`https://github.com/charmbracelet/crush.git`
  - 当前提交：`2030d97`
  - 当前状态：OpenCode 的后续维护项目，后续应优先参考。

## 优先参考顺序

后续做 Nova 架构和功能时，优先读 Crush，再读 OpenCode。

OpenCode 更适合看早期产品形态和配置方式：多 provider、会话管理、工具调用、SQLite 持久化、LSP、文件变更追踪、自动压缩、外部编辑器和自定义命令。

Crush 更适合看当前工程化架构：`internal/app` 做顶层组装，`internal/agent` 管会话 agent，`internal/agent/tools` 放内置工具，`internal/hooks` 独立处理 hook，`internal/session` 和 `internal/message` 做持久化模型，`internal/shell` 处理后台任务，`internal/permission` 处理权限，`internal/pubsub` 串联 UI 和运行时事件。

## 对 Nova 的直接启发

Nova 现在最需要借鉴 Crush 的不是 Go 语言实现，而是模块边界。

Nova 的 Python 后端后续应拆成这些稳定边界：配置服务、AgentSession、工具注册表、工具执行器、权限审批、Hook 引擎、进程管理、会话和消息持久化、事件总线、记忆和技能加载。这样 Web UI 才能像 Codex / CC / Crush 一样显示真实状态，而不是靠前端猜测。

Crush 的另一个重要启发是工具自描述：每个工具除了实现，还要有独立描述、参数 schema、权限级别和失败兜底说明。Nova 后续的工具面板、审批卡片、工具调用详情和 `/tools` 指令都应该直接读取这份工具元数据。

## 后续阅读入口

- `references/upstream/crush/AGENTS.md`：架构总览，优先阅读。
- `references/upstream/crush/internal/agent/`：Agent 会话和提示词模板。
- `references/upstream/crush/internal/agent/tools/`：内置工具定义和工具描述。
- `references/upstream/crush/internal/hooks/`：Hook 机制和工具调用前置决策。
- `references/upstream/crush/internal/permission/`：权限判断和 allow-list。
- `references/upstream/crush/internal/shell/`：Shell 执行和后台任务。
- `references/upstream/crush/internal/session/`、`references/upstream/crush/internal/message/`：会话与消息持久化。
