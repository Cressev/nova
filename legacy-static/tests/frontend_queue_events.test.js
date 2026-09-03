const assert = require("node:assert");

(async () => {
  const { consumeStreamLines } = await import("../static/js/runtime/stream.js");
  let appended = 0;
  const queued = [];
  const assistantNode = {
    classList: { remove() {} },
  };

  const result = consumeStreamLines(
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
      appendMessage() {
        appended += 1;
      },
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
})();
