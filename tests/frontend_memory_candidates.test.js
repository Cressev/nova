const assert = require("node:assert");
const fs = require("node:fs");

const app = fs.readFileSync("static/js/app.js", "utf8");
const css = fs.readFileSync("static/css/styles.css", "utf8");

assert(app.includes("renderMemoryCandidate"), "右侧 Agent Context 应渲染待确认记忆候选卡片");
assert(app.includes("/api/memory/candidates/"), "前端应调用候选事实 approve/edit/deny API");
assert(app.includes("approveMemoryCandidate"), "候选事实应支持确认写入");
assert(app.includes("editMemoryCandidate"), "候选事实应支持编辑后确认");
assert(app.includes("denyMemoryCandidate"), "候选事实应支持拒绝且不写入");
assert(app.includes("memory_candidates"), "memory/status 返回的候选事实必须进入前端状态");
assert(/\.memory-candidate\s*\{[^}]*border:/s.test(css), "候选事实卡片应有独立视觉层级");
assert(/\.memory-candidate-actions\s*\{[^}]*grid-template-columns:/s.test(css), "候选事实动作区应有稳定布局");
