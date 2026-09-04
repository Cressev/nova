import { useEffect, useMemo, useRef, useState } from "react"
import type { ChatMessage, ChatSession, PendingApprovalItem, RuntimeConfig, ToolCallData, TraceEvent } from "./types"
import { api, cx, formatTime, projectName, relativeTime, shortText, workspaceGroupKey } from "./lib/api"
import { Markdown, CopyButton } from "./components/Markdown"
import { ToolEventRow, deriveToolSummary, type ToolEventView } from "./components/ToolEvent"
import { PermissionCard, QuestionCard } from "./components/Takeover"
import { TraceView } from "./components/TraceView"

/* ============================================================================
   Nova App —— React 版（对齐 dsh ui-conversation 的组件分区）。
   数据流：App 持全部会话态；ConversationView 渲染消息时间线；
   工具/审批/提问事件以内联条目插入时间线（按 sequence 排序）；
   takeovers（待审批/待提问）停靠在 composer 上方 dock。
   ========================================================================== */

type TimelineEntry =
  | { kind: "message"; key: string; message: ChatMessage }
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

function MessageView({ message }: { message: ChatMessage }) {
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
function Sidebar({ sessions, selectedId, currentWorkspace, version, onSelect, onDelete, onNewChat }: {
  sessions: ChatSession[]
  selectedId: string | null
  currentWorkspace: string
  version: string
  onSelect: (session: ChatSession) => void
  onDelete: (id: string) => void
  onNewChat: () => void
}) {
  const [query, setQuery] = useState("")
  const [searchOpen, setSearchOpen] = useState(false)
  const groups = useMemo(() => {
    const map = new Map<string, ChatSession[]>()
    for (const session of sessions) {
      if (query && !String(session.title || "").toLowerCase().includes(query.toLowerCase())) continue
      const key = workspaceGroupKey(session.workspace)
      if (!map.has(key)) map.set(key, [])
      map.get(key)!.push(session)
    }
    return [...map.entries()].map(([key, list]) => ({
      key,
      name: key === "__ungrouped__" ? "未分组" : projectName(list[0]?.workspace || key),
      sessions: list,
    }))
  }, [sessions, query])

  return (
    <aside className="sidebar">
      <div className="brand-row">
        <span className="brand-mark" aria-hidden="true">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 2.5 14.6 9 21.5 11.5 14.6 14 12 20.5 9.4 14 2.5 11.5 9.4 9 12 2.5Z" fill="currentColor"/></svg>
        </span>
        <strong className="brand-name">Nova</strong>
        <span className="version-badge" id="nova-version">{version}</span>
        <button className="sidebar-collapse" type="button" aria-label="折叠侧栏" title="折叠侧栏">
          <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true"><path d="M11.2 4.4 6.6 9l4.6 4.6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
        </button>
      </div>
      <button className="new-session" type="button" onClick={onNewChat}>
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true"><path d="M7 2v10M2 7h10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>
        <span>新会话</span>
      </button>
      <div className="sidebar-group sidebar-sessions">
        <div className="group-label">
          <span>工作区</span>
          <div className="group-actions">
            <button className="icon-ghost" type="button" aria-label="搜索会话" title="搜索会话" onClick={() => setSearchOpen((v) => !v)}>
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true"><circle cx="6.4" cy="6.4" r="4.4" stroke="currentColor" strokeWidth="1.4"/><path d="m9.8 9.8 2.9 2.9" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/></svg>
            </button>
          </div>
        </div>
        {searchOpen ? (
          <input
            className="session-search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索会话…"
          />
        ) : null}
        <nav id="session-list" className="session-list">
          {groups.map((group) => (
            <section className="session-group" key={group.key}>
              <button className={cx("session-group-head", group.sessions.some((s) => s.id === selectedId) ? "active" : "")} type="button" aria-expanded>
                <svg className="folder-icon-svg" width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M1.8 4.2A1.7 1.7 0 0 1 3.5 2.5h2.6l1.4 1.7h5A1.7 1.7 0 0 1 14.2 6v5.8a1.7 1.7 0 0 1-1.7 1.7H3.5a1.7 1.7 0 0 1-1.7-1.7V4.2Z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/></svg>
                <strong>{group.name}</strong>
              </button>
              <div className="session-group-items">
                {group.sessions.map((session) => (
                  <button
                    key={session.id}
                    className={cx("session-item", session.id === selectedId ? "active" : "")}
                    type="button"
                    onClick={() => onSelect(session)}
                  >
                    <span className="session-dot" aria-hidden="true" />
                    <strong>{shortText(session.title || "新会话", 28)}</strong>
                    <span className="session-time">{relativeTime(session.updated_at || session.created_at)}</span>
                    <span
                      className="session-delete"
                      role="button"
                      tabIndex={-1}
                      aria-label="删除对话"
                      onClick={(e) => {
                        e.stopPropagation()
                        onDelete(session.id)
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.stopPropagation()
                          onDelete(session.id)
                        }
                      }}
                    >×</span>
                  </button>
                ))}
              </div>
            </section>
          ))}
        </nav>
      </div>
      <div className="sidebar-foot">
        <button className="sidebar-foot-button" type="button" id="open-settings">
          <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M6.8 1.8h2.4l.4 1.7 1.5.9 1.6-.7 1.2 2.1-1.2 1.2v1.7l1.2 1.2-1.2 2.1-1.6-.7-1.5.9-.4 1.7H6.8l-.4-1.7-1.5-.9-1.6.7-1.2-2.1 1.2-1.2V8.2L2.1 7l1.2-2.1 1.6.7 1.5-.9.4-1.7Z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round"/><circle cx="8" cy="8" r="2.1" stroke="currentColor" strokeWidth="1.2"/></svg>
          <span>设置</span>
        </button>
      </div>
    </aside>
  )
}

/* ---- 头部 ---- */
function ChatHeader({ title, modeLabel, bgTasks, activeTab, onTab, onSessionLog }: {
  title: string
  modeLabel: string
  bgTasks: number
  activeTab: "chat" | "trace"
  onTab: (tab: "chat" | "trace") => void
  onSessionLog: () => void
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
        }
        if (openAt !== null) spans.push(Date.now() - openAt)
        if (turns === 0) { setText(""); return }
        const wallMs = spans.reduce((a, b) => a + b, 0)
        const llmMs = Math.max(0, wallMs - toolMs)
        const fmt = (ms: number) => {
          const sec = Math.round(ms / 1000)
          return sec < 60 ? `${sec}s` : `${Math.floor(sec / 60)}m${String(sec % 60).padStart(2, "0")}s`
        }
        setText(`${turns} 轮 · ${steps} 步 | LLM ${fmt(llmMs)} · 工具调用 ${fmt(toolMs)} | 平均每轮 ${fmt(wallMs / turns)}`)
      })
      .catch(() => { if (alive) setText("") })
    return () => { alive = false }
  }, [sessionId])
  if (!text) return null
  return <div className="stats-line" id="stats-line">{text}</div>
}

/* ---- 主时间线 ---- */
function ConversationView({ entries, streamingText }: { entries: TimelineEntry[]; streamingText: string | null }) {
  const bottomRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" })
  }, [entries.length, streamingText])
  return (
    <div className="messages" id="messages">
      {entries.map((entry) => {
        if (entry.kind === "message") return <MessageView key={entry.key} message={entry.message} />
        if (entry.kind === "checkpoint") return <CheckpointView key={entry.key} message={entry.message} />
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
    </div>
  )
}

/* ---- App 根 ---- */
export default function App() {
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [entries, setEntries] = useState<TimelineEntry[]>([])
  const [takeovers, setTakeovers] = useState<PendingApprovalItem[]>([])
  const [runtimeConfig, setRuntimeConfig] = useState<RuntimeConfig>({})
  const [workspace, setWorkspace] = useState("")
  const [version, setVersion] = useState("")
  const [streamState, setStreamState] = useState("")
  const [streamingText, setStreamingText] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<"chat" | "trace">("chat")
  const [draft, setDraft] = useState("")
  const [running, setRunning] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)

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

  const selectSession = async (session: ChatSession) => {
    setSelectedId(session.id)
    setActiveTab("chat")
    try {
      await loadTimeline(session.id)
    } catch { /* 选择失败保持空 */ }
  }

  const newChat = async () => {
    const session = await api<ChatSession>("/api/chat/sessions", { method: "POST", body: JSON.stringify({ title: "新线程" }) })
    setSelectedId(session.id)
    setEntries([])
    setTakeovers([])
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
      const session = await api<ChatSession>("/api/chat/sessions", { method: "POST", body: JSON.stringify({ title: "新对话" }) })
      sessionId = session.id
      setSelectedId(sessionId)
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
        if (type === "assistant_delta") {
          assistantText += String(event.delta || "")
          setStreamingText(assistantText)
          setStreamState("Nova 正在输出")
        } else if (type === "tool_start") {
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
  const model = String(runtimeConfig.model || "glm-4.7")
  const modelOptions = useMemo(() => {
    const list = Array.isArray(runtimeConfig.models) ? runtimeConfig.models.map(String) : []
    return list.includes(model) ? list : [model, ...list]
  }, [runtimeConfig.models, model])
  const selectedSession = sessions.find((s) => s.id === selectedId)

  return (
    <div className="app-shell" data-main-state={mainState}>
      <Sidebar
        sessions={sessions}
        selectedId={selectedId}
        currentWorkspace={workspace}
        version={version}
        onSelect={selectSession}
        onDelete={deleteSession}
        onNewChat={newChat}
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
              <button className="hero-chip" type="button" title="切换工作区">
                <svg className="hero-selector-icon" width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M1.8 4.2A1.7 1.7 0 0 1 3.5 2.5h2.6l1.4 1.7h5A1.7 1.7 0 0 1 14.2 6v5.8a1.7 1.7 0 0 1-1.7 1.7H3.5a1.7 1.7 0 0 1-1.7-1.7V4.2Z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/></svg>
                <span>{projectName(workspace) || "选择工作区"}</span>
                <span className="hero-selector-chevron" aria-hidden="true">▾</span>
              </button>
              <button className="hero-chip" type="button" title="切换模式">
                <svg className="hero-selector-icon" width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M8 1.5 13.5 3.5V7.5C13.5 11 11.2 13.6 8 14.5C4.8 13.6 2.5 11 2.5 7.5V3.5L8 1.5Z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/></svg>
                <span>{modeLabel}</span>
                <span className="hero-selector-chevron" aria-hidden="true">▾</span>
              </button>
            </div>
          </div>
        ) : (
          <ConversationView entries={entries} streamingText={streamingText} />
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
          onSubmit={(e) => {
            e.preventDefault()
            void submit()
          }}
        >
          <textarea
            id="message-input"
            ref={textareaRef}
            rows={1}
            placeholder="给智能体发消息"
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value)
              const el = e.target
              el.style.height = "auto"
              el.style.height = `${el.scrollHeight}px`
            }}
            onKeyDown={(e) => {
              // isComposing：中文输入法组字中的 Enter 是确认候选词，不是发送（dsh 同样拦截）。
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault()
                void submit()
              }
            }}
          />
          <div className="composer-toolbar">
            <div className="toolbar-left">
              <button id="composer-add" className="composer-add" type="button" aria-label="添加附件" title="添加附件">＋</button>
              <label className="toolbar-select permission-select" title="权限模式">
                <svg className="select-icon shield-icon" width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M8 1.5 13.5 3.5V7.5C13.5 11 11.2 13.6 8 14.5C4.8 13.6 2.5 11 2.5 7.5V3.5L8 1.5Z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/></svg>
                <span className="select-value">{PERMISSION_VALUE_LABELS[String(runtimeConfig.permission_mode || "workspace_write")] || "工作区写入"} <span aria-hidden="true" className="select-chevron">▾</span></span>
                <select
                  id="permission-select"
                  value={String(runtimeConfig.permission_mode || "workspace_write")}
                  onChange={(e) => {
                    const value = e.target.value
                    void api("/api/runtime/config", { method: "POST", body: JSON.stringify({ permission_mode: value }) }).then(reloadShell).catch(() => {})
                  }}
                >
                  <option value="read_only">只读</option>
                  <option value="ask">询问</option>
                  <option value="workspace_write">标准模式（工作区写入）</option>
                  <option value="plan">计划</option>
                  <option value="bypass_permissions">完全访问</option>
                </select>
              </label>
            </div>
            <div className="toolbar-right">
              {streamState ? <span id="stream-state" className="stream-state" aria-live="polite">{streamState}</span> : null}
              <label className="toolbar-select model-select" title="模型">
                <span className="model-select-label">{model} <span aria-hidden="true" className="select-chevron">▾</span></span>
                <select
                  id="model-select"
                  value={model}
                  onChange={(e) => {
                    const value = e.target.value
                    void api("/api/runtime/config", { method: "POST", body: JSON.stringify({ model: value }) }).then(reloadShell).catch(() => {})
                  }}
                >
                  {modelOptions.map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              </label>
              {hasContent ? <button id="regenerate-button" className="round-button ghost" type="button" aria-label="重新生成" title="重新生成最后一轮" onClick={() => void regenerate()}><svg width="15" height="15" viewBox="0 0 15 15" fill="none" aria-hidden="true"><path d="M12.2 6.2A5 5 0 1 0 12.5 9" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/><path d="M12.6 2.8v3.6H9" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/></svg></button> : null}
              {running ? (
                <button id="stop-button" className="round-button stop" type="button" aria-label="停止" onClick={cancel}>■</button>
              ) : (
                <button id="send-button" className="round-button send" type="submit" aria-label="发送" disabled={!draft.trim()}>↑</button>
              )}
            </div>
          </div>
        </form>
        <StatsLine sessionId={selectedId} />
      </main>
    </div>
  )
}
