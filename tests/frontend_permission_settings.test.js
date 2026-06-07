const assert = require("node:assert");
const fs = require("node:fs");

const source = fs.readFileSync("static/app.js", "utf8");

assert(!source.includes('name="permission_mode"'), "设置页不应该暴露独立 permission_mode 下拉");
for (const internalMode of ["plan", "accept_edits", "dont_ask", "bypass_permissions"]) {
  assert(!source.includes(`renderPermissionOption("${internalMode}"`), `设置页不应该显示内部权限模式 ${internalMode}`);
}
assert(source.includes("derivePermissionMode"), "保存配置时应从沙箱模式和审批策略推导内部 permission_mode");
