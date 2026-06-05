# cc源码参考结论

## 背景

用户指定 `references/upstream/src` 后续简称为“cc源码”。Nova 后续设计和实现要同时参考 cc源码，不只参考 OpenAI Codex 源码。

本次只做结构和关键模块预研，不复制源码实现；后续实现应按 Nova 的 Python/FastAPI/Web-first 架构重新设计。

## 已查看的关键入口

- `references/upstream/src/QueryEngine.ts`
- `references/upstream/src/query.ts`
- `references/upstream/src/Tool.ts`
- `references/upstream/src/services/tools/StreamingToolExecutor.ts`
- `references/upstream/src/services/tools/toolExecution.ts`
- `references/upstream/src/components/StatusLine.tsx`
- `references/upstream/src/skills/loadSkillsDir.ts`
- `references/upstream/src/skills/bundled/remember.ts`
- `references/upstream/src/services/mcp/MCPConnectionManager.tsx`

## 可借鉴设计

### 1. 会话引擎独立于 UI

cc源码把 `QueryEngine` 作为单个会话的生命周期持有者，持久保存消息、usage、读文件缓存、权限拒绝、技能发现、嵌套记忆等状态。Nova 当前 runtime 仍偏请求驱动，后续应抽象 `AgentSession` / `QueryEngine`：

- 一个会话对象跨多轮对话持续存在。
- UI、HTTP API、未来 CLI 只负责输入输出，不直接拥有 agent 状态。
- 会话级状态包括消息、工具上下文、权限、文件读缓存、token 用量、trace、工作区、模型配置。

### 2. 主循环要内置上下文预算和恢复

cc源码的 `query.ts` 不只是“模型输出后执行工具”，还包含自动 compact、reactive compact、max output tokens 恢复、stop hook、token budget 和工具结果摘要。Nova 后续不能只靠最大工具轮次兜底，应增加：

- 当前上下文窗口估算和真实 usage 记录。
- 自动压缩触发点。
- 超长输出/超长上下文的恢复路径。
- 工具结果摘要，避免大型 stdout 或搜索结果撑爆上下文。

### 3. 工具并行应由执行器统一调度

cc源码的 `StreamingToolExecutor` 明确区分并发安全工具和独占工具：

- 并发安全工具可以并行。
- 非并发工具必须独占执行。
- 结果按工具到达顺序回填。
- Bash 类错误可以取消兄弟工具。
- 用户中断、流式 fallback、兄弟工具失败会生成合成错误结果。

Nova 当前已有工具元数据和初版并行，但还需要统一工具执行器，避免并行、取消、异常和展示状态散落在 runtime 里。

### 4. ToolUseContext 是后端体感底座

cc源码的 `ToolUseContext` 集中携带模型、工具、命令、MCP、权限、AppState、AbortController、文件缓存、技能发现、记忆触发、进度回调等上下文。Nova 后续也应建立统一 `ToolUseContext`，让每个工具都能拿到一致的：

- 工作区和允许目录。
- 权限上下文。
- trace/span 上报器。
- abort/cancel 信号。
- 会话消息和文件缓存。
- MCP/Skill/Memory 上下文。

### 5. 权限模型需要规则源和模式分层

cc源码权限上下文不仅有 mode，还区分 allow/deny/ask 规则、规则来源、额外工作目录、后台 agent 是否避免弹窗等。Nova 后续权限要从简单 `read_only/ask/workspace_write` 进化为：

- 会话临时授权。
- 用户全局授权。
- 项目级授权。
- 明确拒绝规则。
- shell、文件写入、网络、MCP、跨目录访问的独立规则。
- 后台任务无法弹窗时的默认策略。

### 6. 状态线应是结构化数据，不是硬编码标签

cc源码状态线输入包含 model、workspace、cost、context window、session、rate limit、vim mode、agent、remote、worktree，并支持自定义命令渲染和 debounce。Nova 现有 statusline 可以继续沿这个方向升级：

- 后端输出稳定结构化状态。
- 前端默认渲染紧凑状态线。
- 未来允许用户配置显示项或渲染脚本。
- 状态线要显示会话、工作区、权限、模型、上下文、成本/耗时、工作树和远端状态。

### 7. Skills 要有 frontmatter 能力边界

cc源码的 Skill loader 支持 `allowed-tools`、`paths`、`hooks`、`model`、`effort`、`user-invocable`、`context=fork`、参数替换和基于真实路径去重。Nova 的 Skills 不应只是 prompt 片段，后续应支持：

- Skill 自身目录和资源路径。
- 允许工具白名单。
- 适用路径。
- 可见/隐藏。
- 指定模型或 effort。
- hooks。
- fork/inline 执行上下文。
- 按 canonical path 去重。

### 8. 记忆应先审查再写入

cc源码的 `remember` 思路是先读取各层记忆、分类、找重复/冲突/过时内容，再提出变更建议，不能直接修改。Nova 后续记忆功能应避免盲目写 `AGENTS.md`：

- 区分给开发 Agent 的全局记忆、项目记忆、本地个人偏好、Nova 外层开发过程文件。
- 写入前给出 promotion/cleanup/ambiguous/no-action 报告。
- 需要用户审批后再落盘。

### 9. MCP 要有连接管理器

cc源码把 MCP reconnect/toggle 做成集中管理能力，并让连接变更能刷新 tools、commands、resources。Nova 后续接 MCP 时应有 `MCPManager`：

- 统一读取配置。
- 启停/重连 server。
- 动态刷新工具和资源。
- UI 显示连接状态、错误和可用工具。

### 10. 交互能力不只靠页面，需要 runtime 事件模型

cc源码里工具进度、状态线、权限、任务、远端 session、worktree、slash command、model picker 都围绕运行时状态变化工作。Nova Web UI 要更像 Codex/Claude Code，关键不是堆更多面板，而是让后端持续发出可解释事件：

- `agent_status`
- `tool_queued`
- `tool_started`
- `tool_progress`
- `tool_done`
- `permission_requested`
- `permission_resolved`
- `context_compacting`
- `context_compacted`
- `session_restored`
- `worktree_changed`
- `mcp_changed`

## 对 Nova 的优先级建议

1. 重构 `AgentSession/QueryEngine`，把会话状态从单次请求中抽离出来。
2. 实现统一工具执行器，支持队列、并行安全、独占、取消、进度和有序回填。
3. 扩展权限规则模型，补前端审批卡片和持久授权。
4. 实现工具事件持久化，刷新后能展开查看完整工具详情。
5. 做真实工作树创建/切换/清理，并纳入 statusline。
6. 加入上下文预算、工具结果摘要和第一版 compact。
7. 设计 Nova Skills frontmatter 和 loader。
8. 增加记忆审查工作流，不直接盲写记忆文件。
9. 建立 MCPManager，并在右侧面板展示连接与工具状态。
10. 把 statusline 升级为结构化后端状态 + 可配置前端渲染。
