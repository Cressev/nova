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

当前仓库已经进入第一版运行时实现。已完成一个 Web-first MVP 骨架：Python FastAPI 后端、本地静态 Web UI、任务创建接口、任务事件时间线和本地 JSONL trace。

## 快速启动

当前版本可以直接从源码启动：

```bash
PYTHONPATH=src python3 -m nova_gateway.cli serve --host 127.0.0.1 --port 8765
```

如需启用 GLM-4.7 对话模型，启动前设置环境变量：

```bash
export BIGMODEL_API_KEY="你的 BigModel API Key"
PYTHONPATH=src python3 -m nova_gateway.cli serve --host 127.0.0.1 --port 8765
```

备用启动方式：

```bash
PYTHONPATH=src python3 run_nova.py
```

打开浏览器访问：

```text
http://127.0.0.1:8765
```

当前 Web UI 是对话形式。左侧是对话列表，右侧是聊天窗口；模型配置状态会显示在聊天窗口顶部。

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
│   └── nova_gateway/
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
