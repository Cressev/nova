const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const appJs = fs.readFileSync(path.join(root, "static/js/app.js"), "utf8");
const styles = fs.readFileSync(path.join(root, "static/css/styles.css"), "utf8");

assert.match(appJs, /function renderHookContexts/);
assert.match(appJs, /Hook 追加上下文/);
assert.match(appJs, /renderHookContexts\(data\.hook_contexts\)/);
assert.match(appJs, /event\.data\?\.hook_contexts/);
assert.match(styles, /\.hook-contexts/);
