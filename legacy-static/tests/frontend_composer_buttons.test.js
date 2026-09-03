const assert = require("node:assert");
const fs = require("node:fs");

const css = fs.readFileSync("static/css/styles.css", "utf8");
const html = fs.readFileSync("static/index.html", "utf8");

// dsh 形态：composer 是大圆角输入卡 + 工具栏行（选择器 + 圆形发送键）
assert(css.includes(".composer-card"), "composer 输入卡样式应存在");
assert(css.includes("border-radius: 16px"), "composer 卡应为 16px 大圆角");
assert(css.includes(".round-button"), "圆形操作按钮样式应存在");
assert(css.includes(".round-button.send.queue"), "排队态仍需要独立视觉样式");
assert(css.includes(".toolbar-select"), "工具栏选择器（权限/模型）样式应存在");
assert(html.includes('id="permission-select"'), "composer 工具栏应有权限选择器");
assert(html.includes('id="model-select"'), "composer 工具栏应有模型选择器");
assert(html.includes("探索未至之境") || html.includes('id="empty-hero"'), "空状态 hero 应存在");
console.log("frontend_composer_buttons ok");
