const assert = require("node:assert");
const fs = require("node:fs");

const html = fs.readFileSync("static/index.html", "utf8");
const app = fs.readFileSync("static/js/app.js", "utf8");
const css = fs.readFileSync("static/css/styles.css", "utf8");

assert(html.includes('id="runtime-overview"'), "顶部线程条应包含统一运行态概览容器");
assert(app.includes("function buildRuntimeOverviewItems"), "前端应有 runtime-state 到概览项的映射函数");
for (const kind of ["turn", "tool", "approval", "process", "queue"]) {
  assert(app.includes(`kind: "${kind}"`), `运行态概览应覆盖 ${kind}`);
}
assert(app.includes("chip.dataset.runtimeKind"), "运行态芯片应带可测试的类型标记");
assert(app.includes("renderRuntimeOverview(runtimeState)"), "历史恢复应从后端 runtime-state 渲染概览");
assert(app.includes("renderRuntimeOverviewFromDom(\"running\")"), "流式运行中应实时刷新运行态概览");
assert(css.includes(".runtime-overview"), "运行态概览需要独立布局样式");
assert(css.includes(".runtime-chip.running") && css.includes(".runtime-chip.warning"), "运行态芯片应区分运行和待处理状态");
