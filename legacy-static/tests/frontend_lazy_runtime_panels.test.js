const assert = require("node:assert");
const fs = require("node:fs");

const app = fs.readFileSync("static/js/app.js", "utf8");

assert(app.includes("loadRuntimeShell"), "首屏应有轻量 runtime shell 加载函数");
assert(app.includes("scheduleRuntimeShellLoad"), "runtime shell 应延后调度，避免阻塞刷新首屏");
assert(app.includes("loadInspectorPanelDetails"), "低频 inspector 详情应按需加载");
assert(
  app.includes('api("/api/workspace/status?quick=true")'),
  "首屏工作区状态应使用 quick=true，避免同步等待完整工作区状态",
);
assert(
  app.includes("includePicker = true"),
  "工作区候选列表应可与状态加载解耦",
);

const shellStart = app.indexOf("async function loadRuntimeShell()");
const shellEnd = app.indexOf("async function loadRuntimePanels()", shellStart);
assert(shellStart >= 0 && shellEnd > shellStart, "loadRuntimeShell 应在完整 runtime panels 前独立定义");
const shellBody = app.slice(shellStart, shellEnd);

for (const slowEndpoint of ["/api/review/summary", "/api/lsp/status", "/api/skills/status"]) {
  assert(
    !shellBody.includes(slowEndpoint),
    `首屏轻量加载不应等待慢接口 ${slowEndpoint}`,
  );
}

const initBlock = app.slice(app.lastIndexOf("loadHealth();"));
assert(initBlock.includes("scheduleRuntimeShellLoad();"), "初始化应延后调度轻量 runtime shell");
assert(!initBlock.includes("loadRuntimeShell();"), "初始化不应同步启动 runtime shell 请求");
assert(
  initBlock.includes("loadWorkspaceStatus({ quick: true, includePicker: false });"),
  "初始化应只加载快速工作区状态，不同步加载候选目录",
);
assert(!initBlock.includes("api(\"/api/workspaces\")"), "初始化不应直接请求工作区候选列表");
assert(
  !initBlock.includes("loadRuntimePanels();"),
  "初始化不应直接加载完整 runtime panels",
);
assert(
  app.includes("loadInspectorPanelDetails(panel)"),
  "打开 inspector 弹窗时应按需加载当前面板详情",
);
assert(
  app.includes('loadWorkspaceStatus({ quick: false })'),
  "打开工作区详情时应补齐完整工作区状态",
);
assert(
  app.includes("window.setTimeout(scheduleIdle") || app.includes("window.setTimeout(() => scheduleIdle"),
  "runtime shell 应有显式延迟，避免 requestIdleCallback 在首屏过早触发",
);
assert(app.includes("SESSION_GROUP_PREVIEW_LIMIT"), "历史项目组首屏应限制渲染数量，避免刷新后生成过多 DOM");
assert(app.includes("selectVisibleSessionGroups"), "历史项目组应通过选择函数保留当前项目和最近项目");
assert(!app.includes("renderGitFiles"), "前端不应再保留 Git 专属渲染函数");
