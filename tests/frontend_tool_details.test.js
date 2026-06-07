const assert = require("node:assert");
const fs = require("node:fs");

const app = fs.readFileSync("static/js/app.js", "utf8");
const css = fs.readFileSync("static/css/styles.css", "utf8");

assert(app.includes("renderToolMetadata"), "工具卡片应该渲染 schema、权限、风险和耗时等元数据");
assert(app.includes("renderDiffPreview"), "写文件和 apply_patch 工具应该渲染 diff preview");
assert(app.includes('data-action="retry-tool"'), "失败工具卡片应该提供重试入口");
assert(app.includes("duration_ms"), "工具卡片应该展示后端返回的执行耗时");
assert(css.includes(".tool-meta-grid"), "工具元数据需要紧凑网格样式");
assert(css.includes(".tool-diff-preview"), "diff preview 需要独立样式");
assert(css.includes(".tool-retry"), "重试按钮需要独立样式");
