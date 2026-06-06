# 参考源码架构阅读结论

## 背景

用户要求开发前继续阅读参考代码，尤其关注大型工程如何构建，再修改 Nova 的下一步开发计划和长期建议。

本次阅读不以复制实现为目标，而是提炼 Nova 后续必须补上的工程边界：事件协议、会话引擎、工具执行、权限、上下文、扩展和测试。

## 已阅读的关键材料

- OpenAI Codex：`sdk/typescript/src/events.ts`、`sdk/typescript/src/thread.ts`、`codex-rs/core/src/session/turn.rs`、`tools/mod.rs`、`exec_policy.rs`、`skills.rs`、`thread_manager.rs`。
- cc源码：`QueryEngine.ts`、`query.ts`、`Tool.ts`、`services/tools/StreamingToolExecutor.ts`、`skills/loadSkillsDir.ts`。
- Hermes：`agent/conversation_loop.py`、`agent/tool_executor.py`、`agent/context_engine.py`、`agent/context_compressor.py`、`agent/memory_manager.py`、`gateway/stream_events.py`、`hermes_state.py`、`mcp_serve.py`。
- OpenClaw：`README.md`、`VISION.md`、`docs/web/control-ui.md`、`docs/web/dashboard.md`、`src/acp/translator.ts`、`ui/src/ui/views/agents-panels-tools-skills.ts`、gateway/plugin/test 组织脚本。
- VS Code：`src/vs/base/common/event.ts`、`src/vs/platform/registry/common/platform.ts`、`src/vs/platform/instantiation/common/serviceCollection.ts`、`src/vs/workbench/services/extensions/common/extensions.ts`。

## 核心结论

Codex 的启发是：Agent 体验首先是事件体验。一次 turn 要有稳定的 started、item updated、completed、failed、usage 等事件，前端才能不猜状态。

cc源码的启发是：工具并行不能只是把多个函数一起跑。必须有工具执行器，区分并发安全和独占工具，同时处理进度、取消、兄弟工具失败和有序回填。

Hermes 的启发是：长会话一定会遇到上下文压缩、会话恢复、记忆污染和数据库锁问题。压缩摘要必须声明为参考材料而不是当前指令，记忆上下文必须防止泄漏到 UI。

OpenClaw 的启发是：Web 网关不是简单网页壳，而是控制平面。它要承载会话、配置、审批、日志、技能、节点和通道，同时用测试和脚本守住边界。

VS Code 的启发是：大型工程靠小而稳定的契约生长。事件工具、Registry、ServiceCollection、ExtensionHost delta 这些结构都说明，扩展性来自清晰接口和生命周期，不来自到处互相调用。

## 对 Nova 开发计划的影响

Nova 下一步必须增加一个“读代码与架构映射”的前置关卡。每次大功能开发前，先写清楚要借鉴哪个项目、借鉴哪个机制、Nova 为什么不能照抄、最后落到哪个接口或测试。

短期开发顺序应从“四个周期补功能”调整为：

1. 先建立 runtime event backbone，也就是统一事件协议和持久 timeline。
2. 再建立 `AgentSession`，让每个会话有稳定状态和恢复能力。
3. 再建立 `ToolExecutor`，让工具排队、并行、取消、权限和结果回填统一。
4. 再做权限审批、上下文预算、压缩、记忆、Skills 和 MCP。
5. 最后把 UI 打磨绑定到真实事件，不再做没有后端状态支撑的面板。

## 对长期建议的影响

Nova 要作为开源求职作品，不能只展示“我接了模型 API”。它需要展示：

- 清晰的运行时架构图。
- 稳定事件协议。
- 工具执行器测试。
- 权限审批和失败恢复测试。
- 上下文压缩和记忆边界说明。
- 可复现 demo 脚本。
- 用脚本守住边界的工程习惯。

Nova 要作为自用产品，也不能牺牲体验。路径选择、会话恢复、工具详情、状态线、审批卡片、设置和错误解释，都必须从运行时状态自然长出来。
