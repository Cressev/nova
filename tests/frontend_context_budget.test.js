const assert = require("node:assert");
const fs = require("node:fs");

const app = fs.readFileSync("static/js/app.js", "utf8");
const css = fs.readFileSync("static/css/styles.css", "utf8");

assert(app.includes("context_budget_status"), "状态线应读取后端 context_budget_status");
assert(app.includes("auto_compact_threshold_tokens"), "设置或状态面板应展示自动 compact 阈值");
assert(app.includes("compact_recommended"), "前端应能展示是否建议 compact");
assert(app.includes("context-status-"), "状态线应按预算状态添加视觉 class");
assert(/\.context-status-warning/s.test(css), "上下文 warning 状态应有独立样式");
assert(/\.context-status-critical/s.test(css), "上下文 critical 状态应有独立样式");
