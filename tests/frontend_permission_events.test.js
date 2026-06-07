const assert = require("node:assert");

(async () => {
  const { consumeStreamLines } = await import("../static/js/runtime/stream.js");
  const seen = [];
  const assistantNode = {
    classList: { remove() {} },
  };

  consumeStreamLines(
    `${JSON.stringify({
      type: "permission_request",
      call_id: "tool_shell",
      tool: "shell_command",
      permission: "shell",
      title: "需要审批：shell_command",
      arguments: { command: "pwd" },
    })}\n`,
    assistantNode,
    {
      onPermissionRequest: (event) => seen.push(event),
    },
  );

  assert.strictEqual(seen.length, 1);
  assert.strictEqual(seen[0].tool, "shell_command");
  assert.strictEqual(seen[0].permission, "shell");
})();
