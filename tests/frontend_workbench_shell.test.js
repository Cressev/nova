const assert = require("node:assert");
const fs = require("node:fs");

const html = fs.readFileSync("static/index.html", "utf8");
const app = fs.readFileSync("static/js/app.js", "utf8");
const css = fs.readFileSync("static/css/styles.css", "utf8");

assert(!html.includes("计划优先"), "首屏不应再显示教学式 banner 文案");
assert(html.includes('id="thread-state"'), "顶部线程条应展示当前运行状态");
assert(app.includes("MutationObserver(syncThreadState)"), "线程条状态应跟随 composer 运行态变化");
assert(css.includes(".workbench-strip"), "Workbench 顶部状态条需要独立样式入口");
assert(css.includes(".composer-hints") && css.includes("display: none"), "输入区不应常驻展示快捷键提示");
assert(!css.includes("radial-gradient(circle at 62% 34%"), "品牌区不应继续使用装饰性光斑");
