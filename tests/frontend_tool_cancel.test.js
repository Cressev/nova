const assert = require("node:assert");
const fs = require("node:fs");

const app = fs.readFileSync("static/js/app.js", "utf8");
const css = fs.readFileSync("static/css/styles.css", "utf8");

assert(app.includes("cancelToolCall"), "前端应该实现运行中工具调用取消函数");
assert(app.includes("/api/tool-calls/"), "取消必须调用真实 tool call cancel API");
assert(app.includes('data-action="cancel-tool"'), "运行中工具的展开体应提供取消按钮");
assert(css.includes(".tool-running-actions"), "展开体动作区需要独立样式");
console.log("frontend_tool_cancel ok");
