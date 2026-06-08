const assert = require("node:assert");
const fs = require("node:fs");

const app = fs.readFileSync("static/js/app.js", "utf8");

assert(
  app.includes("renderMessageLoadError"),
  "历史线程项目不可用时，前端应渲染错误提示而不是抛到控制台",
);
assert(
  /try\s*{[\s\S]*\/runtime-state[\s\S]*}\s*catch\s*\(/.test(app),
  "loadMessages 读取 runtime-state 应有 try/catch 兜底",
);
assert(
  app.includes("runtimeState.unavailable"),
  "后端返回降级 runtime-state 时，前端应显示历史线程不可用提示",
);
