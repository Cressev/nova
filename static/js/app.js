import { api } from "./api/client.js";
import { BUILTIN_COMMANDS, filterCommandMatches, nextCommandSelectionIndex } from "./components/command_palette.js";
import {
  chooseWorkspaceTabCompletion,
  groupWorkspaceDialogItems,
} from "./components/workspace_picker.js";
import { consumeStreamLines } from "./runtime/stream.js";
import {
  readStorageBool,
  readStorageList,
  writeStorageBool,
  writeStorageList,
} from "./state/storage.js";
import { queryRequired } from "./ui/dom.js";

const DEFAULT_STATUSLINE_ITEMS = [
  "model",
  "context",
  "tokens",
  "session",
  "project",
  "permission",
  "background_tasks",
];

const state = {
  selectedSessionId: null,
  selectedSessionTitle: "Nova Chat",
  sending: false,
  sessionActive: false,
  turnCancelRequested: false,
  streamAbortController: null,
  queuedMessages: [],
  showAllSessionGroups: readStorageBool("nova.showAllSessionGroups", false),
  collapsedProjects: new Set(readStorageList("nova.collapsedProjects")),
  expandedSessionGroups: new Set(readStorageList("nova.expandedSessionGroups")),
  workspaceCandidates: [],
  workspaceRecentProjects: [],
  workspaceCompletion: null,
  workspaceSuggestionIndex: -1,
  workspaceDialogCandidates: [],
  workspaceDialogIndex: -1,
  workspaceDialogRequestId: 0,
  workspaceDialogStatus: null,
  messagesRequestId: 0,
  runtimePanelsRequestId: 0,
  runtimeConfig: null,
  worktrees: null,
  processes: [],
  statusline: null,
  statuslineItems: ensureStatuslineDefaults(readStorageList("nova.statuslineItems", DEFAULT_STATUSLINE_ITEMS)),
  sidebarCollapsed: readStorageBool("nova.sidebarCollapsed", false),
  inspectorCollapsed: readStorageBool("nova.inspectorCollapsed", false),
  statuslineCollapsed: readStorageBool("nova.statuslineCollapsed", false),
  settingsCollapsed: new Set(readStorageList("nova.settingsCollapsed")),
  commands: BUILTIN_COMMANDS,
  commandSelectionIndex: -1,
  mcp: null,
  review: null,
  subagents: [],
  skills: null,
};


// ---- dsh 形态重构兼容层 ----
// 新 UI 砍掉了 Inspector、模式卡、技能卡、状态卡、状态线、消息导航轨等面板。
// 旧代码大量渲染/绑定仍引用这些节点；对已删除的 id 返回幽灵节点使旧逻辑无操作化，
// 避免逐行清理 4000 行引入回归。后续迭代再逐步删除这些死代码。
const REMOVED_ELEMENT_IDS = new Set([
  "health", "provider", "chat-title", "thread-state", "runtime-overview", "runtime-state-panel",
  "project-root", "workspace-path", "workspace-state", "workspace-project", "workspace-details",
  "review-state", "review-summary", "quality-gate-summary", "review-risks", "review-tests",
  "review-run-tests", "review-refresh", "review-test-output",
  "subagent-spawn", "subagent-prompt", "subagent-list",
  "mode-list", "mode-pill", "skill-count", "skill-list",
  "permissions-list", "test-command", "serve-command",
  "worktree-name", "worktree-create", "worktree-diff", "worktree-cleanup", "worktree-list", "worktree-diff-output",
  "process-state", "process-list", "tool-count", "tool-list", "mcp-state", "mcp-list",
  "memory-state", "memory-list", "config-state", "config-list",
  "workspace-form", "workspace-input", "workspace-candidates", "workspace-suggestions",
  "composer-statusline", "message-rail", "statusline-toggle", "thread-search",
  "inspector-dialog", "inspector-dialog-title", "inspector-dialog-close", "inspector-toggle",
  "memory-dialog", "memory-dialog-title", "memory-dialog-name", "memory-dialog-content",
  "memory-dialog-state", "memory-dialog-close", "memory-dialog-cancel", "memory-dialog-save",
]);

const ghostClassList = { add() {}, remove() {}, toggle() {}, contains() { return false; } };
const ghostStyle = new Proxy({}, { get: () => () => undefined, set: () => true });

function makeGhostElement(id) {
  const tag = `ghost:${id}`;
  const proxy = new Proxy(function noop() {}, {
    get(_t, prop) {
      switch (prop) {
        case "isConnected": return false;
        case "classList": return ghostClassList;
        case "style": return ghostStyle;
        case "dataset": return {};
        case "children":
        case "childNodes": return [];
        case "textContent":
        case "innerText":
        case "innerHTML":
        case "value": return "";
        case "hidden":
        case "disabled":
        case "checked":
        case "open": return false;
        case "getAttribute": return () => null;
        case "querySelector": return () => null;
        case "querySelectorAll": return () => [];
        case "closest": return () => null;
        case "appendChild":
        case "removeChild":
        case "insertBefore":
        case "replaceChildren":
        case "remove":
        case "removeAttribute":
        case "setAttribute":
        case "scrollTo":
        case "scrollIntoView":
        case "focus":
        case "blur":
        case "click":
        case "addEventListener":
        case "removeEventListener":
        case "showModal":
        case "close": return () => undefined;
        default: return undefined;
      }
    },
    set() { return true; },
    apply() { return undefined; },
  });
  return proxy;
}

const originalQuerySelector = Document.prototype.querySelector;
Document.prototype.querySelector = function patchedQuerySelector(selector) {
  if (typeof selector === "string" && selector.startsWith("#")) {
    const id = selector.slice(1);
    if (REMOVED_ELEMENT_IDS.has(id)) return makeGhostElement(id);
  }
  return originalQuerySelector.call(this, selector);
};
// 幽灵节点实现：所有 DOM 读写都是无操作；isConnected=false 供旧 guard 短路。

const healthEl = queryRequired("#health");
const providerEl = queryRequired("#provider");
const newChatEl = document.querySelector("#new-chat");
const form = document.querySelector("#chat-form");
const messageEl = document.querySelector("#message");
const sendButtonEl = document.querySelector("#send-button");
const stopButtonEl = document.querySelector("#stop-button");
const streamStateEl = document.querySelector("#stream-state");
const threadStateEl = document.querySelector("#thread-state");
const runtimeOverviewEl = document.querySelector("#runtime-overview");
const runtimeStatePanelEl = document.querySelector("#runtime-state-panel");
const sessionListEl = document.querySelector("#session-list");
const messagesEl = document.querySelector("#messages");
const chatTitleEl = document.querySelector("#chat-title");
const projectNameEl = document.querySelector("#project-name");
const projectRootEl = document.querySelector("#project-root");
const workspacePathEl = document.querySelector("#workspace-path");
const workspaceStateEl = document.querySelector("#workspace-state");
const workspaceProjectEl = document.querySelector("#workspace-project");
const workspaceDetailsEl = document.querySelector("#workspace-details");
const reviewStateEl = document.querySelector("#review-state");
const reviewSummaryEl = document.querySelector("#review-summary");
const qualityGateSummaryEl = document.querySelector("#quality-gate-summary");
const reviewRisksEl = document.querySelector("#review-risks");
const reviewTestsEl = document.querySelector("#review-tests");
const reviewRunTestsEl = document.querySelector("#review-run-tests");
const reviewRefreshEl = document.querySelector("#review-refresh");
const reviewTestOutputEl = document.querySelector("#review-test-output");
const subagentSpawnEl = document.querySelector("#subagent-spawn");
const subagentPromptEl = document.querySelector("#subagent-prompt");
const subagentListEl = document.querySelector("#subagent-list");
const modeListEl = document.querySelector("#mode-list");
const modePillEl = document.querySelector("#mode-pill");
const skillCountEl = document.querySelector("#skill-count");
const skillListEl = document.querySelector("#skill-list");
const permissionsListEl = document.querySelector("#permissions-list");
const testCommandEl = document.querySelector("#test-command");
const serveCommandEl = document.querySelector("#serve-command");
const worktreeNameEl = document.querySelector("#worktree-name");
const worktreeCreateEl = document.querySelector("#worktree-create");
const worktreeDiffEl = document.querySelector("#worktree-diff");
const worktreeCleanupEl = document.querySelector("#worktree-cleanup");
const worktreeListEl = document.querySelector("#worktree-list");
const worktreeDiffOutputEl = document.querySelector("#worktree-diff-output");
const processStateEl = document.querySelector("#process-state");
const processListEl = document.querySelector("#process-list");
const commandPaletteEl = document.querySelector("#command-palette");
const toolCountEl = document.querySelector("#tool-count");
const toolListEl = document.querySelector("#tool-list");
const mcpStateEl = document.querySelector("#mcp-state");
const mcpListEl = document.querySelector("#mcp-list");
const memoryStateEl = document.querySelector("#memory-state");
const memoryListEl = document.querySelector("#memory-list");
const configStateEl = document.querySelector("#config-state");
const configListEl = document.querySelector("#config-list");
const workspaceFormEl = document.querySelector("#workspace-form");
const workspaceOpenEl = document.querySelector("#workspace-open");
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
const inspectorDialogEl = document.querySelector("#inspector-dialog");
const inspectorDialogTitleEl = document.querySelector("#inspector-dialog-title");
const inspectorDialogCloseEl = document.querySelector("#inspector-dialog-close");
const memoryDialogEl = document.querySelector("#memory-dialog");
const memoryDialogTitleEl = document.querySelector("#memory-dialog-title");
const memoryDialogNameEl = document.querySelector("#memory-dialog-name");
const memoryDialogContentEl = document.querySelector("#memory-dialog-content");
const memoryDialogStateEl = document.querySelector("#memory-dialog-state");
const memoryDialogCloseEl = document.querySelector("#memory-dialog-close");
const memoryDialogCancelEl = document.querySelector("#memory-dialog-cancel");
const memoryDialogSaveEl = document.querySelector("#memory-dialog-save");

if (threadStateEl && threadStateEl.isConnected !== false && streamStateEl) {
  const syncThreadState = () => {
    threadStateEl.textContent = streamStateEl.textContent || "等待输入";
  };
  new MutationObserver(syncThreadState).observe(streamStateEl, {
    characterData: true,
    childList: true,
    subtree: true,
  });
  syncThreadState();
}
const sidebarToggleEl = document.querySelector("#sidebar-toggle");
const inspectorToggleEl = document.querySelector("#inspector-toggle");
const statuslineToggleEl = document.querySelector("#statusline-toggle");
const INSPECTOR_PANEL_TITLES = {
  workspace: "Workspace",
  review: "Review",
  run: "Run",
  processes: "Processes",
  permissions: "Permissions",
  tools: "Tools",
  subagents: "Sub Agents",
  memory: "Memory",
  config: "Config",
};
let workspaceSuggestTimer = null;
let workspaceDialogTimer = null;
const TOOL_TOOLTIP_DELAY_MS = 1000;
const MCP_DEMO_TOOL = "mcp__demo__echo";
const SESSION_PREVIEW_LIMIT = 5;
const SESSION_GROUP_PREVIEW_LIMIT = 8;

let commandMatches = [];

function ensureStatuslineDefaults(items) {
  const next = new Set(items);
  try {
    const version = window.localStorage.getItem("nova.statuslineSchemaVersion");
    if (version !== "2") {
      for (const id of DEFAULT_STATUSLINE_ITEMS) {
        next.add(id);
      }
      window.localStorage.setItem("nova.statuslineSchemaVersion", "2");
      writeStorageList("nova.statuslineItems", next);
    }
  } catch {
    for (const id of DEFAULT_STATUSLINE_ITEMS) {
      next.add(id);
    }
  }
  return next;
}

const STATUSLINE_ITEMS = [
  { id: "model", label: "模型" },
  { id: "context", label: "上下文剩余" },
  { id: "tokens", label: "Token 用量" },
  { id: "session", label: "Session ID" },
  { id: "project", label: "项目" },
  { id: "permission", label: "权限" },
  { id: "background_tasks", label: "后台任务" },
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

async function loadWorkspaceStatus({ quick = false, includePicker = true } = {}) {
  try {
    const workspaceStatusRequest = quick
      ? api("/api/workspace/status?quick=true")
      : api("/api/workspace/status");
    const [status, workspaces] = await Promise.all([
      workspaceStatusRequest,
      includePicker ? api("/api/workspaces") : Promise.resolve(null),
    ]);
    renderWorkspace(status);
    if (workspaces) {
      renderWorkspacePicker(workspaces);
    }
  } catch (error) {
    workspacePathEl.textContent = "工作区状态读取失败";
    workspaceProjectEl.textContent = "-";
    workspaceDetailsEl.innerHTML = '<p class="muted">工作区状态读取失败。</p>';
  }
}

async function loadWorkspaceCandidates(query = "") {
  const suffix = query ? `?q=${encodeURIComponent(query)}` : "";
  const workspaces = await api(`/api/workspaces${suffix}`);
  renderWorkspacePicker(workspaces);
  return workspaces;
}

async function loadRuntimeShell() {
  const requestId = ++state.runtimePanelsRequestId;
  const [config, tools, processes, statusline, worktrees] = await Promise.all([
    api("/api/runtime/config"),
    api("/api/tools"),
    api(processesEndpoint()),
    loadStatuslineData(),
    api("/api/worktrees"),
  ]);
  if (requestId !== state.runtimePanelsRequestId) {
    return;
  }
  state.runtimeConfig = config;
  state.worktrees = worktrees;
  state.statusline = statusline;
  state.processes = processes.items || [];
  renderRuntimeConfig(config);
  renderWorktrees(worktrees);
  renderToolCount(tools.items || []);
  renderProcessesPanel(state.processes);
  renderStatusline();
  renderSettings();
}

function scheduleRuntimeShellLoad() {
  const run = () => {
    void loadRuntimeShell().catch((error) => {
      streamStateEl.textContent = `运行状态加载失败：${error instanceof Error ? error.message : "未知错误"}`;
    });
  };
  const scheduleIdle = () => {
    if ("requestIdleCallback" in window) {
      window.requestIdleCallback(run, { timeout: 1200 });
      return;
    }
    run();
  };
  window.setTimeout(scheduleIdle, 650);
}

async function loadRuntimePanels() {
  const requestId = ++state.runtimePanelsRequestId;
  const [config, tools, mcp, review, subagents, processes, skills, memory, statusline, worktrees] = await Promise.all([
    api("/api/runtime/config"),
    api("/api/tools"),
    api("/api/mcp/status"),
    api("/api/review/summary"),
    api("/api/subagents"),
    api(processesEndpoint()),
    api("/api/skills/status"),
    api("/api/memory/status"),
    loadStatuslineData(),
    api("/api/worktrees"),
  ]);
  if (requestId !== state.runtimePanelsRequestId) {
    return;
  }
  state.runtimeConfig = config;
  state.worktrees = worktrees;
  state.statusline = statusline;
  state.mcp = mcp;
  state.review = review;
  state.subagents = subagents.items || [];
  state.processes = processes.items || [];
  state.skills = skills;
  renderRuntimeConfig(config);
  renderWorktrees(worktrees);
  renderTools(tools.items || []);
  renderMcpPanel(mcp);
  renderReviewPanel(review);
  renderSubagentsPanel(state.subagents);
  renderProcessesPanel(state.processes);
  renderSkillsPanel(skills);
  renderMemory(memory);
  renderStatusline();
  renderSettings();
}

async function loadInspectorPanelDetails(panel) {
  if (panel === "workspace") {
    await loadWorkspaceStatus({ quick: false });
    return;
  }
  if (panel === "review") {
    await refreshReviewPanel();
    return;
  }
  if (panel === "run") {
    const [worktrees, runtimeState] = await Promise.all([
      api("/api/worktrees"),
      state.selectedSessionId
        ? api(`/api/chat/sessions/${state.selectedSessionId}/runtime-state`)
        : Promise.resolve(null),
    ]);
    state.worktrees = worktrees;
    renderWorktrees(worktrees);
    if (runtimeState) {
      renderRuntimeOverview(runtimeState);
    } else {
      renderIdleRuntimeOverview();
    }
    return;
  }
  if (panel === "processes") {
    await refreshProcessesPanel();
    return;
  }
  if (panel === "tools") {
    const [tools, mcp, skills] = await Promise.all([
      api("/api/tools"),
      api("/api/mcp/status"),
      api("/api/skills/status"),
    ]);
    state.mcp = mcp;
    state.skills = skills;
    renderTools(tools.items || []);
    renderMcpPanel(mcp);
    renderSkillsPanel(skills);
    return;
  }
  if (panel === "subagents") {
    await refreshSubagentsPanel();
    return;
  }
  if (panel === "memory") {
    const memory = await api("/api/memory/status");
    renderMemory(memory);
    return;
  }
  if (panel === "config" || panel === "permissions") {
    await loadRuntimeShell();
  }
}

async function loadStatuslineData() {
  const suffix = state.selectedSessionId ? `?session_id=${encodeURIComponent(state.selectedSessionId)}` : "";
  return api(`/api/runtime/statusline${suffix}`);
}

function processesEndpoint() {
  const suffix = state.selectedSessionId ? `?session_id=${encodeURIComponent(state.selectedSessionId)}` : "";
  return `/api/processes${suffix}`;
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
      `${formatCompactNumber(Math.max((data.context_remaining_tokens || 0) - draftTokens, 0))} 剩余 / ${data.context_remaining_percent ?? "-"}% · ${contextBudgetLabel(data.context_budget_status)}`,
    ],
    tokens: [
      "Token",
      `${formatCompactNumber((data.used_tokens || 0) + draftTokens)} 已用${data.estimated ? " 估算" : ""}`,
    ],
    session: ["Session", data.session_id ? shortId(data.session_id) : "未创建"],
    project: ["项目", data.current_project || data.project || projectName(data.current_project_path || data.workspace || "")],
    permission: ["权限", data.permission_mode],
    background_tasks: ["后台任务", `${Number(data.background_task_count || data.background_tasks || 0)} 个`],
    state: ["状态", state.sending ? "working" : data.status],
  };
  statuslineEl.innerHTML = "";
  for (const item of STATUSLINE_ITEMS) {
    if (!state.statuslineItems.has(item.id)) {
      continue;
    }
    const [label, value] = rows[item.id] || [];
    const node = document.createElement("span");
    node.className = item.id === "context"
      ? `statusline-item context-status-${data.context_budget_status || "normal"}`
      : "statusline-item";
    node.innerHTML = `<strong>${escapeHtml(label)}</strong><em>${escapeHtml(String(value ?? "-"))}</em>`;
    statuslineEl.appendChild(node);
  }
}

function turnStatusLabel(status, active = false) {
  const labels = {
    running: "运行中",
    completed: "已完成",
    failed: "失败",
    cancelled: "已停止",
  };
  if (status) {
    return labels[status] || status;
  }
  return active ? "运行中" : "空闲";
}

function countByStatus(items = [], statuses = []) {
  const targets = new Set(statuses);
  return items.filter((item) => targets.has(item.status)).length;
}

function buildRuntimeOverviewItems(runtimeState = {}) {
  const runtime = runtimeState.runtime || runtimeState;
  const tools = runtime.tool_calls || [];
  const approvals = runtimeState.pending_approvals || [];
  const processes = runtimeState.processes || [];
  const queuedMessages = runtimeState.queued_messages || runtime.queued_messages || [];
  const runningTools = countByStatus(tools, ["running", "started", "background"]);
  const failedTools = countByStatus(tools, ["failed", "cancelled"]);
  const runningProcesses = countByStatus(processes, ["running"]);
  const turnStatus = runtime.current_turn?.status || (runtime.active || runtimeState.active ? "running" : "");
  return [
    {
      kind: "turn",
      label: "Turn",
      value: turnStatusLabel(turnStatus, Boolean(runtime.active || runtimeState.active)),
      status: turnStatus || "idle",
    },
    {
      kind: "tool",
      label: "工具",
      value: tools.length ? `${runningTools}/${tools.length}` : "0",
      status: failedTools ? "warning" : (runningTools ? "running" : "idle"),
    },
    {
      kind: "approval",
      label: "审批",
      value: approvals.length ? `${approvals.length}` : "0",
      status: approvals.length ? "warning" : "idle",
    },
    {
      kind: "process",
      label: "后台",
      value: processes.length ? `${runningProcesses}/${processes.length}` : "0",
      status: runningProcesses ? "running" : "idle",
    },
    {
      kind: "queue",
      label: "队列",
      value: queuedMessages.length ? `${queuedMessages.length}` : "0",
      status: queuedMessages.length ? "warning" : "idle",
    },
  ];
}

function renderRuntimeOverviewItems(items = []) {
  if (!runtimeOverviewEl) {
    return;
  }
  runtimeOverviewEl.innerHTML = "";
  for (const item of items) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = `runtime-chip ${item.status || "idle"}`;
    chip.dataset.runtimeKind = item.kind;
    chip.innerHTML = `
      <span>${escapeHtml(item.label)}</span>
      <strong>${escapeHtml(String(item.value))}</strong>
    `;
    chip.addEventListener("click", () => {
      if (item.kind === "tool" || item.kind === "approval") {
        openInspectorDialog("tools");
        return;
      }
      if (item.kind === "process") {
        openInspectorDialog("processes");
        return;
      }
      if (item.kind === "queue") {
        messageEl.focus();
      }
    });
    runtimeOverviewEl.appendChild(chip);
  }
}

function renderRuntimeOverview(runtimeState = {}) {
  renderRuntimeOverviewItems(buildRuntimeOverviewItems(runtimeState));
  renderRuntimeStatePanel(runtimeState);
}

function renderIdleRuntimeOverview() {
  const active = Boolean(state.sending || state.sessionActive);
  renderRuntimeOverview({
    runtime: {
      active,
      current_turn: active ? { status: "running" } : null,
      tool_calls: [],
      queued_messages: state.queuedMessages || [],
    },
    pending_approvals: [],
    processes: state.processes || [],
    queued_messages: state.queuedMessages || [],
    active,
  });
}

function renderRuntimeOverviewFromDom(turnStatus = "") {
  const toolCalls = Array.from(messagesEl.querySelectorAll(".tool-event")).map((node) => ({
    status: node.classList.contains("running")
      ? "running"
      : node.classList.contains("failed") ? "failed" : "completed",
  }));
  const pendingApprovals = Array.from(messagesEl.querySelectorAll(".permission-event.pending")).map((node) => ({
    id: node.dataset.callId || "",
  }));
  const active = Boolean(state.sending || state.sessionActive || turnStatus === "running");
  renderRuntimeOverview({
    runtime: {
      active,
      current_turn: { status: turnStatus || (active ? "running" : "completed") },
      tool_calls: toolCalls,
      queued_messages: state.queuedMessages || [],
    },
    pending_approvals: pendingApprovals,
    processes: state.processes || [],
    queued_messages: state.queuedMessages || [],
    active,
  });
}

function renderRuntimeStatePanel(runtimeState = {}) {
  if (!runtimeStatePanelEl) {
    return;
  }
  const runtime = runtimeState.runtime || runtimeState;
  const tools = runtime.tool_calls || [];
  const approvals = runtimeState.pending_approvals || [];
  const processes = runtimeState.processes || [];
  const queuedMessages = runtimeState.queued_messages || runtime.queued_messages || [];
  const turnStatus = runtime.current_turn?.status || (runtime.active || runtimeState.active ? "running" : "");
  const finalAnswer = runtime.final_answer?.content ? shortText(runtime.final_answer.content, 42) : "-";
  const rows = [
    ["Turn", turnStatusLabel(turnStatus, Boolean(runtime.active || runtimeState.active))],
    ["工具调用", runtimeStateSummary(tools, "tool")],
    ["待审批", approvals.length ? `${approvals.length} 个` : "0"],
    ["后台任务", runtimeStateSummary(processes, "process")],
    ["排队消息", queuedMessages.length ? `${queuedMessages.length} 条` : "0"],
    ["最终回复", finalAnswer],
  ];
  runtimeStatePanelEl.innerHTML = rows.map(([label, value]) => `
    <div class="runtime-state-row">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(String(value))}</strong>
    </div>
  `).join("");
}

function runtimeStateSummary(items = [], kind = "") {
  if (!Array.isArray(items) || items.length === 0) {
    return "0";
  }
  const running = countByStatus(items, ["running", "started", "background"]);
  const failed = countByStatus(items, ["failed", "cancelled"]);
  if (kind === "tool") {
    return `${running} 运行 / ${failed} 异常 / ${items.length} 总数`;
  }
  if (kind === "process") {
    return `${running} 运行 / ${items.length} 总数`;
  }
  return String(items.length);
}

function contextBudgetLabel(status) {
  if (status === "critical") {
    return "需压缩";
  }
  if (status === "warning") {
    return "接近上限";
  }
  return "正常";
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
    <label class="setting-field setting-field-wide setting-secret-field">
      <span>Langfuse Public Key</span>
      <input name="langfuse_public_key" type="password" value="" autocomplete="off" placeholder="${escapeHtml(config.langfuse_public_key_set ? "已设置，输入新 Key 可替换" : "用于捕捉 Agent 执行轨迹")}" />
      <small>${escapeHtml(config.langfuse_configured ? "Langfuse 已配置，下一轮请求开始上报 trace" : "需要 Public Key 和 Secret Key；不会回显明文")}</small>
    </label>
    <label class="setting-field setting-field-wide setting-secret-field">
      <span>Langfuse Secret Key</span>
      <input name="langfuse_secret_key" type="password" value="" autocomplete="off" placeholder="${escapeHtml(config.langfuse_secret_key_set ? "已设置，输入新 Key 可替换" : "填写后立即生效")}" />
      <small>仅保存在当前项目的 .nova/runtime-secrets.json。</small>
    </label>
    <label class="setting-field">
      <span>Langfuse Host</span>
      <input name="langfuse_host" type="text" value="${escapeHtml(config.langfuse_host || "https://cloud.langfuse.com")}" autocomplete="off" />
    </label>
    <label class="setting-field setting-field-inline">
      <span>Langfuse 上报</span>
      <input name="langfuse_enabled" type="checkbox" ${config.langfuse_enabled === false ? "" : "checked"} />
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
  workspaceStateEl.textContent = status.permissions?.permission_mode || "local";
  workspaceProjectEl.textContent = status.project_root ? projectName(status.project_root) : "-";

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

  renderWorkspaceDetails(status);
  renderPermissions(status.permissions);
  bindCommandChip(testCommandEl, status.commands.test, "运行测试");
  bindCommandChip(serveCommandEl, status.commands.serve, "启动服务");
}

function renderWorkspacePicker(workspaces) {
  state.workspaceCandidates = workspaces.candidates || [];
  state.workspaceRecentProjects = workspaces.recent_projects || [];
  state.workspaceCompletion = workspaces.completion || null;
  workspaceCandidatesEl.innerHTML = "";
  for (const path of state.workspaceCandidates) {
    const option = document.createElement("option");
    option.value = path;
    workspaceCandidatesEl.appendChild(option);
  }
  renderWorkspaceDialogList();
  renderWorkspaceSuggestions();
}

function renderWorkspaceDetails(status) {
  const rows = [
    ["项目", projectName(status.project_root)],
    ["路径", status.project_root],
    ["权限", status.permissions?.approval_policy || "-"],
    ["Shell", status.permissions?.shell_commands ? "允许" : "关闭"],
    ["网络", status.permissions?.network_access ? "允许" : "关闭"],
  ];
  workspaceDetailsEl.innerHTML = rows.map(([label, value]) => `
    <div class="workspace-detail-row">
      <span>${escapeHtml(label)}</span>
      <strong title="${escapeHtml(String(value))}">${escapeHtml(String(value))}</strong>
    </div>
  `).join("");
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
    ["Langfuse", config.langfuse_configured ? `已配置 · ${config.langfuse_host || "cloud"}` : "未配置"],
    ["上下文窗口", `${formatCompactNumber(config.context_window_tokens || 0)} tokens`],
    ["自动压缩阈值", `${formatCompactNumber(state.statusline?.auto_compact_threshold_tokens || 0)} tokens`],
    ["预算状态", `${contextBudgetLabel(state.statusline?.context_budget_status)}${state.statusline?.compact_recommended ? " · 建议 /compact" : ""}`],
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

function renderWorktrees(worktrees) {
  if (!worktreeListEl) {
    return;
  }
  if (worktrees?.error) {
    worktreeListEl.textContent = worktrees.error;
    worktreeCleanupEl.disabled = true;
    worktreeDiffEl.disabled = true;
    worktreeDiffOutputEl.textContent = "当前项目不是 Git 仓库，工作树模式不可用。";
    return;
  }
  const items = worktrees?.items || [];
  const current = worktrees?.current || "";
  worktreeCleanupEl.disabled = !current;
  worktreeDiffEl.disabled = !current;
  if (items.length === 0) {
    worktreeListEl.innerHTML = '<span class="muted">暂无 Nova 工作树</span>';
    return;
  }
  worktreeListEl.innerHTML = items.map((item) => `
    <button type="button" data-worktree-path="${escapeHtml(item.path)}">
      <strong>${escapeHtml(item.name)}${item.name === current ? " · 当前" : ""}</strong>
      <span>${escapeHtml(item.branch || "")} · ${item.dirty_count || 0} 个改动</span>
    </button>
  `).join("");
  for (const button of worktreeListEl.querySelectorAll("button[data-worktree-path]")) {
    button.addEventListener("click", () => switchWorkspace(button.dataset.worktreePath));
  }
}

async function createWorktreeFromPanel() {
  const name = worktreeNameEl.value.trim();
  if (!name) {
    streamStateEl.textContent = "请输入工作树名称";
    worktreeNameEl.focus();
    return;
  }
  worktreeCreateEl.disabled = true;
  streamStateEl.textContent = `正在创建并切换工作树 ${name}`;
  try {
    const created = await api("/api/worktrees", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
    worktreeDiffOutputEl.textContent = `已切换到 ${created.path}`;
    await refreshWorkspaceSurface();
    streamStateEl.textContent = `工作树 ${created.name} 已就绪`;
  } catch (error) {
    streamStateEl.textContent = `工作树创建失败：${error instanceof Error ? error.message : "未知错误"}`;
  } finally {
    worktreeCreateEl.disabled = false;
  }
}

async function showCurrentWorktreeDiff() {
  worktreeDiffEl.disabled = true;
  worktreeDiffOutputEl.textContent = "正在读取当前工作树 diff";
  try {
    const result = await api("/api/worktrees/current/diff");
    const diff = result.diff || "当前工作树没有未提交改动。";
    worktreeDiffOutputEl.textContent = [
      `工作树：${result.name}`,
      `路径：${result.path}`,
      `改动：${result.dirty_count}`,
      "",
      diff,
    ].join("\n");
    streamStateEl.textContent = "工作树 diff 已更新";
  } catch (error) {
    worktreeDiffOutputEl.textContent = error instanceof Error ? error.message : "diff 读取失败";
    streamStateEl.textContent = "工作树 diff 读取失败";
  } finally {
    worktreeDiffEl.disabled = !state.worktrees?.current;
  }
}

async function cleanupCurrentWorktree() {
  const current = state.worktrees?.current;
  if (!current) {
    streamStateEl.textContent = "当前没有可清理的 Nova 工作树";
    return;
  }
  const discard = window.confirm(`清理工作树 ${current} 会丢弃其中未提交改动，确认继续吗？`);
  if (!discard) {
    streamStateEl.textContent = "已取消工作树清理";
    return;
  }
  worktreeCleanupEl.disabled = true;
  streamStateEl.textContent = `正在清理工作树 ${current}`;
  try {
    await api(`/api/worktrees/${encodeURIComponent(current)}?discard=true`, { method: "DELETE" });
    worktreeDiffOutputEl.textContent = `已清理工作树 ${current}`;
    await refreshWorkspaceSurface();
    streamStateEl.textContent = `工作树 ${current} 已清理`;
  } catch (error) {
    streamStateEl.textContent = `工作树清理失败：${error instanceof Error ? error.message : "未知错误"}`;
  } finally {
    worktreeCleanupEl.disabled = !state.worktrees?.current;
  }
}

async function refreshWorkspaceSurface() {
  await Promise.all([
    loadWorkspaceStatus(),
    loadRuntimeShell(),
    loadSessions({ refreshMessages: false }),
    refreshStatusline(),
  ]);
}

function renderToolCount(items) {
  toolCountEl.textContent = `${items.length}`;
}

function renderTools(items) {
  renderToolCount(items);
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

function renderMcpPanel(mcp) {
  if (!mcpStateEl || !mcpListEl) {
    return;
  }
  const servers = mcp?.servers || [];
  const tools = mcp?.tools || [];
  const resources = mcp?.resources || [];
  mcpStateEl.textContent = servers.length > 0 ? `${servers.length} servers` : "未配置";
  mcpListEl.innerHTML = "";
  if (mcp?.error) {
    const error = document.createElement("div");
    error.className = "mcp-error";
    error.textContent = mcp.error;
    mcpListEl.appendChild(error);
    return;
  }
  if (servers.length === 0) {
    const empty = document.createElement("div");
    empty.className = "mcp-empty";
    empty.textContent = ".nova/mcp.json 未配置";
    mcpListEl.appendChild(empty);
    return;
  }
  for (const server of servers) {
    const card = document.createElement("article");
    card.className = `mcp-server ${server.status === "connected" ? "connected" : ""}`;
    const serverTools = tools.filter((tool) => tool.server === server.name);
    const serverResources = resources.filter((resource) => resource.server === server.name);
    const toolRows = serverTools.map((tool) => `
      <button class="mcp-tool" type="button" data-mcp-tool="${escapeHtml(tool.name)}">
        <span>${escapeHtml(tool.name)}</span>
        <small>${escapeHtml(tool.description || "")}</small>
      </button>
    `).join("");
    const resourceRows = serverResources.map((resource) => `
      <div class="mcp-resource">
        <span>${escapeHtml(resource.uri || resource.name || "")}</span>
        <small>${escapeHtml(resource.description || "")}</small>
      </div>
    `).join("");
    card.innerHTML = `
      <div class="mcp-server-head">
        <strong>${escapeHtml(server.name)}</strong>
        <span>${escapeHtml(server.transport || "-")} · ${escapeHtml(server.status || "-")}</span>
      </div>
      ${server.error ? `<div class="mcp-error">${escapeHtml(server.error)}</div>` : ""}
      <div class="mcp-section">
        <em>Tools</em>
        ${toolRows || '<small class="mcp-empty">暂无工具</small>'}
      </div>
      <div class="mcp-section">
        <em>Resources</em>
        ${resourceRows || '<small class="mcp-empty">暂无资源</small>'}
      </div>
    `;
    card.querySelectorAll("[data-mcp-tool]").forEach((button) => {
      button.addEventListener("click", () => callMcpDemoTool(button.dataset.mcpTool || MCP_DEMO_TOOL));
    });
    mcpListEl.appendChild(card);
  }
}

function renderReviewPanel(review) {
  if (!reviewStateEl || !reviewSummaryEl || !reviewRisksEl || !reviewTestsEl) {
    return;
  }
  const changedFiles = review?.changed_files || [];
  const risks = review?.risks || [];
  const suggestedTests = review?.suggested_tests || [];
  const errors = Number(review?.diagnostics?.summary?.error || 0);
  reviewStateEl.textContent = `${changedFiles.length} 文件 · ${risks.length} 风险 · ${errors} 错误`;
  reviewStateEl.className = errors > 0 || risks.some((item) => item.severity === "high") ? "review-state warning" : "review-state ready";
  reviewSummaryEl.textContent = review?.summary || "Review summary 暂无数据";
  renderQualityGateSummary(review?.quality_gates);
  reviewRisksEl.innerHTML = risks.length === 0
    ? '<small class="review-empty">暂无风险</small>'
    : risks.map((item) => `
      <article class="review-risk ${escapeHtml(item.severity || "info")}">
        <strong>${escapeHtml(item.title || "风险")}</strong>
        <span>${escapeHtml(item.detail || "")}</span>
      </article>
    `).join("");
  reviewTestsEl.innerHTML = suggestedTests.length === 0
    ? '<small class="review-empty">暂无建议测试</small>'
    : suggestedTests.map((item, index) => `
      <button type="button" class="review-test" data-review-test="${index}">
        <strong>${escapeHtml(item.label || "测试")}</strong>
        <span>${escapeHtml(item.command || "")}</span>
      </button>
    `).join("");
  reviewTestsEl.querySelectorAll("[data-review-test]").forEach((button) => {
    button.addEventListener("click", () => runReviewTests(button.dataset.reviewTest || "0"));
  });
}

function renderQualityGateSummary(qualityGates = {}) {
  if (!qualityGateSummaryEl) {
    return;
  }
  const warnings = Array.isArray(qualityGates.warnings) ? qualityGates.warnings : [];
  const stateText = qualityGates.commit_allowed ? "允许提交" : "暂不允许提交";
  qualityGateSummaryEl.className = `quality-gate-summary ${qualityGates.commit_allowed ? "ready" : "warning"}`;
  qualityGateSummaryEl.innerHTML = `
    <div>
      <strong>质量门禁 · ${escapeHtml(stateText)}</strong>
      <span>staged ${Number(qualityGates.staged_files?.length || 0)} · secrets ${Number(qualityGates.sensitive_findings?.length || 0)}</span>
    </div>
    <small>${escapeHtml(warnings.slice(0, 2).join("；") || "无阻断项")}</small>
  `;
}

async function refreshReviewPanel() {
  if (reviewStateEl) {
    reviewStateEl.textContent = "刷新中";
  }
  try {
    const review = await api("/api/review/summary");
    state.review = review;
    renderReviewPanel(review);
    streamStateEl.textContent = "Review 已刷新";
  } catch (error) {
    if (reviewSummaryEl) {
      reviewSummaryEl.textContent = `Review 读取失败：${error instanceof Error ? error.message : "未知错误"}`;
    }
  }
}

async function runReviewTests(index = "0") {
  const selected = state.review?.suggested_tests?.[Number(index)] || state.review?.suggested_tests?.[0] || null;
  if (reviewRunTestsEl) {
    reviewRunTestsEl.disabled = true;
  }
  if (reviewTestOutputEl) {
    reviewTestOutputEl.textContent = `正在运行：${selected?.command || "默认测试命令"}`;
  }
  try {
    const payload = await api("/api/review/run-tests", {
      method: "POST",
      body: JSON.stringify(selected?.command ? { command: selected.command } : {}),
    });
    if (reviewTestOutputEl) {
      reviewTestOutputEl.textContent = [
        `${payload.ok ? "通过" : "失败"} · exit ${payload.exit_code ?? "-"}`,
        `$ ${payload.command || ""}`,
        payload.stdout ? `stdout:\n${payload.stdout}` : "",
        payload.stderr ? `stderr:\n${payload.stderr}` : "",
      ].filter(Boolean).join("\n\n");
    }
    streamStateEl.textContent = payload.ok ? "Review 测试通过" : "Review 测试失败";
    await refreshReviewPanel();
  } catch (error) {
    if (reviewTestOutputEl) {
      reviewTestOutputEl.textContent = `测试运行失败：${error instanceof Error ? error.message : "未知错误"}`;
    }
  } finally {
    if (reviewRunTestsEl) {
      reviewRunTestsEl.disabled = false;
    }
  }
}

function renderSubagentsPanel(subagents = []) {
  if (!subagentListEl) {
    return;
  }
  const items = Array.isArray(subagents) ? subagents : [];
  if (items.length === 0) {
    subagentListEl.innerHTML = '<small class="subagent-empty">暂无子 Agent</small>';
    return;
  }
  subagentListEl.innerHTML = items.map((agent) => {
    const status = agent.status || "unknown";
    const result = agent.result || agent.error || "";
    const prompt = agent.prompt || "";
    return `
      <article class="subagent-item ${escapeHtml(status)}" data-subagent-id="${escapeHtml(agent.id || "")}">
        <div class="subagent-head">
          <strong>${escapeHtml(agent.name || "worker")}</strong>
          <span>${escapeHtml(status)}</span>
        </div>
        <p>${escapeHtml(shortText(prompt, 120))}</p>
        ${result ? `<pre>${escapeHtml(shortText(result, 1000))}</pre>` : ""}
        <div class="subagent-actions">
          <button type="button" data-action="wait">Wait</button>
          <button type="button" data-action="close">Close</button>
        </div>
      </article>
    `;
  }).join("");
  subagentListEl.querySelectorAll(".subagent-item").forEach((item) => {
    const id = item.dataset.subagentId || "";
    item.querySelector('[data-action="wait"]')?.addEventListener("click", () => waitSubagent(id));
    item.querySelector('[data-action="close"]')?.addEventListener("click", () => closeSubagent(id));
  });
}

async function refreshSubagentsPanel() {
  state.runtimePanelsRequestId += 1;
  const payload = await api("/api/subagents");
  state.subagents = payload.items || [];
  renderSubagentsPanel(state.subagents);
  renderStatusline();
}

async function spawnSubagent() {
  if (!subagentPromptEl || !subagentSpawnEl) {
    return;
  }
  const prompt = subagentPromptEl.value.trim();
  if (!prompt) {
    streamStateEl.textContent = "请输入要委派给子 Agent 的任务";
    subagentPromptEl.focus();
    return;
  }
  subagentSpawnEl.disabled = true;
  streamStateEl.textContent = "正在创建子 Agent";
  try {
    await api("/api/subagents", {
      method: "POST",
      body: JSON.stringify({ prompt, name: "worker" }),
    });
    subagentPromptEl.value = "";
    streamStateEl.textContent = "子 Agent 已创建";
    await refreshSubagentsPanel();
  } catch (error) {
    streamStateEl.textContent = `子 Agent 创建失败：${error instanceof Error ? error.message : "未知错误"}`;
  } finally {
    subagentSpawnEl.disabled = false;
  }
}

async function waitSubagent(id) {
  if (!id) {
    return;
  }
  streamStateEl.textContent = `等待子 Agent ${shortId(id)}`;
  try {
    const agent = await api(`/api/subagents/${encodeURIComponent(id)}/wait`, {
      method: "POST",
      body: JSON.stringify({ timeout_ms: 5000 }),
    });
    state.subagents = [agent, ...state.subagents.filter((item) => item.id !== id)];
    renderSubagentsPanel(state.subagents);
    streamStateEl.textContent = `子 Agent ${shortId(id)} 状态：${agent.status}`;
  } catch (error) {
    streamStateEl.textContent = `等待子 Agent 失败：${error instanceof Error ? error.message : "未知错误"}`;
  }
}

async function closeSubagent(id) {
  if (!id) {
    return;
  }
  try {
    const agent = await api(`/api/subagents/${encodeURIComponent(id)}`, { method: "DELETE" });
    state.subagents = [agent, ...state.subagents.filter((item) => item.id !== id)];
    renderSubagentsPanel(state.subagents);
    streamStateEl.textContent = `子 Agent ${shortId(id)} 已关闭`;
  } catch (error) {
    streamStateEl.textContent = `关闭子 Agent 失败：${error instanceof Error ? error.message : "未知错误"}`;
  }
}

function renderProcessesPanel(processes = []) {
  if (!processListEl || !processStateEl) {
    return;
  }
  const items = Array.isArray(processes) ? processes : [];
  const runningCount = items.filter((process) => ["running", "started"].includes(process.status)).length;
  processStateEl.textContent = `${runningCount}`;
  if (items.length === 0) {
    processListEl.innerHTML = '<small class="process-empty">暂无后台任务</small>';
    return;
  }
  processListEl.innerHTML = items.map((process) => {
    const status = process.status || "unknown";
    return `
      <article class="process-item ${escapeHtml(status)}" data-process-id="${escapeHtml(process.id || "")}">
        <div class="process-head">
          <strong>${escapeHtml(shortId(process.id || "proc"))}</strong>
          <span>${escapeHtml(status)}</span>
        </div>
        <code>${escapeHtml(shortText(process.command || "", 180))}</code>
        <small>${escapeHtml(process.cwd || "")}${process.call_id ? ` · call ${escapeHtml(shortId(process.call_id))}` : ""}</small>
        <div class="process-actions">
          <button type="button" data-action="inspect">Output</button>
          <button type="button" data-action="kill" ${status === "running" ? "" : "disabled"}>Kill</button>
        </div>
        <pre class="process-output"></pre>
      </article>
    `;
  }).join("");
  processListEl.querySelectorAll(".process-item").forEach((item) => {
    const id = item.dataset.processId || "";
    item.querySelector('[data-action="inspect"]')?.addEventListener("click", () => inspectProcess(item, id));
    item.querySelector('[data-action="kill"]')?.addEventListener("click", () => killProcess(id));
  });
}

async function refreshProcessesPanel() {
  const payload = await api(processesEndpoint());
  state.processes = payload.items || [];
  renderProcessesPanel(state.processes);
  await refreshStatusline();
}

async function inspectProcess(item, id) {
  const output = item.querySelector(".process-output");
  if (!id || !output) {
    return;
  }
  output.textContent = "正在读取输出...";
  try {
    const process = await api(`/api/processes/${encodeURIComponent(id)}`);
    output.textContent = [
      process.stdout ? `stdout:\n${process.stdout}` : "",
      process.stderr ? `stderr:\n${process.stderr}` : "",
      !process.stdout && !process.stderr ? "暂无输出" : "",
    ].filter(Boolean).join("\n\n");
  } catch (error) {
    output.textContent = `读取失败：${error instanceof Error ? error.message : "未知错误"}`;
  }
}

async function killProcess(id) {
  if (!id) {
    return;
  }
  streamStateEl.textContent = `正在终止后台任务 ${shortId(id)}`;
  try {
    await api(`/api/processes/${encodeURIComponent(id)}`, { method: "DELETE" });
    streamStateEl.textContent = `后台任务 ${shortId(id)} 已终止`;
    await refreshProcessesPanel();
  } catch (error) {
    streamStateEl.textContent = `终止后台任务失败：${error instanceof Error ? error.message : "未知错误"}`;
  }
}

function renderSkillsPanel(report) {
  if (!skillCountEl || !skillListEl) {
    return;
  }
  const skills = report?.skills || [];
  skillCountEl.textContent = String(skills.length);
  skillListEl.innerHTML = "";
  if (skills.length === 0) {
    skillListEl.textContent = "未发现技能";
    return;
  }
  for (const skill of skills.slice(0, 18)) {
    const card = document.createElement("article");
    card.className = `skill-item ${skill.scope === "project" ? "project" : "global"}`;
    card.dataset.skillName = skill.name || "";
    card.dataset.skillScope = skill.scope || "";
    card.innerHTML = `
      <button class="skill-trigger" type="button" data-skill-name="${escapeHtml(skill.name || "")}">
        <strong>${escapeHtml(skill.trigger || `$${skill.name || ""}`)}</strong>
        <span>${escapeHtml(skill.scope || "-")}</span>
      </button>
      <small>${escapeHtml(skill.description || skill.preview || "无说明")}</small>
      <div class="skill-actions">
        <button type="button" data-action="use-skill" data-skill-name="${escapeHtml(skill.name || "")}">调用</button>
        <button type="button" data-action="read-skill" data-skill-name="${escapeHtml(skill.name || "")}" data-skill-scope="${escapeHtml(skill.scope || "")}">查看</button>
      </div>
      <pre class="skill-content" hidden></pre>
    `;
    card.querySelectorAll('[data-action="use-skill"], .skill-trigger').forEach((button) => {
      button.addEventListener("click", () => fillSkillCommand(skill.name || ""));
    });
    card.querySelector('[data-action="read-skill"]')?.addEventListener("click", () => readSkillCard(card));
    skillListEl.appendChild(card);
  }
}

function fillSkillCommand(skillName) {
  if (!skillName) {
    return;
  }
  messageEl.value = `$${skillName} `;
  autoResizeTextarea();
  messageEl.focus();
}

async function readSkillCard(card) {
  const skillName = card.dataset.skillName || "";
  const scope = card.dataset.skillScope || "";
  const output = card.querySelector(".skill-content");
  if (!skillName || !scope || !output) {
    return;
  }
  output.hidden = false;
  output.textContent = "读取 SKILL.md 中...";
  try {
    const detail = await api(`/api/skills/${encodeURIComponent(scope)}/${encodeURIComponent(skillName)}`);
    output.textContent = detail.content || "SKILL.md 为空";
  } catch (error) {
    output.textContent = `读取失败：${error instanceof Error ? error.message : "未知错误"}`;
  }
}

async function callMcpDemoTool(toolName) {
  if (!toolName) {
    return;
  }
  const startEvent = {
    call_id: `mcp-ui-${Date.now()}`,
    tool: toolName,
    title: `MCP demo 调用：${toolName}`,
    arguments: { text: "hello from Nova UI" },
    data: {
      spec: {
        permission: "mcp",
        risk: "low",
        category: "mcp",
        schema: { text: "要原样返回的文本" },
      },
    },
  };
  const node = appendToolEvent(startEvent);
  try {
    const payload = await api(`/api/mcp/tools/${encodeURIComponent(toolName)}/call`, {
      method: "POST",
      body: JSON.stringify({ arguments: { text: "hello from Nova UI" } }),
    });
    const doneEvent = (payload.events || []).findLast?.((event) => event.type === "tool_done")
      || (payload.events || []).find((event) => event.type === "tool_done")
      || {
        tool: toolName,
        title: "MCP demo 调用完成",
        ok: true,
        output: payload.result?.content || "",
        data: payload.result_json?.data || {},
      };
    finishToolEvent(node, doneEvent);
    streamStateEl.textContent = "MCP demo 调用完成";
  } catch (error) {
    finishToolEvent(node, {
      tool: toolName,
      title: "MCP demo 调用失败",
      ok: false,
      output: error instanceof Error ? error.message : "未知错误",
      data: {
        failure_reason: error instanceof Error ? error.message : "未知错误",
        spec: { permission: "mcp", risk: "low", category: "mcp", schema: {} },
      },
    });
    streamStateEl.textContent = "MCP demo 调用失败";
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
  const candidates = memory.memory_candidates || [];
  if (candidates.length > 0) {
    appendMemoryCandidateGroup(candidates);
  }
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

function appendMemoryCandidateGroup(candidates) {
  const heading = document.createElement("div");
  heading.className = "memory-heading";
  heading.textContent = "待确认记忆";
  memoryListEl.appendChild(heading);
  for (const candidate of candidates) {
    memoryListEl.appendChild(renderMemoryCandidate(candidate));
  }
}

function renderMemoryCandidate(candidate) {
  const card = document.createElement("article");
  card.className = "memory-candidate";
  card.dataset.candidateId = candidate.id || "";
  card.innerHTML = `
    <div class="memory-candidate-main">
      <strong>${escapeHtml(candidate.content || "")}</strong>
      <span>${escapeHtml(candidate.source || "manual")} -> ${escapeHtml(candidate.name || "index.md")}</span>
    </div>
    <div class="memory-candidate-actions">
      <button type="button" data-action="approve">确认</button>
      <button type="button" data-action="edit">编辑</button>
      <button type="button" data-action="deny">拒绝</button>
    </div>
  `;
  card.querySelector('[data-action="approve"]').addEventListener("click", () => approveMemoryCandidate(candidate.id));
  card.querySelector('[data-action="edit"]').addEventListener("click", () => editMemoryCandidate(candidate));
  card.querySelector('[data-action="deny"]').addEventListener("click", () => denyMemoryCandidate(candidate.id));
  return card;
}

async function approveMemoryCandidate(candidateId) {
  if (!candidateId) {
    return;
  }
  await api(`/api/memory/candidates/${encodeURIComponent(candidateId)}/approve`, { method: "POST", body: "{}" });
  await loadRuntimeShell();
  streamStateEl.textContent = "记忆候选已确认写入";
}

function editMemoryCandidate(candidate) {
  openMemoryDialog({
    name: candidate.name || "index.md",
    content: candidate.content || "",
    mode: "candidate",
    source: "candidate",
    scope: "project",
    candidateId: candidate.id || "",
  });
}

async function denyMemoryCandidate(candidateId) {
  if (!candidateId) {
    return;
  }
  await api(`/api/memory/candidates/${encodeURIComponent(candidateId)}/deny`, {
    method: "POST",
    body: JSON.stringify({ reason: "用户在界面拒绝" }),
  });
  await loadRuntimeShell();
  streamStateEl.textContent = "记忆候选已拒绝";
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

function openMemoryDialog({ name, content, mode, source = "memory", scope = "project", candidateId = "" }) {
  const isPersona = source === "persona";
  const isCandidate = source === "candidate";
  memoryDialogEl.dataset.source = source;
  memoryDialogEl.dataset.scope = scope;
  memoryDialogEl.dataset.candidateId = candidateId;
  memoryDialogTitleEl.textContent = isCandidate
    ? "编辑候选记忆"
    : mode === "create"
    ? (isPersona ? "添加人格文件" : "添加记忆文件")
    : `编辑 ${isPersona ? "人格" : "记忆"} ${name}`;
  memoryDialogNameEl.value = normalizeMemoryFileName(name);
  memoryDialogNameEl.disabled = mode !== "create" && !isCandidate;
  memoryDialogContentEl.value = content || "";
  memoryDialogStateEl.textContent = isCandidate
    ? "编辑后点击保存，会立即确认并写入当前项目 .nova/memory。"
    : isPersona
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
  const candidateId = memoryDialogEl.dataset.candidateId || "";
  const isPersona = source === "persona";
  const isCandidate = source === "candidate";
  memoryDialogStateEl.textContent = isCandidate ? "正在确认候选记忆" : isPersona ? "正在保存人格文件" : "正在保存记忆文件";
  try {
    if (isCandidate) {
      await api(`/api/memory/candidates/${encodeURIComponent(candidateId)}/edit`, {
        method: "POST",
        body: JSON.stringify({ name, content: memoryDialogContentEl.value }),
      });
    } else {
      await api(isPersona ? "/api/persona/files" : "/api/memory/files", {
        method: "POST",
        body: JSON.stringify({ scope, name, content: memoryDialogContentEl.value }),
      });
    }
    memoryDialogEl.close();
    await loadRuntimeShell();
    streamStateEl.textContent = isCandidate ? `已确认候选记忆 ${name}` : `已更新${isPersona ? "人格" : "记忆"} ${name}`;
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
    state.sessionActive = false;
    syncComposerRunState();
    chatTitleEl.textContent = state.selectedSessionTitle;
    if (refreshMessages) {
      renderEmptyState();
    }
    return;
  }

  if (state.selectedSessionId && !sessions.some((session) => session.id === state.selectedSessionId)) {
    state.selectedSessionId = null;
    state.selectedSessionTitle = "新会话";
    state.sessionActive = false;
  }
  // dsh 形态：页面加载后自动恢复最近活跃会话（后端按 updated_at 倒序，sessions[0] 即最新），
  // 避免刷新后首条消息落入新会话导致上下文断裂（"问了上句忘记下句"）。
  // 用户点了"新会话"则保持空态（state.startNewChat），直到发出第一条消息。
  if (!state.selectedSessionId && !state.startNewChat && sessions.length > 0 && !state.autoRestored) {
    const recent = sessions[0];
    state.autoRestored = true;
    state.selectedSessionId = recent.id;
    state.selectedSessionTitle = recent.title || "Nova Chat";
    chatTitleEl.textContent = state.selectedSessionTitle;
    if (refreshMessages) {
      await loadMessages();
    }
  } else if (!state.selectedSessionId) {
    syncComposerRunState();
    if (refreshMessages) {
      renderEmptyState();
    }
  }

  const groups = groupSessionsByProject(sessions);
  const visibleGroups = selectVisibleSessionGroups(groups, {
    selectedSessionId: state.selectedSessionId,
    currentWorkspace: projectRootEl.textContent.trim(),
  });
  for (const group of visibleGroups) {
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
    const expanded = state.expandedSessionGroups.has(group.workspace);
    const visibleSessions = expanded ? group.sessions : group.sessions.slice(0, SESSION_PREVIEW_LIMIT);
    for (const session of visibleSessions) {
      itemsEl.appendChild(renderSessionItem(session));
    }
    if (group.sessions.length > SESSION_PREVIEW_LIMIT) {
      itemsEl.appendChild(renderSessionGroupMore(group, expanded));
    }
    sessionListEl.appendChild(groupNode);
  }
  if (!state.showAllSessionGroups && visibleGroups.length < groups.length) {
    const more = document.createElement("button");
    more.type = "button";
    more.className = "session-more session-group-more";
    more.textContent = `展开更多项目 ${groups.length - visibleGroups.length} 组`;
    more.addEventListener("click", (event) => {
      event.preventDefault();
      state.showAllSessionGroups = true;
      writeStorageBool("nova.showAllSessionGroups", true);
      loadSessions({ refreshMessages: false });
    });
    sessionListEl.appendChild(more);
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

function selectVisibleSessionGroups(groups, { selectedSessionId = null, currentWorkspace = "" } = {}) {
  if (state.showAllSessionGroups || groups.length <= SESSION_GROUP_PREVIEW_LIMIT) {
    return groups;
  }
  const selectedGroup = groups.find((group) =>
    group.sessions.some((session) => session.id === selectedSessionId),
  );
  const currentGroup = groups.find((group) => group.workspace === currentWorkspace);
  const visible = [];
  for (const group of [selectedGroup, currentGroup, ...groups]) {
    if (!group || visible.some((item) => item.workspace === group.workspace)) {
      continue;
    }
    visible.push(group);
    if (visible.length >= SESSION_GROUP_PREVIEW_LIMIT) {
      break;
    }
  }
  return visible;
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

function renderSessionGroupMore(group, expanded) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "session-more";
  button.textContent = expanded
    ? "收起历史"
    : `展开全部 ${group.sessions.length} 条`;
  button.addEventListener("click", (event) => {
    event.preventDefault();
    if (expanded) {
      state.expandedSessionGroups.delete(group.workspace);
    } else {
      state.expandedSessionGroups.add(group.workspace);
    }
    writeStorageList("nova.expandedSessionGroups", state.expandedSessionGroups);
    loadSessions({ refreshMessages: false });
  });
  return button;
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
      await Promise.all([loadWorkspaceStatus(), loadRuntimeShell()]);
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
  // dsh 形态：空状态由 hero 展示，消息区保持干净；
  // MutationObserver 会根据 messages 子元素数量自动显隐 hero。
  messagesEl.innerHTML = "";
}

function renderMessageLoadError(error) {
  const message = error instanceof Error ? error.message : "历史线程读取失败";
  messagesEl.innerHTML = `
    <div class="empty-state error-state">
      <h3>历史线程暂不可用</h3>
      <p>${escapeHtml(message)}</p>
      <div class="quick-actions">
        <button type="button" data-prompt="/status 总结当前项目状态">/status</button>
        <button type="button" data-prompt="/review 检查当前工作区">/review</button>
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
    state.sessionActive = false;
    syncComposerRunState();
    renderIdleRuntimeOverview();
    renderEmptyState();
    return;
  }
  const sessionId = state.selectedSessionId;
  const requestId = ++state.messagesRequestId;
  let runtimeState;
  try {
    runtimeState = await api(`/api/chat/sessions/${sessionId}/runtime-state`);
  } catch (error) {
    if (requestId !== state.messagesRequestId || sessionId !== state.selectedSessionId) {
      return;
    }
    renderMessageLoadError(error);
    renderIdleRuntimeOverview();
    streamStateEl.textContent = "历史线程暂不可用";
    return;
  }
  if (requestId !== state.messagesRequestId || sessionId !== state.selectedSessionId) {
    return;
  }
  if (runtimeState.unavailable) {
    state.sessionActive = false;
    syncComposerRunState();
    renderIdleRuntimeOverview();
    renderMessageLoadError(new Error(runtimeState.unavailable_reason || "历史线程所属项目不可用"));
    streamStateEl.textContent = "历史线程暂不可用";
    return;
  }
  state.sessionActive = Boolean(runtimeState.active);
  syncComposerRunState();
  renderRuntimeOverview(runtimeState);
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
  renderRuntimeOverview(runtimeState);
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
  if (event.type === "user_question" || event.type === "user.question" || event.event_type === "user.question") {
    appendUserQuestionEvent({
      call_id: event.call_id || event.id,
      tool: event.tool,
      title: event.title,
      message: event.message,
      questions: event.data?.questions || event.questions || [],
      data: event.data || {},
    });
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
    appendMessage(message, { queued: true });
    knownMessageIds.add(message.id);
  }
  setQueuedMessages(runtimeState.queued_messages || [], { autoscroll: false, renderOverview: false });
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
  // dsh 形态：运行状态不插入消息流（只在 composer 状态位显示），返回游离节点保持调用方兼容
  const node = document.createElement("div");
  node.className = "agent-status";
  node.textContent = text;
  return node;
}

function setQueuedMessages(messages, options = {}) {
  state.queuedMessages = Array.isArray(messages) ? messages.filter(Boolean) : [];
  renderQueueControl(options);
  if (options.renderOverview !== false) {
    renderIdleRuntimeOverview();
  }
}

function removeQueuedMessage(messageId) {
  if (!messageId) {
    return;
  }
  state.queuedMessages = state.queuedMessages.filter((message) => message.id !== messageId);
  renderQueueControl();
  renderIdleRuntimeOverview();
}

function renderQueueControl(options = {}) {
  messagesEl.querySelector("[data-queue-control]")?.remove();
  if (!state.queuedMessages.length) {
    return null;
  }
  const count = state.queuedMessages.length;
  const preview = shortText(state.queuedMessages[0]?.content || "", 72);
  const node = document.createElement("div");
  node.className = "agent-status queue-control";
  node.dataset.queueControl = "true";
  node.innerHTML = `
    <span>已排队 ${count} 条${preview ? `：${escapeHtml(preview)}` : ""}</span>
    <button type="button" data-action="clear-queue">清空队列</button>
  `;
  node.querySelector('[data-action="clear-queue"]').addEventListener("click", () => {
    void clearQueuedMessages();
  });
  messagesEl.appendChild(node);
  if (options.autoscroll !== false) {
    scrollMessagesToBottom();
  }
  return node;
}

function removeClearedQueueMessages(messages) {
  for (const message of messages || []) {
    if (!message?.id) {
      continue;
    }
    const node = messagesEl.querySelector(`[data-message-id="${CSS.escape(message.id)}"]`);
    if (!node) {
      continue;
    }
    if (node.classList.contains("queued")) {
      node.remove();
      continue;
    }
    const badge = node.querySelector(".message-queue-badge");
    if (badge) {
      badge.hidden = true;
    }
  }
  renderMessageRail();
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
  // dsh compaction：checkpoint 消息渲染为单行折叠行，不占对话气泡位
  const content = message.content || "";
  const isCheckpoint = (message.id || "").startsWith("comp_") || content.includes("<compacted-summary>");
  if (isCheckpoint) {
    const node = document.createElement("article");
    node.className = "message checkpoint";
    node.id = targetId;
    node.dataset.messageId = message.id || "";
    const summaryMatch = content.match(/<compacted-summary>([\s\S]*?)<\/compacted-summary>/);
    const summaryText = summaryMatch ? summaryMatch[1].trim() : content;
    node.innerHTML = `
      <details class="checkpoint-details">
        <summary>◷ 上下文检查点 · 更早的对话已压缩为摘要</summary>
        <pre class="checkpoint-body">${escapeHtml(summaryText)}</pre>
      </details>
    `;
    messagesEl.appendChild(node);
    scrollMessagesToBottom();
    return node;
  }
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
    <div class="message-content">${renderMarkdown(message.content || "")}</div>
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
  // 流式期间用 markdown 渲染（dsh AssistantMarkdown 等价）
  node.querySelector(".message-content").innerHTML = renderMarkdown(content);
  scrollMessagesToBottom();
}

function updateMessageMeta(node, message) {
  node.dataset.messageId = message.id || "";
  node.querySelector(".message-time").textContent = message.created_at
    ? formatTime(message.created_at)
    : "生成中";
}

function toolPurposeText(event) {
  const purpose = event.data?.annotation || event.annotation || event.title || "";
  return String(purpose || "").trim() || "工具执行中";
}

function toolCallId(event = {}) {
  return event.call_id || event.id || "";
}

function findRuntimeNodeByCallId(selector, callId) {
  if (!callId || !messagesEl) {
    return null;
  }
  return messagesEl.querySelector(`${selector}[data-call-id="${CSS.escape(callId)}"]`);
}

function findToolNodeByCallId(callId) {
  return findRuntimeNodeByCallId(".tool-event", callId);
}

function findPermissionNodeByCallId(callId) {
  return findRuntimeNodeByCallId(".permission-event", callId);
}


// dsh ToolRow 模式：点击头部行展开/收起工具详情
function bindToolRowToggle(node) {
  const head = node.querySelector(".tool-row");
  if (!head) return;
  const toggle = () => {
    const open = node.classList.toggle("expanded");
    head.setAttribute("aria-expanded", String(open));
  };
  head.addEventListener("click", (event) => {
    if (event.target.closest("button, a, select, input")) return;
    toggle();
  });
  head.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      toggle();
    }
  });
}


// ---- DSH ToolRow 图标库（从 dsh 本尊 GUI 实测提取的 SVG 路径）----
const DSH_TOOL_ICONS = {
  terminal: `<path transform="translate(0.6689 1.073)" d="M11.4818 5.57813C11.4818 4.45301 11.4807 3.66237 11.4075 3.05908C11.3359 2.46953 11.2024 2.13852 10.9939 1.89441C10.9247 1.81341 10.8493 1.73801 10.7683 1.66882C10.5242 1.46033 10.1932 1.32686 9.60364 1.25525C9.00034 1.18198 8.20974 1.18091 7.0846 1.18091L5.57813 1.18091C4.45301 1.18091 3.66238 1.18198 3.05908 1.25525C2.46953 1.32686 2.13852 1.46033 1.89441 1.66882C1.81341 1.73801 1.73801 1.81341 1.66882 1.89441C1.46033 2.13852 1.32686 2.46953 1.25525 3.05908C1.18198 3.66238 1.18091 4.45301 1.18091 5.57813L1.18091 6.2771C1.18091 7.40218 1.18197 8.19288 1.25525 8.79614C1.32687 9.38553 1.46036 9.71674 1.66882 9.96082C1.73797 10.0417 1.81347 10.1173 1.89441 10.1864C2.13851 10.3948 2.46965 10.5275 3.05908 10.5991C3.66238 10.6724 4.45298 10.6735 5.57813 10.6735L7.0846 10.6735C8.20977 10.6735 9.00033 10.6724 9.60364 10.5991C10.1931 10.5275 10.5242 10.3948 10.7683 10.1864C10.8493 10.1173 10.9247 10.0417 10.9939 9.96082C11.2024 9.71674 11.3358 9.38553 11.4075 8.79614C11.4808 8.19288 11.4818 7.40218 11.4818 6.2771L11.4818 5.57813ZM12.6627 6.2771C12.6627 7.37222 12.6637 8.247 12.5798 8.93799C12.4942 9.64284 12.3133 10.2359 11.8928 10.7282C11.7834 10.8562 11.6637 10.9751 11.5356 11.0845C11.0434 11.5049 10.4511 11.6867 9.74634 11.7723C9.05525 11.8563 8.17999 11.8552 7.0846 11.8552L5.57813 11.8552C4.48273 11.8552 3.60747 11.8563 2.91638 11.7723C2.21157 11.6867 1.61933 11.5049 1.12708 11.0845C0.99901 10.9751 0.879281 10.8562 0.769898 10.7282C0.349454 10.2359 0.168506 9.64284 0.0828864 8.93799C-0.00101964 8.247 4.88512e-07 7.37222 6.47206e-07 6.2771L6.47206e-07 5.57813C6.47206e-07 4.48273 -0.00106163 3.60747 0.0828864 2.91638C0.168502 2.21168 0.349594 1.61928 0.769898 1.12708C0.879302 0.998981 0.998981 0.879302 1.12708 0.769898C1.61928 0.349594 2.21168 0.168502 2.91638 0.0828864C3.60747 -0.00106163 4.48273 6.47206e-07 5.57813 6.47206e-07L7.0846 6.47206e-07C8.17999 6.47206e-07 9.05525 -0.00106163 9.74634 0.0828864C10.451 0.168505 11.0434 0.349587 11.5356 0.769898C11.6637 0.879302 11.7834 0.998981 11.8928 1.12708C12.3131 1.61928 12.4942 2.21169 12.5798 2.91638C12.6638 3.60747 12.6627 4.48273 12.6627 5.57813L12.6627 6.2771Z" fill="currentColor"/>`,
  chevron: `<path d="M11.8486 5.5L11.4238 5.92383L8.69727 8.65137C8.44157 8.90706 8.21562 9.13382 8.01172 9.29785C7.79912 9.46883 7.55595 9.61756 7.25 9.66602C7.08435 9.69222 6.91565 9.69222 6.75 9.66602C6.44405 9.61756 6.20088 9.46883 5.98828 9.29785C5.78438 9.13382 5.55843 8.90706 5.30273 8.65137L2.57617 5.92383L2.15137 5.5L3 4.65137L3.42383 5.07617L6.15137 7.80273C6.42595 8.07732 6.59876 8.24849 6.74023 8.3623C6.87291 8.46904 6.92272 8.47813 6.9375 8.48047C6.97895 8.48703 7.02105 8.48703 7.0625 8.48047C7.07728 8.47813 7.12709 8.46904 7.25977 8.3623C7.40124 8.24849 7.57405 8.07732 7.84863 7.80273L10.5762 5.07617L11 4.65137L11.8486 5.5Z" fill="currentColor"/>`,
};

// 工具名 → 前导图标（dsh 按变体分图标：bash 终端、read/browse 文档夹、其余通用）
function toolLeadingIcon(tool) {
  const name = String(tool || "").toLowerCase();
  const isDoc = /read|write|list|glob|file|edit|search|grep/.test(name);
  const icon = isDoc
    ? '<rect x="2.2" y="1.2" width="9.6" height="11.6" rx="2" stroke="currentColor" stroke-width="1.2" fill="none"/><path d="M4.8 5.2h4.4M4.8 7.4h4.4M4.8 9.6h2.8" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" fill="none"/>'
    : DSH_TOOL_ICONS.terminal;
  return `<svg width="14" height="14" viewBox="0 0 14 14" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">${icon}</svg>`;
}


// ---- 轻量 Markdown 渲染（dsh AssistantMarkdown 的最小等价物）----
// 先整体转义再生成标签，代码块内容二次保护，避免注入。
function renderMarkdown(raw) {
  const esc = (t) => t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  const inline = (t) => esc(t)
    .replace(/`([^`\n]+)`/g, '<code class="md-code">$1</code>')
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');

  const lines = String(raw || "").split("\n");
  const out = [];
  let inCode = false;
  let codeBuf = [];
  let listBuf = [];

  const flushList = () => {
    if (listBuf.length) {
      out.push('<ul class="md-list">' + listBuf.map((i) => `<li>${inline(i)}</li>`).join("") + "</ul>");
      listBuf = [];
    }
  };

  for (const line of lines) {
    const fence = line.match(/^```(\w*)/);
    if (fence) {
      if (inCode) {
        out.push(`<pre class="md-pre"><code>${esc(codeBuf.join("\n"))}</code></pre>`);
        codeBuf = [];
        inCode = false;
      } else {
        flushList();
        inCode = true;
      }
      continue;
    }
    if (inCode) {
      codeBuf.push(line);
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.*)/);
    if (heading) {
      flushList();
      const level = heading[1].length;
      out.push(`<h${level + 2} class="md-h">${inline(heading[2])}</h${level + 2}>`); // h3-h6，避免抢页面标题层级
      continue;
    }
    const li = line.match(/^\s*(?:[-*]|\d+\.)\s+(.*)/);
    if (li) {
      listBuf.push(li[1]);
      continue;
    }
    if (!line.trim()) {
      flushList();
      continue;
    }
    flushList();
    out.push(`<p class="md-p">${inline(line)}</p>`);
  }
  if (inCode && codeBuf.length) {
    out.push(`<pre class="md-pre"><code>${esc(codeBuf.join("\n"))}</code></pre>`);
  }
  flushList();
  return out.join("");
}

// 工具显示名映射（dsh 按变体给语义标签，如 Bash/Read；Nova 工具名转中文短标签）
const TOOL_DISPLAY_NAMES = {
  read: "Read",
  write: "Write",
  edit: "Edit",
  glob: "Glob",
  grep: "Grep",
  bash: "Bash",
  todo_write: "TodoWrite",
  web_fetch: "WebFetch",
  web_search: "WebSearch",
  memory_write: "MemoryWrite",
  memory_remove: "MemoryRemove",
  read_image: "ReadImage",
  skill: "Skill",
  job_output: "JobOutput",
  job_list: "JobList",
  job_kill: "JobKill",
  ask_user_question: "AskUser",
  subagent: "Subagent",
  list_agents: "ListAgents",
  send_message: "SendMessage",
  interrupt_agent: "InterruptAgent",
  create_goal: "CreateGoal",
  get_goal: "GetGoal",
  update_goal: "UpdateGoal",
  schedule_create: "ScheduleCreate",
  schedule_list: "ScheduleList",
  schedule_delete: "ScheduleDelete",
  lsp: "LSP",
  session_search: "SessionSearch",
};

// dsh ToolRow 五件套行：[16px 图标位] [工具名] [2px 分隔点] [摘要 flex 截断] [14px chevron]
function renderToolRowHead(tool, summary, extra = "") {
  const name = String(tool || "tool");
  const label = TOOL_DISPLAY_NAMES[name] || name.replaceAll("_", " ");
  return `<div class="tool-row" role="button" tabindex="0" aria-expanded="false">
    <span class="tool-leading">${toolLeadingIcon(tool)}</span>
    <span class="tool-title">${escapeHtml(label)}</span>
    <span class="tool-sep" aria-hidden="true"></span>
    <span class="tool-summary">${escapeHtml(summary || "")}${extra}</span>
    <svg class="tool-chevron" width="14" height="14" viewBox="0 0 14 14" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">${DSH_TOOL_ICONS.chevron}</svg>
  </div>`;
}

function appendToolEvent(event, beforeNode = null, options = {}) {
  const callId = toolCallId(event);
  const existingToolNode = findToolNodeByCallId(callId);
  const existingPermissionNode = findPermissionNodeByCallId(callId);
  const node = existingToolNode || existingPermissionNode || document.createElement("article");
  const wasInserted = Boolean(node.parentElement);
  node.className = `tool-event running${existingPermissionNode && !existingToolNode ? " approval-linked" : ""}`;
  node.dataset.callId = callId;
  node.dataset.tool = event.tool || "";
  node.dataset.arguments = JSON.stringify(event.arguments || {}, null, 2);
  node.dataset.argumentsRaw = JSON.stringify(event.arguments || {});
  node.dataset.toolData = JSON.stringify(event.data || {});
  node.innerHTML = `
    ${renderToolRowHead(event.tool, toolPurposeText(event))}
    <div class="tool-event-head" hidden>${''}</div>
    ${renderToolMetadata(event.data || {})}
    ${renderToolKeyParams(event.arguments || {})}
    <div class="tool-actions">
      <button class="tool-cancel" type="button" data-action="cancel-tool">取消</button>
    </div>
    <details class="tool-args">
      <summary>调用参数</summary>
      <pre>${escapeHtml(node.dataset.arguments)}</pre>
    </details>
  `;
  node.querySelector('[data-action="cancel-tool"]').addEventListener("click", () => cancelToolCall(node));
  bindToolRowToggle(node);
  if (!wasInserted) {
    if (beforeNode?.parentElement === messagesEl) {
      messagesEl.insertBefore(node, beforeNode);
    } else {
      messagesEl.appendChild(node);
    }
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
    renderRuntimeOverviewFromDom("cancelled");
  } catch (error) {
    if (button) {
      button.disabled = false;
      button.textContent = "取消";
    }
    streamStateEl.textContent = `取消失败：${error instanceof Error ? error.message : "未知错误"}`;
  }
}

function markRunningToolsAsCancelRequested() {
  for (const node of messagesEl.querySelectorAll(".tool-event.running")) {
    node.classList.remove("running");
    node.classList.add("failed");
    const status = node.querySelector(".tool-event-head em");
    if (status) {
      status.textContent = "已请求停止";
    }
    const button = node.querySelector('[data-action="cancel-tool"]');
    if (button) {
      button.disabled = true;
      button.textContent = "停止中";
    }
  }
  for (const node of messagesEl.querySelectorAll(".message.assistant.streaming")) {
    node.classList.remove("streaming");
    const time = node.querySelector(".message-time");
    if (time) {
      time.textContent = "已停止";
    }
  }
  updateAllTurnToolControls();
  renderRuntimeOverviewFromDom("cancelled");
}

function finishToolEvent(node, event, options = {}) {
  if (!node) {
    node = findToolNodeByCallId(toolCallId(event)) || findPermissionNodeByCallId(toolCallId(event)) || appendToolEvent(event);
  }
  node.className = `tool-event ${event.ok ? "ok" : "failed"}`;
  const args = node.dataset.arguments || "{}";
  const rawArgs = node.dataset.argumentsRaw || args;
  const data = event.data || {};
  const retryButton = !event.ok && data.retryable
    ? '<button class="tool-retry" type="button" data-action="retry-tool">重试</button>'
    : "";
  const statusLabel = event.data?.status === "cancelled" ? "已取消" : (event.ok ? "完成" : "失败");
  const failSummary = !event.ok && data.failure_reason ? String(data.failure_reason).split("\n")[0].slice(0, 120) : "";
  const doneSummary = failSummary || toolPurposeText(event) || "";
  node.innerHTML = `
    ${renderToolRowHead(event.tool, doneSummary)}
    <div class="tool-event-head" hidden>${''}</div>
    ${renderToolMetadata(data)}
    ${renderToolKeyParams(parseToolArguments(rawArgs))}
    ${renderHookContexts(data.hook_contexts)}
    ${data.failure_reason ? `<div class="tool-failure">${escapeHtml(data.failure_reason)}</div>` : ""}
    <div class="tool-actions">
      ${retryButton}
    </div>
    ${renderDiffPreview(data.diff)}
    ${renderToolExecutionDetails(args, event.output || "", data)}
  `;
  node.dataset.argumentsRaw = rawArgs;
  node.dataset.toolData = JSON.stringify(data);
  node.querySelector('[data-action="retry-tool"]')?.addEventListener("click", () => retryToolCall(node));
  bindToolRowToggle(node);
  syncBackgroundProcessFromToolDone(data);
  if (options.autoscroll !== false) {
    scrollMessagesToBottom();
  }
  renderRuntimeOverviewFromDom(state.sending || state.sessionActive ? "running" : "completed");
}

function renderToolMetadata(data = {}) {
  const spec = data.spec || {};
  const job = data.job || {};
  const jobId = data.job_id || job.id || "";
  const items = [
    ["权限", spec.permission || data.permission],
    ["风险", spec.risk],
    ["分类", spec.category],
    ["后台任务", data.background && jobId ? shortId(jobId) : ""],
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

function parseToolArguments(rawArgs) {
  try {
    return JSON.parse(rawArgs || "{}");
  } catch {
    return {};
  }
}

function renderToolKeyParams(args = {}) {
  const entries = Object.entries(args)
    .filter(([key, value]) => key !== "annotation" && value !== undefined && value !== null && value !== "")
    .slice(0, 4);
  if (entries.length === 0) {
    return "";
  }
  return `
    <div class="tool-key-params">
      ${entries.map(([key, value]) => `
        <span title="${escapeHtml(String(value))}">
          <em>${escapeHtml(key)}</em>
          <strong>${escapeHtml(shortText(String(value), 46))}</strong>
        </span>
      `).join("")}
    </div>
  `;
}

function renderToolExecutionDetails(args, output, data = {}) {
  const stdout = data.stdout || data.output || output || "";
  const stderr = data.stderr || "";
  const resultJson = data.result_json || data.result || null;
  return `
    <details class="tool-args">
      <summary>完整 args</summary>
      <pre>${escapeHtml(args || "{}")}</pre>
    </details>
    <details class="tool-result">
      <summary>stdout / stderr / result_json</summary>
      <pre>${escapeHtml([
        stdout ? `stdout:\n${shortText(String(stdout), 4000)}` : "",
        stderr ? `stderr:\n${shortText(String(stderr), 4000)}` : "",
        resultJson ? `result_json:\n${JSON.stringify(resultJson, null, 2)}` : "",
        !stdout && !stderr && !resultJson ? "暂无结构化输出" : "",
      ].filter(Boolean).join("\n\n"))}</pre>
    </details>
  `;
}

function syncBackgroundProcessFromToolDone(data = {}) {
  const job = data.job || {};
  const jobId = data.job_id || job.id || "";
  if (!data.background && !jobId) {
    return;
  }
  if (job && job.id) {
    state.processes = [job, ...state.processes.filter((item) => item.id !== job.id)];
    renderProcessesPanel(state.processes);
  }
  refreshProcessesPanel().catch(() => {});
}

function renderHookContexts(contexts = []) {
  const values = Array.isArray(contexts) ? contexts.filter(Boolean) : [];
  if (values.length === 0) {
    return "";
  }
  const body = values.map((context) => `<li>${escapeHtml(String(context))}</li>`).join("");
  return `
    <details class="hook-contexts" open>
      <summary>Hook 追加上下文</summary>
      <ul>${body}</ul>
    </details>
  `;
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
        const retryInsertBefore = node.nextSibling;
        const toolNode = appendToolEvent(event, retryInsertBefore);
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
    node = findToolNodeByCallId(toolCallId(event));
  }
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

function appendUserQuestionEvent(event) {
  const callId = event.call_id || "";
  const questions = Array.isArray(event.questions) ? event.questions : [];
  const node = document.createElement("article");
  node.className = "user-question-event pending";
  node.dataset.callId = callId;
  const fields = questions.map((question, index) => {
    const id = question.id || `q${index + 1}`;
    const header = question.header ? `<div class="question-head">${escapeHtml(question.header)}</div>` : "";
    const options = Array.isArray(question.options) && question.options.length
      ? question.options.map((option) => `
          <label class="question-option">
            <input type="${question.multi_select ? "checkbox" : "radio"}" name="opt-${escapeHtml(id)}" value="${escapeHtml(option.label)}" />
            <span><strong>${escapeHtml(option.label)}</strong>${option.description ? ` — ${escapeHtml(option.description)}` : ""}</span>
          </label>`).join("")
      : "";
    return `
      <div class="question-item" data-question-id="${escapeHtml(id)}">
        ${header}
        <div class="question-text">${escapeHtml(question.question || "")}</div>
        ${options ? `<div class="question-options">${options}</div>` : ""}
        ${options ? "" : `<input type="text" class="question-answer" data-question-id="${escapeHtml(id)}" placeholder="输入回答…" />`}
      </div>`;
  }).join("");
  node.innerHTML = `
    <div class="permission-event-head">
      <span>向用户提问</span>
      <strong>${escapeHtml(event.title || "ask_user_question")}</strong>
      <em>待回答</em>
    </div>
    <p>${escapeHtml(event.message || "等待用户回答后继续。")}</p>
    <div class="question-list">${fields}</div>
    <div class="permission-actions">
      <button type="button" data-action="answer">提交回答</button>
      <small>回答会作为工具结果续跑该调用</small>
    </div>
  `;
  node.querySelector('[data-action="answer"]').addEventListener("click", () => submitUserAnswers(node));
  messagesEl.appendChild(node);
  scrollMessagesToBottom();
  return node;
}

async function submitUserAnswers(node) {
  const callId = node.dataset.callId;
  if (!callId) {
    return;
  }
  const answers = {};
  let answered = false;
  node.querySelectorAll(".question-item").forEach((item) => {
    const id = item.dataset.questionId || "";
    const checked = Array.from(item.querySelectorAll("input[type=radio]:checked, input[type=checkbox]:checked")).map((input) => input.value);
    if (checked.length) {
      answers[id] = checked.length > 1 || item.querySelector('input[type="checkbox"]') ? checked : checked[0];
      answered = true;
      return;
    }
    const text = item.querySelector(".question-answer")?.value?.trim();
    if (text) {
      answers[id] = text;
      answered = true;
    }
  });
  if (!answered) {
    streamStateEl.textContent = "请至少回答一个问题";
    return;
  }
  node.querySelectorAll("button, input").forEach((element) => {
    element.disabled = true;
  });
  try {
    const response = await api(`/api/approvals/${encodeURIComponent(callId)}/answer`, {
      method: "POST",
      body: JSON.stringify({ answers }),
    });
    node.classList.remove("pending");
    node.classList.add("answered");
    node.querySelector(".permission-event-head em").textContent = "已回答";
    const activeToolNodes = new Map();
    for (const evt of response.events || []) {
      if (evt.type === "tool_start") {
        activeToolNodes.set(evt.call_id || evt.tool || "tool", appendToolEvent(evt, node));
      }
      if (evt.type === "tool_done") {
        finishToolEvent(activeToolNodes.get(evt.call_id || evt.tool || "tool"), evt);
      }
    }
    await Promise.all([loadRuntimeShell(), refreshStatusline()]);
    streamStateEl.textContent = "回答已提交，Nova 继续执行";
  } catch (error) {
    node.querySelector(".permission-event-head em").textContent = "提交失败";
    node.querySelectorAll("button, input").forEach((element) => {
      element.disabled = false;
    });
    streamStateEl.textContent = `提交失败：${error instanceof Error ? error.message : "未知错误"}`;
  }
}

function appendPermissionEvent(event, beforeNode = null, options = {}) {
  const callId = toolCallId(event);
  const existingNode = findPermissionNodeByCallId(callId) || findToolNodeByCallId(callId);
  if (existingNode) {
    return existingNode;
  }
  const args = JSON.stringify(event.arguments || {}, null, 2);
  const hookContexts = event.data?.hook_contexts || [];
  const node = document.createElement("article");
  node.className = "permission-event pending";
  node.dataset.callId = callId;
  node.dataset.tool = event.tool || "";
  node.innerHTML = `
    <div class="permission-event-head">
      <span>${escapeHtml(event.permission || event.data?.permission || "审批")}</span>
      <strong>${escapeHtml(event.title || `需要审批：${event.tool || "工具"}`)}</strong>
      <em>待确认</em>
    </div>
    <p>${escapeHtml(event.message || "执行该工具前需要用户确认。")}</p>
    ${renderHookContexts(hookContexts)}
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
  renderRuntimeOverviewFromDom("running");
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
          const toolNode = appendToolEvent(event, node);
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
    await Promise.all([loadRuntimeShell(), refreshStatusline()]);
    renderRuntimeOverviewFromDom(state.sending || state.sessionActive ? "running" : "completed");
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
  state.startNewChat = true;
  const session = await api("/api/chat/sessions", {
    method: "POST",
    body: JSON.stringify({ title: "新线程" }),
  });
  state.selectedSessionId = session.id;
  state.selectedSessionTitle = session.title;
  chatTitleEl.textContent = session.title;
  // dsh 形态：新会话回到干净的 hero 空态，清掉上一轮的状态残留
  messagesEl.innerHTML = "";
  streamStateEl.textContent = "";
  await Promise.all([loadSessions({ refreshMessages: false }), refreshStatusline()]);
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
    const choice = chooseWorkspaceTabCompletion({
      currentValue: workspaceInputEl.value.trim(),
      completion: state.workspaceCompletion,
      candidates: suggestions.map((item) => item.dataset.path),
      selectedIndex: event.key === "Enter"
        ? (state.workspaceSuggestionIndex >= 0 ? state.workspaceSuggestionIndex : 0)
        : state.workspaceSuggestionIndex,
    });
    if (choice.value) {
      event.preventDefault();
      workspaceInputEl.value = choice.value;
      if (choice.action === "complete") {
        state.workspaceSuggestionIndex = -1;
        scheduleWorkspaceSuggestions(0, choice.value);
      } else {
        workspaceSuggestionsEl.hidden = true;
        if (event.key === "Enter") {
          await switchWorkspace(choice.value);
        }
      }
    }
  }
});

workspaceInputEl.addEventListener("dblclick", openWorkspaceDialog);
workspaceOpenEl?.addEventListener("click", openWorkspaceDialog);

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
    const choice = chooseWorkspaceTabCompletion({
      currentValue: workspaceDialogInputEl.value.trim(),
      completion: state.workspaceCompletion,
      candidates: items.map((item) => item.dataset.path),
      selectedIndex: state.workspaceDialogIndex,
    });
    if (choice.value) {
      selectWorkspaceDialogCandidate(choice.value, { browseChildren: choice.action !== "complete" });
    }
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
  await loadRuntimeShell();
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
inspectorDialogCloseEl?.addEventListener("click", () => {
  inspectorDialogEl?.close();
});

for (const button of document.querySelectorAll("[data-inspector-target]")) {
  button.addEventListener("click", () => {
    openInspectorDialog(button.dataset.inspectorTarget || "workspace");
  });
}

for (const button of document.querySelectorAll("[data-inspector-tab]")) {
  button.addEventListener("click", () => {
    openInspectorDialog(button.dataset.inspectorTab || "workspace");
  });
}

worktreeCreateEl.addEventListener("click", createWorktreeFromPanel);
worktreeDiffEl.addEventListener("click", showCurrentWorktreeDiff);
worktreeCleanupEl.addEventListener("click", cleanupCurrentWorktree);
reviewRefreshEl?.addEventListener("click", refreshReviewPanel);
reviewRunTestsEl?.addEventListener("click", () => runReviewTests("0"));
subagentSpawnEl?.addEventListener("click", spawnSubagent);
subagentPromptEl?.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    event.preventDefault();
    spawnSubagent();
  }
});

settingsSaveEl.addEventListener("click", async () => {
  const payload = collectRuntimeSettings();
  const secrets = collectRuntimeSecrets();
  streamStateEl.textContent = "正在保存运行配置";
  try {
    state.runtimeConfig = await api("/api/runtime/config", {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
    if (Object.keys(secrets).length > 0) {
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
    await Promise.all([loadHealth(), loadRuntimeShell(), refreshStatusline()]);
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

function openInspectorDialog(panel = "workspace") {
  activateInspectorPanel(panel);
  if (inspectorDialogEl && !inspectorDialogEl.open) {
    inspectorDialogEl.showModal();
  }
  void loadInspectorPanelDetails(panel).catch((error) => {
    streamStateEl.textContent = `加载 ${INSPECTOR_PANEL_TITLES[panel] || "面板"} 失败：${error instanceof Error ? error.message : "未知错误"}`;
  });
}

function activateInspectorPanel(panel = "workspace") {
  const activePanel = INSPECTOR_PANEL_TITLES[panel] ? panel : "workspace";
  // 一个弹窗复用原来的多个右侧面板：默认只显示被入口点中的面板，
  // DOM 节点和 API 渲染目标不移动，避免打断现有刷新与按钮事件。
  for (const node of document.querySelectorAll("[data-inspector-panel]")) {
    node.hidden = node.dataset.inspectorPanel !== activePanel;
  }
  for (const tab of document.querySelectorAll("[data-inspector-tab]")) {
    tab.classList.toggle("active", tab.dataset.inspectorTab === activePanel);
  }
  if (inspectorDialogTitleEl) {
    inspectorDialogTitleEl.textContent = INSPECTOR_PANEL_TITLES[activePanel];
  }
}

sidebarToggleEl.addEventListener("click", () => {
  state.sidebarCollapsed = !state.sidebarCollapsed;
  writeStorageBool("nova.sidebarCollapsed", state.sidebarCollapsed);
  applyShellChromeState();
});

// 明暗主题切换：沿用 dsh 的 data-ds-dark-theme 属性约定，localStorage 持久化
const themeToggleEl = document.querySelector("#theme-toggle");

function applyThemePreference() {
  const saved = window.localStorage.getItem("nova.theme");
  const dark = saved ? saved === "dark" : false;
  document.body.classList.toggle("booting-theme", false);
  if (dark) {
    document.body.setAttribute("data-ds-dark-theme", "");
  } else {
    document.body.removeAttribute("data-ds-dark-theme");
  }
}

if (themeToggleEl) {
  themeToggleEl.addEventListener("click", () => {
    const dark = document.body.hasAttribute("data-ds-dark-theme");
    window.localStorage.setItem("nova.theme", dark ? "light" : "dark");
    applyThemePreference();
  });
  applyThemePreference();
}

inspectorToggleEl.addEventListener("click", () => {
  state.inspectorCollapsed = !state.inspectorCollapsed;
  writeStorageBool("nova.inspectorCollapsed", state.inspectorCollapsed);
  applyShellChromeState();
});

// ---- dsh 形态新元素接线：composer 工具栏选择器 / 版本徽章 / 空状态 hero ----
const permissionSelectEl = document.querySelector("#permission-select");
const modelSelectEl = document.querySelector("#model-select");
const novaVersionEl = document.querySelector("#nova-version");
const emptyHeroEl = document.querySelector("#empty-hero");
const workspaceEntryEl = document.querySelector("[data-workspace-open]");

// 权限预设切换：复用设置页的 derivePermissionConfig，保存后立即生效
if (permissionSelectEl) {
  permissionSelectEl.addEventListener("change", async () => {
    const preset = permissionSelectEl.value;
    streamStateEl.textContent = "正在切换权限模式";
    try {
      state.runtimeConfig = await api("/api/runtime/config", {
        method: "PATCH",
        body: JSON.stringify(derivePermissionConfig(preset === "bypass" ? "bypass_permissions" : preset)),
      });
      streamStateEl.textContent = state.runtimeConfig.restart_required
        ? `权限已设为 ${permissionSelectEl.selectedOptions[0]?.textContent || preset}，重启后完全生效`
        : `权限已设为 ${permissionSelectEl.selectedOptions[0]?.textContent || preset}`;
    } catch (error) {
      streamStateEl.textContent = `权限切换失败：${error instanceof Error ? error.message : "未知错误"}`;
    }
  });
}

// 模型切换：写入运行配置的 provider_model
if (modelSelectEl) {
  modelSelectEl.addEventListener("change", async () => {
    const model = modelSelectEl.value;
    streamStateEl.textContent = "正在切换模型";
    try {
      state.runtimeConfig = await api("/api/runtime/config", {
        method: "PATCH",
        body: JSON.stringify({ provider_model: model }),
      });
      streamStateEl.textContent = `模型已切换为 ${model}`;
    } catch (error) {
      streamStateEl.textContent = `模型切换失败：${error instanceof Error ? error.message : "未知错误"}`;
    }
  });
}

// 版本徽章：从 /api/health 读版本号（dsh 的 mono 版本徽章签名细节）
if (novaVersionEl) {
  api("/api/health")
    .then((health) => {
      novaVersionEl.textContent = health && health.version ? `v${health.version}` : "nova";
    })
    .catch(() => {});
}

// 工作区入口按钮：复用侧栏 ⇄ 按钮的弹窗打开逻辑
if (workspaceEntryEl && workspaceOpenEl) {
  workspaceEntryEl.addEventListener("click", () => workspaceOpenEl.click());
}

// 空状态 hero：会话有消息时隐藏，清空时回归（dsh 空态引导页行为）
function syncEmptyHero() {
  if (!emptyHeroEl || !messagesEl) return;
  const hasContent = messagesEl.childElementCount > 0;
  emptyHeroEl.hidden = hasContent;
  emptyHeroEl.style.display = hasContent ? "none" : "";
}

if (emptyHeroEl && messagesEl) {
  new MutationObserver(syncEmptyHero).observe(messagesEl, { childList: true });
  syncEmptyHero();
}

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
  const secrets = {};
  const bigmodelKey = form.querySelector('[name="bigmodel_api_key"]').value.trim();
  const langfusePublicKey = form.querySelector('[name="langfuse_public_key"]').value.trim();
  const langfuseSecretKey = form.querySelector('[name="langfuse_secret_key"]').value.trim();
  const langfuseHost = form.querySelector('[name="langfuse_host"]').value.trim();
  if (bigmodelKey) {
    secrets.bigmodel_api_key = bigmodelKey;
  }
  if (langfusePublicKey) {
    secrets.langfuse_public_key = langfusePublicKey;
  }
  if (langfuseSecretKey) {
    secrets.langfuse_secret_key = langfuseSecretKey;
  }
  if (langfuseHost) {
    secrets.langfuse_host = langfuseHost;
  }
  secrets.langfuse_enabled = form.querySelector('[name="langfuse_enabled"]').checked;
  return secrets;
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
    await Promise.all([loadWorkspaceStatus(), loadRuntimeShell(), loadSessions()]);
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
    state.workspaceRecentProjects = workspaces.recent_projects || [];
    state.workspaceCompletion = workspaces.completion || null;
    state.workspaceDialogStatus = workspaces.query_status || null;
    renderWorkspaceDialogList();
    renderWorkspaceDialogState();
  } catch (error) {
    if (requestId !== state.workspaceDialogRequestId) {
      return;
    }
    state.workspaceDialogCandidates = [];
    state.workspaceCompletion = null;
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
  const groups = groupWorkspaceDialogItems({
    query: workspaceDialogInputEl.value.trim(),
    recentProjects: state.workspaceRecentProjects,
    candidates: state.workspaceDialogCandidates,
  });
  if (groups.length === 0) {
    workspaceDialogListEl.innerHTML = '<div class="workspace-dialog-empty">没有匹配的下级目录</div>';
    return;
  }
  for (const group of groups) {
    const title = document.createElement("div");
    title.className = "workspace-dialog-section-title";
    title.textContent = group.title;
    workspaceDialogListEl.appendChild(title);
    for (const path of group.items) {
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
  }
  renderWorkspaceDialogActive();
}

function selectWorkspaceDialogCandidate(path, options = {}) {
  if (!path) {
    return;
  }
  workspaceDialogInputEl.value = path;
  workspaceInputEl.value = path;
  state.workspaceDialogIndex = -1;
  const browseChildren = options.browseChildren !== false;
  scheduleWorkspaceDialogCandidates(0, browseChildren ? `${path}/` : path);
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
    await Promise.all([loadWorkspaceStatus(), loadRuntimeShell(), loadSessions()]);
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

function isTurnActive() {
  return Boolean(state.sending || state.sessionActive);
}

function syncComposerRunState() {
  setSendButtonMode(isTurnActive() ? "queue" : "send");
}

// dsh 形态：圆形图标按钮，无文字 label；queue 态换图标并加 title 提示
function setSendButtonMode(mode) {
  if (mode === "queue") {
    sendButtonEl.dataset.mode = "queue";
    sendButtonEl.classList.add("queue");
    sendButtonEl.textContent = "+";
    sendButtonEl.title = "排队";
    sendButtonEl.setAttribute("aria-label", "排队");
    if (stopButtonEl) {
      stopButtonEl.disabled = false;
      stopButtonEl.setAttribute("aria-disabled", "false");
      stopButtonEl.title = "停止";
    }
    return;
  }
  sendButtonEl.dataset.mode = "send";
  sendButtonEl.classList.remove("queue");
  sendButtonEl.textContent = "↑";
  sendButtonEl.title = "发送";
  sendButtonEl.setAttribute("aria-label", "发送");
  if (stopButtonEl) {
    stopButtonEl.disabled = true;
    stopButtonEl.setAttribute("aria-disabled", "true");
    stopButtonEl.title = "停止";
  }
}

setSendButtonMode("send");

async function cancelActiveTurn() {
  const sessionId = state.selectedSessionId;
  if (!sessionId || !isTurnActive()) {
    return;
  }
  state.turnCancelRequested = true;
  state.sessionActive = false;
  streamStateEl.textContent = "正在停止当前运行";
  if (stopButtonEl) {
    stopButtonEl.disabled = true;
    stopButtonEl.setAttribute("aria-disabled", "true");
    stopButtonEl.title = "停止中";
  }
  markRunningToolsAsCancelRequested();
  state.streamAbortController?.abort();
  state.sending = false;
  syncComposerRunState();
  streamStateEl.textContent = "已请求停止当前运行";
  void api(`/api/chat/sessions/${encodeURIComponent(sessionId)}/cancel`, {
    method: "POST",
    body: "{}",
  }).catch((error) => {
    streamStateEl.textContent = `停止请求失败：${error instanceof Error ? error.message : "未知错误"}`;
    setSendButtonMode(state.sending ? "queue" : "send");
  });
}

async function clearQueuedMessages() {
  const sessionId = state.selectedSessionId;
  if (!sessionId || state.queuedMessages.length === 0) {
    return;
  }
  streamStateEl.textContent = "正在清空队列";
  try {
    const result = await api(`/api/chat/sessions/${encodeURIComponent(sessionId)}/queue/clear`, {
      method: "POST",
      body: "{}",
    });
    removeClearedQueueMessages(result.cleared_messages || state.queuedMessages);
    setQueuedMessages(result.queued_messages || []);
    appendStatusEvent(`已清空 ${result.cleared_count || 0} 条排队输入`);
    streamStateEl.textContent = "队列已清空";
  } catch (error) {
    streamStateEl.textContent = `清空队列失败：${error instanceof Error ? error.message : "未知错误"}`;
  }
}

function setupInspectorCards() {
  for (const card of document.querySelectorAll(".inspector-panel")) {
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

stopButtonEl?.addEventListener("click", () => {
  void cancelActiveTurn();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const content = messageEl.value.trim();
  if (!content) {
    return;
  }
  if (isTurnActive()) {
    try {
      const sessionId = await ensureSession();
      const queued = await fetch(`/api/chat/sessions/${sessionId}/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      if (queued.status === 200) {
        // 竞态兜底：前端认为上一轮还在跑，但后端已结束——这次 POST 实际完整执行了一轮。
        // 不能重发（会重复消息），解析已返回的 SSE 提取回复渲染。
        const sseText = await queued.text();
        appendMessage({ id: `local_user_${Date.now()}`, role: "user", content, created_at: new Date().toISOString() });
        let rescued = "";
        for (const line of sseText.split("\n")) {
          try {
            const evt = JSON.parse(line.trim());
            if (evt.type === "assistant_done" && evt.message?.content) {
              rescued = evt.message.content;
            }
          } catch {}
        }
        if (rescued) {
          appendMessage({ id: `local_asst_${Date.now()}`, role: "assistant", content: rescued, created_at: new Date().toISOString() });
        }
        messageEl.value = "";
        autoResizeTextarea();
        messageEl.focus();
        await loadSessions({ refreshMessages: false });
        streamStateEl.textContent = rescued ? "回复完成" : "回复已完成，刷新消息以同步";
        return;
      }
      if (queued.status !== 202) {
        throw new Error(await queued.text());
      }
      const payload = await queued.json();
      appendMessage(payload.message, { queued: true });
      setQueuedMessages(payload.queued_messages || [payload.message]);
      messageEl.value = "";
      autoResizeTextarea();
      messageEl.focus();
      streamStateEl.textContent = "消息已排队，当前工具轮结束后进入上下文";
    } catch (error) {
      streamStateEl.textContent = `排队失败：${error instanceof Error ? error.message : "未知错误"}`;
    }
    return;
  }
  state.sending = true;
  state.sessionActive = true;
  state.startNewChat = false;
  state.turnCancelRequested = false;
  state.streamAbortController = new AbortController();
  sendButtonEl.disabled = false;
  syncComposerRunState();
  renderIdleRuntimeOverview();
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
    messageEl.focus();
    followStreamScroll();
    const ok = await streamAssistant(sessionId, content, assistantNode);
    // 立即复位运行态：后端 turn 已结束，前端若继续标记"运行中"，
    // 下一条消息会误入排队分支并触发 200 竞态。
    state.sending = false;
    state.sessionActive = false;
    syncComposerRunState();
    streamStateEl.textContent = state.turnCancelRequested ? "已停止" : (ok ? "回复完成" : "请求失败");
    renderRuntimeOverviewFromDom(state.turnCancelRequested ? "cancelled" : (ok ? "completed" : "failed"));
    await Promise.all([
      loadSessions({ refreshMessages: false }),
      loadWorkspaceStatus(),
      loadRuntimeShell(),
      loadHealth(),
    ]);
    renderStatusline();
  } catch (error) {
    if (state.turnCancelRequested && error?.name === "AbortError") {
      if (assistantNode) {
        assistantNode.classList.remove("streaming");
      }
      streamStateEl.textContent = "已停止";
      renderRuntimeOverviewFromDom("cancelled");
      return;
    }
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
    renderRuntimeOverviewFromDom("failed");
  } finally {
    state.sending = false;
    state.sessionActive = false;
    state.streamAbortController = null;
    sendButtonEl.disabled = false;
    syncComposerRunState();
    renderRuntimeOverviewFromDom(state.turnCancelRequested ? "cancelled" : "completed");
    messageEl.focus();
  }
});


// ---- 流式滚动跟随（对照 dsh ChatView：FOLLOW_THRESHOLD + atBottom 状态）----
// 用户贴在底部时新内容自动跟滚；一旦上滚离开底部就停止跟随，直到手动回底。
const SCROLL_FOLLOW_THRESHOLD = 80;
let messagesAtBottom = true;

if (messagesEl) {
  const syncAtBottom = () => {
    messagesAtBottom =
      messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight <=
      SCROLL_FOLLOW_THRESHOLD + 1;
  };
  messagesEl.addEventListener("scroll", syncAtBottom, { passive: true });
}

function followStreamScroll() {
  if (!messagesEl || !messagesAtBottom) return;
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

async function streamAssistant(sessionId, content, assistantNode) {
  const response = await fetch(`/api/chat/sessions/${sessionId}/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
    signal: state.streamAbortController?.signal,
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
      renderRuntimeOverviewFromDom("running");
      return;
    }
    if (event.event_type === "turn.completed") {
      streamStateEl.textContent = event.title || "回复完成";
      renderRuntimeOverviewFromDom("completed");
      return;
    }
    if (event.event_type === "turn.cancelled") {
      state.turnCancelRequested = true;
      streamStateEl.textContent = event.title || "已停止";
      currentAssistantNode.classList.remove("streaming");
      appendStatusEvent(event.message || event.title || "已停止当前运行", { beforeNode: currentAssistantNode });
      updateTurnToolControl(currentAssistantNode);
      renderRuntimeOverviewFromDom("cancelled");
      return;
    }
    if (event.event_type === "turn.failed") {
      streamStateEl.textContent = event.title || "请求失败";
      appendStatusEvent(event.message || event.title || "请求失败", { beforeNode: currentAssistantNode });
      updateTurnToolControl(currentAssistantNode);
      renderRuntimeOverviewFromDom("failed");
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
    removeQueuedMessage(message.id);
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
    renderRuntimeOverviewFromDom("running");
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
        followStreamScroll();
        streamStateEl.textContent = "Nova 正在输出";
      },
      onToolStart: (event) => {
        streamStateEl.textContent = `工具执行：${event.tool}`;
        const node = appendToolEvent(event, currentAssistantNode);
        followStreamScroll();
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
        followStreamScroll();
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
  if (state.turnCancelRequested) {
    currentAssistantNode.classList.remove("streaming");
  }
  return ok;
}

messageEl.addEventListener("keydown", (event) => {
  if (!commandPaletteEl.hidden && event.key === "Escape") {
    hideCommandPalette();
    return;
  }
  if (!commandPaletteEl.hidden && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
    event.preventDefault();
    state.commandSelectionIndex = nextCommandSelectionIndex(
      state.commandSelectionIndex,
      commandMatches.length,
      event.key === "ArrowUp" ? "up" : "down",
    );
    renderCommandPalette();
    return;
  }
  if (!commandPaletteEl.hidden && event.key === "Tab") {
    event.preventDefault();
    const selected = state.commandSelectionIndex >= 0
      ? commandMatches[state.commandSelectionIndex]
      : commandMatches[0];
    fillCommand(selected);
    return;
  }
  // isComposing：中文输入法组字中的 Enter 是"确认候选词"，不是发送。
  // 不检查会在打字过程中把半截消息提前发出（dsh 同样在 composer 层拦截）。
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
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
  const matches = filterCommandMatches(value, state.commands);
  if (matches.length === 0) {
    hideCommandPalette();
    return;
  }
  commandMatches = matches;
  if (state.commandSelectionIndex >= commandMatches.length) {
    state.commandSelectionIndex = commandMatches.length - 1;
  }
  if (state.commandSelectionIndex < 0) {
    state.commandSelectionIndex = 0;
  }
  renderCommandPalette();
}

function renderCommandPalette() {
  commandPaletteEl.removeAttribute("hidden");
  commandPaletteEl.innerHTML = "";
  const header = document.createElement("div");
  header.className = "command-palette-title";
  header.innerHTML = "<strong>内置指令</strong><span>Tab 补全，Enter 发送</span>";
  commandPaletteEl.appendChild(header);
  commandMatches.forEach((command, index) => {
    const item = document.createElement("button");
    item.type = "button";
    item.className = `command-item ${index === state.commandSelectionIndex ? "selected" : ""}`;
    item.setAttribute("aria-selected", String(index === state.commandSelectionIndex));
    const hint = command.argumentHint ? ` <em>${escapeHtml(command.argumentHint)}</em>` : "";
    item.innerHTML = `
      <strong>${command.name}${hint}</strong>
      <span>${command.description}</span>
    `;
    item.addEventListener("click", () => fillCommand(command));
    commandPaletteEl.appendChild(item);
  });
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
  state.commandSelectionIndex = -1;
}

async function loadCommands() {
  try {
    const payload = await api("/api/commands");
    state.commands = (payload.items || []).map((command) => ({
      name: command.name,
      description: command.description,
      argumentHint: command.argument_hint || "",
      group: command.group || "runtime",
      source: command.source || "builtin",
      aliases: command.aliases || [],
    }));
  } catch (error) {
    state.commands = BUILTIN_COMMANDS;
  }
  updateCommandPalette();
}

loadHealth();
loadCommands();
loadWorkspaceStatus({ quick: true, includePicker: false });
loadSessions();
scheduleRuntimeShellLoad();
activateInspectorPanel("workspace");
setupInspectorCards();
applyShellChromeState();
