const assert = require("node:assert");
const fs = require("node:fs");

const html = fs.readFileSync("static/index.html", "utf8");
const app = fs.readFileSync("static/js/app.js", "utf8");

for (const id of [
  "worktree-name",
  "worktree-create",
  "worktree-diff",
  "worktree-cleanup",
  "worktree-list",
  "worktree-diff-output",
]) {
  assert(html.includes(`id="${id}"`), `工作树 UI 应包含 #${id}`);
}

assert(app.includes("/api/worktrees"), "前端应加载工作树列表并创建工作树");
assert(app.includes("/api/worktrees/current/diff"), "前端应支持查看当前工作树 diff");
assert(/DELETE/.test(app) && /worktrees/.test(app), "前端应支持清理工作树");
assert(app.includes("renderWorktrees"), "前端应有独立的工作树渲染函数");
