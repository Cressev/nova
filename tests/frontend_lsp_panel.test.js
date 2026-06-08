const assert = require("node:assert");
const fs = require("node:fs");

const html = fs.readFileSync("static/index.html", "utf8");
const app = fs.readFileSync("static/js/app.js", "utf8");
const css = fs.readFileSync("static/css/styles.css", "utf8");

assert(html.includes('id="lsp-state"'), "右侧面板应该显示 LSP 状态");
assert(html.includes('id="lsp-list"'), "右侧面板应该显示 LSP 语言服务列表");
assert(app.includes("/api/lsp/status"), "前端应该读取 LSP 状态 API");
assert(app.includes("/api/lsp/diagnostics"), "前端应该提供诊断读取入口");
assert(app.includes("/api/lsp/definition"), "前端应该提供定义查找入口");
assert(app.includes("renderLspPanel"), "前端应该有独立的 LSP 面板渲染函数");
assert(css.includes(".lsp-panel"), "LSP 面板需要独立样式");
