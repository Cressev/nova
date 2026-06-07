const assert = require("node:assert");

(async () => {
  const { consumeStreamLines } = await import("../static/js/runtime/stream.js");
  const seen = [];
  const assistantNode = {
    classList: { remove() {} },
  };

  const result = consumeStreamLines(
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
})();
