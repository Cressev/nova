const assert = require("node:assert");
const fs = require("node:fs");

const html = fs.readFileSync("static/index.html", "utf8");
const app = fs.readFileSync("static/js/app.js", "utf8");
const css = fs.readFileSync("static/css/styles.css", "utf8");

for (const label of ["Workspace", "Review", "Run", "Permissions", "Tools", "Memory", "Config", "Processes"]) {
  assert(html.includes(label), `右侧终局面板应该包含 ${label}`);
}

assert(html.includes('id="process-list"'), "右侧应该有独立 Processes 列表");
assert(app.includes("/api/processes"), "前端应该读取后台进程 API");
assert(app.includes("renderProcessesPanel"), "前端应该有独立 Processes 面板渲染函数");
assert(app.includes("killProcess"), "Processes 面板应该能终止后台进程");
assert(app.includes("turn-process-collapsed"), "会话过程折叠能力不能丢");
assert(css.includes(".process-panel"), "Processes 面板需要独立样式");
assert(css.includes(".turn-process-control"), "会话过程折叠控件样式不能丢");
