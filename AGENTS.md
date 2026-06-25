# AGENTS.md

## 项目摘要

- 项目名称：Nova
- 项目根目录：`/mnt/d/Documents/Study/Code/codex/personal-dev-agent`
- 主要目标：构建一款个人本地优先编码 Agent 产品，参考 Codex、Claude Code、OpenClaw、Hermes 的成熟能力，重点实现项目记忆、安全工具执行、可复用 Skills、trace/replay 和长期个人开发工作流。
- 当前阶段：v1.0 MVP 代码实现。
- 主要用户/干系人：个人开发者；当前主要用户是项目发起人。

## 适用范围

本文件定义 Agent 在以下目录内工作的长期规则：

- `/mnt/d/Documents/Study/Code/codex/personal-dev-agent`

如果任务涉及该目录外的文件、系统、服务或远程资源，必须先检查风险；风险不明确时先向用户确认。

## 标准路径

- 项目根目录：`/mnt/d/Documents/Study/Code/codex/personal-dev-agent`
- 持久记忆根目录：项目根目录
- 产品研发文档：`产品研发文档集/`
- 预研文档：`产品研发文档集/预研文档集/`
- 版本文档：`产品研发文档集/v1.0/`、`产品研发文档集/v1.1/`
- 报告目录：`reports/`
- 发现目录：`findings/`
- 参考模板目录：`references/`
- 用户任务入口：`user-queries.md`
- 持久任务追踪：`TODOList.md`
- 当前状态：`CURRENT.md`
- 项目进展：`PROGRESS.md`
- 追加式日志：`log.md`

## 项目故事

本项目从“开发一款企业级 Agent 应用帮助找工作”的讨论开始。经过招聘信息爬取可行性和项目突出度分析后，方向调整为个人使用的本地开发 Agent 产品。

当前产品定位是：做一个个人本地优先编码 Agent，不直接复刻 Codex 或 Claude Code，而是结合成熟产品的优势形成自己的主张：

- 学习 Codex 的工程 workflow、权限、Skills、插件、MCP、review 和 subagents。
- 学习 Claude Code 的项目指令、Hooks、Subagents、Agent teams 和扩展边界。
- 学习 Hermes 的长期记忆、Skills 自改进、多模型、多入口和多执行环境。
- 学习 OpenClaw 的自托管 gateway 和长期在线个人助手思路。
- 学习 OpenClaw-RL 的 trace 到自改进信号闭环，但第一阶段先做 Skill 改进，不做模型训练。

未来 Agent 在行动前必须理解：本项目已经进入 v1.0 MVP 代码实现阶段，当前优先把对话式 Web Agent 网关、模型接入、工具执行和权限审批跑通。

## 会话启动规则

每次开始新任务或上下文重置后，在做实质工作前必须：

1. 读取 `AGENTS.md`。
2. 读取 `CURRENT.md`。
3. 读取 `PROGRESS.md`。
4. 读取相关 durable 目录的 `README.md`。
5. 读取 `log.md` 末尾。
6. 读取 `user-queries.md` 末尾。
7. 找到最新未处理 query block。
8. 运行 `date '+%y/%m/%d-%H:%M:%S %Z'`，立即追加 `[Recieve:<date>]`。

`[Recieve:...]` 必须在开始任何实质工作前写入，不能在任务结束后补写。

## 任务入口规则

- `user-queries.md` 是用户任务入口和 durable source of truth。
- 用户查询块用 `---` 分隔。
- 不得重写、润色、合并、删除、重排、纠正历史 query 文本。
- 如果用户直接在聊天中提出任务，必须把用户原话追加到 `user-queries.md`，并立即标记 `[Recieve:<date>]`。
- 最终交付后追加 `[Done:<date>]`。
- 如果本次任务做了 git 提交，在 Done 同一行追加提交码：`[Done:<date> | <commit hash>]`。

## 工作循环

- 在实质探索前说明第一步。
- 先读取真实文件和命令输出，再做判断。
- 获得足够上下文后制定短计划。
- 文件编辑前说明将修改什么。
- 执行过程中持续更新 `TODOList.md`。
- 使用最相关的命令、测试或文件检查验证结果。
- 最终回复前更新 durable 资产。

## TODOList 规则

- `TODOList.md` 是持久化任务追踪文档，只允许追加，不允许删除或重写历史记录。
- 每个用户请求都要创建新的任务块：
  - 开始分隔符：`------ todo-list begin at YYYY/MM/DD/HH:mm:ss -----`
  - 逐字记录用户请求。
  - 创建结构化清单，所有初始状态为 `[]`。
- 状态标记：
  - `[]`：未完成
  - `[x]`：已完成
  - `[o]`：失败
- 执行过程中必须持续更新状态，不能在任务结束后批量补写。
- 失败任务必须写入“执行问题记录”，包含任务编号、失败原因和处理方式。
- 失败任务修复后，将状态改为 `[x]` 并补充修复说明。
- 所有任务完成后追加结束分隔符：`------ todo-list end at YYYY/MM/DD/HH:mm:ss -----`。
- 详细规则见 `references/todolist_workflow_template.md`。

## 日志规则

- `log.md` 是追加式日志。
- 时间必须来自 `date '+%y/%m/%d-%H:%M:%S %Z'`。
- 推荐格式：
  - `- [yy/mm/dd-HH:MM:SS TZ] 一句话说明动作或结果。产物：路径。验证：验证方式。下一步：下一步。`
- 日志保持简洁；长分析放入 `reports/`、`findings/` 或产品研发文档。

## 报告规则

- 只有用户明确要求报告、归档文档、总结文档或交付文档时，才新建报告。
- 报告保存在 `reports/`。
- 报告文件名必须以 `yyyy-mm-dd-hhmm_` 开头。
- 新增报告时必须更新 `reports/README.md`。

## findings 规则

- 可复用环境事实、坑点、部署约束、稳定决策写入 `findings/`。
- 新增 finding 时必须更新 `findings/README.md`。

## Git 策略

- 本地 git：已初始化。
- 远程 git：`git@github.com:Cressev/nova.git`。
- GitHub 仓库：`https://github.com/Cressev/nova`。
- 仓库可见性：private。
- 默认分支：`main`。
- 提交规则：提交信息使用简洁英文动词短语；每次完成可运行改动后推送远程。
- 未经用户明确要求，不重写历史，不执行 destructive git 操作。

## 文件安全

- 不得删除或覆盖用户已有变更，除非用户明确要求。
- 如果处于 Git 仓库内，编辑前优先检查 git 状态。
- 无关 dirty work 保持不动。
- 编辑应窄而可审查。
- 删除文件、清理目录、覆盖配置、执行破坏性命令前必须确认，除非用户已经给出精确清理指令。

## 用户偏好

- 文档语言全部使用中文。
- 最终回答用中文，简洁说明变更、路径、验证和残余风险。
- 产品研发文档使用 `产品研发文档集/` 中文目录结构。
- 产品研发文档正文只有在用户明确要求写某份文档时才写；不得主动补写、扩写或生成普通产研文档内容。
- 后续产品研发文档优先写入飞书云文档；本地 `产品研发文档集/` 作为草稿、镜像或飞书不可用时的落地位置。
- 使用飞书时，Agent 使用 bot 身份；用户使用自己的飞书账号接收文档权限和私信通知。
- 产品形态优先 Web 网关和网页交互，CLI 作为备用功能。
- Web 交互必须以对话形式为主，任务、工具调用、trace 和审批作为对话旁路信息展示，不要把任务面板作为第一入口。
- 每次完成开发后，必须启动本地网站/服务，并在最终回复中给出可验证 URL；如果默认端口被占用，换可用端口并说明。
- 后端尽量使用 Python 实现，便于用户学习和理解。
- 代码中的关键设计点、边界条件和不直观逻辑使用中文注释；避免无意义空泛注释。
- Langfuse 仅作为开发调试和工程可观测工具，不作为最终用户界面。
- 用户侧可观测、任务进度、工具调用、审批、trace 摘要和结果展示需要由本项目自行设计前端交互。
- 不使用 `pda` 作为产品名、简称或默认命令名；产品命名要更自然、更有辨识度。
- 产品命名偏好：要更好玩、新颖、有品牌感，接近 OpenClaw、Hermes 这类有记忆点的名字；避免过于普通的工程工具名。
- 正式产品名：Nova。默认命令名使用 `nova`，不要使用 `pda`。
- 第一版模型 Provider：BigModel GLM-4.7，base URL 为 `https://open.bigmodel.cn/api/paas/v4`；API key 只允许通过 `BIGMODEL_API_KEY` 环境变量读取，不得写入代码、文档、日志或项目记忆。

## 硬规则 / 不得违反

- 不得把重要状态只留在聊天里，必须写入 durable 文件。
- 不得跳过 `user-queries.md` 的 `[Recieve:...]` 和 `[Done:...]`。
- 不得删除或重写 `TODOList.md` 历史任务块。
- 不得将多个用户请求合并为一个 query block。
- 文档正文必须使用中文。
- 除 README 维护说明外，产品研发文档正文默认保持空白，只有用户明确要求写时才写。

## 外部系统

- GitHub：当前可通过 `gh` 使用账号 `Cressev` 访问私有仓库。涉及仓库拉取、API 读取、远程提交时应说明动作。
- `Cressev/project-memory-persistence`：项目记忆持久化 Skill 来源。初始化规则以该仓库 `SKILL.md` 为准。
- 飞书：优先用 `lark-cli` 创建和更新飞书新版文档（docx），Agent 使用 bot 身份私信通知用户；不要再把产研文档作为 Drive Markdown 普通文件交付。
- `references/upstream/src`：后续简称“cc源码”。进行 Nova agent runtime、工具执行、权限、状态线、技能、记忆、MCP、会话恢复和交互体感设计时，必须同时参考该目录，不只参考 OpenAI Codex 源码。

## 验证预期

- 文件结构检查：`find personal-dev-agent -maxdepth 4 -type f | sort`
- 中文化扫描：对 Markdown 文件扫描明显英文段落或旧路径引用。
- 目录 README 检查：确认 durable 目录包含 `README.md`。
- 当前测试命令：`PYTHONPATH=src python3 -m unittest discover -s tests`。
- 当前启动命令：`PYTHONPATH=src python3 -m nova.cli serve --host 127.0.0.1 --port 8765`。

## Durable Artifacts

- 产品研发文档：需求、调研、头脑风暴、功能方案、版本清单、技术文档、发布文档。
- findings：稳定环境发现、长期坑点、约束和复现事实。
- reports：用户明确要求的结构化报告或交付文档。
- logs：关键动作、验证和最终结果。
- TODOList：每个用户请求的持久执行追踪。
- user-queries：用户请求原文和接收/完成时间。

## 待确认问题

初始化必须确认但用户尚未回答的问题：

- 是否为本项目初始化或使用本地 git？
- 是否配置或使用远程 git 仓库？
- 是否存在未经确认绝不能触碰的文件、目录、机器、服务或命令？
- 希望未来 Agent 默认遵循哪些用户偏好？
- 本项目中哪些内容算 durable artifact？
- 前端具体技术栈后续是否升级为 React + Vite + Tailwind？
- Python 后端是否采用 FastAPI？
