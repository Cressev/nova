const assert = require("node:assert");
const fs = require("node:fs");

const app = fs.readFileSync("static/js/app.js", "utf8");

assert(app.includes('"background_tasks"'), "状态线默认项应该包含后台任务数");
assert(app.includes("background_task_count"), "状态线应该读取后端 background_task_count");
assert(app.includes("current_project_path"), "状态线应该读取后端 current_project_path");
assert(app.includes("后台任务"), "状态线应该用中文展示后台任务");
assert(app.includes("ensureStatuslineDefaults"), "旧浏览器本地配置也应该补齐新的状态线默认项");
