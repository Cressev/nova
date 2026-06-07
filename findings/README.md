# findings 目录说明

## 用途

本目录用于保存未来会反复影响项目的稳定发现，例如环境限制、工具行为、部署坑点、复现条件、长期风险边界等。

## 当前文件

- `2026-06-03-feishu-sync-prerequisites.md`：飞书同步前置条件和当前阻塞状态。
- `2026-06-05-cc-source-reference.md`：cc源码可借鉴的 agent runtime、工具执行、权限、状态线、技能、记忆和 MCP 设计结论。
- `2026-06-06-reference-architecture-reading.md`：Codex、cc源码、Hermes、OpenClaw、VS Code 源码阅读后对 Nova 架构路线的修正结论。
- `2026-06-07-opencode-crush-source-reference.md`：OpenCode / Crush 源码下载位置、版本和后续优先参考入口。

## 放置规则

- 只放稳定、可复用、未来可能再次影响决策的信息。
- 不放临时命令输出。
- 不放普通任务进度，任务进度写入 `PROGRESS.md` 或 `log.md`。
