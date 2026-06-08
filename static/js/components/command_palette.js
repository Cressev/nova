export const BUILTIN_COMMANDS = [
  { name: "/help", description: "查看内置指令说明", group: "help" },
  { name: "/status", description: "查看网关、模型、权限、工作区和 Git 状态", group: "runtime" },
  { name: "/model", description: "查看模型与 Base URL", group: "config" },
  { name: "/tools", description: "列出当前可用工具和并行能力", group: "tools" },
  { name: "/permissions", description: "查看权限模式和限制", group: "permissions" },
  { name: "/approvals", description: "查看审批策略", group: "permissions" },
  { name: "/sandbox", description: "查看沙箱模式", group: "permissions" },
  { name: "/memory", description: "查看项目记忆；支持 search / summarize / compact", argumentHint: "[search|summarize|compact] [关键词]", group: "memory" },
  { name: "/remember", description: "创建长期记忆候选，等待用户确认后写入", argumentHint: "<事实或偏好>", group: "memory" },
  { name: "/ps", description: "查看后台任务", group: "processes" },
  { name: "/jobs", description: "查看后台任务（/ps 的别名）", group: "processes" },
  { name: "/stop", description: "终止后台任务（/kill 的别名）", argumentHint: "<后台任务ID>", group: "processes" },
  { name: "/kill", description: "终止后台任务", argumentHint: "<后台任务ID>", group: "processes" },
  { name: "/review", description: "读取当前 diff 摘要", group: "review" },
  { name: "/plan", description: "先拆解任务再执行", argumentHint: "<目标和验收标准>", group: "planning" },
  { name: "/compact", description: "压缩当前会话并写入 session 记忆", argumentHint: "[压缩要求]", group: "context" },
  { name: "/clear", description: "创建空线程提示", group: "session" },
];

export function filterCommandMatches(value, commands = BUILTIN_COMMANDS) {
  const query = String(value || "").trimStart().split(/\s+/, 1)[0].toLowerCase();
  if (!query.startsWith("/")) {
    return [];
  }
  return commands.filter((command) => command.name.startsWith(query));
}

export function nextCommandSelectionIndex(currentIndex, count, direction) {
  if (count <= 0) {
    return -1;
  }
  if (direction === "up") {
    return currentIndex <= 0 ? count - 1 : currentIndex - 1;
  }
  return currentIndex < 0 || currentIndex >= count - 1 ? 0 : currentIndex + 1;
}
