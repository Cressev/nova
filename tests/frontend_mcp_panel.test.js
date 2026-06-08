const assert = require("node:assert");
const fs = require("node:fs");

const html = fs.readFileSync("static/index.html", "utf8");
const app = fs.readFileSync("static/js/app.js", "utf8");
const css = fs.readFileSync("static/css/styles.css", "utf8");

assert(html.includes('id="mcp-state"'), "右侧 Tools 区域应该展示 MCP 连接状态");
assert(html.includes('id="mcp-list"'), "右侧 Tools 区域应该展示 MCP server/tools/resources 列表");
assert(app.includes("/api/mcp/status"), "前端应该读取 MCP 状态 API");
assert(app.includes("/api/mcp/tools/"), "前端应该提供 MCP demo tool 调用入口");
assert(app.includes("renderMcpPanel"), "前端应该有独立的 MCP 面板渲染函数");
assert(app.includes("mcp__demo__echo"), "前端应该能触发 demo MCP tool");
assert(css.includes(".mcp-panel"), "MCP 面板需要独立样式，避免混在普通工具 chip 里");
