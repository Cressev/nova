const assert = require("node:assert");
const fs = require("node:fs");

const html = fs.readFileSync("static/index.html", "utf8");
const app = fs.readFileSync("static/js/app.js", "utf8");
const css = fs.readFileSync("static/css/styles.css", "utf8");

assert(html.includes('id="subagent-list"'), "右侧应该有子 Agent 状态列表");
assert(html.includes('id="subagent-spawn"'), "右侧应该有 spawn 子 Agent 入口");
assert(app.includes("/api/subagents"), "前端应该读取子 Agent 列表 API");
assert(app.includes("/wait"), "前端应该调用子 Agent wait API");
assert(app.includes("renderSubagentsPanel"), "前端应该有独立的子 Agent 面板渲染函数");
assert(app.includes("closeSubagent"), "前端应该支持关闭子 Agent");
assert(app.includes("runtimePanelsRequestId"), "运行面板并发刷新需要 request id 防止旧响应覆盖子 Agent 列表");
assert(app.includes("requestId !== state.runtimePanelsRequestId"), "旧的运行面板响应不应该覆盖新的子 Agent 状态");
assert(css.includes(".subagent-panel"), "子 Agent 面板需要独立样式");
