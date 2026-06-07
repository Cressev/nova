# Nova 模块职责表

这份文档说明 Nova 当前代码目录的分工。目标不是把目录拆得很多，而是让后续开发知道每个能力应该落在哪里，避免所有逻辑继续堆到一个文件里。

| 模块 | 职责 | 关键文件 |
| --- | --- | --- |
| api | HTTP 路由入口，负责把浏览器请求转交给运行时、工具、工作区和配置模块。 | `src/nova_gateway/main.py` |
| runtime | Agent 单轮执行流程，负责模型调用、工具调用、事件流和最终回答。 | `src/nova_gateway/runtime/agent.py` |
| sessions | 会话生命周期，负责 active turn、队列消息和会话状态服务。 | `src/nova_gateway/sessions/agent_session.py` |
| tools | 工具注册、工具执行、并行/串行策略、工具元数据和失败兜底。 | `src/nova_gateway/tools/` |
| approvals | 权限审批和 pending tool call，负责 approve/deny 后的续跑数据。 | `src/nova_gateway/approvals/` |
| processes | 前台/后台进程管理，负责 stdout/stderr 分片、取消、tail 和 kill。 | `src/nova_gateway/processes/` |
| memory | Agent 指令、人格文件和长期记忆的注入、读写与展示状态。 | `src/nova_gateway/memory/` |
| workspace | 当前项目目录、候选目录、项目切换、工作区状态和 Git 摘要。 | `src/nova_gateway/workspace/` |
| providers | 大模型 Provider 适配，当前负责 GLM-4.7 调用和流式输出。 | `src/nova_gateway/providers/` |
| config | 运行配置、权限预设、API Key 热更新和本地配置持久化。 | `src/nova_gateway/config/` |
| observability | 本地 trace、timeline 和后续 Langfuse 调试扩展入口。 | `src/nova_gateway/observability/` |
| frontend | Web 工作台入口、API 客户端、状态存储、运行时事件解析、UI 辅助和组件。 | `static/js/`, `static/css/` |

## 前端目录

| 目录 | 职责 |
| --- | --- |
| `static/js/api` | 浏览器到 Nova 网关的请求封装。 |
| `static/js/state` | localStorage 和前端状态辅助。 |
| `static/js/runtime` | 流式事件解析、后续 runtime event 适配。 |
| `static/js/ui` | DOM 查询和通用 UI 辅助。 |
| `static/js/components` | 命令面板等可复用 UI 组件配置。 |
| `static/css` | 主题、布局和组件样式。 |
