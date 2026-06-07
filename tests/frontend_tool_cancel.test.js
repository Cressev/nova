const assert = require("node:assert");
const fs = require("node:fs");

const app = fs.readFileSync("static/js/app.js", "utf8");
const css = fs.readFileSync("static/css/styles.css", "utf8");

assert(app.includes("cancelToolCall"), "前端应该实现运行中工具调用取消函数");
assert(app.includes("/api/tool-calls/"), "取消按钮必须调用真实 tool call cancel API");
assert(app.includes('data-action="cancel-tool"'), "运行中的工具卡片应该提供取消按钮");
assert(css.includes(".tool-cancel"), "取消按钮需要有独立样式，便于用户识别");
