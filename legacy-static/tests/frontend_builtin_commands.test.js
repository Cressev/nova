const assert = require("node:assert");

(async () => {
  const { BUILTIN_COMMANDS, filterCommandMatches, nextCommandSelectionIndex } = await import("../static/js/components/command_palette.js");
  const names = BUILTIN_COMMANDS.map((command) => command.name);
  assert(names.includes("/ps"), "命令面板应该保留 /ps 查看后台任务");
  assert(names.includes("/kill"), "命令面板应该保留 /kill 终止后台任务");
  assert(names.includes("/jobs"), "命令面板应该展示 /jobs 别名，满足完整 Slash 清单");
  assert(names.includes("/stop"), "命令面板应该展示 /stop 别名，满足完整 Slash 清单");
  assert(
    BUILTIN_COMMANDS.find((command) => command.name === "/kill")?.argumentHint === "<后台任务ID>",
    "/kill 应展示后台任务 ID 参数提示",
  );
  assert(
    BUILTIN_COMMANDS.find((command) => command.name === "/remember")?.argumentHint === "<事实或偏好>",
    "/remember 应展示记忆写入参数提示",
  );
  assert.deepEqual(
    filterCommandMatches("/jo", BUILTIN_COMMANDS).map((command) => command.name),
    ["/jobs"],
    "输入 /jo 应只匹配 /jobs",
  );
  assert.equal(nextCommandSelectionIndex(-1, 1, "down"), 0, "首次向下选择第一项");
  assert.equal(nextCommandSelectionIndex(0, 2, "down"), 1, "向下移动到下一项");
  assert.equal(nextCommandSelectionIndex(1, 2, "down"), 0, "向下到末尾后循环");
  assert.equal(nextCommandSelectionIndex(0, 2, "up"), 1, "向上到开头前循环到末尾");
})();
