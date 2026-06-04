# CURRENT.md

## 当前任务

Nova 已进入代码实现阶段。当前已修正为对话式 Web Agent 网关，并接入 BigModel GLM-4.7 Provider（通过 `BIGMODEL_API_KEY` 环境变量读取 key）。

## 当前状态

- 已通过 `gh api` 读取到 `Cressev/project-memory-persistence` 的 `SKILL.md` 和 references 模板。
- 已按 Skill 要求创建根目录核心资产：`AGENTS.md`、`CURRENT.md`、`PROGRESS.md`、`log.md`、`TODOList.md`、`user-queries.md`、`findings/`、`reports/`、`references/`。
- 旧记忆目录结构已清理。
- 产品研发文档集已按用户要求使用中文目录结构，并补齐各 durable 目录 README。
- 文档正文已统一为中文。
- 产品研发文档集中的普通文档已清空；README 保留用于说明目录安排和维护规则。
- 已记录用户偏好：只有用户明确要求写某份产品研发文档时，Agent 才能写正文。
- 产品研发文档文件名日期已改为当前初始化日期 `20260603`。
- 已写入 `产品研发文档集/预研文档集/20260603_需求文档.md`。
- 其他普通产品研发文档仍保持空白。
- 已修正技术选型方向：MVP 推荐 Python FastAPI 后端网关 + Web 前端 + 本地 Markdown/JSONL 记忆与 trace；CLI 作为备用启动/诊断功能。
- Langfuse 用于开发者调试、工程观测、trace 分析、prompt/eval 管理；不直接暴露给最终用户。
- 用户看到的任务进度、工具调用、审批、trace 摘要和执行结果，需要在 Web 前端中自行设计。
- 命名偏好已更新：不要使用 `pda` 作为产品名、简称或默认命令名。
- 命名偏好进一步更新：名字要好玩、新颖、有品牌感，避免普通工程缩写。
- 正式产品名已确定为 Nova；默认命令名使用 `nova`。
- 已同步修改需求文档和 v1.0 技术文档，使用 Nova 作为正式产品名。
- v1.0 技术方案已写明：Web-first、本地 FastAPI 网关、Python 后端、用户侧前端可观测、Langfuse 开发调试可观测。
- 用户要求后续产品研发文档优先写飞书云文档，并用 bot 在飞书云盘创建同构目录、导入已写文档、私信通知用户。
- 已使用 `lark-cli` 在用户飞书云盘创建 `Nova/产品研发文档集/` 同构目录。
- 已将已有正文的本地 Markdown 草稿创建为飞书新版文档（docx），Markdown 普通文件不作为后续主要交付形态。
- 已按用户要求删除此前误上传到飞书云盘的 6 个 Markdown 普通文件，只保留 docx 云文档作为主要交付。
- 已使用 bot 私信通知用户初次同步，消息 ID：`om_x100b6ecad9a4a4a4b48a1ffb9fc887b`。
- 已使用 bot 私信通知用户 docx 更正，消息 ID：`om_x100b6ecae7b3b0bcb3c567bb7616f96`。
- 飞书云盘根目录：`https://jcnu7fvwv6c8.feishu.cn/drive/folder/CfHOfw192lym5zdllA6ctINMnPg`。
- 飞书产品研发文档集：`https://jcnu7fvwv6c8.feishu.cn/drive/folder/HZznf3LjmlZ2iYdgPr8cWe4Hncf`。
- 飞书 docx 文档：
  - 产品研发文档集 README：`https://jcnu7fvwv6c8.feishu.cn/docx/XkUVdBFQzojDbWxbXRTcrYcMn6f`
  - 预研文档集 README：`https://jcnu7fvwv6c8.feishu.cn/docx/X7sudxRfdop3pzxzon4c0OilnLf`
  - v1.0 README：`https://jcnu7fvwv6c8.feishu.cn/docx/E6jndpHE6oKiCZxcLJoc6uqTnTh`
  - v1.1 README：`https://jcnu7fvwv6c8.feishu.cn/docx/JULQdy0m7oendJxhIj3cZI93nxh`
  - 20260603_需求文档：`https://jcnu7fvwv6c8.feishu.cn/docx/YybZd1eunoIzrZxDOLEcCx8Rnhb`
  - 20260603_v1.0技术文档：`https://jcnu7fvwv6c8.feishu.cn/docx/CHVSdG6JKolNKRxJGSvceGewn2d`
- 用户要求先不等审批文档，直接开干、干中学。
- 已创建第一版代码骨架：
  - `src/nova_gateway/`：FastAPI 后端、任务存储、模拟 runtime、trace recorder、CLI。
  - `static/`：本地 Web UI。
  - `tests/`：基础 API 测试。
  - `pyproject.toml`：Python 项目配置。
  - `run_nova.py`：源码直接启动入口。
- 当前本地服务已启动：`http://127.0.0.1:8765`。
- 本轮验证通过：
  - `PYTHONPATH=src python3 -m unittest discover -s tests`
  - `PYTHONPATH=src python3 -m nova_gateway.cli doctor`
  - `GET /api/health`
  - `POST /api/tasks`
  - `GET /api/tasks/{task_id}/events`
  - `GET /api/tasks/{task_id}/trace`
- 已按用户纠正，将 Web UI 从任务面板改为对话式交互。
- 已新增聊天会话和消息接口：`/api/chat/sessions`、`/api/chat/sessions/{session_id}/messages`。
- 已接入 BigModel OpenAI-compatible Chat Completions：
  - base URL：`https://open.bigmodel.cn/api/paas/v4`
  - model：`glm-4.7`
  - key 环境变量：`BIGMODEL_API_KEY`
- 已补充中文代码注释，覆盖 Provider、Store、API 编排和前端关键交互。
- 已初始化 git 仓库并推送到 GitHub private 仓库：
  - 远程仓库：`https://github.com/Cressev/nova`
  - SSH remote：`git@github.com:Cressev/nova.git`
  - main 分支首个提交：`a51a763`
- 用户新增开发交付要求：每次完成开发后必须启动本地网站/服务，并给出可验证 URL。

## 立即下一步

- 继续实现真实 Agent Runtime：工具注册表、权限引擎、文件读取/搜索/shell 工具。
- 将对话回复和工具执行打通，让 GLM-4.7 能在权限控制下调用本地工具。
- 增加审批接口和前端审批卡片。
- 等待用户明确指定要写的产品研发文档后，再写对应正文。
- 新增产研文档时使用实际创建日期，不沿用示例日期。
- 如用户继续要求，可按顺序写技术调研、核心功能实现方案或 v1.0 功能开发清单。
- 若用户要求写选型文档，可写入 `产品研发文档集/预研文档集/20260603_技术调研.md`。
- 后续架构文档应体现 Web-first、本地网关、Python 后端、Langfuse 开发者可观测、用户前端可观测五个边界。
- 已确定正式产品名 Nova；后续文档、代码和命令名优先使用 Nova / `nova`。
- 是否立即把项目目录从 `personal-dev-agent` 改为 `nova` 仍待用户确认。
- 飞书同步已完成；后续新增/修改产研文档时，优先创建或更新飞书新版文档（docx），不要用 Drive Markdown 普通文件作为主要交付。

## 已知阻塞

- 初始化偏好尚未完全确认：git 策略、远程仓库、禁止触碰范围、durable artifact 定义、前端是否升级到 React + Vite。
- 产品研发文档正文不得主动补写。
- 当前已接模型 Provider，但尚未实现流式输出、真实工具调用和审批闭环。
- 每次开发收尾必须确认服务已启动并把 URL 交给用户验证。
- 本轮已重做静态 Web UI：深色侧边栏、对话主窗口、模型/网关状态、空状态快捷提示、发送中状态和更清晰的消息气泡。
- 本轮已用用户提供的 BigModel key 启动当前服务进程，key 仅注入运行时环境，不落盘、不提交。
- 当前服务地址：`http://127.0.0.1:8765`。
- Provider 状态已验证：`configured=true`。
- 真实 GLM-4.7 对话已验证，返回内容：`Nova 已连接`。
- 已检查优秀 Skill 的共性：触发条件精准、主文件简洁、复杂内容拆 references、危险操作有门禁、交付前强验证；后续 Nova 的 Skills/工作流按这些原则设计。
- 已安装本地 UI/design 和前端验证相关 Skill：`figma-generate-design`、`figma-implement-design`、`figma-create-design-system-rules`、`figma-use`、`playwright`、`screenshot`。安装后需重启 Codex 才会自动触发，但当前已直接读取其 `SKILL.md` 参考。
- 已实现聊天流式输出：
  - 后端 Provider 支持 OpenAI-compatible SSE。
  - 新增 `/api/chat/sessions/{session_id}/stream` NDJSON 流接口。
  - 前端发送后立即显示用户消息。
  - assistant 消息按 `assistant_delta` 增量更新。
- 当前服务已用 BigModel key 启动，网站地址：`http://127.0.0.1:8765`。
- 流式验证通过：真实请求返回 `user_message`、`assistant_delta`、`assistant_done`。

## 最后更新

2026-06-04
