const assert = require("node:assert");
const fs = require("node:fs");

const app = fs.readFileSync("static/js/app.js", "utf8");
const css = fs.readFileSync("static/css/styles.css", "utf8");

assert(app.includes("cancelActiveTurn"), "前端应提供整轮对话停止函数");
assert(app.includes("AbortController"), "运行中的 stream 应可被 AbortController 中断");
assert(app.includes("/cancel"), "停止按钮必须调用真实 session cancel API");
assert(app.includes("state.streamAbortController"), "当前流的 abort controller 应保存在状态中");
assert(app.includes('sendButtonEl.dataset.mode = "stop"'), "发送按钮运行中应切换为停止模式");
assert(css.includes("#send-button.stop"), "停止模式按钮需要独立样式，用户能立即识别");
