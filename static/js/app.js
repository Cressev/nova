import { api } from "./api/client.js";
import { BUILTIN_COMMANDS } from "./components/command_palette.js";
import { consumeStreamLines } from "./runtime/stream.js";
import {
  readStorageBool,
  readStorageList,
  writeStorageBool,
  writeStorageList,
} from "./state/storage.js";
import { queryRequired } from "./ui/dom.js";

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
  workspaceDialogStatus: null,
  messagesRequestId: 0,
  runtimeConfig: null,
  statusline: null,
  statuslineItems: new Set(readStorageList("nova.statuslineItems", [
    "model",
    "context",
    "tokens",
    "session",
    "permission",
  ])),
  sidebarCollapsed: readStorageBool("nova.sidebarCollapsed", false),
  inspectorCollapsed: readStorageBool("nova.inspectorCollapsed", false),
  statuslineCollapsed: readStorageBool("nova.statuslineCollapsed", false),
  settingsCollapsed: new Set(readStorageList("nova.settingsCollapsed")),
};

const healthEl = queryRequired("#health");
const providerEl = queryRequired("#provider");
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
const workspaceDialogStateEl = document.querySelector("#workspace-dialog-state");
const workspaceDialogListEl = document.querySelector("#workspace-dialog-list");
const workspaceDialogCloseEl = document.querySelector("#workspace-dialog-close");
const workspaceDialogSubmitEl = document.querySelector("#workspace-dialog-submit");
const workspaceDialogCreateEl = document.querySelector("#workspace-dialog-create");
const messageRailEl = document.querySelector("#message-rail");
const statuslineEl = document.querySelector("#composer-statusline");
const settingsOpenEl = document.querySelector("#settings-open");
const settingsDialogEl = document.querySelector("#settings-dialog");
const settingsCloseEl = document.querySelector("#settings-close");
const settingsRuntimeEl = document.querySelector("#settings-runtime");
const settingsStatuslineEl = document.querySelector("#settings-statusline");
const settingsSaveEl = document.querySelector("#settings-save");
const settingsRestartEl = document.querySelector("#settings-restart");
const settingsNoteEl = document.querySelector("#settings-note");
const memoryDialogEl = document.querySelector("#memory-dialog");
const memoryDialogTitleEl = document.querySelector("#memory-dialog-title");
const memoryDialogNameEl = document.querySelector("#memory-dialog-name");
const memoryDialogContentEl = document.querySelector("#memory-dialog-content");
const memoryDialogStateEl = document.querySelector("#memory-dialog-state");
const memoryDialogCloseEl = document.querySelector("#memory-dialog-close");
const memoryDialogCancelEl = document.querySelector("#memory-dialog-cancel");
const memoryDialogSaveEl = document.querySelector("#memory-dialog-save");
const sidebarToggleEl = document.querySelector("#sidebar-toggle");
const inspectorToggleEl = document.querySelector("#inspector-toggle");
const statuslineToggleEl = document.querySelector("#statusline-toggle");
let workspaceSuggestTimer = null;
let workspaceDialogTimer = null;
const TOOL_TOOLTIP_DELAY_MS = 1000;

let commandMatches = [];

const STATUSLINE_ITEMS = [
  { id: "model", label: "模型" },
  { id: "context", label: "上下文剩余" },
  { id: "tokens", label: "Token 用量" },
  { id: "session", label: "Session ID" },
  { id: "project", label: "项目" },
  { id: "permission", label: "权限" },
  { id: "state", label: "状态" },
];

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

function normalizeWorkspacePath(path) {
  if (typeof path !== "string") {
    return "";
  }
  return path.trim().replace(/[\\/]+$/, "");
}

function workspaceGroupKey(path) {
  const normalized = normalizeWorkspacePath(path);
  return normalized ? normalized.toLowerCase() : "__unbound__";
}

function parentProjectName(path) {
  const parts = normalizeWorkspacePath(path).split(/[\\/]/).filter(Boolean);
  return parts.length > 1 ? parts.at(-2) : "";
}

function workspaceDisplayName(path) {
  const normalized = normalizeWorkspacePath(path);
  return normalized ? projectName(normalized) : "未绑定项目";
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
  const [config, tools, memory, statusline] = await Promise.all([
    api("/api/runtime/config"),
    api("/api/tools"),
    api("/api/memory/status"),
    loadStatuslineData(),
  ]);
  state.runtimeConfig = config;
  state.statusline = statusline;
  renderRuntimeConfig(config);
  renderTools(tools.items || []);
  renderMemory(memory);
  renderStatusline();
  renderSettings();
}

async function loadStatuslineData() {
  const suffix = state.selectedSessionId ? `?session_id=${encodeURIComponent(state.selectedSessionId)}` : "";
  return api(`/api/runtime/statusline${suffix}`);
}

async function refreshStatusline() {
  try {
    state.statusline = await loadStatuslineData();
    renderStatusline();
    renderSettings();
  } catch {
    statuslineEl.innerHTML = '<span class="statusline-muted">状态线读取失败</span>';
  }
}

function renderStatusline() {
  if (!statuslineEl || !state.statusline) {
    return;
  }
  statuslineEl.hidden = state.statuslineCollapsed;
  statuslineToggleEl.textContent = state.statuslineCollapsed ? "展开状态线" : "收起状态线";
  if (state.statuslineCollapsed) {
    return;
  }
  const data = state.statusline;
  const draftTokens = estimateDraftTokens(messageEl.value);
  const rows = {
    model: ["模型", data.model],
    context: [
      "上下文",
      `${formatCompactNumber(Math.max((data.context_remaining_tokens || 0) - draftTokens, 0))} 剩余 / ${data.context_remaining_percent ?? "-"}%`,
    ],
    tokens: [
      "Token",
      `${formatCompactNumber((data.used_tokens || 0) + draftTokens)} 已用${data.estimated ? " 估算" : ""}`,
    ],
    session: ["Session", data.session_id ? shortId(data.session_id) : "未创建"],
    project: ["项目", data.project || projectName(data.workspace || "")],
    permission: ["权限", data.permission_mode],
    state: ["状态", state.sending ? "working" : data.status],
  };
  statuslineEl.innerHTML = "";
  for (const item of STATUSLINE_ITEMS) {
    if (!state.statuslineItems.has(item.id)) {
      continue;
    }
    const [label, value] = rows[item.id] || [];
    const node = document.createElement("span");
    node.className = "statusline-item";
    node.innerHTML = `<strong>${escapeHtml(label)}</strong><em>${escapeHtml(String(value ?? "-"))}</em>`;
    statuslineEl.appendChild(node);
  }
}

function renderSettings() {
  if (!settingsRuntimeEl || !settingsStatuslineEl) {
    return;
  }
  const config = state.runtimeConfig || {};
  const line = state.statusline || {};
  const pending = config.pending_config || {};
  settingsRuntimeEl.innerHTML = `
    <label class="setting-field setting-field-wide setting-secret-field">
      <span>BigModel API Key</span>
      <input name="bigmodel_api_key" type="password" value="" autocomplete="off" placeholder="${escapeHtml(config.api_key_set ? "已设置，输入新 Key 可替换" : "填写后立即生效，无需重启")}" />
      <small>${escapeHtml(config.api_key_set ? `当前来源：${config.api_key_source === "runtime" ? "设置页" : "环境变量"}` : "仅保存在本机 .nova/runtime-secrets.json，不会回显明文")}</small>
    </label>
    ${renderSettingsField("provider_model", "模型", pending.provider_model ?? config.model ?? line.model ?? "", "text")}
    ${renderSettingsField("provider_base_url", "Base URL", pending.provider_base_url ?? config.base_url ?? "", "text")}
    ${renderSettingsField("context_window_tokens", "上下文窗口", pending.context_window_tokens ?? config.context_window_tokens ?? line.context_window_tokens ?? 128000, "number")}
    <label class="setting-field setting-field-wide">
      <span>权限预设</span>
      <select name="permission_preset">
        ${renderPermissionOption("read_only", "只读：只允许读项目", permissionPresetFromConfig(config))}
        ${renderPermissionOption("ask", "询问：写入和命令前确认", permissionPresetFromConfig(config))}
        ${renderPermissionOption("workspace_write", "工作区写入：自动改当前项目", permissionPresetFromConfig(config))}
        ${renderPermissionOption("plan", "计划：只拆方案不执行", permissionPresetFromConfig(config))}
        ${renderPermissionOption("bypass_permissions", "跳过权限：完全访问", permissionPresetFromConfig(config))}
      </select>
      <small>像 Codex 一样选择一个权限预设；Nova 会自动配置底层沙箱和审批策略。</small>
    </label>
    <label class="setting-field setting-field-inline">
      <span>网络访问</span>
      <input name="network_access" type="checkbox" ${(pending.network_access ?? config.network_access) ? "checked" : ""} />
    </label>
    ${renderSettingsField("max_tool_rounds", "最大工具轮次", pending.max_tool_rounds ?? config.max_tool_rounds ?? 6, "number")}
  `;

  settingsStatuslineEl.innerHTML = "";
  for (const item of STATUSLINE_ITEMS) {
    const label = document.createElement("label");
    label.className = "statusline-option";
    label.innerHTML = `
      <input type="checkbox" value="${escapeHtml(item.id)}" ${state.statuslineItems.has(item.id) ? "checked" : ""} />
      <span>${escapeHtml(item.label)}</span>
    `;
    label.querySelector("input").addEventListener("change", (event) => {
      if (event.target.checked) {
        state.statuslineItems.add(item.id);
      } else {
        state.statuslineItems.delete(item.id);
      }
      writeStorageList("nova.statuslineItems", state.statuslineItems);
      renderStatusline();
    });
    settingsStatuslineEl.appendChild(label);
  }
  settingsNoteEl.textContent = config.restart_required
    ? "已有待生效配置，点击“重启网关”后生效。"
    : "API Key、模型和权限配置保存后都会立即影响下一次请求。";
  settingsRestartEl.disabled = !config.restart_required;
  applySettingsSectionState();
}

function renderSettingsField(name, label, value, type) {
  return `
    <label class="setting-field">
      <span>${escapeHtml(label)}</span>
      <input name="${escapeHtml(name)}" type="${escapeHtml(type)}" value="${escapeHtml(String(value))}" />
    </label>
  `;
}

function permissionPresetFromConfig(config) {
  const pending = config.pending_config || {};
  const permissionMode = pending.permission_mode || config.permission_mode;
  if (["read_only", "ask", "workspace_write", "plan", "bypass_permissions"].includes(permissionMode)) {
    return permissionMode;
  }
  const sandboxMode = pending.sandbox_mode || config.sandbox_mode;
  const approvalPolicy = pending.approval_policy || config.approval_policy;
  if (sandboxMode === "read_only") {
    return "read_only";
  }
  if (sandboxMode === "danger_full_access" && approvalPolicy === "never") {
    return "bypass_permissions";
  }
  if (approvalPolicy === "on_request" || approvalPolicy === "untrusted") {
    return "ask";
  }
  return "workspace_write";
}

function derivePermissionConfig(permissionPreset) {
  const presets = {
    read_only: { permission_mode: "read_only", sandbox_mode: "read_only", approval_policy: "never" },
    ask: { permission_mode: "ask", sandbox_mode: "workspace_write", approval_policy: "on_request" },
    workspace_write: { permission_mode: "workspace_write", sandbox_mode: "workspace_write", approval_policy: "never" },
    plan: { permission_mode: "plan", sandbox_mode: "read_only", approval_policy: "never" },
    bypass_permissions: { permission_mode: "bypass_permissions", sandbox_mode: "danger_full_access", approval_policy: "never" },
  };
  return presets[permissionPreset] || presets.ask;
}

function renderPermissionOption(value, label, selectedValue) {
  return `<option value="${escapeHtml(value)}" ${value === selectedValue ? "selected" : ""}>${escapeHtml(label)}</option>`;
}

function formatCompactNumber(value) {
  const number = Number(value || 0);
  if (number >= 1000000) {
    return `${(number / 1000000).toFixed(1)}M`;
  }
  if (number >= 1000) {
    return `${(number / 1000).toFixed(1)}k`;
  }
  return String(number);
}

function shortId(value) {
  const text = String(value || "");
  if (text.length <= 16) {
    return text || "-";
  }
  return `${text.slice(0, 8)}…${text.slice(-4)}`;
}

function estimateDraftTokens(text) {
  const cleaned = (text || "").trim();
  return cleaned ? Math.max(1, Math.ceil(cleaned.length / 4)) : 0;
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
    ["沙箱模式", permissions.sandbox_mode],
    ["审批 ID", permissions.approval_policy_id],
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
    ["API Key", config.api_key_set ? `已配置 · ${config.api_key_source === "runtime" ? "设置页" : "环境变量"}` : "未配置"],
    ["上下文窗口", `${formatCompactNumber(config.context_window_tokens || 0)} tokens`],
    ["工具轮次", String(config.max_tool_rounds)],
    ["沙箱模式", config.sandbox_mode],
    ["审批策略", config.approval_policy],
    ["只读并行", config.tool_parallel_readonly ? "已启用" : "关闭"],
    ["审批 UI", config.approval_ui_enabled ? "已启用" : "未实现"],
    ["Hooks", config.hooks_enabled ? "已启用" : "未配置"],
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
      <em class="tool-tooltip">
        <b>${escapeHtml(item.description || item.name)}</b>
        <small>权限：${escapeHtml(item.permission || "-")} · 并行：${item.supports_parallel ? "支持" : "不支持"} · 风险：${escapeHtml(item.risk || "-")}</small>
        <code>${escapeHtml(JSON.stringify(item.schema || {}, null, 2))}</code>
      </em>
    `;
    node.setAttribute("aria-label", `${item.name}：${item.description || ""}`);
    node.addEventListener("click", () => {
      messageEl.value = `/tools`;
      autoResizeTextarea();
      messageEl.focus();
    });
    bindToolTooltip(node);
    toolListEl.appendChild(node);
  }
}

function hideToolTooltip(node) {
  const tooltip = node.querySelector(".tool-tooltip");
  clearTimeout(Number(node.dataset.tooltipTimer || 0));
  node.dataset.tooltipTimer = "";
  tooltip?.classList.remove("visible", "align-left", "align-right");
}

function scheduleToolTooltip(node) {
  const tooltip = node.querySelector(".tool-tooltip");
  if (!tooltip) {
    return;
  }
  hideToolTooltip(node);
  const timer = window.setTimeout(() => {
    const rect = node.getBoundingClientRect();
    const preferLeft = rect.left + 320 > window.innerWidth - 16;
    tooltip.classList.toggle("align-left", preferLeft);
    tooltip.classList.toggle("align-right", !preferLeft);
    tooltip.classList.add("visible");
  }, TOOL_TOOLTIP_DELAY_MS);
  node.dataset.tooltipTimer = String(timer);
}

function bindToolTooltip(node) {
  node.addEventListener("mouseenter", () => scheduleToolTooltip(node));
  node.addEventListener("mouseleave", () => hideToolTooltip(node));
  node.addEventListener("focus", () => scheduleToolTooltip(node));
  node.addEventListener("blur", () => hideToolTooltip(node));
}

function renderMemory(memory) {
  memoryStateEl.textContent = memory.enabled ? "已启用" : "关闭";
  memoryListEl.innerHTML = "";
  appendMemoryGroup("Agent 指令", (memory.injected_sources || []).filter((item) => item.kind === "instruction"));
  appendMemoryGroup("人格文件", memory.persona_files || []);
  appendMemoryGroup("长期记忆", memory.memory_files || []);
  const personaButton = document.createElement("button");
  personaButton.type = "button";
  personaButton.className = "memory-add";
  personaButton.textContent = "添加人格文件";
  personaButton.addEventListener("click", addPersonaFile);
  memoryListEl.appendChild(personaButton);
  const memoryButton = document.createElement("button");
  memoryButton.type = "button";
  memoryButton.className = "memory-add";
  memoryButton.textContent = "添加记忆文件";
  memoryButton.addEventListener("click", addMemoryFile);
  memoryListEl.appendChild(memoryButton);
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
    if (item.injected && item.path && item.name?.endsWith(".md") && ["persona", "memory"].includes(item.kind)) {
      row.tabIndex = 0;
      row.title = "点击查看和编辑";
      row.addEventListener("click", () => editContextFile(item));
      row.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          editContextFile(item);
        }
      });
    }
    memoryListEl.appendChild(row);
  }
}

async function editContextFile(item) {
  try {
    const name = item.name;
    if (item.kind === "persona") {
      const scope = item.scope === "全局人格" ? "global" : "project";
      const file = await api(`/api/persona/files/${encodeURIComponent(scope)}/${encodeURIComponent(name)}`);
      openMemoryDialog({ name, content: file.content || "", mode: "edit", source: "persona", scope });
      return;
    }
    const file = await api(`/api/memory/files/${encodeURIComponent(name)}`);
    openMemoryDialog({ name, content: file.content || "", mode: "edit", source: "memory", scope: "project" });
  } catch (error) {
    streamStateEl.textContent = `上下文文件编辑失败：${error instanceof Error ? error.message : "未知错误"}`;
  }
}

async function addPersonaFile() {
  openMemoryDialog({ name: "soul.md", content: "", mode: "create", source: "persona", scope: "project" });
}

async function addMemoryFile() {
  openMemoryDialog({ name: "index.md", content: "", mode: "create", source: "memory", scope: "project" });
}

function normalizeMemoryFileName(name) {
  const cleaned = String(name || "").trim().replaceAll("\\", "/").split("/").pop();
  if (!cleaned) {
    return "";
  }
  return cleaned.endsWith(".md") ? cleaned : `${cleaned}.md`;
}

function openMemoryDialog({ name, content, mode, source = "memory", scope = "project" }) {
  const isPersona = source === "persona";
  memoryDialogEl.dataset.source = source;
  memoryDialogEl.dataset.scope = scope;
  memoryDialogTitleEl.textContent = mode === "create"
    ? (isPersona ? "添加人格文件" : "添加记忆文件")
    : `编辑 ${isPersona ? "人格" : "记忆"} ${name}`;
  memoryDialogNameEl.value = normalizeMemoryFileName(name);
  memoryDialogNameEl.disabled = mode !== "create";
  memoryDialogContentEl.value = content || "";
  memoryDialogStateEl.textContent = isPersona
    ? `仅支持 .md 文件；保存后会进入${scope === "global" ? "全局" : "当前项目"} .nova/persona。`
    : "仅支持 .md 文件；保存后会进入当前项目 .nova/memory。";
  memoryDialogSaveEl.disabled = false;
  memoryDialogEl.showModal();
  memoryDialogContentEl.focus();
}

async function saveMemoryDialog() {
  const name = normalizeMemoryFileName(memoryDialogNameEl.value);
  if (!name || !name.endsWith(".md")) {
    memoryDialogStateEl.textContent = "请输入 .md 文件名。";
    return;
  }
  memoryDialogSaveEl.disabled = true;
  const source = memoryDialogEl.dataset.source || "memory";
  const scope = memoryDialogEl.dataset.scope || "project";
  const isPersona = source === "persona";
  memoryDialogStateEl.textContent = isPersona ? "正在保存人格文件" : "正在保存记忆文件";
  try {
    await api(isPersona ? "/api/persona/files" : "/api/memory/files", {
      method: "POST",
      body: JSON.stringify({ scope, name, content: memoryDialogContentEl.value }),
    });
    memoryDialogEl.close();
    await loadRuntimePanels();
    streamStateEl.textContent = `已更新${isPersona ? "人格" : "记忆"} ${name}`;
  } catch (error) {
    memoryDialogStateEl.textContent = `保存失败：${error instanceof Error ? error.message : "未知错误"}`;
    memoryDialogSaveEl.disabled = false;
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
    const currentWorkspace = projectRootEl.textContent.trim();
    state.selectedSessionId = (sessions.find((session) => session.workspace === currentWorkspace) || sessions[0]).id;
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
    await refreshStatusline();
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
    const workspace = normalizeWorkspacePath(session.workspace);
    const key = workspaceGroupKey(workspace);
    if (!map.has(key)) {
      map.set(key, {
        workspace,
        name: workspaceDisplayName(workspace),
        sessions: [],
        updated_at: session.updated_at,
      });
    }
    const group = map.get(key);
    group.sessions.push(session);
    if (new Date(session.updated_at) > new Date(group.updated_at)) {
      group.updated_at = session.updated_at;
    }
  }
  const groups = Array.from(map.values());
  const nameCounts = groups.reduce((counts, group) => {
    counts.set(group.name, (counts.get(group.name) || 0) + 1);
    return counts;
  }, new Map());
  for (const group of groups) {
    if (group.workspace && nameCounts.get(group.name) > 1) {
      const parent = parentProjectName(group.workspace);
      group.name = parent ? `${group.name} · ${parent}` : group.workspace;
    }
  }
  return groups.sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));
}

function renderSessionItem(session) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = `session-item ${session.id === state.selectedSessionId ? "active" : ""}`;
    item.innerHTML = `
      <span class="session-main">
        <strong>${shortText(session.title)}</strong>
        <small>${shortText(workspaceDisplayName(session.workspace), 28)}</small>
        <span>${formatTime(session.updated_at)}</span>
      </span>
      <button class="session-delete" type="button" aria-label="删除对话" title="删除对话">×</button>
    `;
    item.addEventListener("click", () => selectSession(session));
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

async function selectSession(session) {
  if (session.workspace && session.workspace !== projectRootEl.textContent.trim()) {
    streamStateEl.textContent = "正在切换到历史线程所属项目";
    try {
      await api("/api/workspace/select", {
        method: "POST",
        body: JSON.stringify({ path: session.workspace }),
      });
      await Promise.all([loadWorkspaceStatus(), loadRuntimePanels()]);
    } catch (error) {
      streamStateEl.textContent = `项目切换失败：${error instanceof Error ? error.message : "未知错误"}`;
      return;
    }
  }
  state.selectedSessionId = session.id;
  state.selectedSessionTitle = session.title || "Nova Chat";
  chatTitleEl.textContent = state.selectedSessionTitle;
  await Promise.all([loadSessions({ refreshMessages: false }), loadMessages(), refreshStatusline()]);
  streamStateEl.textContent = "历史线程已加载";
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
  const runtimeState = await api(`/api/chat/sessions/${sessionId}/runtime-state`);
  if (requestId !== state.messagesRequestId || sessionId !== state.selectedSessionId) {
    return;
  }
  const items = runtimeState.timeline?.items || [];
  const hasRuntimeRestorations = Boolean(
    runtimeState.pending_approvals?.length
      || runtimeState.processes?.length
      || runtimeState.queued_messages?.length,
  );

  if (items.length === 0 && !hasRuntimeRestorations) {
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
  appendRuntimeStateRestorations(runtimeState, items);
  updateAllTurnToolControls();
  renderMessageRail();
  scrollMessagesToBottom();
}

function appendStoredEvent(event) {
  if (event.type === "turn" || event.event_type?.startsWith("turn.")) {
    appendStatusEvent(event.title || event.message || "运行状态更新", { autoscroll: false });
    return;
  }
  if (event.type === "tool") {
    const node = appendToolEvent(
      {
        call_id: event.id,
        tool: event.tool,
        arguments: event.arguments || {},
        title: event.title,
        parallel: event.parallel,
        data: event.data || {},
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
  if (event.type === "permission" || event.event_type === "permission.requested") {
    appendPermissionEvent(
      {
        call_id: event.id,
        tool: event.tool,
        permission: event.data?.permission,
        title: event.title,
        message: event.message,
        arguments: event.arguments || {},
        data: event.data || {},
      },
      null,
      { autoscroll: false },
    );
    return;
  }
  if (event.type === "hook" || event.event_type?.startsWith("hook.")) {
    appendStatusEvent(event.title || event.message || "Hook 事件", { autoscroll: false });
    return;
  }
  if (event.type === "status") {
    appendStatusEvent(event.title, { autoscroll: false });
  }
}

function appendRuntimeStateRestorations(runtimeState, timelineItems = []) {
  // runtime-state 里有些对象是当前进程态，不一定已经写进 timeline，刷新页面时要补回可见状态。
  const knownEventIds = new Set();
  const knownMessageIds = new Set();
  const knownProcessIds = new Set();
  for (const entry of timelineItems) {
    if (entry.kind === "message") {
      knownMessageIds.add(entry.item?.id);
    }
    if (entry.kind === "event") {
      knownEventIds.add(entry.item?.id);
      collectProcessIds(entry.item, knownProcessIds);
    }
  }

  for (const approval of runtimeState.pending_approvals || []) {
    const callId = approval.call_id || approval.id;
    if (knownEventIds.has(callId)) {
      continue;
    }
    appendPermissionEvent(
      {
        call_id: callId,
        tool: approval.tool,
        permission: approval.permission,
        title: approval.reason || "等待工具审批",
        message: approval.reason,
        arguments: approval.arguments || {},
        data: { permission: approval.permission },
      },
      null,
      { autoscroll: false },
    );
  }

  for (const process of runtimeState.processes || []) {
    if (knownProcessIds.has(process.id)) {
      continue;
    }
    appendStatusEvent(
      `后台任务 ${shortId(process.id)}：${shortText(process.command || "运行中", 80)} · ${process.status || "running"}`,
      { autoscroll: false },
    );
  }

  for (const message of runtimeState.queued_messages || []) {
    if (knownMessageIds.has(message.id)) {
      continue;
    }
    appendStatusEvent(`排队输入：${shortText(message.content || "", 80)}`, { autoscroll: false });
  }
}

function collectProcessIds(value, target) {
  if (!value || typeof value !== "object") {
    return;
  }
  if (typeof value.id === "string" && value.id.startsWith("proc_")) {
    target.add(value.id);
  }
  for (const child of Array.isArray(value) ? value : Object.values(value)) {
    collectProcessIds(child, target);
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
  if (message.id) {
    const existing = messagesEl.querySelector(`[data-message-id="${CSS.escape(message.id)}"]`);
    if (existing) {
      existing.classList.toggle("queued", Boolean(options.queued));
      updateMessage(existing, message.content || "");
      updateMessageMeta(existing, message);
      const badge = existing.querySelector(".message-queue-badge");
      if (badge) {
        badge.hidden = !options.queued;
      }
      return existing;
    }
  }
  const targetId = options.showDivider && message.role === "user"
    ? appendTurnDivider(message)
    : `message-${message.id || Date.now()}`;
  const node = document.createElement("article");
  node.className = `message ${message.role}${options.queued ? " queued" : ""}`;
  node.id = targetId;
  node.dataset.messageId = message.id || "";
  node.innerHTML = `
    <div class="message-head">
      <div class="message-role">${roleLabel(message.role)}</div>
      ${message.role === "user" ? `<span class="message-queue-badge" ${options.queued ? "" : "hidden"}>queue</span>` : ""}
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
  node.dataset.argumentsRaw = JSON.stringify(event.arguments || {});
  node.dataset.toolData = JSON.stringify(event.data || {});
  node.innerHTML = `
    <div class="tool-event-head">
      <span>${escapeHtml(event.tool || "tool")}</span>
      <strong>${escapeHtml(event.title || "工具执行中")}</strong>
      <em>${event.parallel ? "并行" : "运行中"}</em>
    </div>
    ${renderToolMetadata(event.data || {})}
    <div class="tool-actions">
      <button class="tool-cancel" type="button" data-action="cancel-tool">取消</button>
    </div>
    <details open>
      <summary>调用参数</summary>
      <pre>${escapeHtml(node.dataset.arguments)}</pre>
    </details>
  `;
  node.querySelector('[data-action="cancel-tool"]').addEventListener("click", () => cancelToolCall(node));
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

async function cancelToolCall(node) {
  const callId = node.dataset.callId;
  if (!callId) {
    return;
  }
  const button = node.querySelector('[data-action="cancel-tool"]');
  if (button) {
    button.disabled = true;
    button.textContent = "取消中";
  }
  try {
    await api(`/api/tool-calls/${encodeURIComponent(callId)}/cancel`, { method: "POST" });
    node.className = "tool-event failed";
    const status = node.querySelector(".tool-event-head em");
    if (status) {
      status.textContent = "已取消";
    }
    streamStateEl.textContent = "已请求取消工具调用";
  } catch (error) {
    if (button) {
      button.disabled = false;
      button.textContent = "取消";
    }
    streamStateEl.textContent = `取消失败：${error instanceof Error ? error.message : "未知错误"}`;
  }
}

function finishToolEvent(node, event, options = {}) {
  if (!node) {
    node = appendToolEvent(event);
  }
  node.className = `tool-event ${event.ok ? "ok" : "failed"}`;
  const args = node.dataset.arguments || "{}";
  const rawArgs = node.dataset.argumentsRaw || args;
  const data = event.data || {};
  const retryButton = !event.ok && data.retryable
    ? '<button class="tool-retry" type="button" data-action="retry-tool">重试</button>'
    : "";
  const statusLabel = event.data?.status === "cancelled" ? "已取消" : (event.ok ? "完成" : "失败");
  node.innerHTML = `
    <div class="tool-event-head">
      <span>${escapeHtml(event.tool || "tool")}</span>
      <strong>${escapeHtml(event.title || "工具完成")}</strong>
      <em>${statusLabel}</em>
    </div>
    ${renderToolMetadata(data)}
    ${data.failure_reason ? `<div class="tool-failure">${escapeHtml(data.failure_reason)}</div>` : ""}
    <div class="tool-actions">
      ${retryButton}
    </div>
    <details class="tool-args" open>
      <summary>调用参数</summary>
      <pre>${escapeHtml(args)}</pre>
    </details>
    ${renderDiffPreview(data.diff)}
    <details class="tool-result">
      <summary>工具结果</summary>
      <pre>${escapeHtml(shortText(event.output || "", 4000))}</pre>
    </details>
  `;
  node.dataset.argumentsRaw = rawArgs;
  node.dataset.toolData = JSON.stringify(data);
  node.querySelector('[data-action="retry-tool"]')?.addEventListener("click", () => retryToolCall(node));
  if (options.autoscroll !== false) {
    scrollMessagesToBottom();
  }
}

function renderToolMetadata(data = {}) {
  const spec = data.spec || {};
  const items = [
    ["权限", spec.permission || data.permission],
    ["风险", spec.risk],
    ["分类", spec.category],
    ["耗时", typeof data.duration_ms === "number" ? `${data.duration_ms} ms` : ""],
  ].filter(([, value]) => value !== undefined && value !== null && value !== "");
  if (items.length === 0 && !spec.schema) {
    return "";
  }
  const meta = items.map(([label, value]) => `
    <div>
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(String(value))}</strong>
    </div>
  `).join("");
  const schema = spec.schema
    ? `<details class="tool-schema"><summary>输入 Schema</summary><pre>${escapeHtml(JSON.stringify(spec.schema, null, 2))}</pre></details>`
    : "";
  return `<div class="tool-meta-grid">${meta}</div>${schema}`;
}

function renderDiffPreview(diff) {
  if (!diff || !Array.isArray(diff.files)) {
    return "";
  }
  const files = diff.files.length > 0 ? diff.files.join(", ") : "未知文件";
  const summary = `${files} · +${diff.additions || 0} / -${diff.deletions || 0}`;
  return `
    <details class="tool-diff-preview" open>
      <summary>Diff preview：${escapeHtml(summary)}</summary>
      <pre>${escapeHtml(shortText(diff.preview || "", 6000))}</pre>
    </details>
  `;
}

async function retryToolCall(node) {
  const tool = node.dataset.tool;
  if (!tool) {
    return;
  }
  let args = {};
  try {
    args = JSON.parse(node.dataset.argumentsRaw || node.dataset.arguments || "{}");
  } catch {
    args = {};
  }
  const button = node.querySelector('[data-action="retry-tool"]');
  if (button) {
    button.disabled = true;
    button.textContent = "重试中";
  }
  try {
    const response = await api("/api/tool-calls/retry", {
      method: "POST",
      body: JSON.stringify({ tool, arguments: args }),
    });
    const activeToolNodes = new Map();
    for (const event of response.events || []) {
      if (event.type === "tool_start") {
        const toolNode = appendToolEvent(event, node.nextSibling);
        activeToolNodes.set(event.call_id || event.tool || "tool", toolNode);
      }
      if (event.type === "tool_output") {
        appendToolOutput(activeToolNodes.get(event.call_id || event.tool || "tool"), event);
      }
      if (event.type === "tool_done") {
        finishToolEvent(activeToolNodes.get(event.call_id || event.tool || "tool"), event);
      }
    }
    streamStateEl.textContent = "工具已重试";
  } catch (error) {
    if (button) {
      button.disabled = false;
      button.textContent = "重试";
    }
    streamStateEl.textContent = `重试失败：${error instanceof Error ? error.message : "未知错误"}`;
  }
}

function appendToolOutput(node, event) {
  if (!node) {
    return;
  }
  let output = node.querySelector(".tool-stream-output");
  if (!output) {
    output = document.createElement("pre");
    output.className = "tool-stream-output";
    node.appendChild(output);
  }
  const label = event.stream === "stderr" ? "stderr" : "stdout";
  output.textContent += `[${label}] ${event.chunk || ""}`;
  output.scrollTop = output.scrollHeight;
}

function appendPermissionEvent(event, beforeNode = null, options = {}) {
  const args = JSON.stringify(event.arguments || {}, null, 2);
  const node = document.createElement("article");
  node.className = "permission-event pending";
  node.dataset.callId = event.call_id || "";
  node.dataset.tool = event.tool || "";
  node.innerHTML = `
    <div class="permission-event-head">
      <span>${escapeHtml(event.permission || event.data?.permission || "审批")}</span>
      <strong>${escapeHtml(event.title || `需要审批：${event.tool || "工具"}`)}</strong>
      <em>待确认</em>
    </div>
    <p>${escapeHtml(event.message || "执行该工具前需要用户确认。")}</p>
    <details open>
      <summary>请求参数</summary>
      <pre>${escapeHtml(args)}</pre>
    </details>
    <div class="permission-actions">
      <button type="button" data-action="approve">允许</button>
      <button type="button" data-action="deny">拒绝</button>
      <small>approve/deny 会真实续跑该工具调用</small>
    </div>
  `;
  node.querySelector('[data-action="approve"]').addEventListener("click", () => processApproval(node, true));
  node.querySelector('[data-action="deny"]').addEventListener("click", () => processApproval(node, false));
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

async function processApproval(node, approved) {
  const callId = node.dataset.callId;
  if (!callId) {
    return;
  }
  node.querySelectorAll("button").forEach((button) => {
    button.disabled = true;
  });
  try {
    const response = await api(`/api/approvals/${encodeURIComponent(callId)}/${approved ? "approve" : "deny"}`, {
      method: "POST",
      body: JSON.stringify(approved ? {} : { reason: "用户在页面拒绝执行" }),
    });
    node.classList.toggle("approved", approved);
    node.classList.toggle("denied", !approved);
    node.querySelector(".permission-event-head em").textContent = approved ? "已允许" : "已拒绝";
    if (approved) {
      const activeToolNodes = new Map();
      for (const event of response.events || []) {
        if (event.type === "tool_start") {
          const toolNode = appendToolEvent(event, node.nextSibling);
          activeToolNodes.set(event.call_id || event.tool || "tool", toolNode);
        }
        if (event.type === "tool_output") {
          appendToolOutput(activeToolNodes.get(event.call_id || event.tool || "tool"), event);
        }
        if (event.type === "tool_done") {
          finishToolEvent(activeToolNodes.get(event.call_id || event.tool || "tool"), event);
        }
      }
    } else if (response.message) {
      appendMessage(response.message);
      streamStateEl.textContent = "已拒绝工具调用，Nova 已给出替代路径";
    }
    await Promise.all([loadRuntimePanels(), refreshStatusline()]);
  } catch (error) {
    node.querySelector(".permission-event-head em").textContent = "审批失败";
    node.querySelectorAll("button").forEach((button) => {
      button.disabled = false;
    });
    streamStateEl.textContent = `审批失败：${error instanceof Error ? error.message : "未知错误"}`;
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
  await Promise.all([loadSessions(), refreshStatusline()]);
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
    if (state.workspaceDialogStatus?.can_create) {
      await createWorkspaceFolderFromDialog();
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

settingsOpenEl.addEventListener("click", async () => {
  await loadRuntimePanels();
  settingsDialogEl.showModal();
});

settingsCloseEl.addEventListener("click", () => {
  settingsDialogEl.close();
});

memoryDialogCloseEl.addEventListener("click", () => {
  memoryDialogEl.close();
});

memoryDialogCancelEl.addEventListener("click", () => {
  memoryDialogEl.close();
});

memoryDialogSaveEl.addEventListener("click", saveMemoryDialog);

settingsSaveEl.addEventListener("click", async () => {
  const payload = collectRuntimeSettings();
  const secrets = collectRuntimeSecrets();
  streamStateEl.textContent = "正在保存运行配置";
  try {
    state.runtimeConfig = await api("/api/runtime/config", {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
    if (secrets.bigmodel_api_key) {
      await api("/api/runtime/secrets", {
        method: "PATCH",
        body: JSON.stringify(secrets),
      });
      streamStateEl.textContent = state.runtimeConfig.restart_required
        ? "API Key 已立即生效，运行配置重启后生效"
        : "API Key 已保存并立即生效";
    } else {
      streamStateEl.textContent = state.runtimeConfig.restart_required
        ? "配置已保存，重启后生效"
        : "配置已保存并立即生效";
    }
    await Promise.all([loadHealth(), loadRuntimePanels(), refreshStatusline()]);
    renderSettings();
  } catch (error) {
    streamStateEl.textContent = `配置保存失败：${error instanceof Error ? error.message : "未知错误"}`;
  }
});

settingsRestartEl.addEventListener("click", async () => {
  settingsRestartEl.disabled = true;
  streamStateEl.textContent = "Nova 网关正在重启";
  try {
    await api("/api/runtime/restart", { method: "POST", body: JSON.stringify({}) });
    settingsNoteEl.textContent = "网关正在重启，请稍等后刷新或继续使用当前页面。";
  } catch (error) {
    streamStateEl.textContent = `重启请求失败：${error instanceof Error ? error.message : "未知错误"}`;
  }
});

sidebarToggleEl.addEventListener("click", () => {
  state.sidebarCollapsed = !state.sidebarCollapsed;
  writeStorageBool("nova.sidebarCollapsed", state.sidebarCollapsed);
  applyShellChromeState();
});

inspectorToggleEl.addEventListener("click", () => {
  state.inspectorCollapsed = !state.inspectorCollapsed;
  writeStorageBool("nova.inspectorCollapsed", state.inspectorCollapsed);
  applyShellChromeState();
});

statuslineToggleEl.addEventListener("click", () => {
  state.statuslineCollapsed = !state.statuslineCollapsed;
  writeStorageBool("nova.statuslineCollapsed", state.statuslineCollapsed);
  renderStatusline();
});

for (const button of document.querySelectorAll("[data-settings-section]")) {
  button.addEventListener("click", () => {
    const section = button.dataset.settingsSection;
    if (state.settingsCollapsed.has(section)) {
      state.settingsCollapsed.delete(section);
    } else {
      state.settingsCollapsed.add(section);
    }
    writeStorageList("nova.settingsCollapsed", state.settingsCollapsed);
    applySettingsSectionState();
  });
}

function collectRuntimeSettings() {
  const form = settingsDialogEl.querySelector(".settings-panel");
  const permissionConfig = derivePermissionConfig(form.querySelector('[name="permission_preset"]').value);
  return {
    provider_model: form.querySelector('[name="provider_model"]').value.trim(),
    provider_base_url: form.querySelector('[name="provider_base_url"]').value.trim(),
    context_window_tokens: Number(form.querySelector('[name="context_window_tokens"]').value),
    ...permissionConfig,
    network_access: form.querySelector('[name="network_access"]').checked,
    max_tool_rounds: Number(form.querySelector('[name="max_tool_rounds"]').value),
  };
}

function collectRuntimeSecrets() {
  const form = settingsDialogEl.querySelector(".settings-panel");
  return {
    bigmodel_api_key: form.querySelector('[name="bigmodel_api_key"]').value.trim(),
  };
}

function applyShellChromeState() {
  document.body.classList.toggle("sidebar-collapsed", state.sidebarCollapsed);
  document.body.classList.toggle("inspector-collapsed", state.inspectorCollapsed);
  sidebarToggleEl.textContent = state.sidebarCollapsed ? "›" : "‹";
  inspectorToggleEl.textContent = state.inspectorCollapsed ? "‹" : "›";
  sidebarToggleEl.setAttribute("aria-label", state.sidebarCollapsed ? "展开左侧栏" : "收起左侧栏");
  inspectorToggleEl.setAttribute("aria-label", state.inspectorCollapsed ? "展开右侧栏" : "收起右侧栏");
}

function applySettingsSectionState() {
  for (const button of document.querySelectorAll("[data-settings-section]")) {
    const section = button.dataset.settingsSection;
    const collapsed = state.settingsCollapsed.has(section);
    button.classList.toggle("collapsed", collapsed);
    const content = button.parentElement?.querySelector(section === "runtime" ? "#settings-runtime" : "#settings-statusline");
    if (content) {
      content.hidden = collapsed;
    }
  }
}

function openWorkspaceDialog() {
  workspaceDialogInputEl.value = workspaceInputEl.value.trim();
  state.workspaceDialogStatus = null;
  renderWorkspaceDialogState();
  workspaceDialogEl.showModal();
  scheduleWorkspaceDialogCandidates(0);
  workspaceDialogInputEl.focus();
  workspaceDialogInputEl.select();
}

async function switchWorkspaceFromDialog() {
  const path = workspaceDialogInputEl.value.trim();
  if (!path || !state.workspaceDialogStatus?.can_select) {
    return;
  }
  workspaceDialogEl.close();
  await switchWorkspace(path);
}

async function createWorkspaceFolderFromDialog() {
  const path = workspaceDialogInputEl.value.trim();
  if (!path || !state.workspaceDialogStatus?.can_create) {
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
    state.workspaceDialogStatus = workspaces.query_status || null;
    renderWorkspaceDialogList();
    renderWorkspaceDialogState();
  } catch (error) {
    if (requestId !== state.workspaceDialogRequestId) {
      return;
    }
    state.workspaceDialogCandidates = [];
    state.workspaceDialogStatus = null;
    renderWorkspaceDialogList(error instanceof Error ? error.message : "目录读取失败");
    renderWorkspaceDialogState(error instanceof Error ? error.message : "目录读取失败");
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

function renderWorkspaceDialogState(errorMessage = "") {
  const status = state.workspaceDialogStatus;
  if (errorMessage) {
    workspaceDialogStateEl.textContent = errorMessage;
    workspaceDialogStateEl.dataset.state = "error";
    workspaceDialogSubmitEl.disabled = true;
    workspaceDialogCreateEl.disabled = true;
    return;
  }
  const reason = status?.reason || "请输入或选择项目目录";
  workspaceDialogStateEl.textContent = reason;
  workspaceDialogStateEl.dataset.state = status?.can_select
    ? "select"
    : status?.can_create ? "create" : "blocked";
  workspaceDialogSubmitEl.disabled = !status?.can_select;
  workspaceDialogCreateEl.disabled = !status?.can_create;
  workspaceDialogSubmitEl.title = status?.can_select ? "切换到已存在目录" : reason;
  workspaceDialogCreateEl.title = status?.can_create ? "新建目录并切换" : reason;
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
    if (
      node.classList.contains("tool-event")
      || node.classList.contains("permission-event")
      || node.classList.contains("agent-status")
    ) {
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
    ? `展开本轮过程 · ${count} 个事件`
    : `收起本轮过程 · ${count} 个事件`;
}

function updateTurnToolControl(assistantNode) {
  const button = assistantNode?.querySelector?.(".turn-tools-toggle");
  if (!button) {
    return;
  }
  const processNodes = turnProcessNodes(assistantNode);
  const count = processNodes.filter((node) => (
    node.classList.contains("tool-event") || node.classList.contains("permission-event")
  )).length;
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
  if (!content) {
    return;
  }
  if (state.sending) {
    try {
      const sessionId = await ensureSession();
      const queued = await fetch(`/api/chat/sessions/${sessionId}/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      if (queued.status !== 202) {
        throw new Error(await queued.text());
      }
      const payload = await queued.json();
      appendMessage(payload.message, { queued: true });
      messageEl.value = "";
      autoResizeTextarea();
      streamStateEl.textContent = "消息已排队，当前工具轮结束后进入上下文";
    } catch (error) {
      streamStateEl.textContent = `排队失败：${error instanceof Error ? error.message : "未知错误"}`;
    }
    return;
  }
  state.sending = true;
  sendButtonEl.disabled = false;
  sendButtonEl.querySelector(".send-label").textContent = "排队";
  streamStateEl.textContent = "正在连接模型";

  let assistantNode = null;
  try {
    const sessionId = await ensureSession();
    await refreshStatusline();
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
    renderStatusline();
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
  let currentAssistantNode = assistantNode;
  const handleRuntimeEvent = (event) => {
    // runtime_event 是后端统一运行时协议；旧事件仍负责渲染工具详情，避免实时视图重复。
    if (event.event_type === "turn.started") {
      streamStateEl.textContent = event.title || "Nova 正在处理";
      appendStatusEvent(event.title || "开始处理用户请求", { beforeNode: currentAssistantNode });
      updateTurnToolControl(currentAssistantNode);
      return;
    }
    if (event.event_type === "turn.completed") {
      streamStateEl.textContent = event.title || "回复完成";
      return;
    }
    if (event.event_type === "turn.failed") {
      streamStateEl.textContent = event.title || "请求失败";
      appendStatusEvent(event.message || event.title || "请求失败", { beforeNode: currentAssistantNode });
      updateTurnToolControl(currentAssistantNode);
      return;
    }
    if (event.event_type?.startsWith("hook.")) {
      appendStatusEvent(event.title || "Hook 事件", { beforeNode: currentAssistantNode });
      updateTurnToolControl(currentAssistantNode);
      return;
    }
    if (event.type === "status" || event.event_type === "memory.compacted") {
      appendStatusEvent(event.title || event.message || "运行状态更新", { beforeNode: currentAssistantNode });
      updateTurnToolControl(currentAssistantNode);
    }
  };
  const handleQueuedMessage = (event) => {
    const message = event.message || {
      id: `queued_${Date.now()}`,
      role: "user",
      content: "排队消息",
      created_at: new Date().toISOString(),
    };
    appendMessage(message, { queued: false });
    currentAssistantNode = appendMessage({
      id: `local_assistant_${Date.now()}`,
      role: "assistant",
      content: "",
      created_at: null,
    });
    currentAssistantNode.classList.add("streaming");
    assistantText = "";
    activeToolNodes.clear();
    streamStateEl.textContent = "正在处理排队消息";
    return currentAssistantNode;
  };

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const result = consumeStreamLines(buffer, assistantNode, {
      onDelta: (delta) => {
        assistantText += delta;
        updateMessage(currentAssistantNode, assistantText);
        streamStateEl.textContent = "Nova 正在输出";
      },
      onToolStart: (event) => {
        streamStateEl.textContent = `工具执行：${event.tool}`;
        const node = appendToolEvent(event, currentAssistantNode);
        activeToolNodes.set(event.call_id || event.tool || "tool", node);
        updateTurnToolControl(currentAssistantNode);
      },
      onToolDone: (event) => {
        const key = event.call_id || event.tool || "tool";
        finishToolEvent(activeToolNodes.get(key), event);
        activeToolNodes.delete(key);
        streamStateEl.textContent = event.ok ? "工具完成，继续推理" : "工具失败，继续处理";
      },
      onToolOutput: (event) => {
        const key = event.call_id || event.tool || "tool";
        appendToolOutput(activeToolNodes.get(key), event);
      },
      onPermissionRequest: (event) => {
        streamStateEl.textContent = `${event.tool || "工具"} 等待审批`;
        appendPermissionEvent(event, currentAssistantNode);
        updateTurnToolControl(currentAssistantNode);
      },
      onStatus: (event) => {
        streamStateEl.textContent = event.status || "运行中";
        appendStatusEvent(event.status || "运行中", { beforeNode: currentAssistantNode });
        updateTurnToolControl(currentAssistantNode);
      },
      onRuntimeEvent: handleRuntimeEvent,
      onQueuedMessage: handleQueuedMessage,
    }, { updateMessage, updateMessageMeta });
    buffer = result.rest;
    ok = ok && result.ok;
  }

  if (buffer.trim()) {
    const result = consumeStreamLines(`${buffer}\n`, assistantNode, {
      onDelta: (delta) => {
        assistantText += delta;
        updateMessage(currentAssistantNode, assistantText);
      },
      onToolStart: (event) => {
        const node = appendToolEvent(event, currentAssistantNode);
        activeToolNodes.set(event.call_id || event.tool || "tool", node);
        updateTurnToolControl(currentAssistantNode);
      },
      onToolDone: (event) => {
        const key = event.call_id || event.tool || "tool";
        finishToolEvent(activeToolNodes.get(key), event);
        activeToolNodes.delete(key);
        streamStateEl.textContent = event.ok ? "工具完成，继续推理" : "工具失败，继续处理";
      },
      onToolOutput: (event) => {
        const key = event.call_id || event.tool || "tool";
        appendToolOutput(activeToolNodes.get(key), event);
      },
      onPermissionRequest: (event) => {
        streamStateEl.textContent = `${event.tool || "工具"} 等待审批`;
        appendPermissionEvent(event, currentAssistantNode);
        updateTurnToolControl(currentAssistantNode);
      },
      onStatus: (event) => {
        streamStateEl.textContent = event.status || "运行中";
        appendStatusEvent(event.status || "运行中", { beforeNode: currentAssistantNode });
        updateTurnToolControl(currentAssistantNode);
      },
      onRuntimeEvent: handleRuntimeEvent,
      onQueuedMessage: handleQueuedMessage,
    }, { updateMessage, updateMessageMeta });
    ok = ok && result.ok;
  }
  return ok;
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
  renderStatusline();
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
applyShellChromeState();
