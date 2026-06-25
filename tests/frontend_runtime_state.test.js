const assert = require("node:assert");
const fs = require("node:fs");

const app = fs.readFileSync("static/js/app.js", "utf8");

assert(
  app.includes("/runtime-state"),
  "刷新历史线程时应读取 session runtime-state，而不是只读取 timeline",
);
assert(
  /runtimeState\.timeline\?\.items/.test(app),
  "历史渲染应从 runtime-state.timeline.items 恢复消息和事件",
);
assert(
  app.includes("appendRuntimeStateRestorations"),
  "刷新历史线程时应把 runtime-state 里的审批、后台任务和排队输入恢复到页面",
);
assert(
  app.includes("state.sessionActive") && app.includes("runtimeState.active"),
  "停止按钮和排队逻辑应同步后端 runtime-state.active，而不是只依赖本地 sending",
);
assert(
  app.includes("isTurnActive()"),
  "运行态判断应集中到 isTurnActive，覆盖本地 stream 和后端 active session",
);
assert(
  /runtimeState\.pending_approvals/.test(app)
    && /runtimeState\.processes/.test(app)
    && /runtimeState\.queued_messages/.test(app),
  "前端恢复逻辑应显式消费 pending_approvals、processes 和 queued_messages",
);
