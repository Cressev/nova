const assert = require("node:assert");
const fs = require("node:fs");

const html = fs.readFileSync("static/index.html", "utf8");
const app = fs.readFileSync("static/js/app.js", "utf8");
const css = fs.readFileSync("static/css/styles.css", "utf8");

assert(html.includes('id="review-state"'), "右侧 Review 面板应该显示 review 状态");
assert(html.includes('id="review-summary"'), "右侧 Review 面板应该显示结构化 summary");
assert(html.includes('id="review-run-tests"'), "右侧 Review 面板应该有运行测试按钮");
assert(app.includes("/api/review/summary"), "前端应该读取 Review summary API");
assert(app.includes("/api/review/run-tests"), "前端应该调用 Review 测试运行 API");
assert(app.includes("renderReviewPanel"), "前端应该有独立的 Review 面板渲染函数");
assert(css.includes(".review-panel"), "Review 面板需要独立样式");
