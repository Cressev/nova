export function consumeStreamLines(buffer, assistantNode, handlers, renderers = {}) {
  const lines = buffer.split("\n");
  const rest = lines.pop() || "";
  let ok = true;
  const updateMessage = renderers.updateMessage || handlers.updateMessage || (() => {});
  const updateMessageMeta = renderers.updateMessageMeta || handlers.updateMessageMeta || (() => {});

  for (const line of lines) {
    if (!line.trim()) {
      continue;
    }
    let event;
    try {
      event = JSON.parse(line);
    } catch {
      continue;
    }
    if (event.type === "assistant_delta") {
      handlers.onDelta(event.delta || "");
    }
    if (event.type === "tool_start") {
      handlers.onToolStart?.(event);
    }
    if (event.type === "tool_done") {
      handlers.onToolDone?.(event);
    }
    if (event.type === "tool_output") {
      handlers.onToolOutput?.(event);
    }
    if (event.type === "permission_request") {
      handlers.onPermissionRequest?.(event);
    }
    if (event.type === "hook_start" || event.type === "hook_done") {
      handlers.onStatus?.({ status: event.title || "Hook 事件" });
    }
    if (event.type === "agent_status") {
      handlers.onStatus?.(event);
    }
    if (event.type === "runtime_event") {
      handlers.onRuntimeEvent?.(event.event || {});
    }
    if (event.type === "assistant_done") {
      assistantNode.classList.remove("streaming");
      if (event.message?.content) {
        updateMessage(assistantNode, event.message.content);
      }
      if (event.message) {
        updateMessageMeta(assistantNode, event.message);
      }
    }
    if (event.type === "queued_message") {
      const nextAssistantNode = handlers.onQueuedMessage?.(event);
      if (nextAssistantNode) {
        assistantNode = nextAssistantNode;
      }
      handlers.onStatus?.({ status: "新消息已排队" });
    }
    if (event.type === "error") {
      ok = false;
      assistantNode.className = "message error";
      updateMessage(assistantNode, event.message?.content || event.message || "模型调用失败");
      if (event.message && typeof event.message === "object") {
        updateMessageMeta(assistantNode, event.message);
      }
    }
  }

  return { ok, rest };
}
