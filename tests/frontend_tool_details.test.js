const assert = require("node:assert");
const fs = require("node:fs");

const app = fs.readFileSync("static/js/app.js", "utf8");
const css = fs.readFileSync("static/css/styles.css", "utf8");

// dsh ToolRow 展开体：块族（Terminal/Diff/Read/Search/Web/IN-OUT）替代元数据卡
assert(app.includes("renderToolBody"), "工具行展开体应由变体路由到 dsh 块族");
assert(app.includes("renderTerminalBlockBody"), "bash/pwsh 展开体应是终端块（命令横幅 + 输出内滚）");
assert(app.includes("renderDiffBlockBody"), "write/edit 展开体应是 diff 块（+/- 行 + 增删统计）");
assert(app.includes("renderReadBlockBody"), "read 展开体应是等宽读块");
assert(app.includes("renderSearchBlockBody"), "grep/glob 展开体应是搜索块");
assert(app.includes("renderIoCardBody"), "其他工具展开体应是 IN/OUT 卡");
assert(app.includes('data-action="retry-tool"'), "失败工具展开体应保留重试入口");
assert(css.includes(".terminal-block"), "终端块需要样式（横幅固定 + 224px 内滚）");
assert(css.includes(".diff-block"), "diff 块需要样式");
assert(css.includes(".io-card"), "IN/OUT 卡需要样式");
console.log("frontend_tool_details ok");
