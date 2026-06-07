const assert = require("node:assert");
const fs = require("node:fs");

const source = fs.readFileSync("static/app.js", "utf8");

assert(!source.includes('name="permission_mode"'), "设置页不应该暴露独立 permission_mode 下拉");
assert(source.includes('name="permission_preset"'), "设置页应该提供 Codex-like 权限预设");
for (const preset of ["read_only", "ask", "workspace_write", "plan", "bypass_permissions"]) {
  assert(source.includes(`renderPermissionOption("${preset}"`), `设置页缺少权限预设 ${preset}`);
}
assert(source.includes("permissionPresetFromConfig"), "设置页应能从当前配置反推权限预设状态");
assert(source.includes("derivePermissionConfig"), "保存配置时应从权限预设推导内部配置");
assert(source.includes("权限预设"), "设置页应使用用户可理解的权限预设文案");
