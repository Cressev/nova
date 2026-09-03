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
assert(
  app.includes("state.queuedMessages")
    && app.includes("setQueuedMessages")
    && app.includes("/queue/clear"),
  "排队输入应该有前端状态和真实清空队列接口",
);
assert(
  app.includes("removeQueuedMessage(message.id)")
    && app.includes("onQueuedMessage: handleQueuedMessage"),
  "排队消息开始执行时，前端应从队列状态移除，而不是一直显示为待执行",
);
assert(
  app.includes("syncBackgroundProcessFromToolDone")
    && app.includes("refreshProcessesPanel().catch"),
  "后台 shell 工具完成后应主动刷新 Processes 面板和状态线",
);
assert(
  app.includes("syncBackgroundProcessFromToolDone"),
  "后台 shell 工具完成后应同步进程面板（dsh 行式下 job 追踪走 Processes 面板，不做行内元数据卡）",
);
assert(
  app.includes("process.call_id") && app.includes("shortId(process.call_id)"),
  "Processes 面板应展示 call_id，方便从后台进程反查工具调用",
);
assert(
  app.includes("function processesEndpoint()")
    && app.includes("session_id=${encodeURIComponent(state.selectedSessionId)}")
    && app.includes("api(processesEndpoint())"),
  "Processes 面板应按当前 session 读取后台任务，避免不同会话任务混在一起",
);
