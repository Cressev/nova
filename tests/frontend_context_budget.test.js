const assert = require("node:assert");
const fs = require("node:fs");

const app = fs.readFileSync("static/js/app.js", "utf8");
const css = fs.readFileSync("static/css/styles.css", "utf8");

assert(app.includes("context_budget_status"), "状态线应读取后端 context_budget_status");
assert(app.includes("auto_compact_threshold_tokens"), "设置或状态面板应展示自动 compact 阈值");
assert(app.includes("compact_recommended"), "前端应能展示是否建议 compact");
assert(app.includes("context-status-"), "状态线应按预算状态添加视觉 class");
// 状态线 UI 已随 dsh 重构移除；context budget 数据层断言保留
// critical 状态样式随状态线 UI 移除；数据层断言保留
