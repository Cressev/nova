import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react"
import type { ChatMessage, ChatSession, PendingApprovalItem, RuntimeConfig, ToolCallData, TraceEvent } from "./types"
import { api, abbreviateHomePath, cx, formatDateTime, formatTime, projectName, relativeTime, relativeTimeAgo, shortText, workspaceGroupKey } from "./lib/api"
import { deriveSessionGroups } from "./lib/sessionTree"
import { subscribeWorkspaceView, workspaceViewSnapshot, setWorkspaceGroupBy, setWorkspaceOrderBy, toggleWorkspaceGroup, setWorkspaceAccountOrder, workspaceOrderSnapshot, setWorkspaceOrder, workspaceAccountOrder } from "./lib/workspaceViewStore"
import { detectComposerTrigger, filterTriggerCandidates, triggerToken, type TriggerCandidate } from "./lib/composerTrigger"
import { Markdown, CopyButton } from "./components/Markdown"
import { useInlineComments, SelectionToolbar, CommentPanel } from "./components/InlineComments"
import { WorkspacePicker } from "./components/WorkspacePicker"
import { ToolEventRow, deriveToolSummary, type ToolEventView } from "./components/ToolEvent"
import { PermissionCard, QuestionCard } from "./components/Takeover"
import { SettingsDialog } from "./components/SettingsDialog"
import { TraceView } from "./components/TraceView"
import { ThinkRow } from "./components/ThinkRow"
import { MenuSelect } from "./components/MenuSelect"

/* ============================================================================
   Nova App —— React 版（对齐 dsh ui-conversation 的组件分区）。
   数据流：App 持全部会话态；ConversationView 渲染消息时间线；
   工具/审批/提问事件以内联条目插入时间线（按 sequence 排序）；
   takeovers（待审批/待提问）停靠在 composer 上方 dock。
   ========================================================================== */

type TimelineEntry =
  | { kind: "message"; key: string; message: ChatMessage }
  | { kind: "think"; key: string; text: string; running: boolean }
  | { kind: "tool"; key: string; view: ToolEventView }
  | { kind: "permission"; key: string; item: PendingApprovalItem }
  | { kind: "question"; key: string; item: PendingApprovalItem }
  | { kind: "checkpoint"; key: string; message: ChatMessage }

interface ToolRowEvent {
  id: string
  event_type?: string
  type?: string
  tool?: string
  arguments?: Record<string, unknown>
  output?: string
  title?: string
  message?: string
  status?: string
  phase?: string
  created_at?: string
  data?: ToolCallData
}

const PERMISSION_MODE_LABELS: Record<string, string> = {
  read_only: "只读模式",
  ask: "询问模式",
  workspace_write: "标准模式",
  plan: "计划模式",
  bypass_permissions: "完全访问",
}

/* composer 权限位显示语义值（dsh「Full access ▾」形态）；header 模式位显示模式名。 */
const PERMISSION_VALUE_LABELS: Record<string, string> = {
  read_only: "只读",
  ask: "询问",
  workspace_write: "工作区写入",
  plan: "计划",
  bypass_permissions: "完全访问",
}

function roleLabel(role: string): string {
  if (role === "user") return "你"
  if (role === "assistant") return "Nova"
  if (role === "error") return "错误"
  return role
}

function findLastIndex<T>(arr: T[], pred: (x: T) => boolean): number {
  for (let i = arr.length - 1; i >= 0; i -= 1) {
    if (pred(arr[i])) return i
  }
  return -1
}

/** 从后端 timeline items（message/event 混合）折叠为渲染条目。 */
function foldTimeline(items: Array<{ kind: string; item: Record<string, unknown> }>): TimelineEntry[] {
  const entries: TimelineEntry[] = []
  const toolIndex = new Map<string, number>()
  for (const wrapper of items || []) {
    const item = (wrapper.item || {}) as Record<string, unknown>
    if (wrapper.kind === "message") {
      const message = item as unknown as ChatMessage
      const content = String(message.content || "")
      const isCheckpoint = String(message.id || "").startsWith("comp_") || content.includes("<compacted-summary>")
      entries.push(isCheckpoint
        ? { kind: "checkpoint", key: `cp-${message.id}`, message }
        : { kind: "message", key: `m-${message.id}`, message })
      continue
    }
    const event = item as unknown as ToolRowEvent
    const eventType = String(event.event_type || event.type || "")
    if (eventType.startsWith("turn.") || eventType === "status" || eventType.startsWith("hook.") || eventType === "memory.compacted") {
      continue // dsh 形态：运行状态不进消息流
    }
    if (eventType === "reasoning.completed") {
      // 持久化思考：渲染为 Think 披露行。事件时间戳晚于 assistant 消息落库时间，
      // 但语义上思考先于回答 → 插入点=同轮 assistant 消息之前（dsh Think 行位置）。
      const thinkEntry: TimelineEntry = {
        kind: "think",
        key: `think-${event.id}`,
        text: String(event.message || ""),
        running: false,
      }
      const lastMsgIdx = findLastIndex(entries, (e) => e.kind === "message" && e.message.role === "assistant")
      if (lastMsgIdx >= 0) entries.splice(lastMsgIdx, 0, thinkEntry)
      else entries.push(thinkEntry)
      continue
    }
    if (eventType === "user.question" || event.type === "user_question") {
      const questions = (event.data?.questions || (event as unknown as { questions?: unknown }).questions || []) as PendingApprovalItem["questions"]
      entries.push({
        kind: "question",
        key: `q-${event.id}`,
        item: {
          id: event.id,
          call_id: (event as unknown as { call_id?: string }).call_id || event.id,
          tool: event.tool || "",
          arguments: {},
          questions,
          reason: event.message,
          data: event.data,
        },
      })
      continue
    }
    if (eventType === "permission.requested" || event.type === "permission") {
      entries.push({
        kind: "permission",
        key: `p-${event.id}`,
        item: {
          id: event.id,
          call_id: event.id,
          tool: event.tool || "",
          arguments: (event.arguments || {}) as Record<string, unknown>,
          reason: event.message,
          data: event.data,
        },
      })
      continue
    }
    if (eventType.startsWith("tool.") || eventType === "tool") {
      const tool = String(event.tool || "")
      const callId = String(event.id || "")
      const key = `t-${callId}`
      const existing = toolIndex.get(key)
      const view: ToolEventView = {
        callId,
        tool,
        args: (event.arguments || {}) as Record<string, unknown>,
        output: String(event.output || ""),
        data: (event.data || {}) as ToolCallData,
        state: event.status === "ok" ? "ok" : event.status === "cancelled" ? "stopped" : event.status ? "error" : "running",
      }
      if (existing !== undefined && entries[existing]?.kind === "tool") {
        ;(entries[existing] as { view: ToolEventView }).view = view
      } else {
        toolIndex.set(key, entries.length)
        entries.push({ kind: "tool", key, view })
      }
    }
  }
  return entries
}

function MessageView({ message, onForkAt }: { message: ChatMessage; onForkAt?: (messageId: string, messageRole: string) => void }) {
  const [collapsedTools, setCollapsedTools] = useState(false)
  if (message.role === "user") {
    return (
      <article className={cx("message", "user")} data-message-id={message.id}>
        <div className="message-head">
          <div className="message-role">{roleLabel(message.role)}</div>
        </div>
        <div className="message-content"><Markdown content={message.content || ""} /></div>
        <div className="message-actions" data-role="user"><CopyButton text={message.content || ""} /></div>
      </article>
    )
  }
  return (
    <article className={cx("message", message.role === "assistant" ? "assistant" : "error")} data-message-id={message.id}>
      <div className="message-head">
        <div className="message-role">{roleLabel(message.role)}</div>
        <button
          className="turn-tools-toggle"
          type="button"
          hidden={!collapsedTools}
          onClick={() => setCollapsedTools(false)}
        >展开过程</button>
      </div>
      <div className="message-content"><Markdown content={message.content || ""} /></div>
      <div className="message-actions" data-role={message.role}>
        {message.created_at ? <span className="message-clock">{formatTime(message.created_at)}</span> : null}
        <CopyButton text={message.content || ""} />
        {onForkAt && message.role === "assistant" && message.id ? (
          <button
            className="message-fork-button"
            type="button"
            title="从该回复处分叉出新会话"
            onClick={() => onForkAt(message.id, message.role)}
          >分支</button>
        ) : null}
      </div>
    </article>
  )
}

function CheckpointView({ message }: { message: ChatMessage }) {
  const summaryMatch = String(message.content || "").match(/<compacted-summary>([\s\S]*?)<\/compacted-summary>/)
  const summaryText = summaryMatch ? summaryMatch[1].trim() : String(message.content || "")
  return (
    <article className="message checkpoint">
      <details className="checkpoint-details">
        <summary>◷ 上下文检查点 · 更早的对话已压缩为摘要</summary>
        <pre className="checkpoint-body">{summaryText}</pre>
      </details>
    </article>
  )
}

/* ---- 侧栏 ---- */
function Sidebar({ sessions, selectedId, currentWorkspace, version, runtimeBySession, draftActive, onSelect, onRename, onFork, onArchive, onNewChat, onOpenSettings, onWorkspaceSwitched }: {
  sessions: ChatSession[]
  selectedId: string | null
  currentWorkspace: string
  version: string
  runtimeBySession: Record<string, { active?: boolean; pending?: boolean; descendantRunning?: boolean; completedUnviewed?: boolean }>
  onSelect: (session: ChatSession) => void
  onRename: (session: ChatSession, title: string) => void
  onFork: (session: ChatSession) => void
  onArchive: (session: ChatSession) => void
  onOpenSettings: () => void
  onNewChat: (workspace?: string) => void
  onWorkspaceSwitched: (root: string) => void
  /** dsh 空白占位（D1）：true 时在当前工作区分组顶部渲染一条无时间无菜单的占位行 */
  draftActive: boolean
}) {
  const [query, setQuery] = useState("")
  const [remoteMatches, setRemoteMatches] = useState<Record<string, string>>({})
  const [searchError, setSearchError] = useState("")
  const [workspaceActionError, setWorkspaceActionError] = useState("")
  const [searchExpanded, setSearchExpanded] = useState(false)
  const [viewMenuOpen, setViewMenuOpen] = useState(false)
  const searchInputRef = useRef<HTMLInputElement | null>(null)
  const sidebarRootRef = useRef<HTMLElement | null>(null)
  const [workspacePanelOpen, setWorkspacePanelOpen] = useState(false)
  const [recentWorkspacePaths, setRecentWorkspacePaths] = useState<string[]>([])
  const [workspaceRegistry, setWorkspaceRegistry] = useState<Array<{ path: string; title: string; created_at?: string }>>([])
  const [homePath, setHomePath] = useState<string>("")
  // dsh quietBars（D10）：滚动条只在指针位于栏内时绘制，离开 2s linger 后隐藏
  const [pointerInside, setPointerInside] = useState(false)
  // dsh 悬停卡点击复制（D7）：闪现"已复制"反馈
  const [copiedId, setCopiedId] = useState<string | null>(null)
  const lingerTimer = useRef<number | undefined>(undefined)
  const copyFlashTimer = useRef<number | undefined>(undefined)
  // dsh runningSubagentCount（D12）：该会话名下正在运行的子会话数
  const subagentRunningCount = (sessionId: string): number =>
    sessions.filter((item) => item.parent_session_id === sessionId && runtimeBySession[item.id]?.active).length
  const [workspaceMenuPath, setWorkspaceMenuPath] = useState<string | null>(null)
  const [workspaceRenamePath, setWorkspaceRenamePath] = useState<string | null>(null)
  const [workspaceRenameTitle, setWorkspaceRenameTitle] = useState("")
  const [menuSessionId, setMenuSessionId] = useState<string | null>(null)
  const [menuFocusIndex, setMenuFocusIndex] = useState(0)
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null)
  const [editingTitle, setEditingTitle] = useState("")
  const view = useSyncExternalStore(subscribeWorkspaceView, workspaceViewSnapshot, workspaceViewSnapshot)
  const [expandedSessionGroups, setExpandedSessionGroups] = useState<Record<string, boolean>>({})
  const [draggingSessionId, setDraggingSessionId] = useState<string | null>(null)
  const [dragOverSessionId, setDragOverSessionId] = useState<string | null>(null)
  const [dragOverAfter, setDragOverAfter] = useState(false)
  const [draggingGroupKey, setDraggingGroupKey] = useState<string | null>(null)
  const [dragOverGroupKey, setDragOverGroupKey] = useState<string | null>(null)
  const [dragOverGroupAfter, setDragOverGroupAfter] = useState(false)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => { try { return localStorage.getItem("nova.sidebar.collapsed") === "1" } catch { return false } })
  // dsh 折叠编排（D9）：宽内容先淡出 150ms，settle 后才 display:none 卸载并淡入导轨图标
  const [collapsedSettled, setCollapsedSettled] = useState(() => { try { return localStorage.getItem("nova.sidebar.collapsed") === "1" } catch { return false } })
  useEffect(() => {
    document.body.classList.toggle("sidebar-collapsed", sidebarCollapsed)
    try { localStorage.setItem("nova.sidebar.collapsed", sidebarCollapsed ? "1" : "0") } catch { /* 隐私模式下只保留当前页状态 */ }
    if (!sidebarCollapsed) { setCollapsedSettled(false); return }
    const timer = window.setTimeout(() => setCollapsedSettled(true), 150)
    return () => { window.clearTimeout(timer); document.body.classList.remove("sidebar-collapsed") }
  }, [sidebarCollapsed])
  useEffect(() => {
    document.body.classList.toggle("sidebar-settled", collapsedSettled && sidebarCollapsed)
  }, [collapsedSettled, sidebarCollapsed])
  useEffect(() => {
    void api<{ home?: string; recent_projects?: string[]; workspaces?: Array<{ path: string; title: string; created_at?: string }> }>("/api/workspaces").then((payload) => { setHomePath(payload.home || ""); setRecentWorkspacePaths(payload.recent_projects || []); setWorkspaceRegistry(payload.workspaces || []) }).catch(() => setRecentWorkspacePaths([]))
  }, [])
  useEffect(() => {
    setRemoteMatches({}); setSearchError("")
    if (!query.trim()) { return }
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      void api<{ items?: Array<{ session_id: string; snippet?: string }> }>(`/api/chat/sessions/search?q=${encodeURIComponent(query.slice(0, 500))}`, { signal: controller.signal })
        .then((payload) => {
          const next: Record<string, string> = {}
          for (const item of payload.items || []) next[item.session_id] = item.snippet || ""
          setRemoteMatches(next); setSearchError("")
        })
        .catch((error: unknown) => { if ((error as Error)?.name !== "AbortError") setSearchError("搜索暂不可用") })
    }, 250)
    return () => { window.clearTimeout(timer); controller.abort() }
  }, [query])
  useEffect(() => {
    const acceptDrop = (event: DragEvent) => { if (draggingSessionId && dragOverSessionId) { event.preventDefault() } }
    const commitDrop = () => { if (draggingSessionId && dragOverSessionId) { setDraggingSessionId(null); setDragOverSessionId(null); setDragOverAfter(false) } }
    document.addEventListener("dragover", acceptDrop)
    document.addEventListener("drop", commitDrop)
    return () => { document.removeEventListener("dragover", acceptDrop); document.removeEventListener("drop", commitDrop) }
  }, [draggingSessionId, dragOverSessionId])
  useEffect(() => {
    if (!menuSessionId) return
    const menu = document.querySelector<HTMLElement>(`[data-session-menu="${menuSessionId}"]`)
    menu?.querySelector<HTMLElement>(`button:nth-child(${menuFocusIndex + 1})`)?.focus()
  }, [menuSessionId, menuFocusIndex])
  useEffect(() => {
    const close = (event: PointerEvent) => {
      const target = event.target as HTMLElement
      if (!target.closest(".session-row-menu, .workspace-row-menu, .workspace-rename-popover, .view-menu")) {
        setMenuSessionId(null)
        setWorkspaceMenuPath(null)
        setWorkspaceRenamePath(null)
        setViewMenuOpen(false)
      }
    }
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMenuSessionId(null)
        setWorkspaceMenuPath(null)
        setWorkspaceRenamePath(null)
        setViewMenuOpen(false)
      }
    }
    document.addEventListener("pointerdown", close)
    document.addEventListener("keydown", key)
    return () => { document.removeEventListener("pointerdown", close); document.removeEventListener("keydown", key) }
  }, [])
  // dsh searchOnExpand（D6）：导轨搜索展开侧栏后输入框自动聚焦
  useEffect(() => {
    if (searchExpanded && !sidebarCollapsed) searchInputRef.current?.focus()
  }, [searchExpanded, sidebarCollapsed])
  // dsh 滚动条跟随指针（D10）：离开由栏的 BOX 判定（常驻 pointermove 监听），
  // 固定定位的弹层不算离开；离开后 2000ms 才隐藏，期间折返即取消。
  const pointerInsideRef = useRef(false)
  useEffect(() => {
    const armLinger = () => {
      if (lingerTimer.current !== undefined) return
      lingerTimer.current = window.setTimeout(() => { lingerTimer.current = undefined; pointerInsideRef.current = false; setPointerInside(false) }, 2000)
    }
    const onMove = (event: PointerEvent) => {
      const rect = sidebarRootRef.current?.getBoundingClientRect()
      if (!rect) return
      const inside = event.clientX >= rect.left && event.clientX < rect.right && event.clientY >= rect.top && event.clientY < rect.bottom
      if (inside) {
        if (lingerTimer.current !== undefined) { window.clearTimeout(lingerTimer.current); lingerTimer.current = undefined }
        if (!pointerInsideRef.current) { pointerInsideRef.current = true; setPointerInside(true) }
      } else if (pointerInsideRef.current) {
        armLinger()
      }
    }
    document.addEventListener("pointermove", onMove)
    return () => { document.removeEventListener("pointermove", onMove); if (lingerTimer.current !== undefined) { window.clearTimeout(lingerTimer.current); lingerTimer.current = undefined } }
  }, [])
  const copyTitle = (session: ChatSession) => {
    void navigator.clipboard?.writeText(session.title || "").catch(() => { /* 剪贴板不可用静默 */ })
    setCopiedId(session.id)
    if (copyFlashTimer.current !== undefined) window.clearTimeout(copyFlashTimer.current)
    copyFlashTimer.current = window.setTimeout(() => setCopiedId(null), 1500)
  }
  const groups = useMemo(() => {
    const searchSessions = query.trim() ? sessions.filter((session) => String(session.title || "").toLowerCase().includes(query.trim().toLowerCase()) || remoteMatches[session.id]) : sessions
    const derived = deriveSessionGroups(searchSessions, selectedId, query.trim() && Object.keys(remoteMatches).length === 0 ? query : "", view.groupBy, view.orderBy, view.sessionOrderByAccount)
    const known = new Set(derived.map((group) => group.key))
    const emptyWorkspaceGroups = !query.trim() && view.groupBy === "workspace" ? (workspaceRegistry.length ? workspaceRegistry.filter((item) => !known.has(workspaceGroupKey(item.path))).map((item) => ({ key: workspaceGroupKey(item.path), name: item.title, workspace: item.path, sessions: [], ungrouped: false })) : recentWorkspacePaths.filter((path) => !known.has(workspaceGroupKey(path))).map((path) => ({ key: workspaceGroupKey(path), name: projectName(path), workspace: path, sessions: [], ungrouped: false }))) : []
    const allGroups = [...derived, ...emptyWorkspaceGroups]
    const order = workspaceOrderSnapshot()
    const byKey = new Map(allGroups.map((group) => [group.key, group]))
    const orderedGroups = [...order.map((key) => byKey.get(key)).filter((group): group is typeof allGroups[number] => Boolean(group)), ...allGroups.filter((group) => !order.includes(group.key))]
    return orderedGroups.map((group) => ({ ...group, expanded: group.ungrouped || view.groupExpansion[group.key] !== false || (view.groupExpansion[group.key] === undefined && group.sessions.some((session) => session.id === selectedId)) }))
  }, [sessions, selectedId, query, remoteMatches, view.groupBy, view.orderBy, view.sessionOrderByAccount, view.groupExpansion])
  useEffect(() => {
    if (query.trim() || view.groupBy !== "workspace" || groups.length === 0) return
    const currentOrder = workspaceOrderSnapshot()
    const nextOrder = groups.map((group) => group.key)
    if (nextOrder.some((key, index) => currentOrder[index] !== key) || currentOrder.length !== nextOrder.length) setWorkspaceOrder(nextOrder)
  }, [groups, query, view.groupBy])
  // dsh 搜索结果合并（D4）：本地标题匹配 + 远程内容摘要命中，平铺结果列表，50 条上限
  const searchRows = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return []
    return sessions
      .filter((session) => String(session.title || "").toLowerCase().includes(q) || remoteMatches[session.id])
      .slice(0, 50)
  }, [sessions, query, remoteMatches])

  return (
    <aside
      ref={sidebarRootRef}
      className={cx("sidebar", sidebarCollapsed && "is-collapsed", !pointerInside && "quiet-bars")}
      onPointerEnter={() => { if (lingerTimer.current !== undefined) { window.clearTimeout(lingerTimer.current); lingerTimer.current = undefined } pointerInsideRef.current = true; setPointerInside(true) }}
      onPointerLeave={() => { if (lingerTimer.current === undefined && pointerInsideRef.current) { pointerInsideRef.current = false; lingerTimer.current = window.setTimeout(() => { lingerTimer.current = undefined; setPointerInside(false) }, 2000) } }}
    >
      <div className="brand-row">
        {/* dsh（D13）：品牌在宽态兼作"新会话"快捷入口 */}
        <button className="brand-button" type="button" aria-label="Nova 新会话" title="新会话" onClick={() => onNewChat()}>
          <span className="brand-mark" aria-hidden="true">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 2.5 14.6 9 21.5 11.5 14.6 14 12 20.5 9.4 14 2.5 11.5 9.4 9 12 2.5Z" fill="currentColor"/></svg>
          </span>
          <strong className="brand-name">Nova</strong>
          <span className="version-badge" id="nova-version">{version}</span>
        </button>
        <button className="sidebar-collapse" type="button" aria-label={sidebarCollapsed ? "展开侧栏" : "折叠侧栏"} title={sidebarCollapsed ? "展开侧栏" : "折叠侧栏"} aria-pressed={sidebarCollapsed} onClick={() => setSidebarCollapsed((value) => !value)}>
          <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true"><path d="M11.2 4.4 6.6 9l4.6 4.6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
        </button>
      </div>
      <button className="new-session rail-add-control" type="button" aria-label="新会话" title="新会话" onClick={() => onNewChat()}>
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true"><path d="M7 2v10M2 7h10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>
        <span>新会话</span>
      </button>
      <div className="sidebar-group sidebar-sessions">
        <div className="group-label">
          {/* dsh 内联展开式搜索（D4）：收起时是图标，展开后占满头部行，
              工作区标签隐藏；Esc/清除键 = 清空并收起。 */}
          <div className={cx("search-slot", searchExpanded && "expanded")}>
            <button
              className="icon-ghost rail-search-toggle"
              type="button"
              aria-label="搜索会话"
              aria-expanded={searchExpanded}
              title="搜索会话"
              onClick={() => { setSidebarCollapsed(false); setSearchExpanded(true) }}
            >
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true"><circle cx="6.4" cy="6.4" r="4.4" stroke="currentColor" strokeWidth="1.4"/><path d="m9.8 9.8 2.9 2.9" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/></svg>
            </button>
            {searchExpanded ? (
              <>
                <input
                  ref={searchInputRef}
                  className="session-search"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Escape") { setQuery(""); setSearchExpanded(false) } }}
                  placeholder="搜索会话…"
                  maxLength={500}
                  aria-busy={Boolean(query.trim() && !searchError && Object.keys(remoteMatches).length === 0)}
                />
                <button
                  className="icon-ghost search-clear"
                  type="button"
                  aria-label="清除搜索"
                  title="清除搜索"
                  onClick={(e) => { e.stopPropagation(); setQuery(""); setSearchExpanded(false) }}
                >
                  <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true"><path d="m3 3 6 6M9 3l-6 6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>
                </button>
              </>
            ) : null}
          </div>
          {!searchExpanded ? (
            <button
              className={cx("group-label-workspace", workspacePanelOpen ? "active" : "")}
              type="button"
              title="切换 / 新建工作区"
              onClick={() => setWorkspacePanelOpen((v) => !v)}
            >
              <span>工作区</span>
              <span className="group-label-ws-name">{projectName(currentWorkspace) || "未选择"}</span>
              <svg className="chevron-icon" width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M4 6.5 8 10.5 12 6.5" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"/></svg>
            </button>
          ) : null}
          {/* dsh 折叠导轨补齐（D6）：导轨态显示"添加工作区"入口，点击展开侧栏并打开工作区面板 */}
          {sidebarCollapsed ? (
            <button
              className="icon-ghost rail-ws-add"
              type="button"
              aria-label="添加工作区"
              title="添加工作区"
              onClick={() => { setSidebarCollapsed(false); setWorkspacePanelOpen(true) }}
            >
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true"><path d="M1.5 3.5A1.4 1.4 0 0 1 2.9 2.1h2.2l1.2 1.4h4.2a1.4 1.4 0 0 1 1.4 1.4v4.6a1.4 1.4 0 0 1-1.4 1.4H2.9a1.4 1.4 0 0 1-1.4-1.4V3.5Z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round"/><path d="M7 5.6v3M5.5 7.1h3" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/></svg>
            </button>
          ) : null}
          <div className="group-actions">
            {/* dsh ViewOptionsMenu（D5）：分组/排序单入口勾选菜单，替代逐次切换按钮 */}
            <div className="view-menu">
              <button
                className="icon-ghost view-menu-toggle"
                type="button"
                aria-label="视图选项"
                title="视图选项"
                aria-expanded={viewMenuOpen}
                onPointerDown={(event) => event.stopPropagation()}
                onClick={(event) => { event.stopPropagation(); setViewMenuOpen((v) => !v) }}
              >
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true"><path d="M2 3.5h10M2 7h10M2 10.5h10" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/><circle cx="5" cy="3.5" r="1.6" fill="var(--surface,#fff)" stroke="currentColor" strokeWidth="1.2"/><circle cx="9.5" cy="7" r="1.6" fill="var(--surface,#fff)" stroke="currentColor" strokeWidth="1.2"/><circle cx="4.5" cy="10.5" r="1.6" fill="var(--surface,#fff)" stroke="currentColor" strokeWidth="1.2"/></svg>
              </button>
              {viewMenuOpen ? (
                <div className="view-context-menu" role="menu" aria-label="视图选项" onPointerDown={(event) => event.stopPropagation()}>
                  <div className="view-menu-label">分组</div>
                  <button type="button" role="menuitemradio" aria-checked={view.groupBy === "workspace"} onClick={() => { setWorkspaceGroupBy("workspace"); setViewMenuOpen(false) }}>按工作区</button>
                  <button type="button" role="menuitemradio" aria-checked={view.groupBy === "flat"} onClick={() => { setWorkspaceGroupBy("flat"); setViewMenuOpen(false) }}>单列表</button>
                  <div className="view-menu-separator" role="separator" />
                  <div className="view-menu-label">排序</div>
                  <button type="button" role="menuitemradio" aria-checked={view.orderBy === "manual"} onClick={() => { setWorkspaceOrderBy("manual"); setViewMenuOpen(false) }}>手动</button>
                  <button type="button" role="menuitemradio" aria-checked={view.orderBy === "updated"} onClick={() => { setWorkspaceOrderBy("updated"); setViewMenuOpen(false) }}>最近</button>
                </div>
              ) : null}
            </div>
          </div>
        </div>
        {workspacePanelOpen ? (
          <div className="sidebar-workspace-panel">
            <WorkspacePicker current={currentWorkspace} onSwitched={(root) => { onWorkspaceSwitched(root); setWorkspacePanelOpen(false) }} />
          </div>
        ) : null}
        {searchError ? <div className="session-search-warning" role="status">{searchError}</div> : null}
        {workspaceActionError ? <div className="session-search-warning" role="alert">{workspaceActionError}</div> : null}
        {query.trim() ? (
          /* dsh 独立搜索结果列表（D4）：状态点 + 标题 + 工作区 + 内容摘要 */
          <div className="session-search-results" role="tree" aria-label="搜索结果" aria-busy={Boolean(!searchError && Object.keys(remoteMatches).length === 0)}>
            {searchRows.length === 0 ? <div className="search-empty">无匹配会话</div> : searchRows.map((session) => (
              <button
                key={session.id}
                type="button"
                className={cx("search-result-row", session.id === selectedId && "selected")}
                role="treeitem"
                aria-selected={session.id === selectedId}
                onClick={() => onSelect(session)}
              >
                <span className="search-result-heading">
                  {runtimeBySession[session.id]?.pending || runtimeBySession[session.id]?.active || runtimeBySession[session.id]?.descendantRunning ? <span className={cx("session-dot", runtimeBySession[session.id]?.pending ? "pending" : "running")} aria-hidden="true" /> : null}
                  <span className="search-result-title">{shortText(session.title || "新会话", 28)}</span>
                </span>
                <span className="search-result-meta">
                  <span className="search-result-workspace">{projectName(session.workspace || "") || "未分组"}</span>
                  {remoteMatches[session.id] ? <span className="search-result-snippet">{shortText(remoteMatches[session.id], 72)}</span> : null}
                </span>
              </button>
            ))}
          </div>
        ) : (
        <nav id="session-list" className="session-list" role="tree" aria-label="会话工作区树">
          {groups.map((group) => (
            <section className={cx("session-group", dragOverGroupKey === group.key ? (dragOverGroupAfter ? "drop-after" : "drop-before") : "")} key={group.key} role="group" aria-label={group.name} draggable={!query.trim() && view.groupBy === "workspace" && !group.ungrouped} onDragStart={(event) => { if (!query.trim() && view.groupBy === "workspace" && !group.ungrouped) { event.dataTransfer.effectAllowed = "move"; event.dataTransfer.setData("text/plain", `workspace:${group.key}`); setDraggingGroupKey(group.key) } }} onDragEnd={() => { setDraggingGroupKey(null); setDragOverGroupKey(null); setDragOverGroupAfter(false) }} onDragOver={(event) => { if (draggingGroupKey && draggingGroupKey !== group.key) { event.preventDefault(); setDragOverGroupKey(group.key); setDragOverGroupAfter(event.nativeEvent.offsetY > event.currentTarget.clientHeight / 2) } }} onDrop={(event) => { event.preventDefault(); if (draggingGroupKey && dragOverGroupKey && draggingGroupKey !== dragOverGroupKey) { const keys = groups.filter((item) => !item.ungrouped).map((item) => item.key); const from = keys.indexOf(draggingGroupKey); const to = keys.indexOf(group.key); if (from >= 0 && to >= 0) { const previous = workspaceOrderSnapshot(); keys.splice(from, 1); keys.splice(Math.max(0, to + (dragOverGroupAfter ? 1 : 0)), 0, draggingGroupKey); setWorkspaceOrder(keys); const anchorKey = keys[keys.indexOf(draggingGroupKey) + (dragOverGroupAfter ? 1 : -1)] || null; const movingGroup = groups.find((item) => item.key === draggingGroupKey); void api<{ workspaces?: Array<{ path: string; title: string }> }>("/api/workspaces/reorder", { method: "POST", body: JSON.stringify({ path: movingGroup?.workspace, anchor: anchorKey ? groups.find((item) => item.key === anchorKey)?.workspace : null }) }).then((payload) => { if (payload.workspaces) setWorkspaceRegistry(payload.workspaces) }).catch(() => { setWorkspaceOrder(previous); setWorkspaceActionError("工作区顺序保存失败，已恢复原顺序") }) } } setDraggingGroupKey(null); setDragOverGroupKey(null); setDragOverGroupAfter(false) }}>
              <div className={cx("session-group-head", group.sessions.some((s) => s.id === selectedId) ? "active" : "", workspaceMenuPath === group.workspace ? "menu-open" : "")} role="button" tabIndex={0} aria-expanded={group.expanded} onClick={() => toggleWorkspaceGroup(group.key)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggleWorkspaceGroup(group.key) } }}>
                <svg className="folder-icon-svg" width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M1.8 4.2A1.7 1.7 0 0 1 3.5 2.5h2.6l1.4 1.7h5A1.7 1.7 0 0 1 14.2 6v5.8a1.7 1.7 0 0 1-1.7 1.7H3.5a1.7 1.7 0 0 1-1.7-1.7V4.2Z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/></svg>
                <strong>{group.name}</strong>
                {/* dsh 工作区悬停卡（D8）：名称 + ~ 缩写路径（点击复制）+ 创建时间；未分组桶无卡；菜单打开时抑制 */}
                {group.workspace ? (
                  <span className="workspace-hover-card" role="tooltip">
                    <strong>{group.name}</strong>
                    <span
                      className="workspace-hover-path"
                      title="点击复制路径"
                      onClick={(event) => { event.stopPropagation(); void navigator.clipboard?.writeText(group.workspace || "").catch(() => { /* 剪贴板不可用静默 */ }) }}
                    >{abbreviateHomePath(group.workspace, homePath)}</span>
                    {workspaceRegistry.find((item) => item.path === group.workspace)?.created_at ? <span>创建于 {formatDateTime(workspaceRegistry.find((item) => item.path === group.workspace)!.created_at!)}</span> : null}
                  </span>
                ) : null}
                {/* dsh 分组头"+"（D11）：在该工作区直接新建会话 */}
                {group.workspace && !group.ungrouped ? <span className="workspace-row-plus"><button type="button" aria-label={`在“${group.name}”中新建会话`} title={`在“${group.name}”中新建会话`} onPointerDown={(event) => event.stopPropagation()} onClick={(event) => { event.stopPropagation(); onNewChat(group.workspace || undefined) }}><svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true"><path d="M6 2v8M2 6h8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg></button></span> : null}
                {group.workspace && !group.ungrouped ? <span className="workspace-row-menu"><button type="button" aria-label={`工作区操作：${group.name}`} onPointerDown={(event) => event.stopPropagation()} onClick={(event) => { event.stopPropagation(); setWorkspaceMenuPath(workspaceMenuPath === group.workspace ? null : group.workspace) }}>•••</button>{workspaceMenuPath === group.workspace ? <span className="workspace-context-menu" role="menu" onPointerDown={(event) => event.stopPropagation()}><button type="button" role="menuitem" onClick={() => { setWorkspaceRenamePath(group.workspace); setWorkspaceRenameTitle(group.name); setWorkspaceMenuPath(null) }}>重命名</button><button type="button" role="menuitem" disabled={group.workspace === currentWorkspace} onClick={async () => { if (!window.confirm("只从 Nova 注册表移除，不删除本地目录。继续吗？")) return; try { await api("/api/workspaces/delete", { method: "POST", body: JSON.stringify({ path: group.workspace }) }); setWorkspaceRegistry((items) => items.filter((item) => item.path !== group.workspace)); setWorkspaceMenuPath(null); setWorkspaceActionError("") } catch { setWorkspaceActionError("工作区删除失败，请重试") } }}>删除</button></span> : null}</span> : null}
              </div>
              <div className="session-group-items" hidden={!group.expanded}>
                {/* dsh 空白占位行（D1）：无状态点、无时间、无行菜单——对不存在的内容无从操作 */}
                {draftActive && workspaceGroupKey(group.workspace || null) === workspaceGroupKey(currentWorkspace || null) ? (
                  <div
                    className="session-item draft-placeholder"
                    role="treeitem"
                    aria-selected="true"
                    data-draft-placeholder=""
                    onClick={() => (document.getElementById("message-input") as HTMLTextAreaElement | null)?.focus()}
                  >
                    <strong>新会话</strong>
                  </div>
                ) : null}
                {group.sessions.slice(0, expandedSessionGroups[group.key] ? group.sessions.length : 5).map((session) => (
                  <div
                    key={session.id}
                    className={cx("session-item", session.id === selectedId ? "active" : "", menuSessionId === session.id ? "menu-open" : "", draggingSessionId === session.id ? "dragging" : "", dragOverSessionId === session.id ? (dragOverAfter ? "drop-after" : "drop-before") : "")}
                    role="treeitem"
                     aria-selected={session.id === selectedId}
                     draggable={!query.trim()}
                     onDragStart={(event) => { if (!query.trim()) { event.dataTransfer.effectAllowed = "move"; event.dataTransfer.setData("text/plain", session.id); setDraggingSessionId(session.id) } }}
                     onDragEnd={() => { setDraggingSessionId(null); setDragOverSessionId(null); setDragOverAfter(false) }}
                     onDragOver={(event) => { if (draggingSessionId && draggingSessionId !== session.id) { event.preventDefault(); setDragOverSessionId(session.id); setDragOverAfter(event.nativeEvent.offsetY > event.currentTarget.clientHeight / 2) } }}
                     onDrop={(event) => {
                       event.preventDefault()
                       if (!draggingSessionId || draggingSessionId === session.id) return
                       const ids = group.sessions.map((item) => item.id)
                       const from = ids.indexOf(draggingSessionId)
                       const to = ids.indexOf(session.id)
                       if (from < 0 || to < 0) return
                       const after = event.nativeEvent.offsetY > event.currentTarget.clientHeight / 2
                       ids.splice(from, 1)
                       const targetIndex = ids.indexOf(session.id)
                       ids.splice(Math.max(0, targetIndex + (after ? 1 : 0)), 0, draggingSessionId)
                       const previous = workspaceAccountOrder(group.key)
                        setWorkspaceAccountOrder(group.key, ids)
                        const beforeSessionId = ids[ids.indexOf(draggingSessionId) + 1] || null
                        void api<{ items?: ChatSession[] }>("/api/chat/sessions/reorder", { method: "POST", body: JSON.stringify({ workspace: group.workspace || session.workspace || null, session_id: draggingSessionId, before_session_id: beforeSessionId }) }).catch(() => { setWorkspaceAccountOrder(group.key, previous); setWorkspaceActionError("会话顺序保存失败，已恢复原顺序") })
                       setDraggingSessionId(null); setDragOverSessionId(null); setDragOverAfter(false)
                     }}
                    tabIndex={0}
                    onClick={() => { if (menuSessionId !== session.id) onSelect(session) }}
                    onKeyDown={(e) => { if (e.key === "Enter" && menuSessionId !== session.id) onSelect(session); if (e.key === "ArrowDown") { e.preventDefault(); const next = groups.flatMap((item) => item.sessions); const index = next.findIndex((item) => item.id === session.id); if (next[index + 1]) onSelect(next[index + 1]) } if (e.key === "ArrowUp") { e.preventDefault(); const next = groups.flatMap((item) => item.sessions); const index = next.findIndex((item) => item.id === session.id); if (next[index - 1]) onSelect(next[index - 1]) } }}
                  >
                    {/* dsh 悬停卡（D7）：完整标题（点击复制）+ "N前" + 状态行（含子代理计数 D12） */}
                    <span className="session-hover-card" role="tooltip">
                      <strong className="hover-copyable" onClick={(event) => { event.stopPropagation(); copyTitle(session) }} title="点击复制标题">{session.title || "新会话"}{copiedId === session.id ? <em className="copied-flash">已复制</em> : null}</strong>
                      <span>{relativeTimeAgo(session.updated_at || session.created_at)}</span>
                      {runtimeBySession[session.id]?.pending ? <span className="hover-status warning">等待你的操作</span> : null}
                      {runtimeBySession[session.id]?.active ? <span className="hover-status ongoing">运行中</span> : null}
                      {subagentRunningCount(session.id) > 0 ? <span className="hover-status ongoing">{subagentRunningCount(session.id)} 个子会话运行中</span> : null}
                      {runtimeBySession[session.id]?.completedUnviewed ? <span className="hover-status done">已完成未查看</span> : null}
                    </span>
                     <span className={cx("session-dot", runtimeBySession[session.id]?.pending ? "pending" : runtimeBySession[session.id]?.active || runtimeBySession[session.id]?.descendantRunning ? "running" : runtimeBySession[session.id]?.completedUnviewed ? "completed" : "")} aria-hidden="true" /><span className="visually-hidden">会话状态：{runtimeBySession[session.id]?.pending ? "等待操作" : runtimeBySession[session.id]?.active ? "运行中" : runtimeBySession[session.id]?.descendantRunning ? "子会话运行中" : runtimeBySession[session.id]?.completedUnviewed ? "已完成未查看" : "空闲"}</span>
                    {session.parent_session_id ? <span className="session-fork-icon" title="分支会话">⑂</span> : null}
                    {editingSessionId === session.id ? <input className="session-inline-rename" value={editingTitle} autoFocus onChange={(e) => setEditingTitle(e.target.value)} onClick={(e) => e.stopPropagation()} onKeyDown={(e) => { if (e.key === "Enter" && editingTitle.trim()) { e.preventDefault(); onRename(session, editingTitle.trim()); setEditingSessionId(null) } if (e.key === "Escape") { e.preventDefault(); setEditingSessionId(null) } }} onBlur={() => { if (editingTitle.trim() && editingTitle.trim() !== session.title) onRename(session, editingTitle.trim()); setEditingSessionId(null) }} /> : <strong>{shortText(session.title || "新会话", 28)}</strong>}
                    <span className="session-time">{relativeTime(session.updated_at || session.created_at)}</span>
                    <span className={cx("session-row-menu", menuSessionId === session.id ? "open" : "")}>
                      <button
                        type="button"
                        className="session-more"
                        aria-label={`会话操作：${session.title}`}
                        aria-expanded={menuSessionId === session.id}
                         onPointerDown={(e) => e.stopPropagation()}
                        onClick={(e) => { e.stopPropagation(); setMenuFocusIndex(0); setMenuSessionId(menuSessionId === session.id ? null : session.id) }}
                      >•••</button>
                      {menuSessionId === session.id ? (
                        <div className="session-context-menu" role="menu" onPointerDown={(event) => event.stopPropagation()} data-session-menu={session.id} onMouseLeave={() => setMenuSessionId(null)} onKeyDown={(event) => { if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); setMenuFocusIndex((index) => (index + (event.key === "ArrowDown" ? 1 : 2)) % 3) } if (event.key === "Escape") { event.preventDefault(); setMenuSessionId(null) } }} onClick={(e) => e.stopPropagation()}>
                          <button type="button" role="menuitem" onClick={() => { setMenuSessionId(null); setEditingSessionId(session.id); setEditingTitle(session.title) }}>重命名</button>
                          <button type="button" role="menuitem" onClick={() => { setMenuSessionId(null); onFork(session) }}>创建分支</button>
                          <button type="button" role="menuitem" onClick={() => { setMenuSessionId(null); onArchive(session) }}>归档会话</button>
                                                  </div>
                      ) : null}
                    </span>
                  </div>
                ))}
                {group.sessions.length > 5 ? <button className="session-overflow-button" type="button" aria-expanded={Boolean(expandedSessionGroups[group.key])} onClick={() => setExpandedSessionGroups((current) => ({ ...current, [group.key]: !current[group.key] }))}>{expandedSessionGroups[group.key] ? "收起" : `展开其余 ${group.sessions.length - 5} 条`}</button> : null}
              </div>
            </section>
          ))}
        </nav>
        )}
      </div>
      {workspaceRenamePath ? <div className="workspace-rename-popover" role="dialog"><input autoFocus value={workspaceRenameTitle} onChange={(event) => setWorkspaceRenameTitle(event.target.value)} onKeyDown={async (event) => { if (event.nativeEvent.isComposing || event.keyCode === 229) return; if (event.key === "Escape") { setWorkspaceRenamePath(null); return } if (event.key !== "Enter" || !workspaceRenameTitle.trim()) return; try { await api("/api/workspaces/rename", { method: "POST", body: JSON.stringify({ path: workspaceRenamePath, title: workspaceRenameTitle.trim() }) }); setWorkspaceRegistry((items) => items.map((item) => item.path === workspaceRenamePath ? { ...item, title: workspaceRenameTitle.trim() } : item)); setWorkspaceRenamePath(null); setWorkspaceActionError("") } catch { setWorkspaceActionError("工作区重命名失败，请重试") } }} /><button type="button" onClick={() => setWorkspaceRenamePath(null)}>取消</button></div> : null}
      <div className="sidebar-foot">
        <button className="sidebar-foot-button" type="button" id="open-settings" onClick={onOpenSettings}>
          <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M6.8 1.8h2.4l.4 1.7 1.5.9 1.6-.7 1.2 2.1-1.2 1.2v1.7l1.2 1.2-1.2 2.1-1.6-.7-1.5.9-.4 1.7H6.8l-.4-1.7-1.5-.9-1.6.7-1.2-2.1 1.2-1.2V8.2L2.1 7l1.2-2.1 1.6.7 1.5-.9.4-1.7Z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round"/><circle cx="8" cy="8" r="2.1" stroke="currentColor" strokeWidth="1.2"/></svg>
          <span>设置</span>
        </button>
      </div>
    </aside>
  )
}

/* ---- 头部 ---- */
function ChatHeader({ title, modeLabel, bgTasks, activeTab, onTab, onSessionLog, commentCount, commentPanelOpen, onToggleComments }: {
  title: string
  modeLabel: string
  bgTasks: number
  activeTab: "chat" | "trace"
  onTab: (tab: "chat" | "trace") => void
  onSessionLog: () => void
  commentCount: number
  commentPanelOpen: boolean
  onToggleComments: () => void
}) {
  return (
    <header className="chat-header" id="chat-header">
      <div className="header-title-row">
        <div className="header-title-cluster">
          <span className="header-session-title" id="header-session-title">{title}</span>
          <span className="header-mode" id="header-mode">{modeLabel}</span>
          {bgTasks > 0 ? <button className="header-bg-tasks" type="button">{bgTasks} 个后台任务 <span aria-hidden="true">▾</span></button> : null}
        </div>
        <div className="header-utilities">
          {commentCount > 0 || commentPanelOpen ? (
            <button
              className={cx("header-comment-toggle", commentPanelOpen ? "active" : "")}
              id="header-comment-toggle"
              type="button"
              title={commentPanelOpen ? "收起评论栏" : "展开评论栏"}
              aria-pressed={commentPanelOpen}
              onClick={onToggleComments}
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M14 7.3c0 3.15-2.68 5.7-6 5.7-.66 0-1.3-.1-1.88-.28L3.2 14l.78-2.6A5.44 5.44 0 0 1 2 7.3C2 4.15 4.68 1.6 8 1.6s6 2.55 6 5.7Z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round"/></svg>
              <span>评论</span>
              {commentCount > 0 ? <span className="header-comment-badge">{commentCount > 99 ? "99+" : commentCount}</span> : null}
            </button>
          ) : null}
          <button className="session-log-button" id="session-log-open" type="button" onClick={onSessionLog}>Session log ⤓</button>
        </div>
      </div>
      <div className="header-tabs">
        <button className={cx("header-tab", activeTab === "chat" ? "active" : "")} type="button" onClick={() => onTab("chat")}>对话</button>
        <button className={cx("header-tab", activeTab === "trace" ? "active" : "")} type="button" onClick={() => onTab("trace")}>轨迹</button>
      </div>
    </header>
  )
}

/* ---- 统计行（dsh StatsLine） ---- */
function StatsLine({ sessionId }: { sessionId: string | null }) {
  const [text, setText] = useState("")
  useEffect(() => {
    if (!sessionId) { setText(""); return }
    let alive = true
    api<{ items?: TraceEvent[] }>(`/api/chat/sessions/${encodeURIComponent(sessionId)}/trace`)
      .then((result: { items?: TraceEvent[] }) => {
        const items = result.items || []
        if (!alive) return
        let turns = 0, steps = 0, toolMs = 0
        let promptTokens = 0, completionTokens = 0
        const spans: number[] = []
        let openAt: number | null = null
        for (const e of items) {
          const ts = e.created_at ? Date.parse(e.created_at) : NaN
          if (Number.isNaN(ts)) continue
          if (e.event_type === "turn.started") { turns += 1; openAt = ts }
          if (e.event_type === "turn.completed" && openAt !== null) { spans.push(ts - openAt); openAt = null }
          if (e.event_type === "tool.completed") {
            steps += 1
            const d = (e as unknown as { duration_ms?: number }).duration_ms
            if (typeof d === "number") toolMs += d
          }
          if (e.event_type === "tokens.usage") {
            const d = (e as unknown as { data?: { prompt_tokens?: number; completion_tokens?: number } }).data
            if (d) {
              promptTokens += Number(d.prompt_tokens) || 0
              completionTokens += Number(d.completion_tokens) || 0
            }
          }
        }
        if (openAt !== null) spans.push(Date.now() - openAt)
        if (turns === 0) { setText(""); return }
        const wallMs = spans.reduce((a, b) => a + b, 0)
        const llmMs = Math.max(0, wallMs - toolMs)
        const fmt = (ms: number) => {
          const sec = Math.round(ms / 1000)
          return sec < 60 ? `${sec}s` : `${Math.floor(sec / 60)}m${String(sec % 60).padStart(2, "0")}s`
        }
        // token-meter（dsh llm/token-meter）：provider 真实用量；无 usage 事件的旧会话隐藏该段
        const tokenText = promptTokens + completionTokens > 0
          ? ` | tokens ↑${promptTokens.toLocaleString()} ↓${completionTokens.toLocaleString()}`
          : ""
        setText(`${turns} 轮 · ${steps} 步 | LLM ${fmt(llmMs)} · 工具调用 ${fmt(toolMs)}${tokenText} | 平均每轮 ${fmt(wallMs / turns)}`)
      })
      .catch(() => { if (alive) setText("") })
    return () => { alive = false }
  }, [sessionId])
  if (!text) return null
  return <div className="stats-line" id="stats-line">{text}</div>
}

/* ---- 主时间线 ---- */
function ConversationView({ entries, streamingText, onForkAt }: { entries: TimelineEntry[]
  streamingText: string | null
  onForkAt?: (messageId: string, messageRole: string) => void
}) {
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const bottomRef = useRef<HTMLDivElement | null>(null)
  const [following, setFollowing] = useState(true)
  const [hasNewer, setHasNewer] = useState(false)
  const isNearBottom = () => {
    const el = scrollRef.current
    return !el || el.scrollHeight - el.scrollTop - el.clientHeight < 72
  }
  useEffect(() => {
    if (following) {
      bottomRef.current?.scrollIntoView({ block: "end" })
      setHasNewer(false)
    } else if (streamingText !== null) {
      setHasNewer(true)
    }
  }, [entries.length, streamingText, following])
  return (
    <div
      className="scroll-body"
      id="messages-scroll"
      ref={scrollRef}
      onScroll={() => {
        const near = isNearBottom()
        setFollowing(near)
        if (near) setHasNewer(false)
      }}
    >
      <div className="messages" id="messages">
      {entries.map((entry) => {
        if (entry.kind === "message") return <MessageView key={entry.key} message={entry.message} onForkAt={onForkAt} />
        if (entry.kind === "checkpoint") return <CheckpointView key={entry.key} message={entry.message} />
        if (entry.kind === "think") return <ThinkRow key={entry.key} text={entry.text} running={entry.running} />
        if (entry.kind === "tool") {
          return <ToolEventRow key={entry.key} view={entry.view} />
        }
        if (entry.kind === "permission") {
          return <PermissionCard key={entry.key} item={entry.item} />
        }
        return <QuestionCard key={entry.key} item={entry.item} />
      })}
      {streamingText !== null ? (
        <article className="message assistant streaming">
          <div className="message-head"><div className="message-role">Nova</div></div>
          <div className="message-content"><Markdown content={streamingText} /></div>
        </article>
      ) : null}
      <div ref={bottomRef} />
       {!following || hasNewer ? <button className="jump-latest" type="button" onClick={() => { bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); setFollowing(true); setHasNewer(false) }} aria-label="跳转到最新消息" title="跳转到最新消息">↓</button> : null}
      </div>
    </div>
  )
}

/* ---- App 根 ---- */
export default function App() {
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  // dsh 空白占位会话（D1）：点"新会话"只进入草稿态，首条消息才真正建会话；
  // 选回已有会话或刷新即放弃，不再积累"新对话"空壳。
  const [draftActive, setDraftActive] = useState(false)
  const [entries, setEntries] = useState<TimelineEntry[]>([])
  const inline = useInlineComments(selectedId, entries.length)
  const [takeovers, setTakeovers] = useState<PendingApprovalItem[]>([])
  const [runtimeBySession, setRuntimeBySession] = useState<Record<string, { active?: boolean; pending?: boolean }>>({})
  const [runtimeConfig, setRuntimeConfig] = useState<RuntimeConfig>({})
  const [workspace, setWorkspace] = useState("")
  const [workspacePickerOpen, setWorkspacePickerOpen] = useState(false)
  const [version, setVersion] = useState("")
  const [streamState, setStreamState] = useState("")
  const [streamingText, setStreamingText] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<"chat" | "trace">("chat")
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [draft, setDraft] = useState("")
  const [running, setRunning] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)

  /* ---- / 与 $ 触发弹窗（dsh ui-input-trigger 交互契约） ---- */
  const [triggerCommands, setTriggerCommands] = useState<TriggerCandidate[]>([])
  const [triggerSkills, setTriggerSkills] = useState<TriggerCandidate[]>([])
  const [triggerCaret, setTriggerCaret] = useState(0)
  const [triggerHighlight, setTriggerHighlight] = useState(0)
  // 记录“本 token 已被 Esc/外点关闭”，query 变化后自动重开（dsh 语义）。
  const [triggerDismissedKey, setTriggerDismissedKey] = useState<string | null>(null)
  useEffect(() => {
    // 命令与技能清单是本地只读端点，挂载时拉一次即可。
    void api<{ items?: Array<{ name: string; description?: string; argument_hint?: string }> }>("/api/commands")
      .then((payload) => {
        setTriggerCommands((payload.items || []).map((item) => ({ name: item.name, description: item.description || "", hint: item.argument_hint || undefined })))
      })
      .catch(() => {})
    void api<{ skills?: Array<{ name: string; description?: string; user_invocable?: boolean }> }>("/api/skills/status")
      .then((payload) => {
        setTriggerSkills((payload.skills || []).filter((item) => item.user_invocable !== false).map((item) => ({ name: item.name, description: item.description || "" })))
      })
      .catch(() => {})
  }, [])
  const triggerHit = useMemo(() => detectComposerTrigger(draft, triggerCaret), [draft, triggerCaret])
  const triggerCandidates = useMemo(
    () => (triggerHit ? filterTriggerCandidates(triggerHit.kind, triggerHit.query, triggerCommands, triggerSkills) : []),
    [triggerHit, triggerCommands, triggerSkills],
  )
  const triggerOpen = triggerHit !== null && triggerCandidates.length > 0 && triggerDismissedKey !== triggerHit.tokenKey
  // 菜单高度按 composer 上方实际可用空间收敛（dsh useAnchoredMaxHeight 同款）：
  // 设计上限 320px，超出可用空间则钳制，菜单体内部滚动，绝不溢出到页眉底下。
  const [triggerMenuMaxHeight, setTriggerMenuMaxHeight] = useState(320)
  useEffect(() => {
    if (!triggerOpen) return
    const measure = () => {
      const card = document.querySelector<HTMLElement>("#chat-form")
      if (!card) return
      const spaceAbove = card.getBoundingClientRect().top - 12
      setTriggerMenuMaxHeight(Math.max(160, Math.min(320, spaceAbove)))
    }
    measure()
    window.addEventListener("resize", measure)
    window.addEventListener("scroll", measure, true)
    return () => { window.removeEventListener("resize", measure); window.removeEventListener("scroll", measure, true) }
  }, [triggerOpen, triggerCandidates.length])
  // 候选集变化时高亮回到第一项；越界时收敛到末尾。
  useEffect(() => { setTriggerHighlight(0) }, [triggerHit?.tokenKey])
  useEffect(() => {
    if (triggerHighlight >= triggerCandidates.length) setTriggerHighlight(Math.max(0, triggerCandidates.length - 1))
  }, [triggerCandidates.length, triggerHighlight])
  // 高亮行滚动进可视区（dsh MenuView 同款：combobox 焦点不离开 textarea）。
  useEffect(() => {
    if (!triggerOpen || triggerHighlight === 0) return
    document.getElementById(`composer-trigger-option-${triggerHighlight}`)?.scrollIntoView({ block: "nearest" })
  }, [triggerOpen, triggerHighlight])
  // 菜单外 pointerdown 关闭（点 composer 卡片内部不关，dsh 同款边界）。
  useEffect(() => {
    if (!triggerOpen) return
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as HTMLElement | null
      if (!target) return
      if (target.closest(".composer-trigger-menu") || target.closest(".composer-card")) return
      setTriggerDismissedKey(triggerHit?.tokenKey ?? null)
    }
    document.addEventListener("pointerdown", onPointerDown, true)
    return () => { document.removeEventListener("pointerdown", onPointerDown, true) }
  }, [triggerOpen, triggerHit])
  const pickTrigger = (index: number) => {
    const hit = triggerHit
    const candidate = triggerCandidates[index]
    if (!hit || !candidate) return
    const token = triggerToken(hit.kind, candidate.name)
    setDraft(token)
    setTriggerCaret(token.length)
    setTriggerDismissedKey(null)
    requestAnimationFrame(() => {
      const el = textareaRef.current
      if (!el) return
      el.focus()
      el.setSelectionRange(token.length, token.length)
    })
  }

  const reloadSessions = async () => {
    try {
      const list = await api<ChatSession[]>("/api/chat/sessions")
      setSessions(list)
    } catch { /* 列表失败不打断主流程 */ }
  }

  const reloadShell = async () => {
    try {
      const [config, ws] = await Promise.all([
        api<RuntimeConfig>("/api/runtime/config"),
        api<{ project_root?: string; workspace?: string }>("/api/workspace/status?quick=true"),
      ])
      setRuntimeConfig(config)
      setWorkspace(String(ws.project_root || ws.workspace || ""))
      const models = Array.isArray(config.models) ? config.models : []
      if (!models.includes(String(config.model || "")) && config.model) setVersion(String(config.version || ""))
    } catch { /* 静默 */ }
    try {
      const health = await api<{ version?: string }>("/api/health")
      setVersion(String(health.version || ""))
    } catch { /* 静默 */ }
  }

  const loadTimeline = async (sessionId: string) => {
    const state = await api<{
      session?: ChatSession
      timeline?: { items?: Array<{ kind: string; item: Record<string, unknown> }> }
      pending_approvals?: PendingApprovalItem[]
    }>(`/api/chat/sessions/${encodeURIComponent(sessionId)}/runtime-state`)
    setEntries(foldTimeline(state.timeline?.items || []))
    setTakeovers((state.pending_approvals || []).map((a) => ({
      ...a,
      questions: (a as unknown as { questions?: PendingApprovalItem["questions"] }).questions || (a.data as { questions?: PendingApprovalItem["questions"] } | undefined)?.questions || [],
    })))
  }

  useEffect(() => {
    void reloadSessions()
    void reloadShell()
  }, [])
  useEffect(() => {
    if (sessions.length === 0) return
    let cancelled = false
    const refresh = async () => {
      const next: Record<string, { active?: boolean; pending?: boolean; descendantRunning?: boolean; completedUnviewed?: boolean }> = {}
      await Promise.all(sessions.map(async (session) => {
        try {
          const state = await api<{ active?: boolean; descendant_running?: boolean; pending_approvals?: unknown[]; runtime?: { final_answer?: unknown } }>(`/api/chat/sessions/${encodeURIComponent(session.id)}/runtime-state?passive=true`)
          next[session.id] = { active: Boolean(state.active), pending: Boolean(state.pending_approvals?.length), descendantRunning: Boolean((state as { descendant_running?: boolean }).descendant_running), completedUnviewed: Boolean(state.runtime?.final_answer && session.id !== selectedId) }
        } catch { /* 单个会话状态失败不影响列表 */ }
      }))
      if (!cancelled) setRuntimeBySession(next)
    }
    void refresh()
    const timer = window.setInterval(refresh, 5000)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [sessions, selectedId])

  const handleForkAt = async (messageId: string, _role: string) => {
    if (!selectedId) return
    try {
      const child = await api<ChatSession>(
        `/api/chat/sessions/${encodeURIComponent(selectedId)}/fork`,
        { method: "POST", body: JSON.stringify({ at_seq: null }) },
      )
      await reloadSessions()
      await selectSession(child)
    } catch (exc) {
      console.error("fork failed", exc)
    }
  }

  const selectSession = async (session: ChatSession) => {
    setDraftActive(false)
    setSelectedId(session.id)
    setActiveTab("chat")
    try {
      await loadTimeline(session.id)
    } catch { /* 选择失败保持空 */ }
  }

  // dsh startSession 语义（D1/D11）：不落库，仅进入草稿态；带 workspace 参数时
  // 先切换到目标工作区（分组头"+"在该工作区新建会话）。
  const newChat = async (workspacePath?: string) => {
    if (workspacePath && workspacePath !== workspace) {
      try {
        const next = await api<{ current_root?: string }>("/api/workspace/select", { method: "POST", body: JSON.stringify({ path: workspacePath }) })
        setWorkspace(String(next.current_root || workspacePath))
        await reloadSessions()
      } catch { /* 切换失败仍在当前工作区开草稿 */ }
    }
    setDraftActive(true)
    setSelectedId(null)
    setEntries([])
    setTakeovers([])
  }

  const renameSession = async (session: ChatSession, title: string) => {
    if (!title.trim() || title.trim() === session.title) return
    await api<ChatSession>(`/api/chat/sessions/${encodeURIComponent(session.id)}`, {
      method: "PATCH",
      body: JSON.stringify({ title: title.trim() }),
    })
    await reloadSessions()
  }

  const forkSession = async (session: ChatSession) => {
    const child = await api<ChatSession>(`/api/chat/sessions/${encodeURIComponent(session.id)}/fork`, {
      method: "POST",
      body: JSON.stringify({ at_seq: null }),
    })
    await reloadSessions()
    await selectSession(child)
  }

  const archiveSession = async (session: ChatSession) => {
    await api<ChatSession>(`/api/chat/sessions/${encodeURIComponent(session.id)}/archive`, {
      method: "POST",
      body: JSON.stringify({ archived: true }),
    })
    if (selectedId === session.id) {
      setSelectedId(null)
      setEntries([])
    }
    await reloadSessions()
  }

  const deleteSession = async (id: string) => {
    if (!window.confirm("删除这个对话？")) return
    await api(`/api/chat/sessions/${encodeURIComponent(id)}`, { method: "DELETE" })
    if (selectedId === id) {
      setSelectedId(null)
      setEntries([])
    }
    await reloadSessions()
  }

  /* ---- NDJSON 流消费（dsh stream 协议移植） ---- */
  const send = async (content: string) => {
    let sessionId = selectedId
    if (!sessionId) {
      // 草稿态首条消息：此刻才创建会话（dsh 空白占位转正），并走自动命名。
      const session = await api<ChatSession>("/api/chat/sessions", { method: "POST", body: JSON.stringify({ title: "新对话" }) })
      sessionId = session.id
      setSelectedId(sessionId)
      setDraftActive(false)
      await reloadSessions()
    }
    setEntries((prev) => [...prev, { kind: "message", key: `local-u-${Date.now()}`, message: { id: `local-${Date.now()}`, role: "user", content, created_at: new Date().toISOString() } }])
    setRunning(true)
    setStreamingText("")
    setStreamState("Nova 正在处理")
    const controller = new AbortController()
    abortRef.current = controller
    const liveTools = new Map<string, ToolEventView>()
    try {
      const response = await fetch(`/api/chat/sessions/${sessionId}/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
        signal: controller.signal,
      })
      if (!response.ok || !response.body) throw new Error(await response.text())
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ""
      let assistantText = ""
      let thinkKey: string | null = null
      let thinkText = ""
      const upsertThink = (running: boolean) => {
        const key = thinkKey
        if (key === null) return
        setEntries((prev) => {
          const idx = prev.findIndex((e) => e.key === key)
          const next = [...prev]
          const item = { kind: "think" as const, key, text: thinkText, running }
          if (idx >= 0 && next[idx].kind === "think") next[idx] = item
          else next.push(item)
          return next
        })
      }
      const pushTool = (view: ToolEventView, replaceKey: string) => {
        setEntries((prev) => {
          const idx = prev.findIndex((e) => e.key === replaceKey)
          const next = [...prev]
          if (idx >= 0 && next[idx].kind === "tool") next[idx] = { kind: "tool", key: replaceKey, view }
          else next.push({ kind: "tool", key: replaceKey, view })
          return next
        })
      }
      const handleLine = (line: string) => {
        if (!line.trim()) return
        let event: Record<string, unknown>
        try {
          event = JSON.parse(line) as Record<string, unknown>
        } catch {
          return
        }
        const type = String(event.type || "")
        if (type === "reasoning_delta") {
          // 思考在正文/工具调用之前发生：按段累积，插在消息流时间线上
          if (thinkKey === null) {
            thinkKey = `think-${Date.now()}`
            thinkText = ""
          }
          thinkText += String(event.delta || "")
          upsertThink(true)
          setStreamState("Nova 正在思考")
        } else if (type === "assistant_delta") {
          // 正文开始 → 当前思考段落封口（running=false）
          if (thinkKey !== null) { upsertThink(false); thinkKey = null; thinkText = "" }
          assistantText += String(event.delta || "")
          setStreamingText(assistantText)
          setStreamState("Nova 正在输出")
        } else if (type === "tool_start") {
          if (thinkKey !== null) { upsertThink(false); thinkKey = null; thinkText = "" }
          const tool = String(event.tool || "")
          const callId = String(event.call_id || tool || `tool-${Date.now()}`)
          const args = (event.arguments || {}) as Record<string, unknown>
          const view: ToolEventView = { callId, tool, args, output: "", data: (event.data || {}) as ToolCallData, state: "running" }
          liveTools.set(callId, view)
          pushTool(view, `t-${callId}`)
          setStreamState(`工具执行：${tool}`)
        } else if (type === "tool_output") {
          const callId = String(event.call_id || "")
          const view = liveTools.get(callId)
          if (view) {
            view.output = `${view.output}${String(event.output || "")}`
            pushTool({ ...view }, `t-${callId}`)
          }
        } else if (type === "tool_done") {
          const callId = String(event.call_id || "")
          const view = liveTools.get(callId)
          if (view) {
            const ok = Boolean(event.ok)
            const data = (event.data || {}) as ToolCallData
            const cancelled = data.status === "cancelled"
            const finalView: ToolEventView = {
              ...view,
              output: String(event.output ?? view.output),
              data,
              state: ok ? "ok" : cancelled ? "stopped" : "error",
            }
            liveTools.delete(callId)
            pushTool(finalView, `t-${callId}`)
            setStreamState(ok ? "工具完成，继续推理" : "工具失败，继续处理")
          }
        } else if (type === "permission_request") {
          const tool = String(event.tool || "工具")
          const callId = String(event.call_id || event.id || "")
          setTakeovers((prev) => [...prev, {
            id: callId,
            call_id: callId,
            tool,
            arguments: (event.arguments || {}) as Record<string, unknown>,
            reason: String(event.message || ""),
            data: (event.data || {}) as ToolCallData,
          }])
          setStreamState(`${tool} 等待审批`)
        } else if (type === "session_title") {
          // dsh session-title：自动命名事件——侧栏标题实时更新，不等刷新。
          const title = String(event.title || "")
          if (title) {
            setSessions((prev) => prev.map((s) => (s.id === sessionId ? { ...s, title } : s)))
          }
        } else if (type === "assistant_done") {
          const message = (event.message || {}) as ChatMessage
          setStreamingText(null)
          assistantText = ""
          if (message.content) {
            setEntries((prev) => [...prev, { kind: "message", key: `m-${message.id}`, message }])
          }
          setStreamState("回复完成")
          window.setTimeout(() => setStreamState((cur) => (cur === "回复完成" ? "" : cur)), 2500)
        } else if (type === "runtime_event") {
          const inner = (event.event || {}) as Record<string, unknown>
          const eventType = String(inner.event_type || "")
          if (eventType === "turn.started") setStreamState(String(inner.title || "Nova 正在处理"))
          if (eventType === "turn.completed") setStreamState(String(inner.title || "回复完成"))
          if (eventType === "turn.cancelled" || eventType === "turn.failed") {
            setStreamState(String(inner.title || inner.message || "已停止"))
            setStreamingText(null)
          }
          if (eventType === "user.question" || inner.type === "user_question") {
            const data = (inner.data || {}) as { questions?: PendingApprovalItem["questions"] }
            const callId = String(inner.call_id || inner.id || "")
            setTakeovers((prev) => [...prev, {
              id: callId,
              call_id: callId,
              tool: String(inner.tool || ""),
              arguments: {},
              questions: data.questions || (inner as { questions?: PendingApprovalItem["questions"] }).questions || [],
              reason: String(inner.message || ""),
              data: data as ToolCallData,
            }])
            setStreamState("等待你的回答")
          }
        } else if (type === "error") {
          const message = (event.message || {}) as ChatMessage
          setStreamingText(null)
          setEntries((prev) => [...prev, {
            kind: "message",
            key: `err-${Date.now()}`,
            message: {
              id: `err-${Date.now()}`,
              role: "error",
              content: typeof event.message === "string" ? event.message : String(message.content || "模型调用失败"),
              created_at: new Date().toISOString(),
            },
          }])
          setStreamState("请求失败")
        }
      }
      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split("\n")
        buffer = lines.pop() || ""
        for (const line of lines) handleLine(line)
      }
      if (buffer.trim()) handleLine(buffer)
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        setEntries((prev) => [...prev, {
          kind: "message",
          key: `err-${Date.now()}`,
          message: { id: `err-${Date.now()}`, role: "error", content: String(error instanceof Error ? error.message : error), created_at: new Date().toISOString() },
        }])
      }
      setStreamState("已停止")
    } finally {
      setRunning(false)
      setStreamingText(null)
      abortRef.current = null
      if (sessionId) {
        try { await loadTimeline(sessionId) } catch { /* 恢复失败不打断 */ }
      }
      await reloadSessions()
    }
  }

  const submit = async () => {
    const content = draft.trim()
    if (!content || running) return
    setDraft("")
    if (textareaRef.current) textareaRef.current.style.height = "auto"
    await send(content)
  }

  const cancel = () => {
    abortRef.current?.abort()
    void api(`/api/chat/sessions/${encodeURIComponent(selectedId || "")}/cancel`, { method: "POST" }).catch(() => {})
  }

  const regenerate = async () => {
    const lastUser = [...entries].reverse().find((e) => e.kind === "message" && e.message.role === "user")
    const text = lastUser && lastUser.kind === "message" ? lastUser.message.content : ""
    if (!text || running) return
    await send(text)
  }

  const downloadSessionLog = async () => {
    if (!selectedId) return
    const trace = await api<{ items?: TraceEvent[] }>(`/api/chat/sessions/${encodeURIComponent(selectedId)}/trace`)
    const blob = new Blob([JSON.stringify(trace, null, 2)], { type: "application/json" })
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = `nova-session-log-${selectedId}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  const hasContent = entries.length > 0
  const mainState = !hasContent && streamingText === null
    ? "empty"
    : activeTab === "trace"
      ? "trace"
      : "conversation"
  const modeLabel = PERMISSION_MODE_LABELS[String(runtimeConfig.permission_mode || "")] || "标准模式"
  // 后端权威字段是 provider_model；兼容旧 payload 的 model 字段。
  const model = String(runtimeConfig.provider_model || runtimeConfig.model || "glm-4.7")
  const activeProviderId = String(runtimeConfig.active_provider_id || "")
  const modelSelectValue = `${activeProviderId}::${model}`
  const modelOptions = useMemo(() => {
    const list = Array.isArray(runtimeConfig.models) ? runtimeConfig.models.map(String) : []
    return list.includes(model) ? list : [model, ...list]
  }, [runtimeConfig.models, model])
  const selectedSession = sessions.find((s) => s.id === selectedId)

  return (
    <div className="app-shell" data-main-state={mainState}>
      <SettingsDialog open={settingsOpen} onClose={() => setSettingsOpen(false)} onSaved={() => void reloadShell()} />
      <Sidebar
        sessions={sessions}
        selectedId={selectedId}
        currentWorkspace={workspace}
        version={version}
         runtimeBySession={runtimeBySession}
         draftActive={draftActive}
        onSelect={selectSession}
        onRename={renameSession}
        onFork={forkSession}
        onArchive={archiveSession}
        onNewChat={newChat}
        onOpenSettings={() => setSettingsOpen(true)}
        onWorkspaceSwitched={setWorkspace}
      />
      <main className="main-col">
        {hasContent || streamingText !== null ? (
          <ChatHeader
            title={selectedSession?.title || "新会话"}
            modeLabel={modeLabel}
            bgTasks={0}
            activeTab={activeTab}
            onTab={setActiveTab}
            onSessionLog={() => void downloadSessionLog()}
            commentCount={inline.threads.length}
            commentPanelOpen={inline.panelOpen}
            onToggleComments={() => {
              if (inline.panelOpen) {
                inline.setPanelOpen(false)
                inline.setActiveAnchorId(null)
              } else {
                inline.setActiveAnchorId(null)
                inline.setPanelOpen(true)
              }
            }}
          />
        ) : null}
        {activeTab === "trace" && selectedId ? (
          <TraceView sessionId={selectedId} />
        ) : !hasContent && streamingText === null ? (
          <div className="empty-hero" id="empty-hero">
            <div className="hero-headline">
              <span className="hero-mark" aria-hidden="true">✦</span>
              <h1>探索未至之境</h1>
              <span className="hero-preview-badge">预览版</span>
            </div>
            <div className="hero-selector-row">
              <button className={cx("hero-chip", workspacePickerOpen ? "active" : "")} type="button" title="切换工作区" onClick={() => setWorkspacePickerOpen((v) => !v)}>
                <svg className="hero-selector-icon" width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M1.8 4.2A1.7 1.7 0 0 1 3.5 2.5h2.6l1.4 1.7h5A1.7 1.7 0 0 1 14.2 6v5.8a1.7 1.7 0 0 1-1.7 1.7H3.5a1.7 1.7 0 0 1-1.7-1.7V4.2Z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/></svg>
                <span>{projectName(workspace) || "选择工作区"}</span>
                <svg className="hero-selector-chevron" width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M4 6.5 8 10.5 12 6.5" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"/></svg>
              </button>
              <button className="hero-chip" type="button" title="切换模式">
                <svg className="hero-selector-icon" width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M8 1.5 13.5 3.5V7.5C13.5 11 11.2 13.6 8 14.5C4.8 13.6 2.5 11 2.5 7.5V3.5L8 1.5Z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/></svg>
                <span>{modeLabel}</span>
                <span className="hero-selector-chevron" aria-hidden="true">▾</span>
              </button>
            </div>
            {workspacePickerOpen ? (
              <div className="hero-workspace-pop">
                <WorkspacePicker current={workspace} onSwitched={(root) => { setWorkspace(root); setWorkspacePickerOpen(false) }} />
              </div>
            ) : null}
          </div>
        ) : (
          <ConversationView
            entries={entries}
            streamingText={streamingText}
            onForkAt={handleForkAt}
          />
        )}
        <div className="takeover-dock" id="takeover-dock">
          {takeovers.map((item) => (
            (item.questions && item.questions.length > 0)
              ? <QuestionCard key={`tk-q-${item.call_id}`} item={item} onAnswered={() => setTakeovers((prev) => prev.filter((t) => t.call_id !== item.call_id))} />
              : <PermissionCard key={`tk-p-${item.call_id}`} item={item} onResolved={() => setTakeovers((prev) => prev.filter((t) => t.call_id !== item.call_id))} />
          ))}
        </div>
        <form
          className="composer-card"
          id="chat-form"
          data-composer-card=""
          onSubmit={(e) => {
            e.preventDefault()
            void submit()
          }}
        >
          {triggerOpen && triggerHit ? (
            <div
              className="composer-trigger-menu"
              id="composer-trigger-listbox"
              role="listbox"
              aria-label="命令与技能候选"
              style={{ maxHeight: triggerMenuMaxHeight }}
            >
              <div className="composer-trigger-head">{triggerHit.kind === "slash" ? "内置指令" : "技能"}</div>
              <div className="composer-trigger-viewport">
                {triggerCandidates.map((candidate, index) => (
                  <button
                    key={`${triggerHit.kind}-${candidate.name}`}
                    id={`composer-trigger-option-${index}`}
                    type="button"
                    role="option"
                    aria-selected={index === triggerHighlight}
                    className={cx("composer-trigger-item", index === triggerHighlight && "active")}
                    // mousedown 而非 click：焦点留在 textarea（dsh combobox 模式）。
                    onMouseDown={(e) => { e.preventDefault(); pickTrigger(index) }}
                  >
                    <span className="composer-trigger-name">{triggerHit.kind === "slash" ? candidate.name : `$${candidate.name}`}</span>
                    {candidate.hint ? <span className="composer-trigger-hint">{candidate.hint}</span> : null}
                    <span className="composer-trigger-desc">{candidate.description}</span>
                  </button>
                ))}
              </div>
              <div className="composer-trigger-foot">↑↓ 选择 · Enter 确认 · Esc 关闭</div>
            </div>
          ) : null}
          <textarea
            id="message-input"
            ref={textareaRef}
            rows={1}
            placeholder="给智能体发消息（/ 指令 · $ 技能）"
            value={draft}
            role="combobox"
            aria-expanded={triggerOpen}
            aria-controls="composer-trigger-listbox"
            aria-activedescendant={triggerOpen ? `composer-trigger-option-${triggerHighlight}` : undefined}
            onChange={(e) => {
              setDraft(e.target.value)
              setTriggerCaret(e.target.selectionStart ?? e.target.value.length)
              const el = e.target
              el.style.height = "auto"
              el.style.height = `${el.scrollHeight}px`
            }}
            onSelect={(e) => { setTriggerCaret((e.target as HTMLTextAreaElement).selectionStart ?? 0) }}
            onKeyDown={(e) => {
              // isComposing：中文输入法组字中的 Enter 是确认候选词，不是发送（dsh 同样拦截）。
              if (e.nativeEvent.isComposing) return
              // 触发菜单键盘仲裁（dsh arbitrate 契约）：↑↓ 移动、Enter 选中、Esc 关闭。
              if (triggerOpen) {
                if (e.key === "ArrowUp" || e.key === "ArrowDown") {
                  e.preventDefault()
                  setTriggerHighlight((index) => (index + (e.key === "ArrowDown" ? 1 : triggerCandidates.length - 1)) % triggerCandidates.length)
                  return
                }
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault()
                  pickTrigger(triggerHighlight)
                  return
                }
                if (e.key === "Escape") {
                  e.preventDefault()
                  e.stopPropagation()
                  setTriggerDismissedKey(triggerHit?.tokenKey ?? null)
                  return
                }
              }
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault()
                void submit()
              }
            }}
          />
          <div className="composer-toolbar">
            <div className="toolbar-left">
              <button id="composer-add" className="composer-add" type="button" aria-label="添加附件" title="添加附件"><svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M8 3.2v9.6M3.2 8h9.6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg></button>
              <MenuSelect
                id="permission-select"
                title="权限模式"
                value={String(runtimeConfig.permission_mode || "workspace_write")}
                options={[
                  { value: "read_only", label: "只读" },
                  { value: "ask", label: "询问" },
                  { value: "workspace_write", label: "工作区写入" },
                  { value: "plan", label: "计划" },
                  { value: "bypass_permissions", label: "Full access" },
                ]}
                leadingIcon={<svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M8 1.5 13.5 3.5V7.5C13.5 11 11.2 13.6 8 14.5C4.8 13.6 2.5 11 2.5 7.5V3.5L8 1.5Z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/></svg>}
                onChange={(value) => {
                  void api("/api/runtime/config", { method: "PATCH", body: JSON.stringify({ permission_mode: value }) }).then(reloadShell).catch(() => {})
                }}
              />
              <MenuSelect
                id="sandbox-select"
                title="文件沙箱"
                value={String(runtimeConfig.sandbox_mode || "read_only")}
                options={[
                  { value: "read_only", label: "只读沙箱" },
                  { value: "workspace_write", label: "工作区沙箱" },
                  { value: "danger_full_access", label: "全放开沙箱" },
                ]}
                leadingIcon={<svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true"><rect x="2.5" y="6.5" width="11" height="7" rx="1.5" stroke="currentColor" strokeWidth="1.3"/><path d="M5.5 6.5V4.5a2.5 2.5 0 0 1 5 0v2" stroke="currentColor" strokeWidth="1.3"/></svg>}
                onChange={(value) => {
                  void api("/api/runtime/config", { method: "PATCH", body: JSON.stringify({ sandbox_mode: value }) }).then(reloadShell).catch(() => {})
                }}
              />
            </div>
            <div className="toolbar-right">
              {streamState ? <span id="stream-state" className="stream-state" aria-live="polite">{streamState}</span> : null}
              <MenuSelect
                id="model-select"
                title="模型"
                value={modelSelectValue}
                options={[{ value: modelSelectValue, label: model }]}
                groups={(Array.isArray(runtimeConfig.model_groups) ? runtimeConfig.model_groups : [])
                  .filter((g: any) => Array.isArray(g.models) && g.models.length > 0)
                  .map((g: any) => ({
                    id: String(g.id || ""),
                    label: String(g.label || g.id || ""),
                    // dsh 语义：同模型跨组是不同端点，不去重——都列出
                    options: (g.models as { id: string; name?: string | null }[]).map((m) => ({
                      value: `${String(g.id || "")}::${String(m.id)}`,
                      label: String(m.name || m.id),
                       hint: String(m.id),
                    })),
                  }))}
                onChange={(value) => {
                  // 选中即启用该模型所属的供应商组（dsh groups 语义）
                  const separator = value.indexOf("::")
                   const groupId = separator >= 0 ? value.slice(0, separator) : ""
                   const selectedModel = separator >= 0 ? value.slice(separator + 2) : value
                   

                  const patch: Record<string, unknown> = { provider_model: selectedModel }
                  if (groupId && groupId !== activeProviderId) {
                    patch.active_provider_id = groupId
                  }
                  void api("/api/runtime/config", { method: "PATCH", body: JSON.stringify(patch) }).then(reloadShell).catch(() => {})
                }}
              />
              {hasContent ? <button id="regenerate-button" className="round-button ghost" type="button" aria-label="重新生成" title="重新生成最后一轮" onClick={() => void regenerate()}><svg width="15" height="15" viewBox="0 0 15 15" fill="none" aria-hidden="true"><path d="M12.2 6.2A5 5 0 1 0 12.5 9" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/><path d="M12.6 2.8v3.6H9" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/></svg></button> : null}
              {running ? (
                <button id="stop-button" className="round-button stop" type="button" aria-label="停止" onClick={cancel}><svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><rect x="3" y="3" width="8" height="8" rx="1.5" fill="currentColor"/></svg></button>
              ) : (
                <button id="send-button" className="round-button send" type="submit" aria-label="发送" disabled={!draft.trim()}><svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M8 12.5V3.5M3.8 7.7L8 3.5l4.2 4.2" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/></svg></button>
              )}
            </div>
          </div>
        </form>
        <StatsLine sessionId={selectedId} />
      </main>
      <CommentPanel
        open={inline.panelOpen && !!selectedId}
        threads={inline.threads}
        activeAnchorId={inline.activeAnchorId}
        streamingMap={inline.streamingMap}
        busyAnchors={inline.busyAnchors}
        onClose={() => { inline.setPanelOpen(false); inline.setActiveAnchorId(null) }}
        onAsk={inline.followUp}
        onDelete={(aid) => void inline.removeThread(aid)}
        onFocusAnchor={inline.focusAnchor}
      />
      {inline.toolbar ? (
        <SelectionToolbar
          state={inline.toolbar}
          asking={inline.asking}
          onExplain={inline.explain}
          onAsk={inline.ask}
          onClose={() => { inline.setToolbarClosed?.(); }}
        />
      ) : null}
    </div>
  )
}
