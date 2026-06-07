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

const result = sandbox.consumeStreamLines(
  `${JSON.stringify({
    type: "runtime_event",
    event: {
      event_type: "turn.started",
      phase: "started",
      title: "开始处理用户请求",
      sequence: 1,
      turn_id: "turn_test",
    },
  })}\n`,
  assistantNode,
  {
    onRuntimeEvent: (event) => seen.push(event),
  },
);

assert.strictEqual(result.ok, true);
assert.deepStrictEqual(result.rest, "");
assert.strictEqual(seen.length, 1);
assert.strictEqual(seen[0].event_type, "turn.started");
