const assert = require("node:assert");
const fs = require("node:fs");

const app = fs.readFileSync("static/app.js", "utf8");
const css = fs.readFileSync("static/styles.css", "utf8");

assert(app.includes("const TOOL_TOOLTIP_DELAY_MS = 1000"), "工具详情应悬浮 1 秒后显示");
assert(app.includes("scheduleToolTooltip"), "工具详情应由 JS 延迟控制");
assert(!css.includes(".tool-chip:hover .tool-tooltip"), "工具详情不应该用 CSS hover 立即显示");
assert(css.includes(".tool-tooltip.visible"), "工具详情显示态应使用 visible 类控制");
