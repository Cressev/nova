const assert = require("node:assert");

(async () => {
  const { BUILTIN_COMMANDS } = await import("../static/js/components/command_palette.js");
  const names = BUILTIN_COMMANDS.map((command) => command.name);
  assert(names.includes("/ps"), "命令面板应该保留 /ps 查看后台任务");
  assert(names.includes("/kill"), "命令面板应该保留 /kill 终止后台任务");
  assert(!names.includes("/jobs"), "命令面板不应该再显示 /jobs 重复查看命令");
  assert(!names.includes("/stop"), "命令面板不应该再显示 /stop 重复终止命令");
})();
