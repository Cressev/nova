const state = {
  selectedSessionId: null,
  sending: false,
};

const healthEl = document.querySelector("#health");
const providerEl = document.querySelector("#provider");
const newChatEl = document.querySelector("#new-chat");
const form = document.querySelector("#chat-form");
const messageEl = document.querySelector("#message");
const sessionListEl = document.querySelector("#session-list");
const messagesEl = document.querySelector("#messages");

async function api(path, options = {}) {
  // 统一处理 API 错误，避免每个交互分散写重复逻辑。
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

function shortText(text, max = 90) {
  return text.length > max ? `${text.slice(0, max)}...` : text;
}

async function loadHealth() {
  try {
    // Provider 状态只展示是否已配置，不把密钥或敏感内容传到前端。
    const [health, provider] = await Promise.all([
      api("/api/health"),
      api("/api/provider"),
    ]);
    healthEl.textContent = health.ok ? "网关在线" : "异常";
    providerEl.textContent = provider.configured
      ? `${provider.model} 已配置`
      : `${provider.model} 未配置 key`;
  } catch {
    healthEl.textContent = "离线";
  }
}

async function loadSessions() {
  const sessions = await api("/api/chat/sessions");
  sessionListEl.innerHTML = "";
  if (sessions.length === 0) {
    sessionListEl.innerHTML = '<div class="empty">暂无对话。</div>';
    messagesEl.innerHTML = '<div class="empty">新建对话后即可开始聊天。</div>';
    return;
  }

  if (!state.selectedSessionId) {
    // 首次进入时自动选中最近会话，让页面像普通聊天工具一样可直接继续。
    state.selectedSessionId = sessions[0].id;
  }

  for (const session of sessions) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = `task-item ${session.id === state.selectedSessionId ? "active" : ""}`;
    item.innerHTML = `
      <strong>${shortText(session.title, 42)}</strong>
      <span>${formatTime(session.updated_at)}</span>
    `;
    item.addEventListener("click", () => selectSession(session.id));
    sessionListEl.appendChild(item);
  }

  await loadMessages();
}

async function selectSession(sessionId) {
  state.selectedSessionId = sessionId;
  await Promise.all([loadSessions(), loadMessages()]);
}

async function loadMessages() {
  if (!state.selectedSessionId) {
    return;
  }
  const messages = await api(`/api/chat/sessions/${state.selectedSessionId}/messages`);

  messagesEl.innerHTML = "";
  if (messages.length === 0) {
    messagesEl.innerHTML = '<div class="empty">这个对话还没有消息。</div>';
    return;
  }
  for (const message of messages) {
    const node = document.createElement("article");
    node.className = `message ${message.role}`;
    node.innerHTML = `
      <div class="message-role">${message.role}</div>
      <div class="message-content">${escapeHtml(message.content)}</div>
      <div class="message-time">${formatTime(message.created_at)}</div>
    `;
    messagesEl.appendChild(node);
  }
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function escapeHtml(value) {
  // 模型输出按纯文本渲染，避免 HTML 注入；换行单独转成 <br>。
  const div = document.createElement("div");
  div.textContent = value;
  return div.innerHTML.replaceAll("\n", "<br>");
}

newChatEl.addEventListener("click", async () => {
  const session = await api("/api/chat/sessions", {
    method: "POST",
    body: JSON.stringify({ title: "新对话" }),
  });
  state.selectedSessionId = session.id;
  await loadSessions();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const content = messageEl.value.trim();
  if (!content || !state.selectedSessionId || state.sending) {
    return;
  }
  state.sending = true;
  // 先清空输入框，降低用户重复点击时的误操作概率。
  messageEl.value = "";
  await api(`/api/chat/sessions/${state.selectedSessionId}/messages`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });
  state.sending = false;
  await Promise.all([loadSessions(), loadMessages(), loadHealth()]);
});

loadHealth();
loadSessions();
