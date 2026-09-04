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

/* ---- kind 徽章图标（逐 path 照抄 dsh TrajectoryTable/icons/index）---- */
const KIND_ICON: Record<FusedRecord["kind"], React.ReactNode> = {
  user: (
    <svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M11.0307 5.46369C11.0305 3.78995 9.6734 2.43357 7.99961 2.43357C6.32601 2.43379 4.96972 3.79009 4.96949 5.46369C4.96949 7.13748 6.32587 8.49455 7.99961 8.49477C9.67354 8.49477 11.0307 7.13762 11.0307 5.46369ZM12.3163 5.46369C12.3163 7.84777 10.3837 9.78042 7.99961 9.78042C5.61572 9.7802 3.68288 7.84763 3.68288 5.46369C3.6831 3.07993 5.61586 1.14718 7.99961 1.14695C10.3836 1.14695 12.3161 3.0798 12.3163 5.46369Z" fill="currentColor" />
      <path d="M8.00002 10.3316C11.7343 10.3316 14.1864 11.8997 15.0387 14.4445L14.4292 14.6483L13.8197 14.8531C13.1955 12.9893 11.3673 11.6182 8.00002 11.6182C4.63277 11.6182 2.80455 12.9893 2.18031 14.8531L1.5708 14.6483L0.961304 14.4445C1.81368 11.8997 4.26579 10.3316 8.00002 10.3316Z" fill="currentColor" />
    </svg>
  ),
  message: (
    <svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M6.1 3.1Q6.6 7.8 11.3 8.3Q6.6 8.8 6.1 13.5Q5.6 8.8 0.9 8.3Q5.6 7.8 6.1 3.1Z" fill="currentColor" />
      <path d="M11.9 1Q12.2 3.7 14.9 4Q12.2 4.3 11.9 7Q11.6 4.3 8.9 4Q11.6 3.7 11.9 1Z" fill="currentColor" />
      <path d="M12.5 9.4Q12.7 11.4 14.7 11.6Q12.7 11.8 12.5 13.8Q12.3 11.8 10.3 11.6Q12.3 11.4 12.5 9.4Z" fill="currentColor" />
    </svg>
  ),
  tool: (
    <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M14 3.3a3.8 3.8 0 0 1-4.8 4.8l-5.1 5.1a1.6 1.6 0 1 1-2.3-2.3l5.1-5.1A3.8 3.8 0 0 1 11.7 1l-2.3 2.3 2.3 2.3L14 3.3Z" />
    </svg>
  ),
  system: (
    <svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <circle cx="8" cy="8" r="6.7" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      <circle cx="8" cy="5.5" r=".85" fill="currentColor" stroke="none" />
      <path d="M8 7.75v3.4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  ),
}

const KIND_TAG_CLASS: Record<FusedRecord["kind"], string> = {
  user: "tt-user",
  message: "tt-assistant",
  tool: "tt-tool",
  system: "tt-system",
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
          // dsh 轨迹表没有思考行：reasoning 属于助手消息（对话 tab 的 Think 披露行消费此事件）
          return
        }
        if (type === "system.prompt") {
          // dsh 语义：系统提示词=会话最开头的一行 SYSTEM（本会话首条 trace 事件）
          const promptText = String(e.message || "")
          push({ kind: "system", key: `sys:${e.id || "event"}`, text: promptText.split("\n").filter(Boolean)[0]?.slice(0, 90) || "System prompt", tool: null, args: {}, output: promptText, status: "ok", iso: e.created_at })
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
  function push(partial: Omit<FusedRecord, "key" | "turn" | "step" | "ts" | "failed" | "durationMs" | "kind" | "text"> & Partial<Pick<FusedRecord, "durationMs" | "key">> & Pick<FusedRecord, "kind" | "text">) {
    if (!current) openTurn()
    step += 1
    const failed = partial.status === "failed" || partial.status === "error"
    const record: FusedRecord = {
      ...(partial as Omit<FusedRecord, "key" | "turn" | "step" | "ts" | "failed">),
      durationMs: partial.durationMs ?? null,
      key: partial.key ?? `r-${seq++}`,
      turn: current!.turn,
      step,
      ts: Date.parse(partial.iso) || 0,
      failed,
    }
    current!.records.push(record)
    if (Number.isNaN(current!.startedAt)) current!.startedAt = record.ts
  }
  for (const s of seeds) s.run()
  // dsh 语义：系统提示词行永远置顶（用户消息 created_at 可能早于 system.prompt 事件，
  // 不能让 SYSTEM 行被时间序排到轮内末尾）
  for (let i = 0; i < turnsAll.length; i += 1) {
    const sysRows = turnsAll[i].records.filter((r) => r.key.startsWith("sys:"))
    if (sysRows.length === 0) continue
    const rest = turnsAll[i].records.filter((r) => !r.key.startsWith("sys:"))
    turnsAll[i].records = [...sysRows, ...rest]
    if (i > 0) {
      // 跨 turn 的 sys 行（异常情况）统一搬到第一个 turn 置顶
      turnsAll[0].records = [...sysRows, ...turnsAll[0].records]
      turnsAll[i].records = rest
    }
  }
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

export type LaneMode = "sequence" | "duration" | "actual"

type TimelineSpan = {
  record: FusedRecord
  lane: number
  color: string
  start: number
  end: number
}

/* dsh timeline.ts：sequence=每记录 1 单位无缝拼接（默认）；duration=真实时长+空闲压缩；actual=墙钟 */
function projectSpans(records: FusedRecord[], turns: TurnGroup[], mode: LaneMode) {
  if (mode === "sequence") {
    const spans: TimelineSpan[] = records.map((record, i) => ({
      record,
      lane: laneFor(record.kind),
      color: colorFor(record),
      start: i,
      end: i + 1,
    }))
    const boundaries = turns
      .map((t) => ({ turn: t.turn, at: records.indexOf(t.records[0]) }))
      .filter((b) => b.at >= 0)
    return { spans, boundaries, domainStart: 0, domainEnd: Math.max(1, records.length) }
  }
  const timed = records.map((record) => {
    const next = records[records.indexOf(record) + 1]
    const rawEnd = mode === "duration"
      ? record.ts + Math.max(record.durationMs ?? 0, 120)
      : Math.min(next ? next.ts : record.ts + 30000, record.ts + 30000)
    return { record, lane: laneFor(record.kind), color: colorFor(record), start: record.ts, end: Math.max(rawEnd, record.ts + 30) }
  })
  // duration 模式：按开始时间压缩空闲（dsh compressIdle）
  let removedIdle = 0
  let coveredUntil: number | null = null
  const offsets = new Map<FusedRecord, number>()
  for (const span of [...timed].sort((a, b) => a.start - b.start)) {
    if (mode === "duration" && coveredUntil !== null && span.start > coveredUntil) {
      removedIdle += span.start - coveredUntil
    }
    offsets.set(span.record, removedIdle)
    coveredUntil = coveredUntil === null ? span.end : Math.max(coveredUntil, span.end)
  }
  const spans = timed.map((t) => ({
    ...t,
    start: t.start - (offsets.get(t.record) ?? 0),
    end: t.end - (offsets.get(t.record) ?? 0),
  }))
  const boundaries = turns
    .map((t) => ({ turn: t.turn, at: spans.find((sp) => sp.record.key === t.records[0]?.key)?.start ?? -1 }))
    .filter((b) => b.at >= 0)
  const domainStart = spans.length ? Math.min(...spans.map((sp) => sp.start)) : 0
  const domainEnd = spans.length ? Math.max(...spans.map((sp) => sp.end)) : 1
  return { spans, boundaries, domainStart, domainEnd }
}

function laneFor(kind: FusedRecord["kind"]): number {
  return kind === "user" ? 0 : kind === "message" ? 1 : 2
}

function colorFor(record: FusedRecord): string {
  if (record.failed) return "#e5484d"
  return record.kind === "user" ? "#22c55e" : record.kind === "message" ? "#a78bfa" : "#f59e0b"
}

function TimelineLanes({ records, turns, mode, focusKeys, onFocusKeysChange, onRecordSelect }: {
  records: FusedRecord[]
  turns: TurnGroup[]
  mode: LaneMode
  focusKeys: Set<string> | null
  onFocusKeysChange: (keys: Set<string> | null) => void
  onRecordSelect: (record: FusedRecord) => void
}) {
  const rootRef = useRef<HTMLDivElement | null>(null)
  const trackRef = useRef<HTMLDivElement | null>(null)
  const dragRef = useRef<{ pointerId: number; anchorTime: number; anchorClientX: number; record: FusedRecord | null } | null>(null)
  const panRef = useRef<{ pointerId: number; anchorClientX: number; anchorStart: number; moved: boolean; pannable: boolean } | null>(null)
  const [draft, setDraft] = useState<TimeRange | null>(null)
  const [viewport, setViewport] = useState<TimeRange | null>(null)
  const [panning, setPanning] = useState(false)

  const model = useMemo(() => projectSpans(records, turns, mode), [records, turns, mode])
  const { spans, boundaries } = model
  const modelStart = model.domainStart
  const modelEnd = model.domainEnd
  const fullDuration = Math.max(1, modelEnd - modelStart)
  const domainStart = viewport === null ? modelStart : viewport.start
  const domainDuration = viewport === null ? fullDuration : Math.max(1, viewport.end - viewport.start)

  // 滚轮缩放：锚点跟随光标（dsh wheel 语义；sequence 模式最小 4 个操作单位）
  useEffect(() => {
    const root = rootRef.current
    if (root === null) return
    const onWheel = (event: WheelEvent) => {
      event.preventDefault()
      const track = trackRef.current
      if (track === null || spans.length === 0) return
      const rect = track.getBoundingClientRect()
      const anchorFraction = Math.min(1, Math.max(0, (event.clientX - rect.left) / Math.max(1, rect.width)))
      const minDuration = mode === "sequence"
        ? Math.min(4, fullDuration)
        : (fullDuration / Math.max(1, spans.length)) * MINIMUM_ZOOM_EVENTS
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
  }, [domainDuration, domainStart, fullDuration, modelEnd, modelStart, mode, spans.length])

  // Escape 重置（dsh 语义）
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setViewport(null)
        onFocusKeysChange(null)
      }
    }
    window.addEventListener("keydown", onKey)
    return () => { window.removeEventListener("keydown", onKey) }
  }, [onFocusKeysChange])

  // 投影或视口变化 → 重新计算焦点键集合（范围外记录淡化由表格过滤承担）
  useEffect(() => {
    if (range === null) return
    const keys = new Set(spans.filter((sp) => sp.end >= domainStart && sp.start <= domainStart + domainDuration).map((sp) => sp.record.key))
    onFocusKeysChange(keys.size === spans.length ? null : keys)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, viewport])

  const [range, setRange] = useState<TimeRange | null>(null)
  if (spans.length === 0) return null

  const visibleRange = draft ?? range
  const frac = (t: number) => (t - domainStart) / domainDuration

  const fractionAt = (clientX: number): number => {
    const track = trackRef.current
    if (track === null) return 0
    const rect = track.getBoundingClientRect()
    return Math.min(1, Math.max(0, (clientX - rect.left) / Math.max(1, rect.width)))
  }
  const recordAt = (target: EventTarget | null): FusedRecord | null => {
    const el = target instanceof HTMLElement ? target.closest<HTMLElement>("[data-record-key]") : null
    if (el === null) return null
    return spans.find((sp) => sp.record.key === el.dataset.recordKey)?.record ?? null
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
      if (!moved) { setViewport(null); setRange(null); onFocusKeysChange(null) }
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
      // 点击色块：选中该记录并跳转到表格行（dsh onRecordSelect + 表格滚动定位）
      setRange(null)
      onFocusKeysChange(null)
      onRecordSelect(drag.record)
      return
    }
    const minimumDuration = Math.min(domainDuration, fullDuration / Math.max(1, spans.length))
    const committed = selected.end - selected.start < minimumDuration
      ? (() => {
          const center = isClick ? selected.start : (selected.start + selected.end) / 2
          const st = Math.min(Math.max(center - minimumDuration / 2, modelStart), modelEnd - minimumDuration)
          return { start: st, end: st + minimumDuration }
        })()
      : selected
    setRange(committed)
    const keys = new Set(spans.filter((sp) => sp.end >= committed.start && sp.start <= committed.end).map((sp) => sp.record.key))
    onFocusKeysChange(keys.size === spans.length ? null : keys)
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
        {boundaries.map((b) => (
          <span
            key={`b-${b.turn}`}
            className="trajectory-turnBoundary"
            style={{ left: `${Math.min(Math.max(frac(b.at), 0), 1) * 100}%` }}
            aria-hidden="true"
          />
        ))}
        {spans.map((sp) => {
          const left = `${Math.min(Math.max(frac(sp.start), 0), 1) * 100}%`
          const width = `${Math.max(0.25, (Math.min(Math.max(frac(sp.end), 0), 1) - Math.min(Math.max(frac(sp.start), 0), 1)) * 100)}%`
          return (
            <span
              key={sp.record.key}
              className="trajectory-span"
              data-record-key={sp.record.key}
              style={{ top: `calc(${sp.lane} * 14px)`, left, width, background: sp.color }}
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
        <div className="trajectory-details-title">
          <span className={`tt-kindTag ${KIND_TAG_CLASS[record.kind]}`}>
            <span className="tt-kindTagIcon" aria-hidden="true">{KIND_ICON[record.kind]}</span>
            <span className="tt-kindTagLabel">{KIND_BADGE[record.kind]}</span>
          </span>
          <span className="trajectory-details-location">Turn {record.turn} · Step {record.step}</span>
        </div>
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
          <>
            <dl className="trajectory-overview">
              <div><dt>Status</dt><dd className={record.failed ? "err" : undefined}>{record.failed ? "Error" : record.status === "pending" ? "Pending" : "Completed"}</dd></div>
              <div><dt>Started</dt><dd>{fmtStarted(record.iso)}</dd></div>
              <div><dt>Duration</dt><dd>{fmtDuration(record.durationMs)}</dd></div>
              <div><dt>Timing source</dt><dd>Session timestamps</dd></div>
              <div><dt>Hierarchy</dt><dd>Turn {record.turn} · Step {record.step}</dd></div>
            </dl>
            <div className="tt-overviewSections">
              {record.kind === "tool" ? (
                <>
                  <section className="tt-overviewSection">
                    <h3 className="tt-overviewHeading">Payload</h3>
                    <div className="tt-overviewPreview"><pre className="trajectory-pre">{JSON.stringify(record.args ?? {}, null, 2)}</pre></div>
                  </section>
                  <section className="tt-overviewSection">
                    <h3 className="tt-overviewHeading">Result</h3>
                    <div className="tt-overviewPreview"><pre className="tt-resultBlockText">{record.output?.trim() || "(no output)"}</pre></div>
                  </section>
                  <section className="tt-overviewSection">
                    <h3 className="tt-overviewHeading">Schema</h3>
                    <div className="tt-overviewPreview"><pre className="trajectory-pre">{schema}</pre></div>
                  </section>
                </>
              ) : null}
              <section className="tt-overviewSection">
                <h3 className="tt-overviewHeading">Timing</h3>
                <div className="tt-overviewPreview">
                  <dl className="trajectory-overview">
                    <div><dt>Step</dt><dd>{record.step}</dd></div>
                    <div><dt>Duration</dt><dd>{fmtDuration(record.durationMs)}</dd></div>
                    <div><dt>Timing source</dt><dd>Session timestamps</dd></div>
                  </dl>
                </div>
              </section>
            </div>
          </>
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
  const [durationPressed, setDurationPressed] = useState(false)
  const [actualSwitch, setActualSwitch] = useState(false)
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set())
  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  const [focusKeys, setFocusKeys] = useState<Set<string> | null>(null)
  const [detailWidth, setDetailWidth] = useState<number | null>(null)
  const [callsCollapsed, setCallsCollapsed] = useState(false)
  const reload = useRef(0)

  useEffect(() => {
    const gen = ++reload.current
    setTurns(null)
    setError("")
    setSelectedKey(null)
    setFocusKeys(null)
    Promise.all([
      api<ChatMessage[] | { messages?: ChatMessage[] }>(`/api/chat/sessions/${encodeURIComponent(sessionId)}/messages`),
      api<{ items?: TraceEvent[] }>(`/api/chat/sessions/${encodeURIComponent(sessionId)}/trace`),
    ])
      .then(async ([msgData, traceData]) => {
        if (gen !== reload.current) return
        const messages = Array.isArray(msgData) ? msgData : (msgData.messages || [])
        const folded = foldRecords(messages, traceData.items || [])
        // 老会话没有 system.prompt 事件：用系统提示词端点补一条虚拟 SYSTEM 行（dsh：置顶拼接）
        const firstTurn = folded.turns[0]
        const hasSysRow = folded.turns.some((t) => t.records.some((r) => r.key.startsWith("sys:")))
        if (firstTurn && !hasSysRow) {
          try {
            const promptData = await api<{ prompt?: string }>("/api/agent/system-prompt")
            const promptText = String(promptData.prompt || "")
            if (promptText && firstTurn.records.length > 0) {
              const firstTs = Math.min(...firstTurn.records.map((r) => r.ts))
              firstTurn.records.unshift({
                key: "sys:virtual",
                turn: firstTurn.turn,
                step: 0,
                ts: firstTs - 1,
                kind: "system",
                text: promptText.split("\n").filter(Boolean)[0]?.slice(0, 90) || "System prompt",
                tool: null,
                args: {},
                output: promptText,
                status: "ok",
                iso: firstTurn.records[0].iso,
                durationMs: null,
                failed: false,
              })
            }
          } catch { /* 端点不可用则跳过 */ }
        }
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

  // dsh 工具条语义：实际时间(墙钟) > 时长(真实时长+压缩空闲) > sequence(等宽无缝，默认)
  const laneMode: LaneMode = actualSwitch ? "actual" : durationPressed ? "duration" : "sequence"

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
            className={`trajectory-mode${durationPressed ? " active" : ""}`}
            aria-pressed={durationPressed}
            onClick={() => { setDurationPressed((v) => !v); setActualSwitch(false) }}
          >
            <svg className="trajectory-toggle-icon" viewBox="0 0 16 16" fill="none" aria-hidden="true"><circle cx="8" cy="8" r="5.25" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round"/><path d="M8 4.75V8l2.25 1.5" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round"/></svg>
            时长
          </button>
          <button
            type="button"
            role="switch"
            aria-checked={actualSwitch}
            className="trajectory-switch"
            title={actualSwitch ? "使用等宽序贯投影" : "使用实际墙钟时间"}
            onClick={() => { setActualSwitch((v) => !v); setDurationPressed(false) }}
          >
            <span>实际时间</span>
            <span className="trajectory-switch-track" data-on={actualSwitch || undefined} aria-hidden="true"><span className="trajectory-switch-thumb" /></span>
          </button>
          <button type="button" className={`trajectory-mode${allCollapsed ? " active" : ""}`} aria-pressed={allCollapsed} onClick={toggleCollapseAll}>{allCollapsed ? "⊞Turns" : "⊟Turns"}</button>
          <button type="button" className={`trajectory-mode${callsCollapsed ? " active" : ""}`} aria-pressed={callsCollapsed} onClick={() => setCallsCollapsed((v) => !v)}>{callsCollapsed ? "⊞Calls" : "⊟Calls"}</button>
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
      <TimelineLanes
        records={turns.flatMap((t) => t.records)}
        turns={turns}
        mode={laneMode}
        focusKeys={focusKeys}
        onFocusKeysChange={setFocusKeys}
        onRecordSelect={(record) => {
          setSelectedKey(record.key)
          // dsh：点击色块后表格滚动定位到对应行
          requestAnimationFrame(() => {
            const row = document.querySelector(`.tt-row[data-record-key="${CSS.escape(record.key)}"]`)
            row?.scrollIntoView({ block: "center", behavior: "smooth" })
          })
        }}
      />
      <div className="trajectory-ledger-wrap">
        <div className="tt-tablePane">
        <table className="tt-table" aria-label="轨迹账本">
          <colgroup>
            <col className="tt-eventColumn" />
            <col className="tt-contentColumn" />
          </colgroup>
          <tbody>
          {turns.length === 0 ? <tr><td colSpan={2}><p className="trace-loading">无记录</p></td></tr> : null}
          {turns.map((turn) => {
            // 时间线框选范围外的记录隐藏（dsh range 过滤语义）
            const visibleRecords = focusKeys === null
              ? turn.records
              : turn.records.filter((r) => focusKeys.has(r.key))
            if (visibleRecords.length === 0) return null
            const isCollapsed = collapsed.has(turn.turn) && visibleRecords.length > 1
            // ⊟Calls：折叠工具调用（隐藏 tool 行，在首个 tool 位置留摘要）
            const callsHidden = callsCollapsed && visibleRecords.some((r) => r.kind === "tool")
            const rowRecords = callsHidden ? visibleRecords.filter((r) => r.kind !== "tool") : visibleRecords
            const hiddenCalls = visibleRecords.filter((r) => r.kind === "tool").length
            const steps = new Set(visibleRecords.map((r) => r.step)).size
            const activeTurn = selected ? selected.turn : null

            const rowEl = (record: FusedRecord, idx: number, list: FusedRecord[], turnStart: boolean, turnEnd: boolean) => {
              const matched = q !== "" && (record.text.toLowerCase().includes(q) || (record.tool || "").toLowerCase().includes(q))
              const isSelected = record.key === selectedKey
              const outputLine = record.output && record.output.trim()
                ? (record.output.split("\n").filter(Boolean).slice(-1)[0] ?? "").slice(0, 80)
                : ""
              const select = () => setSelectedKey(record.key === selectedKey ? null : record.key)
              return (
                <tr
                  key={record.key}
                  tabIndex={0}
                  data-record-key={record.key}
                  data-kind={record.kind}
                  data-selected={isSelected || undefined}
                  data-error={record.failed || undefined}
                  data-turn-start={turnStart || undefined}
                  data-turn-end={turnEnd || undefined}
                  data-matched={matched || undefined}
                  className={`tt-row${record.failed ? " tt-failed" : ""}`}
                  onClick={select}
                  onKeyDown={(e) => { if (e.key === "Enter") select() }}
                >
                  <td className="tt-event">
                    {record.turn === activeTurn ? <span className="tt-turnRail" aria-hidden="true" /> : null}
                    <span className="tt-selectionRail" aria-hidden="true" />
                    {turnStart ? (
                      <span className={`tt-turnLabel${record.turn === activeTurn ? " tt-turnLabelActive" : ""}`} aria-label={`Turn ${record.turn}`}>
                        <span className="tt-turnLabelFull" aria-hidden="true">Turn {record.turn}</span>
                        <span className="tt-turnLabelCompact" aria-hidden="true">#{record.turn}</span>
                      </span>
                    ) : null}
                    <div className="tt-eventInner">
                      <span className="tt-kindSlot">
                        <span className={`tt-kindTag ${KIND_TAG_CLASS[record.kind]}`}>
                          <span className="tt-kindTagIcon" aria-hidden="true">{KIND_ICON[record.kind]}</span>
                          <span className="tt-kindTagLabel">{KIND_BADGE[record.kind]}</span>
                        </span>
                      </span>
                    </div>
                  </td>
                  <td className="tt-content">
                    {record.kind === "tool" && record.tool ? (
                      <span className="tt-resultPreview" title={`${record.tool} ${JSON.stringify(record.args)}${outputLine ? ` → ${outputLine}` : ""}`}>
                        <span className="tt-resultRequest">
                          <span className="tt-toolCallNameTypeface">{record.tool}</span>
                          <span className="tt-toolCallPayload">{JSON.stringify(record.args)}</span>
                        </span>
                        {outputLine ? (
                          <span className="tt-inlineResult"><span className="tt-arrow" aria-hidden="true">→</span><span className="tt-inlineResultText">{outputLine}</span></span>
                        ) : null}
                      </span>
                    ) : (
                      <span className={`tt-contentText${record.text === "(tool call only)" ? " tt-toolCallOnly" : ""}`}>{record.text}</span>
                    )}
                  </td>
                </tr>
              )
            }

            const summaryRow = (key: string, label: string, onExpand: () => void, turnEnd: boolean) => (
              <tr
                key={key}
                className="tt-row tt-collapsedSummary"
                data-collapsed-summary="turn"
                data-turn-end={turnEnd || undefined}
                tabIndex={0}
                onClick={onExpand}
                onKeyDown={(e) => { if (e.key === "Enter") onExpand() }}
              >
                <td className="tt-event" />
                <td className="tt-content">
                  <span className="tt-collapsedTurnContent">
                    <span className="tt-collapsedTurnEllipsis" aria-hidden="true">…</span>
                    <span className="tt-collapsedTurnText">{label}</span>
                  </span>
                </td>
              </tr>
            )

            if (isCollapsed) {
              return [
                rowEl(visibleRecords[0], 0, visibleRecords, true, false),
                summaryRow(`t-${turn.turn}`, `${steps} ${steps === 1 ? "step" : "steps"} · ${visibleRecords.filter((r) => r.kind === "tool").length} tool ${visibleRecords.filter((r) => r.kind === "tool").length === 1 ? "call" : "calls"}`, () => setCollapsed((prev) => { const n = new Set(prev); n.delete(turn.turn); return n }), true),
              ]
            }
            if (callsHidden && rowRecords.length > 0 && hiddenCalls > 0) {
              // 工具行折叠：首行后插一条调用摘要
              return [
                ...rowRecords.slice(0, 1).map((record, i) => rowEl(record, i, rowRecords, true, false)),
                summaryRow(`c-${turn.turn}`, `${hiddenCalls} tool ${hiddenCalls === 1 ? "call" : "calls"}`, () => setCallsCollapsed(false), false),
                ...rowRecords.slice(1).map((record, i) => rowEl(record, i + 1, rowRecords, false, i === rowRecords.slice(1).length - 1)),
              ]
            }
            return visibleRecords.map((record, idx) => rowEl(record, idx, visibleRecords, idx === 0, idx === visibleRecords.length - 1))
          })}
          </tbody>
        </table>
        </div>
        {selected ? <DetailPanel record={selected} onClose={() => setSelectedKey(null)} width={detailWidth} onWidthChange={setDetailWidth} /> : null}
      </div>
    </div>
  )
}
