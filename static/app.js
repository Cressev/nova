const state = {
  selectedSessionId: null,
  selectedSessionTitle: "Nova Chat",
  sending: false,
};

const healthEl = document.querySelector("#health");
const providerEl = document.querySelector("#provider");
const newChatEl = document.querySelector("#new-chat");
const form = document.querySelector("#chat-form");
const messageEl = document.querySelector("#message");
const sendButtonEl = document.querySelector("#send-button");
const streamStateEl = document.querySelector("#stream-state");
const sessionListEl = document.querySelector("#session-list");
const messagesEl = document.querySelector("#messages");
const chatTitleEl = document.querySelector("#chat-title");

async function api(path, options = {}) {
  // 统一处理 API 错误，调用方只关注业务逻辑。
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  return response.json();
}

function formatTime(value) {
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function shortText(text, max = 64) {
  return text.length > max ? `${text.slice(0, max)}...` : text;
}

async function loadHealth() {
  try {
    // 只展示模型是否可用，不把密钥或敏感内容传到前端。
    const [health, provider] = await Promise.all([
      api("/api/health"),
      api("/api/provider"),
    ]);
    healthEl.textContent = health.ok ? "网关在线" : "网关异常";
    healthEl.className = health.ok ? "pill ready" : "pill warning";
    providerEl.textContent = provider.configured
      ? `${provider.model} 已连接`
      : `${provider.model} 未配置`;
    providerEl.className = provider.configured ? "pill ready" : "pill warning";
  } catch {
    healthEl.textContent = "网关离线";
    healthEl.className = "pill warning";
  }
}

async function loadSessions() {
  const sessions = await api("/api/chat/sessions");
  sessionListEl.innerHTML = "";

  if (sessions.length === 0) {
    sessionListEl.innerHTML = '<div class="section-label">暂无对话</div>';
    renderEmptyState();
    return;
  }

  if (!state.selectedSessionId) {
    state.selectedSessionId = sessions[0].id;
  }

  for (const session of sessions) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = `session-item ${session.id === state.selectedSessionId ? "active" : ""}`;
    item.innerHTML = `
      <strong>${shortText(session.title)}</strong>
      <span>${formatTime(session.updated_at)}</span>
    `;
    item.addEventListener("click", () => selectSession(session.id, session.title));
    sessionListEl.appendChild(item);
  }

  const selected = sessions.find((session) => session.id === state.selectedSessionId);
  if (selected) {
    state.selectedSessionTitle = selected.title;
    chatTitleEl.textContent = selected.title;
  }
  await loadMessages();
}

async function selectSession(sessionId, title) {
  state.selectedSessionId = sessionId;
  state.selectedSessionTitle = title || "Nova Chat";
  chatTitleEl.textContent = state.selectedSessionTitle;
  await Promise.all([loadSessions(), loadMessages()]);
}

function renderEmptyState() {
  messagesEl.innerHTML = `
    <div class="empty-state">
      <h3>开始和 Nova 对话</h3>
      <p>Nova 已接入本地网关和 GLM-4.7。先把对话跑顺，再逐步接入文件工具、审批和执行轨迹。</p>
      <div class="quick-actions">
        <button type="button" data-prompt="帮我总结一下这个项目现在做到哪一步">总结状态</button>
        <button type="button" data-prompt="下一步应该先实现哪个功能，为什么">规划下一步</button>
        <button type="button" data-prompt="解释一下 Nova 当前的后端结构">解释后端</button>
      </div>
    </div>
  `;
  for (const button of messagesEl.querySelectorAll("[data-prompt]")) {
    button.addEventListener("click", () => {
      messageEl.value = button.dataset.prompt;
      messageEl.focus();
    });
  }
}

async function loadMessages() {
  if (!state.selectedSessionId) {
    renderEmptyState();
    return;
  }
  const messages = await api(`/api/chat/sessions/${state.selectedSessionId}/messages`);

  if (messages.length === 0) {
    renderEmptyState();
    return;
  }

  messagesEl.innerHTML = "";
  for (const message of messages) {
    appendMessage(message);
  }
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function appendMessage(message) {
  const node = document.createElement("article");
  node.className = `message ${message.role}`;
  node.dataset.messageId = message.id || "";
  node.innerHTML = `
    <div class="message-role">${roleLabel(message.role)}</div>
    <div class="message-content">${escapeHtml(message.content || "")}</div>
    <div class="message-time">${message.created_at ? formatTime(message.created_at) : "生成中"}</div>
  `;
  messagesEl.appendChild(node);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return node;
}

function updateMessage(node, content) {
  node.querySelector(".message-content").innerHTML = escapeHtml(content);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function updateMessageMeta(node, message) {
  node.dataset.messageId = message.id || "";
  node.querySelector(".message-time").textContent = message.created_at
    ? formatTime(message.created_at)
    : "生成中";
}

function roleLabel(role) {
  return {
    user: "你",
    assistant: "Nova",
    error: "错误",
    system: "系统",
  }[role] || role;
}

function escapeHtml(value) {
  // 模型输出按纯文本渲染，避免 HTML 注入；换行单独转成 <br>。
  const div = document.createElement("div");
  div.textContent = value;
  return div.innerHTML.replaceAll("\n", "<br>");
}

async function ensureSession() {
  if (state.selectedSessionId) {
    return state.selectedSessionId;
  }
  const session = await api("/api/chat/sessions", {
    method: "POST",
    body: JSON.stringify({ title: "新对话" }),
  });
  state.selectedSessionId = session.id;
  state.selectedSessionTitle = session.title;
  chatTitleEl.textContent = session.title;
  return session.id;
}

newChatEl.addEventListener("click", async () => {
  const session = await api("/api/chat/sessions", {
    method: "POST",
    body: JSON.stringify({ title: "新对话" }),
  });
  state.selectedSessionId = session.id;
  state.selectedSessionTitle = session.title;
  chatTitleEl.textContent = session.title;
  await loadSessions();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const content = messageEl.value.trim();
  if (!content || state.sending) {
    return;
  }
  state.sending = true;
  sendButtonEl.disabled = true;
  sendButtonEl.querySelector(".send-label").textContent = "生成中";
  streamStateEl.textContent = "正在连接模型";

  let assistantNode = null;
  try {
    const sessionId = await ensureSession();
    const optimisticUser = {
      id: `local_user_${Date.now()}`,
      role: "user",
      content,
      created_at: new Date().toISOString(),
    };
    appendMessage(optimisticUser);
    assistantNode = appendMessage({
      id: `local_assistant_${Date.now()}`,
      role: "assistant",
      content: "",
      created_at: null,
    });
    assistantNode.classList.add("streaming");
    messageEl.value = "";
    autoResizeTextarea();
    const ok = await streamAssistant(sessionId, content, assistantNode);
    streamStateEl.textContent = ok ? "回复完成" : "请求失败";
    await Promise.all([loadSessions(), loadHealth()]);
  } catch (error) {
    const message = error instanceof Error ? error.message : "请求失败";
    if (assistantNode) {
      assistantNode.className = "message error";
      updateMessage(assistantNode, `请求失败：${message}`);
    } else {
      appendMessage({
        id: `local_error_${Date.now()}`,
        role: "error",
        content: `请求失败：${message}`,
        created_at: new Date().toISOString(),
      });
    }
    streamStateEl.textContent = "请求失败";
  } finally {
    state.sending = false;
    sendButtonEl.disabled = false;
    sendButtonEl.querySelector(".send-label").textContent = "发送";
    messageEl.focus();
  }
});

async function streamAssistant(sessionId, content, assistantNode) {
  const response = await fetch(`/api/chat/sessions/${sessionId}/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!response.ok || !response.body) {
    throw new Error(await response.text());
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let assistantText = "";
  let ok = true;

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const result = consumeStreamLines(buffer, assistantNode, (delta) => {
      assistantText += delta;
      updateMessage(assistantNode, assistantText);
      streamStateEl.textContent = "Nova 正在输出";
    });
    buffer = result.rest;
    ok = ok && result.ok;
  }

  if (buffer.trim()) {
    const result = consumeStreamLines(`${buffer}\n`, assistantNode, (delta) => {
      assistantText += delta;
      updateMessage(assistantNode, assistantText);
    });
    ok = ok && result.ok;
  }
  return ok;
}

function consumeStreamLines(buffer, assistantNode, onDelta) {
  const lines = buffer.split("\n");
  const rest = lines.pop() || "";
  let ok = true;

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
      onDelta(event.delta || "");
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
    if (event.type === "error") {
      ok = false;
      assistantNode.className = "message error";
      updateMessage(assistantNode, event.message?.content || "模型调用失败");
      if (event.message) {
        updateMessageMeta(assistantNode, event.message);
      }
    }
  }
  return { rest, ok };
}

messageEl.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    form.requestSubmit();
  }
});

messageEl.addEventListener("input", autoResizeTextarea);

function autoResizeTextarea() {
  // 输入区随内容长高，但限制最大高度，避免挤掉对话窗口。
  messageEl.style.height = "auto";
  messageEl.style.height = `${Math.min(messageEl.scrollHeight, 180)}px`;
}

loadHealth();
loadSessions();
