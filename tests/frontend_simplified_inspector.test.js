const assert = require("node:assert");
const fs = require("node:fs");

const html = fs.readFileSync("static/index.html", "utf8");
const app = fs.readFileSync("static/js/app.js", "utf8");
const css = fs.readFileSync("static/css/styles.css", "utf8");

const inspectorMatch = html.match(/<aside class="inspector">([\s\S]*?)<\/aside>/);
assert(inspectorMatch, "默认页面应该保留右侧 inspector 外壳");
const sidebarMatch = html.match(/<aside class="sidebar">([\s\S]*?)<\/aside>/);
assert(sidebarMatch, "默认页面应该保留左侧 sidebar 外壳");

const defaultInspector = inspectorMatch[1];
const sidebar = sidebarMatch[1];
const detailedSections = [
  "Workspace",
  "Review",
  "Sub Agents",
  "Run",
  "Processes",
  "Permissions",
  "Tools",
  "Memory",
  "Config",
];
const expandedCount = detailedSections.filter((label) => defaultInspector.includes(`<span>${label}</span>`)).length;

assert(
  expandedCount <= 3,
  `默认右侧不应展开全部详细功能，当前展开了 ${expandedCount} 个`,
);
for (const hiddenByDefault of ["Sub Agents", "Memory", "Config", "Processes"]) {
  assert(
    !defaultInspector.includes(hiddenByDefault),
    `低频功能 ${hiddenByDefault} 不应直接出现在首屏右侧`,
  );
}
assert(defaultInspector.includes("inspector-hub"), "默认右侧应该提供精简 command hub 入口");
assert(html.includes('id="inspector-dialog"'), "详细功能应该移动到弹窗或抽屉中");
assert(app.includes("openInspectorDialog"), "前端应该能按需打开详细 inspector 面板");
assert(css.includes(".inspector-hub"), "精简右侧入口需要独立样式");
assert(html.includes('id="workspace-open"'), "项目切换应该通过按需按钮打开弹窗");
assert(css.includes(".project-card .workspace-switcher"), "左侧不应常驻完整路径输入框");
assert(css.includes(".side-compact-card .mode-list"), "左侧不应常驻运行模式详情列表");
assert(css.includes(".side-compact-card .skill-list"), "左侧不应常驻技能详情列表");
assert(sidebar.includes("切换项目"), "左侧应保留项目切换入口");
assert(app.includes("SESSION_PREVIEW_LIMIT"), "历史线程默认应限制展示数量");
assert(app.includes("expandedSessionGroups"), "用户应能按需展开完整历史线程");
assert(css.includes(".session-more"), "历史线程展开入口需要独立样式");
