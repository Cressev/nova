const assert = require("node:assert");
const fs = require("node:fs");

const app = fs.readFileSync("static/js/app.js", "utf8");

assert(app.includes("response.message"), "拒绝审批后前端必须读取后端返回的 assistant message");
assert(
  /appendMessage\(\s*response\.message/.test(app),
  "拒绝审批后前端必须把 assistant 替代路径渲染到消息流",
);
assert(app.includes("用户在页面拒绝执行"), "拒绝审批时应向后端传递用户拒绝原因");
