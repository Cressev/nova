const assert = require("node:assert");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("static/app.js", "utf8");
const match = source.match(/function consumeStreamLines[\s\S]*?\n}\n\nmessageEl\.addEventListener/);

assert(match, "没有找到 consumeStreamLines 函数");

const sandbox = {
  updateMessage() {},
  updateMessageMeta() {},
};
vm.createContext(sandbox);
vm.runInContext(`${match[0].replace(/\n\nmessageEl\.addEventListener$/, "")}`, sandbox);

const seen = [];
const assistantNode = {
  classList: { remove() {} },
};

sandbox.consumeStreamLines(
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
