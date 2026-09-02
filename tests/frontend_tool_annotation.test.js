const assert = require("node:assert");
const fs = require("node:fs");

const app = fs.readFileSync("static/js/app.js", "utf8");
const css = fs.readFileSync("static/css/styles.css", "utf8");

// dsh ToolRow 形态：目的/摘要统一落在 .tool-summary 槽（五件套第 4 位）
assert(app.includes("toolPurposeText"), "工具行应该用统一函数选择 annotation/purpose/title");
assert(app.includes("event.data?.annotation"), "工具行摘要应该优先展示后端传来的 annotation");
assert(app.includes("tool-summary"), "工具行应该有摘要文本槽位（dsh 五件套）");
assert(css.includes(".tool-summary"), "工具摘要需要独立样式（灰色 14px 截断）");
assert(css.includes(".tool-row {"), "dsh 五件套行样式应存在");
assert(app.includes("renderToolRowHead"), "应该有统一的工具行渲染函数");
console.log("frontend_tool_annotation ok");
