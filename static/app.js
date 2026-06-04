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
      <p>Nova 已经接入本地网关。你可以直接让它解释代码、拆解需求、制定实现步骤，后续会继续接入文件工具和审批流。</p>
      <div class="quick-actions">
        <button type="button" data-prompt="帮我总结一下这个项目现在做到哪一步">总结项目状态</button>
        <button type="button" data-prompt="下一步应该先实现哪个功能，为什么">规划下一步</button>
        <button type="button" data-prompt="解释一下 Nova 当前的后端结构">解释后端结构</button>
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
    const node = document.createElement("article");
    node.className = `message ${message.role}`;
    node.innerHTML = `
      <div class="message-role">${roleLabel(message.role)}</div>
      <div class="message-content">${escapeHtml(message.content)}</div>
      <div class="message-time">${formatTime(message.created_at)}</div>
    `;
    messagesEl.appendChild(node);
  }
  messagesEl.scrollTop = messagesEl.scrollHeight;
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
  sendButtonEl.textContent = "发送中";

  try {
    const sessionId = await ensureSession();
    messageEl.value = "";
    await api(`/api/chat/sessions/${sessionId}/messages`, {
      method: "POST",
      body: JSON.stringify({ content }),
    });
    await Promise.all([loadSessions(), loadMessages(), loadHealth()]);
  } finally {
    state.sending = false;
    sendButtonEl.disabled = false;
    sendButtonEl.textContent = "发送";
    messageEl.focus();
  }
});

messageEl.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    form.requestSubmit();
  }
});

loadHealth();
loadSessions();
