const state = {
  selectedSessionId: null,
  selectedSessionTitle: "Nova Chat",
  sending: false,
  collapsedProjects: new Set(readStorageList("nova.collapsedProjects")),
  workspaceCandidates: [],
  workspaceSuggestionIndex: -1,
  workspaceDialogCandidates: [],
  workspaceDialogIndex: -1,
  workspaceDialogRequestId: 0,
  messagesRequestId: 0,
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
const projectNameEl = document.querySelector("#project-name");
const projectRootEl = document.querySelector("#project-root");
const workspacePathEl = document.querySelector("#workspace-path");
const gitBranchEl = document.querySelector("#git-branch");
const dirtyCountEl = document.querySelector("#dirty-count");
const gitFilesEl = document.querySelector("#git-files");
const modeListEl = document.querySelector("#mode-list");
const modePillEl = document.querySelector("#mode-pill");
const permissionsListEl = document.querySelector("#permissions-list");
const testCommandEl = document.querySelector("#test-command");
const serveCommandEl = document.querySelector("#serve-command");
const commandPaletteEl = document.querySelector("#command-palette");
const toolCountEl = document.querySelector("#tool-count");
const toolListEl = document.querySelector("#tool-list");
const memoryStateEl = document.querySelector("#memory-state");
const memoryListEl = document.querySelector("#memory-list");
const configStateEl = document.querySelector("#config-state");
const configListEl = document.querySelector("#config-list");
const workspaceFormEl = document.querySelector("#workspace-form");
const workspaceInputEl = document.querySelector("#workspace-input");
const workspaceCandidatesEl = document.querySelector("#workspace-candidates");
const workspaceSuggestionsEl = document.querySelector("#workspace-suggestions");
const workspaceDialogEl = document.querySelector("#workspace-dialog");
const workspaceDialogInputEl = document.querySelector("#workspace-dialog-input");
const workspaceDialogListEl = document.querySelector("#workspace-dialog-list");
const workspaceDialogCloseEl = document.querySelector("#workspace-dialog-close");
const workspaceDialogSubmitEl = document.querySelector("#workspace-dialog-submit");
const workspaceDialogCreateEl = document.querySelector("#workspace-dialog-create");
const messageRailEl = document.querySelector("#message-rail");
let workspaceSuggestTimer = null;
let workspaceDialogTimer = null;

function readStorageList(key) {
  try {
    const value = JSON.parse(localStorage.getItem(key) || "[]");
    return Array.isArray(value) ? value : [];
  } catch {
    return [];
  }
}

function writeStorageList(key, values) {
  localStorage.setItem(key, JSON.stringify(Array.from(values)));
}

const BUILTIN_COMMANDS = [
  { name: "/status", description: "查看网关、权限和 Git 状态" },
  { name: "/tools", description: "列出当前可用工具和并行能力" },
  { name: "/permissions", description: "查看权限模式和限制" },
  { name: "/memory", description: "查看项目记忆注入状态" },
  { name: "/review", description: "读取当前 diff 摘要" },
  { name: "/plan", description: "先拆解任务再执行" },
  { name: "/help", description: "查看内置指令说明" },
];
let commandMatches = [];

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

function scrollMessagesToBottom() {
  messagesEl.scrollTo({ top: messagesEl.scrollHeight, behavior: "smooth" });
}

function projectName(path) {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts.at(-1) || "Nova";
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

async function loadWorkspaceStatus() {
  try {
    const [status, workspaces] = await Promise.all([
      api("/api/workspace/status"),
      api("/api/workspaces"),
    ]);
    renderWorkspace(status);
    renderWorkspacePicker(workspaces);
  } catch (error) {
    workspacePathEl.textContent = "工作区状态读取失败";
    dirtyCountEl.textContent = "-";
  }
}

async function loadWorkspaceCandidates(query = "") {
  const suffix = query ? `?q=${encodeURIComponent(query)}` : "";
  const workspaces = await api(`/api/workspaces${suffix}`);
  renderWorkspacePicker(workspaces);
  return workspaces;
}

async function loadRuntimePanels() {
  const [config, tools, memory] = await Promise.all([
    api("/api/runtime/config"),
    api("/api/tools"),
    api("/api/memory/status"),
  ]);
  renderRuntimeConfig(config);
  renderTools(tools.items || []);
  renderMemory(memory);
}

function renderWorkspace(status) {
  projectNameEl.textContent = projectName(status.project_root);
  projectRootEl.textContent = status.project_root;
  workspaceInputEl.value = status.project_root;
  workspacePathEl.textContent = status.project_root;
  gitBranchEl.textContent = status.git.available ? status.git.branch || "detached" : "no git";
  dirtyCountEl.textContent = String(status.git.dirty_count);

  const localMode = status.modes.find((mode) => mode.id === "local");
  modePillEl.textContent = localMode?.enabled ? "本地模式" : "模式未就绪";
  modePillEl.className = localMode?.enabled ? "pill ready" : "pill warning";

  modeListEl.innerHTML = "";
  for (const mode of status.modes) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = `mode-item ${mode.enabled ? "enabled" : "disabled"} ${mode.id === "local" ? "active" : ""}`;
    item.disabled = !mode.enabled;
    item.innerHTML = `
      <strong>${mode.label}</strong>
      <span>${mode.description}</span>
    `;
    modeListEl.appendChild(item);
  }

  renderGitFiles(status.git);
  renderPermissions(status.permissions);
  bindCommandChip(testCommandEl, status.commands.test, "运行测试");
  bindCommandChip(serveCommandEl, status.commands.serve, "启动服务");
}

function renderWorkspacePicker(workspaces) {
  state.workspaceCandidates = workspaces.candidates || [];
  workspaceCandidatesEl.innerHTML = "";
  for (const path of state.workspaceCandidates) {
    const option = document.createElement("option");
    option.value = path;
    workspaceCandidatesEl.appendChild(option);
  }
  renderWorkspaceDialogList();
  renderWorkspaceSuggestions();
}

function renderGitFiles(git) {
  if (!git.available) {
    gitFilesEl.innerHTML = '<p class="muted">当前目录不是 Git 仓库。</p>';
    return;
  }
  if (git.files.length === 0) {
    gitFilesEl.innerHTML = '<p class="muted">工作区干净。</p>';
    return;
  }
  gitFilesEl.innerHTML = "";
  for (const file of git.files.slice(0, 12)) {
    const item = document.createElement("div");
    item.className = "git-file";
    item.innerHTML = `
      <span>${escapeHtml(file.status)}</span>
      <strong title="${escapeHtml(file.path)}">${escapeHtml(file.path)}</strong>
    `;
    gitFilesEl.appendChild(item);
  }
}

function renderPermissions(permissions) {
  permissionsListEl.innerHTML = "";
  const rows = [
    ["工作区写入", permissions.workspace_write ? "允许" : "只读"],
    ["网络访问", permissions.network_access ? "允许" : "关闭"],
    ["审批策略", permissions.approval_policy],
    ["权限模式", permissions.permission_mode],
    ["Shell", permissions.shell_commands ? "受控允许" : "关闭"],
  ];
  for (const [label, value] of rows) {
    const item = document.createElement("div");
    item.className = "permission-row";
    item.innerHTML = `<span>${label}</span><strong>${value}</strong>`;
    permissionsListEl.appendChild(item);
  }
}

function renderRuntimeConfig(config) {
  configStateEl.textContent = config.permission_mode;
  const rows = [
    ["模型", config.model],
    ["工具轮次", String(config.max_tool_rounds)],
    ["只读并行", config.tool_parallel_readonly ? "已启用" : "关闭"],
    ["审批 UI", config.approval_ui_enabled ? "已启用" : "未实现"],
    ["工作树", config.worktree_enabled ? "已启用" : "未实现"],
  ];
  renderKeyValueRows(configListEl, rows);
}

function renderTools(items) {
  toolCountEl.textContent = `${items.length}`;
  toolListEl.innerHTML = "";
  for (const item of items) {
    const node = document.createElement("button");
    node.type = "button";
    node.className = "tool-chip";
    node.innerHTML = `
      <strong>${escapeHtml(item.name)}</strong>
      <span>${item.supports_parallel ? "并行" : item.permission}</span>
    `;
    node.title = item.description;
    node.addEventListener("click", () => {
      messageEl.value = `/tools`;
      autoResizeTextarea();
      messageEl.focus();
    });
    toolListEl.appendChild(node);
  }
}

function renderMemory(memory) {
  memoryStateEl.textContent = memory.enabled ? "已启用" : "关闭";
  memoryListEl.innerHTML = "";
  const globalSource = memory.global ? [memory.global] : [];
  const projectSource = memory.project ? [memory.project] : [];
  appendMemoryGroup("给开发 Agent：全局", globalSource);
  appendMemoryGroup("给开发 Agent：项目", projectSource);
  appendMemoryGroup("只给 Nova 开发过程", memory.development_state || []);
}

function appendMemoryGroup(title, items) {
  const heading = document.createElement("div");
  heading.className = "memory-heading";
  heading.textContent = title;
  memoryListEl.appendChild(heading);
  for (const item of items) {
    const row = document.createElement("div");
    row.className = `memory-row ${item.injected ? "injected" : "ignored"}`;
    row.innerHTML = `
      <span title="${escapeHtml(item.path)}">${escapeHtml(shortPath(item.path))}</span>
      <strong>${memoryLabel(item)}</strong>
    `;
    memoryListEl.appendChild(row);
  }
}

function memoryLabel(item) {
  if (!item.exists) {
    return "缺失";
  }
  return item.injected ? "注入" : "不注入";
}

function shortPath(path) {
  const parts = String(path || "").split(/[\\/]/).filter(Boolean);
  return parts.slice(-2).join("/") || path;
}

function renderKeyValueRows(container, rows) {
  container.innerHTML = "";
  for (const [label, value] of rows) {
    const item = document.createElement("div");
    item.className = "permission-row";
    item.innerHTML = `<span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong>`;
    container.appendChild(item);
  }
}

function bindCommandChip(node, command, label) {
  node.textContent = label;
  node.title = command;
  node.onclick = () => {
    messageEl.value = `请执行并验证：${command}`;
    autoResizeTextarea();
    messageEl.focus();
  };
}

async function loadSessions({ refreshMessages = true } = {}) {
  const sessions = await api("/api/chat/sessions");
  sessionListEl.innerHTML = "";

  if (sessions.length === 0) {
    sessionListEl.innerHTML = '<div class="section-label">暂无对话</div>';
    state.selectedSessionId = null;
    state.selectedSessionTitle = "Nova Chat";
    chatTitleEl.textContent = state.selectedSessionTitle;
    if (refreshMessages) {
      renderEmptyState();
    }
    return;
  }

  if (!state.selectedSessionId || !sessions.some((session) => session.id === state.selectedSessionId)) {
    state.selectedSessionId = sessions[0].id;
  }

  const groups = groupSessionsByProject(sessions);
  for (const group of groups) {
    const groupNode = document.createElement("section");
    groupNode.className = "session-group";
    const collapsed = state.collapsedProjects.has(group.workspace);
    const activeInGroup = group.sessions.some((session) => session.id === state.selectedSessionId);
    groupNode.innerHTML = `
      <button class="session-group-head ${activeInGroup ? "active" : ""}" type="button" aria-expanded="${!collapsed}">
        <span aria-hidden="true">${collapsed ? "▸" : "▾"}</span>
        <strong>${escapeHtml(group.name)}</strong>
        <em>${group.sessions.length}</em>
      </button>
      <div class="session-group-items" ${collapsed ? "hidden" : ""}></div>
    `;
    groupNode.querySelector(".session-group-head").addEventListener("click", (event) => {
      event.preventDefault();
      toggleSessionGroup(groupNode, group.workspace);
    });
    const itemsEl = groupNode.querySelector(".session-group-items");
    for (const session of group.sessions) {
      itemsEl.appendChild(renderSessionItem(session));
    }
    sessionListEl.appendChild(groupNode);
  }

  const selected = sessions.find((session) => session.id === state.selectedSessionId);
  if (selected) {
    state.selectedSessionTitle = selected.title;
    chatTitleEl.textContent = selected.title;
  }
  if (refreshMessages) {
    await loadMessages();
  }
}

function toggleSessionGroup(groupNode, workspace) {
  const head = groupNode.querySelector(".session-group-head");
  const items = groupNode.querySelector(".session-group-items");
  const arrow = head?.querySelector("span");
  const collapsed = !state.collapsedProjects.has(workspace);
  if (collapsed) {
    state.collapsedProjects.add(workspace);
  } else {
    state.collapsedProjects.delete(workspace);
  }
  if (items) {
    items.hidden = collapsed;
  }
  if (arrow) {
    arrow.textContent = collapsed ? "▸" : "▾";
  }
  head?.setAttribute("aria-expanded", String(!collapsed));
  writeStorageList("nova.collapsedProjects", state.collapsedProjects);
}

function groupSessionsByProject(sessions) {
  const map = new Map();
  for (const session of sessions) {
    const workspace = session.workspace || "unknown";
    if (!map.has(workspace)) {
      map.set(workspace, {
        workspace,
        name: projectName(workspace),
        sessions: [],
        updated_at: session.updated_at,
      });
    }
    const group = map.get(workspace);
    group.sessions.push(session);
    if (new Date(session.updated_at) > new Date(group.updated_at)) {
      group.updated_at = session.updated_at;
    }
  }
  return Array.from(map.values()).sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));
}

function renderSessionItem(session) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = `session-item ${session.id === state.selectedSessionId ? "active" : ""}`;
    item.innerHTML = `
      <span class="session-main">
        <strong>${shortText(session.title)}</strong>
        <small>${shortText(projectName(session.workspace || ""), 28)}</small>
        <span>${formatTime(session.updated_at)}</span>
      </span>
      <button class="session-delete" type="button" aria-label="删除对话" title="删除对话">×</button>
    `;
    item.addEventListener("click", () => selectSession(session.id, session.title));
    item.querySelector(".session-delete").addEventListener("click", async (event) => {
      event.stopPropagation();
      await deleteSession(session.id);
    });
  return item;
}

async function deleteSession(sessionId) {
  const response = await fetch(`/api/chat/sessions/${sessionId}`, { method: "DELETE" });
  if (!response.ok) {
    streamStateEl.textContent = "删除对话失败";
    return;
  }
  if (state.selectedSessionId === sessionId) {
    state.selectedSessionId = null;
  }
  await loadSessions();
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
      <h3>启动一个开发线程</h3>
      <p>像使用 Codex 一样，把目标、上下文、约束和验收标准写进同一个 thread。右侧会持续显示项目、Git 和验证状态。</p>
      <div class="quick-actions">
        <button type="button" data-prompt="/plan 帮我把下一个开发任务拆成可执行步骤">/plan</button>
        <button type="button" data-prompt="/review 检查当前未提交变更">/review</button>
        <button type="button" data-prompt="/status 总结当前线程、模型和工作区状态">/status</button>
      </div>
    </div>
  `;
  for (const button of messagesEl.querySelectorAll("[data-prompt]")) {
    button.addEventListener("click", () => {
      messageEl.value = button.dataset.prompt;
      messageEl.focus();
      autoResizeTextarea();
      updateCommandPalette();
    });
  }
  renderMessageRail();
}

async function loadMessages() {
  if (!state.selectedSessionId) {
    renderEmptyState();
    return;
  }
  const sessionId = state.selectedSessionId;
  const requestId = ++state.messagesRequestId;
  const timeline = await api(`/api/chat/sessions/${sessionId}/timeline`);
  if (requestId !== state.messagesRequestId || sessionId !== state.selectedSessionId) {
    return;
  }
  const items = timeline.items || [];

  if (items.length === 0) {
    renderEmptyState();
    return;
  }

  messagesEl.innerHTML = "";
  let userMessageCount = 0;
  for (const entry of items) {
    if (entry.kind === "message") {
      const message = entry.item;
      appendMessage(message, { showDivider: message.role === "user" && userMessageCount > 0 });
      if (message.role === "user") {
        userMessageCount += 1;
      }
      continue;
    }
    if (entry.kind === "event") {
      appendStoredEvent(entry.item);
    }
  }
  updateAllTurnToolControls();
  renderMessageRail();
  scrollMessagesToBottom();
}

function appendStoredEvent(event) {
  if (event.type === "tool") {
    const node = appendToolEvent(
      {
        call_id: event.id,
        tool: event.tool,
        arguments: event.arguments || {},
        title: event.title,
        parallel: event.parallel,
      },
      null,
      { autoscroll: false },
    );
    finishToolEvent(
      node,
      {
        call_id: event.id,
        tool: event.tool,
        ok: event.status === "ok",
        title: event.title,
        output: event.output || "",
        data: event.data || {},
      },
      { autoscroll: false },
    );
    return;
  }
  if (event.type === "status") {
    appendStatusEvent(event.title, { autoscroll: false });
  }
}

function appendStatusEvent(text, options = {}) {
  const node = document.createElement("div");
  node.className = "agent-status";
  node.textContent = text;
  if (options.beforeNode?.parentElement === messagesEl) {
    messagesEl.insertBefore(node, options.beforeNode);
  } else {
    messagesEl.appendChild(node);
  }
  if (options.autoscroll !== false) {
    scrollMessagesToBottom();
  }
  return node;
}

function appendTurnDivider(message) {
  const targetId = `message-${message.id || Date.now()}`;
  const divider = document.createElement("button");
  divider.type = "button";
  divider.className = "turn-divider";
  divider.innerHTML = `
    <span></span>
    <strong>${escapeHtml(shortText(message.content || "历史提问", 72))}</strong>
    <em>${message.created_at ? formatTime(message.created_at) : "刚刚"}</em>
  `;
  divider.addEventListener("click", () => {
    document.querySelector(`#${targetId}`)?.scrollIntoView({
      block: "start",
      behavior: "smooth",
    });
  });
  messagesEl.appendChild(divider);
  return targetId;
}

function appendMessage(message, options = {}) {
  const targetId = options.showDivider && message.role === "user"
    ? appendTurnDivider(message)
    : `message-${message.id || Date.now()}`;
  const node = document.createElement("article");
  node.className = `message ${message.role}`;
  node.id = targetId;
  node.dataset.messageId = message.id || "";
  node.innerHTML = `
    <div class="message-head">
      <div class="message-role">${roleLabel(message.role)}</div>
      ${message.role === "assistant" ? '<button class="turn-tools-toggle" type="button" hidden>收起过程</button>' : ""}
    </div>
    <div class="message-content">${escapeHtml(message.content || "")}</div>
    <div class="message-time">${message.created_at ? formatTime(message.created_at) : "生成中"}</div>
  `;
  if (message.role === "assistant") {
    setupTurnToolToggle(node);
  }
  messagesEl.appendChild(node);
  if (message.role === "user") {
    renderMessageRail();
  }
  scrollMessagesToBottom();
  return node;
}

function updateMessage(node, content) {
  node.querySelector(".message-content").innerHTML = escapeHtml(content);
  scrollMessagesToBottom();
}

function updateMessageMeta(node, message) {
  node.dataset.messageId = message.id || "";
  node.querySelector(".message-time").textContent = message.created_at
    ? formatTime(message.created_at)
    : "生成中";
}

function appendToolEvent(event, beforeNode = null, options = {}) {
  const node = document.createElement("article");
  node.className = "tool-event running";
  node.dataset.callId = event.call_id || "";
  node.dataset.tool = event.tool || "";
  node.dataset.arguments = JSON.stringify(event.arguments || {}, null, 2);
  node.innerHTML = `
    <div class="tool-event-head">
      <span>${escapeHtml(event.tool || "tool")}</span>
      <strong>${escapeHtml(event.title || "工具执行中")}</strong>
      <em>${event.parallel ? "并行" : "运行中"}</em>
    </div>
    <details open>
      <summary>调用参数</summary>
      <pre>${escapeHtml(node.dataset.arguments)}</pre>
    </details>
  `;
  if (beforeNode?.parentElement === messagesEl) {
    messagesEl.insertBefore(node, beforeNode);
  } else {
    messagesEl.appendChild(node);
  }
  if (options.autoscroll !== false) {
    scrollMessagesToBottom();
  }
  return node;
}

function finishToolEvent(node, event, options = {}) {
  if (!node) {
    node = appendToolEvent(event);
  }
  node.className = `tool-event ${event.ok ? "ok" : "failed"}`;
  const args = node.dataset.arguments || "{}";
  node.innerHTML = `
    <div class="tool-event-head">
      <span>${escapeHtml(event.tool || "tool")}</span>
      <strong>${escapeHtml(event.title || "工具完成")}</strong>
      <em>${event.ok ? "完成" : "失败"}</em>
    </div>
    <details class="tool-args" open>
      <summary>调用参数</summary>
      <pre>${escapeHtml(args)}</pre>
    </details>
    <details class="tool-result">
      <summary>工具结果</summary>
      <pre>${escapeHtml(shortText(event.output || "", 4000))}</pre>
    </details>
  `;
  if (options.autoscroll !== false) {
    scrollMessagesToBottom();
  }
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
    body: JSON.stringify({ title: "新线程" }),
  });
  state.selectedSessionId = session.id;
  state.selectedSessionTitle = session.title;
  chatTitleEl.textContent = session.title;
  await loadSessions();
});

workspaceFormEl.addEventListener("submit", async (event) => {
  event.preventDefault();
  const path = workspaceInputEl.value.trim();
  if (!path) {
    openWorkspaceDialog();
    return;
  }
  if (path === projectRootEl.textContent.trim()) {
    openWorkspaceDialog();
    return;
  }
  await switchWorkspace(path);
});

workspaceInputEl.addEventListener("focus", () => {
  scheduleWorkspaceSuggestions(0);
});

workspaceInputEl.addEventListener("input", () => {
  state.workspaceSuggestionIndex = -1;
  scheduleWorkspaceSuggestions(160);
});

workspaceInputEl.addEventListener("keydown", async (event) => {
  const suggestions = Array.from(workspaceSuggestionsEl.querySelectorAll("button"));
  if (event.key === "ArrowDown" && suggestions.length > 0) {
    event.preventDefault();
    state.workspaceSuggestionIndex = Math.min(state.workspaceSuggestionIndex + 1, suggestions.length - 1);
    renderWorkspaceSuggestionActive();
    return;
  }
  if (event.key === "ArrowUp" && suggestions.length > 0) {
    event.preventDefault();
    state.workspaceSuggestionIndex = Math.max(state.workspaceSuggestionIndex - 1, 0);
    renderWorkspaceSuggestionActive();
    return;
  }
  if ((event.key === "Enter" || event.key === "Tab") && !workspaceSuggestionsEl.hidden && suggestions.length > 0) {
    const index = state.workspaceSuggestionIndex >= 0 ? state.workspaceSuggestionIndex : 0;
    const path = suggestions[index]?.dataset.path;
    if (path) {
      event.preventDefault();
      workspaceInputEl.value = path;
      workspaceSuggestionsEl.hidden = true;
      if (event.key === "Enter") {
        await switchWorkspace(path);
      }
    }
  }
});

workspaceInputEl.addEventListener("dblclick", openWorkspaceDialog);

workspaceDialogInputEl.addEventListener("input", () => {
  state.workspaceDialogIndex = -1;
  scheduleWorkspaceDialogCandidates(120);
});

workspaceDialogInputEl.addEventListener("keydown", async (event) => {
  const items = Array.from(workspaceDialogListEl.querySelectorAll("button[data-path]"));
  if (event.key === "ArrowDown" && items.length > 0) {
    event.preventDefault();
    state.workspaceDialogIndex = Math.min(state.workspaceDialogIndex + 1, items.length - 1);
    renderWorkspaceDialogActive();
    return;
  }
  if (event.key === "ArrowUp" && items.length > 0) {
    event.preventDefault();
    state.workspaceDialogIndex = Math.max(state.workspaceDialogIndex - 1, 0);
    renderWorkspaceDialogActive();
    return;
  }
  if (event.key === "Tab" && items.length > 0) {
    event.preventDefault();
    selectWorkspaceDialogCandidate(items[state.workspaceDialogIndex >= 0 ? state.workspaceDialogIndex : 0].dataset.path);
    return;
  }
  if (event.key === "Enter") {
    event.preventDefault();
    const path = items[state.workspaceDialogIndex]?.dataset.path || workspaceDialogInputEl.value.trim();
    if (path && state.workspaceDialogIndex >= 0) {
      selectWorkspaceDialogCandidate(path);
      return;
    }
    await switchWorkspaceFromDialog();
  }
});

workspaceDialogCloseEl.addEventListener("click", () => {
  workspaceDialogEl.close();
});

workspaceDialogSubmitEl.addEventListener("click", async () => {
  await switchWorkspaceFromDialog();
});

workspaceDialogCreateEl.addEventListener("click", async () => {
  await createWorkspaceFolderFromDialog();
});

function openWorkspaceDialog() {
  workspaceDialogInputEl.value = workspaceInputEl.value.trim();
  workspaceDialogEl.showModal();
  scheduleWorkspaceDialogCandidates(0);
  workspaceDialogInputEl.focus();
  workspaceDialogInputEl.select();
}

async function switchWorkspaceFromDialog() {
  const path = workspaceDialogInputEl.value.trim();
  if (!path) {
    return;
  }
  workspaceDialogEl.close();
  await switchWorkspace(path);
}

async function createWorkspaceFolderFromDialog() {
  const path = workspaceDialogInputEl.value.trim();
  if (!path) {
    return;
  }
  streamStateEl.textContent = "正在新建项目目录";
  try {
    await api("/api/workspace/folders", {
      method: "POST",
      body: JSON.stringify({ path }),
    });
    workspaceDialogEl.close();
    state.selectedSessionId = null;
    await Promise.all([loadWorkspaceStatus(), loadRuntimePanels(), loadSessions()]);
    streamStateEl.textContent = "目录已新建并切换";
  } catch (error) {
    const message = error instanceof Error ? error.message : "新建目录失败";
    streamStateEl.textContent = `新建目录失败：${message}`;
    renderWorkspaceDialogList(message);
  }
}

function scheduleWorkspaceDialogCandidates(delay = 120, query = workspaceDialogInputEl.value.trim()) {
  window.clearTimeout(workspaceDialogTimer);
  workspaceDialogTimer = window.setTimeout(async () => {
    await loadWorkspaceDialogCandidates(query);
  }, delay);
}

async function loadWorkspaceDialogCandidates(query = workspaceDialogInputEl.value.trim()) {
  const requestId = ++state.workspaceDialogRequestId;
  try {
    const suffix = query ? `?q=${encodeURIComponent(query)}` : "";
    const workspaces = await api(`/api/workspaces${suffix}`);
    if (requestId !== state.workspaceDialogRequestId) {
      return;
    }
    state.workspaceDialogCandidates = workspaces.candidates || [];
    renderWorkspaceDialogList();
  } catch (error) {
    if (requestId !== state.workspaceDialogRequestId) {
      return;
    }
    state.workspaceDialogCandidates = [];
    renderWorkspaceDialogList(error instanceof Error ? error.message : "目录读取失败");
  }
}

function renderWorkspaceDialogList(errorMessage = "") {
  if (!workspaceDialogListEl) {
    return;
  }
  workspaceDialogListEl.innerHTML = "";
  if (errorMessage) {
    workspaceDialogListEl.innerHTML = `<div class="workspace-dialog-empty">${escapeHtml(errorMessage)}</div>`;
    return;
  }
  if (state.workspaceDialogCandidates.length === 0) {
    workspaceDialogListEl.innerHTML = '<div class="workspace-dialog-empty">没有匹配的下级目录</div>';
    return;
  }
  for (const path of state.workspaceDialogCandidates) {
    const item = document.createElement("button");
    item.type = "button";
    item.dataset.path = path;
    item.innerHTML = `
      <strong>${escapeHtml(projectName(path))}</strong>
      <span>${escapeHtml(path)}</span>
    `;
    item.addEventListener("click", () => selectWorkspaceDialogCandidate(path));
    workspaceDialogListEl.appendChild(item);
  }
  renderWorkspaceDialogActive();
}

function selectWorkspaceDialogCandidate(path) {
  if (!path) {
    return;
  }
  workspaceDialogInputEl.value = path;
  workspaceInputEl.value = path;
  state.workspaceDialogIndex = -1;
  scheduleWorkspaceDialogCandidates(0, `${path}/`);
}

function renderWorkspaceDialogActive() {
  const items = Array.from(workspaceDialogListEl.querySelectorAll("button[data-path]"));
  items.forEach((item, index) => {
    item.classList.toggle("active", index === state.workspaceDialogIndex);
  });
  items[state.workspaceDialogIndex]?.scrollIntoView({ block: "nearest" });
}

function scheduleWorkspaceSuggestions(delay = 160, query = workspaceInputEl.value.trim()) {
  window.clearTimeout(workspaceSuggestTimer);
  workspaceSuggestTimer = window.setTimeout(async () => {
    try {
      await loadWorkspaceCandidates(query);
      renderWorkspaceSuggestions();
    } catch {
      workspaceSuggestionsEl.hidden = true;
    }
  }, delay);
}

function renderWorkspaceSuggestions() {
  workspaceSuggestionsEl.innerHTML = "";
  const currentValue = workspaceInputEl.value.trim().toLowerCase();
  const candidates = state.workspaceCandidates
    .filter((path) => !currentValue || path.toLowerCase().includes(currentValue) || projectName(path).toLowerCase().includes(currentValue))
    .slice(0, 8);
  if (!document.activeElement || document.activeElement !== workspaceInputEl || candidates.length === 0) {
    workspaceSuggestionsEl.hidden = true;
    return;
  }
  for (const path of candidates) {
    const item = document.createElement("button");
    item.type = "button";
    item.dataset.path = path;
    item.innerHTML = `
      <strong>${escapeHtml(projectName(path))}</strong>
      <span>${escapeHtml(path)}</span>
    `;
    item.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      workspaceInputEl.value = path;
      workspaceSuggestionsEl.hidden = true;
    });
    workspaceSuggestionsEl.appendChild(item);
  }
  state.workspaceSuggestionIndex = Math.min(state.workspaceSuggestionIndex, candidates.length - 1);
  renderWorkspaceSuggestionActive();
  workspaceSuggestionsEl.hidden = false;
}

function renderWorkspaceSuggestionActive() {
  const suggestions = Array.from(workspaceSuggestionsEl.querySelectorAll("button"));
  suggestions.forEach((item, index) => {
    item.classList.toggle("active", index === state.workspaceSuggestionIndex);
  });
  suggestions[state.workspaceSuggestionIndex]?.scrollIntoView({ block: "nearest" });
}

async function switchWorkspace(path) {
  streamStateEl.textContent = "正在切换项目";
  try {
    await api("/api/workspace/select", {
      method: "POST",
      body: JSON.stringify({ path }),
    });
    state.selectedSessionId = null;
    await Promise.all([loadWorkspaceStatus(), loadRuntimePanels(), loadSessions()]);
    streamStateEl.textContent = "项目已切换";
  } catch (error) {
    const message = error instanceof Error ? error.message : "切换失败";
    streamStateEl.textContent = `切换失败：${message}`;
  }
}

function renderMessageRail() {
  messageRailEl.innerHTML = "";
  const userMessages = Array.from(messagesEl.querySelectorAll(".message.user"));
  if (userMessages.length <= 1) {
    messageRailEl.hidden = true;
    return;
  }
  messageRailEl.hidden = false;
  for (const node of userMessages) {
    const button = document.createElement("button");
    button.type = "button";
    button.title = shortText(node.querySelector(".message-content")?.textContent || "历史提问", 80);
    button.addEventListener("click", () => {
      node.scrollIntoView({ block: "start", behavior: "smooth" });
    });
    messageRailEl.appendChild(button);
  }
}

function setupTurnToolToggle(assistantNode) {
  const button = assistantNode.querySelector(".turn-tools-toggle");
  if (!button) {
    return;
  }
  button.addEventListener("click", () => {
    const collapsed = !assistantNode.classList.contains("turn-process-collapsed");
    toggleTurnProcess(assistantNode, collapsed);
  });
  updateTurnToolControl(assistantNode);
}

function turnProcessNodes(assistantNode) {
  const nodes = [];
  let node = assistantNode.previousElementSibling;
  while (node && !(node.classList.contains("message") && node.classList.contains("user"))) {
    if (node.classList.contains("tool-event") || node.classList.contains("agent-status")) {
      nodes.push(node);
    }
    node = node.previousElementSibling;
  }
  return nodes.reverse();
}

function toggleTurnProcess(assistantNode, collapsed) {
  const processNodes = turnProcessNodes(assistantNode);
  assistantNode.classList.toggle("turn-process-collapsed", collapsed);
  for (const node of processNodes) {
    node.classList.toggle("turn-process-hidden", collapsed);
  }
  updateTurnToolControl(assistantNode);
  streamStateEl.textContent = collapsed ? "已收起当前轮执行过程" : "已展开当前轮执行过程";
}

function ensureTurnProcessControl(assistantNode, processNodes, count) {
  let control = assistantNode._turnProcessControl;
  if (!control) {
    control = document.createElement("button");
    control.type = "button";
    control.className = "turn-process-control";
    control.addEventListener("click", () => {
      const collapsed = !assistantNode.classList.contains("turn-process-collapsed");
      toggleTurnProcess(assistantNode, collapsed);
    });
    assistantNode._turnProcessControl = control;
  }
  if (processNodes[0]?.parentElement === messagesEl && control.parentElement !== messagesEl) {
    messagesEl.insertBefore(control, processNodes[0]);
  } else if (processNodes[0]?.parentElement === messagesEl && control.nextElementSibling !== processNodes[0]) {
    messagesEl.insertBefore(control, processNodes[0]);
  }
  control.hidden = processNodes.length === 0;
  control.textContent = assistantNode.classList.contains("turn-process-collapsed")
    ? `展开本轮过程 · ${count} 个工具`
    : `收起本轮过程 · ${count} 个工具`;
}

function updateTurnToolControl(assistantNode) {
  const button = assistantNode?.querySelector?.(".turn-tools-toggle");
  if (!button) {
    return;
  }
  const processNodes = turnProcessNodes(assistantNode);
  const count = processNodes.filter((node) => node.classList.contains("tool-event")).length;
  const hasProcess = processNodes.length > 0;
  button.hidden = !hasProcess;
  button.textContent = assistantNode.classList.contains("turn-process-collapsed") ? "展开过程" : "收起过程";
  if (hasProcess) {
    ensureTurnProcessControl(assistantNode, processNodes, count);
  } else if (assistantNode._turnProcessControl) {
    assistantNode._turnProcessControl.remove();
    assistantNode._turnProcessControl = null;
  }
}

function updateAllTurnToolControls() {
  for (const node of messagesEl.querySelectorAll(".message.assistant")) {
    updateTurnToolControl(node);
  }
}

function setupInspectorCards() {
  for (const card of document.querySelectorAll(".inspector-card")) {
    const title = card.querySelector(".card-title");
    if (!title || title.querySelector(".card-toggle")) {
      continue;
    }
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "card-toggle";
    toggle.setAttribute("aria-label", "折叠或展开面板");
    toggle.textContent = "−";
    toggle.addEventListener("click", () => {
      const collapsed = card.classList.toggle("collapsed");
      toggle.textContent = collapsed ? "+" : "−";
    });
    title.appendChild(toggle);
  }
}

document.addEventListener("click", (event) => {
  if (!event.target.closest(".workspace-switcher")) {
    workspaceSuggestionsEl.hidden = true;
  }
  const target = event.target.closest("[data-prompt]");
  if (!target) {
    if (!event.target.closest(".command-palette")) {
      hideCommandPalette();
    }
    return;
  }
  messageEl.value = target.dataset.prompt;
  autoResizeTextarea();
  messageEl.focus();
  updateCommandPalette();
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
    if (messagesEl.querySelector(".empty-state")) {
      messagesEl.innerHTML = "";
    }
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
    await Promise.all([
      loadSessions({ refreshMessages: false }),
      loadWorkspaceStatus(),
      loadRuntimePanels(),
      loadHealth(),
    ]);
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
  const activeToolNodes = new Map();

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const result = consumeStreamLines(buffer, assistantNode, {
      onDelta: (delta) => {
        assistantText += delta;
        updateMessage(assistantNode, assistantText);
        streamStateEl.textContent = "Nova 正在输出";
      },
      onToolStart: (event) => {
        streamStateEl.textContent = `工具执行：${event.tool}`;
        const node = appendToolEvent(event, assistantNode);
        activeToolNodes.set(event.call_id || event.tool || "tool", node);
        updateTurnToolControl(assistantNode);
      },
      onToolDone: (event) => {
        const key = event.call_id || event.tool || "tool";
        finishToolEvent(activeToolNodes.get(key), event);
        activeToolNodes.delete(key);
        streamStateEl.textContent = event.ok ? "工具完成，继续推理" : "工具失败，继续处理";
      },
      onStatus: (event) => {
        streamStateEl.textContent = event.status || "运行中";
        appendStatusEvent(event.status || "运行中", { beforeNode: assistantNode });
        updateTurnToolControl(assistantNode);
      },
    });
    buffer = result.rest;
    ok = ok && result.ok;
  }

  if (buffer.trim()) {
    const result = consumeStreamLines(`${buffer}\n`, assistantNode, {
      onDelta: (delta) => {
        assistantText += delta;
        updateMessage(assistantNode, assistantText);
      },
      onToolStart: (event) => {
        const node = appendToolEvent(event, assistantNode);
        activeToolNodes.set(event.call_id || event.tool || "tool", node);
        updateTurnToolControl(assistantNode);
      },
      onToolDone: (event) => {
        const key = event.call_id || event.tool || "tool";
        finishToolEvent(activeToolNodes.get(key), event);
        activeToolNodes.delete(key);
        streamStateEl.textContent = event.ok ? "工具完成，继续推理" : "工具失败，继续处理";
      },
      onStatus: (event) => {
        streamStateEl.textContent = event.status || "运行中";
        appendStatusEvent(event.status || "运行中", { beforeNode: assistantNode });
        updateTurnToolControl(assistantNode);
      },
    });
    ok = ok && result.ok;
  }
  return ok;
}

function consumeStreamLines(buffer, assistantNode, handlers) {
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
      handlers.onDelta(event.delta || "");
    }
    if (event.type === "tool_start") {
      handlers.onToolStart?.(event);
    }
    if (event.type === "tool_done") {
      handlers.onToolDone?.(event);
    }
    if (event.type === "agent_status") {
      handlers.onStatus?.(event);
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
  if (!commandPaletteEl.hidden && event.key === "Escape") {
    hideCommandPalette();
    return;
  }
  if (!commandPaletteEl.hidden && event.key === "Tab") {
    event.preventDefault();
    fillCommand(commandMatches[0]);
    return;
  }
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    hideCommandPalette();
    form.requestSubmit();
  }
});

messageEl.addEventListener("input", () => {
  autoResizeTextarea();
  updateCommandPalette();
});

messageEl.addEventListener("focus", updateCommandPalette);

function autoResizeTextarea() {
  // 输入区随内容长高，但限制最大高度，避免挤掉对话窗口。
  messageEl.style.height = "auto";
  messageEl.style.height = `${Math.min(messageEl.scrollHeight, 180)}px`;
}

function updateCommandPalette() {
  const value = messageEl.value.trimStart();
  if (!value.startsWith("/")) {
    hideCommandPalette();
    return;
  }
  const query = value.split(/\s+/, 1)[0].toLowerCase();
  const matches = BUILTIN_COMMANDS.filter((command) => command.name.startsWith(query));
  if (matches.length === 0) {
    hideCommandPalette();
    return;
  }
  commandMatches = matches;
  commandPaletteEl.removeAttribute("hidden");
  commandPaletteEl.innerHTML = "";
  const header = document.createElement("div");
  header.className = "command-palette-title";
  header.innerHTML = "<strong>内置指令</strong><span>Tab 补全，Enter 发送</span>";
  commandPaletteEl.appendChild(header);
  for (const command of matches) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "command-item";
    item.innerHTML = `
      <strong>${command.name}</strong>
      <span>${command.description}</span>
    `;
    item.addEventListener("click", () => fillCommand(command));
    commandPaletteEl.appendChild(item);
  }
  commandPaletteEl.hidden = false;
}

function fillCommand(command) {
  if (!command) {
    return;
  }
  messageEl.value = `${command.name} `;
  autoResizeTextarea();
  hideCommandPalette();
  messageEl.focus();
}

function hideCommandPalette() {
  commandPaletteEl.hidden = true;
  commandMatches = [];
}

loadHealth();
loadWorkspaceStatus();
loadRuntimePanels();
loadSessions();
setupInspectorCards();
