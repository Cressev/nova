const assert = require("node:assert");
const fs = require("node:fs");

const html = fs.readFileSync("static/index.html", "utf8");
const app = fs.readFileSync("static/js/app.js", "utf8");
const css = fs.readFileSync("static/css/styles.css", "utf8");

assert(html.includes('id="memory-dialog"'), "应该提供主题一致的记忆编辑弹窗");
assert(app.includes("openMemoryDialog"), "记忆查看/编辑应打开弹窗");
assert(!app.includes("window.prompt"), "记忆查看/编辑不能再使用浏览器 prompt");
assert(app.includes(".md"), "添加记忆文件应限制 Markdown 文件");
assert(app.includes("添加人格文件"), "人格文件应有独立添加入口，不能混在记忆文件里");
assert(app.includes("/api/persona/files"), "人格文件应走独立 persona API");
assert(app.includes(".nova/persona"), "人格文件保存文案应指向 persona 目录");
assert(app.includes(".nova/memory"), "长期记忆文件仍应保留 memory 目录");
assert(html.includes("Agent Context"), "右侧面板应把人格与记忆合并为上下文，而不是只叫 Memory");
assert(
  /\.memory-dialog-field\s+textarea\s*\{[^}]*grid-area:\s*auto;/s.test(css),
  "记忆弹窗 textarea 必须覆盖全局输入框的 grid-area，避免被挤到右侧",
);
