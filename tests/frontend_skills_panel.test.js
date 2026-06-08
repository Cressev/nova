const assert = require("node:assert");
const fs = require("node:fs");

const html = fs.readFileSync("static/index.html", "utf8");
const app = fs.readFileSync("static/js/app.js", "utf8");
const css = fs.readFileSync("static/css/styles.css", "utf8");

assert(html.includes('id="skill-count"'), "侧边栏 Skills 应展示已发现技能数量");
assert(html.includes('id="skill-list"'), "侧边栏 Skills 应展示技能列表");
assert(app.includes("/api/skills/status"), "前端应读取技能状态 API");
assert(app.includes("/api/skills/"), "前端应能读取 SKILL.md 详情");
assert(app.includes("renderSkillsPanel"), "前端应有独立的 Skills 面板渲染函数");
assert(app.includes("data-skill-name"), "技能项应保留可触发的 skill name");
assert(app.includes("$"), "技能触发方式应在 UI 中体现为 $skill");
assert(css.includes(".skill-list"), "技能列表需要独立样式");
