const assert = require("node:assert");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("static/app.js", "utf8");
const match = source.match(/function consumeStreamLines[\s\S]*?\n}\n\nmessageEl\.addEventListener/);

assert(match, "没有找到 consumeStreamLines 函数");

let appended = 0;
const sandbox = {
  updateMessage() {},
  updateMessageMeta() {},
  appendMessage() {
    appended += 1;
  },
};
vm.createContext(sandbox);
vm.runInContext(`${match[0].replace(/\n\nmessageEl\.addEventListener$/, "")}`, sandbox);

const queued = [];
const assistantNode = {
  classList: { remove() {} },
};

const result = sandbox.consumeStreamLines(
  `${JSON.stringify({
    type: "queued_message",
    message: {
      id: "msg_queued",
      role: "user",
      content: "第二条",
      created_at: "2026-06-07T10:00:00Z",
    },
  })}\n`,
  assistantNode,
  {
    onQueuedMessage: (event) => {
      queued.push(event.message);
      return assistantNode;
    },
  },
);

assert.strictEqual(result.ok, true);
assert.strictEqual(result.rest, "");
assert.strictEqual(appended, 0, "queued_message 不应该直接 append 造成重复气泡");
assert.strictEqual(queued.length, 1);
assert.strictEqual(queued[0].id, "msg_queued");
