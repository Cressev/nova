const assert = require("node:assert");
const fs = require("node:fs");

const html = fs.readFileSync("static/index.html", "utf8");
const app = fs.readFileSync("static/js/app.js", "utf8");
const css = fs.readFileSync("static/css/styles.css", "utf8");

assert(!html.includes('id="lsp-state"'), "v1 暂不做 LSP，右侧面板不应显示 LSP 状态");
assert(!html.includes('id="lsp-list"'), "v1 暂不做 LSP，右侧面板不应显示 LSP 列表");
assert(!app.includes("/api/lsp/status"), "v1 前端不应读取 LSP 状态 API");
assert(!app.includes("/api/lsp/diagnostics"), "v1 前端不应提供诊断读取入口");
assert(!app.includes("/api/lsp/definition"), "v1 前端不应提供定义查找入口");
assert(!app.includes("renderLspPanel"), "v1 前端不应保留 LSP 面板渲染函数");
assert(!css.includes(".lsp-panel"), "v1 前端不应保留 LSP 面板样式");
