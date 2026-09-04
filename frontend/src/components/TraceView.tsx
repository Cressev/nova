import { useEffect, useMemo, useRef, useState } from "react"
import type { ChatMessage, TraceEvent } from "../types"
import { api } from "../lib/api"
import { Markdown } from "./Markdown"

/* ============================================================================
   轨迹视图 —— 对齐 dsh ui-trajectory（交互级审计 2026-09-04）：
   ① 工具条：Duration/实际时间(switch)/⊟Turns/⊟Calls 折叠开关 + 搜索（索引高亮，不过滤）；
   ② 三泳道 tile 时间线（Input 绿/Model 紫/Tools 橙/错误红）；
   ③ 账本：Turn 分组（组头 Turn N + 展开行），行点击 → 右侧选中 +
     详情面板（381px aside：TOOL=Summary/Payload/Result/Schema/Timing；
     消息=Summary/Preview/Raw；× 关闭）；
   ④ composer 常驻（由 App 骨架管理，本组件不渲染）。
   审计事实源：findings/2026-09-04-dsh-ui-alignment-audit.md
   ========================================================================== */

interface FusedRecord {
  key: string
  kind: "user" | "message" | "tool" | "system"
  text: string
  tool: string | null
  args: Record<string, unknown>
  output: string | null
  status: string | null
  iso: string
  ts: number
  turn: number
  step: number
  failed: boolean
  durationMs: number | null
}

interface TurnGroup {
  turn: number
  startedAt: number
  records: FusedRecord[]
}

const KIND_BADGE: Record<FusedRecord["kind"], string> = {
  user: "USER",
  message: "ASSISTANT",
  tool: "TOOL",
  system: "SYSTEM",
}

function monospaceToolText(tool: string, args: Record<string, unknown>, output: string | null): string {
  const keys = Object.keys(args || {})
  const argsPreview = keys.length > 0
    ? keys.slice(0, 3).map((k) => `${k}:${String(args[k]).slice(0, 40)}`).join(", ")
    : ""
  const out = output && output.trim()
    ? output.split("\n").filter(Boolean).slice(-1)[0]?.slice(0, 60) ?? ""
    : ""
  const head = argsPreview ? `${tool} ${argsPreview}` : tool
  return out ? `${head} → ${out}` : head
}

function foldRecords(messages: ChatMessage[], events: TraceEvent[]): { turns: TurnGroup[]; totalMs: number } {
  const seeds: Array<{ ts: number; run: () => void }> = []
  for (const m of messages) {
    const ts = m.created_at ? Date.parse(m.created_at) : NaN
    if (Number.isNaN(ts)) continue
    seeds.push({
      ts,
      run: () => {
        if (m.role === "user") {
          // 用户消息开启新 Turn（dsh 语义），且自身是 Turn 的首行
          openTurn()
          push({ kind: "user", text: m.content || "", tool: null, args: {}, output: null, status: "ok", iso: m.created_at || "" })
        } else if (m.role === "assistant") {
          const text = (m.content || "").trim()
          push({ kind: "message", text: text === "" ? "(tool call only)" : text, tool: null, args: {}, output: m.content || "", status: "ok", iso: m.created_at || "" })
        }
      },
    })
  }
  for (const e of events) {
    const ts = e.created_at ? Date.parse(e.created_at) : NaN
    if (Number.isNaN(ts)) continue
    seeds.push({
      ts,
      run: () => {
        const type = String(e.event_type || "")
        // 轮次/状态/钩子类事件不产生账本行（审计 §4.4）
        if (type.startsWith("turn.") || type === "status" || type === "memory.compacted" || type.startsWith("hook.")) return
        if (type === "permission.requested") {
          push({ kind: "system", text: `permission · ${e.tool || "工具"} 待确认`, tool: e.tool || null, args: (e.arguments || {}) as Record<string, unknown>, output: null, status: "pending", iso: e.created_at })
          return
        }
        if (type === "user.question") {
          push({ kind: "system", text: "ask · 等待用户回答", tool: "ask_user_question", args: {}, output: null, status: "pending", iso: e.created_at })
          return
        }
        if (type.startsWith("tool.")) {
          const durationMs = typeof e.duration_ms === "number" ? e.duration_ms : null
          const args = (e.arguments || {}) as Record<string, unknown>
          const output = typeof e.output === "string" ? e.output : null
          push({
            kind: "tool",
            text: monospaceToolText(String(e.tool || "tool"), args, output),
            tool: String(e.tool || "tool"),
            args,
            output,
            status: e.status || "ok",
            iso: e.created_at,
            durationMs,
          })
        }
      },
    })
  }
  seeds.sort((a, b) => a.ts - b.ts)

  const turnsAll: TurnGroup[] = []
  let current: TurnGroup | null = null
  let seq = 0
  let step = 0
  function openTurn() {
    current = { turn: turnsAll.length + 1, startedAt: NaN, records: [] }
    turnsAll.push(current)
    step = 0
  }
  function push(partial: Omit<FusedRecord, "key" | "turn" | "step" | "ts" | "failed" | "durationMs" | "kind" | "text"> & Partial<Pick<FusedRecord, "durationMs">> & Pick<FusedRecord, "kind" | "text">) {
    if (!current) openTurn()
    step += 1
    const failed = partial.status === "failed" || partial.status === "error"
    const record: FusedRecord = {
      ...(partial as Omit<FusedRecord, "key" | "turn" | "step" | "ts" | "failed">),
      durationMs: partial.durationMs ?? null,
      key: `r-${seq++}`,
      turn: current!.turn,
      step,
      ts: Date.parse(partial.iso) || 0,
      failed,
    }
    current!.records.push(record)
    if (Number.isNaN(current!.startedAt)) current!.startedAt = record.ts
  }
  for (const s of seeds) s.run()
  const turns = turnsAll.filter((t) => t.records.length > 0)
  const all = turns.flatMap((t) => t.records)
  // dsh：Timing source = session timestamps —— 缺 duration_ms 的工具行
  // 用「下一事件时间 − 自身时间」推导（封顶 10 分钟，防长闲置虚高）
  for (let i = 0; i < all.length; i += 1) {
    const rec = all[i]
    if (rec.kind === "tool" && rec.durationMs === null && i + 1 < all.length) {
      const gap = all[i + 1].ts - rec.ts
      rec.durationMs = Math.max(0, Math.min(gap, 600_000))
    }
  }
  const totalMs = all.length >= 2 ? all[all.length - 1].ts - all[0].ts : 0
  return { turns, totalMs }
}

/* ---- 泳道 tile 时间线（dsh：三泳道 + 语义色 + 网格底）---- */
function TimelineLanes({ records, turns }: { records: FusedRecord[]; turns: TurnGroup[] }) {
  const BINS = 48
  const start = records.length ? records[0].ts : 0
  const span = Math.max(1, (records.length ? records[records.length - 1].ts : 1) - start)
  const lanes: Array<Array<{ color: string; count: number }>> = [
    Array.from({ length: BINS }, () => ({ color: "", count: 0 })),
    Array.from({ length: BINS }, () => ({ color: "", count: 0 })),
    Array.from({ length: BINS }, () => ({ color: "", count: 0 })),
  ]
  for (const record of records) {
    const lane = record.kind === "user" ? 0 : record.kind === "message" ? 1 : 2
    const bin = Math.min(BINS - 1, Math.floor(((record.ts - start) / span) * BINS))
    const cell = lanes[lane][bin]
    cell.count += 1
    if (record.failed) cell.color = "#e5484d"
    else if (lane === 0) cell.color = cell.color || "#22c55e"
    else if (lane === 1) cell.color = cell.color || "#a78bfa"
    else cell.color = cell.color || "#f59e0b"
  }
  return (
    <div className="trajectory-timeline" aria-label="轨迹时间线">
      <div className="trajectory-lane-labels" aria-hidden="true">
        <span>Input</span>
        <span>Model</span>
        <span>Tools</span>
      </div>
      <div className="trajectory-lanes">
        {lanes.map((lane, i) => (
          <div className="trajectory-lane" key={i}>
            {lane.map((cell, bin) => (
              <span
                key={bin}
                className="trajectory-tile"
                data-filled={cell.count > 0 || undefined}
                style={cell.color ? { background: cell.color } : undefined}
              />
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}

/* ---- 详情面板（dsh：381px aside，随行类型换 tab 集）---- */
type DetailTab = { id: string; label: string }

function tabsForRecord(record: FusedRecord): DetailTab[] {
  if (record.kind === "tool") {
    return [
      { id: "overview", label: "Summary" },
      { id: "payload", label: "Payload" },
      { id: "output", label: "Result" },
      { id: "schema", label: "Schema" },
      { id: "timing", label: "Timing" },
    ]
  }
  if (record.kind === "message" || record.kind === "user") {
    return [
      { id: "overview", label: "Summary" },
      { id: "rendered", label: "Preview" },
      { id: "raw", label: "Raw" },
    ]
  }
  return [{ id: "overview", label: "Summary" }]
}

function fmtDuration(ms: number | null): string {
  if (ms === null) return "—"
  if (ms < 1000) return `${Math.round(ms)} ms`
  return `${(ms / 1000).toFixed(1)} s`
}

function fmtStarted(iso: string): string {
  if (!iso) return "—"
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString("zh-CN", { hour12: false })
}

function DetailPanel({ record, onClose }: { record: FusedRecord; onClose: () => void }) {
  const tabs = tabsForRecord(record)
  const [active, setActive] = useState("overview")
  const [schema, setSchema] = useState<string>("加载中…")
  useEffect(() => setActive("overview"), [record.key])
  useEffect(() => {
    if (record.kind !== "tool" || !record.tool) return
    let alive = true
    api<{ tools?: Array<{ name: string; description?: string; input_schema?: unknown; parameters?: unknown }> }>("/api/tools")
      .then((data) => {
        if (!alive) return
        const spec = (data.tools || []).find((t) => t.name === record.tool)
        if (!spec) { setSchema("未找到该工具的 Schema"); return }
        const sch = spec.input_schema ?? spec.parameters ?? null
        setSchema([spec.description || "", "", JSON.stringify(sch ?? {}, null, 2)].join("\n").trim())
      })
      .catch(() => { if (alive) setSchema("Schema 加载失败") })
    return () => { alive = false }
  }, [record.tool, record.kind])

  return (
    <aside className="trajectory-details" aria-label="轨迹详情">
      <div className="trajectory-details-head">
        <span className={`trajectory-badge badge-${record.kind}`}>{KIND_BADGE[record.kind]}</span>
        <span className="trajectory-details-title">Turn {record.turn} · Step {record.step}</span>
        <button type="button" className="trajectory-details-close" aria-label="关闭详情" onClick={onClose}>×</button>
      </div>
      <div className="trajectory-details-tabs" role="tablist" aria-label="Event details">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={active === tab.id}
            className={`trajectory-details-tab${active === tab.id ? " active" : ""}`}
            onClick={() => setActive(tab.id)}
          >{tab.label}</button>
        ))}
      </div>
      <div className="trajectory-details-body" role="tabpanel">
        {active === "overview" ? (
          <dl className="trajectory-overview">
            <div><dt>Status</dt><dd className={record.failed ? "err" : undefined}>{record.failed ? "Error" : record.status === "pending" ? "Pending" : "Completed"}</dd></div>
            <div><dt>Started</dt><dd>{fmtStarted(record.iso)}</dd></div>
            <div><dt>Duration</dt><dd>{fmtDuration(record.durationMs)}</dd></div>
            <div><dt>Timing source</dt><dd>Session timestamps</dd></div>
            <div><dt>Hierarchy</dt><dd>Turn {record.turn} · Step {record.step}</dd></div>
          </dl>
        ) : null}
        {active === "payload" ? <pre className="trajectory-pre">{JSON.stringify(record.args ?? {}, null, 2)}</pre> : null}
        {active === "output" ? <pre className="trajectory-pre">{record.output?.trim() || "(no output)"}</pre> : null}
        {active === "schema" ? <pre className="trajectory-pre">{schema}</pre> : null}
        {active === "timing" ? (
          <dl className="trajectory-overview">
            <div><dt>Step</dt><dd>{record.step}</dd></div>
            <div><dt>Duration</dt><dd>{fmtDuration(record.durationMs)}</dd></div>
            <div><dt>Timing source</dt><dd>Session timestamps</dd></div>
          </dl>
        ) : null}
        {active === "rendered" ? <div className="trajectory-markdown"><Markdown content={record.output ?? record.text} /></div> : null}
        {active === "raw" ? <pre className="trajectory-pre">{record.output ?? record.text}</pre> : null}
      </div>
    </aside>
  )
}

/* ---- 主视图 ---- */
export function TraceView({ sessionId }: { sessionId: string }) {
  const [turns, setTurns] = useState<TurnGroup[] | null>(null)
  const [error, setError] = useState("")
  const [query, setQuery] = useState("")
  const [durationMode, setDurationMode] = useState(false)
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set())
  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  const reload = useRef(0)

  useEffect(() => {
    const gen = ++reload.current
    setTurns(null)
    setError("")
    setSelectedKey(null)
    Promise.all([
      api<ChatMessage[] | { messages?: ChatMessage[] }>(`/api/chat/sessions/${encodeURIComponent(sessionId)}/messages`),
      api<{ items?: TraceEvent[] }>(`/api/chat/sessions/${encodeURIComponent(sessionId)}/trace`),
    ])
      .then(([msgData, traceData]) => {
        if (gen !== reload.current) return
        const messages = Array.isArray(msgData) ? msgData : (msgData.messages || [])
        const folded = foldRecords(messages, traceData.items || [])
        setTurns(folded.turns)
      })
      .catch((e: unknown) => { if (gen === reload.current) setError(String(e instanceof Error ? e.message : e)) })
  }, [sessionId])

  const q = query.trim().toLowerCase()
  const matchCount = useMemo(() => {
    if (!turns || !q) return 0
    return turns.flatMap((t) => t.records).filter((r) => r.text.toLowerCase().includes(q) || (r.tool || "").toLowerCase().includes(q)).length
  }, [turns, q])

  const selected = useMemo(() => {
    if (!turns || !selectedKey) return null
    return turns.flatMap((t) => t.records).find((r) => r.key === selectedKey) ?? null
  }, [turns, selectedKey])

  if (error) return <div className="trace-view"><p className="trace-loading">轨迹加载失败：{error}</p></div>
  if (!turns) return <div className="trace-view"><p className="trace-loading">加载中…</p></div>

  // ⊟Turns/⊟Calls = 全部分组折叠开关（审计 §4.2：26 行→11 行，剩组头+摘要）
  const allCollapsed = turns.length > 0 && turns.every((t) => collapsed.has(t.turn))
  const toggleCollapseAll = () => {
    setCollapsed(allCollapsed ? new Set() : new Set(turns.map((t) => t.turn)))
  }

  return (
    <div className="trace-view trajectory-view">
      <div className="trajectory-toolbar" role="toolbar" aria-label="轨迹工具条">
        <div className="trajectory-modes">
          <button type="button" className={`trajectory-mode${durationMode ? " active" : ""}`} onClick={() => setDurationMode((v) => !v)}>Duration</button>
          <button
            type="button"
            role="switch"
            aria-checked={durationMode}
            className="trajectory-switch"
            onClick={() => setDurationMode((v) => !v)}
          ><span aria-hidden="true" />实际时间</button>
          <button type="button" className={`trajectory-mode${allCollapsed ? " active" : ""}`} onClick={toggleCollapseAll}>⊟Turns</button>
          <button type="button" className={`trajectory-mode${allCollapsed ? " active" : ""}`} onClick={toggleCollapseAll}>⊟Calls</button>
        </div>
        <div className="trajectory-searchbox">
          <input
            className="trajectory-search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索"
            aria-label="搜索轨迹"
          />
          {q ? <span className="trajectory-search-count">{matchCount} 处匹配</span> : null}
        </div>
      </div>
      {!durationMode ? <TimelineLanes records={turns.flatMap((t) => t.records)} turns={turns} /> : null}
      <div className="trajectory-ledger-wrap">
        <div className="trajectory-ledger" role="grid" aria-label="轨迹账本">
          {turns.length === 0 ? <p className="trace-loading">无记录</p> : null}
          {turns.map((turn) => {
            const isCollapsed = collapsed.has(turn.turn)
            const steps = turn.records.length
            const calls = turn.records.filter((r) => r.kind === "tool").length
            return (
              <div className="trajectory-turn" key={turn.turn}>
                <button
                  type="button"
                  className="trajectory-turn-header"
                  aria-expanded={!isCollapsed}
                  onClick={() => {
                    setCollapsed((prev) => {
                      const next = new Set(prev)
                      if (next.has(turn.turn)) next.delete(turn.turn)
                      else next.add(turn.turn)
                      return next
                    })
                  }}
                >
                  <span className="trajectory-turn-chevron" aria-hidden="true">{isCollapsed ? "▸" : "▾"}</span>
                  <span className="trajectory-turn-label">Turn {turn.turn}</span>
                  <span className="trajectory-turn-index">#{turn.turn}</span>
                  {turn.records[0]?.kind === "user" ? (
                    <span className="trajectory-turn-lead">{turn.records[0].text.slice(0, 48)}</span>
                  ) : null}
                  {isCollapsed
                    ? <span className="trajectory-turn-subtotal">… {steps} steps · {calls} tool calls</span>
                    : <span className="trajectory-turn-meta">{steps} 条</span>}
                </button>
                {!isCollapsed ? turn.records.map((record) => {
                  // 搜索=高亮匹配行而非过滤（审计 §4.8）
                  const matched = q !== "" && (record.text.toLowerCase().includes(q) || (record.tool || "").toLowerCase().includes(q))
                  return (
                    <div
                      key={record.key}
                      role="row"
                      tabIndex={0}
                      className={`trajectory-row kind-${record.kind}${record.failed ? " failed" : ""}${record.key === selectedKey ? " selected" : ""}${matched ? " matched" : ""}`}
                      onClick={() => setSelectedKey(record.key === selectedKey ? null : record.key)}
                      onKeyDown={(e) => { if (e.key === "Enter") setSelectedKey(record.key === selectedKey ? null : record.key) }}
                    >
                      <span className={`trajectory-badge badge-${record.kind}`}>{KIND_BADGE[record.kind]}</span>
                      <span className={`trajectory-text${record.kind === "tool" ? " mono" : ""}`}>{record.text}</span>
                      {record.durationMs !== null && record.durationMs > 0 ? (
                        <span className="trajectory-row-time">{(record.durationMs / 1000).toFixed(1)}s</span>
                      ) : null}
                    </div>
                  )
                }) : null}
              </div>
            )
          })}
        </div>
        {selected ? <DetailPanel record={selected} onClose={() => setSelectedKey(null)} /> : null}
      </div>
    </div>
  )
}
