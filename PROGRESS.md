# PROGRESS.md

## 项目故事

Nova 是面向个人开发者的本地优先编码 Agent 产品。项目从“做企业级 Agent 应用帮助找工作”的讨论开始，随后收敛到“做一个个人使用的类 Codex、Claude Code、OpenClaw、Hermes 的产品”。

## 已完成阶段

### 预研方向确认

- 已确认不把招聘网站爬虫作为核心项目方向。
- 已确认产品重点是个人本地开发 Agent，而不是招聘信息聚合。
- 已分析 Codex、Claude Code、OpenClaw、OpenClaw-RL、Hermes、Kimi Code CLI 的可借鉴能力。

### 产品研发文档初始化

- 已创建 `产品研发文档集/`。
- 已创建 `预研文档集/`、`v1.0/`、`v1.1/`。
- 已按用户要求清空普通产品研发文档正文。
- 已重写产品研发文档集及各子目录 README，用于说明目录安排和维护规则。
- 已记录规则：只有用户明确要求写某份文档时，Agent 才能写正文。
- 已将产研文档文件名日期统一修正为当前初始化日期 `20260603`。
- 已按用户明确要求写入详细需求文档：`产品研发文档集/预研文档集/20260603_需求文档.md`。
- 除需求文档外，其他普通产品研发文档仍保持空白。
- 已确定正式产品名为 Nova。
- 已将需求文档中的产品名、`.nova` 运行时路径、Web-first、Python 后端、Langfuse 开发调试观测、用户侧前端可观测边界同步更新。
- 已按用户要求写入 `产品研发文档集/v1.0/20260603_v1.0技术文档.md`。
- 已使用 `lark-cli` 在飞书云盘创建 `Nova/产品研发文档集/` 同构目录。
- 已按用户纠正，将已有正文的文档创建为飞书新版文档（docx），后续飞书产研文档默认使用 docx 云文档形态。
- 已删除此前误上传的 6 个 Drive Markdown 普通文件。
- 已使用 bot 私信通知用户飞书同步和 docx 更正完成。

### 项目记忆系统校正

- 已读取 `Cressev/project-memory-persistence` 的 `SKILL.md`。
- 已按 Skill 要求迁移为根目录记忆资产。
- 已创建 `CURRENT.md`、`PROGRESS.md`、`log.md`、`TODOList.md`、`user-queries.md`。
- 已创建 `findings/`、`reports/`、`references/` 并补充 README。
- 已删除不符合目标 Skill 的旧记忆目录。
- 已补充 `references/` 下的工作流模板。

### v1.0 运行时实现启动

- 用户明确要求不等审批文档，直接开始实现、干中学。
- 已创建 Nova Web-first MVP 骨架。
- 已实现 Python FastAPI 本地网关。
- 已实现任务创建、任务列表、任务详情、事件列表和 trace 读取接口。
- 已实现本地 JSONL trace recorder。
- 已实现可替换的模拟 Agent Runtime，用于跑通任务流和用户侧可观测。
- 已创建原生静态 Web UI，用于提交任务、查看任务列表、执行过程和结果。
- 已提供备用 CLI 启动入口：`PYTHONPATH=src python3 -m nova_gateway.cli serve --host 127.0.0.1 --port 8765`。
- 已通过基础 API 测试和真实 HTTP 请求验证。
- 已按用户纠正改为对话式 Web UI，而不是任务面板入口。
- 已接入 BigModel GLM-4.7 Provider，使用 `BIGMODEL_API_KEY` 环境变量读取密钥。
- 已补充中文代码注释。
- 已初始化 git 仓库并推送到 GitHub private 仓库：`https://github.com/Cressev/nova`。
- 已重做对话式 UI，形成更接近开发 Agent 工作台的界面：深色会话侧边栏、主聊天区、模型状态、空状态快捷操作和发送状态。
- 已使用运行时环境变量注入 BigModel key，验证 `glm-4.7` 可真实回复。
- 已查看优秀 Skill 的设计模式，并明确后续 Nova 工作流和 Skills 应采用渐进披露、强门禁、强验证、脚本/引用拆分等模式。
- 已安装 Figma/UI 和浏览器验证相关 Skill 到本地，作为后续 UI 设计和验证参考。
- 已实现 GLM-4.7 流式输出，解决发送后用户消息不立即显示的问题。
- 已按本地 UI/design skill 的实现和验证思路二次改造前端：
  - 暗色 Agent 工作台视觉系统。
  - 流式生成中光标和消息入场动效。
  - 输入框自适应高度、发送状态栏和错误消息可见化。
  - 真实浏览器验证无控制台错误。
- 已补充后端测试：favicon、流式成功事件、流式缺 key 错误事件均有覆盖。
- 已按 Codex App 官方手册完成 v1.0 功能开发文档，并同步为飞书新版文档：
  - 本地：`产品研发文档集/v1.0/20260604_Codex_App风格功能开发文档.md`
  - 飞书：`https://jcnu7fvwv6c8.feishu.cn/docx/ErprdC3kro4XFhxNObwcDwu7nzh`
- 已启动 Codex-style 工作台实现：
  - 前端升级为三栏工作台。
  - 新增本地/工作树/云端模式 UI。
  - 新增 Workspace、Review、Run、Permissions、Browser 右侧面板。
  - 新增 `/api/workspace/status` 只读接口，展示项目、Git 和运行命令摘要。
- 已下载并研究 OpenAI 开源 Codex 仓库 `openai/codex`：
  - 本地路径：`references/upstream/openai-codex/`。
  - clone commit：`ad2012d`。
  - 许可证：Apache-2.0。
  - 上游源码目录已加入 `.gitignore`，仅作参考，不纳入 Nova 仓库。
- 已实现第一版 Codex-like Agent 工具循环：
  - 后端新增 `CodexLikeAgentRuntime`，采用“模型决策 -> 工具执行 -> 工具结果回填 -> 最终流式回答”的闭环。
  - 新增 `WorkspaceTools`，支持读取文件、列目录、ripgrep 搜索、受控 shell、文本替换、创建文件和 Git 状态。
  - 工具路径限制在工作区内，并拦截 `.git`、`.nova`、`references/upstream`、`.playwright-cli`、`output` 等保护路径。
  - shell 命令使用白名单和破坏性命令拦截，当前版本不允许 `rm`、`sudo`、`chmod`、`chown`、`git push` 等高风险操作。
  - GLM 工具调用解析支持 `<tool_call>{...}` 缺少闭合标签的情况。
- 已实现前端工具事件展示：
  - 用户消息发送后立即显示。
  - 工具调用以卡片形式展示运行中、完成和失败状态。
  - 工具卡片排在最终回答前，并保留在当前轮对话中。
  - 发送新线程第一条消息时自动清理空状态。
- 已按用户指出的缺口补齐一轮基础能力：
  - UI 字号、间距和三栏密度已压小。
  - 右侧面板显示真实 Tools、Memory、Config，不再用 Browser 占位冒充。
  - 后端新增 runtime config、tools、memory status API。
  - 工具新增元数据、权限级别、并行标记。
  - 工具扩展到 `git_diff` 和 `apply_patch`。
  - 只读工具支持并行执行路径，写工具和 shell 不并行。
  - 配置支持模型、base_url、权限模式、网络开关、工具轮次环境变量。
  - 初版项目记忆曾注入 `AGENTS.md`、`CURRENT.md`、`PROGRESS.md`，后续已按用户纠正改为全局 + 项目级 `AGENTS.md` 指令模型。
  - 内置指令支持 `/status`、`/tools`、`/permissions`、`/memory`、`/review`、`/plan`、`/help`。
  - 输入框输入 `/` 自动弹出指令选择。
  - Agent 流增加状态事件和通用异常兜底。
- 已按用户指出的记忆边界和项目切换问题补齐一轮：
  - 产品内 Agent 只注入全局 `~/.nova/AGENTS.md` 和当前工作区 `AGENTS.md`。
  - `CURRENT.md`、`PROGRESS.md`、`TODOList.md`、`log.md` 明确归为 Nova 外层开发过程文件，不注入产品内 Agent。
  - 新增 `WorkspaceManager`、`GET /api/workspaces`、`POST /api/workspace/select`，支持在允许根目录内切换项目目录。
  - 前端左侧新增项目路径输入、候选目录和切换按钮。
  - 右侧 Memory 面板按“给开发 Agent：全局 / 给开发 Agent：项目 / 只给 Nova 开发过程”分组。
  - `/` 指令菜单改为紧凑结构化列表，Enter 选择指令，Ctrl+Enter 发送。
  - 修复 `/memory` 内置指令读取旧结构导致异常的问题。
- 已按用户要求在继续开发前补读参考源码：
  - Codex：事件协议、线程、turn loop、工具、执行策略、Skills 和 thread manager。
  - cc源码：QueryEngine、StreamingToolExecutor、ToolUseContext、Skills loader。
  - Hermes：会话循环、工具执行、上下文压缩、记忆隔离、压缩锁、事件桥和权限响应。
  - OpenClaw：Gateway 控制 UI、ACP translator、审批 relay、工具/技能配置面板和边界检查脚本。
  - VS Code：事件系统、Registry、ServiceCollection 和扩展宿主 delta 模型。
- 已根据源码阅读修正路线：下一步先做 runtime event backbone（运行时事件骨架），再做 `AgentSession`、`ToolExecutor`、权限审批、上下文预算/压缩、记忆、Skills 和 MCP。
- 已追加更新飞书文档 `Nova 下一步开发计划与长期路线`，补充“先读代码，再开发”的故事线和新版开发顺序。
- 已新增 finding：`findings/2026-06-06-reference-architecture-reading.md`。

## 当前阶段

项目已从产品预研进入 v1.0 MVP 代码实现阶段。当前已跑通对话式本地 Web Agent、GLM-4.7 流式输出、全局/项目级 Agent 指令注入、项目目录切换、内置指令、只读工具并行和第一版真实工具调用闭环。根据最新源码阅读，后续重点先调整为运行时事件骨架和持久 timeline，再补 `AgentSession`、统一工具执行器、权限审批、上下文压缩、记忆/Skills/MCP 和 Langfuse 开发观测。

## 后续方向

- 先实现 runtime event backbone，统一 turn、工具、权限、压缩、失败和恢复事件。
- 基于事件骨架重构 `AgentSession`，让每个会话跨多轮保存稳定状态。
- 建立统一 `ToolExecutor`，处理并行安全、独占、取消、失败和有序回填。
- 继续完善权限引擎和审批卡片。
- 实现工作树创建、切换和清理。
- 增加命令输出分片和 git diff/file change 卡片。
- 完善 trace 摘要页和 Langfuse 开发调试接入。
- 根据“待确认问题”明确 git、远程仓库、风险边界、用户偏好和持久化资产定义。
- 等待用户明确指定要写的产品研发文档，再写具体正文。
- 等待用户确认是否将项目目录重命名为 `nova`，以及是否继续写 v1.0 功能开发清单。
