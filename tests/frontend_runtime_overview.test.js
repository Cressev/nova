const assert = require("node:assert");
const fs = require("node:fs");

const html = fs.readFileSync("static/index.html", "utf8");
const app = fs.readFileSync("static/js/app.js", "utf8");
const css = fs.readFileSync("static/css/styles.css", "utf8");

assert(html.includes('id="runtime-overview"'), "顶部线程条应包含统一运行态概览容器");
assert(html.includes('id="runtime-state-panel"'), "Run 面板应包含当前 session 运行态聚合容器");
assert(app.includes("function buildRuntimeOverviewItems"), "前端应有 runtime-state 到概览项的映射函数");
assert(app.includes("function renderRuntimeStatePanel"), "前端应把 runtime-state 渲染到 Run 面板");
for (const kind of ["turn", "tool", "approval", "process", "queue"]) {
  assert(app.includes(`kind: "${kind}"`), `运行态概览应覆盖 ${kind}`);
}
assert(app.includes("chip.dataset.runtimeKind"), "运行态芯片应带可测试的类型标记");
assert(app.includes("renderRuntimeOverview(runtimeState)"), "历史恢复应从后端 runtime-state 渲染概览");
assert(app.includes("renderRuntimeOverviewFromDom(\"running\")"), "流式运行中应实时刷新运行态概览");
assert(app.includes("function findPermissionNodeByCallId"), "审批卡应能按 call id 被工具卡复用");
assert(app.includes("existingPermissionNode") && app.includes("approval-linked"), "approve 后工具卡应复用审批卡节点");
assert(!app.includes("appendToolEvent(event, node.nextSibling)"), "approve 后不应在审批卡后面创建割裂的新工具卡");
assert(app.includes("function renderToolKeyParams"), "工具卡默认态应展示关键参数摘要");
assert(app.includes("stdout / stderr / result_json"), "工具详情应折叠展示 stdout、stderr 和 result_json");
assert(css.includes(".runtime-overview"), "运行态概览需要独立布局样式");
assert(css.includes(".runtime-state-panel"), "Run 面板运行态聚合需要独立样式");
assert(css.includes(".runtime-chip.running") && css.includes(".runtime-chip.warning"), "运行态芯片应区分运行和待处理状态");
