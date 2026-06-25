const assert = require("node:assert");
const fs = require("node:fs");

const app = fs.readFileSync("static/js/app.js", "utf8");
const css = fs.readFileSync("static/css/styles.css", "utf8");

assert(app.includes("toolPurposeText"), "工具卡片应该用统一函数选择 annotation/purpose/title");
assert(app.includes("event.data?.annotation"), "工具卡片标题应该优先展示后端传来的 annotation");
assert(app.includes("tool-event-purpose"), "工具卡片应该有简短目的文本节点");
assert(css.includes(".tool-event-purpose"), "工具目的文本需要独立样式，避免和工具名堆在一起");
