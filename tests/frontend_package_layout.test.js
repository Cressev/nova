const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const requiredFiles = [
  "static/js/app.js",
  "static/js/api/client.js",
  "static/js/state/storage.js",
  "static/js/runtime/stream.js",
  "static/js/ui/dom.js",
  "static/js/components/command_palette.js",
  "static/css/styles.css",
];

for (const file of requiredFiles) {
  assert(fs.existsSync(file), `前端重构后应存在 ${file}`);
  assert(fs.statSync(file).size > 0, `${file} 不能是空占位文件`);
}

const html = fs.readFileSync("static/index.html", "utf8");
assert(html.includes('/static/css/styles.css'), "HTML 应引用 static/css/styles.css");
assert(html.includes('/static/js/app.js'), "HTML 应引用 static/js/app.js");
assert(html.includes('type="module"'), "入口脚本应使用 ES module 以支持目录化拆分");

const app = fs.readFileSync("static/js/app.js", "utf8");
for (const modulePath of [
  "./api/client.js",
  "./state/storage.js",
  "./runtime/stream.js",
  "./ui/dom.js",
  "./components/command_palette.js",
]) {
  assert(app.includes(modulePath), `入口 app.js 应导入 ${modulePath}`);
}

for (const oldPath of ["static/app.js", "static/styles.css"]) {
  assert(!fs.existsSync(oldPath), `${oldPath} 不应继续作为前端入口文件`);
}

for (const dir of ["api", "state", "runtime", "ui", "components"]) {
  assert(fs.existsSync(path.join("static/js", dir)), `static/js/${dir} 目录应存在`);
}
