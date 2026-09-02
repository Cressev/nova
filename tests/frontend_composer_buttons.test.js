const assert = require("node:assert");
const fs = require("node:fs");

const css = fs.readFileSync("static/css/styles.css", "utf8");

assert(css.includes("grid-auto-columns: max-content"), "composer 操作区按钮不应按大列宽拉伸");
assert(css.includes("align-self: end"), "composer 操作区应贴近输入区底部，而不是拉满整块高度");
assert(css.includes("justify-self: end"), "composer 操作区应右对齐，保持编辑器工具条体感");
assert(!css.includes("grid-auto-columns: minmax(88px, auto)"), "发送/排队按钮不能恢复旧的大列宽");
assert(!css.includes("min-width: 96px"), "发送/排队按钮不能恢复旧的大按钮宽度");
assert(css.includes("#send-button.queue"), "排队态仍需要独立视觉样式");
assert(
  /#send-button\.queue\s*\{[^}]*background:\s*var\(--dsw-static-deepseek-100\)/s.test(css),
  "排队态应使用 deepseek-100 淡蓝令牌底色，而不是高饱和大色块或硬编码色值",
);
assert(css.includes(".send-icon") && css.includes("font-size: 14px"), "发送/停止图标尺寸应压小，避免按钮显笨重");
