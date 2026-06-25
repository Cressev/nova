# Nova

Nova 是一款面向个人开发者的本地优先编码 Agent 产品。

项目目标是参考 Codex、Claude Code、OpenClaw、Hermes 等产品，构建一个可个人长期使用的开发 Agent。重点不在“再做一个聊天机器人”，而在本地控制、项目记忆持久化、安全工具执行、可复用技能和可追踪工程流程。

## 产品方向

- 本地优先的终端 Agent，支持阅读、修改、测试和审查代码。
- 项目记忆可跨会话保留，避免上下文压缩或新会话导致信息丢失。
- Shell、文件编辑、网络访问、外部集成都要经过权限边界控制。
- 将代码审查、调试、文档、项目规划等重复任务沉淀成 Skills。
- 通过 trace 和 replay 理解 Agent 行为，并持续改进后续运行效果。

## 当前状态

当前仓库已经进入第一版可运行原型：Python FastAPI 本地网关、Codex-style 三栏 Web 工作台、GLM-4.7 流式对话、工作区工具调用、只读工具并行、全局/项目级 Agent 指令注入、项目目录切换、内置 slash 指令和工具调用可视化。

Nova 不是单纯聊天页。它的目标是做成个人长期使用的本地开发 Agent：能理解当前项目、读文件、查代码、看 Git diff、调用受控 shell、展示工具过程，并逐步补齐审批、工作树、会话隔离和 trace replay。

## 已实现能力

- Web-first 交互：浏览器中直接对话、查看项目、Git、工具、权限和记忆状态。
- GLM-4.7 Provider：兼容 BigModel OpenAI-style API，密钥只从 `BIGMODEL_API_KEY` 环境变量读取。
- Codex-like 工具循环：模型决策、工具执行、工具结果回填、最终流式回答。
- 工作区工具：`read_file`、`list_files`、`search_text`、`git_status`、`git_diff`、`shell_command`、`replace_in_file`、`create_file`、`apply_patch`。
- 只读工具并行：多个只读工具可并行执行，写入和 shell 不并行。
- 记忆边界：产品内 Agent 只注入全局 `~/.nova/AGENTS.md` 和当前项目 `AGENTS.md`；Nova 开发过程文件不注入。
- 项目切换：在允许根目录内切换当前工作区。
- 内置指令：`/status`、`/tools`、`/permissions`、`/memory`、`/review`、`/plan`、`/help`。
- 前端可观测：工具开始、完成、失败、模型状态和最终回答都在对话流中展示。

## 快速启动

当前版本可以直接从源码启动：

```bash
PYTHONPATH=src python3 -m nova.cli serve --host 127.0.0.1 --port 8765
```

如需启用 GLM-4.7 对话模型，启动前设置环境变量：

```bash
export BIGMODEL_API_KEY="你的 BigModel API Key"
PYTHONPATH=src python3 -m nova.cli serve --host 127.0.0.1 --port 8765
```

备用启动方式：

```bash
PYTHONPATH=src python3 run_nova.py
```

打开浏览器访问：

```text
http://127.0.0.1:8765
```

当前 Web UI 是 Codex-style 三栏工作台。左侧是项目和线程，中间是对话流，右侧是 Workspace、Review、Run、Permissions、Tools、Memory、Config。

运行基础测试：

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

## 仓库结构

```text
.
├── AGENTS.md
├── CLAUDE.md
├── CURRENT.md
├── PROGRESS.md
├── TODOList.md
├── README.md
├── log.md
├── user-queries.md
├── findings/
├── reports/
├── references/
├── src/
│   └── nova/
├── static/
├── tests/
├── 产品研发文档集/
│   ├── 预研文档集/
│   ├── v1.0/
│   └── v1.1/
```

## 项目记忆持久化

本项目按 `project-memory-persistence` 工作流维护项目记忆：

- `CURRENT.md`：当前任务、当前状态、下一步和阻塞。
- `PROGRESS.md`：项目阶段级进展和故事线。
- `log.md`：追加式关键动作日志。
- `user-queries.md`：用户请求原文、接收时间和完成时间。
- `TODOList.md`：每次请求的持久任务清单和执行状态。
- `findings/`：稳定发现和长期坑点。
- `reports/`：用户明确要求的结构化报告。

这些文件服务于 Nova 项目开发过程。产品内开发 Agent 的运行上下文只读取全局 `~/.nova/AGENTS.md` 和当前工作区 `AGENTS.md`。

## 开源协议

MIT License。详见 [LICENSE](LICENSE)。
