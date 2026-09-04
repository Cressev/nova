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
  // 工具事件按 id 去重合并：started+completed 是同一次调用的两半，
  // 只渲染一行（completed 携带 output/status/updated_at；started 行丢弃）
  const toolEventById = new Map<string, TraceEvent>()
  for (const e of events) {
    const type = String(e.event_type || "")
    if (!type.startsWith("tool.")) continue
    const id = String((e as { id?: string }).id || "")
    if (!id) continue
    const prev = toolEventById.get(id)
    if (!prev || type === "tool.completed" || (prev.event_type === "tool.started" && type === "tool.failed")) {
      toolEventById.set(id, e)
    }
  }
  for (const e of events) {
    const ts = e.created_at ? Date.parse(e.created_at) : NaN
    if (Number.isNaN(ts)) continue
    seeds.push({
      ts,
      run: () => {
        const type = String(e.event_type || "")
        // 轮次/状态/钩子类事件不产生账本行（审计 §4.4）
        if (type.startsWith("turn.") || type === "status" || type === "memory.compacted" || type.startsWith("hook.") || type.startsWith("agent.")) return
        if (type === "reasoning.completed") {
          push({ kind: "system", text: `Think · ${String(e.message || "").split("\n")[0].slice(0, 60)}`, tool: null, args: {}, output: String(e.message || ""), status: "ok", iso: e.created_at })
          return
        }
        if (type === "permission.requested") {
          push({ kind: "system", text: `permission · ${e.tool || "工具"} 待确认`, tool: e.tool || null, args: (e.arguments || {}) as Record<string, unknown>, output: null, status: "pending", iso: e.created_at })
          return
        }
        if (type === "user.question") {
          push({ kind: "system", text: "ask · 等待用户回答", tool: "ask_user_question", args: {}, output: null, status: "pending", iso: e.created_at })
          return
        }
        if (type.startsWith("tool.")) {
          // 同一调用只取合并后的那一行；started 事件不渲染
          if (toolEventById.get(String((e as { id?: string }).id || "")) !== e) return
          const args = (e.arguments || {}) as Record<string, unknown>
          const output = typeof e.output === "string" ? e.output : null
          const updated = ((e as { updated_at?: string }).updated_at) || e.created_at
          push({
            kind: "tool",
            text: monospaceToolText(String(e.tool || "tool"), args, output),
            tool: String(e.tool || "tool"),
            args,
            output,
            status: e.status || "ok",
            iso: updated,
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

/* ---- 泳道 tile 时间线（dsh TrajectoryTimeline 对齐：可缩放/拖选/平移/点击）----
   滚轮=缩放（锚点跟随光标，exp(deltaY·0.0015)，最小窗 4 个事件宽度）；
   左键拖=框选时间范围；右键拖=平移；点击色块=选中该记录；点空白=聚焦最近记录；
   Escape=重置视口。时间范围 onChange 时账本只显示范围内的行。 */
interface TimeRange { start: number; end: number }

const MINIMUM_DRAG_PX = 3
const MINIMUM_ZOOM_EVENTS = 4

function orderedRange(a: number, b: number): TimeRange {
  return a <= b ? { start: a, end: b } : { start: b, end: a }
}

function TimelineLanes({ records, turns, range, onRangeChange, onRecordSelect, onRecordFocus }: {
  records: FusedRecord[]
  turns: TurnGroup[]
  range: TimeRange | null
  onRangeChange: (r: TimeRange | null) => void
  onRecordSelect: (record: FusedRecord) => void
  onRecordFocus: (record: FusedRecord) => void
}) {
  const rootRef = useRef<HTMLDivElement | null>(null)
  const trackRef = useRef<HTMLDivElement | null>(null)
  const dragRef = useRef<{ pointerId: number; anchorTime: number; anchorClientX: number; record: FusedRecord | null } | null>(null)
  const panRef = useRef<{ pointerId: number; anchorClientX: number; anchorStart: number; moved: boolean; pannable: boolean } | null>(null)
  const [draft, setDraft] = useState<TimeRange | null>(null)
  const [viewport, setViewport] = useState<TimeRange | null>(null)
  const [panning, setPanning] = useState(false)

  const modelStart = records.length ? records[0].ts : 0
  const modelEnd = records.length ? records[records.length - 1].ts : 1
  const fullDuration = Math.max(1, modelEnd - modelStart)
  const domainStart = viewport === null ? modelStart : viewport.start
  const domainDuration = viewport === null ? fullDuration : Math.max(1, viewport.end - viewport.start)

  // 滚轮缩放：锚点跟随光标（dsh wheel 语义）
  useEffect(() => {
    const root = rootRef.current
    if (root === null) return
    const onWheel = (event: WheelEvent) => {
      event.preventDefault()
      const track = trackRef.current
      if (track === null || records.length === 0) return
      const rect = track.getBoundingClientRect()
      const anchorFraction = Math.min(1, Math.max(0, (event.clientX - rect.left) / Math.max(1, rect.width)))
      const minDuration = (fullDuration / records.length) * MINIMUM_ZOOM_EVENTS
      const nextDuration = Math.min(
        fullDuration,
        Math.max(minDuration, domainDuration * Math.exp(event.deltaY * 0.0015)),
      )
      if (nextDuration >= fullDuration * 0.999) {
        setViewport(null)
        return
      }
      const anchorTime = domainStart + anchorFraction * domainDuration
      const nextStart = Math.min(
        Math.max(anchorTime - anchorFraction * nextDuration, modelStart),
        modelEnd - nextDuration,
      )
      setViewport({ start: nextStart, end: nextStart + nextDuration })
    }
    root.addEventListener("wheel", onWheel, { passive: false })
    return () => { root.removeEventListener("wheel", onWheel) }
  }, [domainDuration, domainStart, fullDuration, modelEnd, modelStart, records.length])

  // Escape 重置（dsh 语义）
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setViewport(null)
        onRangeChange(null)
      }
    }
    window.addEventListener("keydown", onKey)
    return () => { window.removeEventListener("keydown", onKey) }
  }, [onRangeChange])

  if (records.length === 0) return null

  const visibleRange = draft ?? range
  const frac = (t: number) => (t - domainStart) / domainDuration
  // 可见 domain 内的记录 → 泳道 span（绝对定位，dsh span 形态）
  const spans = records
    .map((record) => {
      const lane = record.kind === "user" ? 0 : record.kind === "message" ? 1 : 2
      const color = record.failed ? "#e5484d" : lane === 0 ? "#22c55e" : lane === 1 ? "#a78bfa" : "#f59e0b"
      const next = records[records.indexOf(record) + 1]
      const end = next ? Math.min(next.ts, record.ts + 30000) : record.ts + 30000
      return { record, lane, color, start: record.ts, end: Math.max(end, record.ts + 30) }
    })
    .filter((s) => s.end >= domainStart && s.start <= domainStart + domainDuration)

  const fractionAt = (clientX: number): number => {
    const track = trackRef.current
    if (track === null) return 0
    const rect = track.getBoundingClientRect()
    return Math.min(1, Math.max(0, (clientX - rect.left) / Math.max(1, rect.width)))
  }
  const recordAt = (target: EventTarget | null): FusedRecord | null => {
    const el = target instanceof HTMLElement ? target.closest<HTMLElement>("[data-record-key]") : null
    if (el === null) return null
    return spans.find((s) => s.record.key === el.dataset.recordKey)?.record ?? null
  }

  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button === 2) {
      panRef.current = { pointerId: event.pointerId, anchorClientX: event.clientX, anchorStart: domainStart, moved: false, pannable: viewport !== null }
      setPanning(true)
      event.currentTarget.setPointerCapture(event.pointerId)
      return
    }
    if (event.button !== 0) return
    const fraction = fractionAt(event.clientX)
    const anchorTime = domainStart + fraction * domainDuration
    dragRef.current = { pointerId: event.pointerId, anchorTime, anchorClientX: event.clientX, record: recordAt(event.target) }
    event.currentTarget.setPointerCapture(event.pointerId)
    setDraft({ start: anchorTime, end: anchorTime })
  }

  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const pan = panRef.current
    if (pan !== null && pan.pointerId === event.pointerId) {
      if (Math.abs(event.clientX - pan.anchorClientX) >= MINIMUM_DRAG_PX) pan.moved = true
      if (!pan.pannable) return
      const track = trackRef.current
      if (track === null) return
      const rect = track.getBoundingClientRect()
      const delta = (event.clientX - pan.anchorClientX) / Math.max(1, rect.width)
      const nextStart = Math.min(Math.max(pan.anchorStart - delta * domainDuration, modelStart), modelEnd - domainDuration)
      setViewport({ start: nextStart, end: nextStart + domainDuration })
      return
    }
    const drag = dragRef.current
    if (drag === null || drag.pointerId !== event.pointerId) return
    const pointTime = domainStart + fractionAt(event.clientX) * domainDuration
    setDraft(orderedRange(drag.anchorTime, pointTime))
  }

  const onPointerEnd = (event: React.PointerEvent<HTMLDivElement>) => {
    const pan = panRef.current
    if (pan !== null && pan.pointerId === event.pointerId) {
      const moved = pan.moved || Math.abs(event.clientX - pan.anchorClientX) >= MINIMUM_DRAG_PX
      panRef.current = null
      setPanning(false)
      if (!moved) onRangeChange(null)
      return
    }
    const drag = dragRef.current
    if (drag === null || drag.pointerId !== event.pointerId) return
    const pointTime = domainStart + fractionAt(event.clientX) * domainDuration
    const selected = orderedRange(drag.anchorTime, pointTime)
    dragRef.current = null
    setDraft(null)
    const isClick = Math.abs(event.clientX - drag.anchorClientX) < MINIMUM_DRAG_PX
    if (isClick && drag.record !== null) {
      // 点击色块：直接选中该记录（dsh onRecordSelect）
      onRangeChange(null)
      onRecordSelect(drag.record)
      return
    }
    const minimumDuration = Math.min(domainDuration, fullDuration / Math.max(1, spans.length))
    const committed = selected.end - selected.start < minimumDuration
      ? (() => {
          const center = isClick ? selected.start : (selected.start + selected.end) / 2
          const s = Math.min(Math.max(center - minimumDuration / 2, modelStart), modelEnd - minimumDuration)
          return { start: s, end: s + minimumDuration }
        })()
      : selected
    onRangeChange(committed)
    if (isClick) {
      // 点空白：聚焦最近记录（dsh onRecordFocus）
      let nearest = spans[0]?.record
      let best = Infinity
      for (const s of spans) {
        const d = selected.start < s.start ? s.start - selected.start : selected.start > s.end ? selected.start - s.end : 0
        if (d < best) { best = d; nearest = s.record }
      }
      if (nearest !== undefined) onRecordFocus(nearest)
    }
  }

  const selectionLeft = visibleRange ? `${Math.min(Math.max(frac(visibleRange.start), 0), 1) * 100}%` : null
  const selectionWidth = visibleRange
    ? `${(Math.min(Math.max(frac(visibleRange.end), 0), 1) - Math.min(Math.max(frac(visibleRange.start), 0), 1)) * 100}%`
    : null

  return (
    <div className="trajectory-timeline" ref={rootRef} aria-label="轨迹时间线">
      <div className="trajectory-lane-labels" aria-hidden="true">
        <span>Input</span>
        <span>Model</span>
        <span>Tools</span>
      </div>
      <div
        className="trajectory-lanes"
        ref={trackRef}
        data-panning={panning || undefined}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerEnd}
        onPointerCancel={onPointerEnd}
        onContextMenu={(e) => e.preventDefault()}
      >
        {spans.map((s) => {
          const left = `${Math.min(Math.max(frac(s.start), 0), 1) * 100}%`
          const width = `${Math.max(0.2, (Math.min(Math.max(frac(s.end), 0), 1) - Math.min(Math.max(frac(s.start), 0), 1)) * 100)}%`
          return (
            <span
              key={s.record.key}
              className="trajectory-span"
              data-record-key={s.record.key}
              style={{ top: `calc(${s.lane} * 14px)`, left, width, background: s.color }}
            />
          )
        })}
        {selectionLeft !== null && selectionWidth !== null ? (
          <span className="trajectory-selection" style={{ left: selectionLeft, width: selectionWidth }} aria-hidden="true" />
        ) : null}
      </div>
      <span className="trajectory-zoom-hint" aria-hidden="true">{viewport === null ? `${turns.length} 轮` : "已缩放 · Esc 重置"}</span>
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

function DetailPanel({ record, onClose, width, onWidthChange }: { record: FusedRecord; onClose: () => void; width: number | null; onWidthChange: (w: number) => void }) {
  const tabs = tabsForRecord(record)
  const [active, setActive] = useState("overview")
  const [schema, setSchema] = useState<string>("加载中…")
  const widthRef = useRef(width ?? 381)
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
    <aside className="trajectory-details" aria-label="轨迹详情" style={{ width: width !== null ? `${width}px` : undefined }}>
      <button
        type="button"
        className="trajectory-details-resize"
        aria-label="拖动调整详情宽度"
        onPointerDown={(e) => {
          const startX = e.clientX
          const startW = widthRef.current
          const target = e.currentTarget
          target.setPointerCapture(e.pointerId)
          const onMove = (ev: PointerEvent) => {
            const next = Math.min(440, Math.max(320, startW + (startX - ev.clientX)))
            widthRef.current = next
            onWidthChange(next)
          }
          const onUp = (ev: PointerEvent) => {
            target.removeEventListener("pointermove", onMove)
            target.removeEventListener("pointerup", onUp)
            if (typeof target.releasePointerCapture === "function") {
              try { target.releasePointerCapture(ev.pointerId) } catch { /* 已释放 */ }
            }
          }
          target.addEventListener("pointermove", onMove)
          target.addEventListener("pointerup", onUp)
        }}
      />
      <div className="trajectory-details-head">
        <span className="trajectory-details-name"><span className="trajectory-details-dot" aria-hidden="true" />{KIND_BADGE[record.kind]}</span>
        <span className="trajectory-details-location">Turn {record.turn} · Step {record.step}</span>
        <button type="button" className="trajectory-details-close" aria-label="关闭详情" onClick={onClose}><span aria-hidden="true">×</span></button>
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
  const [range, setRange] = useState<TimeRange | null>(null)
  const [detailWidth, setDetailWidth] = useState<number | null>(null)
  const reload = useRef(0)

  useEffect(() => {
    const gen = ++reload.current
    setTurns(null)
    setError("")
    setSelectedKey(null)
    setRange(null)
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
          <button
            type="button"
            className={`trajectory-mode${durationMode ? " active" : ""}`}
            aria-pressed={durationMode}
            onClick={() => setDurationMode((v) => !v)}
          >
            <svg className="trajectory-toggle-icon" viewBox="0 0 16 16" fill="none" aria-hidden="true"><circle cx="8" cy="8" r="5.25" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round"/><path d="M8 4.75V8l2.25 1.5" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round"/></svg>
            时长
          </button>
          <button
            type="button"
            role="switch"
            aria-checked={durationMode}
            className="trajectory-switch"
            title={durationMode ? "使用等宽时长" : "使用实际时长"}
            onClick={() => setDurationMode((v) => !v)}
          >
            <span>实际时间</span>
            <span className="trajectory-switch-track" data-on={durationMode || undefined} aria-hidden="true"><span className="trajectory-switch-thumb" /></span>
          </button>
          <button type="button" className={`trajectory-mode${allCollapsed ? " active" : ""}`} onClick={toggleCollapseAll}>⊟Turns</button>
          <button type="button" className={`trajectory-mode${allCollapsed ? " active" : ""}`} onClick={toggleCollapseAll}>⊟Calls</button>
        </div>
        <div className="trajectory-searchbox">
          <svg width="12" height="12" viewBox="0 0 14 14" fill="none" aria-hidden="true"><circle cx="6.4" cy="6.4" r="4.4" stroke="currentColor" strokeWidth="1.4"/><path d="m9.8 9.8 2.9 2.9" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/></svg>
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
      {!durationMode ? (
        <TimelineLanes
          records={turns.flatMap((t) => t.records)}
          turns={turns}
          range={range}
          onRangeChange={setRange}
          onRecordSelect={(record) => setSelectedKey(record.key)}
          onRecordFocus={(record) => setSelectedKey(record.key)}
        />
      ) : null}
      <div className="trajectory-ledger-wrap">
        <div className="trajectory-ledger" role="grid" aria-label="轨迹账本">
          {turns.length === 0 ? <p className="trace-loading">无记录</p> : null}
          {turns.map((turn) => {
            // 时间线框选范围外的记录隐藏（dsh range 过滤语义）
            const visibleRecords = range === null
              ? turn.records
              : turn.records.filter((r) => r.ts >= range.start - 1 && r.ts <= range.end + 1)
            if (visibleRecords.length === 0) return null
            const isCollapsed = collapsed.has(turn.turn)
            const steps = visibleRecords.length
            const calls = visibleRecords.filter((r) => r.kind === "tool").length
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
                  {visibleRecords[0]?.kind === "user" ? (
                    <span className="trajectory-turn-lead">{visibleRecords[0].text.slice(0, 48)}</span>
                  ) : null}
                  {isCollapsed
                    ? <span className="trajectory-turn-subtotal">… {steps} steps · {calls} tool calls</span>
                    : <span className="trajectory-turn-meta">{steps} 条</span>}
                </button>
                {!isCollapsed ? visibleRecords.map((record) => {
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
        {selected ? <DetailPanel record={selected} onClose={() => setSelectedKey(null)} width={detailWidth} onWidthChange={setDetailWidth} /> : null}
      </div>
    </div>
  )
}
