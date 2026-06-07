const assert = require("node:assert");
const fs = require("node:fs");

const html = fs.readFileSync("static/index.html", "utf8");
const app = fs.readFileSync("static/app.js", "utf8");
const css = fs.readFileSync("static/styles.css", "utf8");

assert(html.includes('id="memory-dialog"'), "应该提供主题一致的记忆编辑弹窗");
assert(app.includes("openMemoryDialog"), "记忆查看/编辑应打开弹窗");
assert(!app.includes("window.prompt"), "记忆查看/编辑不能再使用浏览器 prompt");
assert(app.includes(".md"), "添加记忆文件应限制 Markdown 文件");
assert(
  /\.memory-dialog-field\s+textarea\s*\{[^}]*grid-area:\s*auto;/s.test(css),
  "记忆弹窗 textarea 必须覆盖全局输入框的 grid-area，避免被挤到右侧",
);
